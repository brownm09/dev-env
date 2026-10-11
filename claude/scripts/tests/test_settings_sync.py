#!/usr/bin/env python3
"""Tests for claude/scripts/_settings_sync.py (dev-env#1049, ADR-139).

What this pins, in order of how badly a regression would hurt:

  1. **Machine-local keys survive.** The whole bug was the app's own writes landing in a
     tracked file. If a sync ever dropped `theme`/`tui`/`autoMode`, we would have traded a
     blocked fast-forward for silent destruction of the user's config.
  2. **Owned keys are replaced, not merged.** `permissions` must come back wholesale from
     the shared source, because the motivating incident was an app rewrite silently
     REMOVING two committed `permissions.allow` entries (`Bash(node -e *)` and
     `Bash(rm -f .../scratch/*)`). A merge-style union would let a removal persist.
  3. **Seed keys are presence-checked, not value-checked.** A `/config` change to `model`
     must stick; a sync that reverted it every prompt would be a new bug.
  4. **ADR-079 discipline.** Backup before write, never write without one, verify by
     read-back, and treat a no-op as a skip rather than a change.
  5. **Symlink migration.** The one-time repo-symlink -> real-file step, including that the
     pre-migration anchor is written once and never overwritten.

Run: py -3 claude/scripts/tests/test_settings_sync.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _settings_sync  # noqa: E402

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  ok: {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL: {msg}")


def check(cond: bool, msg: str) -> None:
    ok(msg) if cond else bad(msg)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


SHARED = {
    "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "pyw -3 a.py"}]}]},
    "permissions": {"allow": ["Bash(node -e *)", "Bash(rm -f C:/x/scratch/*)"], "defaultMode": "plan"},
    "model": "claude-sonnet-4-6",
    "effortLevel": "max",
}

# The app-written half: exactly the keys observed in the dev-env#1049 dirty diff.
MACHINE_LOCAL = {
    "theme": "dark",
    "tui": "fullscreen",
    "agentPushNotifEnabled": True,
    "inputNeededNotifEnabled": True,
    "skipWorkflowUsageWarning": True,
    "autoMode": {"environment": ["### Org-wide", "personal data that must never reach git"]},
}


class Env:
    """A throwaway shared/live/backup trio."""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp())
        self.shared = self.root / "claude" / "settings.shared.json"
        self.live = self.root / "home" / ".claude" / "settings.json"
        self.backups = self.root / "home" / ".claude" / "backups"
        # The launcher source and its installed copy, plus one real hook script, so the
        # shared `hooks` this Env ships passes hooks_guard (dev-env#1146) -- every path
        # its command names exists once ensure_launcher has run.
        self.launcher_src = self.root / "claude" / "scripts" / "_hook_launch.py"
        self.launcher_dest = self.root / "home" / ".claude" / "hook-launch.py"
        self.hook_script = self.root / "home" / ".claude" / "scripts" / "a.py"
        self.launcher_src.parent.mkdir(parents=True, exist_ok=True)
        self.launcher_src.write_text("# launcher v1\n", encoding="utf-8")
        self.hook_script.parent.mkdir(parents=True, exist_ok=True)
        self.hook_script.write_text("pass\n", encoding="utf-8")
        self.shared_data = dict(SHARED, hooks=self.hooks_for(self.hook_script))
        write_json(self.shared, self.shared_data)
        return self

    def hooks_for(self, *scripts: Path) -> dict:
        """A `hooks` value wiring each script through this Env's launcher."""
        return {
            "UserPromptSubmit": [
                {"hooks": [
                    {"type": "command", "command": f"pyw -3 {self.launcher_dest.as_posix()} {s.as_posix()}"}
                    for s in scripts
                ]}
            ]
        }

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)

    def sync(self):
        return _settings_sync.sync(
            self.shared, self.live, self.backups, self.launcher_src, self.launcher_dest
        )

    def live_data(self) -> dict:
        return json.loads(self.live.read_text(encoding="utf-8"))


