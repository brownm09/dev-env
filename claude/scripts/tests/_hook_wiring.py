#!/usr/bin/env python3
"""Shared test helper: parse claude/settings.json into the set of wired hooks,
their events, and their declared timeouts.

The three PR3 output-contract gates all need to reason about *which* hook scripts
are wired and *under which events* — the safe-exit structural test needs the wired
set, the output-contract AST test needs each script's event class (context vs.
non-context, per _hookout.STDOUT_MODEL_VISIBLE_EVENTS), and the settings-wiring
lint needs every (event, command, timeout) triple. Rather than re-parse
settings.json three ways (the exact per-hook-copy drift this whole initiative,
dev-env#717, exists to end), they share this one parser. It lives in tests/ (not
scripts/) so the gates' own `claude/scripts/*.py` glob never scans it as a wired
hook, and it is deliberately *contract-agnostic* — it knows nothing about which
events are model-visible; that lives in _hookout (ADR-103), which the
output-contract gate imports directly.

Not a test file (no `test_*` functions, no runner) — a supporting module, the
tests/ analogue of scripts/`_hookio.py` / `_hookutil.py`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

# tests/ -> scripts/ -> claude/ -> repo root  (matches test_no_crude_command_substring_checks.py)
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
SETTINGS_PATH = REPO_ROOT / "claude" / "settings.json"

# The last whitespace-delimited token of a hook command is the script path; grab
# its `<name>.py` basename. Matches the `pyw -3 C:/.../foo.py` invocation form
# (ADR-007). A command that ends in something other than a .py (none today) -> None.
_SCRIPT_RE = re.compile(r"([\w.-]+\.py)\s*$")


class HookEntry(NamedTuple):
    """One wired hook entry, flattened from settings.json's event/matcher/hook nesting."""

    event: str            # e.g. "PreToolUse", "UserPromptSubmit", "Stop"
    matcher: str | None   # e.g. "Bash", "Write"; None for event groups with no matcher
    command: str          # the full command string
    script: str | None    # the .py basename the command invokes, or None
    timeout: object       # the entry's "timeout" value verbatim (int, or None if absent)


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def script_from_command(command: str) -> str | None:
    """Return the `<name>.py` basename the command invokes, or None."""
    m = _SCRIPT_RE.search(command.strip())
    return m.group(1) if m else None


def hook_entries(settings: dict) -> list[HookEntry]:
    """Every wired hook entry across all events, in settings.json order.

    Duplicates are preserved: a script wired under multiple events (e.g.
    disk-space-check under both PreToolUse and UserPromptSubmit, or
    journal-shard-write-advisory under PostToolUse Bash/Write/Edit) yields one
    HookEntry per registration, which is what the wiring lint needs to check every
    entry's timeout independently.
    """
    entries: list[HookEntry] = []
    for event, groups in settings.get("hooks", {}).items():
        for group in groups:
            matcher = group.get("matcher")
            for h in group.get("hooks", []):
                command = h.get("command", "")
                entries.append(
                    HookEntry(
                        event=event,
                        matcher=matcher,
                        command=command,
                        script=script_from_command(command),
                        timeout=h.get("timeout"),
                    )
                )
    return entries


def wired_script_events(settings: dict) -> dict[str, set[str]]:
    """Map each wired script basename -> the set of events it is registered under.

    A command that resolves to no .py basename is skipped (it contributes no
    script). Used by the output-contract gate to classify a script's emissions
    against context vs. non-context events.
    """
    out: dict[str, set[str]] = {}
    for e in hook_entries(settings):
        if e.script:
            out.setdefault(e.script, set()).add(e.event)
    return out


def wired_scripts(settings: dict) -> list[str]:
    """Sorted, de-duplicated list of wired script basenames."""
    return sorted(wired_script_events(settings))
