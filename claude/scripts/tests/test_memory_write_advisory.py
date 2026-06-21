#!/usr/bin/env python3
"""Unit tests for memory-write-advisory.py's should_advise_memory_write predicate.

memory-write-advisory.py is a PostToolUse(Write) hook that nudges Claude to pair a
durable memory write with an immortalization issue (ADR-048, extends ADR-038). The
"should this nudge fire?" decision is extracted into the pure
should_advise_memory_write() predicate so it can be exercised offline (no Claude
session, no stdin plumbing), matching the repo's fixture-only test convention.

Usage:
    py -3 claude/scripts/tests/test_memory_write_advisory.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "memory-write-advisory.py"
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("memory_write_advisory", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mwa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mwa)  # safe: main() is guarded by __main__
should = mwa.should_advise_memory_write

# A representative durable-memory path (forward and back slashes both exercised).
MEM = "C:/Users/brown/.claude/projects/C--Users-brown-Git-dev-env/memory/feedback_new_rule.md"


def test_durable_write_no_link_advises() -> str:
    assert should("Write", MEM, "User wants X. Why: ... How to apply: ...")
    return "durable memory, no link -> advise"


def test_backslash_path_advises() -> str:
    # Windows-style path separators must normalize so the memory dir is detected.
    win = r"C:\Users\brown\.claude\projects\proj\memory\feedback_x.md"
    assert should("Write", win, "durable rule with no link")
    return "backslash memory path, no link -> advise"


def test_write_with_issue_ref_silent() -> str:
    assert not should("Write", MEM, "User wants X. Tracked in #373.")
    return "memory body cites #373 -> silent"


def test_write_with_adr_ref_silent() -> str:
    assert not should("Write", MEM, "See ADR-048 for the rule.")
    return "memory body cites ADR-048 -> silent"


def test_write_with_claudemd_ref_silent() -> str:
    assert not should("Write", MEM, "Documented in repo: claude/CLAUDE.md.")
    return "memory body cites CLAUDE.md / 'Documented in repo' -> silent"


def test_memory_index_silent() -> str:
    idx = "C:/Users/brown/.claude/projects/x/memory/MEMORY.md"
    assert not should("Write", idx, "- [Foo](foo.md) - bar")
    return "MEMORY.md index -> silent"


def test_non_memory_path_silent() -> str:
    assert not should("Write", "C:/Users/brown/Git/dev-env/claude/CLAUDE.md", "anything")
    return "write outside a memory dir -> silent"


def test_non_md_in_memory_silent() -> str:
    assert not should("Write", "C:/Users/brown/.claude/projects/x/memory/notes.txt", "x")
    return "non-.md file in memory dir -> silent"


def test_edit_tool_silent() -> str:
    assert not should("Edit", MEM, "durable rule, no link")
    return "Edit tool (not Write) -> silent"


def test_empty_inputs_silent() -> str:
    assert not should("Write", "", "")
    assert not should("", MEM, "durable rule, no link")
    return "empty path / empty tool_name -> silent (no crash)"


def main() -> int:
    tests = [
        ("durable write, no link advises", test_durable_write_no_link_advises),
        ("backslash memory path advises", test_backslash_path_advises),
        ("write with issue ref silent", test_write_with_issue_ref_silent),
        ("write with ADR ref silent", test_write_with_adr_ref_silent),
        ("write with CLAUDE.md ref silent", test_write_with_claudemd_ref_silent),
        ("MEMORY.md index silent", test_memory_index_silent),
        ("non-memory path silent", test_non_memory_path_silent),
        ("non-.md in memory silent", test_non_md_in_memory_silent),
        ("Edit tool silent", test_edit_tool_silent),
        ("empty inputs silent", test_empty_inputs_silent),
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
