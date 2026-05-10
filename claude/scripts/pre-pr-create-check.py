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
import json
import re
import subprocess
import sys

_GH_PR_CREATE_RE = re.compile(
    r"(?:^|&&|\|+|;|\n)\s*gh\s+pr\s+create\b"
)

_DOC_PATHS = ("claude/skills/", "claude/hooks/", "claude/scripts/", "claude/routines/")
_REF_DOCS = {"README.md", "docs/REFERENCE.md"}


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
    doc_warning = _doc_reconciliation_warning(cwd)

    checklist = (
        "[pre-pr-check] Before this PR is created, confirm:\n"
        "  1. Ran the project test command (see ## Testing in project CLAUDE.md)\n"
        "  2. Tests passed (or documented why they are not applicable)\n"
        "  3. PR body includes what was tested and the outcome"
    )
    if doc_warning:
        checklist += "\n" + doc_warning

    print(json.dumps({"systemMessage": checklist}))
    sys.exit(0)


if __name__ == "__main__":
    main()
