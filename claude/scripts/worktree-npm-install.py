#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook — auto-installs npm packages in Claude-managed
worktrees when node_modules is absent.

Claude-managed worktrees share the git object store but have independent working
directories. node_modules is never present on first use, causing spurious test
failures unrelated to the current change. This hook detects that condition and
runs npm ci (or npm install if no lockfile) before Claude starts working.

The node_modules directory check doubles as the sentinel — once installed, the
hook exits silently for all subsequent prompts in the same worktree.

Pre-install free-space gate (dev-env#364)
------------------------------------------
An unattended install into a near-full C: drive is the corruption vector behind
dev-env#364: npm partially extracts packages, yet the run can still report exit 0,
so a *truncated* node_modules passes as success and surfaces hours later as
misleading downstream errors (a native binary truncated to a fraction of its size,
`MODULE_NOT_FOUND` deep in a load chain). disk-space-check.py (ADR-037) only
samples free space at prompt boundaries, so a long install that runs *between*
prompts is unguarded. This hook therefore gates its own install: when free space
is low it runs a synchronous reclamation ladder (idle-worktree reclaim, then npm
cache clean) and re-measures; only if space is still below a hard floor does it
*refuse* the install and emit a loud advisory — a one-prompt refusal in place of a
silent truncation that costs hours. Reclamation is synchronous (not detached like
disk-space-check) precisely because the install that follows is synchronous — a
detached reclaim would race the install it is meant to protect.

Fires on every user prompt; exits silently when not applicable.

Stdin JSON shape (UserPromptSubmit):
  {
    "hook_event_name": "UserPromptSubmit",
    "cwd": "..."
  }

Exit 0 always — advisory only, never blocks.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import shutil
import subprocess
import sys
from pathlib import Path

import _hookutil

TARGET_DRIVE = "C:/"
SCAN_DIR = "C:/Users/brown/Git"
RECLAIM_SCRIPT = Path(__file__).resolve().parent / "reclaim-worktree-disk.py"

# Free-space thresholds for the pre-install gate. Hardcoded named constants —
# single-machine global config, consistent with disk-space-check.py / ADR-037.
INSTALL_FLOOR_GB = 10.0   # below this: run the reclamation ladder before installing
HARD_FLOOR_GB = 5.0       # below this even after the full ladder: refuse to install


def install_decision(free_gb: float, reclaimed_free_gb: float | None = None) -> str:
    """Pure decision helper for the pre-install free-space gate.

    Returns one of:
      "proceed"       — enough free space; run the install.
      "reclaim-first" — low on space; run the reclamation ladder, then re-call this
                        with the post-reclaim free figure.
      "abort"         — still below the hard floor after reclamation; refuse to
                        install rather than risk a silently-truncated node_modules.
    """
    if free_gb >= INSTALL_FLOOR_GB:
        return "proceed"
    if reclaimed_free_gb is None:
        return "reclaim-first"
    return "proceed" if reclaimed_free_gb >= HARD_FLOOR_GB else "abort"


def _free_gb(path: str) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def _emit(message: str) -> None:
    print(json.dumps({"systemMessage": message}))
    sys.stdout.flush()


def _run_reclaim_ladder(protect_cwd: str) -> None:
    """Synchronously reclaim regenerable disk space before a low-space install.

    Each rung is best-effort: a failure or timeout falls through to the next,
    and the caller re-measures free space afterward to decide whether to proceed.
    `docker system prune` is deliberately NOT a rung — it deletes images/volumes
    the user may want and is not transparently regenerable; the abort advisory
    recommends it as a manual lever instead.
    """
    # Tier 1 — strip node_modules/.turbo from idle eligible worktrees.
    # timeout=300 mirrors the install's own ceiling: Windows rmtree over the many
    # small files of dozens of worktree node_modules trees (the dominant consumer,
    # dev-env#364) is slow, and the reclaim script early-exits the moment it reaches
    # --min-free-gb, so the cap only bites when the disk is genuinely deep underwater
    # — exactly when we want reclamation to keep going rather than abort prematurely.
    exe = sys.executable or "pythonw.exe"
    try:
        subprocess.run(
            [exe, str(RECLAIM_SCRIPT),
             "--scan-dir", SCAN_DIR,
             "--min-free-gb", str(INSTALL_FLOOR_GB),
             "--protect-cwd", protect_cwd],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Tier 2 — npm cache (fully regenerable; safe to clear automatically).
    # The mid-ladder measurement fails open like the gate's other _free_gb calls:
    # a disk_usage error must never suppress the install via the safe-exit guard, so
    # on error we fall through to Tier 2 rather than let the exception propagate.
    try:
        if _free_gb(TARGET_DRIVE) >= HARD_FLOOR_GB:
            return
    except OSError:
        pass
    try:
        subprocess.run(
            "npm cache clean --force",
            shell=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def _gate_install(cmd: str, cwd: str) -> bool:
    """Decide whether to run the install. Returns True to proceed, False to abort.

    Emits an advisory and returns False only when free space is still below the
    hard floor after the reclamation ladder. Any disk-usage error fails open
    (returns True) — the gate must never be the reason an install does not run.
    """
    try:
        free = _free_gb(TARGET_DRIVE)
    except OSError:
        return True  # fail open — never block an install on a measurement error.

    decision = install_decision(free)
    if decision == "proceed":
        return True

    # decision == "reclaim-first": try to free space, then re-decide.
    _emit(
        f"[worktree-npm-install] {free:.1f} GB free on {TARGET_DRIVE} "
        f"(below {INSTALL_FLOOR_GB:.0f} GB) — reclaiming regenerable space before "
        f"`{cmd}` to avoid a silently-truncated install (dev-env#364)…"
    )
    _run_reclaim_ladder(cwd)

    try:
        reclaimed = _free_gb(TARGET_DRIVE)
    except OSError:
        return True

    if install_decision(free, reclaimed) == "proceed":
        return True

    _emit(
        f"[worktree-npm-install] Only {reclaimed:.1f} GB free on {TARGET_DRIVE} "
        f"after reclaiming idle worktrees and the npm cache (below the "
        f"{HARD_FLOOR_GB:.0f} GB floor) — SKIPPING `{cmd}`. An install now risks "
        "silently truncating native binaries (ENOSPC). Free space manually, e.g. "
        "`docker system prune` (~6 GB) or prune stale worktrees, then re-run "
        f"`{cmd}` in this worktree. See docs/REFERENCE.md → Disk-Full (ENOSPC) Recovery."
    )
    return False


def main() -> None:
    _hookutil.record_heartbeat("worktree-npm-install")
    raw = sys.stdin.read().strip()
    cwd = ""
    if raw:
        try:
            cwd = json.loads(raw).get("cwd", "")
        except json.JSONDecodeError:
            pass

    if not cwd:
        sys.exit(0)

    cwd_path = Path(cwd)
    parts = cwd_path.parts

    # Only run in Claude-managed worktrees (.claude/worktrees/<name> path structure).
    # Require .claude and worktrees as consecutive path components.
    try:
        claude_idx = parts.index(".claude")
        if claude_idx + 1 >= len(parts) or parts[claude_idx + 1] != "worktrees":
            sys.exit(0)
    except ValueError:
        sys.exit(0)

    # Only run in npm repos.
    if not (cwd_path / "package.json").exists():
        sys.exit(0)

    # node_modules presence is the sentinel — already installed, nothing to do.
    if (cwd_path / "node_modules").exists():
        sys.exit(0)

    # Choose npm ci (reproducible) when a lockfile exists, otherwise npm install.
    has_lockfile = (cwd_path / "package-lock.json").exists()
    cmd = "npm ci" if has_lockfile else "npm install"

    # Pre-install free-space gate (dev-env#364) — refuse rather than truncate.
    if not _gate_install(cmd, cwd):
        sys.exit(0)

    # Emit a progress message before starting — install can take 30–120 s on large
    # monorepos and the first prompt would otherwise appear to hang without feedback.
    print(json.dumps({
        "systemMessage": (
            f"[worktree-npm-install] node_modules absent — running `{cmd}`. "
            "This may take up to a few minutes on a large repo…"
        )
    }))
    sys.stdout.flush()

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=300,
            shell=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        sys.exit(0)

    if result.returncode == 0:
        print(json.dumps({
            "systemMessage": (
                f"[worktree-npm-install] `{cmd}` succeeded — "
                "packages installed. node_modules is ready."
            )
        }))
    else:
        stderr_excerpt = result.stderr.strip()[:300] if result.stderr else "(no stderr)"
        print(json.dumps({
            "systemMessage": (
                f"[worktree-npm-install] `{cmd}` failed "
                f"(exit {result.returncode}). "
                f"Run it manually before testing.\n{stderr_excerpt}"
            )
        }))

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: an advisory hook must never block a prompt.
        sys.exit(0)
