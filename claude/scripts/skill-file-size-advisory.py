#!/usr/bin/env python3
"""Claude Code PostToolUse hook -- non-blocking advisory when a SKILL.md
Write/Edit lands at/above a lower watermark (default 204800 / 200 KiB),
giving advance warning before pre-tool-use-skill-file-size-guard.py's hard
256KB ceiling is reached.

The write/edit has ALREADY HAPPENED by the time a PostToolUse hook fires, so
there is nothing left to block -- `_hookout.emit_block()`'s exit 2 here only
*surfaces* the message to the model (exit-2 stderr reaches the model on every
hook event; PostToolUse has no non-blocking model-visible channel), it does
not undo the write. Same established mechanism as memory-write-advisory.py.

Global, not dev-env-specific -- see pre-tool-use-skill-file-size-guard.py's
docstring for the basename-match scope rationale (identical here). Simpler
than that hook: since the write already landed, this just stats the real
on-disk file -- no encoding/newline estimation needed.

Decision:
  1. Read stdin JSON. Fail OPEN on empty/malformed/non-dict.
  2. `tool_name` must be `Write` or `Edit`, else exit 0.
  3. `file_path` from `tool_input`; basename must be `skill.md`
     (case-insensitive), else exit 0.
  4. `os.path.getsize(file_path)` the real file. Fail OPEN (exit 0) on
     `OSError` (race / permissions).
  5. Load `skill_file_size_warn_bytes` (default 204800) and
     `skill_file_size_limit_bytes` (default 262144, reused only to show "N%
     of the hard limit" in the message) from `.claude/hook-config.json`.
  6. If size >= warn_bytes (inclusive -- exactly-at-watermark advises): emit
     the advisory. Else exit 0 silently.

Fail direction: FAILS OPEN -- an advisory nudge must never disrupt a
completed write.

Stdin JSON shape (PostToolUse):
  {"tool_name": "Write" | "Edit", "tool_input": {"file_path": "...", ...}, "cwd": "..."}
"""
import json
import os
import sys

import _hookout
import _hookutil

CONFIG_FILE = ".claude/hook-config.json"
DEFAULT_WARN_BYTES = 204800    # 200 KiB
DEFAULT_LIMIT_BYTES = 262144   # 256 KiB -- keep in sync with the guard hook

ADVISORY = """[skill-file-size] {file_path} is now {size} bytes ({pct}% of the {limit} byte SKILL.md guard threshold) -- consider splitting additional content into a separate reference file the SKILL.md links to (Anthropic's progressive-disclosure pattern): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices"""


def _is_skill_md(file_path: str) -> bool:
    return os.path.basename(file_path).lower() == "skill.md"


def load_bytes_config(cwd: str):
    """Returns (warn_bytes, limit_bytes). Each field independently falls
    back to its own default on any read/parse/type problem or a
    non-positive configured value -- never raises."""
    path = os.path.join(cwd or "", CONFIG_FILE)
    warn, limit = DEFAULT_WARN_BYTES, DEFAULT_LIMIT_BYTES
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        w = int(config.get("skill_file_size_warn_bytes", DEFAULT_WARN_BYTES))
        l = int(config.get("skill_file_size_limit_bytes", DEFAULT_LIMIT_BYTES))
        warn = w if w > 0 else DEFAULT_WARN_BYTES
        limit = l if l > 0 else DEFAULT_LIMIT_BYTES
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return warn, limit


def main() -> None:
    _hookutil.record_heartbeat("skill-file-size-advisory")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)
    file_path = tool_input.get("file_path", "")
    if not file_path or not _is_skill_md(file_path):
        sys.exit(0)

    try:
        size = os.path.getsize(file_path)
    except OSError:
        sys.exit(0)  # race / permissions -- fail open

    warn_bytes, limit_bytes = load_bytes_config(data.get("cwd", ""))
    if size >= warn_bytes:
        pct = round(size / limit_bytes * 100)
        _hookout.emit_block(ADVISORY.format(
            file_path=file_path, size=size, pct=pct, limit=limit_bytes))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
