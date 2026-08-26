#!/usr/bin/env python3
"""Shared module: apply dev-env's shared Claude Code settings into the live user
settings file, without ever making the repo file the app's write target.

Why this exists (dev-env#1049, ADR-139)
---------------------------------------
`~/.claude/settings.json` used to be a *symlink* into this repo's working tree
(`claude/settings.json`). That made one file two things at once:

  1. the version-controlled source of the hooks and permissions dev-env ships, and
  2. the live file the Claude Code app itself writes -- `/config` theme changes,
     `tui`, notification flags, and the `autoMode` environment scan.

Because the app keeps rewriting (2), the tracked file at (1) went dirty on its own,
and `dev-env-sync.py`'s fast-forward pull then failed *forever*: git refuses to
overwrite a dirty tracked file. The canonical drifted behind `origin/main`, and
since `~/.claude/{scripts,skills,hooks}` are junctions into that same checkout, the
machine served STALE tooling -- observed live on 2026-08-25, when the `/review`
skill still prescribed a heredoc that ADR-138 had already banned.

"Commit or stash it" (the dev-env#697 / #795 remedy, for session-authored content)
treats the symptom: the app re-dirties the file on the next settings change. Worse,
one such app rewrite silently DROPPED two committed `permissions.allow` entries --
`Bash(node -e *)` and `Bash(rm -f C:/Users/brown/.claude/scratch/*)`, which back the
documented "jq is NOT available" recipe in claude/CLAUDE.md -- so simply committing
the working tree would have removed them for every machine.

Why not `settings.local.json`
-----------------------------
The obvious fix -- move app-written keys into `settings.local.json` -- does not
work, and this was verified against the official docs and the shipped 2.1.237
binary rather than assumed. `settings.local.json` is **project-scoped only**
(`.claude/settings.local.json`); there is no `~/.claude/settings.local.json`. The
binary's own internal doc string enumerates the device-level sources exhaustively:
"user settings, the checkout's settings.local.json, or a --settings file". And the
docs say the app writes user-scope prefs to `~/.claude/settings.json` *by name*.
So there is nowhere at user scope to move `autoMode`/`theme`/`tui` to.

The design: owned / seed / machine-local
----------------------------------------
`~/.claude/settings.json` becomes a **real, machine-local file** the app owns and
writes freely. `claude/settings.shared.json` is the version-controlled source, and
each of its top-level keys is classified:

  * OWNED_KEYS -- the shared value REPLACES the live value on every sync. Deletions
    propagate, and the app can never again silently drop an allow rule. `hooks` and
    `permissions` are the two things dev-env genuinely ships to every machine.
  * SEED_KEYS -- written only when the key is ABSENT from the live file. This is
    ADR-079 rule 3's "written-if-absent anchor": a fresh machine gets the default,
    but a later `/config` change sticks instead of being reverted every prompt.

Every other key in the live file is machine-local and is never read, written, or
removed here: `theme`, `tui`, `agentPushNotifEnabled`, `inputNeededNotifEnabled`,
`skipWorkflowUsageWarning`, `autoMode`. Keeping `autoMode` out of git is also a
privacy win -- its `environment` block describes the user's personal career data.

A top-level key that appears in the shared file but is in neither list is reported
as unclassified rather than silently ignored, so "I added a key to the shared file
and nothing happened" cannot become a silent no-op.

Safety (ADR-079 "Back up before you mutate")
--------------------------------------------
Every mutating write backs up the live file first, read live at backup time; a
backup that cannot be captured aborts the write. A one-time
`settings.json.pre-migration.bak` anchor is written if absent and never overwritten,
so repeated syncs can't erode the original. Writes are atomic (tmp + os.replace)
and verified by read-back. A no-op is reported as a skip, never as a change.

This module is import-safe and side-effect-free: `dev-env-sync.py` owns the
advisory printing, so the UserPromptSubmit stdout contract (ADR-098) lives in one
place. Pure-ASCII output throughout (the ADR-103 output-contract gate).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, NamedTuple

# Top-level keys of claude/settings.shared.json whose value REPLACES the live
# value wholesale on every sync. These are what dev-env actually ships.
OWNED_KEYS = ("hooks", "permissions")

# Top-level keys written only when ABSENT from the live file (ADR-079 rule 3).
# The app/`/config` may change them afterwards and the change sticks.
SEED_KEYS = ("model", "effortLevel")

# scripts/ -> claude/
_CLAUDE_DIR = Path(__file__).resolve().parent.parent
SHARED_PATH = _CLAUDE_DIR / "settings.shared.json"

LIVE_PATH = Path.home() / ".claude" / "settings.json"
BACKUP_DIR = Path.home() / ".claude" / "backups"

# Written once, never overwritten, never removed -- the pre-migration original.
ANCHOR_NAME = "settings.json.pre-migration.bak"


class SyncPlan(NamedTuple):
    """What a sync would change. Empty owned_updates + seed_inserts means no-op."""

    owned_updates: dict          # owned key -> shared value (live differs)
    seed_inserts: dict           # seed key -> shared value (absent from live)
    unclassified: list           # shared top-level keys in neither OWNED nor SEED


class SyncResult(NamedTuple):
    """Outcome of a sync attempt. `note` is None when there is nothing to say."""

    changed: bool
    migrated: bool               # a repo symlink was materialized this run
    note: "str | None"
    error: "str | None"


# --- pure planning ----------------------------------------------------------------


def classify(shared: dict) -> "tuple[list, list, list]":
    """Split the shared file's top-level keys into (owned, seed, unclassified)."""
    owned, seed, unclassified = [], [], []
    for key in shared:
        if key in OWNED_KEYS:
            owned.append(key)
        elif key in SEED_KEYS:
            seed.append(key)
        else:
            unclassified.append(key)
    return owned, seed, unclassified


