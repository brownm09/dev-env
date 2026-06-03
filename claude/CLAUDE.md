<!-- SOURCE OF TRUTH: C:/Users/brown/Git/dev-env/claude/CLAUDE.md -->
<!-- ~/.claude/CLAUDE.md is a symlink to this file. Edit here, not at ~/.claude/. -->
<!-- To commit: cd C:/Users/brown/Git/dev-env && git add claude/CLAUDE.md && git commit -->

# Claude Code — Global Configuration

This file is loaded automatically in every session, across all projects.
Project-specific CLAUDE.md files extend these conventions — they do not repeat them.

> **ADRs:** The design decisions behind the rules in this file are recorded in [`docs/adr/`](../docs/adr/INDEX.md) in the dev-env repo. Consult the relevant ADR before overriding any rule, hook, skill, or config.

---

## Platform & Environment

- **OS:** Windows 11, Git Bash terminal
- **Node:** 20.11.1 (managed by nvm for Windows; `.nvmrc` is set — run `nvm use` at session start if not already active)
- **Package manager:** npm (workspaces where applicable)
- **`jq` is NOT available.** Use `node -e` with a temp file for JSON parsing:
  ```bash
  TMPFILE="C:/Users/brown/.claude/scratch/tmp_$$.json"
  some-command --format json > "$TMPFILE"
  node -e "
    const d = JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));
    console.log('VAR=' + d.field);
  "
  rm -f "$TMPFILE"
  ```
- **Never use `/tmp/`** for temp files — Node.js on Windows cannot resolve Git Bash Unix paths.
- **Scratch directory:** `C:/Users/brown/.claude/scratch/` — all processing tmp files (`gh` output, JSON parsing intermediaries, etc.) go here regardless of which project is active. Never write tmp files into a project repo working directory.
- **`gh` CLI** is available and authenticated. The `project` scope must be added separately when needed: `gh auth refresh -s project`.
- **Prefer Git Bash** over PowerShell for scripting — PowerShell handles arrays and arithmetic differently and has caused failures in this environment.

---

## CLI Scripting Checklist

Before writing a `gh` or other CLI automation script:

1. Run `<command> --help` first to confirm flag names and syntax
2. Confirm which JSON tools are available (`jq` is NOT available — use `node -e`)
3. Write temp files to `C:/Users/brown/.claude/scratch/`, not `/tmp/` or a project repo directory
4. Check whether any additional `gh` auth scopes are needed

---

## Per-Project CLAUDE.md Requirements

Every project CLAUDE.md **must** include a `## Testing` section specifying the command(s) used to verify a solution before opening a PR. Examples:

```markdown
## Testing
Run `npm test` to execute the full test suite.
Run `npm run build` first if touching compiled output.
```

```markdown
## Testing
Run `pytest` from the repo root. Integration tests require `DATABASE_URL` set.
```

If the project has no automated tests, the section must say so explicitly and describe the manual verification steps instead. The `## Testing` section is used by the global "Test before PR" rule — **if no `## Testing` section exists in the project CLAUDE.md, stop before running `gh pr create` and ask the user to add one. Do not open the PR until the section is present.**

---

## Git Workflow

- **Create an issue before changing files.** When a user's question or request will result in file changes, create a GitHub issue first with `gh issue create` — describe the problem or goal, not the implementation. Do this before writing any code or editing any files. For a single-line change, ask the user whether an issue is warranted before creating one; anything longer than one line warrants an issue without prompting. Exception: engineering-journal draft branches (`draft/YYYY-MM-DD`) may omit an issue. Every PR must then reference the issue via a `Closes #N` line in the PR body.
- **Test before PR.** Before running `gh pr create`, execute the project's test command defined in `## Testing` in the project CLAUDE.md. Tests must pass (or the failure must be explained and documented). Include what was tested, the `Tests: N passed, N skipped, N failed (duration)` summary line, and the outcome in the PR body. **If no `## Testing` section exists in the project CLAUDE.md, stop and ask the user to add one — do not open the PR until it is present.**
  - **Coverage gate.** Also ask whether the change introduces testable behavior not covered by existing tests. If yes, add tests before creating the PR, or explicitly document in the PR body why they are deferred (which tests, what tracks them, why deferral is acceptable). Enforced behaviorally by the author and by `/review` Step 2d (see [ADR-022](../docs/adr/022-test-coverage-gate-before-pr.md)).
  - **Suppression check.** Also run the pre-PR suppression grep from `## Code Quality` — any new suppression without a PR-body justification blocks the PR.
  - **Test integrity check.** Also run the pre-PR test-integrity grep from `## Code Quality` — any new skip marker, deleted test, lowered coverage threshold, or bypass flag without a PR-body justification blocks the PR. The `Tests: N passed, N skipped, N failed (duration)` summary line is required in the PR body; a non-zero skipped count requires a per-skip justification (see [ADR-029](../docs/adr/029-test-integrity-policy.md)).
  - **Pre-existing failure check.** If the project enables `baseline_test_failure_tracking` in `.claude/hook-config.json`, also run `baseline-tests diff`. New failures block the PR; pre-existing failures in files this branch modifies must be fixed inline (≤ ~20 LOC / ~15 min) or filed and referenced in the PR body (see [ADR-030](../docs/adr/030-baseline-test-failure-policy.md)).
