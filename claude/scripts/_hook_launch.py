#!/usr/bin/env python3
"""Hook launcher: run a wired hook script, or fail OPEN if the script is missing.

Why this exists (dev-env#1146, ADR-148)
---------------------------------------
Every hook in claude/settings.shared.json used to run as

    pyw -3 C:/Users/brown/.claude/scripts/<name>.py

and when that file is missing, Python itself exits 2 ("can't open file"). Claude Code
reads exit 2 as a BLOCKING verdict, so one missing script blocked every matching tool
call or prompt, in every session on the machine. Since ADR-139 the wiring lives in the
machine-local ~/.claude/settings.json, while the scripts come from the canonical
checkout's working tree through the ~/.claude/scripts junction -- so any lag between the
two (a canonical drifted onto an older branch, a sync run from a worktree before the
canonical pulled) was a machine-wide lockout. Seen live on 2026-10-09: Bash, PowerShell
and Write were blocked until the canonical was recovered through a hook-free terminal.

A missing script is never a gate's deliberate verdict; it means the tooling is broken.
So every hook now runs through this launcher:

    pyw -3 C:/Users/brown/.claude/hook-launch.py C:/Users/brown/.claude/scripts/<name>.py

  * script present -> it runs IN THIS PROCESS as `__main__` (compiled and exec'd the
    way `python <script>` does it), with stdin, stdout, stderr, argv and the exit code
    all passed through untouched, so each hook keeps its own declared fail direction
    (ADR-103 authoring rule 5);
  * script missing -> one ASCII `{"systemMessage": ...}` line (reaches the USER on every
    hook event, per the _hookout table) and exit 0. That holds for the two fail-closed
    gates too: a missing gate has no code left to scope its block to the commands it
    gates, and both are wired on every Bash call, so failing closed would block ALL Bash
    -- the lockout this file exists to end. Their message says so loudly instead.

Where it lives, and why it must stay dependency-free
----------------------------------------------------
This file is the SOURCE; `_settings_sync.ensure_launcher` copies it to
~/.claude/hook-launch.py, a real file OUTSIDE every junction, before it ever wires a
command that names it. That placement is the point: when the canonical regresses to an
older tree, everything under ~/.claude/scripts regresses with it, but the installed
launcher does not. For the same reason it imports nothing but the standard library --
it has to work precisely when the dev-env tree is stale, half-updated, or broken.
test_hook_launch.py pins the stdlib-only rule.

The script stays the LAST token of the command, so every parser that reads a hook's
identity from that token (tests/_hook_wiring.py, hook-liveness-check.py, the ADR-106
heartbeat ledger) is unaffected. `pyw -3` (ADR-007) is unchanged.

Cost
----
This file runs in front of every hook on every tool call, so it imports only `os` and
`sys`, which the interpreter has loaded before any script starts. `json` is imported
on the missing-script path alone, and `runpy` is avoided entirely: with the `pkgutil`
it pulls in, it measured about 15 ms per hook under `-X importtime`, while the direct
compile + exec below costs almost nothing.
"""
import builtins
import os
import sys

# The two fail-closed gates (ADR-083, ADR-096; ADR-103 authoring rule 5). Only the
# warning text differs for them -- see the module docstring for why they fail open
# here too. Kept equal to test_hook_safe_exit_guard.FAIL_CLOSED by test_hook_launch.py.
GATE_SCRIPTS = frozenset({
    "pre-auto-merge-checkpoint-gate.py",
    "pre-tool-use-journal-compose-force-guard.py",
})

RECOVERY = (
    "The dev-env canonical is probably behind its hook wiring: "
    "git -C ~/Git/dev-env checkout main && git -C ~/Git/dev-env pull"
)


def _ascii(text):
    """Pure-ASCII rendering: a hook's stdout is decoded as cp1252 on this setup."""
    return text.encode("ascii", "replace").decode("ascii")


def missing_message(script):
    """The one-line warning for a wired script that is not on disk."""
    name = os.path.basename(script) if script else "<none>"
    where = os.path.dirname(script) if script else "<no script argument>"
    msg = (
        f"[hook-launch] {name} is missing from {where}; skipped (fail-open, dev-env#1146). "
        f"{RECOVERY}"
    )
    if name in GATE_SCRIPTS:
        msg += f" WARNING: {name} is a fail-closed gate and is NOT enforcing until restored."
    return _ascii(msg)


def run_as_main(script, args):
    """Execute `script` in this process exactly as `python <script> <args>` would.

    argv[0] is the script and sys.path[0] is the script's own directory (so its sibling
    `import _hookio` / `_hookutil` still resolve), not this launcher's. A fresh module
    named `__main__` replaces this one in sys.modules for the duration, with the same
    dunders the interpreter gives a script. `compile` on the raw bytes honours a PEP 263
    coding cookie and a UTF-8 BOM, as the interpreter does.

    Deliberately no try/except: SystemExit (the hook's own 0/2 verdict), and an
    uncaught exception's traceback + exit 1, must propagate exactly as before.
    """
    with open(script, "rb") as fh:
        source = fh.read()
    code = compile(source, script, "exec")
    sys.argv = [script] + list(args)
    sys.path[0] = os.path.dirname(os.path.abspath(script))
    module = type(sys)("__main__")
    module.__dict__.update(
        __file__=script, __builtins__=builtins, __spec__=None,
        __loader__=None, __package__=None, __cached__=None,
    )
    launcher_module = sys.modules.get("__main__")  # keep it alive while the hook runs
    sys.modules["__main__"] = module
    try:
        exec(code, module.__dict__)  # noqa: S102 -- running the wired hook IS the job
    finally:
        sys.modules["__main__"] = launcher_module


def main(argv):
    script = argv[1] if len(argv) > 1 else ""
    if not script or not os.path.isfile(script):
        import json  # only on this path: it costs more than the rest of this file

        sys.stdout.write(json.dumps({"systemMessage": missing_message(script)}) + "\n")
        sys.stdout.flush()
        return 0
    run_as_main(script, argv[2:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
