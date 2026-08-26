# ADR-139: `~/.claude/settings.json` Is Machine-Local; dev-env Ships a Shared Source It Syncs In

**Status:** Accepted
**Date:** 2026-08-25
**Issues:** [dev-env#1049](https://github.com/brownm09/dev-env/issues/1049), [dev-env#1050](https://github.com/brownm09/dev-env/issues/1050) (duplicate of #1049)
**Supersedes (in part):** [ADR-003](003-config-in-version-control.md)'s inclusion of `settings.json` in the symlinked set
**Consolidates:** PR [#1058](https://github.com/brownm09/dev-env/pull/1058), an independent concurrent fix for the same issues, closed as superseded by user decision. Its project-scoping rule is carried forward below — that half was genuinely additive and is not something the structural fix provides.

---

## Context

`~/.claude/settings.json` was a symlink into this repo's working tree
(`claude/settings.json`), per [ADR-003](003-config-in-version-control.md)'s
"config in version control" rule. That made one file two things at once:

1. **The version-controlled source** of the hooks and permissions dev-env ships to every
   machine, and
2. **The live file the Claude Code app itself writes** — `/config` theme changes, `tui`,
   notification flags, and the `autoMode` environment scan.

Role 2 makes role 1 impossible. The app rewrites the file on its own schedule, so the
*tracked* file went dirty with no session having touched it, and
`dev-env-sync.py`'s fast-forward pull then failed permanently — git refuses to overwrite a
dirty tracked file:

```
error: Your local changes to the following files would be overwritten by merge:
	claude/settings.json
Please commit your changes or stash them before you merge.
```

### The consequence is stale global tooling, not a cosmetic warning

`~/.claude/{scripts,skills,hooks}` are junctions into that same canonical checkout, so a
canonical stuck behind `origin/main` serves **stale tooling machine-wide**. Observed live
on 2026-08-25: the canonical sat 3 commits behind through 14 consecutive failed pulls over
6h 20m, and the `/review` skill being served was the **pre-#1042** version — it still
prescribed the `cat > "$TMPFILE" << 'REVIEW_EOF'` heredoc that [ADR-138](138-shell-content-write-guard.md)
had *just* banned, and the ADR-138 guard meant to block that heredoc was itself not yet
wired. Stale tooling did not sit idle; it actively instructed sessions to violate a rule
that had already merged.

### Why this is a new class, not a third repeat

The two precedents ([#697](https://github.com/brownm09/dev-env/issues/697),
[#795](https://github.com/brownm09/dev-env/issues/795)) were **session-authored content
files** left uncommitted — `claude/skills/sources.md` and a `SKILL.md`. "Commit or stash
the edit you made" resolved each, permanently, because a session is not going to re-author
it tomorrow. Here the writer is the app, so that remedy expires on the next settings
change. It is a structural collision, not an outstanding edit.

### The sharper half: the same rewrite silently dropped committed permissions

The uncommitted diff also **removed** two `permissions.allow` entries that were committed
on `main`, plus `"effortLevel": "max"`:

- `Bash(node -e *)` — added deliberately in [#184](https://github.com/brownm09/dev-env/pull/184)
- `Bash(rm -f C:/Users/brown/.claude/scratch/*)` — added deliberately in [#182](https://github.com/brownm09/dev-env/pull/182)

Both back the documented JSON-parsing recipe in `claude/CLAUDE.md` ("`jq` is NOT available.
Use `node -e` with a temp file"). So `git checkout --` and `git add` produced opposite,
both-plausible, both-lossy outcomes: committing would have removed the two permissions for
every machine; discarding would have destroyed the user's `autoMode` config with no backup.
That is a decision for the user, not a cleanup — it was put to them explicitly and resolved
as **restore all three**.

---

## Decision

**`~/.claude/settings.json` is a real, machine-local file the app owns.**
**`claude/settings.shared.json` is the version-controlled source, synced *into* it.**

Each top-level key of the shared file is classified:

| Class | Keys | Sync behavior |
|---|---|---|
| **Owned** | `hooks`, `permissions` | The shared value **replaces** the live value on every sync. Deletions propagate; the app can never again silently drop an allow rule. |
| **Seed** | `model`, `effortLevel` | Written **only when absent** from the live file. A fresh machine gets the default; a later `/config` change sticks. |
| **Machine-local** | `theme`, `tui`, `agentPushNotifEnabled`, `inputNeededNotifEnabled`, `skipWorkflowUsageWarning`, `autoMode` | Never in the shared file. Never read, written, or removed by the sync. |

A top-level key in the shared file that is in neither list is reported as **unclassified**
rather than silently ignored, so "I added a key and nothing happened" cannot happen quietly.

`claude/scripts/_settings_sync.py` implements this. `dev-env-sync.py` calls it at the top of
`main()` — **before** the fast-forward pull, which is load-bearing (see Consequences).
`setup.sh` calls the same code path to seed a fresh machine, and it is runnable by hand:

```bash
py -3 ~/.claude/scripts/_settings_sync.py
```

### Why not `settings.local.json` (the obvious answer — verified, and refuted)

The issue's preferred direction was to move app-written keys into `settings.local.json`,
"already the Claude Code convention for exactly this split." That was checked against the
official documentation and the shipped `2.1.237` binary rather than assumed, and it does
not work:

- The settings table lists **four** files, and `settings.local.json` is **project-scoped
  only** (`.claude/settings.local.json`). There is no `~/.claude/settings.local.json`.[^1]
- The binary's own internal doc string enumerates the device-level sources exhaustively:
  *"Settings source on the device: user settings, the checkout's settings.local.json, or a
  `--settings` file."* All 87 occurrences of the string in the binary are `.claude/`-prefixed.
- The docs state the app writes user-scope prefs to `~/.claude/settings.json` **by name**:
  *"It writes `~/.claude/settings.json` the first time you change an option in the `/config`
  menu that it stores in user settings, such as the theme."*[^1]
- `~/.claude.json` holds none of these keys, so the app had not migrated them elsewhere.

There is therefore nowhere at user scope to move `autoMode`/`theme`/`tui` *to*. The split
has to happen on the other axis: not "which file does the app write", but "which file does
dev-env track."

### Second rule, orthogonal to the first: project-scoped content belongs in project-local

Making the live file machine-local fixes *where dev-env's git sees it*. It does **not** fix *how
broadly it loads*. `~/.claude/settings.json` is the **user** scope — every project's session on this
machine reads it — so a key carrying project-specific content is misplaced there whether or not the
file is tracked.

The `autoMode.environment` block is the concrete case: it is a written description of
**career-playbook's** repo, visibility, policies, and the user's personal career-search data, and
under the old arrangement it loaded for dev-env, lifting-logbook, and every other project — as well
as sitting one `git add` away from being committed.

**Rule:** when the Claude Code app writes a key that embeds project-specific content — an auto-mode
environment scan, a per-project credential hint, anything naming a particular repo's paths,
visibility, or policies — relocate it to *that project's own* `.claude/settings.local.json`.
Verify the project's `.gitignore` actually excludes that path first (`git check-ignore -v
.claude/settings.local.json`); Claude Code auto-excludes the file only when *it* created it. An
ordinary global preference toggle carrying no project-specific content (`theme`, `tui`,
`*NotifEnabled`) is unaffected and stays in the user file.

This rule comes from PR #1058 and is the reason that PR was consolidated rather than simply closed.
Two deliberate divergences from it:

- **#1058's rule text is premised on the symlink** ("because `~/.claude/settings.json` is a symlink
  into this repo's tracked `claude/settings.json`…"). That premise is removed by the decision above,
  so the rule is restated on the argument that survives it: **scope**, not git-tracking.
- **#1058 keeps `skipWorkflowUsageWarning` in the tracked file**, on the reasoning that it is an
  ordinary global toggle like `theme`/`tui`. The classification is right; the destination is not.
  Committing an app-written key is the issue's Direction 3, rejected on its face — and it is exactly
  how the fast-forward re-blocks: the app rewrites the file, the tracked copy goes dirty again, and
  the remedy is another commit. Here `skipWorkflowUsageWarning` is machine-local, alongside the
  other toggles it resembles, and nothing has to be committed when the app changes it.

### Alternatives rejected

- **Auto-stash-and-reapply in `dev-env-sync.py`** (the issue's Direction 2). Contained and
  needs no symlink surgery, but it manages the collision instead of removing it, silently
  mutates user config, and *would not have unblocked the motivating incident* — that diff
  also touched `permissions`, a shared key, which should stop and ask rather than be
  auto-stashed.
- **Periodically commit the app state** (Direction 3). Rejected on its face: it makes every
  machine's local prefs a shared commit and reintroduces the permission-removal problem.
- **`git update-index --skip-worktree`.** Still errors on merge when an incoming commit
  touches the file — which is precisely the case that blocks the pull.
- **Keeping the name `claude/settings.json` for the shared source.** Cheaper (no consumers
  to repoint), but the whole defect was one filename meaning two things; a file named
  `settings.json` that is *not* `~/.claude/settings.json` invites someone to re-symlink it
  and reintroduce the bug. The distinct name is the durable guard, and CI gates the
  repointing.

---

## Consequences

**The tracked file can no longer go dirty on its own**, so the fast-forward pull cannot be
blocked by app activity. This is a by-construction fix, not a managed collision.

**Ordering is load-bearing, in both directions.** The sync runs *before* the pull in
`dev-env-sync.py`. The pull is what removes `claude/settings.json` from the working tree; a
still-symlinked live file would then resolve to nothing and **every hook would stop firing —
including `dev-env-sync.py` itself**, leaving nothing able to self-heal. For the same
reason, this machine's live file was materialized by hand *before* the PR merged.

**The migration detector tests the symlink bit, not the symlink's target.** An earlier draft
asked "does this symlink resolve inside *this* checkout?", which has a silent false negative:
the fix ships from a worktree, so the live symlink points at the *canonical* while the shared
file being synced lives in the worktree — the target falls outside and the migration is
skipped with no error. The invariant is simply that the live user-settings file is a real
file, so the symlink bit alone is the right test. Pinned by a dedicated cross-checkout case
in the suite.

**`permissions` is now repo-owned wholesale**, which strengthens the boundary: the app
cannot silently drop an allow rule again, because the next sync restores the shared list
verbatim. The `Bash(node -e *)` and scratch `rm -f` entries are pinned by name in the test
suite, tied to this issue.

**`autoMode` is out of git *and* correctly scoped.** Its `environment` block is a written
description of the user's personal career-search data. Under the old symlink it was one `git add`
away from being committed to a repo; under the second rule above it now lives only in
career-playbook's own gitignored `.claude/settings.local.json`, so it no longer loads for unrelated
projects either. The sync still classifies `autoMode` as machine-local and never touches it, so an
app that writes it globally again is preserved, not destroyed — the relocation is a scoping decision
a human makes, not something the sync should silently perform.

**Mutations follow [ADR-079](079-backup-restore-convention.md).** Backup before every write,
read live at backup time; refuse to write if the backup cannot be captured; a
never-overwritten `settings.json.pre-migration.bak` anchor; atomic write plus read-back
verification; a no-op recorded as a skip, not a change. Rollback is restoring a backup from
`~/.claude/backups/` (and re-creating the symlink, if ever wanted).

**Fail-open.** `_settings_sync.sync()` never raises; failures come back as `error` and
`dev-env-sync.py` prints them to **stdout** (the only stream a `UserPromptSubmit` hook
forwards on exit 0 — [ADR-098](098-dev-env-sync-advisories-to-stdout.md)) without blocking
the prompt or the pull. An unparseable live file is reported and left untouched rather than
overwritten, because overwriting would destroy the app-written half irrecoverably.

**Consumers repointed to the tracked source** (`hooks` is owned, so the two stay identical,
and the tracked file is the deterministic one for CI to gate):
`claude/scripts/hook-liveness-check.py`, `claude/scripts/tests/_hook_wiring.py` (and through
it `## Testing` items 61, 62, 63), and `claude/scripts/tests/test_pyw_stdio.py`.

**`setup.sh` no longer symlinks `settings.json`**; `seed_claude_settings()` materializes it
instead, and never fails the run — a seed that cannot complete warns with the exact manual
command rather than aborting setup with the links half-applied.

---

[^1]: Claude Code settings reference — *Settings files and who they affect* and *Find or
create your settings files*: https://code.claude.com/docs/en/settings
