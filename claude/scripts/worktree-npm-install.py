#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook — auto-installs npm packages in Claude-managed
worktrees when node_modules is absent, and repairs it when it is present but truncated.

Claude-managed worktrees share the git object store but have independent working
directories. node_modules is never present on first use, causing spurious test
failures unrelated to the current change. This hook detects that condition and
runs npm ci (or npm install if no lockfile) before Claude starts working.

Truncation gate (dev-env#970, ADR-142)
--------------------------------------
node_modules *presence* used to be the whole sentinel, so a tree that exists but is
incomplete passed as healthy — the recurring cross-repo hazard behind dev-env#945
(cover-letter-runtime, 4x), gas-lifting-logbook's ~90% empty shells, and
lifting-logbook#721. Each occurrence was re-diagnosed from scratch because the
failure surfaces as a confusing downstream error (ERR_UNSUPPORTED_DIR_IMPORT,
MODULE_NOT_FOUND deep in a load chain), never as "node_modules is truncated".

The tree is now classified into absent / truncated / fine. The discriminator is
measured, not assumed — see ADR-142 for the 48-tree calibration that rejected both
obvious alternatives (a full node_modules/.package-lock.json audit flagged 12/48,
essentially all false positives, at up to 2.6 s; "the install receipt is missing"
fired on 16/48 healthy trees):

  * PARTIAL — a package directory that is non-empty, is not a symlink/junction, and
    lacks its own package.json. Zero hits across 38 known-good trees; non-zero on
    exactly the suspicious ones, including the @langchain/core truncation dev-env#945
    named. This drives an automatic `npm ci` repair.
  * EMPTY-SHELL RATIO — the share of package dirs that are entirely empty. Benign
    trees reach 21.3% (npm leaves an empty dir for each optional platform dep it
    skips, e.g. @esbuild/linux-x64); genuinely broken trees measured 100%. It has no
    confirmed positive of its own, so per the global Experimental Rigor rule an
    uncalibrated check diagnoses rather than decides: it only ever advises.

The benign classes are excluded by construction rather than by threshold — npm
workspace links are junctions, skipped optional platform deps are *empty* rather than
partial, and a tree with npm's `.<pkg>-XXXXXXXX` staging directories in it has an
install running *right now*, so the audit defers instead of reinstalling over it.

A repair goes through the same _gate_install() free-space ladder as a fresh install
(ADR-045), so a low-disk repair refuses instead of re-truncating. The audit runs once
per session per worktree behind a sentinel: it costs ~0.4 s on a large tree (worst
measured 1.65 s) and a truncation is created at install time, not between prompts.

Once installed and audited clean, the hook exits silently for all subsequent prompts
in the same worktree.

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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import _hookutil

TARGET_DRIVE = "C:/"
SCAN_DIR = "C:/Users/brown/Git"
RECLAIM_SCRIPT = Path(__file__).resolve().parent / "reclaim-worktree-disk.py"

# Truncation audit (dev-env#970). One sentinel per session per worktree — see the
# module docstring for why the audit is not re-run on every prompt.
AUDIT_SENTINEL_PREFIX = "nm_truncation_audit_"

# Share of package dirs that must be entirely empty before the backstop speaks up.
# Measured 2026-08-27 over 48 real trees: benign max 21.3% (optional platform deps
# npm skips), genuinely broken trees 100%. 0.50 sits at a 2.3x margin over the
# benign ceiling — deliberately wide, because this signal only advises (ADR-142).
EMPTY_SHELL_RATIO_FLOOR = 0.50

# os.path.isjunction is Python 3.12+; this machine is 3.12.10. Resolved once, with a
# fallback, so an older interpreter degrades to "not a junction" instead of raising.
_ISJUNCTION = getattr(os.path, "isjunction", None)

# npm's in-flight extraction directories: `.<package>-XXXXXXXX`, renamed into place
# once extraction finishes. Observed live: `.core-YZEumUMX`, `.schematics-cli-SI9Wl1c1`.
_STAGING_SUFFIX_RE = re.compile(r"-[A-Za-z0-9_-]{8}$")

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


def classify_package_dir(is_link: bool, has_package_json: bool, is_empty: bool) -> str:
    """Classify one node_modules package directory. Pure.

    Returns one of:
      "link"        — an npm workspace junction/symlink, not an extracted tarball.
                      Never evidence of truncation: it points at a directory in the
                      repo, so its contents say nothing about the install.
      "ok"          — carries its own package.json, which is what makes npm consider
                      a package installed at all.
      "empty-shell" — no package.json and nothing else either. Ambiguous: this is
                      also exactly what npm leaves behind for an optional dependency
                      whose os/cpu does not match the current platform.
      "partial"     — no package.json but *something* is there. Unambiguous in the
                      measured corpus: nothing legitimate produces this shape.
    """
    if is_link:
        return "link"
    if has_package_json:
        return "ok"
    return "empty-shell" if is_empty else "partial"


def truncation_verdict(checked: int, empty_shells: int, partials: int) -> str:
    """Decide what to do about a scanned node_modules tree. Pure.

    Returns one of:
      "ok"     — no evidence of truncation.
      "repair" — the measured-precise PARTIAL signal fired; reinstall.
      "advise" — a suspicious but uncalibrated shape; say so, change nothing.

    The asymmetry is the point: only PARTIAL has a measured false-positive rate
    (0 across 38 known-good trees) and a confirmed known-bad reference, so only
    PARTIAL is allowed to trigger a destructive reinstall. The two "advise" arms
    are diagnostics — an empty node_modules is unrepresented in the calibration
    corpus entirely, and the empty-shell ratio has no confirmed positive that
    PARTIAL did not already catch.
    """
    if partials > 0:
        return "repair"
    if checked == 0:
        return "advise"
    return "advise" if empty_shells / checked >= EMPTY_SHELL_RATIO_FLOOR else "ok"


def _is_link(entry_path: str, entry: "os.DirEntry | None" = None) -> bool:
    """True for a symlink or a Windows junction — npm's two workspace-link shapes.

    Both checks are needed: a junction is a reparse point but not a symlink, so
    is_symlink()/islink() report False for the npm workspace links this must skip.
    """
    try:
        if entry is not None:
            if entry.is_symlink():
                return True
        elif os.path.islink(entry_path):
            return True
    except OSError:
        return True  # unreadable — treat as a link so it is skipped, never counted
    if _ISJUNCTION is None:
        return False
    try:
        return bool(_ISJUNCTION(entry_path))
    except OSError:
        return True


def _package_dir_shape(entry_path: str) -> "tuple[bool, bool]":
    """(has_package_json, is_empty) from a single scandir of the package directory.

    One pass rather than isfile() + listdir(): the scan visits ~250-1800 package
    dirs, so halving the syscalls per dir is the difference the sentinel is sized
    against. Breaking early is safe — seeing package.json already settles both
    answers.
    """
    is_empty = True
    with os.scandir(entry_path) as it:
        for child in it:
            is_empty = False
            if child.name == "package.json":
                try:
                    if child.is_file():
                        return True, False
                except OSError:
                    return True, False
    return False, is_empty


def is_staging_name(name: str, inside_scope: bool) -> bool:
    """True for one of npm's in-flight extraction directories. Pure.

    npm extracts a package to a sibling `.<name>-XXXXXXXX` directory and renames it
    into place when the extraction completes, so a live install is *full* of
    directories that look exactly like the PARTIAL signal. This was observed live on
    2026-08-27, not theorised: a re-scan of a tree caught 42 of them mid-install.

    Deliberately over-matches rather than under-matches. A false "staging" reading
    suppresses the gate (harmless); a false "partial" reading would run `npm ci` over
    somebody's running install. Inside an @scope directory every child is a package,
    so any dot-prefixed entry there is npm bookkeeping; at the top level, only the
    `-XXXXXXXX` suffix shape qualifies, which leaves .bin/.cache/.vite-temp alone.
    """
    if not name.startswith("."):
        return False
    if inside_scope:
        return True
    return bool(_STAGING_SUFFIX_RE.search(name))


def scan_node_modules(nm_path: Path) -> "tuple[int, int, list[str], int] | None":
    """Scan a node_modules tree's package directories.

    Returns (checked, empty_shells, partial_names, staging), or None when the tree
    itself cannot be read — a measurement failure must fail open, never advise.

    Scope: the top-level tree only. A workspace's own nested node_modules
    (apps/api/node_modules/...) is not scanned — no calibration data covers that
    shape, and a root `npm ci` reinstalls the workspaces anyway (ADR-142).
    """
    try:
        top = list(os.scandir(nm_path))
    except OSError:
        return None

    checked = 0
    empty_shells = 0
    staging = 0
    partials: "list[str]" = []

    def visit(name: str, entry: "os.DirEntry") -> None:
        nonlocal checked, empty_shells
        if _is_link(entry.path, entry):
            return
        try:
            if not entry.is_dir():
                return
        except OSError:
            return
        try:
            has_package_json, is_empty = _package_dir_shape(entry.path)
        except OSError:
            return
        checked += 1
        kind = classify_package_dir(False, has_package_json, is_empty)
        if kind == "empty-shell":
            empty_shells += 1
        elif kind == "partial":
            partials.append(name)

    for entry in top:
        # .bin, .cache, .prisma, .vite, .package-lock.json — npm's own bookkeeping,
        # never packages. (Observed set across the 48-tree corpus.)
        if entry.name.startswith("."):
            if is_staging_name(entry.name, inside_scope=False):
                staging += 1
            continue
        if entry.name.startswith("@"):
            # A scope directory is not itself a package; its children are.
            if _is_link(entry.path, entry):
                continue
            try:
                scoped = list(os.scandir(entry.path))
            except OSError:
                continue
            for child in scoped:
                if is_staging_name(child.name, inside_scope=True):
                    staging += 1
                    continue
                visit(entry.name + "/" + child.name, child)
        else:
            visit(entry.name, entry)

    return checked, empty_shells, partials, staging


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


def _run_install(cmd: str, cwd_path: Path, opening: str) -> None:
    """Run an install/repair command and report the outcome. Never raises.

    Shared by the absent-tree install and the truncation repair so the two report
    identically — the only difference a user should see is why it ran.
    """
    # Emit a progress message before starting — install can take 30–120 s on large
    # monorepos and the first prompt would otherwise appear to hang without feedback.
    _emit(opening)

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
        return

    if result.returncode == 0:
        _emit(
            f"[worktree-npm-install] `{cmd}` succeeded — "
            "packages installed. node_modules is ready."
        )
    else:
        stderr_excerpt = result.stderr.strip()[:300] if result.stderr else "(no stderr)"
        _emit(
            f"[worktree-npm-install] `{cmd}` failed "
            f"(exit {result.returncode}). "
            f"Run it manually before testing.\n{stderr_excerpt}"
        )


def _audit_sentinel_key(cwd: str, session_id: str) -> str:
    """Sentinel key for one worktree in one session.

    The path is hashed rather than sanitized so the key is a fixed length and free
    of path separators; collisions across worktrees are not a concern the way they
    would be for a security boundary.
    """
    digest = hashlib.sha1(cwd.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{session_id or 'nosession'}_{digest}"


def _audit_existing_tree(cwd_path: Path, session_id: str) -> None:
    """Classify an existing node_modules, then repair it, flag it, or say nothing.

    Fails open at every step: an unreadable tree, a sentinel that cannot be written,
    or a low-disk refusal all leave the tree exactly as found.
    """
    _hookutil.cleanup_stale_sentinels(AUDIT_SENTINEL_PREFIX)
    marker = _hookutil.sentinel_path(
        AUDIT_SENTINEL_PREFIX, _audit_sentinel_key(str(cwd_path), session_id)
    )
    if marker.exists():
        return

    scanned = scan_node_modules(cwd_path / "node_modules")
    if scanned is None:
        return  # could not measure — never advise on a measurement failure
    checked, empty_shells, partials, staging = scanned

    if staging:
        # Somebody's `npm install` is extracting into this tree right now. Half-built
        # packages are indistinguishable from truncated ones, so defer rather than
        # run `npm ci` over a live install — and deliberately leave the sentinel
        # unset so the next prompt re-audits once the install has landed.
        return

    try:
        # Written *before* acting, not after: a repair that fails must not retry on
        # every prompt for the rest of the session.
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
    except OSError:
        pass

    verdict = truncation_verdict(checked, empty_shells, len(partials))
    if verdict == "ok":
        return

    if verdict == "advise":
        if checked == 0:
            detail = "it contains no package directories at all"
        else:
            pct = empty_shells / checked * 100
            detail = (
                f"{empty_shells} of {checked} package directories are empty ({pct:.0f}%)"
            )
        _emit(
            f"[worktree-npm-install] node_modules looks incomplete — {detail}. "
            "This shape is not distinctive enough to repair automatically (ADR-142), "
            "so nothing was changed. If tests fail on a missing or malformed module, "
            "run `npm ci` in this worktree. "
            "See docs/REFERENCE.md → Disk-Full (ENOSPC) Recovery."
        )
        return

    # verdict == "repair" — the PARTIAL signal, the one with a measured
    # false-positive rate and a confirmed known-bad reference (dev-env#945).
    shown = ", ".join(partials[:5])
    if len(partials) > 5:
        shown += f", and {len(partials) - 5} more"

    if not (cwd_path / "package-lock.json").exists():
        _emit(
            f"[worktree-npm-install] node_modules is truncated — {len(partials)} "
            f"package(s) present but missing their own package.json ({shown}). "
            "No package-lock.json here, so the clean-slate `npm ci` repair is not "
            "available — run `npm install` in this worktree before testing."
        )
        return

    # Repair through the same free-space ladder a fresh install uses (ADR-045):
    # reinstalling onto a near-full disk is how the truncation got here.
    if not _gate_install("npm ci", str(cwd_path)):
        return  # the gate emitted its own refusal advisory

    _run_install(
        "npm ci",
        cwd_path,
        f"[worktree-npm-install] node_modules is truncated — {len(partials)} "
        f"package(s) present but missing their own package.json ({shown}). "
        "Repairing with `npm ci`. This may take up to a few minutes on a large repo…",
    )


def main() -> None:
    _hookutil.record_heartbeat("worktree-npm-install")
    raw = sys.stdin.read().strip()
    cwd = ""
    session_id = ""
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            cwd = payload.get("cwd", "") or ""
            session_id = payload.get("session_id", "") or ""

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

    # node_modules present is necessary but not sufficient — a tree that exists can
    # still be truncated (dev-env#970, ADR-142). Audit it, then stop either way.
    if (cwd_path / "node_modules").exists():
        _audit_existing_tree(cwd_path, session_id)
        sys.exit(0)

    # Choose npm ci (reproducible) when a lockfile exists, otherwise npm install.
    has_lockfile = (cwd_path / "package-lock.json").exists()
    cmd = "npm ci" if has_lockfile else "npm install"

    # Pre-install free-space gate (dev-env#364) — refuse rather than truncate.
    if not _gate_install(cmd, cwd):
        sys.exit(0)

    _run_install(
        cmd,
        cwd_path,
        f"[worktree-npm-install] node_modules absent — running `{cmd}`. "
        "This may take up to a few minutes on a large repo…",
    )

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: an advisory hook must never block a prompt.
        sys.exit(0)
