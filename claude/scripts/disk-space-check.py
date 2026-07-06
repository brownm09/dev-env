#!/usr/bin/env python3
"""Claude Code UserPromptSubmit + PreToolUse(Bash) hook — free-space safety
net for the C: drive.

Background: Claude-managed worktrees each carry a full node_modules; dozens of
stale worktrees can fill C: to saturation between the weekly prune runs
(dev-env#306). reclaim-worktree-disk.py strips those regenerable artifacts, but
a weekly/6-hourly cadence can still be outrun by a burst of worktree creation.
This hook is the between-runs safety net: it watches free space and, when
space gets low, triggers reclamation before the disk saturates.

Registered under both `UserPromptSubmit` and `PreToolUse(Bash)` (dev-env#592,
ADR-087): the original UserPromptSubmit-only wiring re-checks free space once
per user prompt, so disk exhaustion occurring *within* a single long agentic
turn (many Bash calls with no new prompt in between) could outrun it — a
structural gap ADR-085's Context section named as a candidate fast-follow.
`shutil.disk_usage()` is a cheap syscall (not a subprocess spawn), so running
it before every Bash call is affordable. `main()` was already agnostic to
which hook event invoked it — it only reads `session_id`/`cwd` from stdin and
ignores `hook_event_name`/`tool_name`/`tool_input` — so the same script is
reused unmodified as both hook entries rather than splitting into two files
(mirrors the existing `awake-blocker.py` precedent of one script registered
under multiple hook events).

Behavior (advisory only — never blocks a prompt or tool call; exit 0 always
per ADR-027):
  - free space < WARN_GB: emit a one-time systemMessage warning (no action).
  - free space < ACT_GB:  spawn reclaim-worktree-disk.py DETACHED (so the heavy
                          delete never blocks the prompt/tool call) and emit a
                          one-time systemMessage that reclamation has started.

Each band fires at most once per session, gated by a session_id-keyed marker file
in scratch/ (ADR-027 per-session-state convention) so a sustained low-space
condition does not re-warn or re-spawn on every prompt or Bash call — and the
same gate is shared by both hook entries, so whichever fires first for a given
session silently covers the other.

The detached spawn uses sys.executable (pythonw.exe under the `pyw -3` hook
invocation) rather than the `py` launcher — spawning via `py.exe` would allocate
a fresh console for the grandchild (dev-env#300; enforced by
tests/test_pyw_stdio.py::test_no_hook_spawns_python_via_py_launcher).

Stdin JSON shape (UserPromptSubmit): {"hook_event_name", "session_id", "cwd"}
Stdin JSON shape (PreToolUse/Bash): {"hook_event_name", "tool_name",
"tool_input", "session_id", "cwd"} — only "session_id" and "cwd" are read;
the rest is ignored, which is what makes this script safe to reuse as-is.

Exit 0 always — advisory; any exception is swallowed so a bug never blocks a
prompt or tool call.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import subprocess
import sys
from pathlib import Path

SCRATCH = Path("C:/Users/brown/.claude/scratch")
# Drive whose free space gates reclamation, and the tree scanned for idle worktrees.
TARGET_DRIVE = "C:/"
SCAN_DIR = "C:/Users/brown/Git"
RECLAIM_SCRIPT = Path(__file__).resolve().parent / "reclaim-worktree-disk.py"

# Thresholds. Hardcoded constants (single-machine global config); promote to
# .claude/hook-config.json only if tuning-without-code-change becomes necessary.
WARN_GB = 20.0   # below this: warn the user
ACT_GB = 10.0    # below this: auto-reclaim regenerable worktree artifacts


def classify_free_space(free_gb: float, warn_gb: float, act_gb: float) -> str:
    """Return "act", "warn", or "ok" for a free-space reading.

    Boundaries are inclusive on the healthier side, preserving the exact
    behavior of the inline if/elif this was extracted from: a reading exactly
    equal to a threshold has not yet crossed into that band — only strictly
    below does. So `free_gb == warn_gb` is "ok" and `free_gb == act_gb` is
    "warn", never "act".
    """
    if free_gb < act_gb:
        return "act"
    if free_gb < warn_gb:
        return "warn"
    return "ok"


def _free_gb(path: str) -> float:
    import shutil
    return shutil.disk_usage(path).free / (1024 ** 3)


def _marker_path(session_id: str, band: str) -> Path:
    safe = session_id or "unknown"
    return SCRATCH / f"disk_space_check_{safe}_{band}.flag"


def _already_fired(session_id: str, band: str) -> bool:
    return _marker_path(session_id, band).exists()


def _mark_fired(session_id: str, band: str) -> None:
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        _marker_path(session_id, band).touch()
    except OSError:
        pass


def _emit(message: str) -> None:
    print(json.dumps({"systemMessage": message}))
    sys.stdout.flush()


def _spawn_reclaim(protect_cwd: str) -> bool:
    """Spawn reclaim-worktree-disk.py detached. Returns True if spawned."""
    exe = sys.executable or "pythonw.exe"
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    args = [
        exe,
        str(RECLAIM_SCRIPT),
        "--scan-dir", SCAN_DIR,
        "--min-free-gb", str(ACT_GB),
    ]
    if protect_cwd:
        args += ["--protect-cwd", protect_cwd]
    try:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        return True
    except OSError:
        return False


def main() -> None:
    raw = sys.stdin.read().strip()
    session_id = ""
    cwd = ""
    if raw:
        try:
            payload = json.loads(raw)
            session_id = payload.get("session_id", "") or ""
            cwd = payload.get("cwd", "") or ""
        except json.JSONDecodeError:
            pass

    try:
        free = _free_gb(TARGET_DRIVE)
    except OSError:
        sys.exit(0)

    band = classify_free_space(free, WARN_GB, ACT_GB)

    if band == "act":
        if not _already_fired(session_id, "act"):
            spawned = _spawn_reclaim(cwd)
            if spawned:
                _emit(
                    f"[disk-space-check] {free:.1f} GB free on {TARGET_DRIVE} "
                    f"(below {ACT_GB:.0f} GB) — started background reclamation of "
                    "node_modules/.turbo from idle worktrees. They reinstall on next "
                    "use. If space stays low, prune worktrees manually."
                )
            else:
                _emit(
                    f"[disk-space-check] {free:.1f} GB free on {TARGET_DRIVE} "
                    f"(below {ACT_GB:.0f} GB) — could not start background reclamation. "
                    "Run `py -3 ~/.claude/scripts/reclaim-worktree-disk.py "
                    "--scan-dir C:/Users/brown/Git` manually."
                )
            _mark_fired(session_id, "act")
    elif band == "warn":
        if not _already_fired(session_id, "warn"):
            _emit(
                f"[disk-space-check] {free:.1f} GB free on {TARGET_DRIVE} "
                f"(below {WARN_GB:.0f} GB). Auto-reclamation triggers under "
                f"{ACT_GB:.0f} GB; consider pruning stale worktrees soon."
            )
            _mark_fired(session_id, "warn")

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: an advisory hook must never block a prompt.
        sys.exit(0)
