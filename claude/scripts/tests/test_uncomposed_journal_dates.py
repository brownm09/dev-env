#!/usr/bin/env python3
"""Unit tests for uncomposed-journal-dates.py's select_dates() (dev-env#1413).
Required when uncomposed-journal-dates.py, or the nightly compose's date selection, changes.
Run: py -3 claude/scripts/tests/test_uncomposed_journal_dates.py

Pins the catch-up contract: every past draft inside the lookback that still has a
date-matching stub is listed oldest first, so a nightly run missed while the host was off is
composed by the next run that fires. Pure function, no git.

Exit 0 = all pass.
"""
import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "uncomposed-journal-dates.py"
sys.path.insert(0, str(SCRIPT.parent))  # for the script's _winsubp import
spec = importlib.util.spec_from_file_location("ujd", SCRIPT)
ujd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ujd)

TODAY = date(2026, 11, 23)


def stub(project: str, d: str) -> str:
    return f"sessions/{project}/{d}_101500.stub.md"


CASES = [
    ("yesterday only",
     {"2026-11-22": [stub("dev-env", "2026-11-22")]},
     ["2026-11-22"]),
    ("missed days before yesterday are listed oldest first",
     {"2026-11-19": [stub("dev-env", "2026-11-19")],
      "2026-11-17": [stub("career-playbook", "2026-11-17")],
      "2026-11-22": [stub("meta", "2026-11-22")]},
     ["2026-11-17", "2026-11-19", "2026-11-22"]),
    ("today is excluded (today-guard)",
     {"2026-11-23": [stub("dev-env", "2026-11-23")]},
     []),
    ("a future-dated draft is excluded",
     {"2026-11-24": [stub("dev-env", "2026-11-24")]},
     []),
    ("the lookback's first day is included",
     {"2026-11-09": [stub("dev-env", "2026-11-09")]},
     ["2026-11-09"]),
    ("a day before the lookback is excluded",
     {"2026-11-08": [stub("dev-env", "2026-11-08")]},
     []),
    ("a composed day (stubs deleted) is excluded",
     {"2026-11-20": ["sessions/dev-env/2026-11-20-routine-isolation.md",
                     "sessions/dev-env/open-prs/1400.json"]},
     []),
    ("a carried-forward stub from another date does not count",
     {"2026-11-18": [stub("meta", "2026-06-29")]},
     []),
    ("a manifest alone does not count",
     {"2026-11-18": ["sessions/dev-env/2026-11-18_101500.manifest.jsonl"]},
     []),
    ("a stub under a subdirectory does not count",
     {"2026-11-18": ["sessions/dev-env/reports/2026-11-18_101500.stub.md"]},
     []),
    ("a malformed date key is ignored",
     {"2026-13-40": [stub("dev-env", "2026-13-40")]},
     []),
]

# (name, drafts, composed journal paths on origin/main, want): a draft branch that outlived
# its compose (the compose/<D> conflict route) must not be relisted every night.
MAIN_CASES = [
    ("a leftover draft whose only project is composed is excluded",
     {"2026-11-18": [stub("dev-env", "2026-11-18")]},
     ["sessions/dev-env/2026-11-18-routine-runs.md"],
     []),
    ("a partly composed day is listed for the project still uncomposed",
     {"2026-11-18": [stub("dev-env", "2026-11-18"), stub("career-playbook", "2026-11-18")]},
     ["sessions/dev-env/2026-11-18-routine-runs.md"],
     ["2026-11-18"]),
    ("a report on main does not count as a composed journal",
     {"2026-11-18": [stub("dev-env", "2026-11-18")]},
     ["sessions/dev-env/reports/2026-11-18-audit.md"],
     ["2026-11-18"]),
    ("a stub left on main does not count as a composed journal",
     {"2026-11-18": [stub("dev-env", "2026-11-18")]},
     [stub("dev-env", "2026-11-18")],
     ["2026-11-18"]),
    ("another project's composed journal does not exclude this one",
     {"2026-11-18": [stub("dev-env", "2026-11-18")]},
     ["sessions/meta/2026-11-18-meta-triggers.md"],
     ["2026-11-18"]),
]


def main() -> int:
    failed = 0
    runs = [(n, d, [], w) for n, d, w in CASES] + MAIN_CASES
    for name, drafts, main_paths, want in runs:
        got = ujd.select_dates(drafts, ujd.composed_pairs(main_paths), TODAY)
        if got != want:
            failed += 1
            print(f"FAIL {name}: got {got}, want {want}")
        else:
            print(f"ok   {name}")
    print(f"Tests: {len(runs) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
