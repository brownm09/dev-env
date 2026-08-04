#!/usr/bin/env python3
"""Shared basename-match and `.claude/hook-config.json` loading for the two
SKILL.md size-guard hooks (`pre-tool-use-skill-file-size-guard.py`,
`skill-file-size-advisory.py`) -- kept in one place so the guard's hard limit
and the advisory's watermark/limit can't silently drift apart, per this
repo's established `_hookout.py`/`_hookutil.py`/`_journal_schema.py` pattern
of one shared module per piece of cross-hook logic.
"""
import json
import os

CONFIG_FILE = ".claude/hook-config.json"
DEFAULT_LIMIT_BYTES = 262144   # 256 KiB -- guard's hard-block ceiling
DEFAULT_WARN_BYTES = 204800    # 200 KiB -- advisory's watermark


def is_skill_md(file_path: str) -> bool:
    return os.path.basename(file_path).lower() == "skill.md"


def load_config(cwd: str):
    """Returns (warn_bytes, limit_bytes) from `.claude/hook-config.json` in
    *cwd*. Each field independently falls back to its own default on any
    read/parse/type problem, a non-dict config root, or a non-positive
    configured value -- never raises."""
    path = os.path.join(cwd or "", CONFIG_FILE)
    warn, limit = DEFAULT_WARN_BYTES, DEFAULT_LIMIT_BYTES
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            return warn, limit
        w = int(config.get("skill_file_size_warn_bytes", DEFAULT_WARN_BYTES))
        lim = int(config.get("skill_file_size_limit_bytes", DEFAULT_LIMIT_BYTES))
        warn = w if w > 0 else DEFAULT_WARN_BYTES
        limit = lim if lim > 0 else DEFAULT_LIMIT_BYTES
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return warn, limit
