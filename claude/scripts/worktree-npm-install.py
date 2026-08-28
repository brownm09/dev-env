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
  * EMPTY-SHELL RATIO — the share of package dirs that are entirely empty. The worst
    confirmed-benign tree reaches 15.0% (npm leaves an empty dir for each optional
    platform dep it skips, e.g. @esbuild/linux-x64); genuinely broken trees measured
    100%. It has no confirmed positive of its own, so per the global Experimental
    Rigor rule an uncalibrated check diagnoses rather than decides: it only ever
    advises.

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
    "cwd": "...",
    "session_id": "..."   # the truncation audit's once-per-session key depends on it
  }

Exit 0 always — advisory only, never blocks.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import _hookutil

TARGET_DRIVE = "C:/"
SCAN_DIR = "C:/Users/brown/Git"
RECLAIM_SCRIPT = Path(__file__).resolve().parent / "reclaim-worktree-disk.py"

# Truncation audit (dev-env#970). One sentinel per session per worktree — see the
# module docstring for why the audit is not re-run on every prompt.
AUDIT_SENTINEL_PREFIX = "nm_truncation_audit_"

# Deferral marker, keyed by worktree only (not session): written when an install is
# found in flight, and re-checked before the scan. Without it a worktree that stays
# in the deferred state pays a full scan on *every* prompt forever, which is exactly
# the per-prompt cost the audit sentinel exists to avoid.
DEFER_MARKER_PREFIX = "nm_truncation_defer_"
DEFER_RECHECK_SECONDS = 600

# The advise arms describe a shape the user cannot act on, so they are emitted once
# per worktree rather than once per session — otherwise a benign-but-high empty-shell
# ratio would reprint identically at the start of every session, forever.
ADVISE_MARKER_PREFIX = "nm_truncation_advised_"

# Mutual exclusion around npm in one worktree. `npm ci` removes node_modules before
# installing, so a repair opens a window where the tree is absent; without this lock
# a prompt landing in that window takes main()'s absent-tree branch and starts a
# *second* concurrent install in the same directory — one of the very root causes
# ADR-142 lists for the truncation this hook exists to repair. Stale locks are
# reclaimed by age, since a hook killed mid-install cannot release its own.
INSTALL_LOCK_PREFIX = "nm_install_lock_"
INSTALL_LOCK_STALE_SECONDS = 900

# Share of package dirs that must be entirely empty before the backstop speaks up.
# Measured 2026-08-27 over 48 real trees: the worst *confirmed-benign* tree
# (confident-mcnulty-ad4e52 — 50 of 334 empty, zero partials, all optional platform
# deps npm skipped) sits at 15.0%; genuinely broken trees measured 100%. 0.50 keeps
# a 3.3x margin over that ceiling — deliberately wide, because this signal only
# advises (ADR-142). EMPTY_SHELL_BENIGN_CEILING is the measurement the floor is
# justified against; the test asserts the margin so the two cannot drift apart.
EMPTY_SHELL_BENIGN_CEILING = 0.150
EMPTY_SHELL_RATIO_FLOOR = 0.50

# os.path.isjunction is Python 3.12+; this machine is 3.12.10. Resolved once, with a
# fallback, so an older interpreter degrades to "not a junction" instead of raising.
_ISJUNCTION = getattr(os.path, "isjunction", None)

# npm's own bookkeeping entries inside node_modules. Everything else dot-prefixed is
# treated as an in-flight extraction directory — see is_staging_name() for why the
# allowlist runs this way round rather than matching npm's staging-name shape.
BENIGN_DOT_ENTRIES = frozenset({
    ".bin", ".cache", ".package-lock.json", ".prisma", ".vite", ".vite-temp",
    ".modules.yaml", ".yarn-integrity", ".yarn-state.yml", ".DS_Store",
})

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


