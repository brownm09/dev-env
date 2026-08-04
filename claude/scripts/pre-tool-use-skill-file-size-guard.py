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
         the same substitution, on a line-ending-normalized copy: the real
         Edit tool matches `old_string` against file content with line
         endings normalized to `\n` (a model-authored `old_string` is always
         `\n`, even against a CRLF file), then writes `new_string` back
         converted to the file's own line ending. Fail OPEN (exit 0) if the
         file can't be read, `old_string` is empty, `old_string` isn't found
         in the (normalized) current content, or `old_string` occurs more
         than once with `replace_all` not set -- in each case the real Edit
         tool independently refuses the call itself (not found / not
         unique), so no write happens regardless of what this hook decided.
  5. Load the configured limit from `.claude/hook-config.json` in the
     session's `cwd` (`skill_file_size_limit_bytes`, default 262144), shared
     with skill-file-size-advisory.py via `_skill_file_size.py`.
  6. Block only when the edit would make the file GROW past the limit:
     `size > limit and size > current_on_disk_size` (0 for a not-yet-existing
     file). A file that's already over the limit can still be shrunk one
     edit at a time -- blocking every edit that doesn't single-handedly land
     under the ceiling would make an oversized file impossible to fix
     incrementally, defeating the block message's own recommended
     remediation. Strictly-greater-than the limit blocks -- exactly at the
     limit passes.

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
import _skill_file_size

DEFAULT_LIMIT_BYTES = _skill_file_size.DEFAULT_LIMIT_BYTES  # re-exported for tests/back-compat

BLOCK_MESSAGE = """[skill-file-size-guard] BLOCKED: this {tool_name} would leave {file_path} at {size} bytes, over the {limit} byte SKILL.md guard threshold.

Split additional content into a separate reference file the SKILL.md links to (Anthropic's progressive-disclosure pattern): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices

Override the threshold for this project via "skill_file_size_limit_bytes" in .claude/hook-config.json if 256KB is genuinely too small here."""


def _is_skill_md(file_path: str) -> bool:
    return _skill_file_size.is_skill_md(file_path)


def load_limit_bytes(cwd: str) -> int:
    """Configured hard limit in bytes -- see `_skill_file_size.load_config()`
    for the fallback contract (never raises)."""
    _, limit = _skill_file_size.load_config(cwd)
    return limit


def current_file_size(file_path: str) -> int:
    """Bytes the file currently occupies on disk, or 0 if it doesn't exist
    yet (every byte of a brand-new file counts as growth)."""
    try:
        return os.path.getsize(file_path)
    except OSError:
        return 0


def resulting_write_size(tool_input: dict) -> int:
    """Bytes the file would be after a Write -- pure, no I/O needed."""
    content = tool_input.get("content", "")
    return len(content.encode("utf-8")) if isinstance(content, str) else 0


def resulting_edit_size(file_path: str, tool_input: dict):
    """Bytes the file would be after an Edit, or None -> caller fails open.

    None covers: unreadable file (missing / OS error / undecodable as utf-8),
    an empty old_string, old_string not present in the (line-ending-
    normalized) current content, or old_string occurring more than once with
    replace_all not set -- all cases where either there is nothing to reason
    about, or the real Edit tool will independently refuse the call itself
    (not found / not unique).
    """
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")
    replace_all = bool(tool_input.get("replace_all", False))
    if not old_string:
        return None
    try:
        # newline="" preserves CRLF bytes literally on read -- needed below
        # to detect the file's line-ending convention and reproduce it in
        # the result; dev-env's own skills are LF-forced via .gitattributes,
        # but this hook is global and also reads other projects' files.
        with open(file_path, encoding="utf-8", newline="") as f:
            current = f.read()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None

    # The real Edit tool matches old_string against file content with line
    # endings normalized to \n, then writes new_string back converted to the
    # file's own line ending. Replicate both halves -- matching only the
    # comparison on \n while measuring new_string's raw bytes would still
    # undercount a CRLF file's true resulting size by one byte per inserted
    # line.
    is_crlf = "\r\n" in current
    current_n = current.replace("\r\n", "\n")
    old_string_n = old_string.replace("\r\n", "\n")
    new_string_n = new_string.replace("\r\n", "\n")

    if old_string_n not in current_n:
        return None  # real Edit tool independently fails with "not found"
    if not replace_all and current_n.count(old_string_n) > 1:
        return None  # real Edit tool independently fails with "not unique"

    updated_n = (
        current_n.replace(old_string_n, new_string_n)
        if replace_all
        else current_n.replace(old_string_n, new_string_n, 1)
    )
    updated = updated_n.replace("\n", "\r\n") if is_crlf else updated_n
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
            sys.exit(0)  # fail open: unreadable file / old_string not found or not unique

    limit = load_limit_bytes(data.get("cwd", ""))
    # Block only on growth past the limit -- an edit that shrinks an
    # already-oversized file (but doesn't single-handedly land under the
    # ceiling) must still be allowed, or an oversized SKILL.md becomes
    # impossible to trim incrementally.
    if size > limit and size > current_file_size(file_path):
        _hookout.emit_block(BLOCK_MESSAGE.format(
            tool_name=tool_name, file_path=file_path, size=size, limit=limit))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
