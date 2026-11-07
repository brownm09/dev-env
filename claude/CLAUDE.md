<!-- SOURCE OF TRUTH: C:/Users/brown/Git/dev-env/claude/CLAUDE.md -->
<!-- ~/.claude/CLAUDE.md is a symlink to this file. Edit here, not at ~/.claude/. -->
<!-- To commit: cd C:/Users/brown/Git/dev-env && git add claude/CLAUDE.md && git commit -->

# Claude Code — Global Configuration

This file is loaded automatically in every session, across all projects.
Project-specific CLAUDE.md files extend these conventions — they do not repeat them.

> **ADRs:** The design decisions behind the rules in this file are recorded in [`docs/adr/`](../docs/adr/INDEX.md) in the dev-env repo. Consult the relevant ADR before overriding any rule, hook, skill, or config.

> **Rules here, stories in REFERENCE.** This file holds each rule. The reasoning, incidents and edge cases live in [`docs/REFERENCE.md` → Instruction Detail](../docs/REFERENCE.md#instruction-detail); read a rule's linked detail before overriding it or when an edge case applies. Relative links resolve from `C:/Users/brown/Git/dev-env/claude/`. This file loads in every session, so its size is capped by `test_claude_md_size.py`: write a new rule as a line or two here and put its story in REFERENCE ([ADR-159](../docs/adr/159-instruction-files-hold-rules-not-stories.md)).

---

## Durable Preferences & Memory

Agent memory (`~/.claude/projects/.../memory/`) is a private cache, not the source of truth. It is invisible to the user and to everyone else who works in these repos and processes, and it is not reliably consulted at the moments it matters. The instructions (`CLAUDE.md` and project docs) are loaded every session and visible to everyone — that is where durable rules must live.

- **Never let a durable preference or workflow rule live only in memory.** Whenever you commit one to a `user`/`feedback`/`project` memory file, **pair it — in the same session — with a GitHub issue whose explicit job is to immortalize it into the instructions** (the appropriate `CLAUDE.md`, global or project, or project docs). Link that issue from **both** the memory body and its `MEMORY.md` pointer so the two never drift apart.
- **Search before filing — check for an existing issue first.** Run the *Check for an existing issue before filing a new one* search first, against **dev-env for a global rule, the project's own repo otherwise**. A match means the rule is tracked: reference that issue from the memory body, and comment on it if this occurrence adds detail. File a new issue only when the search is empty. [Detail](../docs/REFERENCE.md#detail-memory-and-issue-pairing)
- **Filing the issue is the floor, not the finish.** The issue exists to drive the rule *into* the instructions — not to substitute for doing so. Prefer to make the instruction edit immediately and close the issue in the same session; a deferred issue is the backstop that keeps the rule from being forgotten, not a place to park it indefinitely.
- **Transient context is exempt.** Session-local or fast-changing notes (open-PR lists, in-flight working state) may live in memory and expire there — no issue required. This rule governs only durable, cross-session rules: the things a human collaborator would need to know to work the way the user expects.
- A non-blocking write-time hook (`memory-write-advisory.py`) reminds you when a memory file is written without such a link, the `/memory-audit` skill reconciles memory against the instructions on demand, and the `weekly-memory-audit` routine runs every Monday to sweep existing memory across all projects for never-ported durables (auto-filing deduped *promote* issues in the correct repo) and report stale/drift findings without modifying any memory file.

See [ADR-038](../docs/adr/038-durable-preferences-documented-in-repo.md), [ADR-048](../docs/adr/048-memory-immortalization-issue-pairing.md), [ADR-069](../docs/adr/069-weekly-memory-audit-routine.md).

---

## Platform & Environment

- **OS:** Windows 11, Git Bash terminal
- **Node:** 20.11.1 (managed by nvm for Windows; `.nvmrc` is set — run `nvm use` at session start if not already active)
- **Package manager:** npm (workspaces where applicable)
- **`jq` is NOT available.** Use `node -e` with a temp file for JSON parsing (no cleanup line: scratch leftovers are harmless — see the Scratch directory bullet below):
  ```bash
  TMPFILE="C:/Users/brown/.claude/scratch/tmp_$$.json"
  some-command --format json > "$TMPFILE"
  node -e "
    const d = JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));
    console.log('VAR=' + d.field);
  "
  ```
- **Never use `/tmp/`** for temp files — Node.js on Windows cannot resolve Git Bash Unix paths.
- **Scratch directory:** `C:/Users/brown/.claude/scratch/`: all temp files (`gh` output, JSON intermediaries) go here, never in a project repo. Cleanup is optional. If you clean up, run it as its own standalone `rm -f` Bash call naming a literal path or a non-recursive glob, never chained onto another command: only that form is auto-approved ([ADR-147](../docs/adr/147-scratch-only-rm-auto-allow-hook.md)), and shell state doesn't persist between calls. A file you'll delete later needs a fixed name, not `$$`.
- **`gh` CLI** is available and authenticated. The `project` scope must be added separately when needed: `gh auth refresh -s project`.
- **Prefer Git Bash** over PowerShell for scripting — PowerShell handles arrays and arithmetic differently and has caused failures in this environment.
- **Run an elevated command yourself with `sudo <command>`; don't ask the user to open an elevated terminal or run it for you.** Windows `sudo` is in Inline mode on this machine (`sudo config` shows it), so the output and exit code come back to the session. Ask the user only if `sudo` fails or is unavailable, and say what failed. This changes how a session runs a command, not what it may run: confirm-first rules for risky or hard-to-reverse actions still apply. Scripts are unchanged: they still fail fast and never relaunch (see *No spawning new terminal windows*).
  - A command's exit code comes back through `sudo`, but a non-zero code can come from `sudo` itself (49 means command not found), so read stderr first. Each call shows a UAC prompt, and answering it is the user's approval; if declined, report that and don't retry. A scheduled routine shouldn't count on `sudo`. From the Bash tool, prefix `MSYS_NO_PATHCONV=1` when an argument starts with `/`, or Git Bash rewrites it and `sudo cmd /c ...` hangs on an interactive prompt. [Detail](../docs/REFERENCE.md#detail-platform-and-environment)
- **`HKCU` writes from a Claude desktop session stay private to the app, so make and verify them from Task Scheduler.** The desktop app is a packaged (MSIX) app, and [Microsoft documents](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes) that its writes under `HKCU` are copied to a private per-user, per-app location. A session's Bash and PowerShell tools inherit this. The write reports success and reads back correctly from the session, while the rest of Windows never sees it.
  - **The route:** a process Task Scheduler starts isn't under the app. Put the change and its read-back in a script that logs to a file outside `AppData`, and run it as a one-shot task: `Register-ScheduledTask` with a `powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File <script>` action and `New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited`, which needs no elevation. Then `Start-ScheduledTask`, wait for the `Ready` state, check `LastTaskResult`, read the log and `Unregister-ScheduledTask`.
  - **A session's own reads can't settle it.** Once a session has written a key, it keeps reading its own value after the real one changes, and its delete doesn't reach the real key. After a fix from outside, check from a task.
  - **Scope.** A Terminal panel tab's write did reach Windows, on one run. An `HKLM` write through `sudo`, a file deleted under `AppData`, and scheduled tasks and packages removed from a session take effect for both sides; new files in a new `AppData` folder stay private. `sudo` doesn't get out of the private `HKCU` view, so for an elevated script that writes `HKCU`, use the same task with `-RunLevel Highest` (not yet run).
  - **Detecting it:** walk the parent processes for `\WindowsApps\Claude_*`. That's false under `sudo` although `HKCU` writes stay private, and true in a Terminal panel tab although its writes are real. `GetCurrentPackageFullName` doesn't work. [Detail](../docs/REFERENCE.md#detail-platform-and-environment)
- **Disk-full (ENOSPC):** `C:` saturating to 0 GB surfaces *indirectly* as truncated `node_modules` (npm exits 0 but a native binary is partial), not an obvious "disk full" error. If an install fails with a confusing downstream error, run `df -h /c` first. Recovery runbook + failure signature + dominant consumers: [`docs/REFERENCE.md` → Disk-Full (ENOSPC) Recovery](../docs/REFERENCE.md#disk-full-enospc-recovery). The `worktree-npm-install.py` gate now refuses a low-space install rather than truncate ([ADR-045](../docs/adr/045-pre-install-freespace-gate.md)).
- **Claude Code has no user-scope `settings.local.json`, only a per-project one; project-scoped app-written content belongs there.** The user file loads for every project, so when the app writes a key embedding project-specific content (an auto-mode environment scan, a per-project credential hint), move it to that project's `.claude/settings.local.json`, after confirming `git check-ignore -v .claude/settings.local.json`. Plain global toggles stay in the user file. [Detail](../docs/REFERENCE.md#detail-platform-and-environment)

---

## Tool Discovery

**Before concluding any tool is "unavailable in this session," check the full tool list in the system prompt first**, both directly-defined tools and deferred names in system-reminders. `ToolSearch` finds only deferred tools, so a zero-result search says nothing about a tool that is already callable. [Detail](../docs/REFERENCE.md#detail-tool-discovery)

---

## Authoring File Content

**The Write/Edit tools are the default for file *content*. The Bash tool is the default for *commands*.** These are different jobs, and the failure mode this rule exists to prevent comes from doing the first one with the second.

Content routed through a shell is parsed by the shell first: an apostrophe, backtick, backslash or code fence can fail the write or silently change what lands in the file.

- **Content → Write (new file, or a wholesale replacement) or Edit (a targeted change).** Prose, markdown, source code, JSON, a PR body, a config file, a commit-message file. The Write tool takes content as a parameter with no shell interpretation at all, so none of the failure modes above can occur.
- **Shell redirection is correct for exactly two things.** (1) **Another program's output** — `gh … --json body > "$TMPFILE"`, `npm test > out.log`, `git diff > patch` — where no literal ever crosses a quoting boundary. (2) **A single-line literal with no apostrophe, backtick, or backslash** — `echo done > flag.txt`. Anything longer or hazard-bearing goes through Write.
- **Editing an existing file → Edit, not `sed -i` / `node -e` / `py -3 -c`.** An in-place edit script is an inline literal too, and its target is the file itself. (A `sed`/`perl` regex kept in *single* quotes is not the hazard — the shell leaves a backslash alone there; a double-quoted or unquoted one is.)
- **A PR body, issue body, or commit message piped into a command's stdin is content too.** Write it to a file, then pass the path: `--body-file "$TMPFILE"`, `git commit -F "$TMPFILE"`. A heredoc feeding an *interpreter's* stdin (`py -3 - <<'PY'`) is a program, not file content, and stays fine.
- **"I quoted the heredoc delimiter" is not evidence of safety.** Reach for Write, not quoting. [Detail](../docs/REFERENCE.md#detail-authoring-file-content)

**Precedence over the bypass-permissions instruction.** A bypass-permissions session is told to make file changes through the shell. That governs shell work (running commands, inspecting state, reading files). For authoring file content, this section wins, in every permission mode.

Enforced by `pre-tool-use-shell-content-write-guard.py`, which blocks an inline literal routed to a file or to a `gh`/`git` prose argument when it is multi-line or carries an apostrophe, backtick, or backslash. Genuine exception: prefix `ALLOW_SHELL_CONTENT_WRITE=1` (Bash) or precede with `$env:ALLOW_SHELL_CONTENT_WRITE=1` (PowerShell). See [ADR-138](../docs/adr/138-shell-content-write-guard.md).

The four engineering-journal content files (`*.stub.md`, `*.manifest.jsonl`, `open-prs/<N>.json`, `tiles/<N>.json`) are the **strict special case** of this rule: they are blocked unconditionally, regardless of how short or clean the content looks, because they always carry free prose or a path field. See Engineering Journal → Stub file workflow and [ADR-129](../docs/adr/129-journal-shell-write-guard.md).

---

## CLI Scripting Checklist

Before writing a `gh` or other CLI automation script — or acting on what one reports:

1. Run `<command> --help` first to confirm flag names and syntax
2. Confirm which JSON tools are available (`jq` is NOT available — use `node -e`)
3. Write temp files to `C:/Users/brown/.claude/scratch/`, not `/tmp/` or a project repo directory
4. Check whether any additional `gh` auth scopes are needed
5. **An absence claim needs an absolute path, the right ref, and, after a write, a fresh read.** Before concluding a file, pattern, or config is *not present*, re-run the check rooted at the repo root (an absolute path, or `git -C <root>`) with stderr showing. Each of these turns a partial view into something that looks like a genuine miss ([Detail](../docs/REFERENCE.md#detail-cli-scripting-checklist)):
   - **cwd scoping.** `git ls-files`, `git ls-tree`, `git grep`, `find`, and `ls` report only what is under the cwd, and a `cd` from an earlier call can persist. Scope every command explicitly.
   - **Ref scoping.** A working tree, `HEAD`, and `origin/main` each answer only for themselves. Before claiming an identifier is free (an ADR number, a `## Testing` item, a migration or fixture filename), check all **three** surfaces: `origin/main` (merged), the open-PR set (`gh pr list`, `gh pr diff <N> --name-only`), and remote branches (`git fetch origin`, `git for-each-ref --sort=-committerdate refs/remotes/origin`, `git ls-tree -r <ref> --name-only -- <dir>/` on recent ones; filter by recency, not ancestry). The third is the one concurrent sessions collide in. For an identifier inside a file's content, read the blob: `gh api "repos/{owner}/{repo}/contents/<path>?ref=<ref>" -H "Accept: application/vnd.github.raw"`.
   - **Visibility blind spots.** `Glob` and plain `git status` skip gitignored files, and `git ls-tree` shows only committed content. Verify with `ls -la` / `Get-ChildItem -Force` or `git status --ignored`.
   - **Suppressed failure.** Never pair an absence check with `2>/dev/null`. MSYS path conversion mangles `git show <ref>:<path>` when the path segment after `:` starts with a dot, and the redirect hides the `fatal:`. Prefix `MSYS_NO_PATHCONV=1`, or read the blob over the API.
   - **Read-after-write staleness.** A *filtered list* read can under-report just after a write with a clean `200`. Never take one as evidence a write failed: re-read after a delay, or check the single-object endpoint (`gh api "repos/{owner}/{repo}/issues/{n}"`). A retry with an unrelated flag can look like the fix when elapsed time was what changed.
   - Related: a partial pathspec commit can leave half a rename uncommitted; see Engineering Journal → Stub file workflow.
6. **A locale-decoded pipe both misreports corruption and creates it; check raw bytes before believing either.** Here `locale.getpreferredencoding(False)` is **cp1252**, so piping text through an interpreter's stdin, or `subprocess.run(..., text=True)` with no `encoding=`, can reinterpret UTF-8 (an em dash becomes U+00E2, U+20AC, U+201D) with no error. `_winsubp.py` protects committed hooks, not ad-hoc inline scripts. ([Detail](../docs/REFERENCE.md#detail-cli-scripting-checklist))
   - **Read direction: the pipe misreports.** Before concluding a file is corrupted, check its raw bytes (`open(path, 'rb')`), bypassing the pipe.
   - **Write direction: the pipe creates the corruption and you ship it.** **Never round-trip remote content through a locale-decoded pipe.** For a read-modify-write of any GitHub body, read bytes and `.decode("utf-8")`, or redirect to a file and read it with `encoding="utf-8"`. Settle corruption **numerically, never visually**: `collections.Counter(ord(ch) for ch in body if ord(ch) > 127)`. Recovery: `corrupted.encode("cp1252").decode("utf-8")`, lossless only while no U+FFFD has replaced a character.

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

Projects that run process experiments **should** add an `## Experiments` section — a corpus catalog, an instrument registry naming each judge/check's known-good AND known-bad calibration references, the results home, and default tier triggers. The *Plan-then-optimize → Pass 3* Experimental-validity dimension and the `/experiment-audit` skill defer to it the way the Observability dimension defers to `## Observability`; **absence is advisory, not blocking** (see the global `## Experimental Rigor` section and [ADR-115](../docs/adr/115-experimental-rigor-protocol.md)). Pass 3's **Gate calibration** dimension defers to the *same* instrument registry — a standing gate's known-good/known-bad references belong there rather than in a section of their own ([ADR-144](../docs/adr/144-gate-calibration-pass-3-dimension.md)).

---

## Git Workflow

- **Create an issue before changing files.** `gh issue create` describing the problem/goal, not the implementation, before any edit. Single-line change: ask first; anything longer warrants an issue without prompting. Exception: engineering-journal `draft/YYYY-MM-DD` branches. Every PR references the issue via `Closes #N`.
- **Every implementation issue has a `## Acceptance criteria` checklist, and every PR is verified against it.** Each `- [ ]` item names how it is checked: a command and its expected result, a test, a file and what it contains, or an observable behaviour. "Works", "is clean", and "docs updated" don't qualify. Investigation and tracking issues put `Exempt: <reason>` as the section's first line. `/review` Step 2g scores each criterion `met`, `not met`, or `not verifiable`, and a missing section is a finding, never a pass. A `not met` criterion is fixed in the PR, or deferred by amending the issue to link a follow-up issue that names the criterion as its own work. After merge, record the verification on each linked issue. Schema: [`claude/templates/acceptance-evidence.md`](templates/acceptance-evidence.md). [Detail](../docs/REFERENCE.md#detail-acceptance-criteria)
- **Check for an existing issue before filing a new one.** Before `gh issue create` for a bug or defect, search open and closed issues, as its own command: `gh issue list --search "<keywords>" --state all` (add `--repo <owner>/<repo>` when the defect lives elsewhere). An **open** match means it's tracked: reference it and comment with any new detail. A **closed** match means verify the fix (`gh issue view <N> --json state,closedAt` plus the closing PR's diff); if it didn't stick, reopen or reference it. [Detail](../docs/REFERENCE.md#detail-duplicate-checks)
- **Check for an existing open PR before implementing.** Before implementing any issue, confirm no open PR addresses it: `gh pr list --search "<issue-number-or-keywords>" --state open`, or `gh issue view <N> --json` for linked PRs. A match means **surface it to the user via `AskUserQuestion`**; closing, superseding or consolidating isn't yours to decide alone. See [ADR-125](../docs/adr/125-check-open-pr-before-implementing-issue.md).
- **Multi-PR decomposition: top-level issue + sub-issues.** When a prompt or initiative maps to multiple PRs, create a **top-level issue** first to capture the full scope and rationale, then a **sub-issue** for each individual PR (referencing the top-level issue in the sub-issue body so the hierarchy is navigable from both directions). Each PR's `Closes #N` references its sub-issue; the top-level issue is closed only after all sub-PRs have merged. Tiles spawned by the decomposing session must embed **both** issue references in their prompt — the top-level issue number/URL and the specific sub-issue being addressed — so the spawned session can maintain continuity without the user re-establishing context. ([ADR-059](../docs/adr/059-multi-pr-issue-hierarchy.md))
- **Test before PR.** Before `gh pr create`, run the project's `## Testing` command; tests must pass (or the failure be explained). Put what was tested + the `Tests: N passed, N skipped, N failed (duration)` line + outcome in the PR body. **No `## Testing` section → stop and ask the user to add one before opening the PR.** Also run, before `gh pr create`:
  - **Coverage gate** — add tests for new testable behavior, or document the deferral in the PR body ([ADR-022](../docs/adr/022-test-coverage-gate-before-pr.md)).
  - **Suppression + test-integrity greps** from `## Code Quality` — any new suppression, skip marker, deleted test, lowered threshold, or bypass flag needs a PR-body justification or it blocks the PR; a non-zero skipped count needs a per-skip justification ([ADR-026](../docs/adr/026-suppression-policy.md), [ADR-029](../docs/adr/029-test-integrity-policy.md)).
  - **Pre-existing failure check** — if `baseline_test_failure_tracking` is on, run `baseline-tests diff`: new failures block; pre-existing failures in touched files are fixed inline (≤ ~20 LOC / ~15 min) or filed ([ADR-030](../docs/adr/030-baseline-test-failure-policy.md)).
- **ADR-warrant check** at three checkpoints: (1) after a plan is approved / first edit, (2) after `gh pr create`, (3) before `gh pr merge`. Warranted when the change touches a rule/hook/skill/settings documented in `claude/`, restructures a `claude/` directory, sets a workflow rule other CLAUDE.md files reference, or has rationale `git log` won't recover. Scan `docs/adr/INDEX.md` tags first. Global rules → dev-env [`docs/adr/`](../docs/adr/INDEX.md); project decisions → that project's `docs/adr/`. **Write the ADR + update `INDEX.md` before merging a qualifying change.** **Prefer the smallest durable home:** a **new ADR** for a decision that could later be reversed on its own; an **amendment** for anything that refines, corrects, extends, or bounds a recorded decision; a **plain CLAUDE.md line** when the rule contains its own reasoning. **If you cannot name an alternative you rejected, it is not a new ADR.** For clearly ADR-worthy multi-file work, create the template at checkpoint 1. [Detail](../docs/REFERENCE.md#detail-adr-warrant)
- **Never commit directly to `main`.** Branch + PR always. Exception: local-only repos with no remote.
- **Branch naming:** `feat/`, `fix/`, `config/`, `chore/`, `draft/` — match the repo's convention.
- **Branch creation in squash-merge repos:** use `new-branch <name>` (sources `~/.claude/scripts/new-branch.sh`) or `git checkout -b <name> origin/main`. Never cut from a squash-merged branch (its commits are gone from main; rebase fails). Verify `git merge-base HEAD origin/main` equals `git rev-parse origin/main`.
- **Verify branch before editing and before every commit** with `git branch --show-current`; worktrees and `git checkout` shift context silently. If wrong: switch if no edits are on disk; else `git stash`, switch, `git stash pop`. Pass `--head <branch>` to `gh pr create` rather than relying on the current checkout. `pre-commit-branch-check.py`, `pre-pr-create-check.py`, `pre-merge-branch-check.py` and `pre-bash-drift-check.py` flag drift without blocking. [Detail](../docs/REFERENCE.md#detail-branch-checks)
- **Worktree Bash commands must not `cd` to the main repo.** In a worktree session, don't prefix git/npm commands with `cd C:/Users/brown/Git/<repo>`: edits land in the worktree while commits and `git status` act on main. Use the default cwd, and `git -C <path>` for another repo. Tell: `git status` clean but Read shows uncommitted edits ([ADR-066](../docs/adr/066-worktree-session-safety-rules.md)).
- **A bare local `main` ref can be silently stale: read `origin/main`, not `main`, from a worktree or compose flow.** `git fetch` doesn't move local `main`. Use `git show origin/main:<path>`, or fast-forward first with `git fetch origin main:main` when `main` isn't checked out anywhere. `session-start-sync.py` does this once at session start, so it can go stale again mid-session. [Detail](../docs/REFERENCE.md#detail-branch-checks)
- **`EnterWorktree` targets the session's primary repo only; it cannot be pointed at a different repo.** For repo B from a repo A session, use `git worktree add <path> -b <branch> origin/<default-branch>` against repo B, and reach it with explicit paths or `git -C <path>`, never `cd` ([dev-env#610](https://github.com/brownm09/dev-env/issues/610)).
- **Two more scoping traps in manual cross-repo git.** Once a worktree tool has fired, a plain `cd` may not persist across Bash calls, so prefix each command with its own `cd`/`-C`. And `-C <repo>` or a leading `cd <path> &&` scopes only the command it's attached to, not later ones after `&&`, `;` or `||`, so give every command its own scoping. Either can create a worktree, branch or commit in the wrong repo silently: check `git remote -v` after any `worktree add`/`checkout`/`clone`. [Detail](../docs/REFERENCE.md#detail-cross-repo-git)
- **Never mutate git state directly in a repo's canonical (non-worktree) checkout.** Concurrent sessions sharing it thrash HEAD out from under each other. `pre-tool-use-canonical-mutate-guard.py` hard-blocks mutating git commands (`checkout`, `switch`, `commit`, `merge`, `rebase`, `reset`, `cherry-pick`, `revert`, `stash pop`/`apply`, `branch -d`/`-D`, bare `pull`) and `gh pr merge` with `-d`/`--delete-branch`, whether run at a canonical root or aimed at one with `-C`/`--git-dir`/`--work-tree`. Isolate into a worktree first (`EnterWorktree` or `git worktree add`), or override with `ALLOW_CANONICAL_MUTATE=1 <command>` after confirming no other session is active there. The engineering-journal stub workflow is permanently exempt: it deliberately shares one canonical via `-C`. [Detail](../docs/REFERENCE.md#detail-canonical-checkout)
  - **GraphQL rate limits.** Sessions share one budget, and `gh pr merge`/`comment`/`view --json` spend GraphQL. The separate REST bucket is often healthy, so fall back to `gh api`. **Judge a GraphQL rate-limit error from the failing call:** one token has two counters and `gh api rate_limit` shows one per read, so a reading neither confirms nor refutes it, and a later pass doesn't mean budget is back. Recovery comes at the reset of a counter reading 0; if no read shows 0, the time isn't known.
- **PR first, then merge.** Open the PR immediately after pushing; don't prompt the user to run `gh pr create`.
- **Turn on Auto-fix for every PR a session opens.** In the desktop app, right after opening the PR, call `mcp__ccd_pr__get_status` (`bind_pr` if it doesn't report the PR). If it reads off, call `mcp__ccd_pr__set_monitor` with `auto_fix: true` and the PR's URL; this rule is the user's request, so don't offer or ask first. In the default mode that call waits on an approval card. If it's refused because the app isn't sure this session opened the PR, don't retry: tell the user they can turn it on. Skip where the `ccd_pr` tools don't exist. Auto-merge stays off. A wake from your own `/review` comment needs only the normal findings disposition. [Detail](../docs/REFERENCE.md#detail-pr-monitoring-and-ci)
- **Write the journal stub immediately after `gh pr create`** — defer and a corrupted session loses all context. Then report the PR URL, prompt `/compact`, and after compaction run `/review <PR-URL>` (posting the review as a PR comment is the skill's default — no flag needed). **Address all findings — fixed-in-PR or filed-and-linked, never "left as-is"** — and merge in the same session. Each finding's disposition (fixed in `<sha>` | filed `#N`) goes in a "Review findings disposition" PR-body section; the `pre-merge-findings-gate` hook blocks merge until it's present, and `/review` applies the `reviewed-by-claude` label ([ADR-028](../docs/adr/028-all-findings-merge-gate.md), [ADR-039](../docs/adr/039-merge-gate-findings-enforcement.md)).
- **Fix everything, always — never ask which findings to address.** Resolve all `/review` / `/code-review` findings as part of the same work; default to fixing in-PR, file-and-link only when genuinely out of scope. Prefer the root-cause fix over the smaller band-aid. Escalate only for a product/design decision the code can't settle or a breach of the *Code Quality → Fix errors on encounter* scope guard ([ADR-028 → Addendum](../docs/adr/028-all-findings-merge-gate.md)).
- **Write a stub on PR merge**; a merge is a session boundary. After `gh pr merge`, include the `PostToolUse` hook's `### Usage Snapshot (post-merge)` block verbatim in the stub; if it prints a `[usage-snapshot] Skipped:` line instead, paste that. Same session as the PR open: update the existing stub. New session: update the opening stub for minor merges, or write a new one for substantial follow-up. Either way set `prs_closed:[N]` in this session's manifest shard and delete `sessions/<project>/open-prs/<N>.json` (a PR predating shards: remove its one line from `open-prs.jsonl`). [Detail](../docs/REFERENCE.md#detail-stub-on-merge)
- **Multiple PRs/merges in one session:** defer stub writing until after the last `gh pr create` / `gh pr merge`; produce one consolidated stub. Never write an intermediate stub between PR opens or merges.
- **PR closed without merging:** the stub was already written at creation; stopping is optional.
- **After merging:** move the linked project board item to Done (command is project-specific — see the project's CLAUDE.md; for dev-env, its GitHub Project section). On each linked issue, record the acceptance-criteria verification: tick each box whose `/review` row is `met`, or post the review's evidence block as a comment beginning `Verified by <PR URL>:` (schema §5 of [`claude/templates/acceptance-evidence.md`](templates/acceptance-evidence.md)). When a work stream completes (milestone, issue group, feature sequence), move the item from Active Work to Shipped in the project roadmap — don't leave roadmaps contradicting shipping history.
- **Capture follow-ups as tiles.** A tile is a one-click session proposal made with `spawn_task`. [Detail](../docs/REFERENCE.md#detail-tiles)
  - **Two checkpoints, as a floor:** (1) a PR reaches merged state, however it merged; (2) a session creates an issue still unresolved at session end. The same discipline fires the moment a genuine follow-up surfaces in any session.
  - **Write out what you considered:** scan for out-of-scope fixes, deferred work, tech debt and ideas, and record each as `→ tiled (task_id / #N)` or `→ not tiled, because <reason>`. "No follow-ups" is valid only as the visible result of that scan.
  - **For each genuine follow-up:** tile it yourself at once, file a tracking issue in the same repo and reference it in the prompt, then write the shard `sessions/<project>/tiles/<issue-number>.json`. Without `spawn_task`, file the issue anyway.
  - **Don't ask.** Never convert a known follow-up into a scheduling question ("should I start it now?"), even for the next unit of a multi-PR initiative. Plan files can't defer tiling, and plan approval doesn't waive it; only an explicit user instruction in this session (e.g. "skip tiles") does, and it waives all tiling for the session.
  - **A required cross-session hand-off is a tile, not a chat brief:** a blocked or corrupted environment, a required restart, or "finish this in a fresh session" gets a self-contained tile, even in plan mode.
  - **Blocked on another issue's future phase:** in the same session, add a forward reference on the blocking issue stating the trigger condition, in addition to the follow-on's own back-reference.
  - **Hard (finish-to-start) dependency:** never co-spawn the two. Spawn only the head of the chain, and have its prompt spawn the next tile (with its issue and shard) once its PR merges. Related but independently workable items spawn together.
  - **Look ahead at every spawn:** if this tile's completion will predictably surface a next thread, bundle it when same-subsystem, small and bounded. Otherwise add a prompt instruction to re-verify it after merge and spawn a separate tile, naming the issue now.
  - **A merge in an unchained session must not end idle.** When this session's opening prompt named no follow-on work (no issue/PR reference, no `=== CHAIN` marker): tile the next thread if one is determinable. Only when none is, rank open issues (1) `start-here`-labeled, (2) the first unchecked item of the newest open `retro-action` issue, (3) open issues in the merged PR's subsystem, (4) most recently updated; dedupe against `sessions/<project>/tiles/` shards and `list_sessions`; offer the top 3 via `AskUserQuestion`; tile the choice. That ask is the one exception to *Don't ask*. `stop-tile-enumeration-gate.py` enforces this floor at Stop.
- **Auto-merge is off by design; `--auto` is now mechanically gated when available.** Every merge is in-session via `gh pr merge --squash --delete-branch` after the post-PR checklist (`/review`, ADR-warrant, doc-reconciliation, board transition, roadmap move). `pre-auto-merge-checkpoint-gate.py` blocks `--auto` unless the PR's latest qualifying comment carries clean-or-disposed `review-findings` and complete `premerge-checkpoints` markers fresh for the head commit, failing closed with no override. `allow_auto_merge` is a separate per-repo setting that drifts silently, so check before relying on it: `gh api repos/brownm09/<repo> --jq .allow_auto_merge`. [Detail](../docs/REFERENCE.md#detail-auto-merge)
- **Wait for CI with one background watch, then merge.** Once only CI stands between a PR and the merge, run `gh pr checks <N> --watch --interval 60` once with `run_in_background: true`, and once more after each later push. Exit 0 means every check passed; anything else needs a look, and "no checks reported" exits 1 at once, which can mean checks haven't registered or the PR is conflicting. This overrides the app's no-polling instruction for this one command only; don't use `CronCreate`, `ScheduleWakeup`, `/loop`, `Monitor` or a `gh` loop. Skip it if the user said to hold the merge. If GraphQL is exhausted, read state from `mcp__ccd_pr__get_status` or REST (`gh api "repos/{owner}/{repo}/commits/<sha>/check-runs"`) and restart the watch after the reset on a counter reading 0. With Auto-fix on, a failure can wake the session twice; handle it once. [Detail](../docs/REFERENCE.md#detail-pr-monitoring-and-ci)
- **Sequence merges around a large refactor.** Land a sizable refactor *ahead* of smaller concurrent PRs, or hold the small ones and rebase them once after it lands, rather than rebasing the large diff every time `main` advances.
- **PR branch state comes from the remote, not the local worktree.** Before judging whether a PR addressed findings or is mergeable: `git fetch origin <headRefName>`, then read via `git show origin/<headRefName>:<path>` — never the local tree, which may be stale or on another branch ([ADR-004](../docs/adr/004-pr-review-reads-from-remote.md)).
- **Missing-file investigation: verify `origin/main` before concluding a feature is unbuilt.** The worktree base may be behind. Run `git fetch origin`, then `git ls-tree -r origin/main --name-only | grep <file>` and `git show origin/main:<path>` before building ([ADR-066](../docs/adr/066-worktree-session-safety-rules.md)).
- **Runbooks** for merging a PR from a worktree, separate clones for independent parallel work, deleting a remote branch in web sessions ([ADR-035](../docs/adr/035-git-push-delete-web-session-constraint.md)), worktree deregistration recovery ([ADR-066](../docs/adr/066-worktree-session-safety-rules.md)), stacked-PR squash-merge sequencing, remote git ops hanging on the Git Credential Manager GUI ([ADR-047](../docs/adr/047-standardize-gh-credential-helper.md)), post-merge follow-up tiles ([ADR-046](../docs/adr/046-post-merge-followup-tiles.md)), and one-time `core.hooksPath` setup are in [`docs/REFERENCE.md` → Git Workflow Runbooks](../docs/REFERENCE.md#git-workflow-runbooks).

### Git failure patterns

- **CI not firing: a merge conflict silences GitHub Actions.** A `CONFLICTING` PR never fires `pull_request` events, so `gh pr checks` reports "no checks reported". Diagnose with `gh pr view <N> --json mergeable,mergeStateStatus`; rebase onto `origin/main` and force-push with lease. [Detail](../docs/REFERENCE.md#detail-ci-not-firing)

- **A worktree holding the base branch blocks `gh pr merge --delete-branch`'s local step.** Symptom: `fatal: '<base>' is already checked out at '<path>'` on a PR that did merge; both branch deletes were skipped. Avoid it with two API calls: `gh pr merge <N> --squash`, then `gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"`. If already hit, run just the delete. [Detail](../docs/REFERENCE.md#detail-base-branch-worktree)

- **Bare `--force` after rebase auto-closes any open PR on the target branch,** and a plain `gh pr reopen` then fails. When `--force-with-lease` rejects with `(stale info)`, run `git fetch origin` and retry the lease; never fall back to `--force`. Already closed: [the runbook](../docs/REFERENCE.md#bare---force-after-rebase-auto-closes-any-open-pr-on-the-target-branch) has the reopen workaround and the replacement-PR route. [Detail](../docs/REFERENCE.md#detail-bare-force)

- **Squash-merging a stacked PR's base leaves the child `CONFLICTING`; a plain rebase fixes it.** If the parent merged without `--delete-branch`, no new PR is needed: `git fetch origin main`, `git rebase origin/main` (no `--onto`; patch-id matching drops the squashed commits), `git fetch origin && git push --force-with-lease`. [Detail](../docs/REFERENCE.md#detail-stacked-squash)

- **`.gitattributes` eol retrofit: `git checkout`/`checkout-index` silently no-op on existing files.** After adding `eol=lf`, CRLF bytes stay on disk though `git check-attr` and `git diff` look clean. On a clean tree only (`git diff --quiet && git diff --cached --quiet || exit 1`), since this discards uncommitted edits: `git ls-files -z | xargs -0 rm -f --`, then `git checkout-index -f -a`. Verify with a byte-level CRLF count or a fresh clone, not `git diff`. [Detail](../docs/REFERENCE.md#detail-eol-retrofit)

- **A `$`-anchored regex silently fails on CRLF lines.** Before trusting a zero-match result from an end-anchored pattern, re-run without the `$`, or check the file's line endings. [Detail](../docs/REFERENCE.md#detail-crlf-regex)

---

## Dev-Env & Project Boards

Dev-env's own architecture (the `~/.claude/` symlink/junction map, the worktree-on-`main` rule, routine authoring) and its GitHub Project IDs, field IDs, and board procedures live in the **dev-env project `CLAUDE.md`** (repo root) — they are project-specific and must not load into every project. Each project's `CLAUDE.md` owns its own `## Testing` commands and board configuration; the global "Test before PR" rule defers to that section.

**GitHub Projects — single-select option mutation hazard (applies to every project).** Running `updateProjectV2Field` with `singleSelectOptions` is a full replacement — passing the existing options unchanged still produces new option IDs and drops every item's prior assignment for that field. Validated against lifting-logbook on 2026-05-08: an Observability epic addition wiped assignments on all 89 project items despite passing the full option list. Recovery precedent: [lifting-logbook#203](https://github.com/merickvaughn/lifting-logbook/issues/203).

Procedure (mandatory before adding/removing/renaming any single-select option on any project):

1. Snapshot current per-item assignments to a git-tracked file under the project repo's `.claude/backups/`.
2. Commit the snapshot.
3. Run the mutation with the full desired option list.
4. Update the project's CLAUDE.md option-ID table with the regenerated IDs in the same PR.
5. Refresh every machine-local cache of the old option IDs, in the same PR: most commonly `.claude/hook-config.json` and `.claude/propose.json`. Don't assume `hook-config.json` is gitignored; lifting-logbook tracks it. [Detail](../docs/REFERENCE.md#detail-project-boards)
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

### Scope of a change

- **Simplicity first.** Write the least code that meets the issue. No features, options, or configurability nobody asked for; no abstraction with a single caller; no handling for errors that cannot happen. If a senior engineer would call it overcomplicated, or 200 lines could be 50, rewrite it before opening the PR.
- **Surgical diffs.** Every changed line should trace to the PR's issue. Match the surrounding style even where you'd choose differently, and don't reformat, re-comment, or refactor code next to your change. Remove the imports, variables, and functions *your* change left unused, and nothing else.
- **How this fits *Fix errors on encounter*.** That rule still applies. Surgical scope governs *this PR's diff*, and an unrelated fix goes in its own issue and PR, as that rule's "separate PR" bullet already says. Pre-existing dead code that isn't an error is not something you encountered and broke: mention it or tile it, but don't delete it in passing.

Adapted, not copied, from [andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills/tree/2c606141936f1eeef17fa3043a72095b4765b9c2) by Jiayuan Zhang ([@forrestchang](https://github.com/forrestchang)), which distills [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls. `/review` checks both rules as non-blocking `[maintainability]` findings. See [ADR-149](../docs/adr/149-scope-of-a-change-rules.md).

### No spawning new terminal windows (Windows scripts)

For PowerShell or batch scripts on Windows, do not author or accept patterns that spawn a new console window or trigger UAC re-launch from inside the script: `Start-Process -Verb RunAs` self-relaunch, `Start-Process ... -NoExit` sibling consoles, `cmd /c start ... powershell ...`, or any equivalent. In non-interactive contexts the prompt hangs or vanishes and leaves orphan processes. [Detail](../docs/REFERENCE.md#detail-no-terminal-spawn)

**Instead:** put `#Requires -RunAsAdministrator` at the top of any script that needs elevation (fail-fast), or use an inline `IsInRole(Administrator)` check that `Write-Error`s and `exit 1`s. The script never relaunches itself; a session that needs to run it elevated uses `sudo` (see *Platform & Environment*). For "keep the window open" debugging, redirect to `Documents\LOGS\<script>_<ts>.txt` rather than `Start-Process -NoExit`.

**Pre-existing exemption (closed allowlist).** `win11-init-tools/install_base_apps.ps1`, `win11-init-tools/set_irfanview_image_assoc.ps1`, and `win11-init-tools/configure_dev_env.ps1` already use self-relaunch for the Explorer-double-click UX. New files needing elevation must use `#Requires` instead.

**At review time**, surface as a **blocking [reliability] finding** when a new file introduces any of the patterns above. Do not downgrade on the grounds that a project CLAUDE.md "documents" the self-relaunch pattern — that documentation only covers the allowlist.

See [ADR-041](../docs/adr/041-no-terminal-spawn-in-windows-scripts.md).

### Back up before you mutate (data & config)

Any operation that mutates persistent **data or configuration** — a database row or schema, a registry key, a config file, a file association, an installed-package or environment (`PATH`) change — must be reversible by construction. Before the first mutating write:

1. **Capture prior state to a restorable artifact first**, read *live* at backup time (not from an earlier diagnostic read), and **refuse to proceed if a restorable backup cannot be captured** — changing state you could not back up leaves no way back.
2. **Provide an idempotent restore path** that returns the system to the *captured* state, not merely a generic "reset to default"; fall back to a default only when no backup exists.
3. **Prefer a written-if-absent anchor that restore does not delete** — so repeated apply runs never overwrite the original captured state and repeated restore runs converge (re-baseline by deleting the artifact deliberately).
4. **Verify each mutating write by read-back**, and record a no-op as a skip without inflating the change count. Read-back from a Claude desktop session's shell doesn't prove an `HKCU` write reached Windows, because the session is reading its own private copy: read back from a process Task Scheduler started (see *Platform & Environment*).

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

**Scope note.** These patterns target JS/TS test frameworks. For pytest, Go `testing`, or Rust, extend them to the language's idioms (`@pytest.mark.skip`, `pytest.skip(`, `t.Skip(`, `#[ignore]`) first; a clean JS-only grep in a non-JS repo proves nothing. Two patterns over-match, so review matches by hand: `--bail` matches non-test contexts, and the threshold grep also flags increases, which aren't violations. [Detail](../docs/REFERENCE.md#detail-test-integrity)

If any pattern matches:
- Each line must map to a Rule 1 justification note in the PR body, **or**
- The violation must be reverted.

A PR that adds integrity violations with no PR-body justification is not mergeable.

See [ADR-029](../docs/adr/029-test-integrity-policy.md) for rationale.

### Pre-existing test failure policy

This policy guards against *inherited red state*: tests already failing when the branch was cut. A *pre-existing failure*'s fingerprint is in the branch-start baseline; a *new failure*'s isn't. See [ADR-030](../docs/adr/030-baseline-test-failure-policy.md). [Detail](../docs/REFERENCE.md#detail-baseline-failures)

- **Rule 1: baseline at branch creation.** Opt in with `"baseline_test_failure_tracking": true` in `.claude/hook-config.json` (Jest only; `"test_command"` overrides `npx jest --json --silent`). `new-branch <name>` then snapshots automatically; raw `git checkout -b` skips it and the pre-PR hook flags that.
- **Rule 2: fix-on-touch.** A failure in a file this branch modified gets fixed inline if it's about 20 lines or 15 minutes; otherwise file an issue and reference it in the PR body. Failures in untouched files are noted in the PR body, not blocking.
- **Rule 3: pre-PR diff (required before `gh pr create`).** Run `baseline-tests diff` from the repo root. Any **new** failure blocks the PR (exit 1). A PR with new failures, or with no PR-body note for outstanding touched-file failures, is not mergeable.

### Privilege-restricted test defaults

When a project enforces a security or isolation guarantee through a specific runtime identity (a restricted DB role, a scoped service account, a tenant-scoped credential), tests and local dev must default to that restricted identity; the privileged one is the opt-in exception. A test passing under admin proves nothing about the boundary. Before adding coverage for a boundary, confirm the test runs under the identity it's scoped to, and through the real framework/DI machinery rather than a hand-built object graph. For any change touching such a boundary, state which identity the coverage runs under in the Pass 3 Security audit. See [ADR-089](../docs/adr/089-privilege-restricted-test-defaults.md). [Detail](../docs/REFERENCE.md#detail-privilege-tests)

### Dependency and lockfile policy

In any npm repo, `package-lock.json` must stay in sync with `package.json`, or `npm ci` fails with `EUSAGE` and `main` can go red. See [ADR-036](../docs/adr/036-lockfile-drift-prevention.md). Applies to npm; extend the check per repo for Yarn or pnpm. [Detail](../docs/REFERENCE.md#detail-lockfile)

- **Rule 1: edit a dependency, regenerate the lockfile, commit both together.**
- **Rule 2: pre-PR sync check (required before `gh pr create` when any `package.json` changed).** From the repo root: `npm install --package-lock-only --ignore-scripts && git diff --exit-code package-lock.json`. A non-empty diff means run `npm install`, commit, re-check.
- **Rule 3: upstream in-range float.** A new in-range publish can flag the lockfile with no change of yours. **Uncommitted local float:** discard it (`git checkout -- package-lock.json`). **CI red on a pushed branch:** regenerate and commit (`npm install`; verify `npm ci` locally first).
- **Scope the CI sync gate to `pull_request`, not `main` pushes,** so an upstream publish can't redden `main`; `npm ci` on `main` still catches install-breaking drift.

---

## Experimental Rigor

Any comparative claim about a process change — A/B arms, before/after, challenger vs. incumbent — is an experiment, and carries a declared tier *before* results exist. The full protocol lives in the `/experiment-audit` skill (`design` mode before generating anything, `verdict` mode before concluding anything); see [ADR-115](../docs/adr/115-experimental-rigor-protocol.md). The one law: **no conclusion without a design that could have produced the opposite conclusion.**

- **Tier 0 — probe.** An n=1 exploration, declared in three lines in the tracking issue. Cheap and encouraged. Legal endings *only*: "signal — escalate to Tier 1," "infeasible as specified," or "shelved — untested." Never adopt / reject / "failure" / "success."
- **Tier 1 — test.** Required before any adopt/reject of a standing process. A pre-registration frozen in the tracking issue *before* generation fixes the hypothesis and one **primary outcome construct** (the outcome, not a proxy of it), one manipulated variable with a held-constant list, criteria traced to that construct and classified primary vs. secondary (a secondary can diagnose but **never alone decide the verdict**: threat T10), a neutral fresh-run baseline, a corpus fixed in advance (n≥3 or all-available, not solely incumbent-failure cases, ≥1 known-good input), an incumbent-influence inventory, instruments **calibrated against known-good AND known-bad references before any arm is scored** (uncalibrated ⇒ quarantined; a check built from one arm's known failure is a diagnostic until calibrated), stage-matched processing, blinded order-randomized scoring in fresh context with drafter ≠ scorer, and a win bar with n and k fixed (no generate-until-win). Probe data may inform the design; it never scores in the verdict. [Detail](../docs/REFERENCE.md#detail-experimental-rigor)
- **Adoption rider.** When the decision would *replace* a standing process: ≥1 held-out input scored once at the end, plus a named post-adoption tripwire (a golden-set / baseline entry) and rollback path.
- **Verdict ∈ {supported, refuted, inconclusive — confounded by X}**, read off the **primary construct** (never a secondary proxy — deciding failure on a proxy is criterion substitution), with per-input results (an aggregate may not hide losses) and a scope statement ("holds for \<corpus\> under \<SHAs / conditions\>"). An unfair or unregistered design cannot produce adopt/reject — only a hypothesis for a proper run. Deviations from the pre-registration are listed, or the verdict is void.

Projects that run experiments should declare an `## Experiments` section (see [Per-Project CLAUDE.md Requirements](#per-project-claudemd-requirements)); Pass 3's **Experimental validity** dimension enforces the design half at plan time. A Stop-hook backstop (`stop-experiment-verdict-gate.py`) nudges once when a conclusion is stated with no `/experiment-audit` run.

**Scope boundary.** This section governs *experiments*; standing *gates* are Pass 3's Gate-calibration dimension. The calibration law applies to both, but tiers, pre-registration, blinding, win bars and verdicts don't apply to a gate, which owes only the provenance of its threshold ([ADR-144](../docs/adr/144-gate-calibration-pass-3-dimension.md)).

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

**Session length:** `turn-count-hook.py` warns at 8 prompts and every 4 after, and at 40k tokens of context and every 20k after. Consider `/clear` or `/compact` if the task has shifted. Override the prompt threshold per project with `"turn_threshold": N` in `.claude/hook-config.json`. [Detail](../docs/REFERENCE.md#detail-context-efficiency)

**Token source:** Whenever you estimate or spend tokens, for any task in any session, say whether they come from the subscription or the API.

- **API use needs approval first.** Never use API billing until the user has approved its purpose, estimated expenditure and spend limit. An approval covers only the use it was given for.
- **Label list-price dollars.** On the subscription, a dollar figure at list price estimates how much of the plan limit something uses. It isn't a charge, so say so.
- **Confirm the source before a headless run.** `claude auth status` shows a claude.ai login, nothing that outranks it is set (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper`, a `CLAUDE_CODE_USE_*` provider variable, `ANTHROPIC_PROFILE`), and the run doesn't pass `--bare`, which accepts only an API key or helper. In `-p` mode an API key is always used when present ([authentication documentation](https://code.claude.com/docs/en/authentication)). Signed in with a plan, `claude -p` draws from subscription limits ([support article](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)).

Stated by the user on 2026-11-06, after a session priced a test in dollars without confirming what its runs would bill against ([dev-env#1280](https://github.com/brownm09/dev-env/issues/1280)). Nothing enforces this mechanically.

**Mechanical operations:** If a task is fully scriptable with known inputs, write the script rather than running an interactive session. Candidate operations: stale PR remediation, branch cleanup, rebase-and-merge sequences. Use `~/.claude/scripts/merge-stale-pr.sh` for engineering-journal stale draft PRs.

**Always plan first — permission modes don't bypass it:** Plan mode (Shift+Tab cycles modes) is the session default for every substantive task. Bypass and auto modes reduce permission prompts only; they do not relax the plan-first discipline.

**Plan-then-optimize before acting:** Any task involving an Agent spawn, a skill invocation, reads across more than one file, or a switch to a new primary objective within the same session (e.g., moving from a `/review` or other skill output to addressing findings, or from one issue to another) requires this protocol. State a numbered plan first, giving each step the check that proves it done (`1. <step> → verify: <check>`), then apply three explicit revision passes before taking any action.

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
- The plan solves the request as stated. When the request has more than one reasonable reading, name the readings and say which one the plan follows, and ask only when the choice changes the result. When a simpler approach than the one requested exists, say so. A bug fix starts with a test that reproduces the bug. ([ADR-149](../docs/adr/149-scope-of-a-change-rules.md))

**Pass 3 — Risk-dimension audit:** before acting, the plan must address each of these eight dimensions explicitly. For any that don't apply, state **`N/A — <reason>`** rather than omitting it. The bar is *stating the decision*, not adding work everywhere.

1. **Testing** — coverage for new behavior (defers to the project `## Testing` section).
2. **Observability** — what is logged/traced at boundaries and on failure (defers to the project `## Observability` section).
3. **Security** — authz, input validation, secret handling, sensitive-data/PII exposure. When the change touches a permission, authorization, or isolation boundary, explicitly confirm test coverage runs under the restricted identity the boundary is scoped to, not just the admin/superuser one (see Code Quality → Privilege-restricted test defaults).
4. **Resilience / failure modes** — error handling, timeouts, fallbacks, and how the change is rolled back / reverted.
5. **Performance** — data-access patterns (N+1), hot paths, payload/bundle size.
6. **Data integrity & migrations** — schema changes, multi-tenant isolation, reversibility.
7. **Experimental validity** — when the plan generates or interprets any comparative result (A/B, before/after, challenger vs. incumbent): declare the tier, confirm pre-registration precedes generation, instruments are calibrated, and arms are contamination-checked and stage-matched — run `/experiment-audit design` before generating and `/experiment-audit verdict` before concluding (defers to the project `## Experiments` section and the global `## Experimental Rigor`). Plans making no comparative claim: `N/A — no experiment`.
8. **Gate calibration** — when the plan introduces or tightens an automated check that classifies inputs the plan does not enumerate (a hook, CI guard, validator, linter rule, LLM judge, or any threshold applied to a population), state: the **measured property** it keys on; the **calibration corpus** of known-good *and* known-bad references, with counts and hit rates; **where each constant came from**, including its class; and the **margin** to the worst known-good observation, named. **An uncalibrated check is a diagnostic: it may report, but it may not block, fail, or take a destructive action.** A check whose input is *extracted* (a regex, a section scan, a parse that degrades to `[]`) also owes a separate assertion that the extraction is non-empty. A fixture-pinned unit test is out of scope. Plans with no such check: `N/A — no gate introduced or tightened`. Worksheet: `/experiment-audit` Step D4 ([ADR-144](../docs/adr/144-gate-calibration-pass-3-dimension.md)). [Detail](../docs/REFERENCE.md#detail-gate-calibration)

**Accessibility** is audited whenever the change touches UI. Each project's CLAUDE.md may declare additional project-specific gates (e.g. lifting-logbook: OTel trace correlation, raw-SQL spans, LLM data scrubbing; career-playbook: `validate.sh`, briefing regeneration, artifact-schema parity) — the audit defers to those.

---

## Session Summaries & Tile Tracking

Every session boundary is a moment to orient the user. These are output rules; of them, a hook checks only the tile table. [Detail](../docs/REFERENCE.md#detail-session-summaries)

- **Close each substantive stop with a summary.** Three parts: **Completed**; **Context / ask** (decisions needed, what to review or test, each with its exact path or URL); **Remaining**. Tile the genuine out-of-scope follow-ups, deduplicated against this session's; merely list the immediate next steps of the task in progress, unless that work must move to a different session, which makes it a hand-off tile. Skip the summary on trivial exchanges.

- **Close a session that involved substantial hands-on correction with a feedback-retro table** ([ADR-128](../docs/adr/128-session-end-feedback-retro-table.md)): **#** / **What was said** (paraphrased) / **Underlying pattern** / **Status** (already durable, partially addressed, not yet formalized) / **Long-term mechanism**. It's separate from the summary above; produce both when both apply. A "not formalized" row is a candidate for the memory + issue-pairing discipline.

- **File an issue per tile, and end the session with a tile table** whenever tiles were spawned, under the exact heading **`### Tiles spawned this session`**, which the hook keys on:

  | # | Tile | Issue | Status | Next |
  |---|------|-------|--------|------|
  | 1 | short title | [#N](url) | open · not started | click the chip, or open the issue |

  **Status** is the issue's open/closed state plus a best-effort "started" note from `list_sessions`; there's no per-chip API. A lost chip is re-spawnable on request.

- **Open with a refresher after a long idle gap.** When `idle-refresher.py` injects its cue (default 60 minutes; override with `idle_refresher_minutes` in `.claude/hook-config.json`), lead with a brief refresher of what we were working on, the current state, and pending to-dos and tiles.

---

## Error Message Diligence

Error messages from CI guards, hooks, bots and library exceptions describe what their author thinks went wrong, and one check can stand in for many upstream states. Before acting on one:

1. **Locate the emitting line** and read the conditional or `raise` site.
2. **Read the condition the code actually evaluates.**
3. **Distinguish the upstream signal from the message's narrative:** if the condition can be false for several reasons, trace one level up (for CI, the parent job's status and `needs:` chain; for hooks, the input that produced the falsy value).

If still unsure, say "the guard printed X; I have not yet confirmed the underlying condition" rather than restating X as fact. Never quote an emitted message back as the diagnosis without having read the emitting line. **Exempt:** errors reliable by construction, such as syntax errors, file-not-found at a path you just wrote, and static type-checker errors naming the symbol. See [ADR-034](../docs/adr/034-error-message-diligence.md). [Detail](../docs/REFERENCE.md#detail-error-diligence)

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

**Reopen an application yourself before asking the user to test something in it.** When the session closed it, or had the user close it, launch it and confirm the process and window exist, then ask. The exception is a step needing the user's own hands first, such as a sign-in, or elevation once `sudo` has failed: say exactly what's needed. [Detail](../docs/REFERENCE.md#detail-user-references)

---

## Abbreviations

Applies to every kind of output, including chat replies, docs, file and branch names, commit messages, and PR and issue titles and bodies.

- **Never coin an abbreviation, acronym, or initialism.** Spell the name out, or use a short descriptive name ("the Round 2 prep"), never initials ("R2P"). The same goes for invented citation codes: cite a record by its file and row or heading, not a code like "ACC:51". A coined term reads like an established one but is private to the session that made it up. Defining it once doesn't fix that, because it spreads into chat replies, commit messages, and PR bodies that never carry the definition.
- **Define standard abbreviations the reader may not know.** Established terms (API, CDC for change data capture, SLO for service-level objective) are fine, but define each one on first use, or in a linked glossary, whenever the reader may not know it.

[Detail](../docs/REFERENCE.md#detail-abbreviations)

---

## Table Row Numbers

Applies to every table shown to the user, including chat replies, PR and issue bodies, review comments, and generated docs and reports.

- **Give every table a leading `#` column numbered 1..N.** The user rules on rows by position ("all except rows 3, 6, 10"), and without numbers they have to count by hand. When the same table is shown again, keep each row's number and append new rows at the end; never renumber.
- **Exception: existing tables in tracked files.** When adding a row to an unnumbered table that already lives in a repo file (a README entry index, a ROADMAP proposal list), match that table's format rather than retrofitting it.

Stated by the user on 2026-10-08 ([dev-env#1131](https://github.com/brownm09/dev-env/issues/1131)). Nothing enforces this mechanically.

---

## Asking the User

**Ask through `AskUserQuestion` whenever the answers can be enumerated.** Use single-select when one answer applies and multi-select when several can, rather than listing the options in prose for the user to answer by typing. This includes decisions left open at the end of a turn, such as the *Context / ask* part of a stop summary. The specific cases elsewhere in this file (an in-flight PR, the issues offered after an unchained merge) are instances of this rule.

- **Keep what the tool's own description asks.** Each question stands on its own, since the dialog may hide the text around it. A recommended option comes first and is labelled "(Recommended)". A choice with a conventional default, or a fact you can check yourself, doesn't get asked at all: pick the default, say so, and proceed.
- **This rule says how to ask, not whether to.** Rules that say not to ask still stand, such as *Fix everything, always* and not asking when to tile a follow-up.
- **Limits.** An open-ended question has no options to click, so it goes in prose. `AskUserQuestion` also isn't available everywhere: in a background or headless session, put the question in the final message, or park the session until someone can answer it.

[Detail](../docs/REFERENCE.md#detail-asking-the-user)

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

- **PR grouping heuristic.** Stubs sharing a PR number (`prs_opened` in one, `prs_closed` in another), or written on a day the open-PR records show that PR open, compose under a single H2, annotated "→ merged in session N" (N is the 1-based ordinal of the closing stub that day). [Detail](../docs/REFERENCE.md#detail-journal-composition)

- **Meta triggers are never asked about.** `/journal-compose` composes the meta journal itself, attended or unattended, with no prompt, and never writes `sessions/meta/YYYY-MM-DD_draft.md` ([ADR-082](../docs/adr/082-journal-compose-worktree-isolation.md) Addendum). Recovery for a miss: [Late meta entry recovery](../docs/REFERENCE.md#late-meta-entry-recovery).

---

### Stub file workflow

Each session writes an isolated stub file — no shared mutable draft. This eliminates write
contention when multiple sessions run in parallel. Slug is determined at day end.

**Sharded companion files — no shared-file editing** ([ADR-056](../docs/adr/056-per-session-sharding-journal-companion-files.md)). All four journal content kinds (stub, manifest, open-PR, tile) are written with the Write tool, or Edit for a same-session rewrite, never through the shell; `pre-tool-use-journal-shell-write-guard.py` blocks that unconditionally ([ADR-129](../docs/adr/129-journal-shell-write-guard.md)). [Detail](../docs/REFERENCE.md#detail-journal-shards)

- **Manifest:** this session's own `sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`, one JSON object paired with the stub. A shard re-created after a compose consumed the original must carry all five fields (`stub`, `topic`, `tokens`, `prs_opened`, `prs_closed`).
- **Open PRs:** `sessions/<project>/open-prs/<N>.json` per PR. Opening writes it; merging or closing deletes it.
- **Tiles:** `sessions/<project>/tiles/<issue-number>.json`, filed under the tile's **target** project and written immediately after each spawn. Closing the paired issue is the completion signal. Schema: [Tile shards](../docs/REFERENCE.md#tile-shards-sessionsprojecttilesissue-numberjson).

**Commit with an explicit pathspec, not a bare `git commit`:** shards merge cleanly, but every session shares the local git index, and a bare commit sweeps in another session's staged shard. Readers still accept the legacy single-file `YYYY-MM-DD.manifest.jsonl` / `open-prs.jsonl`; writers emit only shards. [Detail](../docs/REFERENCE.md#detail-journal-shards)

**An explicit pathspec can also drop half of a rename; check `git status --short` after committing.** Include the **old** path in the `git commit -- <paths>` list for a rename (after `git mv`, `git add` on it fails, so `add` and `commit` take different lists). After **any** explicit-pathspec commit, `git status --short` should print nothing. [Detail](../docs/REFERENCE.md#detail-journal-shards)

**An orphaned open-PR or tile shard *deletion* is yours to commit,** the one exception to "commit only your own files." `reconcile-open-prs.py` and `reconcile-pending-tiles.py` unlink shards at session start but never commit. When a hook reports a deletion it **confirmed MERGED/CLOSED**, commit it at once, stub or no stub, naming each path and never the bare directory:

```bash
git -C C:/Users/brown/Git/engineering-journal add -- sessions/<project>/open-prs/<N>.json
git -C C:/Users/brown/Git/engineering-journal commit -m "journal: close open-pr shard #<N> (merged)" -- sessions/<project>/open-prs/<N>.json
```

(Tile form: `sessions/<project>/tiles/<issue-number>.json`, message `journal: close tile shard #<issue-number> (issue closed)`.) A path *added or modified* is a concurrent session's shard: leave it alone. A deletion whose state couldn't be confirmed isn't committed blind, and one that came back **open** is restored with `git checkout HEAD -- <path>`. [Detail](../docs/REFERENCE.md#detail-journal-orphaned-shards)

**Branch:** `draft/YYYY-MM-DD` — created at the first session of the day, merged to main at day end.

**Day rollover: always cut `draft/<today>` from `main`, however many prior days' drafts are still unmerged.** A stub's filename date and branch date must match, because `/journal-compose` and the nightly routine both key on them; a mismatched stub is silently never composed. Several unmerged `draft/*` branches coexisting is normal. A shard deletion is durable only once its branch merges to `main`, and a branch cut before that resurrects it until the reconcile hook re-unlinks it, so cut the branch at the start and commit those deletions on it. See [ADR-119](../docs/adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md). [Detail](../docs/REFERENCE.md#detail-journal-day-rollover)

**Date-mismatched stub — the divergence state.** A stub whose filename date differs from its draft branch's date. Repair **additively**, never rewriting the shared branch's history: cut `draft/<today>` from `main` for your own stub, tile the remediation, and once `draft/<D>` merges, `git -C C:/Users/brown/Git/engineering-journal fetch origin`, then `checkout draft/<today>` and `merge origin/main`, so `/journal-compose <today>` finds them. If `draft/<D>` already merged: `py -3 ~/.claude/scripts/reconcile-late-stubs.py draft/<D>`, which renames moved stubs to the target date and tags `reconciled/<branch>`. After moving stubs by hand, tag them yourself: `git push origin <tip sha>:refs/tags/reconciled/<branch>`. `new-day-journal-check.py` detects a date-mismatched stub and advises only, in worktree sessions too. [Detail](../docs/REFERENCE.md#detail-journal-day-rollover)

**The first session to see the late-commits warning runs the commands it prints, whatever that session's task.** If one is blocked, give it to the user and say why. The warning means content that may be in no journal. [Detail](../docs/REFERENCE.md#detail-journal-day-rollover)

**Stale edit in the canonical: apply the clean fix, and don't hold your stub back to ask.** For an uncommitted journal edit you didn't make whose file is at least 24 hours old (`stat -c '%y' <path>`; anything newer is a live session's and stays untouched; deletions stay under the shard-deletion rules above): (1) commit it with an explicit pathspec to the branch whose date matches its filename (`git checkout draft/<D>` first if the canonical is elsewhere; a file with no date in its name, such as a shard, stays on the current branch), and push; (2) move the canonical to today's draft branch, or cut it from `main`; (3) commit your own stub there. If day `<D>` is already composed (`git -C C:/Users/brown/Git/engineering-journal ls-tree -r origin/main --name-only -- sessions | grep -E "^sessions/[^/]+/<D>-"` prints a path), commit the edit on today's branch renamed to today's date, and your own stub takes that date too. If no clean fix exists, leave it, write your stub on disk, and tell the user what's blocking. Never use git plumbing to push around a stuck checkout. [Detail](../docs/REFERENCE.md#detail-journal-stale-edit)

**Never create a dedicated worktree to write a stub; always operate directly on the canonical via `-C`.** `draft/YYYY-MM-DD` can be checked out in only one place, so a session whose primary repo is another project still runs every step as `git -C C:/Users/brown/Git/engineering-journal <command>`, never `EnterWorktree` or `git worktree add`. `pre-tool-use-journal-draft-worktree-guard.py` blocks this. [Detail](../docs/REFERENCE.md#detail-journal-canonical)

**Stub filename:** `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`
where `YYYY-MM-DD` is the **local calendar date** and `HHMMSS` is the **local start time**
of the session (`date +%Y-%m-%d` / `date +%H%M%S`). Local time is used so stub filenames
and branch names always share the same calendar day. UTC is reserved for internal
operational artifacts (compose lock files, log file timestamps).

**Sync the canonical with `fetch` + `merge --ff-only origin/<branch>`, never `git pull`.** Concurrent sessions share `.git/FETCH_HEAD`, so `git pull` can merge the wrong branch. If the fast-forward fails on divergence, run the same `merge` without `--ff-only`. [Detail](../docs/REFERENCE.md#detail-journal-canonical)

**First session of the day:**
1. `git -C C:/Users/brown/Git/engineering-journal fetch origin main && git -C C:/Users/brown/Git/engineering-journal checkout main && git -C C:/Users/brown/Git/engineering-journal merge --ff-only origin/main`
2. `git -C C:/Users/brown/Git/engineering-journal checkout -b draft/YYYY-MM-DD`. **Validate:** `git -C C:/Users/brown/Git/engineering-journal merge-base HEAD origin/main` must equal `git -C C:/Users/brown/Git/engineering-journal rev-parse origin/main`. If not, delete the branch (`git -C C:/Users/brown/Git/engineering-journal branch -D draft/YYYY-MM-DD`) and redo steps 1–2.
3. Read the open-PR records (`sessions/<project>/open-prs/*.json`, plus any legacy `open-prs.jsonl`) as session context.
4. Create `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` with the Write tool.
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block.
6. Write this session's manifest shard with the Write tool (see [Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals)).
7. `git add <paths>`, `git commit -m "draft: YYYY-MM-DD session 1" -- <paths>`, `git push -u origin draft/YYYY-MM-DD`, where `<paths>` is the stub, the manifest, and only the exact `open-prs/<N>.json` and `tiles/<issue-number>.json` shards this session created or removed, space-separated. Never the bare `open-prs/` or `tiles/` directory, which sweeps in another session's shard. A path that doesn't exist makes `git add` abort, leaving everything unstaged. The `--` pathspec on `git commit` is required. [Detail](../docs/REFERENCE.md#detail-journal-steps)

**Subsequent sessions:**
1. `git -C C:/Users/brown/Git/engineering-journal fetch origin draft/YYYY-MM-DD && git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD && git -C C:/Users/brown/Git/engineering-journal merge --ff-only origin/draft/YYYY-MM-DD`
2. Read the open-PR records, as in the first-session steps.
3. Read only the `<!-- next-session-context -->` paragraph of the most recent stub: `ls C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.stub.md | sort | tail -1`
4. Create a new `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` with the Write tool.
5. Add the tokens comment.
6. Write this session's manifest shard with the Write tool.
7. `git add <paths>`, `git commit -m "draft: YYYY-MM-DD session N" -- <paths>`, `git push`, with `<paths>` as in the first-session step 7.

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
  - **Report / analysis generated** — whenever the user requests a report or analysis (an audit, investigation, **verification or deploy-check**, comparison, findings summary), save the full output under `sessions/<project>/reports/YYYY-MM-DD-<slug>.md` and link it from the session stub, creating the stub if needed. No PR required. Short analyses (about one screen) may be inlined. `/review <PR-URL>` stays exempt. `stop-journal-stub-checkpoint.py` backstops this. [Detail](../docs/REFERENCE.md#detail-journal-triggers)
  - **Context growth band** — when `turn-count-hook.py` prints a `[journal-stub]` nudge, update this session's stub in place (or create one if it says none exists), commit with an explicit pathspec, and push. If the stub already covers everything since its last update, no edit is needed ([ADR-150](../docs/adr/150-context-growth-journal-stub-trigger.md)).
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
