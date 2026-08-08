<!-- SOURCE OF TRUTH: C:/Users/brown/Git/dev-env/claude/CLAUDE.md -->
<!-- ~/.claude/CLAUDE.md is a symlink to this file. Edit here, not at ~/.claude/. -->
<!-- To commit: cd C:/Users/brown/Git/dev-env && git add claude/CLAUDE.md && git commit -->

# Claude Code — Global Configuration

This file is loaded automatically in every session, across all projects.
Project-specific CLAUDE.md files extend these conventions — they do not repeat them.

> **ADRs:** The design decisions behind the rules in this file are recorded in [`docs/adr/`](../docs/adr/INDEX.md) in the dev-env repo. Consult the relevant ADR before overriding any rule, hook, skill, or config.

---

## Durable Preferences & Memory

Agent memory (`~/.claude/projects/.../memory/`) is a private cache, not the source of truth. It is invisible to the user and to everyone else who works in these repos and processes, and it is not reliably consulted at the moments it matters. The instructions (`CLAUDE.md` and project docs) are loaded every session and visible to everyone — that is where durable rules must live.

- **Never let a durable preference or workflow rule live only in memory.** Whenever you commit one to a `user`/`feedback`/`project` memory file, **pair it — in the same session — with a GitHub issue whose explicit job is to immortalize it into the instructions** (the appropriate `CLAUDE.md`, global or project, or project docs). Link that issue from **both** the memory body and its `MEMORY.md` pointer so the two never drift apart.
- **Search before filing — check for an existing issue first.** Before filing the immortalization issue, search both open and closed issues for content that already covers the same rule: `gh issue list --search "<keywords>" --state all`. Run this against the repo the issue will actually be filed in — dev-env for a global rule, the project's own repo otherwise — via `--repo <owner>/<repo>` when that differs from the session's current working directory; `gh issue list` defaults to the cwd's repo, and a cwd/target mismatch here silently searches the wrong repo's issues and produces a false "no match." A match means the rule is already tracked — reference that issue from the memory body instead of filing a fresh one, and extend it with a comment if it's still open and missing detail this occurrence adds. Only file a new issue when the search comes back empty. Skipping this step risks a near-duplicate landing right next to the original: [dev-env#610](https://github.com/brownm09/dev-env/issues/610) and [dev-env#627](https://github.com/brownm09/dev-env/issues/627) independently documented the identical `EnterWorktree` cross-repo-targeting gap on the same day, and the overlap was only discovered when the session implementing the second issue found the first one's bullet already in place.
- **Filing the issue is the floor, not the finish.** The issue exists to drive the rule *into* the instructions — not to substitute for doing so. Prefer to make the instruction edit immediately and close the issue in the same session; a deferred issue is the backstop that keeps the rule from being forgotten, not a place to park it indefinitely.
- **Transient context is exempt.** Session-local or fast-changing notes (open-PR lists, in-flight working state) may live in memory and expire there — no issue required. This rule governs only durable, cross-session rules: the things a human collaborator would need to know to work the way the user expects.
- A non-blocking write-time hook (`memory-write-advisory.py`) reminds you when a memory file is written without such a link, the `/memory-audit` skill reconciles memory against the instructions on demand, and the `weekly-memory-audit` routine runs every Monday to sweep existing memory across all projects for never-ported durables (auto-filing deduped *promote* issues in the correct repo) and report stale/drift findings without modifying any memory file.

