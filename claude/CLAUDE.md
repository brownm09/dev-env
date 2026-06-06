<!-- SOURCE OF TRUTH: C:/Users/brown/Git/dev-env/claude/CLAUDE.md -->
<!-- ~/.claude/CLAUDE.md is a symlink to this file. Edit here, not at ~/.claude/. -->
<!-- To commit: cd C:/Users/brown/Git/dev-env && git add claude/CLAUDE.md && git commit -->

# Claude Code — Global Configuration

This file is loaded automatically in every session, across all projects.
Project-specific CLAUDE.md files extend these conventions — they do not repeat them.

> **ADRs:** The design decisions behind the rules in this file are recorded in [`docs/adr/`](../docs/adr/INDEX.md) in the dev-env repo. Consult the relevant ADR before overriding any rule, hook, skill, or config.

---

## Durable Preferences & Memory

Agent memory (`~/.claude/projects/.../memory/`) is a private cache, not the source of truth. It is invisible to the user and to everyone else who works in these repos and processes, and it is not reliably consulted at the moments it matters.

- **Any durable user preference or workflow rule committed to memory must also be documented in the version-controlled repo** — in the appropriate `CLAUDE.md` (global or project) or project docs — **or, at minimum, captured in a GitHub issue.** Never let a standing instruction live *exclusively* in memory.
- Do this **in the same session** the preference is stated. When you write the memory entry, link it back to where it is recorded in the repo so the two stay connected.
- Memory may still hold session-local or fast-changing context; this rule governs durable, cross-session preferences and rules — the things a human collaborator would need to know to work the way the user expects.

See [ADR-038](../docs/adr/038-durable-preferences-documented-in-repo.md).

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

Every project CLAUDE.md **must** also include a `## Observability` section describing the project's logging/observability convention — the logger and levels used, structured vs. plain output, where errors and traces go, and what the Observability audit dimension should verify for that project. Projects with no runtime to instrument (content/docs repos) must say so explicitly and name the equivalent verification (e.g. reference-integrity or link-check scripts). The `## Observability` section is what the *Plan-then-optimize → Pass 3* Observability dimension defers to — **if no `## Observability` section exists, note its absence in the plan and ask the user to add one; do not block the PR on it** (advisory, like the `## Testing` reminder — the gate is this CLAUDE.md rule, not a hook).

---

## Git Workflow

- **Create an issue before changing files.** `gh issue create` describing the problem/goal, not the implementation, before any edit. Single-line change: ask first; anything longer warrants an issue without prompting. Exception: engineering-journal `draft/YYYY-MM-DD` branches. Every PR references the issue via `Closes #N`.
- **Test before PR.** Before `gh pr create`, run the project's `## Testing` command; tests must pass (or the failure be explained). Put what was tested + the `Tests: N passed, N skipped, N failed (duration)` line + outcome in the PR body. **No `## Testing` section → stop and ask the user to add one before opening the PR.** Also run, before `gh pr create`:
  - **Coverage gate** — add tests for new testable behavior, or document the deferral in the PR body ([ADR-022](../docs/adr/022-test-coverage-gate-before-pr.md)).
  - **Suppression + test-integrity greps** from `## Code Quality` — any new suppression, skip marker, deleted test, lowered threshold, or bypass flag needs a PR-body justification or it blocks the PR; a non-zero skipped count needs a per-skip justification ([ADR-026](../docs/adr/026-suppression-policy.md), [ADR-029](../docs/adr/029-test-integrity-policy.md)).
  - **Pre-existing failure check** — if `baseline_test_failure_tracking` is on, run `baseline-tests diff`: new failures block; pre-existing failures in touched files are fixed inline (≤ ~20 LOC / ~15 min) or filed ([ADR-030](../docs/adr/030-baseline-test-failure-policy.md)).
