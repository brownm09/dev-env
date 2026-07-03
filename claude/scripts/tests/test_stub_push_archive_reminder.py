#!/usr/bin/env python3
"""Unit tests for stub-push-archive-reminder.py's push-error guard.

`stub-push-archive-reminder.py` arms the journal-archive reminder after a stub is
pushed to engineering-journal. It must NOT arm the reminder when the push failed.
Before #380 the guard read the legacy `output` field (always empty on the real
payload), so it was a no-op — a failed push could still arm the reminder. The
guard is now the pure `has_push_error()` predicate fed by the shared
`read_command_output` helper, exercised offline here.

dev-env#532 (ADR-050 Amendment 10) converged the "is this a git push?" check
itself from a raw `"git push" not in command` substring test onto a new pure
`is_git_push_command()` predicate, built on the same `scan_top_level` engine
already used by usage-snapshot.py / pr-merge-reminder.py /
post-pr-merge-project.py / post-merge-tile-checkpoint.py /
post-pr-merge-pull.py / post-pr-merge-reclaim.py (dev-env#529, ADR-050
Amendment 9). The three heredoc/quote/subshell tests below pin the
false-positive shapes that substring test was blind to (dev-env#499's
original repro class) but the anchored predicate correctly rejects.

dev-env#539 (ADR-050 Amendment 12) converges the companion "does this push
target engineering-journal?" check the same way: the raw
`"engineering-journal" not in command and "engineering_journal" not in
command` substring test is replaced by a new pure
`references_engineering_journal()` predicate, also built on `scan_top_level`
but anchored to a `cd <path>` / `git -C <path>` directory argument rather than
a CLI-invocation verb. The `test_ej_ref_*` tests below pin the same
dev-env#499 false-positive shapes applied to a repo-name literal instead of a
command literal.

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
is_git_push_command = spar.is_git_push_command
references_engineering_journal = spar.references_engineering_journal


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


# ---------------------------------------------------------------------------
# is_git_push_command — command-shape anchoring (dev-env#532, ADR-050 Amendment 10)
#
# Each command below contains the literal substring "git push" but not as a
# genuine top-level invocation. The old crude `"git push" not in command`
# substring test would have matched all three; the scan_top_level-anchored
# predicate correctly rejects them.
# ---------------------------------------------------------------------------

def test_bare_push_matched() -> str:
    assert is_git_push_command("git push -u origin draft/2026-06-21")
    return "bare git push -> matched (sanity baseline)"


def test_push_with_cd_prefix_matched() -> str:
    assert is_git_push_command("cd /Git/engineering-journal && git push")
    return "cd <dir> && git push -> matched (_PUSH_RE's optional cd-prefix branch)"


def test_push_text_in_heredoc_body_not_matched() -> str:
    command = "git commit -F - <<'EOF'\ngit push origin main\nEOF"
    assert not is_git_push_command(command)
    return "'git push' text inside a heredoc body -> no match (dev-env#532)"


def test_push_text_inside_double_quotes_not_matched() -> str:
    # The && inside the quoted commit message would, without quote-tracking,
    # wrongly carve out a second top-level segment starting with "git push"
    # -- the dev-env#499 false-positive class scan_top_level exists to
    # prevent.
    command = 'git commit -m "document the git push workflow && git push origin main"'
    assert not is_git_push_command(command)
    return "'git push' text inside a double-quoted commit message -> no match (dev-env#532)"


def test_push_text_inside_subshell_not_matched() -> str:
    command = "echo $(git log --oneline -1 && git push origin main)"
    assert not is_git_push_command(command)
    return "'git push' text inside a $() subshell -> no match (dev-env#532)"


# ---------------------------------------------------------------------------
# references_engineering_journal — command-shape anchoring (dev-env#539, ADR-050 Amendment 12)
#
# Each false-positive command below contains the literal substring
# "engineering-journal" (or the underscored spelling) but not as a genuine
# top-level `cd`/`git -C` directory reference. The old crude
# `"engineering-journal" not in command and "engineering_journal" not in
# command` substring test would have matched all three; the
# scan_top_level-anchored predicate correctly rejects them.
# ---------------------------------------------------------------------------

def test_ej_ref_with_cd_prefix_matched() -> str:
    assert references_engineering_journal("cd ~/Git/engineering-journal && git push")
    return "cd <engineering-journal dir> && git push -> matched (sanity baseline)"


def test_ej_ref_with_dash_c_matched() -> str:
    command = "git -C C:/Users/brown/Git/engineering-journal push -u origin draft/2026-07-02"
    assert references_engineering_journal(command)
    return "git -C <engineering-journal dir> push -> matched"


def test_ej_ref_underscore_spelling_matched() -> str:
    assert references_engineering_journal("cd /Git/engineering_journal && git push")
    return "underscore spelling 'engineering_journal' -> also matched (both spellings supported, mirrors old check)"


def test_ej_ref_unrelated_repo_not_matched() -> str:
    assert not references_engineering_journal("cd /Git/career-playbook && git push")
    return "cd <unrelated dir> && git push -> no match (sanity baseline)"


def test_ej_ref_text_in_heredoc_body_not_matched() -> str:
    command = "git commit -F - <<'EOF'\nsync notes with engineering-journal\nEOF"
    assert not references_engineering_journal(command)
    return "'engineering-journal' text inside a heredoc body -> no match (dev-env#539)"


def test_ej_ref_text_inside_double_quotes_not_matched() -> str:
    # The && inside the quoted commit message would, without quote-tracking,
    # wrongly carve out a second top-level segment -- the dev-env#499
    # false-positive class scan_top_level exists to prevent. Both spellings
    # are embedded to cover the check's dual-spelling support in one case.
    command = 'git commit -m "sync notes with engineering-journal && update engineering_journal path"'
    assert not references_engineering_journal(command)
    return "'engineering-journal' text inside a double-quoted commit message -> no match (dev-env#539)"


def test_ej_ref_text_inside_subshell_not_matched() -> str:
    command = "echo $(cat notes-about-engineering-journal.txt && echo engineering_journal)"
    assert not references_engineering_journal(command)
    return "'engineering-journal' text inside a $() subshell -> no match (dev-env#539)"


def main() -> int:
    tests = [
        ("clean push has no error", test_clean_push_no_error),
        ("git error: detected", test_git_error_detected),
        ("git fatal: detected", test_git_fatal_detected),
        ("case-insensitive match", test_case_insensitive),
        ("empty output has no error", test_empty_output_no_error),
        ("bare git push matched", test_bare_push_matched),
        ("cd-prefixed git push matched", test_push_with_cd_prefix_matched),
        ("'git push' text in heredoc body ignored (dev-env#532)", test_push_text_in_heredoc_body_not_matched),
        ("'git push' text in double quotes ignored (dev-env#532)", test_push_text_inside_double_quotes_not_matched),
        ("'git push' text in $() subshell ignored (dev-env#532)", test_push_text_inside_subshell_not_matched),
        ("cd-prefixed engineering-journal reference matched", test_ej_ref_with_cd_prefix_matched),
        ("git -C engineering-journal reference matched", test_ej_ref_with_dash_c_matched),
        ("underscore-spelled engineering_journal reference matched", test_ej_ref_underscore_spelling_matched),
        ("unrelated repo reference not matched", test_ej_ref_unrelated_repo_not_matched),
        ("'engineering-journal' text in heredoc body ignored (dev-env#539)", test_ej_ref_text_in_heredoc_body_not_matched),
        ("'engineering-journal' text in double quotes ignored (dev-env#539)", test_ej_ref_text_inside_double_quotes_not_matched),
        ("'engineering-journal' text in $() subshell ignored (dev-env#539)", test_ej_ref_text_inside_subshell_not_matched),
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
