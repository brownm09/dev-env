#!/usr/bin/env python3
"""Tests for _skill_file_size.py -- the basename-match + `.claude/hook-config.json`
loading shared by pre-tool-use-skill-file-size-guard.py and
skill-file-size-advisory.py (dev-env#939).

Direct coverage of the two shared functions. Both hooks' own test files also
exercise this module indirectly through their thin wrapper functions
(`_is_skill_md`, `load_limit_bytes`, `load_bytes_config`) -- this file is the
one place the module's behavior is pinned in isolation, per this repo's
one-test-file-per-shared-module convention (tests/README.md -> Shared support
modules).

Usage:
    py -3 claude/scripts/tests/test_skill_file_size.py

Exit 0 = all pass.
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "_skill_file_size.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("skill_file_size", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()

DEFAULT_WARN = mod.DEFAULT_WARN_BYTES    # 204800
DEFAULT_LIMIT = mod.DEFAULT_LIMIT_BYTES  # 262144


def test_is_skill_md_matches_case_variants():
    assert mod.is_skill_md("/some/dir/SKILL.md") is True
    assert mod.is_skill_md("/some/dir/skill.md") is True
    assert mod.is_skill_md("/some/dir/Skill.MD") is True


def test_is_skill_md_rejects_non_skill_files():
    assert mod.is_skill_md("/some/dir/SKILL.md.bak") is False
    assert mod.is_skill_md("/some/dir/REFERENCE.md") is False
    assert mod.is_skill_md("") is False


def test_load_config_defaults_when_missing():
    with tempfile.TemporaryDirectory() as d:
        warn, limit = mod.load_config(d)
        assert warn == DEFAULT_WARN
        assert limit == DEFAULT_LIMIT


def test_load_config_malformed_json_falls_back():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            f.write("{not json")
        warn, limit = mod.load_config(d)
        assert warn == DEFAULT_WARN
        assert limit == DEFAULT_LIMIT


def test_load_config_non_dict_root_falls_back():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        for root in ([1, 2, 3], "a string", 42):
            with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
                json.dump(root, f)
            warn, limit = mod.load_config(d)
            assert warn == DEFAULT_WARN
            assert limit == DEFAULT_LIMIT


def test_load_config_nonpositive_values_fall_back():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_warn_bytes": 0, "skill_file_size_limit_bytes": -5}, f)
        warn, limit = mod.load_config(d)
        assert warn == DEFAULT_WARN
        assert limit == DEFAULT_LIMIT


def test_load_config_non_integer_values_fall_back():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_warn_bytes": "nope"}, f)
        warn, limit = mod.load_config(d)
        assert warn == DEFAULT_WARN


def test_load_config_independent_field_override():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_warn_bytes": 500}, f)
        warn, limit = mod.load_config(d)
        assert warn == 500
        assert limit == DEFAULT_LIMIT  # not overridden -- stays default


def test_load_config_both_fields_overridden():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w") as f:
            json.dump({"skill_file_size_warn_bytes": 500, "skill_file_size_limit_bytes": 1000}, f)
        warn, limit = mod.load_config(d)
        assert warn == 500
        assert limit == 1000


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