# --- 1. pure planning ------------------------------------------------------------------

print("[1] plan_sync classifies owned / seed / unclassified correctly")

plan = _settings_sync.plan_sync(SHARED, {})
check(set(plan.owned_updates) == {"hooks", "permissions"}, "empty live -> both owned keys planned")
check(set(plan.seed_inserts) == {"model", "effortLevel"}, "empty live -> both seed keys planned")
check(plan.unclassified == [], "no unclassified keys in a well-formed shared file")

plan = _settings_sync.plan_sync(SHARED, dict(SHARED))
check(not plan.owned_updates and not plan.seed_inserts, "identical live -> no-op plan")

# A seed key whose live value DIFFERS is deliberately left alone -- that is a /config change.
live_changed_seed = dict(SHARED, model="claude-opus-5")
plan = _settings_sync.plan_sync(SHARED, live_changed_seed)
check(
    not plan.seed_inserts,
    "seed key present with a different value -> not reverted (a /config change sticks)",
)

# An owned key whose live value differs IS replaced -- this is the permission-drop guard.
live_dropped_perm = dict(SHARED, permissions={"allow": [], "defaultMode": "plan"})
plan = _settings_sync.plan_sync(SHARED, live_dropped_perm)
check("permissions" in plan.owned_updates, "owned key with a different value -> replaced")
check(
    plan.owned_updates["permissions"]["allow"] == SHARED["permissions"]["allow"],
    "owned replacement restores the exact shared allow-list (dev-env#1049 permission drop)",
)

plan = _settings_sync.plan_sync(dict(SHARED, theme="dark"), {})
check(plan.unclassified == ["theme"], "a key in neither list is reported as unclassified")
note = _settings_sync.format_sync_note(plan, migrated_from=None)
check("WARNING" in note and "theme" in note, "unclassified key produces a named WARNING, not silence")

# --- 2. machine-local keys survive a real sync -------------------------------------------

print("[2] machine-local (app-written) keys are never touched")

with Env() as env:
    write_json(env.live, dict(MACHINE_LOCAL))
    result = env.sync()
    data = env.live_data()
    check(result.changed, "first sync into an app-only live file reports a change")
    for key, value in MACHINE_LOCAL.items():
        check(data.get(key) == value, f"machine-local key preserved: {key}")
    check(data["permissions"] == SHARED["permissions"], "owned permissions applied")
    check(data["model"] == SHARED["model"], "seed model applied when absent")
    check(
        "personal data that must never reach git" in json.dumps(data["autoMode"]),
        "autoMode content survives intact (it is the reason this stays out of git)",
    )

# --- 3. idempotence and no-op accounting -------------------------------------------------

print("[3] a second sync is a no-op, recorded as a skip")

with Env() as env:
    write_json(env.live, dict(MACHINE_LOCAL))
    env.sync()
    before = env.live.read_bytes()
    backups_before = len(list(env.backups.glob("settings.json.*.bak")))
    second = env.sync()
    check(not second.changed, "second sync reports changed=False")
    check(second.note is None, "no-op sync emits no advisory noise")
    check(env.live.read_bytes() == before, "no-op sync leaves the file byte-identical")
    check(
        len(list(env.backups.glob("settings.json.*.bak"))) == backups_before,
        "no-op sync takes no new backup (a skip is not a change)",
    )

# --- 4. ADR-079: backup, anchor, read-back ----------------------------------------------

print("[4] ADR-079 backup / anchor / verification discipline")

with Env() as env:
    original = dict(MACHINE_LOCAL, permissions={"allow": []})
    write_json(env.live, original)
    env.sync()
    anchor = env.backups / _settings_sync.ANCHOR_NAME
    check(anchor.exists(), "pre-migration anchor written on the first mutating write")
    check(
        json.loads(anchor.read_text(encoding="utf-8")) == original,
        "anchor captures the pre-sync content, read live at backup time",
    )
    # Re-baseline only by deleting deliberately: a later write must not overwrite it.
    write_json(env.live, dict(MACHINE_LOCAL, permissions={"allow": ["Bash(later *)"]}))
    env.sync()
    check(
        json.loads(anchor.read_text(encoding="utf-8")) == original,
        "anchor is never overwritten by a later sync (ADR-079 rule 3)",
    )

