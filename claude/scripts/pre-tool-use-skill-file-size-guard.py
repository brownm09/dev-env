#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- blocks a Write/Edit that would leave a
SKILL.md file over a configurable byte ceiling (default 262144 / 256 KiB).

Global, not dev-env-specific: matches any `Write`/`Edit` whose target's
basename is `SKILL.md` (case-insensitive), regardless of repo or directory
convention -- this also covers `claude/routines/*/SKILL.md` in dev-env and
any other project's `.claude/skills/*/SKILL.md` or `~/.claude/skills/*/
SKILL.md`, which is intentional (dev-env#939).

There is no published Anthropic byte-size limit for SKILL.md -- only a soft
"keep the body under 500 lines" guideline
(https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
and an unrelated 30MB total-bundle limit for the Skills API upload path. The
256KB default here is an engineering ceiling, not a claimed published limit.

Scope: SKILL.md only (basename match) -- not bundled reference/script files,
which Anthropic's own docs say have "no practical limit" since they're read
on demand, not loaded in full at trigger time the way SKILL.md is.

Decision:
  1. Read stdin JSON. Fail OPEN on empty/malformed/non-dict.
  2. `tool_name` must be `Write` or `Edit`, else exit 0. (Not `NotebookEdit`
     -- a SKILL.md is never a notebook.)
  3. `file_path` from `tool_input`; basename must be `skill.md`
     (case-insensitive), else exit 0 -- a cheap pre-filter, no I/O.
  4. Compute the RESULTING size in bytes if the write/edit proceeds:
       - Write: `len(tool_input["content"].encode("utf-8"))`.
       - Edit: PreToolUse fires BEFORE the edit executes, and Edit's
         tool_input carries only `old_string`/`new_string`/`replace_all` --
         not the resulting content. Read the current on-disk file and apply
         the same substitution. Fail OPEN (exit 0) if the file can't be read,
         `old_string` is empty, or `old_string` isn't found in the current
         content -- in the last case the real Edit tool independently fails
         with "string not found", so no write happens regardless of what
         this hook decided.
  5. Load the configured limit from `.claude/hook-config.json` in the
     session's `cwd` (`skill_file_size_limit_bytes`, default 262144).
  6. If size > limit (strictly greater-than -- exactly-at-limit passes):
     block via `_hookout.emit_block()`, naming the size, the limit, and the
     remediation (split into a reference file the SKILL.md links to --
     Anthropic's own progressive-disclosure pattern).

Fail direction: FAILS OPEN (REFERENCE.md authoring rule 5) -- this is a size
sanity-check, not a critical control point; a crash here must not block every
SKILL.md write/edit across every project on this machine.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Write" | "Edit",
    "tool_input": {
      "file_path": "...",
      "content": "..."                                   # Write
      "old_string": "...", "new_string": "...",
      "replace_all": <bool, optional>                     # Edit
    },
    "cwd": "..."
  }
"""
import json
import os
import sys

import _hookout
import _hookutil

CONFIG_FILE = ".claude/hook-config.json"
DEFAULT_LIMIT_BYTES = 262144  # 256 KiB -- keep in sync with skill-file-size-advisory.py

BLOCK_MESSAGE = """[skill-file-size-guard] BLOCKED: this {tool_name} would leave {file_path} at {size} bytes, over the {limit} byte SKILL.md guard threshold.

Split additional content into a separate reference file the SKILL.md links to (Anthropic's progressive-disclosure pattern): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices

Override the threshold for this project via "skill_file_size_limit_bytes" in .claude/hook-config.json if 256KB is genuinely too small here."""


def _is_skill_md(file_path: str) -> bool:
    return os.path.basename(file_path).lower() == "skill.md"


def load_limit_bytes(cwd: str) -> int:
    """Configured hard limit in bytes, falling back to DEFAULT_LIMIT_BYTES on
    any read/parse/type problem or a non-positive configured value -- never
    raises."""
    path = os.path.join(cwd or "", CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        limit = int(config.get("skill_file_size_limit_bytes", DEFAULT_LIMIT_BYTES))
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError):
        return DEFAULT_LIMIT_BYTES
    return limit if limit > 0 else DEFAULT_LIMIT_BYTES


def resulting_write_size(tool_input: dict) -> int:
    """Bytes the file would be after a Write -- pure, no I/O needed."""
    content = tool_input.get("content", "")
    return len(content.encode("utf-8")) if isinstance(content, str) else 0


def resulting_edit_size(file_path: str, tool_input: dict):
    """Bytes the file would be after an Edit, or None -> caller fails open.

    None covers: unreadable file (missing / OS error / undecodable as utf-8),
    an empty old_string, or old_string not present in the current content --
    all cases where either there is nothing to reason about, or the real Edit
    tool will independently refuse the call on its own.
    """
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")
    replace_all = bool(tool_input.get("replace_all", False))
    if not old_string:
        return None
    try:
        # newline="" preserves CRLF bytes literally on read -- without it,
        # universal-newline translation silently collapses \r\n -> \n, which
        # would UNDER-count the resulting size on a non-LF-normalized file.
        # dev-env's own skills are LF-forced via .gitattributes, but this
        # hook is global and also reads other projects' SKILL.md files.
        with open(file_path, encoding="utf-8", newline="") as f:
            current = f.read()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    if old_string not in current:
        return None  # real Edit tool independently fails with "not found"
    updated = (
        current.replace(old_string, new_string)
        if replace_all
        else current.replace(old_string, new_string, 1)
    )
    return len(updated.encode("utf-8"))


def main() -> None:
    _hookutil.record_heartbeat("pre-tool-use-skill-file-size-guard")
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

    if tool_name == "Write":
        size = resulting_write_size(tool_input)
    else:
        size = resulting_edit_size(file_path, tool_input)
        if size is None:
            sys.exit(0)  # fail open: unreadable file / old_string not found

    limit = load_limit_bytes(data.get("cwd", ""))
    if size > limit:  # strictly greater-than: exactly-at-limit passes
        _hookout.emit_block(BLOCK_MESSAGE.format(
            tool_name=tool_name, file_path=file_path, size=size, limit=limit))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
