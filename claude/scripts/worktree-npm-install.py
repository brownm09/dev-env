#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook — auto-installs npm packages in Claude-managed
worktrees when node_modules is absent.

Claude-managed worktrees share the git object store but have independent working
directories. node_modules is never present on first use, causing spurious test
failures unrelated to the current change. This hook detects that condition and
runs npm ci (or npm install if no lockfile) before Claude starts working.

The node_modules directory check doubles as the sentinel — once installed, the
hook exits silently for all subsequent prompts in the same worktree.

Fires on every user prompt; exits silently when not applicable.

Stdin JSON shape (UserPromptSubmit):
  {
    "hook_event_name": "UserPromptSubmit",
    "cwd": "..."
  }

Exit 0 always — advisory only, never blocks.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path


def emit(message: str) -> None:
    print(json.dumps({"systemMessage": message}))
    sys.stdout.flush()


def main_checkout_for_worktree(cwd_path: Path) -> Path | None:
    parts = cwd_path.parts
    try:
        claude_idx = parts.index(".claude")
    except ValueError:
        return None
    if claude_idx + 1 >= len(parts) or parts[claude_idx + 1] != "worktrees":
        return None
    return Path(*parts[:claude_idx])


def copy_if_missing(main_root: Path, cwd_path: Path, relative: str) -> bool:
    source = main_root / "node_modules" / relative
    target = cwd_path / "node_modules" / relative
    if target.exists() or not source.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def repair_known_incomplete_worktree_modules(cwd_path: Path) -> list[str]:
    """Repair known postinstall-skip artifacts from the main checkout when possible."""
    if not (cwd_path / "node_modules").exists():
        return []

    main_root = main_checkout_for_worktree(cwd_path)
    if not main_root or main_root == cwd_path or not (main_root / "node_modules").exists():
        return []

    repaired: list[str] = []
    for relative in (
        "std-env/dist/index.mjs",
        "std-env/dist/index.d.mts",
        "std-env/dist/index.d.ts",
    ):
        if copy_if_missing(main_root, cwd_path, relative):
            repaired.append(relative)

    turbo_relative = "@turbo/windows-64/bin/turbo.exe"
    source_turbo = main_root / "node_modules" / turbo_relative
    target_turbo = cwd_path / "node_modules" / turbo_relative
    if source_turbo.exists() and target_turbo.exists():
        try:
            source_size = source_turbo.stat().st_size
            target_size = target_turbo.stat().st_size
        except OSError:
            source_size = target_size = 0
        if source_size > 1_000_000 and 0 < target_size < 1_000_000:
            shutil.copy2(source_turbo, target_turbo)
            repaired.append(turbo_relative)

    return repaired


def main() -> None:
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

    repaired = repair_known_incomplete_worktree_modules(cwd_path)
    if repaired:
        emit(
            "[worktree-npm-install] repaired incomplete worktree node_modules "
            f"from the main checkout: {', '.join(repaired)}"
        )

    # node_modules presence is the sentinel — already installed, nothing else to do.
    if (cwd_path / "node_modules").exists():
        sys.exit(0)

    # Choose npm ci (reproducible) when a lockfile exists, otherwise npm install.
    has_lockfile = (cwd_path / "package-lock.json").exists()
    cmd = "npm ci" if has_lockfile else "npm install"

    # Emit a progress message before starting — install can take 30–120 s on large
    # monorepos and the first prompt would otherwise appear to hang without feedback.
    emit(
        f"[worktree-npm-install] node_modules absent — running `{cmd}`. "
        "This may take up to a few minutes on a large repo…"
    )

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
        emit(
            f"[worktree-npm-install] `{cmd}` succeeded — "
            "packages installed. node_modules is ready."
        )
    else:
        stderr_excerpt = result.stderr.strip()[:300] if result.stderr else "(no stderr)"
        emit(
            f"[worktree-npm-install] `{cmd}` failed "
            f"(exit {result.returncode}). "
            f"Run it manually before testing.\n{stderr_excerpt}"
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
