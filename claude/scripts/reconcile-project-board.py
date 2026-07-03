#!/usr/bin/env python3
"""Reconcile a GitHub Project board against its repo's open issues.

Backstop for the gap ADR-053 documents: the `post-tool-use.py` PostToolUse hook
auto-adds each newly-created issue to the configured project board and prompts for
its required fields (Impact / Why on dev-env's board #3) — but PostToolUse hooks are
inert in background / `spawn_task` / SDK-launched sessions, so issues filed from such
a session are silently never added to the board, missing their Impact, Why, and the
Status workflow entirely (motivating instance: dev-env #434/#435/#436, filed by the
background memory-audit session #363).

This script lists a repo's open issues and the project's items, computes the set
difference (orphans = open issues not on the board), adds each orphan, then reports
the orphans — and any *pre-existing* open board items — still missing a required
field, emitting the exact `gh project item-edit` commands. It **never guesses** a
field value and **never** mutates single-select *options* (so it cannot trip the
option-mutation hazard documented in the global CLAUDE.md), which is what makes it
safe to run unattended (the nightly `reconcile-project-board` routine) as well as by
hand. `--scan-dir` applies the same add-only + report-only reconcile to every
configured repo under a directory in one run (dev-env#462, ADR-070) instead of just
the repo the script is invoked from.

Config: reads `.claude/hook-config.json` from the repo root — the same file
`post-tool-use.py` reads, so the project number/owner/node-id and the required-field
IDs never drift between the add-hook and this reconciler. In single-repo mode, a repo
with no such config is not reconcilable and the script exits 1. In `--scan-dir` mode a
repo with no config (most repos — board tracking is opt-in) is silently skipped
instead; only a `gh` `project`-scope failure aborts the whole scan. The default repo
root is the *canonical* checkout of the repo this script lives in: when invoked from a
Claude-managed worktree (`.../.claude/worktrees/<name>/...`, where hook-config is
absent in a project that gitignores it — dev-env's own convention, not every project's,
dev-env#527) the canonical root is derived by stripping the worktree segment, via the
shared `_worktree_canon.canonical_repo_root` (dev-env#454, ADR-073) —
the same resolver `post-tool-use.py` uses. Repos discovered by `--scan-dir` are always
primary checkouts (worktrees are excluded by construction — see `find_git_repos`), so
canonicalization never applies to them.

Usage:
    py -3 reconcile-project-board.py [--repo-root PATH] [--dry-run]
    py -3 reconcile-project-board.py --scan-dir PATH [--dry-run]

    --repo-root PATH   repo whose .claude/hook-config.json drives the reconcile
                       (default: the canonical checkout of this script's repo)
    --scan-dir PATH    discover and reconcile every git repo directly under PATH that
                       has a .claude/hook-config.json with repo/project_number/
                       project_owner set (repos without one are skipped, not failed).
                       Takes precedence over --repo-root when both are given.
    --dry-run          report orphans + missing fields without adding anything

Exit 0  — single-repo mode ran successfully; or a --scan-dir sweep completed (even if
          individual repos were skipped/failed — see repos_skipped/repos_failed in the
          final RESULT line)
Exit 1  — single-repo mode: operational failure (no config, gh list failed, missing
          `project` scope). --scan-dir mode: scan_dir itself could not be read
          (missing/no permission), or the scan aborted early because gh is missing the
          `project` scope for listing OR adding — a token-level failure that would
          repeat identically for every remaining repo, so the scan stops immediately
          instead of repeating the same error once per repo. A RESULT: line is always
          printed before exiting, in every case above, so the routine's "read the final
          RESULT: line" instruction always has something to read.

The pure helpers (open_issue_numbers / board_issue_numbers /
compute_orphans / field_key / colliding_required_fields / is_truncated /
item_missing_fields / board_items_missing_fields / looks_like_scope_error /
render_report / render_scan_summary / _added_and_failed / _validated_config) are
unit-tested offline in tests/test_reconcile_project_board.py — _validated_config needs
only a real tmp-dir hook-config.json, no gh/mocking, so its ok / no-config / bad-config
statuses (including a non-dict top-level JSON payload) are pinned directly.
`canonical_repo_root` and `add_to_project` are shared with post-tool-use.py via
`_worktree_canon.py` and `_gh_project.py` respectively (dev-env#454, ADR-073) —
`canonical_repo_root` is unit-tested offline in tests/test_worktree_canon.py, and the gh
boundary (fetch_* / add_to_project) is not mocked, matching the repo's no-subprocess-mock
convention. find_git_repos is imported from the shared _repo_scan module (ADR-072) —
its own unit tests live in tests/test_repo_scan.py, shared with prune-merged-worktrees.py
and reclaim-worktree-disk.py's --scan-dir mode, which import the same helper.
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import argparse
import json
import os
import subprocess
import sys

from _gh_project import add_to_project
from _repo_scan import find_git_repos
from _worktree_canon import canonical_repo_root

CONFIG_FILE = ".claude/hook-config.json"

# `gh project item-list --format json` items may expose any of these as gh's own built-in
# top-level keys — the exact set observed varies by which system fields a given project
# surfaces (dev-env's board currently emits content/id/labels/repository/status/title plus
# whichever custom fields are set). Kept as a conservative superset, not the exact set of
# any one board, so the guard stays safe when this script is reused against a different
# project's board. A `required_fields` entry whose name lowercases to one of these would
# silently read the wrong value in item_missing_fields (e.g. a field literally named
# "Status" would read the built-in status string and always appear "present") —
# colliding_required_fields() guards against that at config-load time rather than letting
# it silently mask a real gap.
_RESERVED_ITEM_KEYS = frozenset({
    "id", "type", "title", "body", "content", "repository", "url",
    "status", "labels", "milestone", "assignees", "reviewers", "number",
})


class GhError(RuntimeError):
    """A `gh` invocation failed; carries the captured stderr for diagnosis."""


# --- pure helpers (unit-tested in tests/test_reconcile_project_board.py) ------


def field_key(field_name: str) -> str:
    """The key under which `gh project item-list --format json` exposes a field's
    value: the field name lowercased (interior spaces preserved). 'Impact' -> 'impact',
    'Why' -> 'why', 'Linked pull requests' -> 'linked pull requests'."""
    return (field_name or "").strip().lower()


def colliding_required_fields(required_names: list[str]) -> list[str]:
    """Which of `required_names` collide with a reserved gh item key (case-insensitive
    on the lowercased field_key) — these cannot be reliably detected by
    item_missing_fields, since the built-in key is always present and would make the
    field look set when it never was. Returns names in input order; [] when clean."""
    return [name for name in required_names if field_key(name) in _RESERVED_ITEM_KEYS]


def open_issue_numbers(issues: list[dict]) -> set[int]:
    """Set of issue numbers from `gh issue list --json number,...` output."""
    return {it["number"] for it in issues if isinstance(it.get("number"), int)}


def board_issue_numbers(items: list[dict], repo: str) -> set[int]:
    """Set of issue numbers already on the board for `repo`, from
    `gh project item-list --format json` items. Only Issue-type items whose
    `content.repository` matches `repo` are counted — PRs, draft items, and
    cross-repo items are ignored so the set difference can't misfire."""
    out: set[int] = set()
    for item in items:
        content = item.get("content") or {}
        if content.get("type") != "Issue":
            continue
        if repo and content.get("repository") != repo:
            continue
        n = content.get("number")
        if isinstance(n, int):
            out.add(n)
    return out