- **ADR-warrant check.** Evaluate whether the change warrants an architectural decision record at three explicit checkpoints: (1) immediately after a plan is approved (post-`ExitPlanMode`), or at the start of the first file edit for sessions without an explicit planning phase; (2) immediately after `gh pr create` returns; and (3) immediately before `gh pr merge`. A change warrants an ADR when any of the following hold: it changes a rule, hook, skill, or settings value documented in `claude/`; it introduces or restructures a directory under `claude/` (skills, hooks, scripts, routines); it establishes or changes a workflow rule that other CLAUDE.md files reference; or its rationale would be hard to recover from `git log` alone six months later. ADRs for global rules go in dev-env [`docs/adr/`](../docs/adr/INDEX.md); ADRs for project-specific decisions go in that project's `docs/adr/`. **If warranted and not yet written, write the ADR and update `INDEX.md` before merging — never merge a qualifying change without an ADR record.**
  - **Warrant check lookup:** Scan `docs/adr/INDEX.md` tags first — the Tags column covers every ADR's domain keywords. If no tag matches the change type, the warrant check requires no additional file reads. Only open individual ADR files when a tag match suggests a possible overlap or conflict.
  - **Proactive template (opt-in):** When checkpoint 1 fires and the change is clearly ADR-worthy, create the ADR template file on the branch immediately using the next available ADR number and the `NNN-kebab-case-title.md` naming convention, rather than waiting until checkpoint 3. Fill in context and the decision rationale as work proceeds; the ADR is 80% complete by the time `gh pr create` runs, with no extra end-of-session step. This approach is preferable for complex multi-file changes where the full rationale is known upfront. For exploratory work where the decision may not crystallize until checkpoint 2 or 3, write the ADR at whichever checkpoint it becomes clear.
- **Never commit directly to `main`.** All changes go through a branch and PR, regardless of repo.
- **Branch naming:** `feat/`, `fix/`, `config/`, `chore/`, `draft/` prefixes — match the convention already in use in the repo.
- **PR first, then merge.** Open the PR immediately after pushing the branch; do not prompt the user to run `gh pr create` themselves.
- **Write the journal stub immediately after `gh pr create`.** Do not defer until merge — if `/compact` fails or the session is corrupted, all context is permanently lost. Write the stub, then report the PR URL and prompt the user to run `/compact`. Once compaction is complete, immediately run `/review <PR-URL> --post-comment`. Address all findings (blocking and non-blocking) and merge in the same session (see [ADR-028](../docs/adr/028-all-findings-merge-gate.md)). Rationale: `/compact` reduces implementation context to a small summary before review fires, preserving most token savings without a session break. The review skill applies the `reviewed-by-claude` label; a PR without that label has not been reviewed.
  - **Multiple PRs in one session:** if the plan opens more than one PR, defer stub writing until after the last `gh pr create` completes. Produce one stub covering all `prs_opened` entries and `open-prs.jsonl` additions together. Do not write an intermediate stub between PR opens.
- **Write a stub on PR merge.** A merge is a session boundary that requires stub coverage.
  - **Usage snapshot (post-merge):** After `gh pr merge` completes, the `PostToolUse` hook runs `usage-snapshot.py`, which queries `https://api.anthropic.com/api/oauth/usage` and parses the session JSONL for the top-5 costliest exchanges. The hook emits a `### Usage Snapshot (post-merge)` markdown block to the session. Include this block verbatim in the stub body. `/journal-compose` will preserve it in Section 7 (Token Usage).
  - **Multiple merges in one session:** if the plan includes more than one PR merge, defer all stub work until after the last merge completes. Produce one consolidated stub covering every `prs_closed` entry and `open-prs.jsonl` removal together. Do not write an intermediate stub or stop between merges.
  - **Same session as PR open:** the existing stub covers the full lifecycle. Before the final commit below, update the stub's session block with any of the following that arose after the PR was opened: decisions made, discoveries surfaced, dependencies introduced, or deviations from documented process encountered. Then append a second manifest line with `prs_closed: [N]`, remove the PR from `open-prs.jsonl`, commit, and stop.
  - **New session (merge happens later):** choose the cheaper option:
    - **Update the opening stub in place** if the merge session adds only minor content (e.g., the merge itself with no follow-up work) — avoids the token cost `/journal-compose` pays to read and merge two stubs.
    - **Write a new stub** if the merge session involves substantial new content (review responses, follow-up fixes, etc.) — the PR grouping heuristic will combine them under one H2.
  - Either way: set `prs_closed: [N]` in the relevant manifest entry, remove the PR from `open-prs.jsonl`, and stop.
- **PR closed without merging.** If a PR is closed without merging, the stub was already written at PR creation. Stopping is optional — the session may continue if follow-up work remains.
- **After merging a PR:** move the linked project board item to Done. The exact command (project ID, field ID, option ID) is project-specific — each project's CLAUDE.md provides it. For dev-env, see the GitHub Project section below.
- **When a work stream completes** (a milestone, closed issue group, or multi-session feature sequence): update the project's active roadmap or work-tracking document — move the completed item out of Active Work and into a Shipped section (or equivalent). Do not leave roadmaps in a state that contradicts the actual shipping history.
- **Exception:** Local-only repos with no remote may commit to main directly.
- **Branch creation in squash-merge repos:** Use `new-branch <name>` (source `~/.claude/scripts/new-branch.sh` in `.bashrc`) or `git checkout -b <name> origin/main` explicitly. Never cut from a branch that has been squash-merged — its commits no longer exist on main and a rebase will fail. Verify with `git merge-base HEAD origin/main` — output should equal `git rev-parse origin/main`.
- **Separate clones for fully independent parallel work.** Worktrees share the `.git` ref database (branches, stash, FETCH_HEAD, packed-refs). When two sessions share no branches or PRs and you want full `.git/` isolation, use a local clone instead of a worktree:
  ```bash
  git clone --local C:/Users/brown/Git/<repo> C:/Users/brown/Git/<repo>-2
  ```
  `--local` hardlinks the object store so the clone is nearly instant with no extra disk cost for existing objects. Use worktrees (default) when sessions share common context; use a separate clone only when the two workstreams are completely independent.