See [ADR-038](../docs/adr/038-durable-preferences-documented-in-repo.md), [ADR-048](../docs/adr/048-memory-immortalization-issue-pairing.md), [ADR-069](../docs/adr/069-weekly-memory-audit-routine.md).

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
- **Disk-full (ENOSPC):** `C:` saturating to 0 GB surfaces *indirectly* as truncated `node_modules` (npm exits 0 but a native binary is partial), not an obvious "disk full" error. If an install fails with a confusing downstream error, run `df -h /c` first. Recovery runbook + failure signature + dominant consumers: [`docs/REFERENCE.md` → Disk-Full (ENOSPC) Recovery](../docs/REFERENCE.md#disk-full-enospc-recovery). The `worktree-npm-install.py` gate now refuses a low-space install rather than truncate ([ADR-045](../docs/adr/045-pre-install-freespace-gate.md)).

---

## Tool Discovery

`ToolSearch` finds tools that are *deferred* — announced by name only in a system-reminder, needing a schema fetch before they're callable. It does not index every tool available in a session: many tools are already fully defined in the system prompt's tool list and never appear in `ToolSearch` results, by design — that's not a gap in the search, it's tools that don't need finding.

**Before concluding any tool is "unavailable in this session," check the full tool list already present in the system prompt first** — both directly-defined tools and the deferred-tool names surfaced in system-reminders. A zero-result `ToolSearch` only means the tool isn't in the *deferred* set; it says nothing about whether the tool is already directly callable. Incident: a session searched `ToolSearch("spawn_task")`, got nothing, wrongly concluded the tile-spawning tool was absent, and silently skipped a tile the tile-checkpoint rule required ([dev-env#754](https://github.com/brownm09/dev-env/issues/754)) — `mcp__ccd_session__spawn_task` was directly available the whole time. See [ADR-107](../docs/adr/107-toolsearch-is-not-a-tool-availability-check.md).

---

## CLI Scripting Checklist

Before writing a `gh` or other CLI automation script — or acting on what one reports:

1. Run `<command> --help` first to confirm flag names and syntax
2. Confirm which JSON tools are available (`jq` is NOT available — use `node -e`)
3. Write temp files to `C:/Users/brown/.claude/scratch/`, not `/tmp/` or a project repo directory
4. Check whether any additional `gh` auth scopes are needed
5. **An absence claim needs an absolute path — and the right ref.** Before concluding a file, pattern, or config is *not present*, re-run the check rooted at the repo root (an absolute path, or `git -C <root>`) and let its stderr through. `session-start-sync.py` now fetches automatically at session start ([ADR-130](../docs/adr/130-session-start-fetch-ff-only-or-warn.md)), so `origin/main` itself is more often fresh going into an investigation — but it fires once per session, not before each individual absence claim, so the four mechanisms below (and this whole rule) still apply mid-session. Four independent mechanisms each turn a partial view into output indistinguishable from a genuine repo-wide miss:
   - **cwd scoping.** `git ls-files`, `git ls-tree`, `git grep`, `find`, and `ls` report only what is under the cwd, and a plain `cd` from an *earlier, separate* Bash call can silently persist into later ones — so a subtree-scoped miss looks exactly like a repo-wide miss. The only tell is often a `../../` prefix in `git status --short`, which most commands never print. This is the inverse of the Git Workflow warning that cwd does *not* reliably persist after a worktree tool fires — assume neither, and scope every command explicitly. Incident: [dev-env#864](https://github.com/brownm09/dev-env/issues/864) — a persisted `cd apps/web` produced a confident "this repo tracks no lockfile anywhere," in a repo with a tracked `package-lock.json` and a CI step verifying it.
   - **Ref scoping.** The same miss happens on the *ref* axis, where an absolute path does not help: a working tree, `HEAD`, and `origin/main` each answer only for themselves, and none of them sees what an **open PR** already claims. Before claiming a name/number/identifier is free — an ADR number, a `## Testing` item, a migration or fixture filename — check the open-PR set too (`gh pr list`, or `gh pr diff <N> --name-only`), not just the checkout. Incident: `ls docs/adr/` and `INDEX.md` both showed 115 as the highest ADR, so 116 looked free; open PR [dev-env#863](https://github.com/brownm09/dev-env/pull/863) had already claimed it.
   - **Visibility blind spots.** `Glob` and plain `git status` silently skip gitignored files, and `git ls-tree` only shows committed content (never untracked files, ignored or not) — before declaring a directory empty or fully processed, verify with `ls -la` / `Get-ChildItem -Force` or `git status --ignored` instead.
   - **Suppressed failure.** Never pair an absence check with `2>/dev/null` — the failure you most need to see is the one it hides. MSYS path-conversion intermittently mangles `git show <ref>:<path>` into `origin\main;.github\…`; git exits non-zero, the redirect swallows the `fatal:`, and the empty output reads exactly like "the pattern is not present" ([dev-env#602](https://github.com/brownm09/dev-env/issues/602)). Prefix `MSYS_NO_PATHCONV=1`, or read the blob over the API: `gh api "repos/{owner}/{repo}/contents/<path>?ref=<ref>" -H "Accept: application/vnd.github.raw"`.
   - Related but distinct: a partial *pathspec commit you just made* can itself create a working-tree-vs-HEAD divergence (e.g. a rename where only the new path was committed) — see Engineering Journal → Stub file workflow → "An explicit pathspec can also drop half of a rename."
6. **A suspected mojibake/encoding corruption surfaced through a piped command needs a raw-bytes check before you trust it.** Piping matched or filtered text through an intermediate interpreter's stdin — e.g. `rg`'s matches relayed into `py -3 -c "..."` — introduces a decode step whose codepage doesn't necessarily match the source encoding: Python's stdin decode on this Windows/Git-Bash setup can default to a non-UTF-8 codepage (the console's preferred encoding) when text arrives via a Bash pipe from another process, even though the file on disk is correctly UTF-8-encoded. The result reads exactly like real corruption — a genuinely clean file's en dash (U+2013, UTF-8 bytes `E2 80 93`) decoded through such a pipe as three wrong codepoints, `â€"` (U+00E2, U+20AC, U+201C), the textbook signature of UTF-8 bytes misread as windows-1252. Before concluding the file itself is corrupted, verify against its raw bytes directly — `open(path, 'rb')` in Python, or equivalent — bypassing the pipe entirely; a pipe-relayed "corruption" that a direct byte read doesn't reproduce is a diagnostic-pipeline artifact, not a real defect. Incident: [dev-env#952](https://github.com/brownm09/dev-env/issues/952), surfaced mid-investigation of [career-playbook#1139](https://github.com/brownm09/career-playbook/issues/1139).

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

Projects that run process experiments **should** add an `## Experiments` section — a corpus catalog, an instrument registry naming each judge/check's known-good AND known-bad calibration references, the results home, and default tier triggers. The *Plan-then-optimize → Pass 3* Experimental-validity dimension and the `/experiment-audit` skill defer to it the way the Observability dimension defers to `## Observability`; **absence is advisory, not blocking** (see the global `## Experimental Rigor` section and [ADR-115](../docs/adr/115-experimental-rigor-protocol.md)).

---

## Git Workflow

- **Create an issue before changing files.** `gh issue create` describing the problem/goal, not the implementation, before any edit. Single-line change: ask first; anything longer warrants an issue without prompting. Exception: engineering-journal `draft/YYYY-MM-DD` branches. Every PR references the issue via `Closes #N`.
- **Check for an existing open PR before implementing.** Before starting implementation work on any issue — freshly filed or picked up from the backlog — confirm no open PR already addresses it: `gh pr list --search "<issue-number-or-keywords>" --state open`, or `gh issue view <N> --json` and check its linked-PRs/timeline. A match means the work is already in flight — **surface it to the user via `AskUserQuestion`** (closing, superseding, or consolidating onto an existing PR is a visible action, not one to resolve unilaterally) rather than silently duplicating the effort. This is the mirror image of two existing checks, not a repeat of either: *Durable Preferences & Memory → Search before filing* confirms no duplicate *memory-immortalization* issue exists before filing one; *CLI Scripting Checklist → Ref scoping* confirms the open-PR set before claiming an identifier (an ADR number, a filename) is *free*. Neither covers starting work on an issue that already has a fix in flight under an open PR — possibly filed against a different, older duplicate issue. Incident: a session fixing dev-env#918 discovered, only while writing the journal stub, that PR [dev-env#770](https://github.com/brownm09/dev-env/pull/770) (open since 2026-07-14) already fixed the identical bug via the older duplicate issue [dev-env#734](https://github.com/brownm09/dev-env/issues/734). See [ADR-125](../docs/adr/125-check-open-pr-before-implementing-issue.md).
- **Multi-PR decomposition: top-level issue + sub-issues.** When a prompt or initiative maps to multiple PRs, create a **top-level issue** first to capture the full scope and rationale, then a **sub-issue** for each individual PR (referencing the top-level issue in the sub-issue body so the hierarchy is navigable from both directions). Each PR's `Closes #N` references its sub-issue; the top-level issue is closed only after all sub-PRs have merged. Tiles spawned by the decomposing session must embed **both** issue references in their prompt — the top-level issue number/URL and the specific sub-issue being addressed — so the spawned session can maintain continuity without the user re-establishing context. ([ADR-059](../docs/adr/059-multi-pr-issue-hierarchy.md))
- **Test before PR.** Before `gh pr create`, run the project's `## Testing` command; tests must pass (or the failure be explained). Put what was tested + the `Tests: N passed, N skipped, N failed (duration)` line + outcome in the PR body. **No `## Testing` section → stop and ask the user to add one before opening the PR.** Also run, before `gh pr create`:
  - **Coverage gate** — add tests for new testable behavior, or document the deferral in the PR body ([ADR-022](../docs/adr/022-test-coverage-gate-before-pr.md)).
  - **Suppression + test-integrity greps** from `## Code Quality` — any new suppression, skip marker, deleted test, lowered threshold, or bypass flag needs a PR-body justification or it blocks the PR; a non-zero skipped count needs a per-skip justification ([ADR-026](../docs/adr/026-suppression-policy.md), [ADR-029](../docs/adr/029-test-integrity-policy.md)).
  - **Pre-existing failure check** — if `baseline_test_failure_tracking` is on, run `baseline-tests diff`: new failures block; pre-existing failures in touched files are fixed inline (≤ ~20 LOC / ~15 min) or filed ([ADR-030](../docs/adr/030-baseline-test-failure-policy.md)).
- **ADR-warrant check** at three checkpoints — (1) after a plan is approved / first edit, (2) after `gh pr create`, (3) before `gh pr merge`. Warranted when the change touches a rule/hook/skill/settings documented in `claude/`, restructures a `claude/` directory, sets a workflow rule other CLAUDE.md files reference, or has rationale `git log` won't recover. Scan `docs/adr/INDEX.md` tags first; open ADR files only on a tag match. Global rules → dev-env [`docs/adr/`](../docs/adr/INDEX.md); project decisions → that project's `docs/adr/`. **Write the ADR + update `INDEX.md` before merging a qualifying change.** For clearly ADR-worthy multi-file work, create the template at checkpoint 1 ([ADR-011](../docs/adr/011-adr-warrant-check.md)).
- **Never commit directly to `main`.** Branch + PR always. Exception: local-only repos with no remote.
- **Branch naming:** `feat/`, `fix/`, `config/`, `chore/`, `draft/` — match the repo's convention.
- **Branch creation in squash-merge repos:** use `new-branch <name>` (sources `~/.claude/scripts/new-branch.sh`) or `git checkout -b <name> origin/main`. Never cut from a squash-merged branch (its commits are gone from main; rebase fails). Verify `git merge-base HEAD origin/main` equals `git rev-parse origin/main`.
- **Verify branch before editing and before every commit** with `git branch --show-current` — worktrees and `git checkout` shift context silently. If wrong: switch if no edits are on disk; else `git stash`, switch, `git stash pop`. This extends to `gh pr create` and `gh pr merge` too: both silently infer their target branch/PR from the current checkout when not given one explicitly, and a session's tracked cwd/branch can revert without any error surfaced (e.g. after an intermittent Git Bash crash) — prefer passing `--head <branch>` explicitly to `gh pr create` rather than relying on implicit current-branch detection, as a cheap defense-in-depth measure regardless of root cause. `pre-commit-branch-check.py`, `pre-pr-create-check.py`, and `pre-merge-branch-check.py` show the current branch as a visible checkpoint at each of these three commands and flag a loud (non-blocking) warning when it differs from the repo/branch recorded after the session's last Bash call ([ADR-085](../docs/adr/085-bash-repo-branch-drift-detection.md); incident: [dev-env#573](https://github.com/brownm09/dev-env/issues/573)). A fourth checkpoint, `pre-bash-drift-check.py`, extends the same comparison to *every* Bash call rather than just those three commands — gated on elapsed time (≥60s since the last recorded call, not command content) so a drift affecting any other command (a `grep`, a test runner, a build) also gets flagged, at the cost of a `git rev-parse` subprocess only after a genuine gap. Motivated by a drift observed immediately after two long-running background `Agent` calls completed — a trigger shape the original three checkpoints don't cover at all ([ADR-101](../docs/adr/101-bash-drift-check-every-call.md); incident: [dev-env#682](https://github.com/brownm09/dev-env/issues/682)).
- **Worktree Bash commands must not `cd` to the main repo.** In a worktree session (cwd is `.claude/worktrees/<name>`), do **not** prefix Bash git/npm commands with `cd C:/Users/brown/Git/<repo>`. Edit/Read/Write operate on the worktree by absolute path, but a `cd`-ed Bash command runs git against the **main** repo — a different working dir on a different branch — creating split-brain: edits land in the worktree while commits, pulls, and `git status` act on main. Run git/npm in the worktree with the default cwd (no `cd`); use `git -C <path>` to touch another repo (e.g. engineering-journal). Tell: `git status` clean but Read shows uncommitted edits → two different checkouts ([ADR-066](../docs/adr/066-worktree-session-safety-rules.md); incident: [lifting-logbook#502](https://github.com/brownm09/lifting-logbook/pull/502)).
- **A bare local `main` ref can be silently stale — read `origin/main`, not `main`, from a worktree or compose flow.** `git fetch` only updates the `origin/main` remote-tracking ref; it does not fast-forward the local `main` branch ref, which only moves when something explicitly merges/resets/pulls it. A worktree session that runs `git show main:<path>` (or any other `main`-targeted read) can silently act on a commit several merges behind actual current state, with no error surfaced. This produced a false "my changes vanished" alarm mid-recovery — a just-merged PR's content appeared missing moments later because the read used the stale local ref, not `origin/main`. Use `git show origin/main:<path>` (or fast-forward local `main` first via `git fetch origin main:main`, safe only when `main` isn't checked out anywhere) whenever reading "current main state" from within a worktree, a `/journal-compose`-style compose flow, or any cwd that isn't itself actively tracking `main`. Incident: [dev-env#648](https://github.com/brownm09/dev-env/issues/648). `session-start-sync.py` now automates exactly this fetch-and-ff-or-warn at session start for any repo a session works in, backing this rule with a mechanical check rather than relying solely on remembering it every time ([ADR-130](../docs/adr/130-session-start-fetch-ff-only-or-warn.md), dev-env#966) — it fires once at session start, so a `main` ref can still go stale again mid-session; the manual `origin/main` discipline above still applies.
- **`EnterWorktree` targets the session's primary repo only — it cannot be pointed at a different repo.** Before calling it, compare the task's target repo against the session's own primary working directory (the `Primary working directory` line in the system prompt). When a session's primary working directory is repo A but the task needs isolated work in repo B, `EnterWorktree` still silently creates the new worktree under repo A; nothing about the call signals the mismatch, and the tool's own `ExitWorktree` counterpart is scoped to user-initiated exits only, so a session can't self-correct by backing out. For repo B, use `git worktree add <path> -b <branch> origin/<default-branch>` directly against repo B's path instead, and interact with it via explicit paths or `git -C <path>` — never `cd` there, per the bullet above ([dev-env#610](https://github.com/brownm09/dev-env/issues/610)).
- **Two more scoping traps compound the fallback above once you're doing manual cross-repo git.** First, once any worktree tool (`EnterWorktree`/`ExitWorktree`) has fired at least once in a session, the Bash tool's cwd does not reliably persist a plain `cd` across separate Bash calls afterward — observed directly as `Shell cwd was reset to <original session cwd>`. Prefix every subsequent Bash command touching a manually-created worktree with its own explicit `cd`/`-C`, even for back-to-back commands, rather than relying on cwd persistence for the rest of the session. Second, `-C <repo>` (and a leading `cd <path> &&`) scopes only the single command it's attached to — it does **not** propagate across `&&`, `;`, or `||` to later commands in the same invocation: `git -C <dev-env-path> fetch origin && git worktree add <path> -b <branch> origin/main` silently runs `worktree add` against the Bash tool's actual cwd, not the `-C` target, whenever that cwd is a different repo. (This single-invocation `cd <path> &&` prefix is not the persistent `cd` the bullet above warns against — it dies with the command it's chained to rather than lingering for later, separate Bash calls, which is exactly why it's a safe substitute for `-C` here.) Give every command in a multi-command invocation that targets a non-primary repo its own explicit `-C`/`cd` — or split into separate Bash calls — rather than assuming one scoping flag covers the whole chain. Either failure mode can create a worktree, branch, or commit in the wrong repo with no error surfaced; sanity-check the result of any `worktree add`/`checkout`/`clone` before trusting it (`git remote -v`, or a commit-hash comparison against the target repo's known `origin/<default-branch>` tip). ([dev-env#627](https://github.com/brownm09/dev-env/issues/627)) A persisted `cd` also has a third failure mode beyond running a command in the wrong repo — it silently scopes `git ls-files`/`git grep`/`find` to a subtree, so a miss reads as repo-wide absence; see *CLI Scripting Checklist* item 5 ([dev-env#864](https://github.com/brownm09/dev-env/issues/864)).
- **Never mutate git state directly in a repo's canonical (non-worktree) checkout.** Two Claude Code sessions can end up working directly in the same shared canonical checkout at once — nothing about the harness prevents it. When they do, one session's `git checkout`/`commit`/`reset`/etc. silently thrashes HEAD out from under the other: a commit can land on the wrong branch with attribution scrambled, or a session's now-stale branch can revert a concurrent session's already-merged work if opened as a PR without first diffing against `origin/main`. A shared checkout also shares — and can exhaust — the GitHub API rate limit, disabling `gh pr merge`/`gh pr comment`/`gh pr view --json` mid-session. Mechanically enforced: a `PreToolUse(Bash/PowerShell)` hook (`pre-tool-use-canonical-mutate-guard.py`, wired in `claude/settings.json` under both matchers since PowerShell is an equally sanctioned way to run these commands; dev-env#620, ADR-071 Amendment 4) hard-blocks mutating git commands (`checkout`, `switch`, `commit`, `merge`, `rebase`, `reset`, `cherry-pick`, `revert`, `stash pop`/`apply`, `branch -d`/`-D`, bare `pull`) and `gh pr merge` carrying `-d`/`--delete-branch` (same harm model reached through a `gh` invocation instead of a `git` verb — a bare `gh pr merge` stays unblocked, since it merges only remotely) issued with cwd at a canonical root — or redirected at a canonical checkout from elsewhere via a `-C`/`--git-dir`/`--work-tree` flag (so `git -C <other-repo> checkout` from a worktree is caught too; dev-env#576, ADR-071 Amendment 2). Read-only commands, an *ambient* command inside a confirmed-live worktree (an orphaned worktree-shaped directory whose `.git` link is missing or broken doesn't count — the hook confirms liveness rather than trusting the path shape alone; dev-env#749, ADR-071 Amendment 3; the same applies to an already-resolved target root, cwd's or a redirect's — it is exempted only when `git worktree list --porcelain` confirms it a linked, non-canonical entry, not merely because its path string looks worktree-shaped, so an independently-cloned canonical checkout sitting at a worktree-shaped path is no longer wrongly exempted either; dev-env#774, ADR-071 Amendment 6), and a redirect at the engineering-journal checkout stay unaffected — permanently, by design: the Stub file workflow's whole premise is one shared canonical every concurrent session reaches via `-C` (see Engineering Journal → Stub file workflow, and its anti-pattern warning against isolating that workflow into a worktree instead). [dev-env#346](https://github.com/brownm09/dev-env/issues/346) is a narrower, unrelated case (only the `biweekly-retro` routine's own report-writing step) and does not eliminate this exemption — see [dev-env#747](https://github.com/brownm09/dev-env/issues/747) for the actual worktree-locking bug this exemption's flip side (a worktree squatting `draft/YYYY-MM-DD` instead of the canonical mutating it) now guards against. Isolate into a worktree first (`EnterWorktree` or `git worktree add`) for any other repo's canonical, or for engineering-journal work that is *not* the daily stub workflow (e.g. `journal-compose`'s own isolated worktree, ADR-082) — never for the Stub file workflow's own checkout/commit/push steps. Or override with the visible `ALLOW_CANONICAL_MUTATE=1 <command>` prefix after confirming no other session is active in that checkout. See [ADR-071](../docs/adr/071-canonical-checkout-mutate-guard-hook.md); incidents: [dev-env#453](https://github.com/brownm09/dev-env/issues/453), [dev-env#558](https://github.com/brownm09/dev-env/issues/558), [dev-env#576](https://github.com/brownm09/dev-env/issues/576), [dev-env#749](https://github.com/brownm09/dev-env/issues/749), [dev-env#620](https://github.com/brownm09/dev-env/issues/620), [dev-env#774](https://github.com/brownm09/dev-env/issues/774).
- **PR first, then merge.** Open the PR immediately after pushing; don't prompt the user to run `gh pr create`.
- **Write the journal stub immediately after `gh pr create`** — defer and a corrupted session loses all context. Then report the PR URL, prompt `/compact`, and after compaction run `/review <PR-URL> --post-comment`. **Address all findings — fixed-in-PR or filed-and-linked, never "left as-is"** — and merge in the same session. Each finding's disposition (fixed in `<sha>` | filed `#N`) goes in a "Review findings disposition" PR-body section; the `pre-merge-findings-gate` hook blocks merge until it's present, and `/review` applies the `reviewed-by-claude` label ([ADR-028](../docs/adr/028-all-findings-merge-gate.md), [ADR-039](../docs/adr/039-merge-gate-findings-enforcement.md)).
- **Fix everything, always — never ask which findings to address.** Resolve all `/review` / `/code-review` findings as part of the same work; default to fixing in-PR, file-and-link only when genuinely out of scope. Prefer the root-cause fix over the smaller band-aid. Escalate only for a product/design decision the code can't settle or a breach of the *Code Quality → Fix errors on encounter* scope guard ([ADR-028 → Addendum](../docs/adr/028-all-findings-merge-gate.md)).
- **Write a stub on PR merge** — a merge is a session boundary. After `gh pr merge`, the `PostToolUse` hook emits a `### Usage Snapshot (post-merge)` block; include it verbatim in the stub (preserved in journal Section 7). Same session as the PR open: update the existing stub, set `prs_closed:[N]` in this session's manifest shard, and delete the PR's `sessions/<project>/open-prs/<N>.json` shard. New session: update the opening stub in place for minor merges, or write a new stub for substantial follow-up. Either way set `prs_closed:[N]` in this session's manifest shard and delete the PR's open-PR shard. **Both updates are now structurally clobber-safe — the manifest shard is this session's own file and the open-PR shard is a per-PR `rm` — so the superseded ADR-054 surgical-edit discipline is no longer required (just delete `open-prs/<N>.json`; if the PR predates the shard transition and still lives in a legacy `open-prs.jsonl`, remove its single line instead). See Stub file workflow → Sharded companion files and [ADR-056](../docs/adr/056-per-session-sharding-journal-companion-files.md).**
- **Multiple PRs/merges in one session:** defer stub writing until after the last `gh pr create` / `gh pr merge`; produce one consolidated stub. Never write an intermediate stub between PR opens or merges.
- **PR closed without merging:** the stub was already written at creation; stopping is optional.
- **After merging:** move the linked project board item to Done (command is project-specific — see the project's CLAUDE.md; for dev-env, its GitHub Project section). When a work stream completes (milestone, issue group, feature sequence), move the item from Active Work to Shipped in the project roadmap — don't leave roadmaps contradicting shipping history.
- **Capture follow-ups as tiles.** Two checkpoints, one discipline. **(1) A PR reaches merged state** — however it merged: a `gh pr merge` you ran, the two-step REST merge, or auto-merge landing it while you were away ([ADR-046](../docs/adr/046-post-merge-followup-tiles.md)). **(2) A session creates a GitHub issue that remains unresolved at session end** — not closed via a same-session merged PR's Closes/Fixes/Resolves keyword, nor explicitly closed via `gh issue close` — the pure-investigation-session case: well-scoped issues filed with nothing implemented get no other mechanical nudge ([ADR-092](../docs/adr/092-dangling-issue-tile-enumeration-gate.md); incident: [dev-env#638](https://github.com/brownm09/dev-env/issues/638)). Either is an explicit checkpoint — a forcing-function floor that guarantees an enumeration pass, not a ceiling on when tiling is allowed: the same discipline fires the instant a genuine follow-up surfaces in *any* session, checkpoint or not — mid-investigation, while answering a question, in passing during unrelated work ([dev-env#642](https://github.com/brownm09/dev-env/issues/642)). Before the session's post-checkpoint work is done, **write out the follow-ups you considered** — scan for out-of-scope fixes, deferred work, tech debt, and ideas noticed in passing, and record each as `→ tiled (task_id / #N)` or `→ not tiled, because <reason>`. **"No follow-ups" is valid only as the visible result of that scan — never as a bare assertion.** Create a `spawn_task` tile for each genuine follow-up the moment you identify it — checkpoint or not — tiling it yourself, never asking the user whether or when to do it; the tile *is* the low-friction ask, so a chat question in front of it defeats the point. Tiles are low-friction but ephemeral, so **file a tracking issue for each genuine tile in the same repo** and reference that issue in the tile prompt — this gives every tile a durable, linkable, status-trackable anchor (**overrides** ADR-046's tiles-are-capture-not-tracking default; [ADR-094](../docs/adr/094-tile-tables-and-issue-per-tile.md)), and the tiles are surfaced together in the end-of-session table (see the **Session Summaries & Tile Tracking** section). **Then write the tile's shard** `sessions/<project>/tiles/<issue-number>.json` — the issue is the durable *anchor*, the shard is the durable *payload*, and without it a crash before the chip is clicked costs the one-click restart even though the issue survives (see Stub file workflow → Sharded companion files, and [ADR-118](../docs/adr/118-tile-persistence-shards.md)). Where `spawn_task` is unavailable (e.g. some terminal sessions), file the follow-up issue anyway (no chip, but the durable anchor still exists). Plan files, session notes, or carry-over context that defer tile creation to a later merge (e.g. "spawn tiles at PR3") do not override this — only an explicit user instruction anywhere in the current session (e.g. "skip tiles") does, and it waives tiling entirely for that session (both checkpoints and any ad hoc follow-up spawning), not one mechanism at a time. **Plan approval is not that instruction:** even when the approved plan explicitly scopes the follow-up work in-session, the tile checkpoint still fires; spawn the tiles and let the user dismiss any whose work is already underway ([dev-env#413](https://github.com/brownm09/dev-env/issues/413)). **Do not convert a known follow-up into a scheduling/permission question back to the user** ("let me know if you want me to start it now", "should I implement this now or leave it for a fresh session") — this applies even to the next not-yet-started unit of a multi-PR initiative (ADR-059): once the current unit merges, that next unit is a genuine follow-up subject to this same discipline, not "the immediate next step of the task in progress" (the task in progress is, by definition, done). A `stop-tile-enumeration-gate.py` trigger now catches a recurrence of this exact shape mechanically — a bounded natural-language match, advisory (not blocking) since it is a heuristic rather than an objective fact ([ADR-109](../docs/adr/109-tile-gate-deferral-question-trigger.md); incident: this exact anti-pattern recurred across multiple sessions despite the plain-language rule above already existing). **A required cross-session hand-off is itself a tile, not a chat brief.** When in-scope work cannot be finished in the current session — a blocked or corrupted environment (an orphaned or otherwise unusable worktree, a write-blocked cwd), a required session restart, a session boundary, or any "let's finish this in a fresh session" — spawn a `spawn_task` tile whose prompt is the self-contained hand-off, rather than leaving a paste-it-yourself brief in chat and asking the user to restart manually. The tile chip *is* the one-click restart, so it is strictly lower-friction than a manual restart-and-paste — and a blocked or ending session is exactly when it matters most. The *"merely list (don't tile) the immediate next steps of the task in progress"* carve-out (see the **Session Summaries & Tile Tracking** section) does **not** apply once the work must move to a *different* session: at that point it is a genuine cross-session follow-up, not a next step of the task in progress (the current session, by definition, is not continuing it). The user choosing to "restart in a clean session" is a reason to tile the restart, never a reason to skip the tile — and this holds even in plan mode when the user has asked for the hand-off, since spawning the capture chip is an inert proposal, not a mutation of the user's system — the paired tracking issue is itself a normal mutation, filed once the hand-off is actioned ([ADR-113](../docs/adr/113-cross-session-handoff-tiles.md); incident: 2026-07-16 career-playbook Clarium assessment handed off as a chat brief from an orphaned worktree instead of tiled). **When a follow-on is blocked on another open issue's future phase or completion, forward-link it *from that blocking issue*, not only back to it.** A `spawn_task` tile — or any deferred work item — whose trigger is another open issue reaching a later phase (e.g. *"actionable once #905 merges and #810 reaches Phase 3"*) must, **in the same session**, be given a **forward-reference from that blocking issue**: a "Follow-ons" note, a checklist item, or a linked comment stating the trigger condition. This is **in addition to** the existing back-reference discipline — issue-per-tile ([ADR-094](../docs/adr/094-tile-tables-and-issue-per-tile.md)), the tile shard ([ADR-118](../docs/adr/118-tile-persistence-shards.md)), and the parent reference carried in the follow-on's own body/prompt — and it **complements, does not duplicate, the shard mechanism**: the shard survives an app restart via a file on disk (extra-issue evidence that can be lost, and that nobody working the parent issue naturally encounters), whereas the forward-reference survives via the **issue graph — the durable, human-navigable discovery surface** — so whoever works the blocking issue and reaches the triggering phase finds the follow-on *from that issue alone*, regardless of whether the chip or shard still exists (GitHub's auto-generated "mentioned this issue" cross-reference — a weak forward link — is easy to miss in a long timeline and states no trigger condition). Extends [ADR-059](../docs/adr/059-multi-pr-issue-hierarchy.md)'s "navigable from both directions" from decomposition-time hierarchy to temporally phase-dependent follow-ons discovered later ([ADR-123](../docs/adr/123-forward-link-phase-dependent-followons.md); first instance: career-playbook #904 depends on #810 reaching Phase 3 — #810 given a *"Follow-ons discoverable from this issue"* comment 2026-07-27).
- **Auto-merge is off by design; `--auto` is now mechanically gated when available.** Every merge is in-session via `gh pr merge --squash --delete-branch` after the post-PR checklist (`/review`, ADR-warrant, doc-reconciliation, board transition, roadmap move). `gh pr merge --auto` is additionally gated by `pre-auto-merge-checkpoint-gate.py`: it blocks (fails closed, no override) unless the target PR's single most recent qualifying comment carries both a clean-or-disposed `review-findings` marker and a complete `premerge-checkpoints` marker (`adr_warrant`, `doc_reconciliation` both valid), fresh relative to the PR's current head commit — see `/review` Step 2f/Step 8. This restores `--auto`'s "impossible to skip" property without a human in the loop. **It does not, by itself, unlock `--auto` anywhere by default:** `allow_auto_merge` is a separate, per-repo GitHub setting (the item-7 decision tracked in ADR-083's Follow-up section) — it is **not** guaranteed to be `false` everywhere and drifts silently, since a web-UI toggle leaves no git history. **Verify live before relying on either assumption:** `gh api repos/brownm09/<repo> --jq .allow_auto_merge`. As of this writing at least one `brownm09/*` repo already differs from the `false` default — see ADR-083's dated addenda for which repo, why, and current status, rather than trusting a value copied here; a second copy of the same fact would just drift out of sync with the addenda the same way the original blanket claim drifted from live GitHub state. Where the toggle is `false`, `gh pr merge --auto` fails at the GitHub API regardless of the hook. Wait for CI, then run plain `gh pr merge --squash --delete-branch` as the standard path ([ADR-031](../docs/adr/031-auto-merge-disabled.md), [ADR-083](../docs/adr/083-auto-merge-checkpoint-gate.md)).
- **Sequence merges around a large refactor.** When a branch is a sizable refactor, land it *ahead* of smaller concurrent PRs — or hold the small ones and rebase them once, after it lands — rather than reactively rebasing the large diff every time `main` advances. The big diff is the expensive one to replay, so move it to the front of the queue instead of letting it absorb every intervening merge. This bites most in high-throughput repos (lifting-logbook), where `main` can move several times during one PR's lifetime. Auto-merge being off by design (above) means this is a manual sequencing judgment, not a queue setting.
- **PR branch state comes from the remote, not the local worktree.** Before judging whether a PR addressed findings or is mergeable: `git fetch origin <headRefName>`, then read via `git show origin/<headRefName>:<path>` — never the local tree, which may be stale or on another branch ([ADR-004](../docs/adr/004-pr-review-reads-from-remote.md)).
- **Missing-file investigation: verify `origin/main` before concluding a feature is unbuilt.** When a brief names a file/path absent from the active worktree, do not assume it's unimplemented — the worktree base may be commits behind `origin/main`, making an already-merged fix look unbuilt. At investigation start run `git fetch origin`, then `git ls-tree -r origin/main --name-only | grep <file>` and `git show origin/main:<path>` before building. Cheap insurance against duplicating just-merged work; extends the remote-read principle of [ADR-004](../docs/adr/004-pr-review-reads-from-remote.md) from PR review to investigation-start reads ([ADR-066](../docs/adr/066-worktree-session-safety-rules.md); incident: [lifting-logbook#485](https://github.com/brownm09/lifting-logbook/pull/485)). The session-start `git fetch origin` step above is now also run automatically by `session-start-sync.py` ([ADR-130](../docs/adr/130-session-start-fetch-ff-only-or-warn.md), dev-env#966), so a session beginning a missing-file investigation is more often already working from a fresh `origin/main` — the `ls-tree`/`show` verification against it is still a manual step this rule requires.
- **Runbooks** for merging a PR from a worktree, separate clones for independent parallel work, deleting a remote branch in web sessions ([ADR-035](../docs/adr/035-git-push-delete-web-session-constraint.md)), worktree deregistration recovery ([ADR-066](../docs/adr/066-worktree-session-safety-rules.md)), stacked-PR squash-merge sequencing, remote git ops hanging on the Git Credential Manager GUI ([ADR-047](../docs/adr/047-standardize-gh-credential-helper.md)), post-merge follow-up tiles ([ADR-046](../docs/adr/046-post-merge-followup-tiles.md)), and one-time `core.hooksPath` setup are in [`docs/REFERENCE.md` → Git Workflow Runbooks](../docs/REFERENCE.md#git-workflow-runbooks).

### CI not firing — merge conflict silences GitHub Actions

**Pattern:** A PR in `CONFLICTING` (merge conflict) state causes GitHub Actions `pull_request` events to never fire. GitHub cannot create the virtual merge commit at `refs/pull/N/merge`, so the event is silently dropped — no checks are queued, no runs appear.

**Symptom:** `gh pr checks` returns "no checks reported" and `gh run list --branch <branch-name>` shows only stale runs from before the conflict.

**Diagnosis:**
```bash
gh pr view <N> --json mergeable,mergeStateStatus
# mergeable: "CONFLICTING" — direct conflict indicator; mergeStateStatus will also be "DIRTY"
```

**Fix:** Rebase (or squash-rebase) the branch onto `origin/main` and force-push. Once the conflict is resolved, GitHub recreates the merge ref and CI fires normally on the next push.

Motivating incident: [lifting-logbook PR #604](https://github.com/brownm09/lifting-logbook/pull/604).

### Worktree holding the base branch blocks `gh pr merge --delete-branch`'s local step

**Pattern:** `gh pr merge --squash --delete-branch` merges server-side first (a pure API call that always succeeds), then locally checks out the base branch and deletes the merged branch. Git allows a branch to be checked out in at most one worktree at a time, so that local step aborts whenever **any** worktree in the repo — canonical or sibling — already holds the base branch. The abort lands after the remote merge but before the remote branch delete, so **both** the local and the remote branch deletes are silently skipped even though the PR is already merged.

**Symptom:** `failed to run git: fatal: '<base>' is already checked out at '<path>'` immediately after `gh pr merge --squash --delete-branch`, on a PR that nonetheless shows as merged (`gh pr view <N> --json state,mergedAt`).

**Diagnosis:** `git worktree list` from the repo root shows the base branch checked out somewhere other than the checkout the merge ran from. Two distinct causes produce the identical error — the fix is the same either way, but it helps to know which one you hit:
- The **canonical checkout correctly sitting on the base branch** — its normal, expected state in any repo that keeps its canonical on the default branch by convention. This is the common, "healthy" case, not a bug.
- An **idle worktree left squatting the base branch** from an earlier merge's `--delete-branch` step.

**Fix — proactive:** split the merge into two pure-API calls that never depend on which worktree currently holds the base branch:
```bash
gh pr merge <N> --squash                                          # server-side only; always succeeds
gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"   # pure REST ref delete
```
**Fix — reactive** (already hit the failure): the remote merge already succeeded — delete the ref with the same `gh api -X DELETE` call above, or `git push origin --delete <branch>`.

Confirmed as a **general git-worktree mechanic**, not a quirk of any one repo's worktree count: [dev-env#575](https://github.com/brownm09/dev-env/pull/575) (canonical correctly on `main`) and [lifting-logbook#664](https://github.com/brownm09/lifting-logbook/pull/664) (an idle worktree left squatting `main` by an earlier merge; that repo's CLAUDE.md "Standard Issue Workflow" step 8 carries its own copy of this same two-step pattern). Full detection, root cause, and non-destructive auto-correction (parking idle squatters instead of removing them): dev-env [ADR-058](../docs/adr/058-worktree-squatting-main-detection-correction.md); complete runbook: dev-env [`docs/REFERENCE.md` → Git Workflow Runbooks](../docs/REFERENCE.md#git-workflow-runbooks).

### Bare `--force` after rebase auto-closes any open PR on the target branch

**Pattern:** After `git rebase origin/main`, `--force-with-lease` may reject with `(stale info)` when the remote branch has advanced since the last fetch — the local tracking ref no longer matches the actual remote tip. Falling back to bare `git push origin HEAD:<branch> --force` (or `--force`) succeeds at the network level — but GitHub auto-closes any open PR targeting that branch (GitHub fires a `head_ref_deleted` event in close temporal correlation with the force-push, triggering PR auto-close — observed (not officially documented) from the GitHub API event timeline; see [dev-env#724](https://github.com/brownm09/dev-env/issues/724)). **The result: `mergedAt: null`, `mergeCommit: null`, and `gh pr reopen` fails** with "Could not open the pull request."

**Symptom:** `! [rejected] HEAD -> <branch> (stale info)` from `--force-with-lease`, then a bare `--force` push succeeds, and the PR shows `state: CLOSED` with no merge commit.

**Fix:** After any rebase, run `git fetch origin` *before* retrying `--force-with-lease` — never fall back to bare `--force`:
```bash
git rebase origin/main
git fetch origin              # refreshes the stale remote-tracking ref
git push --force-with-lease   # now succeeds without closing the PR
```

**Recovery (PR already auto-closed):** `gh pr reopen` fails with "Could not open the pull request." Create a replacement PR:
```bash
gh pr create --head <branch> --base main --title "..." \
  --body "Replaces #N (auto-closed by bare --force after rebase; [context](https://github.com/.../pull/N))"
```

Motivating incident: [win11-init-tools PR #46](https://github.com/brownm09/win11-init-tools/pull/46) — PR #34 was auto-closed and replaced. Full runbook: [`docs/REFERENCE.md` → Git Workflow Runbooks](../docs/REFERENCE.md#git-workflow-runbooks).

### Squash-merging a stacked PR's base leaves the child `CONFLICTING` — a plain rebase fixes it

**Pattern:** When a child PR's base is another (still-open) PR's branch and that parent gets squash-merged, the child's diff goes `CONFLICTING`/`DIRTY` the moment the parent's branch is deleted (whether via an explicit `gh api -X DELETE .../git/refs/heads/<branch>` call or a repo's own auto-delete-on-merge setting) — `main` now carries the parent's **squashed** content, while the child branch still carries the parent's **original, unsquashed** commits underneath its own, so a 3-way merge sees the parent's changes on both sides at once. GitHub auto-retargets the child's `baseRefName` to the repo's default branch when its old base branch disappears, but retargeting alone doesn't fix the diff.

**This is the recoverable case, not the orphan case** [`docs/REFERENCE.md` → Stacked PR squash-merge sequencing](../docs/REFERENCE.md#git-workflow-runbooks) already documents: if the parent was merged *without* `--delete-branch` (that runbook's Prevention step 1), the child PR is never auto-closed, so no new PR is needed — the fix is just a rebase.

**Symptom:** `gh pr view <child> --json mergeable,mergeStateStatus` reports `"mergeable":"CONFLICTING"`, `"mergeStateStatus":"DIRTY"` right after the parent merges, even though the child's own file changes don't actually overlap the parent's.

**Fix:**
```bash
git fetch origin main
git rebase origin/main   # no --onto, no SHA-hunting — patch-id matching drops the
                         # now-squashed parent commits on its own ("patch contents already upstream")
git fetch origin && git push --force-with-lease   # fetch first — see the bare-`--force` runbook above
```

Motivating incident: career-playbook [#923](https://github.com/brownm09/career-playbook/pull/923), stacked on parent [#878](https://github.com/brownm09/career-playbook/pull/878), 2026-07-27 — full detail: [dev-env#457](https://github.com/brownm09/dev-env/issues/457).

### `.gitattributes` eol retrofit: `git checkout`/`checkout-index` silently no-ops on already-existing files

**Pattern:** Adding `.gitattributes` (e.g. `* text=auto eol=lf`) to normalize line endings *after*
files are already checked out with the wrong ending (classically: Windows `core.autocrlf=true`
producing CRLF) does not, by itself, fix the working tree. The obvious remediation — `git add
.gitattributes`, `git add --renormalize .`, then `git checkout HEAD -- <path>` or `git
checkout-index -f -a` — silently does **nothing** to files that already exist on disk, even with
`-f`/`--force`. No error, no warning: `git check-attr` correctly reports the new `eol: lf`
attribute is in effect, and `git diff`/`git status` report clean (git re-applies the clean filter
before comparing, so it can't see the working-tree bytes are wrong) — but the file's actual bytes
on disk never change.

**Symptom:** A byte-level check (e.g. counting CRLF vs bare-LF) shows a file is still CRLF after
running `git checkout`/`checkout-index -f -a`, despite `git check-attr eol -- <path>` correctly
reporting `eol: lf` and `git diff` showing no pending changes.

**Fix:** Delete the tracked files first, then re-checkout — this bypasses whatever existing-file
fast path is skipping the smudge-filter rewrite. **Run only on a clean working tree** — this
discards uncommitted edits to tracked files with no recovery path (it is not a `git stash`-style
operation), so the guard below refuses to proceed on a dirty tree rather than silently deleting
unstaged work:
```bash
git diff --quiet && git diff --cached --quiet || { echo "Uncommitted changes present — commit or stash first" >&2; exit 1; }
git ls-files -z | xargs -0 rm -f --
git checkout-index -f -a
```
Verify with a byte-level scan (not `git diff`, which won't detect it):
```bash
node -e "
const fs=require('fs');
const b=fs.readFileSync('<path>');
let crlf=0,lf=0;
for(let i=0;i<b.length;i++){ if(b[i]===10){ if(b[i-1]===13) crlf++; else lf++; } }
console.log('CRLF:',crlf,'bare-LF:',lf);
"
```
The most reliable end-to-end validation is a genuine fresh `git clone` of the branch into a
scratch directory — that exercises the real "new checkout" path this fix is meant to guarantee,
rather than re-checking this one already-patched working tree.

Motivating incident: [cover-letter-runtime#7](https://github.com/brownm09/cover-letter-runtime/issues/7) / [PR #10](https://github.com/brownm09/cover-letter-runtime/pull/10), git 2.37.1.windows.1 — full detail: [dev-env#944](https://github.com/brownm09/dev-env/issues/944).

Also observed on a **freshly created** `git worktree add`/`EnterWorktree` checkout (not just a retrofit onto an old one) — see [cover-letter-runtime#14](https://github.com/brownm09/cover-letter-runtime/issues/14) / [PR #21](https://github.com/brownm09/cover-letter-runtime/pull/21). The detection/fix recipe is identical; only the trigger differs. Root cause there is believed to be an intermittent checkout-order race in that machine's git build (`2.37.1.windows.1`), not proven to a git source-line citation — ruled out: Claude Code's own hook system, MCP tools, native git hooks, dev-env's own worktree-creation scripts, and git's parallel-checkout feature.

---

## Dev-Env & Project Boards

Dev-env's own architecture (the `~/.claude/` symlink/junction map, the worktree-on-`main` rule, routine authoring) and its GitHub Project IDs, field IDs, and board procedures live in the **dev-env project `CLAUDE.md`** (repo root) — they are project-specific and must not load into every project. Each project's `CLAUDE.md` owns its own `## Testing` commands and board configuration; the global "Test before PR" rule defers to that section.

**GitHub Projects — single-select option mutation hazard (applies to every project).** Running `updateProjectV2Field` with `singleSelectOptions` is a full replacement — passing the existing options unchanged still produces new option IDs and drops every item's prior assignment for that field. Validated against lifting-logbook on 2026-05-08: an Observability epic addition wiped assignments on all 89 project items despite passing the full option list. Recovery precedent: [lifting-logbook#203](https://github.com/brownm09/lifting-logbook/issues/203).

Procedure (mandatory before adding/removing/renaming any single-select option on any project):

1. Snapshot current per-item assignments to a git-tracked file under the project repo's `.claude/backups/`.
2. Commit the snapshot.
3. Run the mutation with the full desired option list.
4. Update the project's CLAUDE.md option-ID table with the regenerated IDs in the same PR.
5. Refresh every machine-local cache of the old option IDs, in the same PR, with the regenerated IDs from step 4 — most commonly `.claude/hook-config.json` (the `required_fields`/`epic_options` the `post-tool-use.py` project-board hook reads) and `.claude/propose.json` (the `epics` array `/propose` reads). `post-tool-use.py` now live-fetches `single_select` field options at hook-fire time and falls back to this cache only when the live call fails ([ADR-076](../docs/adr/076-live-fetch-project-hook-single-select-options.md)), so a stale cache degrades to a labeled fallback rather than blocking — refreshing it here still matters for that fallback path and for `/propose`, which has no live-fetch equivalent. Don't assume `hook-config.json` is gitignored: that's dev-env's own convention (`.gitignore` ignores all of `.claude/`), not a universal one — e.g. lifting-logbook deliberately tracks it in git, so refreshing it there means a normal commit in this PR, not a local-only edit.
6. Restore assignments by re-issuing `gh project item-edit` for each item, mapping snapshot option name → new option ID.

If a mutation runs without a prior snapshot commit, stop and recover from the latest snapshot before any other work.

---

## Code Quality

### Fix errors on encounter

Fix any error you encounter in any project, including issues unrelated to the task at hand — do not step around a broken thing and leave it broken.

- **Truly unrelated errors go in a separate PR** (still follow the issue-before-changes and test-before-PR rules — the fix is normal work, not an exception to process).
- **Scope guard:** before taking on an unrelated fix, estimate whether it would increase the scope of the current work by more than ~75%. If it would, stop and discuss with the user before proceeding rather than absorbing it silently.
- This comes up most often in lifting-logbook.

See [ADR-038](../docs/adr/038-durable-preferences-documented-in-repo.md).

### No spawning new terminal windows (Windows scripts)

For PowerShell or batch scripts on Windows, do not author or accept patterns that spawn a new console window or trigger UAC re-launch from inside the script: `Start-Process -Verb RunAs` self-relaunch, `Start-Process ... -NoExit` sibling consoles, `cmd /c start ... powershell ...`, or any equivalent round-trip. In agent-driven and non-interactive contexts the UAC dialog has no desktop to render against, the prompt hangs the session or is silently dismissed, the new console dies before producing output, and the user pays cleanup tokens to disentangle the orphan processes.

**Instead:** put `#Requires -RunAsAdministrator` at the top of any script that needs elevation (fail-fast), or use an inline `IsInRole(Administrator)` check that `Write-Error`s and `exit 1`s. Tell the user to open an elevated terminal — do not relaunch one. For "keep the window open" debugging, redirect to `Documents\LOGS\<script>_<ts>.txt` rather than `Start-Process -NoExit`.

**Pre-existing exemption (closed allowlist).** `win11-init-tools/install_base_apps.ps1`, `win11-init-tools/set_irfanview_image_assoc.ps1`, and `win11-init-tools/configure_dev_env.ps1` already use self-relaunch for the Explorer-double-click UX. New files needing elevation must use `#Requires` instead.

**At review time**, surface as a **blocking [reliability] finding** when a new file introduces any of the patterns above. Do not downgrade on the grounds that a project CLAUDE.md "documents" the self-relaunch pattern — that documentation only covers the allowlist.

See [ADR-041](../docs/adr/041-no-terminal-spawn-in-windows-scripts.md).

### Back up before you mutate (data & config)

Any operation that mutates persistent **data or configuration** — a database row or schema, a registry key, a config file, a file association, an installed-package or environment (`PATH`) change — must be reversible by construction. Before the first mutating write:

1. **Capture prior state to a restorable artifact first**, read *live* at backup time (not from an earlier diagnostic read), and **refuse to proceed if a restorable backup cannot be captured** — changing state you could not back up leaves no way back.
2. **Provide an idempotent restore path** that returns the system to the *captured* state, not merely a generic "reset to default"; fall back to a default only when no backup exists.
3. **Prefer a written-if-absent anchor that restore does not delete** — so repeated apply runs never overwrite the original captured state and repeated restore runs converge (re-baseline by deleting the artifact deliberately).
4. **Verify each mutating write by read-back**, and record a no-op as a skip without inflating the change count.

This complements the confirm-before-destructive-action guidance: confirmation makes a risky action *deliberate*; a captured backup plus an idempotent restore makes it *reversible*. The concrete per-platform form lives in the project CLAUDE.md — see win11-init-tools' `## Backup & restore` for the Windows/PowerShell anchor convention (`Documents\LOGS\<Script>Backup.json`, verified WMI/registry writes, `#Requires -RunAsAdministrator`), with `configure_pagefile.ps1` as the reference implementation.

See [ADR-079](../docs/adr/079-backup-restore-convention.md).

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
git diff origin/main -- . | grep -E '(it\.skip|\bxit\(|xdescribe\(|test\.skip|describe\.skip|\.todo\(|pending\(|passWithNoTests|--bail|testPathIgnorePatterns)'
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

### Privilege-restricted test defaults

When a project enforces a security or isolation guarantee via a specific runtime identity distinct from the default/admin one — a restricted DB role, a scoped service account, a sandboxed permission set, a tenant-scoped API credential — test suites and local dev must default to that restricted identity. The privileged/bypass identity is the opt-in exception, not the default. A test that passes under an admin/superuser/root connection proves nothing about whether the boundary it's supposed to exercise actually enforces anything.

Before adding coverage for a permission or isolation boundary, confirm the test actually runs under the identity the boundary is scoped to — not just that the code path is exercised. A test harness that resolves the dependency graph by hand (manually constructing objects) rather than through the real framework/DI machinery is equally blind to bugs that live specifically in that machinery's resolution order or timing.

**Why:** lifting-logbook's Postgres Row-Level Security was inert in production for 3+ weeks ([issue #644](https://github.com/brownm09/lifting-logbook/issues/644)) because every test and local-dev environment ran as the Postgres superuser, which bypasses RLS by design — the one enforcement bug that existed was invisible everywhere except real user traffic, and cost a multi-hour investigation to find. See [ADR-089](../docs/adr/089-privilege-restricted-test-defaults.md) for the full incident and rationale.

**How to apply:** this extends the Plan-then-optimize → Pass 3 Security dimension (below) — for any change touching a permission/authorization/isolation boundary, state explicitly whether test coverage runs under the restricted identity or the admin one, rather than leaving it implicit. Not mechanically greppable the way the suppression/test-integrity checks are — this is a judgment call during the Security audit, not a static pattern.

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

**Rule 3 — Upstream in-range float (green→red with no dependency edit of yours).**
A direct *or transitive* caret dependency can publish a new in-range version while a PR is open or after a merge; `npm install --package-lock-only` then regenerates the lockfile to that newer version and the sync check flags the committed lockfile as stale — a PR that was green an hour ago, or `main` itself, goes red **with no dependency change of yours**. (npm's sync check rebuilds the ideal tree from the *current* registry, so the passage of time alone can break it.) Motivating incidents: lifting-logbook #432/#433/#501/#520 — `@clerk/backend` and the transitive `@clerk/shared` under its own caret. Two cases, opposite responses:
- **Uncommitted local float** — the Rule 2 pre-PR check floats a bump you did not intend into your working tree. **Discard it** (`git checkout -- package-lock.json`); it does not belong in your PR.
- **CI red on a pushed branch / open PR** — the regenerated lockfile is what CI now resolves. **Regenerate and commit** (`npm install`; verify `npm ci` exits 0 locally before pushing).

**Enforcement context — scope the CI sync gate to `pull_request`, not `main` pushes.** The latest-in-range check belongs on PRs, where it catches a contributor editing `package.json` without regenerating the lockfile (its real purpose). Run on every `push` to `main`, it instead recomputes the ideal tree from live-registry time, so an upstream in-range publish reddens `main` with no contributor action; `npm ci` on `main` still catches install-breaking drift. This mirrors the **Layer 3** pre-push hook (ADR-036), which already runs the drift check only when a pushed `package.json` actually changed. Precedent: lifting-logbook #523.

**Scope note.** Applies to npm/`package-lock.json` repos. Yarn (`yarn.lock`) and pnpm (`pnpm-lock.yaml`) have equivalent drift failure modes but different regeneration commands (`yarn install`, `pnpm install --lockfile-only`) — extend the check per repo before relying on it there.

---

## Experimental Rigor

Any comparative claim about a process change — A/B arms, before/after, challenger vs. incumbent — is an experiment, and carries a declared tier *before* results exist. The full protocol lives in the `/experiment-audit` skill (`design` mode before generating anything, `verdict` mode before concluding anything); see [ADR-115](../docs/adr/115-experimental-rigor-protocol.md). The one law: **no conclusion without a design that could have produced the opposite conclusion.**

- **Tier 0 — probe.** An n=1 exploration, declared in three lines in the tracking issue. Cheap and encouraged. Legal endings *only*: "signal — escalate to Tier 1," "infeasible as specified," or "shelved — untested." Never adopt / reject / "failure" / "success."
- **Tier 1 — test.** Required before any adopt/reject of a standing process. Pre-registration frozen in the tracking issue *before* generation: hypothesis + the one **primary outcome construct** the change exists to improve (stated arm-agnostically, as the outcome — *cohesion*, not a mechanism/proxy of it like *bookend correspondence*); one manipulated variable + held-constant list; success criteria classified **primary vs. secondary/mechanism** and traced to that construct — a secondary criterion can diagnose but **never alone decide the verdict** (that is criterion substitution, threat T10); a check built from one arm's *known* failure is a diagnostic, not verdict-bearing, until calibrated (T3); a neutral fresh-run baseline; a corpus fixed in advance (n≥3 or all-available, not solely incumbent-failure cases, with ≥1 known-good input); an **incumbent-influence inventory** (neutralize or log every default the challenger inherits — the challenger runs its own natural defaults); instruments **calibrated against known-good AND known-bad references before any arm is scored** (uncalibrated ⇒ quarantined, non-verdict-bearing); stage-matched processing; drafter ≠ scorer, blinded, order-randomized, fresh context per artifact; and a win bar + aggregation + n and k fixed — no generate-until-win. Probe data may inform the design; it never scores in the verdict.
- **Adoption rider.** When the decision would *replace* a standing process: ≥1 held-out input scored once at the end, plus a named post-adoption tripwire (a golden-set / baseline entry) and rollback path.
- **Verdict ∈ {supported, refuted, inconclusive — confounded by X}**, read off the **primary construct** (never a secondary proxy — deciding failure on a proxy is criterion substitution), with per-input results (an aggregate may not hide losses) and a scope statement ("holds for \<corpus\> under \<SHAs / conditions\>"). An unfair or unregistered design cannot produce adopt/reject — only a hypothesis for a proper run. Deviations from the pre-registration are listed, or the verdict is void.

Projects that run experiments should declare an `## Experiments` section (see [Per-Project CLAUDE.md Requirements](#per-project-claudemd-requirements)); Pass 3's **Experimental validity** dimension enforces the design half at plan time. A Stop-hook backstop (`stop-experiment-verdict-gate.py`) nudges once when a conclusion is stated with no `/experiment-audit` run.

---

## Hook Safety

See [docs/REFERENCE.md — Hooks → Authoring rules](../docs/REFERENCE.md#authoring-rules) for the hook authoring invariants (atomic commits, safe-exit guard, `pyw -3` invocation / no `bash -c`, `import _winsubp` for subprocess-spawning hooks, and declared fail direction — advisory hooks fail open, blocking gates fail closed).

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

**Always plan first — permission modes don't bypass it:** Plan mode (Shift+Tab cycles modes) is the session default for every substantive task. Bypass and auto modes reduce permission prompts only; they do not relax the plan-first discipline.

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

**Pass 3 — Risk-dimension audit:** before acting, the plan must address each of these seven dimensions explicitly. For any that don't apply, state **`N/A — <reason>`** rather than omitting it. The bar is *stating the decision*, not adding work everywhere.

1. **Testing** — coverage for new behavior (defers to the project `## Testing` section).
2. **Observability** — what is logged/traced at boundaries and on failure (defers to the project `## Observability` section).
3. **Security** — authz, input validation, secret handling, sensitive-data/PII exposure. When the change touches a permission, authorization, or isolation boundary, explicitly confirm test coverage runs under the restricted identity the boundary is scoped to, not just the admin/superuser one (see Code Quality → Privilege-restricted test defaults).
4. **Resilience / failure modes** — error handling, timeouts, fallbacks, and how the change is rolled back / reverted.
5. **Performance** — data-access patterns (N+1), hot paths, payload/bundle size.
6. **Data integrity & migrations** — schema changes, multi-tenant isolation, reversibility.
7. **Experimental validity** — when the plan generates or interprets any comparative result (A/B, before/after, challenger vs. incumbent): declare the tier, confirm pre-registration precedes generation, instruments are calibrated, and arms are contamination-checked and stage-matched — run `/experiment-audit design` before generating and `/experiment-audit verdict` before concluding (defers to the project `## Experiments` section and the global `## Experimental Rigor`). Plans making no comparative claim: `N/A — no experiment`.

**Accessibility** is audited whenever the change touches UI. Each project's CLAUDE.md may declare additional project-specific gates (e.g. lifting-logbook: OTel trace correlation, raw-SQL spans, LLM data scrubbing; career-playbook: `validate.sh`, briefing regeneration, artifact-schema parity) — the audit defers to those.

---

## Session Summaries & Tile Tracking

Every session boundary — a completed task, a pause for input, or a return after a long gap — is a moment to orient the user. Four behaviors, all global. They are Claude-facing *output* rules, not mechanics; a targeted `Stop` hook — landing in [#656](https://github.com/brownm09/dev-env/issues/656), and dormant until it merges — reminds only when tiles were spawned but no table was emitted (the one mechanically verifiable half). See [ADR-095](../docs/adr/095-session-boundary-summaries-and-idle-refresher.md), [ADR-094](../docs/adr/094-tile-tables-and-issue-per-tile.md), and [ADR-128](../docs/adr/128-session-end-feedback-retro-table.md).

- **Close each substantive stop with a summary.** When a stop follows real work or leaves something for the user, end the turn with three parts: **Completed** — what got done; **Context / ask** — what's being asked of the user now, if anything (decisions needed, what to review or test, each with its exact path/URL per *User-Actionable References*); **Remaining** — outstanding to-dos. Tile the *genuine out-of-scope follow-ups* among them — using the *Capture follow-ups as tiles* definition of what counts as genuine, and deduplicating against tiles/issues already created earlier in this session so a multi-stop session never re-files the same follow-up; merely list (don't tile) the immediate next steps of the task in progress — but once that remaining work must move to a *different* session (a blocked/orphaned worktree, a required restart, a session boundary), it is a cross-session follow-up: tile the self-contained hand-off per *Capture follow-ups as tiles*, never a paste-it-yourself chat brief ([ADR-113](../docs/adr/113-cross-session-handoff-tiles.md)). **Skip the summary on trivial exchanges** — a greeting, a one-line acknowledgment, a single clarifying question — where it would be pure noise (the same judgment the session-mode preamble uses).

- **Close a session that involved substantial hands-on correction with a feedback-retro table** ([ADR-128](../docs/adr/128-session-end-feedback-retro-table.md)). When a session included a meaningful amount of corrective or judgment-call feedback from the user — not just task requests, but "no, not that," register/diction catches, record-reconciliation calls, process-preference corrections, a repeated theme flagged more than once — close with a table: **What was said** (paraphrased) / **Underlying pattern** / **Status** (already durable, partially addressed, or not yet formalized) / **Long-term mechanism** (how to make this durable). This is distinct from the substantive-stop summary above, which reports task status, not correction patterns — produce both when both apply, and don't let the retro table substitute for the Completed/Context/Remaining recap or vice versa. Any row landing on "not formalized" is a candidate for the existing memory + issue-pairing discipline under *Durable Preferences & Memory* above — the table is a discovery pass surfacing what to immortalize next, not a new persistence mechanism of its own, and it doesn't replace the periodic, cross-project `biweekly-retro` / `weekly-memory-audit` routines, which hunt for global signal at a different cadence and scope. Judging "substantial" is a judgment call, the same as "substantive" above — deliberately no automated skill or subagent scans for this yet (the risk of a rule-writer over-generalizing one correction into an overly broad rule, without a human confirming the generalization's scope, is real); apply this rule directly, the same way the summary bullet above already works without a dedicated skill behind it.

- **File an issue per tile, and end the session with a tile table.** When you spawn a `spawn_task` tile, also file a tracking issue for it in the same repo and reference that issue in the tile prompt — every genuine tile gets a durable, linkable, status-trackable anchor (this **overrides** ADR-046's tiles-are-capture-not-tracking default; [ADR-094](../docs/adr/094-tile-tables-and-issue-per-tile.md)). Then, whenever one or more tiles were spawned this session, close with a table under the exact heading **`### Tiles spawned this session`** (the stable marker the enforcement hook keys on):

  | Tile | Issue | Status | Next |
  |------|-------|--------|------|
  | short title | [#N](url) | open · not started | click the chip, or open the issue |

  **Status, honestly:** the live chip is the one-click "start the session" control and the issue is the durable anchor; there is no per-chip "was it started" API, so **Status** is the issue's open/closed state plus a *best-effort* "started" note from `list_sessions` (title/branch/PR match). A dismissed or lost chip is re-spawnable on request; the issue persists regardless.

- **Open with a refresher after a long idle gap.** When you return to a session after an extended idle period (default ~60 min; the `idle-refresher.py` `UserPromptSubmit` hook measures the gap and injects the cue — Claude cannot measure elapsed idle time on its own — so until that hook lands in [#655](https://github.com/brownm09/dev-env/issues/655), no cue fires and this rule is dormant, not wrong), lead your reply with a brief refresher — what we were working on, the current state, and any pending to-dos/tiles — before addressing the new prompt. The threshold is per-project–overridable via `idle_refresher_minutes` in `.claude/hook-config.json`.

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

## User-Actionable References

Whenever directing the user to view, test, run, or consume any resource, include the exact reference inline — never make the user ask for it:

- **Local files, scripts, and logs** — provide the absolute path or the exact invocation string. Never say "run the script" without the full path, or "see the log" without the file path.
- **Remote resources (PRs, issues, deployed URLs)** — provide the full URL. Never say "check the PR" without the link, or "see the issue" without the URL.

No "see the file" without the path. No "check the PR" without the URL.

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
  stub written on a day where the open-PR records (`open-prs/<N>.json` shards or a legacy
  `open-prs.jsonl`) show the same PR as open should also be grouped under that H2, even if neither
  `prs_opened` nor `prs_closed` is set for that PR in its manifest shard — this covers PRs that span
  more than two sessions. This prevents the composed
  journal from fragmenting create-iterate-review sequences into unrelated-looking sections.

---

### Stub file workflow

Each session writes an isolated stub file — no shared mutable draft. This eliminates write
contention when multiple sessions run in parallel. Slug is determined at day end.

**Sharded companion files — no shared-file editing.** The stub's two companion files are also sharded
per session / per PR ([ADR-056](../docs/adr/056-per-session-sharding-journal-companion-files.md)), so
the stub isolation above extends to them and **no session ever writes a file another session also
writes**:

- **Manifest:** each session writes its own shard `sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`
  (one JSON object, paired 1:1 with the stub) **with the Write tool**; a later same-session rewrite
  (e.g. setting `prs_closed:[N]`) uses the **Edit tool** — never a shell heredoc/`echo`/redirect,
  mechanically blocked by `pre-tool-use-journal-shell-write-guard.py`
  ([ADR-129](../docs/adr/129-journal-shell-write-guard.md)). Setting `prs_closed:[N]` after a merge
  edits *this session's own shard* — never a file another session touches. A shard **re-created after a
  compose has consumed the original** (e.g. late PR-merge bookkeeping) must carry the **full five-field
  set** (`stub`, `topic`, `tokens`, `prs_opened`, `prs_closed`) — never just the field being updated; the
  `journal-shard-write-advisory.py` hook flags violations (and BOMs) at write time (dev-env #556,
  [ADR-081](../docs/adr/081-write-time-journal-shard-validation-hook.md)).
- **Open PRs:** each open PR is its own shard `sessions/<project>/open-prs/<N>.json`. Opening PR #N
  writes `<N>.json` **with the Write tool** (never a shell heredoc/`echo`/redirect —
  [ADR-129](../docs/adr/129-journal-shell-write-guard.md)); merging/closing it **deletes** that file
  (`rm`/`Remove-Item` — a deletion, not a content-write, stays outside that guard's scope) — a per-PR
  delete that cannot touch any other PR's record, even when a *different* session or the
  `reconcile-open-prs.py` hook does the removal.
- **Tiles:** each spawned tile is its own shard `sessions/<project>/tiles/<issue-number>.json`, keyed by
  the tile's paired GitHub issue and filed under the tile's **target** project (from its `cwd`), not the
  spawning session's. **Write it immediately after each `spawn_task` call** — the payload is what lets a
  lost chip be re-spawned *exactly* after a crash or app restart, which the chip itself cannot survive
  (ADR-094); closing the paired issue is the completion signal. **Write it with the Write tool** — the
  general rule for all four journal content-file kinds (stub, manifest, open-PR, tile), mechanically
  enforced by `pre-tool-use-journal-shell-write-guard.py` rather than restated per-kind here (`prompt` is
  free prose and `cwd` is the one required field that is a path — see
  [ADR-129](../docs/adr/129-journal-shell-write-guard.md)).
  Schema, the write recipe, and current phase:
  [REFERENCE → Tile shards](../docs/REFERENCE.md#tile-shards-sessionsprojecttilesissue-numberjson);
  rationale, and why a headless process *cannot* respawn tiles instead, in [ADR-118](../docs/adr/118-tile-persistence-shards.md).

Because shards are disjoint files, git merges concurrent sessions' writes cleanly and removal is a
per-file delete — the pull-first + surgical-edit discipline of the superseded ADR-054 is **no longer
needed** (ordinary pull-before-push git hygiene still applies). That disjointness is a content-merge
guarantee only — it does not extend to the local git index, which every concurrent session in this
checkout shares. **Commit with an explicit pathspec, not a bare `git commit`:** a bare `git commit`
commits the *entire* staged index, not just this session's `git add`-ed files, and can sweep a concurrent
session's already-staged shard into this session's commit — see the `--` pathspec in the commit steps
below ([ADR-056 → Addendum](../docs/adr/056-per-session-sharding-journal-companion-files.md)). Readers
still accept the legacy single-file `YYYY-MM-DD.manifest.jsonl` / `open-prs.jsonl` during the transition;
writers emit only shards. Schemas and write/delete steps: [REFERENCE → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals).

**An explicit pathspec can also drop half of a rename — check `git status --short` after committing.** `git mv` (or an equivalent manual rename) stages a delete at the old path and an add at the new one; a pathspec naming only the new path commits the add and leaves the delete staged, so `HEAD` keeps the old file while the working tree does not. Every gate that scans the working tree then passes while CI, which checks out the commit, fails — often with nothing but an artifact/file count off by one in the CI log as the tell. Include the **old** path in the `git commit -- <paths>` list for a rename (after `git mv` specifically, `git add` on that same old path fails outright — `git mv` stages the removal atomically, leaving nothing on disk or in the index for a subsequent `git add` to act on — so `git add` and `git commit` take different path lists for the same rename). More generally: after **any** explicit-pathspec commit, in this checkout or any other, confirm `git status --short` prints nothing — a leftover `D `/`M ` line (the letter in the *first* column: staged, not yet committed) is the part of the change that did not get committed. Incident: career-playbook PR #955, 2026-07-28 ([dev-env#927](https://github.com/brownm09/dev-env/issues/927); [ADR-056 → Addendum](../docs/adr/056-per-session-sharding-journal-companion-files.md)).

**An orphaned open-PR shard *deletion* is yours to commit — the one deliberate exception to "commit only your own files."** `reconcile-open-prs.py` unlinks the shard of every PR it finds MERGED/CLOSED at session start but never commits, and a session that opens no PR writes no stub — so an unlink can sit uncommitted in the shared canonical indefinitely, leaving permanent `git status` noise every later session must re-triage, one `git restore` away from resurrecting the stale entries, and a *committed* branch that still lists merged PRs as open until a compose reconciles it. When the hook reports a **deletion whose PR it confirmed MERGED/CLOSED**, commit it immediately with an explicit pathspec, whether or not you write a stub:

```bash
git -C C:/Users/brown/Git/engineering-journal add -- sessions/<project>/open-prs/<N>.json
git -C C:/Users/brown/Git/engineering-journal commit -m "journal: close open-pr shard #<N> (merged)" -- sessions/<project>/open-prs/<N>.json
```

Safe precisely because ADR-056 made each shard a disjoint per-PR file: the delete cannot touch another PR's record, and the state was verified live. **The converse still holds** — a dirty open-PR path that is *added or modified* is a concurrent session's in-flight shard: leave it alone, and never sweep it into your pathspec. The hook reports those two classes separately, plus a third — a deletion whose PR state it could **not** confirm (a `gh` failure, e.g. an exhausted GraphQL budget) — which you do not commit blind. The hook never commits on its own: it is an advisory `UserPromptSubmit` hook that must fail open, it runs in a checkout whose git index every concurrent session shares, and it would be committing onto whatever branch the canonical happens to hold. See [ADR-119](../docs/adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md).

**An orphaned tile shard *deletion* is yours to commit too — the open-PR exemption above extends to tile shards.** This resolves the ambiguity in [dev-env#950](https://github.com/brownm09/dev-env/issues/950): two sessions on 2026-08-06 read the bullet above and reached opposite conclusions about whether it covered tile shards. It does. `reconcile-pending-tiles.py` unlinks the shard of every tile whose paired issue it finds `CLOSED` at session start but never commits, and a session that spawns no tile writes no stub — so exactly the open-PR gap recurs: an unlink can sit uncommitted in the shared canonical indefinitely, is one `git restore` away from resurrecting a finished tile, and a committed branch keeps listing it as pending until a compose reconciles it — which is exactly what over-reports the session-start "pending tiles" count. `reconcile-pending-tiles.py` now also scans `git status --porcelain -- sessions` for a tile-shard path already deleted from the working tree but not yet committed, recovers its paired issue from `git show HEAD:<path>` (the file itself is gone), and re-confirms that issue's state with its own live lookup before reporting. When it reports a **deletion whose issue it confirmed CLOSED**, commit it immediately with an explicit pathspec, whether or not you write a stub:

```bash
git -C C:/Users/brown/Git/engineering-journal add -- sessions/<project>/tiles/<issue-number>.json
git -C C:/Users/brown/Git/engineering-journal commit -m "journal: close tile shard #<issue-number> (issue closed)" -- sessions/<project>/tiles/<issue-number>.json
```

More than one confirmed shard in the same session: one combined command, every path space-separated in both the `add` and the `commit -- <paths>` pathspec — never the bare `tiles/` directory. Safe for the identical reason the open-PR case is safe: each shard is a disjoint per-issue file ([ADR-118](../docs/adr/118-tile-persistence-shards.md)), so the delete cannot touch another tile's record, and the state was verified live. **The converse still holds** — a dirty tile path that is *added or modified* is a concurrent session's in-flight shard: leave it alone, never sweep it into your pathspec. The hook reports that class separately, plus a deletion whose issue state it could **not** confirm (a `gh`/`git` failure, or a filename/embedded-`issue` mismatch) and one that came back unexpectedly **open** (an anomaly — never commit; restore instead with `git checkout HEAD -- <path>`) — neither is committed blind. The hook never commits on its own, for the same reasons its `reconcile-open-prs.py` sibling doesn't, and the whole pass is suppressed while the canonical is mid-merge. See [ADR-118](../docs/adr/118-tile-persistence-shards.md) Amendment 5 and [ADR-119](../docs/adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md).

**Branch:** `draft/YYYY-MM-DD` — created at the first session of the day, merged to main at day end.

**Day rollover: always cut `draft/<today>` from `main`, however many prior days' drafts are still unmerged.** A stub's *filename date* and its *branch date* must always match, because every discovery path keys on **both**: `/journal-compose` resolves `SOURCE_BRANCH=draft/<DATE>` *and* globs `sessions/*/<DATE>_*.stub.md` **on that branch**, and the nightly `daily-journal-compose` routine gates on `show-ref --verify refs/remotes/origin/draft/${DATE} || exit 0`. A stub committed to a branch named for a different day is therefore invisible to both — and the routine's miss is **silent**, not an error. So never join yesterday's still-unmerged branch to "keep the day together": that guarantees the newer day is never composed *and* never reported, and when the stale branch finally composes (it composes only its own date) the newer day's stubs ride into `main` uncomposed, where nothing ever looks for them again. Several unmerged `draft/*` branches coexisting is the **normal** steady state — they are independent per-day units, and `new-day-journal-check.py` already enumerates them — so it is not a reason to reuse one. Composing the stale branch first is the right *remediation*, but it cannot be the rule: `/journal-compose` is a dedicated-session operation, so a session that merely needs to write a stub cannot perform it. The bounded cost of cutting fresh: `draft/<today>` taken from `main` does not carry yesterday's open-PR shard edits, so its `open-prs/` view is only as current as the last merge to `main` — `reconcile-open-prs.py` corrects that live at session start via `gh pr view`. **That includes shard *deletions*, and the invariant is about branch lineage, not session ordering: a shard deletion committed to draft branch A stays invisible to any branch B cut from `main` until A merges.** A merged PR's shard removed only on an unmerged branch is still present on `main`, so a branch cut from `main` **resurrects** it — until the reconcile hook re-unlinks it on disk and someone commits that deletion *on the new branch*. The corollary for a single session: cut the branch at the start, not after doing shard cleanup, or you leave the resurrected deletions uncommitted behind you (the hook's sentinel re-arms on age, so the same session may not re-report them). But the corollary is not the whole rule — verified live on 2026-07-22, `draft/2026-07-22` was cut correctly, at the start of a session, from a `main` that still carried all four shards [dev-env#866](https://github.com/brownm09/dev-env/issues/866) had just cleaned up, and all four resurrected anyway; no session ordering would have prevented it. **A deletion is durable only once its carrying branch merges to `main`** — so land the day's draft branch rather than treating a re-commit as the fix.

**Date-mismatched stub — the divergence state, named and handled.** A stub whose filename date differs from the date of the draft branch it is committed on. When you arrive and find today's stubs already sitting on an older `draft/<D>`, repair **additively** — never rewrite that branch's history, since every concurrent session shares it:

1. Still cut `draft/<today>` from `main` for your own stub. This bounds the damage to the already-misfiled set instead of adding to it.
2. Surface it and tile the remediation (a cross-session hand-off per *Capture follow-ups as tiles*) — never leave it implicit.
3. Once `draft/<D>` composes and merges, the mismatched stubs are on `main` uncomposed. Bring them into their own day's branch: `git -C C:/Users/brown/Git/engineering-journal fetch origin`, then `checkout draft/<today>` and `merge origin/main`. `/journal-compose <today>` then discovers them by filename date.
4. If `draft/<D>` had already merged before you noticed, this is the resurrected-branch path instead: `py -3 ~/.claude/scripts/reconcile-late-stubs.py draft/<D>`.

Detection is advisory-only, in `new-day-journal-check.py` — which for this one check is deliberately **not** suppressed in worktree sessions, since those write stubs into the canonical via `git -C` too and are exactly who needs the warning. Residue of the pre-rule behavior: 26 stubs across 5 dates (2026-05-11 … 2026-07-03) sat uncomposed on `origin/main` as of 2026-07-22. See [ADR-119](../docs/adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md); incident: [dev-env#866](https://github.com/brownm09/dev-env/issues/866).

**Never create a dedicated worktree to write a stub — always operate directly on the canonical via `-C`.** `draft/YYYY-MM-DD` can be checked out in exactly one place at a time (git's one-worktree-per-branch rule) — the canonical-direct exemption above exists precisely so every concurrent session reaches the *same* checkout instead of racing for the branch. A session whose own primary repo is a different project — most often a `spawn_task` tile finishing its real work and writing a stub as a side effect — must still run every step below as `git -C C:/Users/brown/Git/engineering-journal <command>`: never `EnterWorktree` (it cannot target a different repo — see the bullet above) and never a manually-created `git worktree add <path> ... draft/YYYY-MM-DD`. That `git worktree add <path> -b <branch> origin/<default-branch>` pattern is the right way to isolate cross-repo work in general — the Stub file workflow is the one deliberate exception to it. Applying the general isolation instinct here instead locks `draft/YYYY-MM-DD` to a throwaway worktree and blocks the canonical, and every other concurrent session, from reaching it until that worktree is parked or removed. Confirmed live twice simultaneously on 2026-07-12 (`stub-823-120134`, `stub-829-165612`, each locking that day's draft branch away from the canonical); a `PreToolUse` hook (`pre-tool-use-journal-draft-worktree-guard.py`, ADR-105) now blocks this at the tool-call level. See [dev-env#747](https://github.com/brownm09/dev-env/issues/747).

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
3. Read the open-PR records (`sessions/<project>/open-prs/*.json` shards, plus any legacy `open-prs.jsonl`) if present — include their PR list as session context before starting work (the `reconcile-open-prs.py` hook also surfaces this at session start).
4. Create `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` **with the Write tool** — never a shell
   heredoc/`echo`/redirect; mechanically blocked by `pre-tool-use-journal-shell-write-guard.py`
   (see [REFERENCE → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals)
   and [ADR-129](../docs/adr/129-journal-shell-write-guard.md))
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Write this session's manifest shard `sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl` **with the
   Write tool** (never a shell heredoc/`echo`/redirect — same mandate as step 4,
   [ADR-129](../docs/adr/129-journal-shell-write-guard.md)) — one JSON object (see
   [REFERENCE → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals))
7. `git add sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl sessions/<project>/open-prs/<N>.json sessions/<project>/tiles/<issue-number>.json`, `git commit -m "draft: YYYY-MM-DD session 1" -- sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl sessions/<project>/open-prs/<N>.json sessions/<project>/tiles/<issue-number>.json`, `git push -u origin draft/YYYY-MM-DD`
   *(include `sessions/<project>/open-prs/<N>.json` — the exact shard file(s) this session itself
   created or removed, never the bare `open-prs/` directory — in both the `git add` and the `git commit`
   pathspec only if this session opened or merged a PR. More than one PR touched in the same session
   means more than one shard path, space-separated (e.g. `open-prs/54.json open-prs/55.json`) — never
   fall back to the bare directory for convenience. A directory pathspec stages whatever shard a
   different concurrent session has just written into that same folder, sweeping it into this
   session's commit under an unrelated message ([dev-env#480](https://github.com/brownm09/dev-env/issues/480)).
   The `--` pathspec on `git commit` is required, not optional — see "Commit with an explicit pathspec"
   above. The same three rules apply to `sessions/<project>/tiles/<issue-number>.json`: include it only
   if this session spawned a tile, name each tile shard exactly (never the bare `tiles/` directory), and
   drop it from both pathspecs otherwise — `git add` on a path that does not exist fails and aborts the
   whole add, silently leaving the stub and manifest unstaged.)*

**Subsequent sessions:**
1. `git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD && git -C C:/Users/brown/Git/engineering-journal pull`
2. Read the open-PR records (`sessions/<project>/open-prs/*.json` shards, plus any legacy `open-prs.jsonl`) if present — include their PR list as session context before starting work (the `reconcile-open-prs.py` hook also surfaces this at session start).
3. Find the most recent stub and read only its `<!-- next-session-context -->` paragraph:
   ```bash
   ls C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.stub.md | sort | tail -1
   ```
4. Create a new `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` **with the Write tool** (never a shell
   heredoc/`echo`/redirect — see step 4 of "First session of the day" above,
   [ADR-129](../docs/adr/129-journal-shell-write-guard.md)) with the current session block
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Write this session's manifest shard `sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl` **with the
   Write tool** (never a shell heredoc/`echo`/redirect — same mandate as step 4,
   [ADR-129](../docs/adr/129-journal-shell-write-guard.md)) — one JSON object (see
   [REFERENCE → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals))
7. `git add sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl sessions/<project>/open-prs/<N>.json sessions/<project>/tiles/<issue-number>.json`, `git commit -m "draft: YYYY-MM-DD session N" -- sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl sessions/<project>/open-prs/<N>.json sessions/<project>/tiles/<issue-number>.json`, `git push`
   *(include `sessions/<project>/open-prs/<N>.json` — the exact shard file(s) this session itself
   created or removed, never the bare `open-prs/` directory — in both the `git add` and the `git commit`
   pathspec only if this session opened or merged a PR. More than one PR touched in the same session
   means more than one shard path, space-separated (e.g. `open-prs/54.json open-prs/55.json`) — never
   fall back to the bare directory for convenience. A directory pathspec stages whatever shard a
   different concurrent session has just written into that same folder, sweeping it into this
   session's commit under an unrelated message ([dev-env#480](https://github.com/brownm09/dev-env/issues/480)).
   The `--` pathspec on `git commit` is required, not optional — see "Commit with an explicit pathspec"
   above. The same three rules apply to `sessions/<project>/tiles/<issue-number>.json`: include it only
   if this session spawned a tile, name each tile shard exactly (never the bare `tiles/` directory), and
   drop it from both pathspecs otherwise — `git add` on a path that does not exist fails and aborts the
   whole add, silently leaving the stub and manifest unstaged.)*

**File formats, stub template, and recovery:** the manifest-shard and open-PR-shard schemas
(referenced in the workflow steps above), the stub-file template, the canonical 11-section compose
structure, and the draft-branch recovery procedure are documented in
[`docs/REFERENCE.md` → Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals).

**End of day (last session):** Run `/journal-compose --force` — it discovers all stubs via manifest
(or glob fallback), merges them, produces the canonical 11-section document, and auto-merges the PR.
`--force` is required when composing today's branch; past-date composition
(`/journal-compose YYYY-MM-DD` for a prior day) does not need the flag. Composition runs inside a
dedicated, disposable engineering-journal worktree — the shared canonical checkout is never
branch-switched or committed to ([ADR-082](../docs/adr/082-journal-compose-worktree-isolation.md)).

---

### Update triggers

**Project journal** (`sessions/<project>/`):
- **Auto-create stub without user prompt on these events:**
  - PR opened — follow the Stub file workflow immediately after `gh pr create`. If no further work is planned (e.g., waiting on CI or human review), stop after writing the stub.
  - PR merged (including auto-merge) — write or update a stub for the merge session (see Git Workflow → Write a stub on PR merge), then stop.
  - PR closed without merging — stub was already written at PR creation; stopping is optional (see Git Workflow → PR closed without merging)
  - PR updated (push to a branch with an open PR) — when the hook reminder fires after `git push`, update the engineering journal immediately: if a stub already exists for the current session, update it in place; otherwise create a new stub. Document what changed in this session (review findings addressed, approach decisions, what was pushed).
  - **Report / analysis generated** — whenever the user requests any report or analysis (an audit, an investigation write-up, a **verification or deploy-check**, a comparison, a findings summary, etc.), capture it in the journal: save the full output as an artifact under `sessions/<project>/reports/YYYY-MM-DD-<slug>.md` and link it from the session stub (create the stub if none exists yet for the session). Report/analysis generation is itself a journal boundary — it does not require a PR. Short analyses (≲ one screen) may be inlined in the stub instead of linked; anything longer must be a linked artifact so the stub stays scannable. Applies to all projects. (A `/review <PR-URL>` session stays exempt per the exclusion below — its findings live on the PR, not in a free-standing report.) This trigger is mechanically backstopped by `stop-journal-stub-checkpoint.py` (a Stop hook): when a session with report/analysis/verification intent does substantive work but ends with no stub — and opened no PR, isn't a `/review` session, and carries no "skip journal" override — it blocks the stop once with an advisory reminder ([ADR-100](../docs/adr/100-stop-journal-stub-checkpoint-hook.md)).
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