def compute_orphans(issues: list[dict], board_numbers: set[int]) -> list[dict]:
    """Open issues not yet on the board, as [{number, url, title}], sorted by number.
    This is the core set difference: open issues - issues already on the board."""
    orphans = [
        {"number": it["number"], "url": it.get("url", ""), "title": it.get("title", "")}
        for it in issues
        if isinstance(it.get("number"), int) and it["number"] not in board_numbers
    ]
    return sorted(orphans, key=lambda o: o["number"])


def item_missing_fields(item: dict, required_names: list[str]) -> list[str]:
    """Which of `required_names` are absent/empty on a board item. A field counts as
    present only when its lowercased-name key holds a non-empty, non-whitespace value
    (an unset single-select / empty text field is absent from the item JSON)."""
    missing = []
    for name in required_names:
        val = item.get(field_key(name))
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append(name)
    return missing


def board_items_missing_fields(
    items: list[dict], required_names: list[str], open_numbers: set[int], repo: str
) -> list[dict]:
    """For board items that are OPEN issues in `repo`, which required fields each lacks,
    as [{number, url, item_id, missing:[...]}] sorted by number. Restricting to *open*
    issues avoids nagging about long-closed Done items that legitimately never carried a
    Why."""
    out = []
    for item in items:
        content = item.get("content") or {}
        if content.get("type") != "Issue":
            continue
        if repo and content.get("repository") != repo:
            continue
        n = content.get("number")
        if not isinstance(n, int) or n not in open_numbers:
            continue
        missing = item_missing_fields(item, required_names)
        if missing:
            out.append(
                {
                    "number": n,
                    "url": content.get("url", ""),
                    "item_id": item.get("id", ""),
                    "missing": missing,
                }
            )
    return sorted(out, key=lambda o: o["number"])


