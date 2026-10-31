#!/usr/bin/env python3
"""Shared test helper: parse claude/settings.shared.json into the set of wired hooks,
their events, and their declared timeouts.

The tracked source, not the live machine-local ~/.claude/settings.json: `hooks` is an
owned key, so _settings_sync keeps the two identical, and the tracked file is the one CI
can gate deterministically (dev-env#1049, ADR-139).

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
import sys
from pathlib import Path
from typing import NamedTuple

# tests/ -> scripts/ -> claude/ -> repo root  (matches test_no_crude_command_substring_checks.py)
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
SETTINGS_PATH = REPO_ROOT / "claude" / "settings.shared.json"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from _settings_sync import py_tokens  # noqa: E402 -- the one hook-command tokenizer (ADR-151)

# The author-home prefix every shipped hook command is written with, and the launcher
# path under it (ADR-148). Defined once so the tests that pin or remap the command form
# (test_settings_hook_wiring, test_settings_sync's calibration) change together when
# dev-env#1113 relocates the prefix.
HOME_PREFIX = "C:/Users/brown/.claude/"
LAUNCHER_PATH = HOME_PREFIX + "hook-launch.py"
SCRIPTS_PREFIX = HOME_PREFIX + "scripts/"

# Every `<name>.py` argument after the launcher is a script the command runs: the hook
# itself (`pyw -3 C:/.../hook-launch.py C:/.../foo.py`, ADR-007, ADR-148), or the
# dispatcher followed by each handler it runs in-process
# (`... hook-launch.py C:/.../hook-dispatch.py C:/.../a.py C:/.../b.py`, dev-env#851,
# ADR-151). Gates must see every handler, not only the last token. Arguments come from
# `_settings_sync.py_tokens`, the tokenizer its missing-script guard uses (quoted paths
# included), so the gates, that guard and hook-liveness-check read a command the same way.
LAUNCHER_NAME = "hook-launch.py"
DISPATCHER_NAME = "hook-dispatch.py"


class HookEntry(NamedTuple):
    """One wired hook entry, flattened from settings.json's event/matcher/hook nesting."""

    event: str            # e.g. "PreToolUse", "UserPromptSubmit", "Stop"
    matcher: str | None   # e.g. "Bash", "Write"; None for event groups with no matcher
    command: str          # the full command string
    script: str | None    # the .py basename the command invokes (a dispatcher's own name), or None
    timeout: object       # the entry's "timeout" value verbatim (int, or None if absent)
    scripts: tuple = ()   # every .py basename the command runs: script, then any dispatched handlers


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def scripts_from_command(command: str) -> tuple:
    """Every `<name>.py` basename the command runs, in order, the launcher excluded."""
    names = (Path(token).name for token in py_tokens(command))
    return tuple(name for name in names if name != LAUNCHER_NAME)


def script_from_command(command: str) -> str | None:
    """Return the `<name>.py` basename the command invokes (a dispatcher's own), or None."""
    scripts = scripts_from_command(command)
    return scripts[0] if scripts else None


def hook_entries(settings: dict) -> list[HookEntry]:
    """Every wired hook entry across all events, in settings.json order.

    Duplicates are preserved: a script wired under multiple events (e.g.
    disk-space-check under both PreToolUse and UserPromptSubmit, or
    journal-shard-write-advisory under PostToolUse Bash/Write/Edit) yields one
    HookEntry per registration, which is what the wiring lint needs to check every
    entry's timeout independently.
    """
    entries: list[HookEntry] = []
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return entries
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            for h in group.get("hooks", []) or []:
                if not isinstance(h, dict):
                    continue
                command = h.get("command", "") or ""
                entries.append(
                    HookEntry(
                        event=event,
                        matcher=matcher,
                        command=command,
                        script=script_from_command(command),
                        timeout=h.get("timeout"),
                        scripts=scripts_from_command(command),
                    )
                )
    return entries


def unparsed_commands(settings: dict) -> list[HookEntry]:
    """Wired entries whose command did not resolve to a `<name>.py` (script is None).

    Every gate that derives its scan set from :func:`wired_scripts` silently drops
    these (a None script contributes no basename), so each such gate calls this to
    fail loudly on an unparseable command rather than trusting a sibling gate to
    catch it (dev-env#726 review)."""
    return [e for e in hook_entries(settings) if e.script is None]


def wired_script_events(settings: dict) -> dict[str, set[str]]:
    """Map each wired script basename -> the set of events it is registered under.

    A command that resolves to no .py basename is skipped (it contributes no
    script). Used by the output-contract gate to classify a script's emissions
    against context vs. non-context events.
    """
    out: dict[str, set[str]] = {}
    for e in hook_entries(settings):
        for script in e.scripts:  # a dispatcher and each of its handlers (dev-env#851)
            out.setdefault(script, set()).add(e.event)
    return out


def wired_scripts(settings: dict) -> list[str]:
    """Sorted, de-duplicated list of wired script basenames."""
    return sorted(wired_script_events(settings))