with Env() as env:
    write_json(env.live, dict(MACHINE_LOCAL))
    # Make the backup directory un-creatable by parking a FILE where it must go.
    env.backups.parent.mkdir(parents=True, exist_ok=True)
    env.backups.write_text("not a directory", encoding="utf-8")
    before = env.live.read_bytes()
    result = env.sync()
    check(not result.changed, "a backup that cannot be captured aborts the write")
    check(result.error is not None and "backup" in result.error, "the refusal names the backup as the cause")
    check(env.live.read_bytes() == before, "live file is untouched when the backup fails")

# --- 4b. backup pruning keeps the anchor -------------------------------------------------

print("[4b] timestamped backups are capped; the anchor is never pruned")

with Env() as env:
    env.backups.mkdir(parents=True, exist_ok=True)
    anchor = env.backups / _settings_sync.ANCHOR_NAME
    anchor.write_text("original", encoding="utf-8")
    # 25 stamped backups, lexicographically ordered the same way real stamps are.
    for i in range(25):
        (env.backups / f"settings.json.20260101_{i:06d}.bak").write_text(str(i), encoding="utf-8")

    removed = _settings_sync.prune_backups(env.backups, keep=10)
    survivors = sorted(p.name for p in env.backups.glob("settings.json.*.bak"))

    check(len(removed) == 15, f"prunes down to the cap (removed {len(removed)}, expected 15)")
    check(anchor.exists(), "ANCHOR is not pruned even though the glob matches its name")
    check(
        anchor.read_text(encoding="utf-8") == "original",
        "ANCHOR content is untouched by pruning",
    )
    stamped = [n for n in survivors if n != _settings_sync.ANCHOR_NAME]
    check(len(stamped) == 10, "exactly `keep` stamped backups survive")
    check(
        stamped[-1] == "settings.json.20260101_000024.bak",
        "the NEWEST stamped backup survives (not the oldest)",
    )

with Env() as env:
    # A backup dir under the cap must lose nothing.
    env.backups.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (env.backups / f"settings.json.20260101_{i:06d}.bak").write_text(str(i), encoding="utf-8")
    check(
        _settings_sync.prune_backups(env.backups, keep=10) == [],
        "fewer backups than the cap prunes nothing",
    )

# --- 4c. the write re-reads, so a concurrent update is not clobbered ----------------------

print("[4c] a concurrent write between plan and write is not discarded")

with Env() as env:
    write_json(env.live, dict(MACHINE_LOCAL))
    real_read_json = _settings_sync.read_json
    calls = {"n": 0}

    def racing_read(path):
        """Simulate the app persisting a /config change after the plan is computed.

        The 2nd read is the pre-write re-read inside sync(); mutating the file just
        before it returns is exactly the window the re-read exists to close.
        """
        calls["n"] += 1
        if calls["n"] == 2:
            current = real_read_json(path)
            if isinstance(current, dict):
                current["theme"] = "light-set-by-the-app"
                write_json(Path(path), current)
        return real_read_json(path)

    _settings_sync.read_json = racing_read
    try:
        result = env.sync()
    finally:
        _settings_sync.read_json = real_read_json

    data = env.live_data()
    check(result.changed, "sync still applies its plan under a concurrent write")
    check(
        data.get("theme") == "light-set-by-the-app",
        "the concurrent app write survives (a stale snapshot would have reverted it)",
    )
    check(data["permissions"] == SHARED["permissions"], "owned keys still applied on that path")

# --- 5. degraded inputs fail safe --------------------------------------------------------

print("[5] degraded inputs never destroy user config")