- **ADR-warrant check** at three checkpoints — (1) after a plan is approved / first edit, (2) after `gh pr create`, (3) before `gh pr merge`. Warranted when the change touches a rule/hook/skill/settings documented in `claude/`, restructures a `claude/` directory, sets a workflow rule other CLAUDE.md files reference, or has rationale `git log` won't recover. Scan `docs/adr/INDEX.md` tags first; open ADR files only on a tag match. Global rules → dev-env [`docs/adr/`](../docs/adr/INDEX.md); project decisions → that project's `docs/adr/`. **Write the ADR + update `INDEX.md` before merging a qualifying change.** For clearly ADR-worthy multi-file work, create the template at checkpoint 1 ([ADR-011](../docs/adr/011-adr-warrant-check.md)).
- **Never commit directly to `main`.** Branch + PR always. Exception: local-only repos with no remote.
- **Branch naming:** `feat/`, `fix/`, `config/`, `chore/`, `draft/` — match the repo's convention.
- **Branch creation in squash-merge repos:** use `new-branch <name>` (sources `~/.claude/scripts/new-branch.sh`) or `git checkout -b <name> origin/main`. Never cut from a squash-merged branch (its commits are gone from main; rebase fails). Verify `git merge-base HEAD origin/main` equals `git rev-parse origin/main`.
- **Verify branch before editing and before every commit** with `git branch --show-current` — worktrees and `git checkout` shift context silently. If wrong: switch if no edits are on disk; else `git stash`, switch, `git stash pop`.
- **PR first, then merge.** Open the PR immediately after pushing; don't prompt the user to run `gh pr create`.
- **Write the journal stub immediately after `gh pr create`** — defer and a corrupted session loses all context. Then report the PR URL, prompt `/compact`, and after compaction run `/review <PR-URL> --post-comment`. **Address all findings — fixed-in-PR or filed-and-linked, never "left as-is"** — and merge in the same session. Each finding's disposition (fixed in `<sha>` | filed `#N`) goes in a "Review findings disposition" PR-body section; the `pre-merge-findings-gate` hook blocks merge until it's present, and `/review` applies the `reviewed-by-claude` label ([ADR-028](../docs/adr/028-all-findings-merge-gate.md), [ADR-039](../docs/adr/039-merge-gate-findings-enforcement.md)).
- **Fix everything, always — never ask which findings to address.** Resolve all `/review` / `/code-review` findings as part of the same work; default to fixing in-PR, file-and-link only when genuinely out of scope. Prefer the root-cause fix over the smaller band-aid. Escalate only for a product/design decision the code can't settle or a breach of the *Code Quality → Fix errors on encounter* scope guard ([ADR-028 → Addendum](../docs/adr/028-all-findings-merge-gate.md)).
- **Write a stub on PR merge** — a merge is a session boundary. After `gh pr merge`, the `PostToolUse` hook emits a `### Usage Snapshot (post-merge)` block; include it verbatim in the stub (preserved in journal Section 7). Same session as the PR open: update the existing stub, append a `prs_closed:[N]` manifest line, remove the PR from `open-prs.jsonl`. New session: update the opening stub in place for minor merges, or write a new stub for substantial follow-up. Either way set `prs_closed:[N]` and remove from `open-prs.jsonl`.
- **Multiple PRs/merges in one session:** defer stub writing until after the last `gh pr create` / `gh pr merge`; produce one consolidated stub. Never write an intermediate stub between PR opens or merges.
- **PR closed without merging:** the stub was already written at creation; stopping is optional.
- **After merging:** move the linked project board item to Done (command is project-specific — see the project's CLAUDE.md; for dev-env, its GitHub Project section). When a work stream completes (milestone, issue group, feature sequence), move the item from Active Work to Shipped in the project roadmap — don't leave roadmaps contradicting shipping history.
- **Auto-merge is off by design.** Every merge is in-session via `gh pr merge --squash --delete-branch` after the post-PR checklist (`/review`, ADR-warrant, doc-reconciliation, board transition, roadmap move). `--auto` is allowed only after that checklist passes in-session, never fire-and-forget ([ADR-031](../docs/adr/031-auto-merge-disabled.md)).
- **PR branch state comes from the remote, not the local worktree.** Before judging whether a PR addressed findings or is mergeable: `git fetch origin <headRefName>`, then read via `git show origin/<headRefName>:<path>` — never the local tree, which may be stale or on another branch ([ADR-004](../docs/adr/004-pr-review-reads-from-remote.md)).
- **Runbooks** for merging a PR from a worktree, separate clones for independent parallel work, deleting a remote branch in web sessions ([ADR-035](../docs/adr/035-git-push-delete-web-session-constraint.md)), and one-time `core.hooksPath` setup are in [`docs/REFERENCE.md` → Git Workflow Runbooks](../docs/REFERENCE.md#git-workflow-runbooks).

---

## Dev-Env & Project Boards

Dev-env's own architecture (the `~/.claude/` symlink/junction map, the worktree-on-`main` rule, routine authoring) and its GitHub Project IDs, field IDs, and board procedures live in the **dev-env project `CLAUDE.md`** (repo root) — they are project-specific and must not load into every project. Each project's `CLAUDE.md` owns its own `## Testing` commands and board configuration; the global "Test before PR" rule defers to that section.

**GitHub Projects — single-select option mutation hazard (applies to every project).** Running `updateProjectV2Field` with `singleSelectOptions` is a full replacement — passing the existing options unchanged still produces new option IDs and drops every item's prior assignment for that field. Validated against lifting-logbook on 2026-05-08: an Observability epic addition wiped assignments on all 89 project items despite passing the full option list. Recovery precedent: [lifting-logbook#203](https://github.com/brownm09/lifting-logbook/issues/203).

Procedure (mandatory before adding/removing/renaming any single-select option on any project):

1. Snapshot current per-item assignments to a git-tracked file under the project repo's `.claude/backups/`.
2. Commit the snapshot.
3. Run the mutation with the full desired option list.
4. Update the project's CLAUDE.md option-ID table with the regenerated IDs in the same PR.
5. Restore assignments by re-issuing `gh project item-edit` for each item, mapping snapshot option name → new option ID.

If a mutation runs without a prior snapshot commit, stop and recover from the latest snapshot before any other work.

---

## Code Quality

### Fix errors on encounter

Fix any error you encounter in any project, including issues unrelated to the task at hand — do not step around a broken thing and leave it broken.

- **Truly unrelated errors go in a separate PR** (still follow the issue-before-changes and test-before-PR rules — the fix is normal work, not an exception to process).
- **Scope guard:** before taking on an unrelated fix, estimate whether it would increase the scope of the current work by more than ~75%. If it would, stop and discuss with the user before proceeding rather than absorbing it silently.
- This comes up most often in lifting-logbook.

See [ADR-038](../docs/adr/038-durable-preferences-documented-in-repo.md).

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

### Dependency and lockfile policy

In any npm repo, `package-lock.json` must stay in sync with the dependency ranges declared in `package.json`. When they drift, `npm ci` refuses to install (it requires an in-sync lockfile) and CI fails at the install step with a cryptic `EUSAGE` error — and `main` can go red. The motivating incident is lifting-logbook #432/#433, where clerk-backend, clerk-shared, and react drift reddened `main` and required reactive recovery PRs. See [ADR-036](../docs/adr/036-lockfile-drift-prevention.md) for the full defense-in-depth rationale.

**Rule 1 — Edit a dependency, regenerate the lockfile, commit both together.**
Any change that adds, removes, or changes the version range of a dependency in a `package.json` (root or workspace) **must** run `npm install` and commit the regenerated `package-lock.json` in the **same** change. Never commit a `package.json` dependency edit without its lockfile update.

**Rule 2 — Pre-PR lockfile-sync check (required before `gh pr create` when any `package.json` changed).**
Run this from the repo root and confirm it exits clean before opening the PR:
```bash
npm install --package-lock-only --ignore-scripts && git diff --exit-code package-lock.json
```
A non-empty diff means the lockfile is out of sync — run `npm install`, commit the result, and re-run the check. The same check runs automatically in the global `pre-push` hook (it blocks the push on drift) and should run in each repo's CI as an early step; the pre-PR run catches it before either fires.

**Scope note.** Applies to npm/`package-lock.json` repos. Yarn (`yarn.lock`) and pnpm (`pnpm-lock.yaml`) have equivalent drift failure modes but different regeneration commands (`yarn install`, `pnpm install --lockfile-only`) — extend the check per repo before relying on it there.

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

**Plan-then-optimize before acting:** Any task involving an Agent spawn, a skill invocation, reads across more than one file, or a switch to a new primary objective within the same session (e.g., moving from a `/review` or other skill output to addressing findings, or from one issue to another) requires this protocol. State a numbered plan first, then apply three explicit revision passes before taking any action.

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

**Pass 3 — Risk-dimension audit:** before acting, the plan must address each of these six dimensions explicitly. For any that don't apply, state **"N/A — \<reason\>"** rather than omitting it. The bar is *stating the decision*, not adding work everywhere.

1. **Testing** — coverage for new behavior (defers to the project `## Testing` section).
2. **Observability** — what is logged/traced at boundaries and on failure (defers to the project `## Observability` section).
3. **Security** — authz, input validation, secret handling, sensitive-data/PII exposure.
4. **Resilience / failure modes** — error handling, timeouts, fallbacks, and how the change is rolled back / reverted.
5. **Performance** — data-access patterns (N+1), hot paths, payload/bundle size.
6. **Data integrity & migrations** — schema changes, multi-tenant isolation, reversibility.

**Accessibility** is audited whenever the change touches UI. Each project's CLAUDE.md may declare additional project-specific gates (e.g. lifting-logbook: OTel trace correlation, raw-SQL spans, LLM data scrubbing; career-playbook: `validate.sh`, briefing regeneration, artifact-schema parity) — the audit defers to those.

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
4. Create `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` (see [REFERENCE → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals))
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Append a manifest entry to `sessions/<project>/YYYY-MM-DD.manifest.jsonl` (see [REFERENCE → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals))
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
6. Append a manifest entry to `sessions/<project>/YYYY-MM-DD.manifest.jsonl` (see [REFERENCE → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals))
7. `git add sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD.manifest.jsonl sessions/<project>/open-prs.jsonl`, `git commit -m "draft: YYYY-MM-DD session N"`, `git push`
   *(omit `open-prs.jsonl` from the add command if it was not modified this session)*

**File formats, stub template, and recovery:** the `.manifest.jsonl` and `open-prs.jsonl` schemas
(referenced in the workflow steps above), the stub-file template, the canonical 11-section compose
structure, and the draft-branch recovery procedure are documented in
[`docs/REFERENCE.md` → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals).

**End of day (last session):** Run `/journal-compose --force` — it discovers all stubs via manifest
(or glob fallback), merges them, produces the canonical 11-section document, and auto-merges the PR.
`--force` is required when composing today's branch; past-date composition
(`/journal-compose YYYY-MM-DD` for a prior day) does not need the flag.

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
