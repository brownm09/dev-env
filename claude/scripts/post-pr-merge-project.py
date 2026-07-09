#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh pr merge' and automatically
moves the linked issue's GitHub Project item to Done.

Project opt-in: add .claude/hook-config.json to the project root with
'status_field_id' and 'done_option_id' fields (in addition to the existing
project fields). Projects without these fields are silently skipped.

hook-config.json schema (fields used by this hook):
  {
    "repo":            "owner/repo-name",
    "project_number":  "3",
    "project_owner":   "brownm09",
    "project_node_id": "PVT_kwHOAjEKvM4BWKFe",
    "status_field_id": "PVTSSF_...",
    "done_option_id":  "98236657"
  }

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "..."},  # NOT "output" — ADR-049
    "session_id": "...",
    "cwd": "..."
  }

Exit 0  — no confirmed merge, no config, or no Closes ref; silent
Exit 2  — item moved to Done (success) or move failed (fallback command shown)
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

from _hookio import (
    confirm_merge_via_gh,
    is_merge_help_only,
    mask_prose_flag_values,
    mask_quoted_spans,
    merge_pr_number_from_output,
    output_has_merge_marker,
    read_command_output,
    scan_top_level,
    should_confirm_via_gh,
)

CONFIG_FILE = ".claude/hook-config.json"