with Env() as env:
    env.live.parent.mkdir(parents=True, exist_ok=True)
    env.live.write_text("{ this is not json", encoding="utf-8")
    before = env.live.read_bytes()
    result = env.sync()
    check(not result.changed, "an unparseable live file is not overwritten")
    check(env.live.read_bytes() == before, "unparseable live file left byte-identical")
    check(result.error is not None, "the skip is reported, not silent")

with Env() as env:
    env.shared.unlink()
    result = env.sync()
    check(not result.changed and result.error is not None, "a missing shared file is a reported no-op")

with Env() as env:
    result = env.sync()
    check(result.changed and env.live.exists(), "an absent live file is created (fresh-machine seed)")
    data = env.live_data()
    check(
        set(data) == {"hooks", "permissions", "model", "effortLevel"},
        "fresh seed writes exactly the owned + seed keys",
    )

# --- 6. symlink migration ----------------------------------------------------------------

print("[6] repo-symlink migration")

with Env() as env:
    repo_copy = env.root / "claude" / "settings.json"
    write_json(repo_copy, dict(SHARED, **MACHINE_LOCAL))
    env.live.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(repo_copy, env.live)
        supported = True
    except (OSError, NotImplementedError):
        supported = False

    if not supported:
        # Windows needs Developer Mode or elevation for os.symlink. The pure detector is
        # still exercised below, so this only skips the end-to-end materialization.
        print("  note: os.symlink unavailable here; end-to-end migration not exercised")
        check(
            not _settings_sync.needs_materialization(env.live),
            "needs_materialization is False for a plain real file",
        )
    else:
        check(
            _settings_sync.needs_materialization(env.live),
            "needs_materialization detects a symlink into the repo",
        )
        result = env.sync()
        check(result.migrated, "sync reports the migration")
        check(not env.live.is_symlink(), "live path is a real file after migration")
        check(
            result.note is not None and "It pointed at:" in result.note,
            "the advisory names the link target it replaced, rather than asserting 'repo symlink'",
        )
        check(
            result.note is not None and "settings.json" in result.note.split("It pointed at:")[1],
            "the reported target is the actual resolved path",
        )
        check(
            (env.backups / _settings_sync.ANCHOR_NAME).exists(),
            "migration captured the pre-migration anchor",
        )
        check(
            env.live_data()["theme"] == "dark",
            "migration preserves the app-written keys it materialized",
        )
        check(
            not _settings_sync.needs_materialization(env.live),
            "post-migration the live file is no longer seen as a repo symlink",
        )
        # Writing through the real file must no longer touch the repo copy -- the entire
        # point of the change (the repo file can never go dirty again).
        before_repo = repo_copy.read_bytes()
        write_json(env.live, dict(env.live_data(), theme="light"))
        check(repo_copy.read_bytes() == before_repo, "writes to the live file no longer reach the repo file")

with Env() as env:
    write_json(env.live, dict(MACHINE_LOCAL))
    check(
        not _settings_sync.needs_materialization(env.live),
        "a real file outside the repo is not treated as a migration candidate",
    )
    check(
        not _settings_sync.needs_materialization(env.live.parent / "absent.json"),
        "a nonexistent path is not a migration candidate",
    )

# The regression this pins: the fix ships from a worktree, so the live symlink points at
# the CANONICAL checkout while the shared file being synced lives in the worktree. An
# earlier "symlink resolving inside THIS checkout" detector skipped that case silently --
# leaving the live file a symlink to a repo file the merge was about to delete.
with Env() as env:
    other_checkout = env.root / "some" / "other" / "canonical" / "claude"
    repo_copy = other_checkout / "settings.json"
    write_json(repo_copy, dict(SHARED, **MACHINE_LOCAL))
    env.live.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(repo_copy, env.live)
    except (OSError, NotImplementedError):
        print("  note: os.symlink unavailable here; cross-checkout case not exercised")
    else:
        check(
            _settings_sync.needs_materialization(env.live),
            "a symlink into a DIFFERENT checkout still needs materialization",
        )
        result = env.sync()
        check(result.migrated, "cross-checkout symlink is migrated, not silently skipped")
        check(not env.live.is_symlink(), "cross-checkout migration produces a real file")

