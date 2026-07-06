#!/usr/bin/env python3
"""Unit tests for check-journal-compose-liveness.py's pure
has_uncommitted_target_date_changes() helper.

See ADR-085 for why the check reads `git status --porcelain` for the target
date rather than a transcript-mtime liveness signal (ADR-051's
worktree_session_is_live() doesn't transfer cleanly here — a session that
might write to a given date's draft branch could be running from any
project's worktree, not one fixed path).

Exercises the pure string-parsing helper offline (no subprocess, no git, no
filesystem) — porcelain output is passed directly as a string, matching the
"caller runs git, script stays pure I/O" convention used by _hookio.py and
_journal_shards.py elsewhere in this repo.

Usage:
    py -3 claude/scripts/tests/test_check_journal_compose_liveness.py

Exit 0 = all pass.
"""
import sys
import importlib.util
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "claude" / "scripts" / "check-journal-compose-liveness.py"

spec = importlib.util.spec_from_file_location("check_journal_compose_liveness", SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

has_uncommitted_target_date_changes = mod.has_uncommitted_target_date_changes
format_abort_message = mod.format_abort_message
main_entrypoint = mod.main


def test_empty_output_is_clean() -> str:
    if has_uncommitted_target_date_changes("", "2026-07-05") is not False:
        raise AssertionError("empty porcelain output must be clean")
    return "empty porcelain output -> clean (no session active)"


def test_matching_untracked_stub_is_dirty() -> str:
    porcelain = "?? sessions/lifting-logbook/2026-07-05_143022.stub.md\n"
    if has_uncommitted_target_date_changes(porcelain, "2026-07-05") is not True:
        raise AssertionError("untracked stub for target date must be dirty")
    return "untracked (??) stub file for the target date -> dirty"


def test_matching_modified_manifest_shard_is_dirty() -> str:
    porcelain = " M sessions/dev-env/2026-07-05_143022.manifest.jsonl\n"
    if has_uncommitted_target_date_changes(porcelain, "2026-07-05") is not True:
        raise AssertionError("modified manifest shard for target date must be dirty")
    return "modified ( M) manifest shard for the target date -> dirty"


def test_unrelated_date_is_clean() -> str:
    porcelain = "?? sessions/lifting-logbook/2026-07-04_090000.stub.md\n"
    if has_uncommitted_target_date_changes(porcelain, "2026-07-05") is not False:
        raise AssertionError("a different date's stub must not count as dirty")
    return "stub file for a different date -> clean"


def test_open_pr_shard_is_clean() -> str:
    porcelain = "?? sessions/lifting-logbook/open-prs/54.json\n"
    if has_uncommitted_target_date_changes(porcelain, "2026-07-05") is not False:
        raise AssertionError("open-PR shards are not date-named and must not match")
    return "open-PR shard (not date-named) -> clean"


def test_renamed_file_checks_destination_path() -> str:
    porcelain = (
        "R  sessions/dev-env/2026-07-04_235959.stub.md -> "
        "sessions/dev-env/2026-07-05_000010.stub.md\n"
    )
    if has_uncommitted_target_date_changes(porcelain, "2026-07-05") is not True:
        raise AssertionError("a rename landing on the target date must be dirty")
    return "renamed (R ) path checks the destination side -> dirty"


def test_multiple_lines_mixed() -> str:
    porcelain = (
        "?? sessions/dev-env/open-prs/54.json\n"
        " M sessions/lifting-logbook/2026-07-04_090000.stub.md\n"
        "?? sessions/career-playbook/2026-07-05_020000.stub.md\n"
    )
    if has_uncommitted_target_date_changes(porcelain, "2026-07-05") is not True:
        raise AssertionError("one matching line among several must still be dirty")
    return "mixed porcelain output, one matching line among several -> dirty"


def test_blank_lines_ignored() -> str:
    porcelain = "\n\n?? sessions/dev-env/2026-07-04_090000.stub.md\n\n"
    if has_uncommitted_target_date_changes(porcelain, "2026-07-05") is not False:
        raise AssertionError("blank lines must not raise or be mistaken for a match")
    return "blank lines interspersed -> ignored, no false positive"


def test_wrong_extension_at_matching_date_is_clean() -> str:
    """Tightened matching (review finding, PR #587): the date marker alone is
    not enough — the path must also end in .stub.md or .manifest.jsonl. Guards
    against a hypothetical future path merely containing "/YYYY-MM-DD_"."""
    porcelain = "?? sessions/dev-env/2026-07-05_notes/some-file.txt\n"
    if has_uncommitted_target_date_changes(porcelain, "2026-07-05") is not False:
        raise AssertionError("a date-marker match without the shard suffix must be clean")
    return "date marker present but wrong extension -> clean (suffix-tightened match)"


def test_main_rejects_malformed_date() -> str:
    """Review finding (PR #587): a malformed $DATE (e.g. from a `date -d
    yesterday` failure) must be a loud usage error, not a silent
    always-clean pass. Short-circuits before reading stdin, so no stdin
    mocking is needed."""
    rc = main_entrypoint(["check-journal-compose-liveness.py", "not-a-date"])
    if rc != 2:
        raise AssertionError(f"expected exit 2 for a malformed date, got {rc!r}")
    return "malformed date argument -> exit 2 (usage error), stdin never read"


def test_main_rejects_unsubstituted_placeholder() -> str:
    """Review finding (PR #587): journal-compose/SKILL.md's Step 0.6 passes the
    literal "YYYY-MM-DD" as a documentation placeholder that Claude is expected
    to substitute with the real resolved date at run time. If that
    substitution is ever skipped, the check must fail loudly rather than
    silently matching nothing and passing vacuously."""
    rc = main_entrypoint(["check-journal-compose-liveness.py", "YYYY-MM-DD"])
    if rc != 2:
        raise AssertionError(f"expected exit 2 for the unsubstituted placeholder, got {rc!r}")
    return "unsubstituted 'YYYY-MM-DD' placeholder -> exit 2, not a vacuous pass"


def test_abort_message_is_ascii_safe() -> str:
    """Matches the ASCII/cp1252-encodability pin used elsewhere in this repo's
    advisory-emitting scripts (e.g. test_posttooluse_inert_advisory.py,
    test_journal_shard_write_advisory.py) — this message can be printed from
    contexts (redirected stdout/stderr, tee'd log files) where a non-ASCII
    character risks an encoding surprise."""
    msg = format_abort_message("2026-07-05")
    if not msg.isascii():
        raise AssertionError(f"message contains non-ASCII characters: {msg!r}")
    try:
        msg.encode("cp1252")
    except UnicodeEncodeError as e:
        raise AssertionError(f"message is not cp1252-encodable: {e}")
    return "abort message is ASCII (and therefore cp1252-safe) -- no encoding surprises"


def main() -> int:
    tests = [
        ("empty output -> clean", test_empty_output_is_clean),
        ("untracked stub for target date -> dirty", test_matching_untracked_stub_is_dirty),
        ("modified manifest shard for target date -> dirty", test_matching_modified_manifest_shard_is_dirty),
        ("unrelated date -> clean", test_unrelated_date_is_clean),
        ("open-PR shard -> clean", test_open_pr_shard_is_clean),
        ("renamed file checks destination -> dirty", test_renamed_file_checks_destination_path),
        ("multiple lines, one match -> dirty", test_multiple_lines_mixed),
        ("blank lines ignored", test_blank_lines_ignored),
        ("wrong extension at matching date -> clean", test_wrong_extension_at_matching_date_is_clean),
        ("main() rejects malformed date -> exit 2", test_main_rejects_malformed_date),
        ("main() rejects unsubstituted placeholder -> exit 2", test_main_rejects_unsubstituted_placeholder),
        ("abort message is ASCII/cp1252-safe", test_abort_message_is_ascii_safe),
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
