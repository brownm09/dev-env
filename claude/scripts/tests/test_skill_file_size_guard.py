#!/usr/bin/env python3
"""Tests for pre-tool-use-skill-file-size-guard.py (dev-env#939).

Two layers: pure-function unit tests against the imported module (basename
matching, byte-size computation, config loading -- all meaningful enough to
test in isolation, unlike a hook with nothing but cheap field checks), plus
subprocess end-to-end tests via _run_hook (mirroring
test_pre_tool_use_nested_agent_background_guard.py's pattern) that drive the
real hook over stdin and assert exit codes / stderr content.

Also covers three /review-found regressions fixed in the same PR: a CRLF
line-ending mismatch that silently disabled the guard on any multi-line Edit
to a CRLF file, a non-dict `.claude/hook-config.json` root that raised past
the guard's own fail-open handler, and a block predicate that blocked
legitimate shrinking edits on an already-oversized file.

Usage:
    py -3 claude/scripts/tests/test_skill_file_size_guard.py

Exit 0 = all pass.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "pre-tool-use-skill-file-size-guard.py"

# The module's first real imports are `_hookout`/`_hookutil`/`_skill_file_size`;
# ensure scripts/ is importable.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("skill_file_size_guard", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()

DEFAULT_LIMIT = mod.DEFAULT_LIMIT_BYTES  # 262144


def _run_hook(payload):
    stdin_text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=90,  # generous headroom under CI resource contention (dev-env#994)
    )


# ---------------------------------------------------------------------------
# Layer 1: pure functions
# ---------------------------------------------------------------------------

def test_is_skill_md_matches_lowercase_basename():
    assert mod._is_skill_md("/some/dir/SKILL.md") is True


def test_is_skill_md_matches_case_variants():
    assert mod._is_skill_md("/some/dir/skill.md") is True
    assert mod._is_skill_md("/some/dir/Skill.MD") is True
    assert mod._is_skill_md("C:/Users/brown/.claude/skills/foo/SKILL.md") is True


def test_is_skill_md_rejects_non_skill_files():
    assert mod._is_skill_md("/some/dir/SKILL.md.bak") is False
    assert mod._is_skill_md("/some/dir/NOTSKILL.md") is False
    assert mod._is_skill_md("/some/dir/REFERENCE.md") is False
    assert mod._is_skill_md("") is False


def test_resulting_write_size_counts_utf8_bytes():
    # "café" -- the é is 2 bytes in UTF-8, so byte length != char length.
    tool_input = {"content": "café"}
    assert mod.resulting_write_size(tool_input) == len("café".encode("utf-8"))
    assert mod.resulting_write_size(tool_input) != len("café")


def test_resulting_write_size_missing_content_defaults_zero():
    assert mod.resulting_write_size({}) == 0
    assert mod.resulting_write_size({"content": None}) == 0


def test_resulting_edit_size_replace_all_false_single_occurrence():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("alpha TARGET beta gamma")  # TARGET appears exactly once
        tool_input = {"old_string": "TARGET", "new_string": "REPLACED"}
        size = mod.resulting_edit_size(path, tool_input)
        expected = len("alpha REPLACED beta gamma".encode("utf-8"))
        assert size == expected


def test_resulting_edit_size_replace_all_true_multiple_occurrences():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("alpha TARGET beta TARGET gamma")
        tool_input = {"old_string": "TARGET", "new_string": "REPLACED", "replace_all": True}
        size = mod.resulting_edit_size(path, tool_input)
        expected = len("alpha REPLACED beta REPLACED gamma".encode("utf-8"))
        assert size == expected


def test_resulting_edit_size_non_unique_without_replace_all_returns_none():
    # The real Edit tool refuses to run when old_string isn't unique and
    # replace_all isn't set ("string not found" / "not unique"). Estimating
    # a first-occurrence-only size here would produce a misleading BLOCKED
    # message for an edit that could never have applied.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("alpha TARGET beta TARGET gamma")
        tool_input = {"old_string": "TARGET", "new_string": "REPLACED"}
        assert mod.resulting_edit_size(path, tool_input) is None


def test_resulting_edit_size_shrinks_below_limit():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * (DEFAULT_LIMIT + 1000) + "TARGET")
        tool_input = {"old_string": "x" * (DEFAULT_LIMIT + 1000), "new_string": "y"}
        size = mod.resulting_edit_size(path, tool_input)
        assert size < DEFAULT_LIMIT


def test_resulting_edit_size_old_string_not_found_returns_none():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("alpha beta gamma")
        tool_input = {"old_string": "NOT_PRESENT", "new_string": "x"}
        assert mod.resulting_edit_size(path, tool_input) is None


def test_resulting_edit_size_empty_old_string_returns_none():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("alpha beta gamma")
        tool_input = {"old_string": "", "new_string": "x"}
        assert mod.resulting_edit_size(path, tool_input) is None


def test_resulting_edit_size_missing_file_returns_none():
    tool_input = {"old_string": "a", "new_string": "b"}
    assert mod.resulting_edit_size("/no/such/file/SKILL.md", tool_input) is None


def test_resulting_edit_size_preserves_crlf_bytes():
    # Without newline="" on read, universal-newline translation would
    # silently collapse \r\n -> \n, undercounting the true on-disk byte
    # count by 1 byte per untouched CRLF line.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        raw = b"line one\r\nTARGET\r\nline three\r\n"
        with open(path, "wb") as f:
            f.write(raw)
        tool_input = {"old_string": "TARGET", "new_string": "REPLACED_LONGER"}
        size = mod.resulting_edit_size(path, tool_input)
        expected = len(raw.replace(b"TARGET", b"REPLACED_LONGER"))
        assert size == expected


def test_resulting_edit_size_multiline_old_string_matches_crlf_file():
    # Regression (review-found, verified against the real Edit tool): a
    # model-authored old_string uses plain \n line breaks even when the
    # target file is CRLF on disk. Before the fix, a literal-byte comparison
    # never found a multi-line old_string in a CRLF file, so the guard
    # returned None (fail open) and never enforced the limit at all on any
    # multi-line CRLF edit -- regardless of how large new_string was.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "wb") as f:
            f.write(b"line one\r\nTARGET_LINE\r\nline three\r\n")
        tool_input = {
            "old_string": "TARGET_LINE\nline three",
            "new_string": "x" * 500000,
        }
        size = mod.resulting_edit_size(path, tool_input)
        assert size is not None
        assert size > DEFAULT_LIMIT


def test_resulting_edit_size_reapplies_crlf_to_new_string_newlines():
    # The real Edit tool converts \n in new_string back to \r\n when writing
    # to a CRLF file -- measuring new_string's raw \n bytes would undercount
    # the true resulting size by one byte per inserted line.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "wb") as f:
            f.write(b"line one\r\nTARGET_LINE\r\nline three\r\n")
        tool_input = {
            "old_string": "TARGET_LINE\nline three",
            "new_string": "REPLACED_LINE\nnew line three",
        }
        size = mod.resulting_edit_size(path, tool_input)
        expected = len(b"line one\r\nREPLACED_LINE\r\nnew line three\r\n")
        assert size == expected


def test_current_file_size_missing_file_returns_zero():
    assert mod.current_file_size("/no/such/file/SKILL.md") == 0


def test_current_file_size_existing_file_returns_actual_size():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * 500)
        assert mod.current_file_size(path) == 500


def test_load_limit_bytes_default_when_missing():
    with tempfile.TemporaryDirectory() as d:
        assert mod.load_limit_bytes(d) == DEFAULT_LIMIT


def test_load_limit_bytes_malformed_json():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            f.write("{not json")
        assert mod.load_limit_bytes(d) == DEFAULT_LIMIT


def test_load_limit_bytes_non_dict_root_falls_back():
    # Regression: a syntactically valid but non-dict config root (e.g. a
    # pasted array instead of an object) used to raise AttributeError from
    # `config.get(...)`, uncaught by the except tuple.
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump([1, 2, 3], f)
        assert mod.load_limit_bytes(d) == DEFAULT_LIMIT


def test_load_limit_bytes_nonpositive_falls_back():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_limit_bytes": 0}, f)
        assert mod.load_limit_bytes(d) == DEFAULT_LIMIT


def test_load_limit_bytes_non_integer_falls_back():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_limit_bytes": "not-a-number"}, f)
        assert mod.load_limit_bytes(d) == DEFAULT_LIMIT


def test_load_limit_bytes_configured_override():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_limit_bytes": 1000}, f)
        assert mod.load_limit_bytes(d) == 1000


# ---------------------------------------------------------------------------
# Layer 2: subprocess end-to-end
# ---------------------------------------------------------------------------

def test_write_new_oversized_skill_md_blocks():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * (DEFAULT_LIMIT + 1)},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert str(DEFAULT_LIMIT) in proc.stderr
        assert path in proc.stderr


def test_write_at_exactly_limit_bytes_passes():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * DEFAULT_LIMIT},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0
        assert proc.stderr == ""


def test_write_one_byte_over_limit_blocks():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * (DEFAULT_LIMIT + 1)},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 2


def test_write_under_limit_passes():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "small skill body"},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0
        assert proc.stderr == ""


def test_edit_that_grows_past_limit_blocks():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("TARGET" + "x" * (DEFAULT_LIMIT - 10))
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": path,
                "old_string": "TARGET",
                "new_string": "y" * 100,
            },
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 2


def test_edit_that_shrinks_below_limit_passes():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        # File is ALREADY over the limit (e.g. predates the guard) -- the
        # Edit shrinks it back under. Must not block a shrink.
        with open(path, "w", encoding="utf-8") as f:
            f.write("TARGET" + "x" * (DEFAULT_LIMIT + 1000))
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": path,
                "old_string": "x" * (DEFAULT_LIMIT + 1000),
                "new_string": "y",
            },
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0


def test_edit_shrinks_but_stays_over_limit_passes():
    # Regression: the file is already oversized and the edit is a genuine
    # shrink, but the result is STILL over the limit. Blocking this makes an
    # oversized file impossible to trim incrementally across multiple edits
    # -- directly contradicting the block message's own recommended
    # remediation ("split into a reference file").
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        original_size = 6 + 300000  # "TARGET" + 300000 x's
        with open(path, "w", encoding="utf-8") as f:
            f.write("TARGET" + "x" * 300000)
        old_string = "TARGET" + "x" * 30000
        resulting_size = original_size - len(old_string) + 1
        assert resulting_size > DEFAULT_LIMIT  # sanity: still over the limit
        assert resulting_size < original_size  # sanity: genuinely shrinking
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": path,
                "old_string": old_string,
                "new_string": "y",
            },
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0


def test_edit_grows_already_oversized_file_still_blocks():
    # The shrink allowance must not become a blanket bypass for already-
    # oversized files -- growing one further must still block.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("TARGET" + "x" * (DEFAULT_LIMIT + 1000))
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": path,
                "old_string": "TARGET",
                "new_string": "y" * 100,
            },
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 2


def test_edit_multiline_crlf_old_string_now_blocks():
    # End-to-end regression test for the CRLF bypass: before the fix this
    # exact scenario returned 0 (not blocked) despite a 500,000-byte insert.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "wb") as f:
            f.write(b"line one\r\nTARGET_LINE\r\nline three\r\n")
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": path,
                "old_string": "TARGET_LINE\nline three",
                "new_string": "x" * 500000,
            },
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 2


def test_edit_old_string_not_found_fails_open():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("alpha beta gamma")
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": path, "old_string": "NOPE", "new_string": "x"},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0


def test_edit_nonexistent_file_fails_open():
    with tempfile.TemporaryDirectory() as d:
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": os.path.join(d, "SKILL.md"),
                "old_string": "a",
                "new_string": "b",
            },
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0


def test_non_skill_md_write_is_fast_noop():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "REFERENCE.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * (DEFAULT_LIMIT + 1)},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0
        assert proc.stderr == ""


def test_case_variant_filename_still_blocks():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "skill.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * (DEFAULT_LIMIT + 1)},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 2


def test_notebookedit_tool_name_allows():
    with tempfile.TemporaryDirectory() as d:
        payload = {
            "tool_name": "NotebookEdit",
            "tool_input": {
                "notebook_path": os.path.join(d, "SKILL.md"),
                "content": "x" * (DEFAULT_LIMIT + 1),
            },
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0


def test_empty_stdin_allows():
    proc = _run_hook("")
    assert proc.returncode == 0


def test_malformed_json_allows():
    proc = _run_hook("{not json")
    assert proc.returncode == 0


def test_non_dict_json_allows():
    proc = _run_hook("[1, 2, 3]")
    assert proc.returncode == 0


def test_missing_tool_input_allows():
    payload = {"tool_name": "Write"}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_non_dict_tool_input_allows():
    payload = {"tool_name": "Write", "tool_input": "not-a-dict"}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_hook_config_missing_uses_default_262144():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * DEFAULT_LIMIT},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0  # exactly at the default limit


def test_hook_config_custom_limit_honored():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_limit_bytes": 100}, f)
        path = os.path.join(d, "SKILL.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * 101},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 2
        assert "100" in proc.stderr


def test_hook_config_non_dict_root_still_blocks():
    # Regression: previously the AttributeError from a non-dict config root
    # propagated past main() and was swallowed by the outer fail-open
    # handler, silently disabling the guard for this call entirely.
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump([1, 2, 3], f)
        path = os.path.join(d, "SKILL.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * (DEFAULT_LIMIT + 1)},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 2


def test_block_message_cites_remediation():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x" * (DEFAULT_LIMIT + 1)},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert "reference file" in proc.stderr
        assert "agent-skills" in proc.stderr


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