# --- 7. the shipped shared file is well-formed ------------------------------------------

print("[7] the real claude/settings.shared.json is classified end to end")

real_shared = _settings_sync.read_json(_settings_sync.SHARED_PATH)
check(real_shared is not None, "the shipped shared file parses as a JSON object")
if real_shared is not None:
    _, _, unclassified = _settings_sync.classify(real_shared)
    check(unclassified == [], f"every shipped top-level key is classified (unclassified={unclassified})")
    allow = real_shared.get("permissions", {}).get("allow", [])
    check("Bash(node -e *)" in allow, "shipped allow-list keeps Bash(node -e *) (dev-env#1049)")
    check(
        "Bash(rm -f C:/Users/brown/.claude/scratch/*)" in allow,
        "shipped allow-list keeps the scratch rm -f entry (dev-env#1049)",
    )
    for key in ("theme", "tui", "autoMode", "skipWorkflowUsageWarning"):
        check(key not in real_shared, f"machine-local key stays out of the tracked file: {key}")

# --- 8. hook launcher install + missing-script guard (dev-env#1146, ADR-148) ------------

print("[8] ensure_launcher installs the launcher; hooks_guard never wires a missing file")

with Env() as env:
    installed, err = _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    check(installed and err is None, "first ensure_launcher installs the launcher")
    check(env.launcher_dest.read_bytes() == env.launcher_src.read_bytes(), "installed bytes match the source")
    installed, err = _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    check(not installed and err is None, "unchanged source -> no-op, reported as a skip (ADR-079 rule 4)")
    env.launcher_src.write_text("# launcher v2\n", encoding="utf-8")
    installed, err = _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    check(installed and env.launcher_dest.read_text(encoding="utf-8") == "# launcher v2\n",
          "a changed source replaces the installed launcher")
    check(not list(env.launcher_dest.parent.glob("hook-launch.py.*.tmp")), "no temp file is left behind")
    env.launcher_src.unlink()
    installed, err = _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    check(not installed and err is None and env.launcher_dest.is_file(),
          "source gone (an older tree) -> the installed launcher is kept, no error")
    env.launcher_dest.unlink()
    installed, err = _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    check(not installed and err is not None and "none is installed" in err,
          "no source and nothing installed -> a named error, never a raise")

# A launcher source that does not compile is never installed: every hook runs through
# the installed copy, so a broken one would make every hook exit 1 and stop silently.
with Env() as env:
    _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    good_bytes = env.launcher_dest.read_bytes()
    env.launcher_src.write_text("def broken(:\n", encoding="utf-8")
    installed, err = _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    check(not installed and err is not None and "does not compile" in err,
          "a launcher source with a syntax error is refused, with a named error")
    check(env.launcher_dest.read_bytes() == good_bytes, "the previously installed launcher is kept")

# The worktree case: installing from a source that is not the canonical says so.
with Env() as env:
    write_json(env.live, dict(MACHINE_LOCAL))
    note = env.sync().note or ""
    check("from " in note and "not the canonical checkout" in note,
          "a launcher installed from a non-canonical tree is named as such in the note")

