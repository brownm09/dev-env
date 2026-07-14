#!/usr/bin/env python3
"""Unit tests for pre-pr-create-check.py.

Exercises the pure helpers offline (no subprocess, no stdin, no filesystem):
the new `build_checklist()` formatter added for dev-env#573's branch-display
and drift-warning integration. `_doc_reconciliation_warning()`,
`_baseline_advisory()`, `current_branch()`, and `current_repo_root()` shell
out to git / read files and are not covered here (pure-helper convention,
matches this repo's other hooks; this file's pre-existing untested logic is
not backfilled per ADR-022's coverage gate — only the new behavior is
tested).

Usage:
    py -3 claude/scripts/tests/test_pre_pr_create_check.py

Exit 0 = all pass.
"""
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import importlib

pre_pr_create_check = importlib.import_module("pre-pr-create-check")


def test_detects_powershell_conditional_brace_pr_create() -> str:
    # dev-env#620: PowerShell 5.1 has no && (the tool's own description confirms
    # it's a parser error there), so its documented "run B only if A succeeds"
    # idiom is `A; if ($?) { B }` -- the added `{` anchor alternative catches
    # this exactly like the bash brace-group equivalent.
    assert pre_pr_create_check._GH_PR_CREATE_RE.search('git push; if ($?) { gh pr create --fill }')
    return "PowerShell 'A; if ($?) { gh pr create ... }' idiom is now detected (dev-env#620)"


def test_detects_bash_brace_group_pr_create() -> str:
    assert pre_pr_create_check._GH_PR_CREATE_RE.search("{ gh pr create --fill; }")
    return "bash brace-group '{ gh pr create ...; }' idiom is now detected too"


def test_build_checklist_baseline_no_extras() -> str:
    msg = pre_pr_create_check.build_checklist("", "", "feat/x", "C:/repo", None)
    assert msg.startswith("[pre-pr-check] Before this PR is created, confirm:\n")
    assert "  3. PR body includes what was tested and the outcome\n" in msg
    assert "Current branch: feat/x (repo: C:/repo)" in msg
    assert "--head <branch>" in msg
    assert "dev-env#573" in msg
    return "build_checklist always shows the numbered list plus a branch/repo display line"


def test_build_checklist_none_branch_and_repo_show_placeholders() -> str:
    msg = pre_pr_create_check.build_checklist("", "", None, None, None)
    assert "Current branch: <detached HEAD or unknown> (repo: <unknown>)" in msg, msg
    return "build_checklist renders placeholders for None branch/repo_root"


def test_build_checklist_appends_drift_warning() -> str:
    msg = pre_pr_create_check.build_checklist("", "", "feat/x", "C:/repo", "⚠ [cwd-drift] mismatch")
    assert "⚠ [cwd-drift] mismatch" in msg
    # Drift warning must appear before baseline/doc lines (there are none here,
    # so just confirm it appears once, after the branch-display line).
    assert msg.index("Current branch:") < msg.index("⚠ [cwd-drift]")
    return "build_checklist appends the drift warning after the branch-display line"


def test_build_checklist_preserves_baseline_and_doc_order() -> str:
    msg = pre_pr_create_check.build_checklist(
        "  4. baseline line", "  ⚠ DOCUMENTATION: doc line", "feat/x", "C:/repo", None
    )
    # Existing relative order (baseline before doc_warning) must be unchanged.
    assert msg.index("baseline line") < msg.index("doc line")
    return "build_checklist keeps baseline_line before doc_warning, matching prior behavior"


def test_build_checklist_omits_absent_optional_lines() -> str:
    msg = pre_pr_create_check.build_checklist("", "", "feat/x", "C:/repo", None)
    assert "baseline" not in msg.lower()
    assert "documentation" not in msg.lower()
    assert "cwd-drift" not in msg.lower()
    return "build_checklist omits baseline/doc/drift lines entirely when all are empty/None"


def main() -> int:
    tests = [
        ("_GH_PR_CREATE_RE: PowerShell conditional-brace idiom (dev-env#620)", test_detects_powershell_conditional_brace_pr_create),
        ("_GH_PR_CREATE_RE: bash brace-group idiom", test_detects_bash_brace_group_pr_create),
        ("build_checklist: baseline, no extras", test_build_checklist_baseline_no_extras),
        ("build_checklist: None branch/repo placeholders", test_build_checklist_none_branch_and_repo_show_placeholders),
        ("build_checklist: appends drift warning", test_build_checklist_appends_drift_warning),
        ("build_checklist: preserves baseline/doc order", test_build_checklist_preserves_baseline_and_doc_order),
        ("build_checklist: omits absent optional lines", test_build_checklist_omits_absent_optional_lines),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