def plan_sync(shared: dict, live: dict) -> SyncPlan:
    """Diff the shared source against the live settings.

    Owned keys are compared by value, so an already-applied sync plans no write.
    Seed keys are compared by *presence* only -- a live value that differs from the
    shared default is a deliberate `/config` change and is left alone.
    """
    owned_keys, seed_keys, unclassified = classify(shared)
    owned_updates = {k: shared[k] for k in owned_keys if live.get(k) != shared[k]}
    seed_inserts = {k: shared[k] for k in seed_keys if k not in live}
    return SyncPlan(owned_updates, seed_inserts, unclassified)


def apply_plan(live: dict, plan: SyncPlan) -> dict:
    """Return a new live-settings dict with the plan applied. Pure."""
    updated = dict(live)
    updated.update(plan.owned_updates)
    updated.update(plan.seed_inserts)
    return updated


def format_sync_note(plan: SyncPlan, migrated: bool) -> "str | None":
    """Advisory text for a completed sync, or None when there is nothing to report.

    Pure ASCII (ADR-103 output-contract gate). Printed by dev-env-sync.py to stdout,
    which is the only stream a UserPromptSubmit hook forwards on exit 0 (ADR-098).
    """
    lines = []
    if migrated:
        lines.append(
            "[dev-env-sync] Migrated ~/.claude/settings.json from a repo symlink to a real, "
            "machine-local file (dev-env#1049, ADR-139)."
        )
        lines.append(
            "  The app can now write theme/tui/autoMode there without dirtying the tracked "
            "repo file and blocking the canonical's fast-forward."
        )
        lines.append(f"  Original preserved at: {BACKUP_DIR / ANCHOR_NAME}")
    if plan.owned_updates:
        lines.append(
            "[dev-env-sync] Applied shared settings to ~/.claude/settings.json: "
            + ", ".join(sorted(plan.owned_updates))
        )
    if plan.seed_inserts:
        lines.append(
            "[dev-env-sync] Seeded absent shared defaults into ~/.claude/settings.json: "
            + ", ".join(sorted(plan.seed_inserts))
        )
    if plan.unclassified:
        lines.append(
            "[dev-env-sync] WARNING: claude/settings.shared.json has top-level key(s) that are "
            "neither owned nor seeded, so they were NOT applied: "
            + ", ".join(sorted(plan.unclassified))
        )
        lines.append(
            "  Add each to OWNED_KEYS or SEED_KEYS in claude/scripts/_settings_sync.py, or "
            "remove it from the shared file if it is machine-local."
        )
    return "\n".join(lines) if lines else None


# --- IO ---------------------------------------------------------------------------


def needs_materialization(live_path: Path) -> bool:
    """True when the live settings file is a symlink and must become a real file.

    The invariant ADR-139 establishes is simply: `~/.claude/settings.json` is a real,
    machine-local file. Any symlink there means the app's writes land somewhere other
    than where they belong, which is the whole defect -- so the symlink bit alone is
    the test.

    Deliberately NOT "a symlink resolving inside this checkout." That narrower check has
    a silent false negative: run from a worktree, `SHARED_PATH.parent` is the worktree's
    `claude/` while the live symlink points at the *canonical* checkout, so the target
    falls outside and the migration is skipped with no error -- which is exactly the
    situation the migration has to survive, since the fix ships from a branch.
    """
    try:
        return live_path.is_symlink()
    except OSError:
        return False