- **Merging a PR developed in a worktree:** Run `gh pr merge --squash --delete-branch` directly from the worktree. Do not call `ExitWorktree` first — it is session-bound and becomes a no-op after `/compact`, which is the common case. `gh` will fail to check out main locally and will fail to delete the local branch (the worktree holds the branch checked out), but the remote merge and remote branch delete complete successfully — those errors are benign. The local worktree directory and branch are cleaned up by the weekly `prune-merged-worktrees.py` run.
- **Deleting a remote branch in Claude Code web sessions:** Never use `git push origin --delete <branch>` in a web/cloud session — the sandbox HTTP git proxy cannot proxy a delete-only send-pack from the shallow clone and the push aborts with a sideband disconnect (`the remote end hung up unexpectedly`). Delete the ref through the GitHub REST API instead, which works in both local and web sessions: `gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"`. `gh pr merge --squash --delete-branch` is unaffected because its branch delete already uses the API path. Root cause and upstream fix: [ADR-035](../docs/adr/035-git-push-delete-web-session-constraint.md) / [REFERENCE.md → Platform Constraints](../docs/REFERENCE.md#platform-constraints).
- **Verify branch before making changes and before every commit.** Run `git branch --show-current` (1) before making any edits in a session and (2) immediately before each `git commit`. Do not assume the branch is correct because it was correct earlier — worktrees, `git checkout`, and multi-repo work can silently shift context. A `UserPromptSubmit` hook already emits the active worktree list on every prompt when multiple worktrees are open. If the branch is wrong: if no edits are on disk yet, switch branches immediately; if edits are already on disk but not committed, run `git stash`, switch to the correct branch, then `git stash pop` before proceeding.
- **Auto-merge is off by design.** Every merge happens in-session via `gh pr merge --squash --delete-branch` after the post-PR-create checklist completes (`/review`, ADR-warrant check, doc-reconciliation check, project board transition, roadmap shipped-row move). Per-PR `gh pr merge --auto --squash` is permitted only after that checklist passes in-session — never as fire-and-forget after `gh pr create`. Rationale: [ADR-031](../docs/adr/031-auto-merge-disabled.md).
- **Pre-push hook wiring (one-time setup):** Before setting, check for an existing value: `git config --system core.hooksPath` and `git config --global core.hooksPath`. If a system-level path exists (enterprise-managed hooks), migrate its hooks into `~/.claude/hooks/` rather than overriding. If another tool (Husky, Lefthook) owns the global value, coordinate rather than overwrite — two tools cannot share `core.hooksPath`. Once clear: `git config --global core.hooksPath ~/.claude/hooks`. The hook chains to any per-repo `.git/hooks/pre-push` so existing repo-level hooks are preserved.
- **PR branch state must come from the remote, not the local worktree.** Whenever checking whether a PR has addressed review findings or is ready to merge: (1) run `git fetch origin <headRefName>` first, (2) read files via `git show origin/<headRefName>:<path>`, never from the local working tree. The local worktree may be behind or on a different branch entirely — reading it produces false "still outstanding" results. (Incident: lifting-logbook#86 commit `50f7ac6` fixed all blockers but a follow-up check read the wrong branch and reported three still open.)

---

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

**Rule:** Any addition or modification to a dev-env-owned artifact — new hook script, new skill, settings change, CLAUDE.md edit — must be committed to `brownm09/dev-env` via branch and PR before the session ends. Do not leave global tooling as untracked files.

**Rule:** The canonical dev-env worktree (`~/Git/dev-env`) must stay on `main` at all times. All dev-env changes go through a separate worktree (use `EnterWorktree` or `git worktree add`). Reason: `~/.claude/settings.json` and `~/.claude/scripts/` are symlinked/junctioned to the canonical worktree's working tree — checking out a feature branch there makes newly merged hooks and scripts invisible until the worktree returns to main. `dev-env-sync` will warn on every prompt when this rule is violated.

**Routines note:** `dev-env/claude/routines/` is a directory junction pointing at `~/.claude/scheduled-tasks/`, so the scheduler tool writes directly to the version-controlled path. After creating a new routine, commit it to dev-env under `claude/routines/`.

**Routine authoring — sync-to-main preamble.** Any routine that reads repo-resident files (a skill, a context file, a queue file) at run time must invoke the `sync-routine-worktree` skill as Step 0, before reading any of those files. Scheduled tasks fire into Claude-managed worktrees whose branches were cut from whatever `main` was at worktree creation; without an explicit sync the routine reads stale files or aborts because a recently-merged file is missing on the worktree branch. The sync skill handles fetch, branch-class-aware sync (Claude-managed worktree / `main` / other), file existence verification, and abort-with-push-notification on conflict — routines pass `REPO`, `VERIFY_FILE`, and `PREFIX` and treat the return as a guard. See `claude/skills/sync-routine-worktree/SKILL.md` and `claude/routines/nightly-cover-letters/SKILL.md` for the canonical pattern. Rationale: `docs/adr/013-sync-routine-worktree-skill.md`.

**Doc-reconciliation checkpoint** (three moments, same as ADR-warrant): (1) immediately after a plan is approved; (2) immediately after `gh pr create` returns; (3) immediately before `gh pr merge`. At each checkpoint, ask: does this change add, remove, rename, or modify the behavior of a skill, hook, script, or routine? If yes, verify that `README.md` and any project-specific reference docs listed in the project's Documentation Maintenance table are updated in this PR. For dev-env, the table is in `dev-env/CLAUDE.md`. **If warranted updates are missing, add them before merging.** Rationale: `docs/adr/019-doc-reconciliation-enforcement.md`.

**Downstream artifacts that name specific dev-env skills/hooks/routines** (update in the same PR as a rename or retirement):

- `tech-leadership-reference/ai-adoption/ai-adoption-readiness-framework.md` — Appendix C names `/propose`, `/review`, `/journal-compose`, `/research`, and the `prune-stale-worktrees` and nightly journal compose routines as live-state evidence.

**Repo path:** `C:/Users/brown/Git/dev-env`

---

## GitHub Project

All new dev-env issues must be added to the **Dev Env** project and given an Impact rating and Why description before work begins.

**Project IDs:**
- Project number: `3`, owner: `brownm09`
- Project node ID: `PVT_kwHOAjEKvM4BWKFe`

**Field IDs:**

| Field | ID | Options |
|---|---|---|
| Status | `PVTSSF_lAHOAjEKvM4BWKFezhRgkMY` | Todo=`f75ad846`, In Progress=`47fc9ee4`, Done=`98236657` |
| Impact | `PVTSSF_lAHOAjEKvM4BWKFezhRgkNc` | High=`08de2558`, Medium=`6320e8a6`, Low=`d8a85c2f` |
| Why | `PVTF_lAHOAjEKvM4BWKFezhRgkN0` | (text) |

> **Single-select option mutation hazard (applies to every project, not just dev-env).** Running `updateProjectV2Field` with `singleSelectOptions` is a full replacement — passing the existing options unchanged still produces new option IDs and drops every item's prior assignment for that field. Validated against lifting-logbook on 2026-05-08: an Observability epic addition wiped assignments on all 89 project items despite passing the full option list. Recovery precedent: [lifting-logbook#203](https://github.com/brownm09/lifting-logbook/issues/203).
>
> **Procedure (mandatory before adding/removing/renaming any single-select option on any project):**
>
> 1. Snapshot current per-item assignments to a git-tracked file under the project repo's `.claude/backups/`.
> 2. Commit the snapshot.
> 3. Run the mutation with the full desired option list.
> 4. Update the project's CLAUDE.md option-ID table with the regenerated IDs in the same PR.
> 5. Restore assignments by re-issuing `gh project item-edit` for each item, mapping snapshot option name → new option ID.
>
> If a mutation runs without a prior snapshot commit, stop and recover from the latest snapshot before any other work.

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

**Run those commands immediately — before any file edits.** Do not proceed to implementation until both Impact and Why are set. This is the same forcing function as the test-before-PR rule: the hook output is visible in the session and must be acted on.

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

---

## Testing

Run the following from the repo root to verify all hook scripts are free of syntax errors:

```bash
py -3 -c "import ast,sys; [ast.parse(open(f,encoding='utf-8').read(),f) for f in sys.argv[1:]]" claude/scripts/*.py
```

`ast.parse` is used instead of `py_compile` because the latter writes `.pyc` files into `claude/scripts/__pycache__/` as a side effect (see [dev-env#276](https://github.com/brownm09/dev-env/issues/276)). Neither `-B` nor `PYTHONDONTWRITEBYTECODE=1` suppresses that — they only affect implicit caching on import, not explicit compilation.

On Windows, `python3` resolves to the Microsoft Store stub — use `py -3` (the Windows Python Launcher). See [ADR-007](../docs/adr/007-hook-command-invocation.md).

For docs-only changes to `claude/CLAUDE.md`: run `grep -n 'date -u' claude/CLAUDE.md` and confirm every match is in an internal operational artifact context (lock files, log timestamps) — not in stub filename or branch name descriptions.

---

## Code Quality

### Suppression policy

A *suppression* is any of the following: `!` (non-null assertion), `?? null` to coerce away `undefined`, `// @ts-ignore`, `// @ts-expect-error`, `eslint-disable` (line or block), or an explicit type cast used to silence an error rather than for a legitimate narrowing.

**Rule 1 — No suppression without justification.**
Every suppression that lands in a PR must be accompanied by a PR-body note explaining why a proper fix was not appropriate. The note must name the specific lines and state the invariant the suppression relies on.

**Rule 2 — Pre-existing errors must be filed, not silenced.**
Before adding a suppression, determine whether the error predates the current branch:
```bash
git stash && npm test 2>&1 | grep -i error; git stash pop
```
If the error exists on the base branch, do **not** suppress it. File a GitHub issue (or batch it into an existing one) and leave the error unmodified. Only suppressions for errors introduced by the current branch are ever permissible — and only with Rule 1 justification.

**Rule 3 — Pre-PR suppression check (required before `gh pr create`).**
Run this from the repo root and review every match before opening a PR:
```bash
git diff origin/main -- . | grep -E '(ts-ignore|ts-expect-error|eslint-disable|![.[]|!\s*[;,)]|\?\? null)'
```
The pattern covers end-of-expression assertions (`!;`, `!,`, `!)`) and chained access (`!.`, `![`). It does not catch every `!` — also scan added lines in the diff manually for any remaining `!` that could be a suppression.

If the output is non-empty:
- Each line must map to a Rule 1 justification note in the PR body, **or**
- The suppression must be removed and replaced with a proper fix.

A PR that adds suppressions with no PR-body justification is not mergeable.

See [ADR-026](../docs/adr/026-suppression-policy.md) for rationale.

### Test integrity policy

A *test integrity violation* is any of the following: adding a skip marker (`it.skip`, `xit`, `xdescribe`, `test.skip`, `describe.skip`, `.todo`, `pending`), deleting a test file or `describe`/`it` block, lowering a coverage threshold in `jest.config.*` / `.nycrc` / `vitest.config.*` / equivalent, adding `--passWithNoTests` / `--bail` / `--testPathIgnorePatterns` to a test invocation or CI command, or hardcoding implementation values to satisfy a specific test input rather than a general contract.

The Test Coverage Gate (ADR-022) guards against *missing* tests on new behavior. This policy guards against *degrading* existing tests to manufacture a green run.

**Rule 1 — No integrity violation without justification.**
Every violation that lands in a PR must be accompanied by a PR-body note naming the specific tests/thresholds and stating why removal or degradation is appropriate (e.g., "deleted tests for the removed `/legacy-export` endpoint").

**Rule 2 — Skipped counts must be visible.**
The test-before-PR run must emit a summary of the form `Tests: N passed, N skipped, N failed (duration)`. Include this line verbatim in the PR body under the Testing section. A non-zero skipped count requires a PR-body note explaining why each skip is acceptable. When the project has no automated tests, state that explicitly in place of the summary line.

**Rule 3 — Pre-PR test-integrity check (required before `gh pr create`).**
Run this alongside the suppression grep:
```bash
git diff origin/main -- . | grep -E '(it\.skip|xit\(|xdescribe\(|test\.skip|describe\.skip|\.todo\(|pending\(|passWithNoTests|--bail|testPathIgnorePatterns)'
```
Also check for deleted test files and lowered coverage thresholds:
```bash
git diff --diff-filter=D --name-only origin/main -- '*.test.*' '*.spec.*' 'tests/**' 'e2e/**'
git diff origin/main -- jest.config.* .nycrc vitest.config.* | grep -E 'threshold|coverage'
```

**Scope note.** The grep patterns above target JavaScript/TypeScript test frameworks (Jest, Vitest, Mocha). For pytest, Go `testing`, or Rust projects, extend the patterns to cover the language's idioms (`@pytest.mark.skip`, `pytest.skip(`, `t.Skip(`, `#[ignore]`, etc.) before running. A clean grep against JS-only patterns in a non-JS repo is not evidence the policy was satisfied.

**Known false-positive classes.** Two patterns over-match by design — review matches manually:
- `--bail` matches any CLI bail flag, including non-test-runner contexts.
- The threshold/coverage grep flags **any** change to those fields, including threshold *increases* (which are improvements, not violations). Only decreased thresholds are violations.

If any pattern matches:
- Each line must map to a Rule 1 justification note in the PR body, **or**
- The violation must be reverted.

A PR that adds integrity violations with no PR-body justification is not mergeable.

See [ADR-029](../docs/adr/029-test-integrity-policy.md) for rationale.

### Pre-existing test failure policy

The Suppression Policy and Test Integrity Policy guard against *new* and *degraded* tests, respectively. This policy guards against *inherited red state* — tests that were already failing when the branch was cut and that drift forever across branches because each session correctly declines to fix unrelated breakage. See [ADR-030](../docs/adr/030-baseline-test-failure-policy.md) for rationale and rejected alternatives.

A *pre-existing failure* is a failing test whose fingerprint (`sha1(file + "::" + test_name + "::" + first_line_of_error)`) was already present in the branch-start baseline snapshot. A *new failure* is one that does not appear in the baseline.

**Rule 1 — Baseline at branch creation.**
Per-project opt-in via `"baseline_test_failure_tracking": true` in `.claude/hook-config.json`. When enabled, `new-branch <name>` (see the Git Workflow → Branch creation in squash-merge repos bullet above) automatically runs `baseline-tests snapshot` immediately after `git checkout -b`. The snapshot is written to `C:/Users/brown/.claude/scratch/baseline_<repo>_<branch>.json`. Branches cut via raw `git checkout -b` skip the snapshot; the pre-PR hook will surface the missing baseline as an advisory.

The hook-config also reads an optional `"test_command"` field (default `npx jest --json --silent`) — the command must emit Jest `--json` output on stdout. Projects whose `npm test` script wraps Jest through turbo/lerna will need to override this to call Jest directly.

**Rule 2 — Fix-on-touch threshold.**
When `baseline-tests diff` classifies a pre-existing failure as `preexisting-touched` (failure file is in the branch's modified set):

- **Fix inline** if the fix is **≤ ~20 LOC or ≤ ~15 minutes** by Claude's judgment.
- **Otherwise** file a GitHub issue (or append to a rolling "Pre-existing test failures" tracking issue) and reference it in the PR body. The failure stays in the baseline for future branches.

The ~20 LOC / ~15 min figure is a judgment-based proxy, not a hard contract — the goal is to prevent both yak-shaving and silent inheritance. Pre-existing failures classified as `preexisting-untouched` are noted in the PR body but are not blocking.

**Rule 3 — Pre-PR baseline diff (required before `gh pr create`).**
Run `baseline-tests diff` from the repo root. The script classifies failures into three groups and prints them:

```
=== NEW failures (block PR — must fix) ===
=== PRE-EXISTING failures in touched files (fix-on-touch or file an issue) ===
=== PRE-EXISTING failures in untouched files (note in PR body) ===
```

If any `new` failures appear, do **not** open the PR — fix them first. The script exits 1 in this case. Any `preexisting-touched` entries must be either fixed inline (Rule 2) or filed; the PR body must list outstanding entries with a link to the tracking issue.

A PR that adds new failures or lists no PR-body justification for outstanding `preexisting-touched` entries is not mergeable.

**Scope note.** The first implementation supports Jest only. Pytest, Go `testing`, and Rust have different JSON output formats and each needs its own parser — a clean run in a non-Jest repo is not evidence the policy applies. The opt-in flag should stay off in repos whose test runners are not yet supported.

---

## Hook Safety

See [docs/REFERENCE.md — Hooks → Authoring rules](../docs/REFERENCE.md#authoring-rules) for the three hook invariants (atomic commits, safe-exit guard, no `bash -c` wrappers).

---

## Model Selection

Route tasks to the least powerful model that can handle them reliably:

| Task type | Model |
|-----------|-------|
| Mechanical: search, format, summarize, diff, rename | Haiku |
| Standard dev: feature implementation, debugging | Sonnet |
| Complex: architectural decisions, novel problems, multi-file reasoning, writing test code, `/review` skill | Opus |

Default to Sonnet when uncertain. Never use Opus for tasks a Haiku prompt handles correctly on the first try.

---

## Context & Token Efficiency

**Directory reads:** When reading from a directory with more than ~3 files, read `INDEX.md` or a top-level manifest first. Load individual files on demand, not by globbing the whole directory. Flag to the user before reading more than 5 files in a single pass.

**Session length:** A `UserPromptSubmit` hook warns at turn 50 (and every 25 turns thereafter). When warned, consider running `/clear` or `/compact` if the task scope has shifted — accumulated context inflates cost toward the end of long sessions. The default threshold can be overridden per-project: add `"turn_threshold": N` to `.claude/hook-config.json`.

**Mechanical operations:** If a task is fully scriptable with known inputs, write the script rather than running an interactive session. Candidate operations: stale PR remediation, branch cleanup, rebase-and-merge sequences. Use `~/.claude/scripts/merge-stale-pr.sh` for engineering-journal stale draft PRs.

**Plan-then-optimize before acting:** Any task involving an Agent spawn, a skill invocation, reads across more than one file, or a switch to a new primary objective within the same session (e.g., moving from a `/review` or other skill output to addressing findings, or from one issue to another) requires this protocol. State a numbered plan first, then apply two explicit revision passes before taking any action.

**Pass 1 — Token efficiency:** check:
- Sequential tool calls that can be parallelized
- `Agent` spawns: all independent subagents must go in a single message with `run_in_background: true` — no synchronous preflight agent that a parallel sibling will redo (root cause of dev-env#51)
- File reads that can be skipped by reading an index or manifest instead of globbing
- Data a downstream step will recompute anyway

**Pass 2 — Outcome correctness:** after the efficiency revision, verify the optimized plan still produces the intended result:
- No implicit ordering dependency was broken by parallelizing two steps
- No read was dropped that a later step actually depends on for its inputs
- No Agent scope was narrowed so far that it misses required context
- The final outputs (files written, PRs opened, commits made) match what the original plan intended
- If the plan includes multiple PR merges, the stub-writing step appears once, after the last merge — not once per merge

---

## Error Message Diligence

Error messages emitted by CI workflow guards, hooks, bots, and library exceptions are written by the *author of the guard* — they describe what the author thinks went wrong, not what actually went wrong. When a single check stands in for multiple upstream states (a job skipped because its `needs:` failed, a downstream output that defaults to empty when an earlier step never ran, a generic catch-block re-raise), the message can be confidently misleading. Restating that message as a diagnosis to the user, to a bot thread, or in a PR comment propagates the wrong root cause and produces follow-up corrections that cost more than the original verification would have.

Before acting on any automation- or library-emitted error message not covered by the Exemption below, complete three diligence steps:

1. **Locate the emitting line.** Find the file and line that printed the message. Read the conditional or `raise` site directly — do not infer it from the message text.
2. **Read the condition the code actually evaluates.** The literal expression (`if X != 'true'`, `if not config.get('key')`) is the ground truth. The message is a human-readable label for that expression and may have drifted.
3. **Distinguish the upstream signal from the message's narrative.** If the evaluated condition can be false for multiple reasons — secret missing, upstream job skipped, network 5xx during a fetch, schema mismatch — trace one level up before accepting the message's framing. For CI: check the parent job's status and `needs:` chain. For hooks/scripts: check the input that produced the falsy value, not just the falsy value itself.

When uncertain after the three steps, surface the uncertainty explicitly: "the guard printed X; I have not yet confirmed the underlying condition" rather than restating X as fact. This applies equally to user-facing messages, PR comments, and bot replies.

**Anti-pattern:** quoting an emitted error message back to the user as the diagnosis without having read the emitting line. The message is evidence of *what was reported*, not evidence of *what is true*.

**Exemption:** local errors where the message is reliable by construction — syntax errors, file-not-found at a path you just wrote, static type-checker errors that name the offending symbol (TypeScript `TS2304`, Pyright `reportUndefinedVariable`, etc.) — do not require the three-step trace. The rule targets guard messages that summarize composite upstream state.

**Rationale incident.** lifting-logbook [PR #395](https://github.com/brownm09/lifting-logbook/pull/395) (2026-06-01). The `staging.yml` deploy-prereq guard printed a "Staging Clerk secret key is not configured" error and pointed to `docs/deploy.md` Step 4. The actual upstream chain was a transient 504 from Artifact Registry in `build-images` → `deploy-api` skipped because of `needs: build-images` → empty `clerk_configured` output → integration-tests' `!= 'true'` check fired the misconfig message. The secret was already correctly set. Both the github-actions bot and I reported the wrong root cause until the user pushed back. Full trace: [PR #395 comment](https://github.com/brownm09/lifting-logbook/pull/395#issuecomment-4594434736). See [ADR-034](../docs/adr/034-error-message-diligence.md).

---

## Documentation and Citations

When writing or updating any architectural documentation (ADRs, design docs, READMEs):

- **Cite primary sources, not summaries.** Three categories:
  - *Official documentation* — for technology and framework choices (NestJS, Next.js, Prisma, etc.)
  - *Specifications* — for protocol and standard choices (IETF RFCs, OASIS specs, GraphQL spec, OpenID Connect)
  - *Foundational writings* — for architectural patterns (Cockburn's Hexagonal Architecture, Uncle Bob's Clean Architecture, Fowler articles)
- **Regulatory references** (GDPR, HIPAA, SOC 2) must link to the primary regulatory source, not a summary or blog post.
- **When making any technical recommendation in a response** — a technology, Claude Code feature, workflow pattern, or architectural approach — include a primary source link in that same response. If no authoritative primary source exists, explicitly label the recommendation as based on observed behavior or heuristic.

---

## Engineering Journal

After each session (or at natural breakpoints for long sessions), create or update a session
transcript in `brownm09/engineering-journal`.

**Repo path:** `C:/Users/brown/Git/engineering-journal`

Each project's CLAUDE.md defines its **project journal path** (e.g., `sessions/lifting-logbook/`).
Use that path wherever `sessions/<project>/` appears below.

---

### Composition rules

- **`/journal-compose` is a dedicated-session operation.** Never run it alongside other tasks.
  If composition is requested in a session that has already processed other work, respond:
  > "Journal composition must run in its own session. Open a new Claude Code session and invoke `/journal-compose` there."
  Then stop — do not compose.

- **Never compose proactively.** If a `draft/YYYY-MM-DD` branch is encountered during any
  work (e.g., while running `git branch`, checking git status, or reading branch output),
  first check whether it exists on the remote before warning:
  ```bash
  git -C C:/Users/brown/Git/engineering-journal ls-remote --heads origin draft/YYYY-MM-DD
  ```
  A local-only draft branch means the PR was already squash-merged and the local ref was not
  cleaned up — ignore it silently. Only if the remote branch exists, emit a single line at
  the next natural pause:
  > "Incomplete journal detected — run `/journal-compose` in a dedicated session."
  Then continue with the user's actual request. Do not read stubs, do not compose, do not
  ask whether to compose.

- **PR grouping heuristic.** When two or more stubs share a PR number (one stub has it in
  `prs_opened`, another in `prs_closed`), compose them under a single H2 dialogue section
  rather than producing a separate H2 per stub. Annotate with "→ merged in session N" (where
  N is the 1-based ordinal of the closing stub for that day) at the end of the section. Any
  stub written on a day where `open-prs.jsonl` shows the same PR as open should also be grouped
  under that H2, even if neither `prs_opened` nor `prs_closed` is set for that PR in its
  manifest entry — this covers PRs that span more than two sessions. This prevents the composed
  journal from fragmenting create-iterate-review sequences into unrelated-looking sections.

---

### Stub file workflow

Each session writes an isolated stub file — no shared mutable draft. This eliminates write
contention when multiple sessions run in parallel. Slug is determined at day end.

**Branch:** `draft/YYYY-MM-DD` — created at the first session of the day, merged to main at day end.

**Stub filename:** `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`
where `YYYY-MM-DD` is the **local calendar date** and `HHMMSS` is the **local start time**
of the session (`date +%Y-%m-%d` / `date +%H%M%S`). Local time is used so stub filenames
and branch names always share the same calendar day. UTC is reserved for internal
operational artifacts (compose lock files, log file timestamps).

**First session of the day:**
1. `git -C C:/Users/brown/Git/engineering-journal checkout main && git pull`
2. `git -C C:/Users/brown/Git/engineering-journal checkout -b draft/YYYY-MM-DD`
   **Branch validation (required):** Confirm the new branch was cut from `main`, not a previous
   draft branch. Both commands below must print the same SHA — if they differ, delete the local
   branch (`git -C C:/Users/brown/Git/engineering-journal branch -D draft/YYYY-MM-DD`) and
   re-run steps 1–2:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal merge-base HEAD origin/main
   git -C C:/Users/brown/Git/engineering-journal rev-parse origin/main
   ```
3. Read `sessions/<project>/open-prs.jsonl` if it exists — include its PR list as session context before starting work.
4. Create `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` (see stub structure below)
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Append a manifest entry to `sessions/<project>/YYYY-MM-DD.manifest.jsonl` (see Manifest format below)
7. `git add sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD.manifest.jsonl sessions/<project>/open-prs.jsonl`, `git commit -m "draft: YYYY-MM-DD session 1"`, `git push -u origin draft/YYYY-MM-DD`
   *(omit `open-prs.jsonl` from the add command if it was not modified this session)*

**Subsequent sessions:**
1. `git -C C:/Users/brown/Git/engineering-journal pull origin draft/YYYY-MM-DD`
2. Read `sessions/<project>/open-prs.jsonl` if it exists — include its PR list as session context before starting work.
3. Find the most recent stub and read only its `<!-- next-session-context -->` paragraph:
   ```bash
   ls C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.stub.md | sort | tail -1
   ```
4. Create a new `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` with the current session block
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Append a manifest entry to `sessions/<project>/YYYY-MM-DD.manifest.jsonl` (see Manifest format below)
7. `git add sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD.manifest.jsonl sessions/<project>/open-prs.jsonl`, `git commit -m "draft: YYYY-MM-DD session N"`, `git push`
   *(omit `open-prs.jsonl` from the add command if it was not modified this session)*

**Manifest format (`YYYY-MM-DD.manifest.jsonl`):**

One JSON line per session, appended after the token comment is known (end of session):
```bash
echo '{"stub":"YYYY-MM-DD_HHMMSS.stub.md","topic":"<H2 heading>","tokens":{"input":N,"output":N,"cost":N},"prs_opened":[],"prs_closed":[]}' \
  >> "C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD.manifest.jsonl"
```
(`YYYY-MM-DD` and `HHMMSS` are local time — same as the stub filename spec above.)

- `prs_opened`: PR numbers opened during this session (e.g., `[54]`). Empty array if none.
- `prs_closed`: PR numbers reviewed/merged during this session (e.g., `[54]`). Empty array if none.
- `priorities` (optional): array of items to surface on the top-level README "Start here" dashboard.
  Each entry: `label` (required string, short title); `ref` (optional string, `owner/repo#N` or
  freeform key used for dedupe); `why` (optional string, one-sentence rationale). Example:
  `"priorities":[{"label":"Staging gate fix","ref":"lifting-logbook#346","why":"blocks next deploy"}]`.
  `/journal-compose` aggregates these across all projects (deduped by `ref`, capped at 5) — see
  [ADR-032](https://github.com/brownm09/dev-env/blob/main/docs/adr/032-journal-start-here-dashboard.md).

The manifest lets `/journal-compose` see the session count, topics, token data, and PR lifecycle
without reading individual stubs. It is advisory: if the manifest is missing or has fewer entries
than the stub glob, stubs are authoritative. Never commit the manifest separately from its stubs
(include it in the same `git add` / commit step).

**Open-PR tracking file (`sessions/<project>/open-prs.jsonl`):**

Tracks PRs whose full lifecycle (open → review → merge) spans multiple sessions. Lives in the
engineering-journal repo; carried forward from day to day via the draft branch merge to main.

Schema — one JSON line per open PR:
```json
{"pr":54,"url":"https://github.com/brownm09/dev-env/pull/54","topic":"<H2 heading from stub>","stub":"YYYY-MM-DD_HHMMSS.stub.md","opened":"YYYY-MM-DD"}
```

- `stub`: the stub filename that opened this PR — used by `/journal-compose` to cross-reference the opening session when a PR spans multiple days.

- **When a session opens a PR:** append a line and commit it alongside the stub (see step 7 above).
- **When a session merges/closes a PR:** remove the matching line using `node -e`, then commit:
  ```bash
  node -e "
    const fs = require('fs');
    const path = 'C:/Users/brown/Git/engineering-journal/sessions/<project>/open-prs.jsonl';
    if (!fs.existsSync(path)) process.exit(0);
    const kept = fs.readFileSync(path,'utf8').trim().split('\n')
      .filter(l => l && JSON.parse(l).pr !== <PR_NUMBER>);
    if (kept.length) fs.writeFileSync(path, kept.join('\n') + '\n');
    else fs.unlinkSync(path);
  "
  ```
  If the last line is removed, the script deletes the file rather than leaving it empty.
- `/journal-compose` preserves this file unchanged in the merge-to-main commit so it carries forward to the next day.

**Draft branch recovery:**

If `draft/YYYY-MM-DD` was merged or deleted before end of day (e.g., by an accidental mid-day `/journal-compose` run):

1. Create a fresh recovery branch from `origin/main`:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal fetch origin
   git -C C:/Users/brown/Git/engineering-journal checkout -b draft/YYYY-MM-DD-recovery origin/main
   ```
2. Copy all session files from the stale local branch onto the recovery branch:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD -- sessions/
   git -C C:/Users/brown/Git/engineering-journal commit -m "draft: recover YYYY-MM-DD stubs (post-kerfuffle)"
   git -C C:/Users/brown/Git/engineering-journal push -u origin draft/YYYY-MM-DD-recovery
   ```
   This also removes from `main` any stubs that were accidentally merged (they will be deleted from the branch
   and composed into a journal when `/journal-compose` runs).
3. If any stub content was committed directly to `main` (e.g., via ad-hoc chore/* PR), revert each accidental commit
   via a PR to `main` and then re-add the observation to the recovery branch.
4. Write the stub for the current session normally — commit to `draft/YYYY-MM-DD-recovery` instead of `draft/YYYY-MM-DD`.
5. When running `/journal-compose`, ensure the engineering-journal working tree is on `draft/YYYY-MM-DD-recovery`.

**Why `draft/YYYY-MM-DD-recovery` instead of `draft/YYYY-MM-DD`:** The pre-push hook blocks pushing to a branch that already has a merged PR (to prevent stale-branch noise). The `-recovery` suffix bypasses the check while keeping the date visible.

If orphaned `chore/*` or `late-stub/*` stub PRs are open (sessions that fell back to ad-hoc branches when the draft was missing):
```bash
# Close the ad-hoc PR — its content was already included via the sessions/ checkout above
gh -R brownm09/engineering-journal pr close <N> \
  --comment "Content recovered onto draft/YYYY-MM-DD-recovery — closing without merge."
```

If the engineering-journal working tree is simply on the wrong branch (not the draft branch), no recovery needed:
```bash
git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD
git -C C:/Users/brown/Git/engineering-journal pull
```

**End of day (last session):**
1. Run `/journal-compose --force` — it discovers all stubs via manifest (or glob fallback), merges
   them, produces the canonical 11-section document, and auto-merges the PR. `--force` is required
   when composing today's branch; past-date composition (`/journal-compose YYYY-MM-DD` for a prior
   day) does not need the flag

---

### Stub structure

Each stub file contains exactly one session block:

```
<!-- stub: YYYY-MM-DD HHMMSS -->

<!-- opening-brief (first stub of the day only) -->
Opening brief: <paste the Next Session Context from the previous day's published journal verbatim;
               use "First session — no prior context." only if this is the project's very first entry>

<!-- session: <slug> -->
## <Topic>
...
<!-- tokens: input=12,450 output=3,200 cost≈$0.08 -->
<!-- next-session-context -->
<one paragraph — for the next session to read and open with>
```

The `<!-- opening-brief -->` block appears **only in the first stub of the day**.
Subsequent stubs begin directly at `<!-- session: <slug> -->`.

---

### Canonical 11-section structure (composed once at day end)

1. Header block (Topic, Repo/Branch, Issues closed, PRs merged)
2. Table of Contents
3. Opening Brief (paste the Next Session Context from the previous day verbatim)
4. Key Decisions (bullet list with links to sections, issues, PRs, ADRs)
5. Dialogue sections (one H2 per task or topic, drawn from draft)
6. Open Items / Next Steps (checkbox list)
7. Token Usage (per-session breakdown tables: model, est. input tokens, est. output tokens,
   est. cost — drawn from `<!-- tokens: ... -->` comments in the draft; when comments are
   absent use retroactive estimates based on session scope, labeled as "retroactive estimate";
   close with a Combined totals table)
8. Token Optimization Suggestions (2–4 per-session observations grouped under a `### Session N`
   heading; close with a `### Cross-Session Patterns` subsection for generalizable findings
   that apply across multiple sessions)
9. Next Session Context (the final `<!-- next-session-context -->` block from the stubs)
10. Reflection (gaps, risks, strategic questions — written last)
11. Further Reading (1–3 primary sources per session that explain the reasoning behind key
    decisions; intended for deliberate study between sessions — link + one sentence on why
    it matters)

---

### Update triggers

**Project journal** (`sessions/<project>/`):
- **Auto-create stub without user prompt on these events:**
  - PR opened — follow the Stub file workflow immediately after `gh pr create`. If no further work is planned (e.g., waiting on CI or human review), stop after writing the stub.
  - PR merged (including auto-merge) — write or update a stub for the merge session (see Git Workflow → Write a stub on PR merge), then stop.
  - PR closed without merging — stub was already written at PR creation; stopping is optional (see Git Workflow → PR closed without merging)
  - PR updated (push to a branch with an open PR) — when the hook reminder fires after `git push`, update the engineering journal immediately: if a stub already exists for the current session, update it in place; otherwise create a new stub. Document what changed in this session (review findings addressed, approach decisions, what was pushed).
- The following do **not** auto-create a stub — they are not session boundaries:
  - Review-only sessions (`/review <PR-URL>`)

- Add to the current session's stub when a strategic decision is made mid-session
- Compose and publish the daily document at end of last session of the day

**Meta journal** (`sessions/meta/`):
- When a `CLAUDE.md` is modified — record what changed, why, and which session prompted it
- When a new platform constraint is discovered — record the symptom, root cause, and fix pattern
- When a workflow failure mode is discovered and remediated — record the symptom, root cause,
  and fix pattern
- When a cross-project convention is established — record the convention and which projects it
  affects
- When the journal structure itself changes — record the new section, placement, and rationale
- When a new canonical reference repo or external resource is identified — record the resource
  and its role
- When a `brownm09/dev-env` PR is merged — record what changed (script, skill, settings, or
  CLAUDE.md), why it was introduced, and which project or session prompted it

**Full journal conventions:** See [`brownm09/engineering-journal`](https://github.com/brownm09/engineering-journal) → `sessions/meta/2026-04-05-workflow-and-journal-setup.md`