def looks_like_scope_error(stderr: str) -> bool:
    """True when a `gh` failure is the missing-`project`-scope error, so the caller can
    print the `gh auth refresh -s project` hint instead of a raw stderr dump. Best-effort
    substring heuristic — a false negative just falls through to the raw-stderr branch,
    which is still a safe (if less helpful) outcome."""
    s = (stderr or "").lower()
    return "scope" in s and "project" in s


def _edit_command(item_id: str, field: dict, node_id: str) -> str:
    """One copy-pasteable `gh project item-edit` line for a single required field.
    Emits the command only — never a chosen value (no guessing)."""
    name = field.get("name", "Field")
    field_id = field.get("field_id", "<field-id>")
    if field.get("type") == "single_select":
        opts = field.get("options", {})
        opt_hint = (
            "   # options: " + ", ".join(f"{k}={v}" for k, v in opts.items())
            if opts
            else ""
        )
        return (
            f"    gh project item-edit --project-id {node_id} --id {item_id} "
            f"--field-id {field_id} --single-select-option-id <option-id>{opt_hint}"
        )
    return (
        f"    gh project item-edit --project-id {node_id} --id {item_id} "
        f'--field-id {field_id} --text "<{name.lower()}>"'
    )


def _added_and_failed(orphans: list[dict], dry_run: bool) -> tuple[int, int]:
    """(added, failed) counts for a list of orphans after the add attempt. dry_run means
    nothing was attempted, so both are 0. Shared by render_report (per-repo RESULT line)
    and _reconcile_repo (the --scan-dir aggregate) so the two definitions of "added" can
    never drift apart."""
    if dry_run:
        return 0, 0
    added = sum(1 for o in orphans if o.get("item_id"))
    return added, len(orphans) - added


def render_report(
    orphans: list[dict],
    preexisting_missing: list[dict],
    config: dict,
    dry_run: bool = False,
) -> str:
    """Build the full stdout report (ending in a machine-readable RESULT line).

    `orphans`   — [{number, url, title, item_id?}] just added (item_id set when the add
                  succeeded; absent/None in --dry-run or on add failure).
    `preexisting_missing` — [{number, url, item_id, missing:[...]}] board items already
                  present but lacking a required field.

    Pure: emits `gh project item-edit` commands for every field that needs a human
    decision but sets nothing itself. Returns the report text, ending in
    `RESULT: orphans_added=N add_failed=N needs_attention=N dry_run=...` — the routine
    reads add_failed too, since a partial add failure must not look like a clean run."""
    required_fields = config.get("required_fields", [])
    required_names = [f.get("name", "Field") for f in required_fields]
    by_name = {f.get("name"): f for f in required_fields}
    node_id = config.get("project_node_id", "<project-node-id>")
    proj_num = config.get("project_number", "<n>")

    added_count, add_failed = _added_and_failed(orphans, dry_run)

    lines: list[str] = []

    # --- orphans ------------------------------------------------------------
    if orphans:
        if dry_run:
            lines.append(f"Would add {len(orphans)} orphan issue(s) to project {proj_num}:")
        elif add_failed:
            lines.append(
                f"Added {added_count}/{len(orphans)} orphan issue(s) to project "
                f"{proj_num} ({add_failed} failed):"
            )
        else:
            lines.append(f"Added {added_count} orphan issue(s) to project {proj_num}:")
        for o in orphans:
            flag = "" if (dry_run or o.get("item_id")) else "  [ADD FAILED - re-run]"
            lines.append(f"  #{o['number']}  {o.get('title', '')}{flag}")
            lines.append(f"      {o['url']}")
    else:
        lines.append(
            f"No orphan issues - every open issue is already on project {proj_num}."
        )

    # --- needs-attention = orphans (all required fields) + pre-existing gaps -
    # Orphans were never on the board, so they cannot also appear in
    # preexisting_missing — the two lists are disjoint.
    attention: list[dict] = []
    for o in orphans:
        attention.append(
            {
                "number": o["number"],
                "url": o["url"],
                "item_id": o.get("item_id"),
                "missing": list(required_names),
            }
        )
    attention.extend(preexisting_missing)
    attention.sort(key=lambda a: a["number"])

    if attention:
        lines.append("")
        lines.append(
            f"{len(attention)} issue(s) need a required field set "
            f"(NOT guessed - set them yourself):"
        )
        for a in attention:
            lines.append(f"  #{a['number']} missing: {', '.join(a['missing'])}")
            item_id = a.get("item_id")
            if not item_id:
                # A dry-run orphan (not yet on the board) or a failed add — no item id
                # exists to target yet.
                lines.append("      (add to the board first, then set fields)")
                continue
            for name in a["missing"]:
                field = by_name.get(name)
                if field:
                    lines.append(_edit_command(item_id, field, node_id))
    else:
        lines.append("All open board issues have their required fields set.")

    lines.append("")
    lines.append(
        f"RESULT: orphans_added={added_count} add_failed={add_failed} "
        f"needs_attention={len(attention)} dry_run={'true' if dry_run else 'false'}"
    )
    return "\n".join(lines)


