# dev-env — Project Instructions

The global Claude Code configuration lives in [`claude/CLAUDE.md`](claude/CLAUDE.md),
symlinked to `~/.claude/CLAUDE.md`. All workflow rules, hook invariants, model selection
guidelines, and journal conventions are defined there and apply to every project.

This file is the dev-env-specific extension layer. Repo-local verification commands,
project board automation IDs, and repo-owned path conventions belong here rather than in the
global file. See [ADR-040](docs/adr/040-project-specific-rules-belong-in-project-claude.md).

## Reference Documentation

| Doc | Purpose |
|---|---|
| [README.md](README.md) | Quick-reference tables for skills, hooks, and routines |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Detailed descriptions, invocation syntax, config options, and ADR links |
| [docs/adr/](docs/adr/) | Design decisions behind rules in `claude/CLAUDE.md` |

## Testing

Run from the repo root to verify all hook scripts are syntax-clean:

```bash
py -3 -c "import ast,sys; [ast.parse(open(f,encoding='utf-8').read(),f) for f in sys.argv[1:]]" claude/scripts/*.py
```

Additionally, run the `pyw -3` stdio verification (Windows-only — confirms `pythonw.exe` honors parent-supplied pipes, the invariant ADR-007's 2026-06-01 decision relies on):

```bash
py -3 claude/scripts/tests/test_pyw_stdio.py
```

(On Windows, `python3` resolves to the Microsoft Store stub; use `py -3` — see [ADR-007](docs/adr/007-hook-command-invocation.md).)

When changing `claude/hooks/pre-push`, also run its behavioral self-test, which drives the
real hook against throwaway fixture repos with a stubbed `npm` and asserts the lockfile-drift
guard's BLOCK / PASS / SKIP paths, working-tree restoration, and repo-hook chaining (see
[ADR-036](docs/adr/036-lockfile-drift-prevention.md)):

```bash
bash claude/hooks/tests/test-pre-push-lockfile.sh
```

For docs-only changes to `claude/CLAUDE.md`: run `grep -n 'date -u' claude/CLAUDE.md` and
confirm every match is in an internal operational artifact context (lock files, log timestamps)
— not in stub filename or branch name descriptions.

## Dev-Env

`~/.claude/` is split between two categories. Treat them differently.

**Owned by `brownm09/dev-env` — symlinked, version-controlled:**

| Path | dev-env source |
|---|---|
| `~/.claude/CLAUDE.md` | `claude/CLAUDE.md` |
| `~/.claude/scripts/` | `claude/scripts/` (directory junction) |
| `~/.claude/skills/` | `claude/skills/` (directory junction) |
| `~/.claude/hooks/` | `claude/hooks/` (directory junction) |
| `~/.claude/scheduled-tasks/` | `claude/routines/` (directory junction to `~/.claude/scheduled-tasks/`) |
| `~/.claude/settings.json` | `claude/settings.json` |

**Machine-local only — never commit:**

`scratch/`, `projects/`, `sessions/`, `backups/`, `ide/`, `plans/`, `shell-snapshots/`

**Rule:** Any addition or modification to a dev-env-owned artifact — new hook script, new
skill, settings change, `CLAUDE.md` edit — must be committed to `brownm09/dev-env` via
branch and PR before the session ends. Do not leave global tooling as untracked files.

**Rule:** The canonical dev-env worktree (`~/Git/dev-env`) must stay on `main` at all times.
All dev-env changes go through a separate worktree (use `EnterWorktree` or `git worktree add`).
Reason: `~/.claude/settings.json` and `~/.claude/scripts/` are symlinked/junctioned to the
canonical worktree's working tree — checking out a feature branch there makes newly merged hooks
and scripts invisible until the worktree returns to main. `dev-env-sync` will warn on every
prompt when this rule is violated.

**Routines note:** `dev-env/claude/routines/` is a directory junction pointing at
`~/.claude/scheduled-tasks/`, so the scheduler tool writes directly to the version-controlled
path. After creating a new routine, commit it to dev-env under `claude/routines/`.

**Routine authoring — sync-to-main preamble.** Any routine that reads repo-resident files
(a skill, a context file, a queue file) at run time must invoke the `sync-routine-worktree`
skill as Step 0, before reading any of those files. Scheduled tasks fire into Claude-managed
worktrees whose branches were cut from whatever `main` was at worktree creation; without an
explicit sync the routine reads stale files or aborts because a recently-merged file is missing
on the worktree branch. The sync skill handles fetch, branch-class-aware sync
(Claude-managed worktree / `main` / other), file-existence verification, and
abort-with-push-notification on conflict. See
`claude/skills/sync-routine-worktree/SKILL.md` and
`claude/routines/nightly-cover-letters/SKILL.md` for the canonical pattern. Rationale:
`docs/adr/013-sync-routine-worktree-skill.md`.

**Doc-reconciliation checkpoint** (three moments, same as ADR-warrant): (1) immediately after
a plan is approved; (2) immediately after `gh pr create` returns; (3) immediately before
`gh pr merge`. At each checkpoint, ask: does this change add, remove, rename, or modify the
behavior of a skill, hook, script, or routine? If yes, verify that `README.md` and any
project-specific reference docs listed in the project's Documentation Maintenance table are
updated in this PR. **If warranted updates are missing, add them before merging.** Rationale:
`docs/adr/019-doc-reconciliation-enforcement.md`.