# Tokenizing: quoted paths (a home with a space) are one token, quotes stripped.
check(
    _settings_sync.py_tokens('pyw -3 "C:/Users/Jane Doe/.claude/hook-launch.py" "C:/Users/Jane Doe/x.py"')
    == ["C:/Users/Jane Doe/.claude/hook-launch.py", "C:/Users/Jane Doe/x.py"],
    "a quoted path containing a space is checked as one path",
)
check(_settings_sync.py_tokens('pyw -3 "unbalanced.py') == [], "unbalanced quotes -> no tokens (reported unparsed)")
check(_settings_sync.py_tokens("node x.js") == [], "a non-Python command names no .py")
with Env() as env:
    spaced = env.root / "Jane Doe" / "hook.py"
    spaced.parent.mkdir(parents=True)
    spaced.write_text("pass\n", encoding="utf-8")
    _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    quoted = {"Stop": [{"hooks": [{"type": "command",
               "command": f'pyw -3 "{env.launcher_dest.as_posix()}" "{spaced.as_posix()}"'}]}]}
    check(_settings_sync.hooks_guard(quoted) is None, "the guard accepts an existing quoted path with a space")

# The recovery text must work in Windows PowerShell 5.1, the hook-free Terminal panel's
# default shell: absolute paths (no `~`) and no `&&`.
plan_withheld = _settings_sync.SyncPlan({}, {}, [], {"hooks": "x"})
note = _settings_sync.format_sync_note(plan_withheld, None) or ""
check("&&" not in note and "~/Git" not in note and "py -3 ~" not in note,
      "the withheld-hooks recovery command uses absolute paths and no &&")