def render_scan_summary(
    repos_scanned: int,
    repos_skipped: int,
    repos_failed: int,
    orphans_added: int,
    add_failed: int,
    needs_attention: int,
    dry_run: bool = False,
) -> str:
    """The final aggregate line --scan-dir mode prints after every per-repo report, so
    "read the final RESULT: line" (the routine's existing instruction) still means the
    aggregate even though scan-dir mode prints one RESULT: line per repo along the way.

    repos_scanned  — git repos found under the scan directory
    repos_skipped  — of those, repos with no (or an incomplete) hook-config.json
    repos_failed   — repos with valid config where a non-scope `gh` call failed
    orphans_added / add_failed / needs_attention — summed across every successfully
        reconciled repo, same semantics as render_report's RESULT line."""
    return (
        f"RESULT: repos_scanned={repos_scanned} repos_skipped={repos_skipped} "
        f"repos_failed={repos_failed} orphans_added={orphans_added} "
        f"add_failed={add_failed} needs_attention={needs_attention} "
        f"dry_run={'true' if dry_run else 'false'}"
    )


# --- config ------------------------------------------------------------------


def load_config(repo_root: str) -> dict | None:
    """Load `<repo_root>/.claude/hook-config.json`, or None if absent/unparseable."""
    try:
        with open(os.path.join(repo_root, CONFIG_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def default_repo_root() -> str:
    """Canonical checkout root of the repo this script lives in. scripts/ -> claude/ ->
    root; canonicalized so a worktree invocation still finds the machine-local config."""
    script_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return canonical_repo_root(script_root)


def is_truncated(count: int, limit: int) -> bool:
    """True when a gh list call may have been silently capped at `limit` — `gh` caps
    results at --limit with no truncation signal, so a result count equal to the limit
    means more items might exist beyond it. Pure; the caller decides what to do (warn)."""
    return count >= limit


# --- network boundary (not unit-tested; repo avoids subprocess mocks) --------


def fetch_open_issues(repo: str, limit: int = 1000) -> list[dict]:
    result = subprocess.run(
        [
            "gh", "issue", "list", "--repo", repo, "--state", "open",
            "--json", "number,url,title", "--limit", str(limit),
        ],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if result.returncode != 0:
        raise GhError(result.stderr.strip() or "gh issue list failed")
    issues = json.loads(result.stdout or "[]")
    if is_truncated(len(issues), limit):
        print(
            f"[reconcile-board] WARNING: gh issue list hit the --limit {limit} cap - "
            f"results may be truncated and some open issues skipped this run.",
            file=sys.stderr,
        )
    return issues


def fetch_board_items(project_number: str, owner: str, limit: int = 1000) -> list[dict]:
    result = subprocess.run(
        [
            "gh", "project", "item-list", str(project_number), "--owner", owner,
            "--format", "json", "--limit", str(limit),
        ],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if result.returncode != 0:
        raise GhError(result.stderr.strip() or "gh project item-list failed")
    items = json.loads(result.stdout or "{}").get("items", [])
    if is_truncated(len(items), limit):
        print(
            f"[reconcile-board] WARNING: gh project item-list hit the --limit {limit} "
            f"cap - results may be truncated and some board items missed this run.",
            file=sys.stderr,
        )
    return items


def _reconcile_repo(config: dict, dry_run: bool) -> dict:
    """Fetch, compute orphans, add them (unless dry_run), and print the report for one
    repo whose config has already been validated (repo/project_number/project_owner
    present, no colliding required fields — see _validated_config). Returns
    {"status": "ok"|"scope-error"|"gh-error", "orphans_added": int, "add_failed": int,
    "needs_attention": int, "add_scope_error": bool}. Never raises — gh failures come
    back in "status" so each caller can apply its own fatal-vs-skip policy (single-repo
    mode fails fast on any status other than "ok"; --scan-dir mode isolates "gh-error"
    to one repo, aborts the whole scan on "scope-error", and also aborts on
    add_scope_error=True — a project-scope failure that only shows up at add time
    because gh's read and write project scopes are distinct; single-repo mode leaves
    that case under status "ok", unchanged from pre-refactor behavior, since it has no
    "remaining repos" to protect by aborting early)."""
    repo = config.get("repo", "")
    project_number = config.get("project_number", "")
    owner = config.get("project_owner", "")
    required_names = [f.get("name", "Field") for f in config.get("required_fields", [])]

    try:
        issues = fetch_open_issues(repo)
        items = fetch_board_items(project_number, owner)
    except GhError as e:
        msg = str(e)
        if looks_like_scope_error(msg):
            return {"status": "scope-error", "orphans_added": 0, "add_failed": 0, "needs_attention": 0, "add_scope_error": False}
        print(f"[reconcile-board] gh call failed: {msg}", file=sys.stderr)
        return {"status": "gh-error", "orphans_added": 0, "add_failed": 0, "needs_attention": 0, "add_scope_error": False}

    open_nums = open_issue_numbers(issues)
    board_nums = board_issue_numbers(items, repo)
    orphans = compute_orphans(issues, board_nums)
    preexisting_missing = board_items_missing_fields(items, required_names, open_nums, repo)

    scope_warned = False
    if not dry_run:
        for o in orphans:
            item_id, err = add_to_project(o["url"], project_number, owner, timeout=30)
            o["item_id"] = item_id
            if item_id is None and not scope_warned and looks_like_scope_error(err):
                print(
                    "[reconcile-board] WARNING: an add failed due to a missing 'project' "
                    "scope. Run:\n    gh auth refresh -s project\nthen re-run this script "
                    "to add the remaining orphans.",
                    file=sys.stderr,
                )
                scope_warned = True

    print(render_report(orphans, preexisting_missing, config, dry_run=dry_run))

    added_count, add_failed = _added_and_failed(orphans, dry_run)
    return {
        "status": "ok",
        "orphans_added": added_count,
        "add_failed": add_failed,
        "needs_attention": len(orphans) + len(preexisting_missing),
        "add_scope_error": scope_warned,
    }


def _validated_config(repo_root: str) -> tuple[dict | None, str, str | None]:
    """Load and validate repo_root's hook-config.json. Returns (config, status, message):
      status="ok"         — config is usable, message=None
      status="no-config"  — config=None, message=None (repo simply hasn't adopted board
                             tracking — the expected case for most --scan-dir repos)
      status="bad-config" — config=None, message=<fully-formatted stderr text> (config is
                             present but missing repo/project_number/project_owner, a
                             required_fields name collides with a reserved gh item key,
                             or the file's top-level JSON is not an object — e.g. a bare
                             array/string/number, which parses cleanly but isn't a usable
                             config)
    Shared by single-repo and scan-dir modes so the two can never validate a config
    differently; they differ only in how they react to each status."""
    config = load_config(repo_root)
    if config is None:
        return None, "no-config", None
    if not isinstance(config, dict):
        return None, "bad-config", (
            "[reconcile-board] hook-config.json does not contain a JSON object "
            "(top-level value is not a dict) — cannot reconcile."
        )

    repo = config.get("repo", "")
    project_number = config.get("project_number", "")
    owner = config.get("project_owner", "")
    if not (repo and project_number and owner):
        return None, "bad-config", (
            "[reconcile-board] hook-config.json is missing repo / project_number / "
            "project_owner — cannot reconcile."
        )

    required_names = [f.get("name", "Field") for f in config.get("required_fields", [])]
    colliding = colliding_required_fields(required_names)
    if colliding:
        return None, "bad-config", (
            "[reconcile-board] required_fields name(s) collide with gh's built-in "
            f"item keys and cannot be reliably detected: {', '.join(colliding)}. "
            "Rename the field in hook-config.json (or extend _RESERVED_ITEM_KEYS if "
            "the collision is unavoidable) before running this script."
        )
    return config, "ok", None


def _main_single_repo(repo_root: str, dry_run: bool) -> int:
    config, status, message = _validated_config(repo_root)
    if status == "no-config":
        print(
            f"[reconcile-board] no .claude/hook-config.json under {repo_root} - "
            f"nothing to reconcile.",
            file=sys.stderr,
        )
        return 1
    if status == "bad-config":
        print(message, file=sys.stderr)
        return 1

    result = _reconcile_repo(config, dry_run)
    if result["status"] == "scope-error":
        print(
            "[reconcile-board] gh is missing the 'project' scope. Run:\n"
            "    gh auth refresh -s project\n"
            "then re-run this script.",
            file=sys.stderr,
        )
        return 1
    if result["status"] == "gh-error":
        return 1
    return 0


def _main_scan_dir(scan_dir: str, dry_run: bool) -> int:
    repos = find_git_repos(scan_dir)
    if repos is None:
        # find_git_repos already printed the WARNING with the underlying OSError.
        # Still emit a RESULT line (all-zero) so "read the final RESULT: line" has
        # something to read on this path too, instead of looking identical to the
        # zero-repos-found case below.
        print(f"[reconcile-board] scan of {scan_dir} failed - nothing was reconciled")
        print()
        print(render_scan_summary(0, 0, 0, 0, 0, 0, dry_run=dry_run))
        return 1
    if not repos:
        print(f"[reconcile-board] no git repos found under {scan_dir}")
        print()
        print(render_scan_summary(0, 0, 0, 0, 0, 0, dry_run=dry_run))
        return 0
    print(f"[reconcile-board] found {len(repos)} repo(s) under {scan_dir}")

    totals = {"orphans_added": 0, "add_failed": 0, "needs_attention": 0}
    repos_skipped = 0
    repos_failed = 0
    scope_aborted = False

    for repo_root in repos:
        config, status, message = _validated_config(repo_root)
        if status == "no-config":
            repos_skipped += 1
            continue
        if status == "bad-config":
            print(f"\nRepo: {repo_root}")
            print(message, file=sys.stderr)
            repos_skipped += 1
            continue

        print(f"\nRepo: {config.get('repo', '')} ({repo_root})")
        result = _reconcile_repo(config, dry_run)
        if result["status"] == "scope-error":
            print(
                "[reconcile-board] gh is missing the 'project' scope. Run:\n"
                "    gh auth refresh -s project\n"
                "then re-run this script. Stopping the scan — every remaining repo "
                "would fail identically (the scope belongs to the gh token, not the "
                "repo).",
                file=sys.stderr,
            )
            scope_aborted = True
            break
        if result["status"] == "gh-error":
            repos_failed += 1
            continue

        totals["orphans_added"] += result["orphans_added"]
        totals["add_failed"] += result["add_failed"]
        totals["needs_attention"] += result["needs_attention"]

        if result["add_scope_error"]:
            print(
                "[reconcile-board] gh is missing the 'project' scope for add operations "
                "(list succeeded, add failed). Run:\n"
                "    gh auth refresh -s project\n"
                "then re-run this script. Stopping the scan — every remaining repo's "
                "adds would fail identically (the scope belongs to the gh token, not "
                "the repo).",
                file=sys.stderr,
            )
            scope_aborted = True
            break

    print()
    print(
        render_scan_summary(
            len(repos), repos_skipped, repos_failed,
            totals["orphans_added"], totals["add_failed"], totals["needs_attention"],
            dry_run=dry_run,
        )
    )
    return 1 if scope_aborted else 0


def main() -> int:
    # gh emits UTF-8 JSON and issue titles can contain non-cp1252 characters; force
    # UTF-8 on our own streams so printing a title (or punctuation) can't raise on a
    # Windows cp1252 console / pipe and abort the unattended routine.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Reconcile a GitHub Project board against open issues.")
    parser.add_argument(
        "--repo-root",
        default=None,
        help="repo whose .claude/hook-config.json drives the reconcile "
        "(default: canonical checkout of this script's repo)",
    )
    parser.add_argument(
        "--scan-dir",
        default=None,
        help="discover and reconcile every git repo directly under PATH that has a "
        ".claude/hook-config.json (repos without one are skipped). Takes precedence "
        "over --repo-root when both are given.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report without adding anything"
    )
    args = parser.parse_args()

    if args.scan_dir:
        return _main_scan_dir(args.scan_dir, args.dry_run)

    repo_root = args.repo_root or default_repo_root()
    return _main_single_repo(repo_root, args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