def read_json(path: Path) -> "dict | None":
    """Parse a JSON object from `path`; None on any read/parse failure or non-object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def backup_live(live_path: Path, backup_dir: Path, stamp: "str | None" = None) -> Path:
    """Copy the live file's CURRENT bytes to a timestamped backup and return its path.

    Read live at backup time, never from an earlier in-memory copy (ADR-079 rule 1).
    Also writes the never-overwritten `ANCHOR_NAME` original if it does not yet exist
    (ADR-079 rule 3). Raises OSError if the backup cannot be captured -- callers must
    let that abort the write rather than proceeding unbacked.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    payload = live_path.read_bytes()

    anchor = backup_dir / ANCHOR_NAME
    if not anchor.exists():
        anchor.write_bytes(payload)

    stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"settings.json.{stamp}.bak"
    target.write_bytes(payload)
    return target


def write_json_verified(path: Path, data: dict) -> None:
    """Atomically write `data` as pretty JSON, then verify by read-back (ADR-079 rule 4).

    Raises OSError if the written file does not parse back to an equal object, so a
    truncated or partially-written settings file is never left in place silently.
    """
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    written = read_json(path)
    if written != data:
        raise OSError(f"read-back verification failed for {path}")


def sync(
    shared_path: Path = SHARED_PATH,
    live_path: Path = LIVE_PATH,
    backup_dir: Path = BACKUP_DIR,
) -> SyncResult:
    """Materialize the live file if needed, then apply the shared settings into it.

    Never raises: every failure is returned as `error` so the calling
    UserPromptSubmit hook can stay fail-open and never block a prompt.
    """
    shared = read_json(shared_path)
    if shared is None:
        return SyncResult(False, False, None, f"could not read {shared_path}")

    migrated = False
    try:
        if needs_materialization(live_path):
            # Materialize: same bytes, but a real file the app can own. Back up first
            # -- through the symlink, so the anchor captures the pre-migration content.
            backup_live(live_path, backup_dir)
            payload = live_path.read_bytes()
            live_path.unlink()
            live_path.write_bytes(payload)
            migrated = True
    except OSError as exc:
        return SyncResult(False, False, None, f"symlink migration failed: {exc}")

    if not live_path.exists():
        # Fresh machine (or a just-removed file): seed owned + seed keys outright.
        plan = SyncPlan(
            {k: shared[k] for k in OWNED_KEYS if k in shared},
            {k: shared[k] for k in SEED_KEYS if k in shared},
            classify(shared)[2],
        )
        try:
            live_path.parent.mkdir(parents=True, exist_ok=True)
            write_json_verified(live_path, apply_plan({}, plan))
        except OSError as exc:
            return SyncResult(False, migrated, None, f"could not create {live_path}: {exc}")
        return SyncResult(True, migrated, format_sync_note(plan, migrated), None)

    live = read_json(live_path)
    if live is None:
        # Unparseable live file: do NOT overwrite -- that would destroy user config
        # with no way to reconstruct the app-written half. Report and leave it alone.
        return SyncResult(
            False, migrated, None, f"{live_path} is not readable as a JSON object; left untouched"
        )

    plan = plan_sync(shared, live)
    if not plan.owned_updates and not plan.seed_inserts:
        # No-op recorded as a skip, never as a change (ADR-079 rule 4).
        note = format_sync_note(plan, migrated) if (migrated or plan.unclassified) else None
        return SyncResult(False, migrated, note, None)

    try:
        backup_live(live_path, backup_dir)
    except OSError as exc:
        return SyncResult(False, migrated, None, f"refusing to write without a backup: {exc}")

    try:
        write_json_verified(live_path, apply_plan(live, plan))
    except OSError as exc:
        return SyncResult(False, migrated, None, f"could not write {live_path}: {exc}")

    return SyncResult(True, migrated, format_sync_note(plan, migrated), None)


if __name__ == "__main__":
    # One-shot bootstrap/repair entry point, so the sync is runnable outside the
    # UserPromptSubmit hook: setup.sh calls it to seed a fresh machine's live settings
    # file, and a user can re-run it by hand after editing the shared source:
    #   py -3 ~/.claude/scripts/_settings_sync.py
    import sys

    outcome = sync()
    if outcome.error:
        print(f"[settings-sync] WARNING: {outcome.error}")
    if outcome.note:
        print(outcome.note)
    if not outcome.error and not outcome.changed and not outcome.migrated:
        print("[settings-sync] Already in sync - no change.")
    sys.exit(1 if outcome.error else 0)