with Env() as env:
    _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    good = env.hooks_for(env.hook_script)
    check(_settings_sync.hooks_guard(good) is None, "every wired path exists -> guard passes")
    gone = env.hook_script.parent / "gone.py"
    reason = _settings_sync.hooks_guard(env.hooks_for(env.hook_script, gone))
    check(reason is not None and "gone.py" in reason and "1 wired file" in reason,
          "a missing hook script is named in the guard's reason")
    env.launcher_dest.unlink()
    reason = _settings_sync.hooks_guard(good)
    check(reason is not None and "hook-launch.py" in reason, "a missing launcher is refused too")
    reason = _settings_sync.hooks_guard({"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]})
    check(reason is not None and "name no .py" in reason,
          "a command naming no .py is refused (an empty extraction is not a pass, ADR-144)")
    check(_settings_sync.hooks_guard([]) is not None, "a non-object hooks value is refused")
    check(
        _settings_sync.hooks_guard({"Stop": [{"hooks": [{"type": "prompt", "prompt": "x"}]}]}) is None,
        "a non-command hook entry runs no file and is not checked",
    )

# The forward case from the incident: the shared file (a worktree, or a canonical mid-pull)
# wires a script the junctioned tree does not have yet. The live wiring must be KEPT.
with Env() as env:
    old_hooks = env.hooks_for(env.hook_script)
    write_json(env.live, dict(MACHINE_LOCAL, hooks=old_hooks))
    _settings_sync.ensure_launcher(env.launcher_src, env.launcher_dest)
    new_script = env.hook_script.parent / "brand-new-hook.py"
    write_json(env.shared, dict(env.shared_data, hooks=env.hooks_for(env.hook_script, new_script)))
    result = env.sync()
    data = env.live_data()
    check(result.error is None, "withholding hooks is reported, not an error")
    check(data["hooks"] == old_hooks, "a missing wired script -> the live hooks are kept untouched")
    check(data["permissions"] == SHARED["permissions"], "the other owned keys are still applied")
    check(data["theme"] == MACHINE_LOCAL["theme"], "machine-local keys still survive")
    note = result.note or ""
    check("NOT applied" in note and "brand-new-hook.py" in note, "the note names the missing script")
    check("checkout main;" in note and " pull;" in note and "_settings_sync.py" in note,
          "the note names the recovery commands")
    note.encode("ascii")
    ok("the withheld note is pure ASCII (ADR-103 output contract)")
    again = env.sync()
    check(not again.changed and "NOT applied" in (again.note or ""),
          "a re-sync with the script still missing writes nothing and says so again")
    new_script.write_text("pass\n", encoding="utf-8")
    third = env.sync()
    check(third.changed and env.live_data()["hooks"] == env.hooks_for(env.hook_script, new_script),
          "once the script exists (the pull landed), the next sync applies the hooks")

# Ordering: the launcher is installed BEFORE hooks naming it are written.
with Env() as env:
    write_json(env.live, dict(MACHINE_LOCAL))
    check(not env.launcher_dest.exists(), "precondition: no launcher installed yet")
    result = env.sync()
    check(env.launcher_dest.is_file(), "sync installs the launcher")
    check(env.live_data()["hooks"] == env.shared_data["hooks"], "and then applies hooks that name it")
    check("Installed the current hook launcher" in (result.note or ""), "the install is reported")

# A launcher that cannot be installed (no source, none installed) withholds hooks.
with Env() as env:
    write_json(env.live, dict(MACHINE_LOCAL))
    env.launcher_src.unlink()
    result = env.sync()
    data = env.live_data()
    check("hooks" not in data, "no launcher on disk -> hooks naming it are withheld")
    check(data["permissions"] == SHARED["permissions"], "other owned keys still applied")
    check("none is installed" in (result.note or "") and "NOT applied" in (result.note or ""),
          "both the launcher error and the withheld key are reported")

# A launcher-only change on an otherwise in-sync file is a change, and is reported.
with Env() as env:
    env.sync()
    env.launcher_src.write_text("# launcher v2\n", encoding="utf-8")
    result = env.sync()
    check(result.changed and "Installed the current hook launcher" in (result.note or ""),
          "a launcher update with settings already in sync counts as a change")
    quiet = env.sync()
    check(not quiet.changed and quiet.note is None, "and the next sync is silent again")

# Fresh machine: no live file, and a wired script is missing.
with Env() as env:
    gone = env.hook_script.parent / "not-here.py"
    write_json(env.shared, dict(env.shared_data, hooks=env.hooks_for(gone)))
    result = env.sync()
    data = env.live_data()
    check(result.changed and "hooks" not in data, "fresh machine: hooks withheld, file still created")
    check(data["permissions"] == SHARED["permissions"] and data["model"] == SHARED["model"],
          "fresh machine: other owned and seed keys still written")
    check("not-here.py" in (result.note or ""), "fresh machine: the missing script is named")

# Calibration (ADR-144): known-good = the real shipped hooks with their paths remapped into
# this repo (all must resolve); known-bad = the same with one script removed.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hook_wiring  # noqa: E402  (tests/ support module; owns the shipped prefix)

real = _settings_sync.read_json(_settings_sync.SHARED_PATH) or {}
real_hooks_text = json.dumps(real.get("hooks", {}))
scripts_dir = Path(_settings_sync.__file__).resolve().parent
remapped_text = real_hooks_text.replace(
    _hook_wiring.LAUNCHER_PATH, (scripts_dir / "_hook_launch.py").as_posix()
).replace(_hook_wiring.SCRIPTS_PREFIX, scripts_dir.as_posix() + "/")
# A prefix the remap missed would leave the "known-good" check probing the REAL
# ~/.claude -- passing on the author's machine and failing only on CI. Fail loudly.
check(_hook_wiring.HOME_PREFIX not in remapped_text,
      "calibration remap rewrote every shipped path (none still points at the real home)")
remapped = json.loads(remapped_text)
n_commands = len(_settings_sync._hook_commands(remapped))
check(n_commands > 50, f"calibration extracted the shipped commands (n={n_commands}), not an empty set")
check(_settings_sync.hooks_guard(remapped) is None,
      f"known-good: all {n_commands} shipped hook commands resolve in this tree")
with Env() as env:
    known_bad_text = remapped_text.replace(
        (scripts_dir / "dev-env-sync.py").as_posix(), (env.root / "dev-env-sync.py").as_posix()
    )
    check(known_bad_text != remapped_text, "known-bad fixture actually differs from known-good")
    reason = _settings_sync.hooks_guard(json.loads(known_bad_text))
    check(reason is not None and "dev-env-sync.py" in reason, "known-bad: one missing script is caught")

print(f"\nResults: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