_MERGE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b")
_CLOSES_RE = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
# Used two ways: (1) extract_pr_number's scan of `gh pr merge` OUTPUT text,
# which needs no masking (gh's own output, not attacker-shaped command
# prose); and (2) extract_pr_number_from_command's own URL fallback, which
# runs this against a mask_prose_flag_values-masked copy of `args`
# (dev-env#664, ADR-050 Amendment 21), so a --subject/--body value
# containing a URL-shaped decoy (e.g. "see https://github.com/other/repo
# /pull/99 for context") can't false-match either — mirrors
# _PR_URL_REPO_RE's own mask_prose_flag_values fix below (dev-env#634,
# Amendment 17), just for the PR-NUMBER extraction path instead of the
# repo-name one. A BARE quoted PR-URL positional argument (test_cmd_url) is
# never preceded by --subject/--body, so mask_prose_flag_values leaves it
# untouched while blanket mask_quoted_spans would have blinded it too.
_PR_URL_RE = re.compile(r"https://github\.com/\S+/pull/(\d+)")
# Same PR-URL shape as _PR_URL_RE but capturing the owner/repo segment instead
# of discarding it — used to detect when a merge command names a DIFFERENT
# repo than cwd's own config (dev-env#559). extract_repo_from_command runs
# this against a mask_prose_flag_values-masked copy of `args` (dev-env#634,
# ADR-050 Amendment 17), so a --subject/--body value containing a URL-shaped
# decoy (e.g. "see https://github.com/other/repo/pull/1 for context") can't
# false-match either — mirrors _REPO_FLAG_RE's own mask_quoted_spans fix
# below, but scoped to prose-flag values only: a BARE quoted PR-URL argument
# (test_repo_from_cross_repo_url) is never preceded by --subject/--body, so
# mask_prose_flag_values leaves it untouched while mask_quoted_spans would
# have blinded it too.
_PR_URL_REPO_RE = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/\d+")
# An explicit --repo flag is the highest-confidence signal, ahead of a URL —
# mirrors _effective_merge_repo's resolution order in pr-merge-reminder.py
# (dev-env#470). Checked first so a --subject/--body value that happens to
# mention an unrelated PR URL cannot override an explicitly-named --repo
# (review finding on dev-env#559 / PR #572). Also matches gh's -R shorthand
# for --repo (dev-env#616) — a bare `-R` merge command previously fell
# through to None, silently resolving to cwd's own config instead. The
# (?<!\S) lookbehind requires -R/--repo to start a standalone token, so it
# can't match mid-word (e.g. a PR title literally containing "add-R support")
# — review finding on PR #623. extract_repo_from_command runs this against a
# mask_quoted_spans-masked string (dev-env#626, ADR-050 Amendment 15) so a
# --subject/--body value containing a space-separated "-R other/repo"
# substring can no longer false-match either — the value's whole quoted span
# is blanked before this regex ever sees it.
_REPO_FLAG_RE = re.compile(r"(?<!\S)(?:--repo|-R)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
# The argument list of the `gh pr merge` invocation only — up to the next shell
# separator — so a /pull/N URL in a --subject/--body value or a chained sibling
# command cannot hijack the PR-number extraction (the whole-command search did;
# #380). The merge-success markers themselves live in _hookio (one source).
_MERGE_ARGS_RE = re.compile(r"\bgh\s+pr\s+merge\b([^\n;|&]*)")


def _check_merge_stmt(token: str) -> bool:
    return bool(_MERGE_RE.match(token.lstrip()))


def _merge_args(command: str) -> str | None:
    """Return the `gh pr merge` invocation's own argument text, or None.

    Bounded via `_MERGE_ARGS_RE` against a `mask_quoted_spans`-masked copy of
    *command* (dev-env#660, ADR-050 Amendment 20), not the raw command --
    `_MERGE_ARGS_RE`'s negated character class `[^\n;|&]*` stops at ANY single
    `&`/`|` (not just doubled `&&`/`||`) with no quote-awareness of its own, so
    a --subject/--body value containing a bare separator character (e.g.
    `--subject "R&D tracking"` -- a perfectly ordinary commit subject, not a
    deliberately crafted string) truncated the args region before a later real
    flag or URL was ever seen. Confirmed live: `extract_repo_from_command('gh pr
    merge 42 --subject "part1 && part2" --repo o/r')` returned `None`, silently
    dropping a real `--repo`. mask_quoted_spans is length-preserving, so the
    match's span offsets against the masked text apply unchanged to the
    original -- the returned text is the REAL (unmasked) args, not the masked
    stand-in, so downstream parsing (the existing `mask_quoted_spans(args)` /
    `mask_prose_flag_values(args)` calls in `extract_repo_from_command`) still
    sees genuine quote/flag-value content to mask in its own turn.
    """
    m = _MERGE_ARGS_RE.search(mask_quoted_spans(command))
    if not m:
        return None
    start, end = m.span(1)
    return command[start:end]


def load_config(cwd: str) -> dict | None:
    path = os.path.join(cwd, CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def merge_succeeded(output: str) -> bool:
    """Return True only if the output confirms a completed merge.

    Gated on gh's merge success markers (not the exit code): a queued `--auto`
    exits 0 but prints no merge marker, and a worktree merge exits non-zero yet
    prints the marker to stderr before its local-cleanup tail fails (issue #275).
    Marker-only is deliberately stricter than the exit-0-OR-marker check in
    post-pr-merge-{pull,reclaim}.py — moving an issue to Done on a not-yet-merged
    `--auto` would be wrong, whereas a premature pull/reclaim is harmless. (#380)
    """
    return output_has_merge_marker(output)


def extract_pr_number_from_command(command: str) -> int | None:
    """Derive the merged PR number from the `gh pr merge` invocation.

    `gh pr merge` output carries no `/pull/N` URL, so the command is the reliable
    source when the PR is named. Extraction is scoped to the merge invocation's
    own arguments (`_MERGE_ARGS_RE`), not the whole command, so a `/pull/N` URL in
    a `--subject`/`--body` value or a chained sibling command cannot hijack it.
    The positional number is preferred over a URL argument:
        gh pr merge 380 --squash             -> 380
        gh pr merge --squash 380             -> 380  (flag-before-arg)
        gh pr merge <url>/pull/380 --squash  -> 380
    A bare `gh pr merge --squash --delete-branch` (the current branch's PR) names
    no number; the caller then falls back to the output success marker. (#380)

    Args-region bounding is quote-aware (`_merge_args`, dev-env#660, ADR-050
    Amendment 20): a positional number placed AFTER a --subject/--body value
    containing a bare separator character (e.g. `--subject "part1 && part2"
    42 --repo o/r`) is no longer silently missed.

    The positional-number match itself also runs against a `mask_quoted_spans`-
    masked copy of `args` (dev-env#650, ADR-050 Amendment 19), so a
    `--subject`/`--body` value containing a space-separated bare number
    ("resolves 42 items") cannot be mistaken for the real merged PR number —
    the same quoted-value blind spot Amendment 15 closed for the repo-flag
    regex family, just for a bare-digit token instead of a `--repo`/`-R` flag.
    These two fixes are complementary, not overlapping: Amendment 20 ensures
    `args` itself is not prematurely truncated before this search ever runs;
    Amendment 19 ensures the search WITHIN `args` isn't hijacked by a decoy.

    The `_PR_URL_RE` fallback below runs against a `mask_prose_flag_values`-
    masked copy of `args` instead (dev-env#664, ADR-050 Amendment 21), so a
    `--subject`/`--body` value containing a URL-shaped decoy (e.g. "see
    https://github.com/other/repo/pull/99 for context") can't false-match
    either — mirrors `extract_repo_from_command`'s own `_PR_URL_REPO_RE` fix
    (dev-env#634, Amendment 17), just for the PR-number extraction path
    instead of the repo-name one. A bare quoted PR-URL positional argument
    (`gh pr merge "https://github.com/o/r/pull/380"`, never preceded by
    `--subject`/`--body`, see `test_cmd_url`) is a legitimate, already-
    supported shape that `mask_prose_flag_values` leaves untouched, unlike
    blanket `mask_quoted_spans`.
    """
    args = _merge_args(command)
    if args is None:
        return None
    # Positional number token (`380`), tolerant of flags before it; a digit run
    # inside a flag value (`--foo=12`) or a branch name (`my-branch-2`) is not a
    # standalone token and is correctly ignored.
    num = re.search(r"(?<!\S)(\d+)(?=\s|$)", mask_quoted_spans(args))
    if num:
        return int(num.group(1))
    url = _PR_URL_RE.search(mask_prose_flag_values(args))
    if url:
        return int(url.group(1))
    return None


def extract_repo_from_command(command: str) -> str | None:
    """Derive the owner/repo a `gh pr merge` invocation explicitly targets.

    Mirrors `extract_pr_number_from_command`: scoped to the merge invocation's
    own arguments (`_MERGE_ARGS_RE`) so a `/pull/N` URL in a `--subject`/`--body`
    value or a chained sibling command cannot hijack it. An explicit `--repo`
    flag is checked first — the highest-confidence signal, mirroring
    `_effective_merge_repo`'s resolution order in `pr-merge-reminder.py`
    (dev-env#470) — so a `--subject`/`--body` value that happens to mention an
    unrelated PR URL cannot override an explicitly-named `--repo` (review
    finding on dev-env#559 / PR #572: without this check, `gh pr merge --repo
    a/b 380 --subject "see https://github.com/c/d/pull/1"` would extract "c/d"
    instead of "a/b", falsely mismatching a legitimate same-repo merge and
    silently skipping its Done-move). Only a `--repo` flag or a PR URL names a
    repo explicitly — `gh pr merge 380 --squash` (bare number) and
    `gh pr merge --squash --delete-branch` (the current branch's own PR) both
    return None; the caller then falls back to cwd's own config.

    This is what `get_pr_body`/`confirm_merge_via_gh` should query — the merge
    command's actual target repo, not necessarily the one named in cwd's
    config (dev-env#559: a `gh pr merge <cross-repo URL>` run from an unrelated
    cwd silently resolved to cwd's own repo and fetched the wrong PR's body).

    The `--repo`/`-R` flag check runs against a `mask_quoted_spans`-masked copy
    of `args` (dev-env#626, ADR-050 Amendment 15), so a `--subject`/`--body`
    value containing a space-separated "-R other/repo" substring cannot be
    mistaken for a real flag. The URL fallback runs against a
    `mask_prose_flag_values`-masked copy of `args` instead (dev-env#634, ADR-050
    Amendment 17), so a `--subject`/`--body` value containing a URL-shaped
    decoy can't false-match either — while a *bare* quoted `/pull/N` URL
    positional argument (e.g. `gh pr merge "https://github.com/o/r/pull/1"`,
    never preceded by `--subject`/`--body`) is a legitimate, already-supported
    shape (see `test_repo_from_cross_repo_url`) that `mask_prose_flag_values`
    leaves untouched, unlike blanket `mask_quoted_spans`.

    Args-region bounding is quote-aware (`_merge_args`, dev-env#660, ADR-050
    Amendment 20): a `--repo` flag placed AFTER a --subject/--body value
    containing a bare separator character (e.g. `--subject "part1 && part2"
    --repo o/r`, or even an ordinary subject like `--subject "R&D tracking"`)
    is no longer silently dropped.
    """
    args = _merge_args(command)
    if args is None:
        return None
    flag = _REPO_FLAG_RE.search(mask_quoted_spans(args))
    if flag:
        return flag.group(1)
    url = _PR_URL_REPO_RE.search(mask_prose_flag_values(args))
    return url.group(1) if url else None


def extract_pr_number(output: str) -> int | None:
    """Find the merged PR number in command output.

    Prefers a legacy `/pull/N` URL (most recent line); otherwise reads gh's
    success marker "Squashed and merged pull request #N" — including the
    cross-repo "owner/repo#N" variant — via the shared `_hookio` helper.
    """
    for line in reversed(output.strip().splitlines()):
        m = _PR_URL_RE.search(line)
        if m:
            return int(m.group(1))
    return merge_pr_number_from_output(output)


def get_pr_body(pr_number: int, repo: str) -> str | None:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "body", "--repo", repo],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("body", "")
    except Exception:
        return None


def parse_closes_numbers(body: str) -> list[int]:
    return [int(n) for n in _CLOSES_RE.findall(body)]


def find_project_item(issue_number: int, config: dict) -> str | None:
    try:
        result = subprocess.run(
            [
                "gh", "project", "item-list", config["project_number"],
                "--owner", config["project_owner"],
                "--format", "json",
                "--limit", "1000",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        for item in data.get("items", []):
            if item.get("content", {}).get("number") == issue_number:
                return item["id"]
        return None
    except Exception:
        return None


def move_to_done(item_id: str, config: dict) -> bool:
    try:
        result = subprocess.run(
            [
                "gh", "project", "item-edit",
                "--project-id", config["project_node_id"],
                "--id", item_id,
                "--field-id", config["status_field_id"],
                "--single-select-option-id", config["done_option_id"],
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        return False


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not scan_top_level(command, _check_merge_stmt):
        sys.exit(0)

    output = read_command_output(data)
    cwd = data.get("cwd", "")

    config = load_config(cwd)
    if config is None:
        sys.exit(0)

    if not config.get("status_field_id") or not config.get("done_option_id"):
        sys.exit(0)

    repo = config.get("repo", "")
    if not repo:
        sys.exit(0)

    # Prefer the repo named in an explicit PR URL over cwd's own config — the
    # merge's actual target, not necessarily the session's pinned directory
    # (dev-env#559: a bare cross-repo PR URL, with no cd-chain and no --repo
    # flag, silently resolved to cwd's repo and fetched the wrong PR's body).
    # A bare `gh pr merge --squash --delete-branch` names no repo — keep
    # config's, unchanged from pre-#559 behavior.
    command_repo = extract_repo_from_command(command)
    if command_repo is not None and command_repo.lower() != repo.lower():
        # cwd's config (project_number/project_node_id/status_field_id/
        # done_option_id below) is scoped to `repo`, not the repo actually
        # named in the command — those fields don't apply cross-repo, and
        # proceeding risks moving an unrelated, same-numbered issue on the
        # WRONG repo's board (the #559 incident: a dev-env merge, resolved
        # against a lifting-logbook-pinned cwd, moved a same-numbered
        # lifting-logbook issue to Done). Skip the whole operation rather
        # than guess.
        sys.exit(0)
    if command_repo is not None:
        repo = command_repo

    # Prefer the PR number named in the command; fall back to gh's success marker
    # for the bare `gh pr merge --squash --delete-branch` form (#380).
    pr_number = extract_pr_number_from_command(command)
    if pr_number is None:
        pr_number = extract_pr_number(output)

    # Confirm an actual merge (not a queued --auto or a failed merge). The
    # success marker is printed even from a worktree, where gh exits non-zero on
    # local-checkout cleanup (issue #275) — so gate on the marker first. gh's
    # already-printed success marker does not always survive to this hook's
    # captured output when it exits abruptly right after that same local-cleanup
    # failure (dev-env#489) — a missed move-to-Done has no other backstop, so
    # confirm via a live `gh pr view` call rather than silently giving up.
    if not merge_succeeded(output):
        # `gh pr merge --help` (or any other non-mutating gh pr merge invocation
        # that prints no marker) can categorically never attempt a real merge —
        # treat it exactly like "not a merge command at all" rather than paying
        # a live gh pr view confirmation that resolves against cwd's current
        # branch and can misattribute an unrelated already-merged PR (dev-env#557).
        if is_merge_help_only(command):
            sys.exit(0)
        exit_code = data.get("tool_response", {}).get("exitCode", -1)
        if not should_confirm_via_gh(exit_code, output):
            sys.exit(0)
        confirmed_number = confirm_merge_via_gh(pr_number, repo, cwd)
        if confirmed_number is None:
            sys.exit(0)
        pr_number = confirmed_number

    if pr_number is None:
        sys.exit(0)

    body = get_pr_body(pr_number, repo)
    if not body:
        sys.exit(0)

    issue_numbers = parse_closes_numbers(body)
    if not issue_numbers:
        sys.exit(0)

    messages = []
    for issue_number in issue_numbers:
        item_id = find_project_item(issue_number, config)
        if item_id is None:
            messages.append(
                f"[project-hook] Issue #{issue_number} not found in project "
                f"(project {config['project_number']}, owner {config['project_owner']}).\n"
                f"  Move manually:\n"
                f"    gh project item-edit --project-id {config['project_node_id']} "
                f"--id <item-id> --field-id {config['status_field_id']} "
                f"--single-select-option-id {config['done_option_id']}"
            )
            continue

        if move_to_done(item_id, config):
            messages.append(
                f"[project-hook] Issue #{issue_number} moved to Done "
                f"(item {item_id})."
            )
        else:
            messages.append(
                f"[project-hook] Failed to move Issue #{issue_number} to Done.\n"
                f"  Move manually:\n"
                f"    gh project item-edit --project-id {config['project_node_id']} "
                f"--id {item_id} --field-id {config['status_field_id']} "
                f"--single-select-option-id {config['done_option_id']}"
            )

    if messages:
        print("\n\n".join(messages), file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
