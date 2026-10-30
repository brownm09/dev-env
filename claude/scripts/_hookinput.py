"""The hook payload loader every hook shares (dev-env#1168).

Kept apart from ``_hookio`` on purpose: that module is ~1,300 lines of command
parsers, and about a dozen hooks need nothing from it but this loader. Each hook
is its own process, so importing the whole module cost them 11-15 ms per fire,
including two that run on every Bash and PowerShell call. ``_hookio``
re-exports both names, so either import works.
"""

from __future__ import annotations

import json
import sys

SHELL_TOOLS = ("Bash", "PowerShell")


def load_hook_input(tools: tuple[str, ...] | None = None) -> dict | None:
    """Read the hook payload from stdin and return it as a dict, or None.

    None means "nothing this hook should act on": stdin is empty or not JSON,
    the JSON is not an object (a list, string, number or null, dev-env#1031),
    or *tools* is given and the payload's ``tool_name`` is not in it. Callers
    either exit 0 on None, failing open, or substitute ``{}`` and carry on,
    which runs the hook on an empty payload; pick by what the hook did before.
    The fail-closed gates (ADR-083, ADR-096) exit 0 on None too: they fail
    closed only on a crash after a payload was read.

    ``sys.stdin`` is looked up at call time, so a test that swaps
    ``mod.sys.stdin`` for a ``StringIO`` keeps working.
    """
    raw = sys.stdin.read().strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if tools is not None and data.get("tool_name") not in tools:
        return None
    return data
