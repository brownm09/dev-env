#!/usr/bin/env python3
"""Claude Code Stop hook — reliable-event safety net for inert PostToolUse hooks.

In sessions launched as background tasks / via `spawn_task` (SDK-driven), *all*
PostToolUse settings hooks are silently inert — no project-board add after
`gh issue create`/`gh pr create`, no Done-move / usage snapshot after
`gh pr merge` — while UserPromptSubmit, PreToolUse, and Stop hooks from the same
settings file still fire. This is an upstream Claude Code Desktop limitation that
no PostToolUse hook-code change can fix because the hooks never dispatch
(ADR-053, dev-env #381; anthropics/claude-code#42336, #53494).

This Stop hook converts that *silent* gap into a *visible* one. It scans the
just-ended session's transcript and, when a board-relevant `gh` command ran but
**no** PostToolUse hook fired all session, emits a one-line advisory pointing to
the documented manual fallback. Because a Stop hook's exit-0 stdout is invisible
to Claude (transcript-only), the advisory is delivered on **exit-2 stderr** — the
only Stop channel that reaches the model (ADR-091/103) — blocking the stop once so
the reminder is actually seen. It fires at most once (a `stop_hook_active` loop
guard plus a per-session sentinel), then exits 0.

Detection (no `gh` calls, no `project` scope — purely the transcript the Stop
payload points at):

  inert  ==  a dev-env (project #3) board action is present in the transcript
             AND zero transcript `attachment` records carry
             `attachment.hookEvent == "PostToolUse"`.

The harness writes an `attachment` record whenever a hook produces output; a
PostToolUse hook that ran emits either a `hook_success` (exit 0) or a
`hook_blocking_error` (exit 2, e.g. post-tool-use.py) attachment, and both carry
`hookEvent == "PostToolUse"`. Zero such attachments alongside a board action that
*would* have produced one in a healthy session is the inert signature.

False-positive guards:
  * Only **high-confidence dev-env actions** trigger: a `gh issue/pr create`
    whose output carries a `github.com/brownm09/dev-env/(issues|pull)/N` URL (a
    guaranteed post-tool-use.py exit-2 in a healthy session), or a `gh pr merge`
    for a dev-env PR with no hard-merge-failure in its output.
  * Because **any** PostToolUse attachment all session ⇒ stay silent, the
    legitimate different-repo / no-config silent-skip paths (ADR-049) can never
    trip it: in a healthy session at least one PostToolUse hook leaves a record.
  * gh's "Squashed and merged" success marker is *not* preserved in the
    transcript (it goes to stderr), so merge detection keys off the command +
    dev-env PR scope + absence of a hard-merge-failure, treating the issue-#275
    worktree cleanup-failure tail as a successful merge.

Fires at most once per session; once it has advised or confirmed the session is
healthy (any PostToolUse attachment present), a scratch sentinel short-circuits the
transcript re-scan on later Stops. The advisory blocks once (exit 2 + stderr); the
`stop_hook_active` loop guard prevents a re-block on the immediate continuation, and
`mark_resolved` runs *after* the stderr emission so a failed delivery leaves the
session unresolved to retry on the next Stop (dev-env#629). Every other path exits 0.

Stdin JSON shape (Stop):
  {"session_id": "uuid", "transcript_path": "/abs/path/to/session.jsonl",
   "stop_hook_active": false, ...}

See ADR-053 (the inert-PostToolUse limitation), ADR-055 (this safety net), and
ADR-091/103 (the exit-2 Stop delivery contract).
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import _hookout
import _hookutil
import json
import re
import sys
from pathlib import Path

# Shared transcript-record readers (ADR-090). `_result_text` is imported only so
# it stays reachable as a module attribute for the existing test suite (the
# module itself uses it solely via iter_bash_calls) — the same
# module-attribute-indirection the tests rely on (ADR-073).
from _hookutil import iter_bash_calls, load_records, _result_text  # noqa: F401

# mask_quoted_spans (dev-env#626, ADR-050 Amendment 15; also dev-env#650,
# Amendment 19) and mask_prose_flag_values (dev-env#634, ADR-050 Amendment 17)
# -- see _devenv_merge_pr.
from _hookio import mask_prose_flag_values, mask_quoted_spans

SENTINEL_PREFIX = "posttooluse-inert-resolved-"

# --- dev-env (project #3) scoping ----------------------------------------------
# A board action only counts when it is unambiguously dev-env's, so the advisory
# can never fire for a create/merge in some other (or unconfigured) repo whose
# PostToolUse silence is legitimate.
_ISSUE_CREATE_RE = re.compile(r"\bgh\s+issue\s+create\b")
_PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")
_MERGE_RE = re.compile(r"\bgh\s+pr\s+merge\b")
_DEVENV_CREATE_URL_RE = re.compile(
    r"https://github\.com/brownm09/dev-env/(issues|pull)/(\d+)"
)
# `_devenv_merge_pr` searches this against a `mask_prose_flag_values`-masked
# copy of `args` (dev-env#634, ADR-050 Amendment 17), so a --subject/--body
# value containing a decoy dev-env PR URL can't be mistaken for a genuine
# self-identifying signal -- a bare (unquoted, or quoted-but-not-inside-a-
# prose-flag) dev-env PR URL is untouched by that masking.
_DEVENV_PR_URL_RE = re.compile(r"https://github\.com/brownm09/dev-env/pull/(\d+)")
DEVENV_REPO = "brownm09/dev-env"
# The argument span of the `gh pr merge` invocation only (up to the next shell
# separator) -- so a `/pull/N` URL in an unrelated flag value or a chained sibling
# command cannot hijack the dev-env scoping or the PR-number extraction. Mirrors
# post-pr-merge-project.py's _MERGE_ARGS_RE.
_MERGE_ARGS_RE = re.compile(r"\bgh\s+pr\s+merge\b([^\n;|&]*)")
# A bare positional PR-number token (`42`) within those args; a digit run inside a
# URL (`/pull/42`) or a flag value (`--foo=12`) is not a standalone token.
# `_devenv_merge_pr` runs this against a mask_quoted_spans-masked copy of
# `args` (dev-env#650, ADR-050 Amendment 19), so a --subject/--body value
# containing a space-separated bare number ("resolves 42 items") can no
# longer be mistaken for the real positional PR number either -- the
# (?<!\S)/(?=\s|$) boundary alone can't tell "whitespace inside a quoted
# value" from "whitespace between top-level tokens," the same gap Amendment
# 15 already closed for _REPO_FLAG_RE. mask_quoted_spans (not
# mask_prose_flag_values) is used because this decoy shape is not scoped to
# a --subject/--body/-t/-b flag specifically -- it could appear inside any
# quoted value.
_MERGE_POS_NUM_RE = re.compile(r"(?<!\S)(\d+)(?=\s|$)")
# A queued `--auto` only *enables* auto-merge -- it is not a completed merge, and
# even a healthy session would not Done-move it yet (cf. post-pr-merge-project.py).
_AUTO_FLAG_RE = re.compile(r"(?<!\S)--auto(?:=\S+)?(?=\s|$)")
# An explicit `--repo owner/name` on the merge invocation overrides the cwd
# scope. Also matches gh's `-R` shorthand for `--repo` (dev-env#616), same
# `(?<!\S)` standalone-token discipline as `_AUTO_FLAG_RE` above so the flag
# can't match mid-word. `_devenv_merge_pr` runs this against a
# mask_quoted_spans-masked copy of `args` (dev-env#626, ADR-050 Amendment 15),
# so a `--subject`/`--body` value containing a space-separated "-R other/repo"
# substring can no longer false-match either.
_REPO_FLAG_RE = re.compile(r"(?<!\S)(?:--repo|-R)[=\s]+(\S+)")
# A genuine merge *failure* (not the harmless issue-#275 worktree cleanup tail,
# which means the PR merged but the local branch could not be deleted).
_HARD_MERGE_FAIL_RE = re.compile(
    r"not mergeable|merge conflict|failed to merge|GraphQL:.*mergeable",
    re.IGNORECASE,
)
# cwd inside the dev-env repo (canonical `.../Git/dev-env`, a Claude-managed
# worktree under it, or a sibling worktree `dev-env-<suffix>`).
_DEVENV_CWD_RE = re.compile(r"[/\\]dev-env(?:-[\w.-]+)?(?=[/\\]|$)", re.IGNORECASE)


# --- pure helpers (offline-testable) -------------------------------------------

def posttooluse_attachment_present(records: list[dict]) -> bool:
    """True iff any transcript `attachment` record is from a PostToolUse hook.

    Matches both exit-0 (`hook_success`) and exit-2 (`hook_blocking_error`)
    attachments via `attachment.hookEvent == "PostToolUse"`. Presence of any such
    record proves PostToolUse dispatch worked this session, so the gap (if any) is
    legitimate, not the inert limitation — the advisory must stay silent.
    """
    for rec in records:
        if rec.get("type") != "attachment":
            continue
        att = rec.get("attachment")
        if isinstance(att, dict) and att.get("hookEvent") == "PostToolUse":
            return True
    return False


def _is_devenv_cwd(cwd: str) -> bool:
    return bool(_DEVENV_CWD_RE.search(cwd or ""))


def _devenv_merge_pr(command: str, cwd: str) -> str | None:
    """Return the dev-env PR number a `gh pr merge` invocation targets, else None.

    Scoped to the merge invocation's own argument span (`_MERGE_ARGS_RE`) so a
    `/pull/N` URL in an unrelated flag value or a chained sibling command can't
    hijack it. Returns None for a queued `--auto` (not a completed merge) or an
    explicit non-dev-env `--repo`. Repo identity: an explicit `--repo` wins, else a
    dev-env `/pull/N` URL self-identifies, else a bare positional number is dev-env
    only from a dev-env cwd. PR number: positional token preferred, then the URL.

    The args-region boundary itself (`_MERGE_ARGS_RE.search`) is run against a
    `mask_quoted_spans`-masked copy of `command` first (dev-env#660, ADR-050
    Amendment 20), not the raw command -- `_MERGE_ARGS_RE`'s negated character
    class stops at any single `&`/`|`/`;`/`\n` with no quote-awareness of its
    own, so a --subject/--body value containing a bare separator character
    (even an ordinary one like `--subject "R&D tracking"`, not just a
    deliberately crafted `&&`) truncated `args` before a later real `--repo` or
    dev-env PR-number was ever seen, silently producing a missed (or, from a
    dev-env-shaped cwd, falsely attributed) board advisory. mask_quoted_spans is
    length-preserving, so the match's span offsets against the masked command
    apply unchanged to the original -- `args` below is the REAL (unmasked) text.

    The `--repo`/`-R` flag check and the bare positional PR-number match both
    run against the same `mask_quoted_spans`-masked copy of that (now correctly
    bounded) `args` (dev-env#626, ADR-050 Amendment 15; the positional-number
    match added in dev-env#650, Amendment 19), so a `--subject`/`--body` value
    containing a space-separated "-R other/repo" substring or a bare decoy
    number ("resolves 42 items") cannot be mistaken for a real flag or the
    real PR number. `url_m` runs against a `mask_prose_flag_values`-masked copy
    of `args` instead (dev-env#634, ADR-050 Amendment 17), so a --subject/--body
    value containing a decoy dev-env PR URL can't be mistaken for a genuine
    self-identifying signal either — while a bare (not inside a prose-flag
    value) dev-env PR URL, quoted or not, is untouched by that masking and
    still self-identifies exactly as before. `url_m` is reused below for the
    PR-number fallback.

    These three amendments are complementary, not overlapping: Amendment 20
    ensures `args` itself is not prematurely truncated before any WITHIN-region
    search runs; Amendments 15/17/19 ensure those searches aren't hijacked by
    a decoy once `args` is correctly bounded.
    """
    am = _MERGE_ARGS_RE.search(mask_quoted_spans(command))
    if not am:
        return None
    start, end = am.span(1)
    args = command[start:end]
    if _AUTO_FLAG_RE.search(args):
        return None

    masked_quoted_args = mask_quoted_spans(args)
    repo_m = _REPO_FLAG_RE.search(masked_quoted_args)
    url_m = _DEVENV_PR_URL_RE.search(mask_prose_flag_values(args))
    if repo_m:
        is_devenv = repo_m.group(1) == DEVENV_REPO
    elif url_m:
        is_devenv = True  # a dev-env /pull/N URL names the repo itself
    else:
        is_devenv = _is_devenv_cwd(cwd)
    if not is_devenv:
        return None

    num_m = _MERGE_POS_NUM_RE.search(masked_quoted_args)
    if num_m:
        return num_m.group(1)
    return url_m.group(1) if url_m else None


def detect_board_actions(calls: list[tuple[str, str, str]]) -> list[dict]:
    """Detect high-confidence dev-env board actions among paired Bash calls.

    A *create* counts when the command is `gh issue/pr create` and its output
    carries a dev-env issue/PR URL (a successful create → board add expected).
    A *merge* counts when the command is a completed `gh pr merge` naming a dev-env
    PR (see `_devenv_merge_pr`: not a queued `--auto`, not another `--repo`) and the
    output shows no hard merge failure (Done-move + usage snapshot expected).
    """
    actions: list[dict] = []
    for command, output, cwd in calls:
        output = output or ""
        if _ISSUE_CREATE_RE.search(command) or _PR_CREATE_RE.search(command):
            m = _DEVENV_CREATE_URL_RE.search(output)
            if m:
                kind = "issue" if m.group(1) == "issues" else "PR"
                actions.append({"action": "create", "label": f"{kind} {m.group(0)}"})
        if _MERGE_RE.search(command):
            pr = _devenv_merge_pr(command, cwd)
            if pr and not _HARD_MERGE_FAIL_RE.search(output):
                actions.append({"action": "merge", "label": f"PR #{pr}"})
    return actions


def should_emit(records: list[dict]) -> list[dict] | None:
    """Return detected dev-env board actions when PostToolUse was inert this
    session, else None. The presence of *any* PostToolUse attachment short-circuits
    to None (dispatch worked → no advisory)."""
    if posttooluse_attachment_present(records):
        return None
    actions = detect_board_actions(iter_bash_calls(records))
    return actions or None


def format_advisory(actions: list[dict]) -> str:
    # ASCII by construction: this text is emitted on exit-2 stderr, which Claude
    # Code pipes as cp1252 on Windows — a char outside it would raise at write time
    # and the whole advisory would vanish. main() routes it through
    # _hookout.ascii_sanitize as a wire-safety backstop, but keeping the source
    # ASCII (`->`, `-`, no emoji) keeps the sanitizer a no-op here.
    labels = "\n".join(f"    - {a['action']}: {a['label']}" for a in actions)
    return (
        "[posttooluse-inert] PostToolUse hooks did not fire this session "
        "(background/SDK-launched - ADR-053); GitHub Project #3 automation was "
        "silently skipped.\n"
        "  Board-relevant gh command(s) ran with no PostToolUse side-effect:\n"
        f"{labels}\n"
        "  Likely missing: board add (create) / Done-move + usage snapshot (merge).\n"
        "  Apply the manual fallback: dev-env CLAUDE.md -> 'GitHub Project' section "
        "-> Fallback (gh project item-add / item-edit). Advisory only; nothing was blocked."
    )


# --- I/O (thin, untested per the pure-helper convention) -----------------------

def mark_resolved(session_id: str) -> None:
    """Record that this session needs no further checking on later Stops."""
    if not session_id:
        return
    try:
        _hookutil.SCRATCH.mkdir(exist_ok=True)
        _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).write_text("")
    except Exception:
        pass


def main() -> None:
    # Defensive: degrade an unencodable char to a replacement instead of letting the
    # exit-2 stderr write raise. The advisory is ASCII by construction and is also
    # routed through _hookout.ascii_sanitize below; this protects future edits.
    try:
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass

    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)

    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    session_id = data.get("session_id") or ""
    stop_hook_active = bool(data.get("stop_hook_active"))

    # Resolved once per session — skip the transcript re-scan on later Stops. (Stop
    # fires at every turn-end, many times per session.)
    if session_id and _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).exists():
        sys.exit(0)

    tpath_str = data.get("transcript_path") or ""
    tpath = Path(tpath_str) if tpath_str else None
    if (tpath is None or not tpath.exists()) and session_id:
        tpath = _hookutil.find_transcript(session_id)
    if tpath is None or not tpath.exists():
        sys.exit(0)

    try:
        records = load_records(tpath)
    except Exception:
        sys.exit(0)

    actions = should_emit(records)
    if actions:
        # The inert signature was detected. A Stop hook's exit-0 stdout is invisible
        # to Claude, so BLOCK (exit 2 + stderr) — the one Stop channel that reaches
        # the model (ADR-091/103). This mirrors ADR-100's stop-journal-stub-checkpoint
        # and journal-stop-check Check 1: a manual stderr write ascii_sanitize-d via
        # _hookout, then a literal `sys.exit(2)`. mark_resolved between the two writes
        # a NoReturn emit_block would forbid, and the literal exit code keeps the gate
        # able to see this stderr write is governed by exit 2 (a variable exit code
        # would read as gov-0 -> a false Check A). Gate the block on
        # `not stop_hook_active` so the continuation after our own block never
        # re-blocks (the loop guard); the per-session sentinel then suppresses later
        # fresh Stops.
        if not stop_hook_active:
            # Emit FIRST, mark_resolved AFTER — so a failed stderr write (caught by
            # the outer guard -> exit 0) leaves the session unresolved to retry on
            # the next Stop, rather than silencing an undelivered warning (dev-env#629).
            sys.stderr.write(_hookout.ascii_sanitize(format_advisory(actions)) + "\n")
            mark_resolved(session_id)
            sys.exit(2)
        # A stop_hook_active continuation reaching here means the sentinel write must
        # have failed on the prior Stop; do NOT re-block — fall through to exit 0.
    elif posttooluse_attachment_present(records):
        # PostToolUse dispatch works this session (a session-level property; ADR-053),
        # so it can never be inert — resolve it so later Stops skip the full re-scan.
        # A session with no board action *and* no PostToolUse attachment stays
        # unresolved, so a board action later in the session is still caught.
        mark_resolved(session_id)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
