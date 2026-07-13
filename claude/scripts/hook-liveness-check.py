#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook — warns when a wired hook has gone quiet.

Every wired hook records a heartbeat (`_hookutil.record_heartbeat`) as the first
statement of its own `main()`. This hook is the other half: it reads
`claude/settings.json` to discover which hook scripts are currently wired, reads
each one's heartbeat file under `~/.claude/scratch/hook-heartbeat/`, and warns
when a non-exempt hook's heartbeat is missing or older than DEFAULT_CADENCE_DAYS.

Without this, a wired hook that silently stops firing has no signal at all --
post-tool-use.py was dead for months (dev-env#377), usage-snapshot.py for 8
days (dev-env#355), each discovered only by accident. The output-contract
gates (ADR-103) verify a hook's code is *correct*; this verifies it is
*running* (ADR-106).

Exempt: hooks wired ONLY to rare-firing events (PostCompact, Notification) --
a script wired exclusively to those events can legitimately go quiet for a
long stretch with nothing wrong (PostCompact fires only on a compaction;
Notification only on specific idle/permission events). A hook also wired to
any other event is NOT exempt, even if PostCompact/Notification is among its
registrations (e.g. awake-blocker.py is wired to Notification too, but also
to UserPromptSubmit/Stop, so it stays subject to the normal cadence).

Stdin JSON shape (UserPromptSubmit):
  {
    "hook_event_name": "UserPromptSubmit",
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 always -- advisory only, never blocks. Delivered via _hookout as a
model-visible (audience="model") advisory: a stale hook is something Claude
can act on (check settings.json wiring, check the script for a crash), matching
the #717 initiative's "warnings model-visible first" decision.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import _hookout
import _hookutil

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"
DEFAULT_CADENCE_DAYS = 7
EXEMPT_EVENTS = frozenset({"PostCompact", "Notification"})

# The last whitespace-delimited token of a hook command is the script path --
# capture its basename minus ".py", which is exactly the literal string every
# hook passes to _hookutil.record_heartbeat() at its own call site (see that
# function's docstring). Matches the `pyw -3 C:/.../foo.py` invocation form
# (ADR-007).
_SCRIPT_RE = re.compile(r"([\w.-]+)\.py\s*$")


def hook_name_from_command(command: str) -> str | None:
    """The bare hook name (script basename minus .py) a settings.json command
    invokes, or None if the command's last token isn't a .py script."""
    m = _SCRIPT_RE.search((command or "").strip())
    return m.group(1) if m else None


def wired_hook_events(settings: dict) -> dict[str, set[str]]:
    """Map each wired hook name -> the set of events it is registered under.

    Walks settings.json's hooks[event][*].hooks[*].command entries. A command
    that doesn't resolve to a .py basename (hook_name_from_command returns
    None) is skipped -- it contributes no hook. Malformed/missing structure at
    any level (a non-dict `hooks`, a non-list event group, a non-dict group or
    hook entry) is skipped rather than raised, so an unexpected settings.json
    shape degrades to whatever it CAN parse instead of aborting the whole scan.
    """
    out: dict[str, set[str]] = {}
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for h in group.get("hooks", []) or []:
                if not isinstance(h, dict):
                    continue
                name = hook_name_from_command(h.get("command", "") or "")
                if name:
                    out.setdefault(name, set()).add(event)
    return out


def exempt_hooks(hook_events: dict[str, set[str]]) -> set[str]:
    """Hooks wired ONLY to rare-firing events -- never expected to heartbeat on
    the usual cadence, so they're excluded from staleness checks entirely."""
    return {
        name
        for name, events in hook_events.items()
        if events and events <= EXEMPT_EVENTS
    }


def stale_hooks(
    hook_events: dict[str, set[str]],
    heartbeat_dir: Path,
    now: float,
    cadence_days: float = DEFAULT_CADENCE_DAYS,
) -> list[dict]:
    """Return ``[{"hook": name, "last_seen": float | None}, ...]`` for every
    non-exempt wired hook whose heartbeat file is missing, unparseable, or
    older than *cadence_days*. Sorted by hook name for deterministic output.
    A heartbeat exactly *cadence_days* old is NOT stale (the boundary belongs
    to the healthy side, matching this repo's other threshold helpers).

    Pure aside from the heartbeat-file reads (*heartbeat_dir* is injectable;
    *now* is caller-supplied) so tests are deterministic without touching the
    real clock or the real scratch directory.
    """
    exempt = exempt_hooks(hook_events)
    cutoff = now - cadence_days * 86400
    stale: list[dict] = []
    for name in sorted(hook_events):
        if name in exempt:
            continue
        last_seen = None
        try:
            raw = (heartbeat_dir / f"{name}.ts").read_text(encoding="utf-8").strip()
            last_seen = float(raw)
        except (OSError, ValueError):
            last_seen = None
        if last_seen is None or last_seen < cutoff:
            stale.append({"hook": name, "last_seen": last_seen})
    return stale


def _age_desc(last_seen: float | None, now: float) -> str:
    if last_seen is None:
        return "never recorded"
    days = (now - last_seen) / 86400
    return f"last seen {days:.1f}d ago"


def format_warning(stale: list[dict], now: float, cadence_days: float) -> str:
    """Build the model-visible advisory text. Plain ASCII by convention (this
    repo's established style for hook-emitted messages)."""
    lines = [
        f"[hook-liveness] {len(stale)} wired hook(s) have not recorded a "
        f"heartbeat in over {cadence_days:g} days -- possible silent failure:",
    ]
    for entry in stale:
        lines.append(f"  - {entry['hook']}: {_age_desc(entry['last_seen'], now)}")
    lines.append(
        "Check claude/settings.json wiring and the script itself for a crash "
        "or a changed invocation path."
    )
    return "\n".join(lines)


def main() -> None:
    _hookutil.record_heartbeat("hook-liveness-check")
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)

    hook_events = wired_hook_events(settings)
    now = time.time()
    stale = stale_hooks(hook_events, _hookutil.HEARTBEAT_DIR, now, DEFAULT_CADENCE_DAYS)
    if not stale:
        sys.exit(0)

    _hookout.emit_advisory(
        "UserPromptSubmit", format_warning(stale, now, DEFAULT_CADENCE_DAYS), audience="model"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