def truncation_verdict(
    checked: int, empty_shells: int, partials: int, staging: int = 0
) -> str:
    """Decide what to do about a scanned node_modules tree. Pure.

    Returns one of:
      "defer"  — an install is extracting into this tree right now; touch nothing.
      "repair" — the measured-precise PARTIAL signal fired; reinstall.
      "advise" — a suspicious but uncalibrated shape; say so, change nothing.
      "ok"     — no evidence of truncation.

    `staging` outranks everything, including PARTIAL: a half-written package is
    indistinguishable from a truncated one, so a live install must never be read as
    damage and reinstalled over. This precedence lives here, in the pure layer,
    rather than as a short-circuit in the caller — it is the single most
    consequential branch in the audit and it is the one most worth a test.

    Below that, the asymmetry is the point: only PARTIAL has a measured
    false-positive rate (0 across 38 known-good trees) and a confirmed known-bad
    reference, so only PARTIAL may trigger a destructive reinstall. The two "advise"
    arms are diagnostics — an empty node_modules is unrepresented in the calibration
    corpus entirely, and the empty-shell ratio has no confirmed positive that PARTIAL
    did not already catch.
    """
    if staging > 0:
        return "defer"
    if partials > 0:
        return "repair"
    if checked == 0:
        return "advise"
    return "advise" if empty_shells / checked >= EMPTY_SHELL_RATIO_FLOOR else "ok"


def _is_link(entry: "os.DirEntry") -> bool:
    """True for a symlink or a Windows junction — npm's two workspace-link shapes.

    Both checks are needed: a junction is a reparse point but not a symlink, so
    is_symlink()/islink() report False for the npm workspace links this must skip.
    """
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True  # unreadable — treat as a link so it is skipped, never counted
    if _ISJUNCTION is None:
        return False
    try:
        return bool(_ISJUNCTION(entry.path))
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


def is_staging_name(name: str) -> bool:
    """True for a dot-entry that is not known npm bookkeeping. Pure.

    npm extracts a package to a sibling `.<name>-XXXXXXXX` directory and renames it
    into place when the extraction completes, so a live install is *full* of
    directories that look exactly like the PARTIAL signal. This was observed live on
    2026-08-27, not theorised: a re-scan of a tree caught 42 of them mid-install.

    Matching npm's staging shape directly (`-[A-Za-z0-9_-]{8}$`) was the first
    attempt and is the wrong way round: that pattern was generalised from three
    samples on one npm version, so any change to the suffix length or alphabet would
    silently reclassify every in-flight extraction as PARTIAL — the one input that
    triggers the destructive arm. Inverting to an allowlist makes the failure
    direction safe: an unrecognised dot-entry defers (harmless), and only names
    npm is *known* to use for bookkeeping are ignored.

    Callers must still confirm the entry is a directory — a stray dot-*file* such as
    a `.DS_Store` is neither bookkeeping nor an extraction in progress, and counting
    one as staging would suppress the whole audit.
    """
    return name.startswith(".") and name not in BENIGN_DOT_ENTRIES


def scan_node_modules(nm_path: Path) -> "tuple[int, int, list[str], int] | None":
    """Scan a node_modules tree's package directories.

    Returns (checked, empty_shells, partial_names, staging), or None when the tree
    itself cannot be read — a measurement failure must fail open, never advise.

    Scope: the top-level tree only. A workspace's own nested node_modules
    (apps/api/node_modules/...) is not scanned — no calibration data covers that
    shape, and a root `npm ci` reinstalls the workspaces anyway (ADR-142).
    """
    checked = 0
    empty_shells = 0
    staging = 0
    partials: "list[str]" = []

    def is_dir(entry: "os.DirEntry") -> bool:
        try:
            return entry.is_dir()
        except OSError:
            return False

    def count_dot_entry(entry: "os.DirEntry") -> None:
        """Tally a dot-prefixed entry as staging, if it can be one at all.

        The is_dir() test is load-bearing, not defensive: a stray dot-*file* such as
        a .DS_Store is neither bookkeeping nor an extraction in progress, and one
        counted as staging would defer — and so suppress — the entire audit.
        """
        nonlocal staging
        if is_staging_name(entry.name) and is_dir(entry):
            staging += 1

    def visit(name: str, entry: "os.DirEntry") -> None:
        nonlocal checked, empty_shells
        # The link decision is routed through classify_package_dir rather than
        # short-circuited here, so the classifier's "link" arm is the one production
        # actually takes and a test of it means something.
        if classify_package_dir(_is_link(entry), False, False) == "link":
            return
        if not is_dir(entry):
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

    try:
        with os.scandir(nm_path) as top:
            for entry in top:
                if entry.name.startswith("."):
                    count_dot_entry(entry)
                    continue
                if entry.name.startswith("@"):
                    # A scope directory is not itself a package; its children are.
                    if _is_link(entry):
                        continue
                    try:
                        with os.scandir(entry.path) as scoped:
                            for child in scoped:
                                if child.name.startswith("."):
                                    count_dot_entry(child)
                                    continue
                                visit(entry.name + "/" + child.name, child)
                    except OSError:
                        continue
                else:
                    visit(entry.name, entry)
    except OSError:
        return None

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


