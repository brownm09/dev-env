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
  * script missing -> exit 0, plus one ASCII `{"systemMessage": ...}` line (reaches the
    USER on every hook event, per the _hookout table), throttled per script; see
    "Throttled warnings" below. That holds for the two fail-closed
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
This file runs in front of every hook on every tool call, so at module level it imports
only `builtins`, `os` and `sys`, all of which the interpreter has already loaded before
any script starts. `json` and `time` are imported on the missing-script path alone.
`runpy` is avoided entirely. Together with the `pkgutil` it pulls in, it showed about
15 ms of import time under `-X importtime`. Measured wall-clock, a runpy draft ran about
44 ms per hook over a direct run, where the compile + exec below adds about 2 ms.
test_hook_launch.py forbids those imports structurally, rather than by timing.

Throttled warnings
------------------
A stale tree can leave a dozen or more hooks missing at once, and each fires on every
tool call. So a non-gate script is announced at most once per THROTTLE_SECONDS, using
a best-effort timestamp file per script under STATE_DIR (failing open: a state file
that cannot be read or written means "announce"). A missing fail-closed gate is
announced every time, because that warning is the one that must not get buried.
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

THROTTLE_SECONDS = 600

# Where the per-script "last announced" timestamps live. HOOK_LAUNCH_STATE_DIR is the
# test-only override; nothing else sets it.
STATE_DIR = os.environ.get("HOOK_LAUNCH_STATE_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "scratch", "hook-launch"
)


def _ascii(text):
    """Pure-ASCII rendering: a hook's stdout is decoded as cp1252 on this setup."""
    return text.encode("ascii", "replace").decode("ascii")


def canonical_repo(script):
    """The dev-env checkout the script's directory resolves to, as an absolute path.

    The wired directory is the ~/.claude/scripts junction, so its realpath is the
    canonical's claude/scripts and the repo is two levels up. Falls back to
    ~/Git/dev-env, expanded, when the directory does not look like that. The result is
    absolute and uses forward slashes, so it works pasted into Git Bash or PowerShell
    (neither git nor PowerShell 5.1 expands a `~` passed to a native program).
    """
    try:
        scripts_dir = os.path.realpath(os.path.dirname(os.path.abspath(script)))
        if os.path.basename(scripts_dir) == "scripts" and os.path.basename(
            os.path.dirname(scripts_dir)
        ) == "claude":
            return os.path.dirname(os.path.dirname(scripts_dir)).replace("\\", "/")
    except (OSError, ValueError):
        pass
    return os.path.join(os.path.expanduser("~"), "Git", "dev-env").replace("\\", "/")


def missing_message(script):
    """The one-line warning for a wired script that is not on disk."""
    name = os.path.basename(script) if script else "<none>"
    where = os.path.dirname(script) if script else "<no script argument>"
    repo = canonical_repo(script) if script else canonical_repo(os.path.join(".", "x"))
    # `;` separates commands in both Git Bash and Windows PowerShell 5.1; `&&` does
    # not parse in the latter, which is the Terminal panel's default shell here.
    msg = (
        f"[hook-launch] {name} is missing from {where}; skipped (fail-open, dev-env#1146). "
        f"The dev-env canonical is probably behind its hook wiring. Fix: "
        f"git -C {repo} checkout main; git -C {repo} pull"
    )
    if name in GATE_SCRIPTS:
        msg += f" WARNING: {name} is a fail-closed gate and is NOT enforcing until restored."
    return _ascii(msg)


def should_announce(name):
    """True when this missing script's warning should be printed now (see docstring).

    Gates always announce. Others announce when their timestamp file is absent, older
    than THROTTLE_SECONDS, or unreadable; announcing refreshes it. Every filesystem
    error fails open (announce), since a silenced warning is the worse failure.
    """
    if name in GATE_SCRIPTS:
        return True
    import time  # missing-script path only

    stamp = os.path.join(STATE_DIR, name + ".ts")
    now = time.time()
    try:
        if now - os.stat(stamp).st_mtime < THROTTLE_SECONDS:
            return False
    except (OSError, ValueError):
        pass
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(stamp, "w", encoding="ascii") as fh:
            fh.write(f"{now:.0f}\n")
    except (OSError, ValueError):
        pass
    return True


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
        # Absolute, as Python 3.9+ makes a directly-run script's __file__.
        __file__=os.path.abspath(script), __builtins__=builtins, __spec__=None,
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
        name = os.path.basename(script) if script else "<none>"
        if should_announce(name):
            import json  # only on this path: it costs more than the rest of this file

            sys.stdout.write(json.dumps({"systemMessage": missing_message(script)}) + "\n")
            sys.stdout.flush()
        return 0
    run_as_main(script, argv[2:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
