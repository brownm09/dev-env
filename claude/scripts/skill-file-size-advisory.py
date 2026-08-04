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

Kept as a separate PostToolUse hook rather than folded into the PreToolUse
guard's own decision: the guard predicts a *pre-write* resulting size (an
estimate the guard itself derives, subject to the CRLF/uniqueness handling
in pre-tool-use-skill-file-size-guard.py), while this hook reports the
*actual* post-write on-disk size -- distinct enough sources of truth that
collapsing them would mean the watermark check no longer reflects what's
really on disk after Claude Code's own write path (encoding, atomicity,
concurrent modification) has run. The second `pyw -3` process this costs is
paid only on a `SKILL.md` Write/Edit -- a narrow, infrequent trigger, not
every tool call.

Decision:
  1. Read stdin JSON. Fail OPEN on empty/malformed/non-dict.
  2. `tool_name` must be `Write` or `Edit`, else exit 0.
  3. `file_path` from `tool_input`; basename must be `skill.md`
     (case-insensitive), else exit 0.
  4. `os.path.getsize(file_path)` the real file. Fail OPEN (exit 0) on
     `OSError` (race / permissions).
  5. Load `skill_file_size_warn_bytes` (default 204800) and
     `skill_file_size_limit_bytes` (default 262144, reused only to show "N%
     of the hard limit" in the message) from `.claude/hook-config.json`,
     shared with the guard hook via `_skill_file_size.py`.
  6. If size >= warn_bytes (inclusive -- exactly-at-watermark advises): emit
     the advisory, gated to once per session per file via a
     `_hookutil.sentinel_path()` marker keyed on `session_id` + a hash of
     `file_path` -- otherwise a multi-edit session actively trying to fix an
     oversized file gets the same nudge re-emitted on every single edit,
     training the reader to ignore it. A missing/empty `session_id` (an
     anomalous payload) skips dedup rather than blocking the advisory, per
     this repo's "a payload we can't dedupe we don't block" convention
     (posttooluse-inert-advisory.py). Else exit 0 silently.

Fail direction: FAILS OPEN -- an advisory nudge must never disrupt a
completed write.

Stdin JSON shape (PostToolUse):
  {"tool_name": "Write" | "Edit", "tool_input": {"file_path": "...", ...},
   "cwd": "...", "session_id": "..."}
"""
import hashlib
import json
import os
import sys

import _hookout
import _hookutil
import _skill_file_size

DEFAULT_WARN_BYTES = _skill_file_size.DEFAULT_WARN_BYTES     # re-exported for tests/back-compat
DEFAULT_LIMIT_BYTES = _skill_file_size.DEFAULT_LIMIT_BYTES   # re-exported for tests/back-compat

SENTINEL_PREFIX = "skill-file-size-advisory-"

ADVISORY = """[skill-file-size] {file_path} is now {size} bytes ({pct}% of the {limit} byte SKILL.md guard threshold) -- consider splitting additional content into a separate reference file the SKILL.md links to (Anthropic's progressive-disclosure pattern): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices"""


def _is_skill_md(file_path: str) -> bool:
    return _skill_file_size.is_skill_md(file_path)


def load_bytes_config(cwd: str):
    """Returns (warn_bytes, limit_bytes) -- see `_skill_file_size.load_config()`
    for the fallback contract (never raises)."""
    return _skill_file_size.load_config(cwd)


def _sentinel_for(session_id: str, file_path: str):
    """Per-session, per-file sentinel path -- two different oversized skills
    edited in the same session each still get their own nudge."""
    file_hash = hashlib.sha1(os.path.normcase(file_path).encode("utf-8")).hexdigest()[:12]
    return _hookutil.sentinel_path(f"{SENTINEL_PREFIX}{file_hash}-", session_id)


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
    if size < warn_bytes:
        sys.exit(0)

    session_id = data.get("session_id") or ""
    sentinel = _sentinel_for(session_id, file_path) if session_id else None
    if sentinel is not None and sentinel.exists():
        sys.exit(0)  # already nudged for this file this session

    # Write the sentinel BEFORE emit_block(): _hookout.emit_block() is typed
    # NoReturn (it exits the process internally), so anything placed after
    # that call never runs. This is the opposite order from
    # posttooluse-inert-advisory.py's "emit first, mark_resolved after"
    # (dev-env#629 -- a failed emission there shouldn't silently consume a
    # one-shot dedup), but that hook hand-rolls its own stderr write+exit
    # specifically to control the ordering; staying on the shared
    # emit_block() helper here means accepting sentinel-before-emit instead.
    # The tradeoff is deliberately low-stakes: on the rare failed-write path
    # this could skip one advisory nudge, never a missed enforcement action.
    if sentinel is not None:
        try:
            _hookutil.SCRATCH.mkdir(exist_ok=True)
            sentinel.write_text("")
        except OSError:
            pass  # best-effort; a failed sentinel write just means one more nudge

    pct = round(size / limit_bytes * 100)
    # No trailing sys.exit(0): every path above that doesn't advise already
    # exited, so this call is unconditional -- emit_block() (NoReturn) is
    # always this function's last action.
    _hookout.emit_block(ADVISORY.format(
        file_path=file_path, size=size, pct=pct, limit=limit_bytes))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