def _worktree_digest(cwd: str) -> str:
    """Short stable key for one worktree path.

    Hashed rather than sanitized so the key is a fixed length and free of path
    separators; collisions are not a security boundary here.
    """
    return hashlib.sha1(cwd.encode("utf-8", "replace")).hexdigest()[:12]


def _scratch_marker(prefix: str, key: str, scratch: "Path | None" = None) -> Path:
    root = scratch if scratch is not None else _hookutil.SCRATCH
    return root / f"{prefix}{key}.flag"


def _marker_age_seconds(path: Path) -> "float | None":
    """Age of a marker file, or None when it is absent or unreadable."""
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def _write_marker(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return True
    except OSError:
        return False


def acquire_install_lock(cwd_path: Path, scratch: "Path | None" = None) -> bool:
    """Claim the right to run npm in this worktree. True when claimed.

    `npm ci` deletes node_modules before installing, so between the delete and the
    rebuild the tree looks *absent* — and main()'s absent-tree branch would happily
    start a second install in the same directory. This lock is what makes the two
    paths mutually exclusive.

    A hook killed mid-install (the wired hook budget is well under a large install)
    cannot release its own lock, so a lock older than INSTALL_LOCK_STALE_SECONDS is
    reclaimed rather than honoured forever. Any error claiming the lock returns
    False: unable to coordinate means unable to install.
    """
    lock = _scratch_marker(INSTALL_LOCK_PREFIX, _worktree_digest(str(cwd_path)), scratch)
    age = _marker_age_seconds(lock)
    if age is not None and age <= INSTALL_LOCK_STALE_SECONDS:
        return False
    return _write_marker(lock)


def release_install_lock(cwd_path: Path, scratch: "Path | None" = None) -> None:
    try:
        _scratch_marker(
            INSTALL_LOCK_PREFIX, _worktree_digest(str(cwd_path)), scratch
        ).unlink()
    except OSError:
        pass


def _run_install(cmd: str, cwd_path: Path, opening: str) -> None:
    """Run an install/repair command and report the outcome. Never raises.

    Shared by the absent-tree install and the truncation repair so the two report
    identically — the only difference a user should see is why it ran.
    """
    if not acquire_install_lock(cwd_path):
        # Another install is already running in this worktree — including one this
        # hook started in an earlier prompt and was killed before it finished.
        return

    try:
        # Emit a progress message before starting — install can take 30–120 s on
        # large monorepos and the first prompt would otherwise appear to hang
        # without feedback.
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
            stderr_excerpt = (
                result.stderr.strip()[:300] if result.stderr else "(no stderr)"
            )
            _emit(
                f"[worktree-npm-install] `{cmd}` failed "
                f"(exit {result.returncode}). "
                f"Run it manually before testing.\n{stderr_excerpt}"
            )
    finally:
        release_install_lock(cwd_path)


def audit_sentinel_key(cwd: str, session_id: str) -> str:
    """Sentinel key for one worktree in one session.

    The path is hashed rather than sanitized so the key is a fixed length and free
    of path separators; collisions across worktrees are not a concern the way they
    would be for a security boundary.

    The `session_id`-absent fallback carries the local date rather than a bare
    constant. A bare constant would make every session-id-less session share one
    key, so the 30-day `cleanup_stale_sentinels` sweep would turn "once per session"
    into "once per month" for that worktree — and a single failed repair would
    silently disable the gate for the whole window. A *unique* fallback has the
    opposite failure: the audit would re-scan on every prompt. The date bounds the
    blind window to a day without reintroducing the per-prompt cost.
    """
    session = session_id or "nosession-" + time.strftime("%Y%m%d")
    return f"{session}_{_worktree_digest(cwd)}"


def _node_modules_is_disposable(cwd_path: Path) -> bool:
    """True when git ignores node_modules — i.e. it is regenerable, not vendored.

    The whole premise that an automatic `npm ci` is safe (ADR-016/ADR-037) is that a
    worktree's node_modules is disposable. A repo that deliberately commits its
    dependency tree breaks that premise, and there the PARTIAL signal is not even
    evidence of damage — a vendored package may legitimately ship without its own
    package.json. Conservative on any error: no answer means no repair.
    """
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "node_modules"],
            cwd=str(cwd_path),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _audit_existing_tree(cwd_path: Path, session_id: str) -> None:
    """Classify an existing node_modules, then repair it, flag it, or say nothing.

    Fails open at every step: an unreadable tree, a sentinel that cannot be written,
    or a low-disk refusal all leave the tree exactly as found.
    """
    marker = _hookutil.sentinel_path(
        AUDIT_SENTINEL_PREFIX, audit_sentinel_key(str(cwd_path), session_id)
    )
    if marker.exists():
        return

    # A worktree left in the deferred state must not pay a full scan on every
    # prompt — that is the very cost the audit sentinel exists to bound, and the
    # defer path cannot use that sentinel without also suppressing the re-audit it
    # is waiting for. This marker is keyed by worktree alone and re-checked by age.
    defer_marker = _scratch_marker(DEFER_MARKER_PREFIX, _worktree_digest(str(cwd_path)))
    defer_age = _marker_age_seconds(defer_marker)
    if defer_age is not None and defer_age <= DEFER_RECHECK_SECONDS:
        return

    # Swept only once the audit is actually going to run. Above the early returns it
    # would glob the whole scratch directory on every prompt for the life of every
    # worktree — measured at 20–24 ms against ~8,500 files — which is a real
    # regression against ADR-016's "three Path.exists() checks per prompt".
    _hookutil.cleanup_stale_sentinels(AUDIT_SENTINEL_PREFIX)
    _hookutil.cleanup_stale_sentinels(DEFER_MARKER_PREFIX)

    scanned = scan_node_modules(cwd_path / "node_modules")
    if scanned is None:
        return  # could not measure — never advise on a measurement failure
    checked, empty_shells, partials, staging = scanned

    verdict = truncation_verdict(checked, empty_shells, len(partials), staging)

    if verdict == "defer":
        # Somebody's `npm install` is extracting into this tree right now. Half-built
        # packages are indistinguishable from truncated ones, so defer rather than
        # run `npm ci` over a live install. The audit sentinel stays unset so the
        # next prompt re-audits once the install lands; the defer marker bounds how
        # often that costs a scan.
        _write_marker(defer_marker)
        return

    try:
        defer_marker.unlink()
    except OSError:
        pass

    if verdict == "ok":
        return

    # Written *before* acting, not after: a repair that fails must not retry on every
    # prompt for the rest of the session. If it cannot be written we do not act — for
    # a destructive repair, "fail open" has to mean "do not repair", not "repair
    # without a record". The write most plausibly fails on a full disk, which is
    # exactly when an unbounded repair loop would do the most damage.
    if not _write_marker(marker):
        return

    if verdict == "advise":
        # These arms cannot converge on their own: a benign-but-high empty-shell
        # ratio is not something the user can act on, so re-emitting every session
        # forever would be pure noise. Once per worktree (the 30-day sentinel sweep
        # is the outer bound) is enough to surface it.
        advised = _scratch_marker(
            ADVISE_MARKER_PREFIX, _worktree_digest(str(cwd_path))
        )
        if advised.exists():
            return
        _write_marker(advised)

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

    if not _node_modules_is_disposable(cwd_path):
        _emit(
            f"[worktree-npm-install] node_modules looks truncated — {len(partials)} "
            f"package(s) present but missing their own package.json ({shown}) — but "
            "git does not ignore node_modules here, so this tree may be deliberately "
            "vendored. Not repairing automatically: `npm ci` would delete it. "
            "Run `npm ci` yourself if the tree really is disposable."
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
