"""Shared I/O helpers for Claude Code PostToolUse Bash hooks.

Claude Code's Bash hook payload exposes a command's output under
``tool_response.stdout`` and ``tool_response.stderr`` — NOT ``output``.
Reading ``output`` yields ``""`` and silently breaks any hook that keys off
command output: the bug was found in ``post-tool-use.py`` (dev-env #377 /
ADR-049) and the same wrong read existed in four sibling hooks (dev-env #380).
Centralising the correct read here means every hook shares one implementation
instead of re-deriving the field precedence (and re-introducing the bug).

Imported the same way as ``_winsubp``: a sibling module in ``scripts/`` that the
``pyw -3`` hook launcher (which puts the script's own directory on ``sys.path``)
and the test harness (``sys.path.insert(0, scripts_dir)``) both resolve.

Usage:
    from _hookio import read_command_output

See ADR-049 (root cause + canonical read) and ADR-050 (shared helper + sibling
hook fixes).
"""

from __future__ import annotations


def read_command_output(data: dict) -> str:
    """Return a Bash command's combined output from a PostToolUse payload.

    Joins ``tool_response.stdout`` and ``tool_response.stderr`` with a newline,
    falling back to the legacy ``output`` key for forward/backward
    compatibility. Returns ``""`` for a missing, empty, ``None``, or non-dict
    ``tool_response`` — never raises, so a hook can call it unguarded.
    """
    tr = data.get("tool_response") or {}
    if not isinstance(tr, dict):
        return ""
    parts = [p for p in (tr.get("stdout"), tr.get("stderr")) if p]
    if parts:
        return "\n".join(parts)
    return str(tr.get("output", "") or "")