**Downstream artifacts that name specific dev-env skills/hooks/routines** (update in the same PR
as a rename or retirement):

- `tech-leadership-reference/ai-adoption/ai-adoption-readiness-framework.md` — Appendix C names
  `/propose`, `/review`, `/journal-compose`, `/research`, and the `prune-stale-worktrees` and
  nightly journal compose routines as live-state evidence.

**Repo path:** `C:/Users/brown/Git/dev-env`

## GitHub Project

All new dev-env issues must be added to the **Dev Env** project and given an Impact rating and
Why description before work begins.

**Project IDs:**
- Project number: `3`, owner: `brownm09`
- Project node ID: `PVT_kwHOAjEKvM4BWKFe`

**Field IDs:**

| Field | ID | Options |
|---|---|---|
| Status | `PVTSSF_lAHOAjEKvM4BWKFezhRgkMY` | Todo=`f75ad846`, In Progress=`47fc9ee4`, Done=`98236657` |
| Impact | `PVTSSF_lAHOAjEKvM4BWKFezhRgkNc` | High=`08de2558`, Medium=`6320e8a6`, Low=`d8a85c2f` |
| Why | `PVTF_lAHOAjEKvM4BWKFezhRgkN0` | (text) |

> **Single-select option mutation hazard (applies to every project, not just dev-env).**
> Running `updateProjectV2Field` with `singleSelectOptions` is a full replacement — passing
> the existing options unchanged still produces new option IDs and drops every item's prior
> assignment for that field. Validated against lifting-logbook on 2026-05-08: an
> Observability epic addition wiped assignments on all 89 project items despite passing the
> full option list. Recovery precedent:
> [lifting-logbook#203](https://github.com/brownm09/lifting-logbook/issues/203).
>
> **Procedure (mandatory before adding/removing/renaming any single-select option on any project):**
>
> 1. Snapshot current per-item assignments to a git-tracked file under the project repo's
>    `.claude/backups/`.
> 2. Commit the snapshot.
> 3. Run the mutation with the full desired option list.
> 4. Update the project's `CLAUDE.md` option-ID table with the regenerated IDs in the same PR.
> 5. Restore assignments by re-issuing `gh project item-edit` for each item, mapping snapshot
>    option name → new option ID.
>
> If a mutation runs without a prior snapshot commit, stop and recover from the latest snapshot
> before any other work.

**Impact guidelines:**

| Level | Meaning |
|---|---|
| High | Causes manual recovery work or token waste on every occurrence |
| Medium | Recurs periodically or silently degrades correctness over time |
| Low | Nice-to-have; low frequency or easily worked around |

**Workflow — automated via PostToolUse hook:**

After `gh issue create` succeeds, the `post-tool-use.py` hook fires automatically and:

1. Adds the issue to project #3 (`gh project item-add`).
2. Exits with code 2, printing the exact `gh project item-edit` commands to set Impact and Why.

**Run those commands immediately — before any file edits.** Do not proceed to implementation
until both Impact and Why are set.

**Fallback (if the hook did not fire or the item-add failed):** run the three steps manually:

```bash
# Requires project scope — add once if needed: gh auth refresh -s project

# 1. Add issue to project, capture item ID
TMPFILE="C:/Users/brown/.claude/scratch/tmp_item_$$.json"
gh project item-add 3 --owner brownm09 --url <issue-url> --format json > "$TMPFILE"
ITEM_ID=$(node -e "const d=JSON.parse(require('fs').readFileSync('$TMPFILE','utf8')); console.log(d.id);")
rm -f "$TMPFILE"

# 2. Set Impact
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkNc \
  --single-select-option-id <option-id>   # 08de2558=High  6320e8a6=Medium  d8a85c2f=Low

# 3. Set Why (one sentence — the cost of not fixing it)
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTF_lAHOAjEKvM4BWKFezhRgkN0 \
  --text "<why this matters>"
```

To look up an item ID (e.g., when moving to In Progress or Done in a new session):

```bash
TMPFILE="C:/Users/brown/.claude/scratch/tmp_item_$$.json"
gh project item-list 3 --owner brownm09 --format json --limit 1000 > "$TMPFILE"
ITEM_ID=$(node -e "
  const d=JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));
  const item=d.items.find(i=>i.content&&i.content.number===<N>);
  console.log(item.id);
")
rm -f "$TMPFILE"
```

**Move to In Progress when work begins:**

```bash
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkMY \
  --single-select-option-id 47fc9ee4
```

**Move to Done after PR merges:**

```bash
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkMY \
  --single-select-option-id 98236657
```

## Documentation Maintenance

When a PR modifies any of the paths below, update the listed reference docs **in the same PR**.

| Change | Required updates |
|---|---|
| Add / remove / rename a skill in `claude/skills/` | Skills table in `README.md` + `docs/REFERENCE.md` Skills section |
| Add / remove / rename a script in `claude/scripts/` or `claude/hooks/` | Hooks table in `README.md` + `docs/REFERENCE.md` Hooks section |
| Add / remove / rename a routine in `claude/routines/` | Routines table in `README.md` + `docs/REFERENCE.md` Routines section |
| Change `hook-config.json` schema (new field, removed field, type change) | Configuration subsection in `docs/REFERENCE.md` |
| Change a skill's invocation syntax or options | Skill entry in `docs/REFERENCE.md` |
| Rename or move any file linked in `README.md` or `docs/REFERENCE.md` | Update the link in both files |
