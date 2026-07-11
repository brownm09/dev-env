#!/usr/bin/env python3
"""Claude Code PreToolUse hook — detects 'gh pr create' in Bash commands and
emits a systemMessage checklist requiring test verification before the PR lands.
Also warns when skill/hook/script/routine files were changed without updating
README.md or docs/REFERENCE.md.

Enforcement model (layered):
  - This hook is advisory only (always exits 0). It fires a systemMessage
    reminder visible in the Claude Code UI.
  - Hard enforcement is handled by CLAUDE.md instruction: Claude must not
    run `gh pr create` until the project's ## Testing section is satisfied.
    The hook is a secondary reminder, not the gate.

Also shows the current branch/repo (dev-env#573) — `gh pr create` with no
explicit `--head` silently infers its head branch from the current checkout,
which is exactly the state that can go stale after a session's tracked cwd
silently reverts (e.g. after an intermittent Git Bash crash). Additionally
appends a drift warning when the repo/branch recorded by
post-tool-use-cwd-track.py after the session's last Bash call differs from
the repo/branch right now. Both are advisory only; never blocks.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 — always; hook is advisory only.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

import _bash_state

_GH_PR_CREATE_RE = re.compile(
    r"(?:^|&&|\|+|;|\n)\s*gh\s+pr\s+create\b"
)

_DOC_PATHS = ("claude/skills/", "claude/hooks/", "claude/scripts/", "claude/routines/")
_REF_DOCS = {"README.md", "docs/REFERENCE.md"}

_SCRATCH_DIR = "C:/Users/brown/.claude/scratch"


def _doc_reconciliation_warning(cwd):
    """Return a warning line if doc-reconciliation appears needed, else empty string."""
    if not cwd:
        return ""
    try:
        result = subprocess.run(
            ["git", "diff", "origin/main", "--name-only"],
            capture_output=True, text=True, cwd=cwd, timeout=10
        )
        if result.returncode != 0:
            return ""
        changed = result.stdout.splitlines()
    except Exception:
        return ""

    has_tooling_change = any(
        any(p.startswith(prefix) for prefix in _DOC_PATHS)
        for p in changed
    )
    if not has_tooling_change:
        return ""

    if any(p in _REF_DOCS for p in changed):
        return ""

    return (
        "  ⚠ DOCUMENTATION: Skill/hook/script/routine files changed without "
        "a matching README.md or docs/REFERENCE.md update — "
        "verify the Documentation Maintenance table in CLAUDE.md is satisfied."
    )


def _baseline_advisory(cwd):
    """Return an advisory line about the pre-existing test failure baseline (ADR-030).

    Three cases:
      - Opt-in flag absent → empty string (feature dormant for this repo).
      - Flag on, baseline file present → 'run baseline-tests diff' reminder.
      - Flag on, baseline file missing → 'no baseline captured' reminder.
    """
    if not cwd:
        return ""
    cfg_path = os.path.join(cwd, ".claude", "hook-config.json")
    if not os.path.exists(cfg_path):
        return ""
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return ""
    if cfg.get("baseline_test_failure_tracking") is not True:
        return ""

    try:
        repo = os.path.basename(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, cwd=cwd, timeout=5
            ).stdout.strip()
        )
        branch_raw = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=5
        ).stdout.strip()
    except Exception:
        return ""
    if not repo or not branch_raw:
        return ""
    branch = branch_raw.replace("/", "-")
    baseline_path = os.path.join(_SCRATCH_DIR, f"baseline_{repo}_{branch}.json")

    if os.path.exists(baseline_path):
        return (
            "  4. Run `baseline-tests diff` and address per the fix-on-touch rule "
            "(ADR-030) — new failures block; preexisting-touched failures must be "
            "fixed inline or filed and listed in the PR body."
        )
    return (
        "  4. ⚠ BASELINE: `baseline_test_failure_tracking` is enabled but no "
        f"baseline file exists at {baseline_path}. Pre-existing failures cannot be "
        "distinguished from new ones. Run `baseline-tests snapshot` from "
        "`origin/main` and re-cut this branch with `new-branch`, or accept the loss "
        "of fix-on-touch coverage for this PR."
    )


def build_checklist(
    baseline_line: str,
    doc_warning: str,
    branch: str | None,
    repo_root: str | None,
    drift_warning: str | None,
) -> str:
    display_branch = branch if branch is not None else "<detached HEAD or unknown>"
    display_repo = repo_root if repo_root is not None else "<unknown>"
    checklist = (
        "[pre-pr-check] Before this PR is created, confirm:\n"
        "  1. Ran the project test command (see ## Testing in project CLAUDE.md)\n"
        "  2. Tests passed (or documented why they are not applicable)\n"
        "  3. PR body includes what was tested and the outcome\n"
        f"  Current branch: {display_branch} (repo: {display_repo}) — confirm "
        "this is the branch you intend to open a PR from; pass --head <branch> "
        "explicitly if there's any doubt (dev-env#573)."
    )
    if drift_warning:
        checklist += "\n" + drift_warning
    if baseline_line:
        checklist += "\n" + baseline_line
    if doc_warning:
        checklist += "\n" + doc_warning
    return checklist


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
    if not _GH_PR_CREATE_RE.search(command):
        sys.exit(0)

    cwd = data.get("cwd", "")
    session_id = data.get("session_id", "") or ""
    doc_warning = _doc_reconciliation_warning(cwd)
    baseline_line = _baseline_advisory(cwd)
    repo_root, branch, drift_warning = _bash_state.drift_warning_for(session_id, cwd)

    checklist = build_checklist(baseline_line, doc_warning, branch, repo_root, drift_warning)

    print(json.dumps({"systemMessage": checklist}))
    sys.exit(0)


if __name__ == "__main__":
    main()
