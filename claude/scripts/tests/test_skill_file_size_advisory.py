#!/usr/bin/env python3
"""Tests for skill-file-size-advisory.py (dev-env#939).

Two layers, same shape as test_skill_file_size_guard.py: pure-function unit
tests against the imported module, plus subprocess end-to-end tests. Unlike
the guard hook, this one always stats a REAL on-disk file (the write/edit has
already happened by PostToolUse time), so every Layer-2 test writes a real
fixture file rather than relying on tool_input content alone.

Usage:
    py -3 claude/scripts/tests/test_skill_file_size_advisory.py

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
MODULE_PATH = SCRIPTS_DIR / "skill-file-size-advisory.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("skill_file_size_advisory", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()

DEFAULT_WARN = mod.DEFAULT_WARN_BYTES    # 204800
DEFAULT_LIMIT = mod.DEFAULT_LIMIT_BYTES  # 262144


def _run_hook(payload):
    stdin_text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_file_of_size(path, size):
    with open(path, "wb") as f:
        f.write(b"x" * size)


# ---------------------------------------------------------------------------
# Layer 1: pure functions
# ---------------------------------------------------------------------------

def test_is_skill_md_matches_lowercase_basename():
    assert mod._is_skill_md("/some/dir/SKILL.md") is True
    assert mod._is_skill_md("/some/dir/skill.md") is True


def test_is_skill_md_rejects_non_skill_files():
    assert mod._is_skill_md("/some/dir/REFERENCE.md") is False
    assert mod._is_skill_md("") is False


def test_load_bytes_config_defaults_when_missing():
    with tempfile.TemporaryDirectory() as d:
        warn, limit = mod.load_bytes_config(d)
        assert warn == DEFAULT_WARN
        assert limit == DEFAULT_LIMIT


def test_load_bytes_config_malformed_json():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            f.write("{not json")
        warn, limit = mod.load_bytes_config(d)
        assert warn == DEFAULT_WARN
        assert limit == DEFAULT_LIMIT


def test_load_bytes_config_nonpositive_falls_back():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_warn_bytes": 0, "skill_file_size_limit_bytes": -5}, f)
        warn, limit = mod.load_bytes_config(d)
        assert warn == DEFAULT_WARN
        assert limit == DEFAULT_LIMIT


def test_load_bytes_config_independent_override():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_warn_bytes": 500}, f)
        warn, limit = mod.load_bytes_config(d)
        assert warn == 500
        assert limit == DEFAULT_LIMIT  # not overridden -- stays default


def test_load_bytes_config_both_overridden():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_warn_bytes": 500, "skill_file_size_limit_bytes": 1000}, f)
        warn, limit = mod.load_bytes_config(d)
        assert warn == 500
        assert limit == 1000


# ---------------------------------------------------------------------------
# Layer 2: subprocess end-to-end
# ---------------------------------------------------------------------------

def test_file_at_or_above_warn_threshold_advises():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        _write_file_of_size(path, DEFAULT_WARN + 1000)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert path in proc.stderr
        assert str(DEFAULT_LIMIT) in proc.stderr


def test_file_below_warn_threshold_silent():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        _write_file_of_size(path, 100)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 0
        assert proc.stderr == ""


def test_file_exactly_at_warn_threshold_advises():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        _write_file_of_size(path, DEFAULT_WARN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 2  # inclusive: >= fires


def test_file_one_byte_under_warn_threshold_silent():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        _write_file_of_size(path, DEFAULT_WARN - 1)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 0


def test_file_missing_after_write_fails_open():
    with tempfile.TemporaryDirectory() as d:
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": os.path.join(d, "SKILL.md")},
            "cwd": d,
        }
        proc = _run_hook(payload)
        assert proc.returncode == 0


def test_non_skill_md_write_is_fast_noop():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "REFERENCE.md")
        _write_file_of_size(path, DEFAULT_WARN + 1000)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 0
        assert proc.stderr == ""


def test_case_variant_filename_still_advises():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "skill.md")
        _write_file_of_size(path, DEFAULT_WARN + 1000)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 2


def test_edit_tool_also_advises():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        _write_file_of_size(path, DEFAULT_WARN + 1000)
        payload = {"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 2


def test_bash_tool_name_not_matched():
    # Unlike journal-shard-write-advisory.py, this hook is not Bash-wired.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        _write_file_of_size(path, DEFAULT_WARN + 1000)
        payload = {"tool_name": "Bash", "tool_input": {"command": f"cat {path}"}, "cwd": d}
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


def test_hook_config_missing_uses_defaults():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SKILL.md")
        _write_file_of_size(path, DEFAULT_WARN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 2  # default warn threshold, inclusive


def test_hook_config_custom_warn_and_limit_honored():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_warn_bytes": 50, "skill_file_size_limit_bytes": 200}, f)
        path = os.path.join(d, "SKILL.md")
        _write_file_of_size(path, 60)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": d}
        proc = _run_hook(payload)
        assert proc.returncode == 2
        assert "200" in proc.stderr  # limit shown for the "N% of limit" framing


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
