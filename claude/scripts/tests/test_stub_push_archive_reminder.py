#!/usr/bin/env python3
"""Unit tests for stub-push-archive-reminder.py's push-error guard.

`stub-push-archive-reminder.py` arms the journal-archive reminder after a stub is
pushed to engineering-journal. It must NOT arm the reminder when the push failed.
Before #380 the guard read the legacy `output` field (always empty on the real
payload), so it was a no-op — a failed push could still arm the reminder. The
guard is now the pure `has_push_error()` predicate fed by the shared
`read_command_output` helper, exercised offline here.

`most_recent_commit_has_stub` (a git call) is intentionally not tested.

Usage:
    py -3 claude/scripts/tests/test_stub_push_archive_reminder.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "stub-push-archive-reminder.py"

# The script imports _winsubp and _hookio (siblings in scripts/); make resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("stub_push_archive_reminder", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
spar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spar)  # safe: main() is guarded by __main__
has_push_error = spar.has_push_error


def test_clean_push_no_error() -> str:
    out = (
        "To github.com:brownm09/engineering-journal.git\n"
        "   abc123..def456  draft/2026-06-21 -> draft/2026-06-21"
    )
    assert not has_push_error(out)
    return "successful push output -> no error (reminder allowed)"


def test_git_error_detected() -> str:
    assert has_push_error("error: failed to push some refs to 'github.com:...'")
    return "'error:' line -> push error detected"


def test_git_fatal_detected() -> str:
    assert has_push_error("fatal: Authentication failed for 'https://github.com/...'")
    return "'fatal:' line -> push error detected"


def test_case_insensitive() -> str:
    assert has_push_error("ERROR: remote rejected")
    assert has_push_error("FATAL: the remote end hung up unexpectedly")
    return "guard is case-insensitive"


def test_empty_output_no_error() -> str:
    # The pre-#380 read was always empty here, so the guard never fired.
    assert not has_push_error("")
    return "empty output -> no error"


def main() -> int:
    tests = [
        ("clean push has no error", test_clean_push_no_error),
        ("git error: detected", test_git_error_detected),
        ("git fatal: detected", test_git_fatal_detected),
        ("case-insensitive match", test_case_insensitive),
        ("empty output has no error", test_empty_output_no_error),
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
