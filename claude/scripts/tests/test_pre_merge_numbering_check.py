#!/usr/bin/env python3
"""Tests for pre-merge-numbering-check.py pure helpers.

Exercises extract_section, extract_testing_numbers, extract_adr_numbers,
is_dev_env_repo, find_new_collisions, find_gaps, and format_block_message
offline (no disk, no network, no subprocess, no git). main()'s git/subprocess
calls are not covered (pure-helper convention; matches every other
test_*.py in this directory). See dev-env issue #516.

Cases pinned:
- extract_section: heading found mid-file, heading absent, heading with no
  following ## (runs to end of file), section immediately followed by
  another heading (empty body).
- extract_testing_numbers: a realistic multi-item CLAUDE.md-shaped Testing
  section (with an indented code block inside one item, which must NOT be
  mistaken for a sibling item); a numbered list outside the Testing section
  is out of scope and ignored.
- extract_adr_numbers: realistic INDEX.md-shaped table rows; the header row
  and the `|---|` separator row are not table entries and must be skipped.
- is_dev_env_repo: https and ssh origin URLs for dev-env; a different repo
  (lifting-logbook); empty/None input.
- find_new_collisions: a genuinely new number that collides with main; an
  edited *existing* item (present at the merge-base) is never flagged even
  when its text differs between branch and main; no items in common -> no
  collisions; all-empty inputs.
- find_gaps: contiguous set -> no gaps; one gap; empty input; single element.
- format_block_message: the rendered message names the file label, the
  colliding number, and both colliding lines.
"""
import importlib.util
import os
import sys

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-merge-numbering-check.py")
# The script imports _winsubp and _hookio (siblings in scripts/); make them resolvable.
sys.path.insert(0, os.path.dirname(_SCRIPT))
spec = importlib.util.spec_from_file_location("pre_merge_numbering_check", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

extract_section = mod.extract_section
extract_testing_numbers = mod.extract_testing_numbers
extract_adr_numbers = mod.extract_adr_numbers
is_dev_env_repo = mod.is_dev_env_repo
find_new_collisions = mod.find_new_collisions
find_gaps = mod.find_gaps
format_block_message = mod.format_block_message


# ---------------------------------------------------------------------------
# extract_section
# ---------------------------------------------------------------------------

def test_extract_section_mid_file():
    text = "# Title\n\n## Before\nignored\n\n## Testing\nline one\nline two\n\n## Observability\nignored\n"
    assert extract_section(text, "Testing") == "line one\nline two\n"

def test_extract_section_heading_absent():
    text = "# Title\n\n## Before\nignored\n"
    assert extract_section(text, "Testing") == ""

def test_extract_section_runs_to_end_of_file():
    text = "## Testing\nline one\nline two"
    assert extract_section(text, "Testing") == "line one\nline two"

def test_extract_section_empty_body():
    text = "## Testing\n## Observability\nignored\n"
    assert extract_section(text, "Testing") == ""


# ---------------------------------------------------------------------------
# extract_testing_numbers
# ---------------------------------------------------------------------------

_CLAUDE_MD_SAMPLE = """# dev-env

## Testing

1. **Hook-script syntax check** — run from the repo root:

   ```bash
   py -3 -c "pass"
   ```

   Some indented prose that continues item 1 -- must not look like a new item.

2. **Second check** — required when changing foo.

## Observability

Not part of Testing.

1. This numbered line is outside the Testing section and must be ignored.
"""

def test_extract_testing_numbers_basic():
    items = extract_testing_numbers(_CLAUDE_MD_SAMPLE)
    assert set(items) == {1, 2}
    assert items[1].startswith("1. **Hook-script syntax check**")
    assert items[2].startswith("2. **Second check**")

def test_extract_testing_numbers_ignores_indented_and_out_of_section_lines():
    # The indented code-block/prose inside item 1, and the numbered line
    # after "## Observability", must not add phantom entries.
    items = extract_testing_numbers(_CLAUDE_MD_SAMPLE)
    assert len(items) == 2

def test_extract_testing_numbers_no_section():
    assert extract_testing_numbers("# Title\nno testing section here\n") == {}


# ---------------------------------------------------------------------------
# extract_adr_numbers
# ---------------------------------------------------------------------------

_INDEX_MD_SAMPLE = """# Architectural Decision Records

| # | Title | Date | Status | Tags |
|---|-------|------|--------|------|
| [001](001-per-session-stub-files.md) | Per-Session Stub Files | 2026-03-27 | Accepted | journal |
| [002](002-journal-compose-session-isolation.md) | Journal-Compose Isolation | 2026-04-04 | Accepted | journal |
"""

def test_extract_adr_numbers_basic():
    items = extract_adr_numbers(_INDEX_MD_SAMPLE)
    assert set(items) == {1, 2}
    assert "Per-Session Stub Files" in items[1]

def test_extract_adr_numbers_skips_header_and_separator():
    # Only the two real entries -- the header row and the |---| separator
    # row have no leading "| [", so neither is mistaken for an ADR entry.
    items = extract_adr_numbers(_INDEX_MD_SAMPLE)
    assert len(items) == 2


# ---------------------------------------------------------------------------
# is_dev_env_repo
# ---------------------------------------------------------------------------

def test_is_dev_env_repo_https():
    assert is_dev_env_repo("https://github.com/brownm09/dev-env.git") is True

def test_is_dev_env_repo_https_no_git_suffix():
    assert is_dev_env_repo("https://github.com/brownm09/dev-env") is True

def test_is_dev_env_repo_ssh():
    assert is_dev_env_repo("git@github.com:brownm09/dev-env.git") is True

def test_is_dev_env_repo_different_repo():
    assert is_dev_env_repo("https://github.com/brownm09/lifting-logbook.git") is False

def test_is_dev_env_repo_empty():
    assert is_dev_env_repo("") is False

def test_is_dev_env_repo_none():
    assert is_dev_env_repo(None) is False


# ---------------------------------------------------------------------------
# find_new_collisions
# ---------------------------------------------------------------------------

def test_find_new_collisions_genuine_collision():
    base = {1: "1. **A**", 2: "2. **B**"}
    branch = {1: "1. **A**", 2: "2. **B**", 3: "3. **Branch's new item**"}
    main = {1: "1. **A**", 2: "2. **B**", 3: "3. **Someone else's new item**", 4: "4. **Another**"}
    result = find_new_collisions(base, branch, main)
    assert result == {3: ("3. **Branch's new item**", "3. **Someone else's new item**")}

def test_find_new_collisions_no_collision():
    base = {1: "1. **A**"}
    branch = {1: "1. **A**", 2: "2. **Branch's item**"}
    main = {1: "1. **A**", 3: "3. **Someone else's item**"}
    assert find_new_collisions(base, branch, main) == {}

def test_find_new_collisions_edited_existing_item_not_flagged():
    # Item 1 existed at the merge-base; the branch reworded it. Even though
    # main's text for #1 also differs, this is an edit, not a fresh claim --
    # never a collision.
    base = {1: "1. **A**"}
    branch = {1: "1. **A, reworded by this branch**"}
    main = {1: "1. **A, reworded differently by another PR**"}
    assert find_new_collisions(base, branch, main) == {}

def test_find_new_collisions_empty_inputs():
    assert find_new_collisions({}, {}, {}) == {}


# ---------------------------------------------------------------------------
# find_gaps
# ---------------------------------------------------------------------------

def test_find_gaps_contiguous():
    assert find_gaps({1, 2, 3}) == []

def test_find_gaps_one_gap():
    assert find_gaps({1, 2, 4}) == [3]

def test_find_gaps_empty():
    assert find_gaps(set()) == []

def test_find_gaps_single_element():
    assert find_gaps({1}) == []


# ---------------------------------------------------------------------------
# format_block_message
# ---------------------------------------------------------------------------

def test_format_block_message_names_file_and_number():
    findings = {
        "CLAUDE.md Testing section": {
            34: ("34. **Branch's item**", "34. **Main's item**"),
        }
    }
    msg = format_block_message(findings)
    assert "CLAUDE.md Testing section" in msg
    assert "#34" in msg
    assert "Branch's item" in msg
    assert "Main's item" in msg
    assert "BLOCKED" in msg


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
