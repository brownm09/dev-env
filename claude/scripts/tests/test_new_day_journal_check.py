#!/usr/bin/env python3
"""Unit tests for new-day-journal-check.py's day-rollover detection (ADR-119, dev-env#866).

The day-rollover check exists because every journal discovery path keys on BOTH halves of
`draft/<DATE>` + `sessions/*/<DATE>_*.stub.md`: `/journal-compose` resolves its source
branch from the date, and `daily-journal-compose` gates on
`show-ref --verify refs/remotes/origin/draft/${DATE} || exit 0`. A stub written onto a
branch named for a different day is therefore composed by nothing and reported by nothing,
*silently* — the failure that left 26 stubs across 5 dates uncomposed on `origin/main`.

These tests pin the two pure helpers behind that check:

  - `branch_date` — the `draft/<date>[-suffix]` name parse, including the documented
    `-recovery` suffix form, and rejection of anything that isn't a real date.
  - `mismatched_stub_paths` — deliberately ONE-SIDED: only a stub dated *after* the branch
    is the rollover failure. Older-dated stubs are branch-lineage artifacts that
    `stale_draft_artifacts`/`unmerged_draft_branches` already cover, and including them
    buried the 6 real hits under 27 unrelated ones on the first live run.

The git-reading helpers (`canonical_current_branch`, `branch_stub_paths`) and
`day_rollover_message` itself are subprocess boundaries, left untested per this repo's
fixture-only / no-subprocess-mock convention (matching `check_pr_state` in
`test_reconcile_open_prs.py`).

Usage:
    py -3 claude/scripts/tests/test_new_day_journal_check.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "new-day-journal-check.py"

# The script imports _winsubp / _hookutil (siblings in scripts/); make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("new_day_journal_check", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

branch_date = mod.branch_date
mismatched_stub_paths = mod.mismatched_stub_paths


def test_branch_date_parses_plain_and_suffixed() -> str:
    assert branch_date("draft/2026-07-21") == "2026-07-21"
    assert branch_date("draft/2026-07-21-recovery") == "2026-07-21", \
        "the documented -recovery form is still a dated draft branch"
    assert branch_date("draft/2026-05-09-lifting-logbook-s6") == "2026-05-09", \
        "arbitrary suffixes (these exist on the remote today) still parse"
    return "draft/<date> parses, with or without the documented suffix forms"


def test_branch_date_rejects_non_draft_and_non_dates() -> str:
    assert branch_date("main") is None
    assert branch_date("fix/866-day-rollover") is None
    assert branch_date("draft/not-a-date") is None
    assert branch_date("draft/2026-7-21") is None, "non-zero-padded is not the convention"
    assert branch_date("draft/") is None
    assert branch_date("draft/20260721") is None, "separators are required"
    return "main, feature branches, and malformed draft names yield None (no false rollover)"


def test_mismatched_stub_paths_flags_only_newer_dates() -> str:
    tree = [
        "sessions/dev-env/2026-07-21_040731.stub.md",        # same day — fine
        "sessions/dev-env/2026-07-22_123151.stub.md",        # NEWER — the rollover failure
        "sessions/career-playbook/2026-07-22_021500.stub.md",  # NEWER
        "sessions/lifting-logbook/2026-06-29_002505.stub.md",  # OLDER — lineage artifact
        "sessions/dev-env/2026-07-21_040731.manifest.jsonl",   # not a stub
        "sessions/dev-env/open-prs/770.json",                  # not a stub
        "sessions/meta/2026-07-20-some-composed-entry.md",     # composed doc, not a stub
    ]
    got = mismatched_stub_paths(tree, "2026-07-21")
    assert got == [
        "sessions/career-playbook/2026-07-22_021500.stub.md",
        "sessions/dev-env/2026-07-22_123151.stub.md",
    ], f"only newer-dated stubs, sorted; got {got}"
    return "only stubs dated after the branch are flagged — older ones are lineage, not rollover"


def test_mismatched_stub_paths_ignores_non_convention_names() -> str:
    tree = [
        "sessions/dev-env/2026-07-22-no-underscore.stub.md",  # no HHMMSS separator
        "sessions/dev-env/20260722_010101.stub.md",           # no date separators
        "sessions/dev-env/short.stub.md",                     # too short to carry a date
        "sessions/dev-env/2026-07-22_010101.stub.md",         # the one real match
    ]
    got = mismatched_stub_paths(tree, "2026-07-21")
    assert got == ["sessions/dev-env/2026-07-22_010101.stub.md"], \
        f"names compose's own glob would not match are not mismatches this check can speak to; got {got}"
    return "non-convention filenames are ignored — they are invisible to compose's glob anyway"


def test_mismatched_stub_paths_empty_when_branch_is_current() -> str:
    tree = [
        "sessions/dev-env/2026-07-22_123151.stub.md",
        "sessions/dev-env/2026-07-21_040731.stub.md",
    ]
    assert mismatched_stub_paths(tree, "2026-07-22") == [], \
        "on its own day's branch, an older stub is not a rollover finding"
    assert mismatched_stub_paths([], "2026-07-22") == [], "empty tree -> no findings"
    return "a branch on its own date reports nothing, and an empty tree does not crash"


def main() -> int:
    tests = [
        ("branch_date parses plain and suffixed draft branches", test_branch_date_parses_plain_and_suffixed),
        ("branch_date rejects non-draft and malformed names", test_branch_date_rejects_non_draft_and_non_dates),
        ("mismatched stubs are only the newer-dated ones", test_mismatched_stub_paths_flags_only_newer_dates),
        ("non-convention stub filenames ignored", test_mismatched_stub_paths_ignores_non_convention_names),
        ("no findings on a branch matching its own date", test_mismatched_stub_paths_empty_when_branch_is_current),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL: {name}")
            print(f"      {exc}")
    print(f"\nTests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
