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
- **Run an elevated command yourself with `sudo <command>`; don't ask the user to open an elevated terminal or run it for you.** Windows `sudo` is in Inline mode here, so output and exit code return. If `sudo config` says it's disabled, ask the user to re-enable it (Settings → For developers, Inline); meanwhile run a one-shot script logging to a file via `Start-Process powershell -Verb RunAs -Wait`. Ask the user only if both fail, and say what failed. This changes how a session runs a command, not what it may run: confirm-first rules for risky or hard-to-reverse actions still apply. Scripts are unchanged: they still fail fast and never relaunch (see *No spawning new terminal windows*).
  - A command's exit code comes back through `sudo`, but a non-zero code can come from `sudo` itself (49 means command not found), so read stderr first. Each call shows a UAC prompt, and answering it is the user's approval; if declined, report that and don't retry. A scheduled routine shouldn't count on `sudo`. From the Bash tool, prefix `MSYS_NO_PATHCONV=1` when an argument starts with `/`, or Git Bash rewrites it and `sudo cmd /c ...` hangs on an interactive prompt. [Detail](../docs/REFERENCE.md#detail-platform-and-environment)
- **`HKCU` writes from a Claude desktop session stay private to the app, so make and verify them from Task Scheduler.** The app is packaged (MSIX), and [Microsoft documents](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes) that its `HKCU` writes go to a private per-app copy, which a session's shell inherits: the write reads back fine from the session while Windows never sees it. Run the change and its read-back as a one-shot scheduled task logging outside `AppData` (`-RunLevel Limited`, no elevation; `Highest` for an elevated script). A session's own read-back proves nothing, `sudo` stays private too, and `HKLM` writes are real. [Detail](../docs/REFERENCE.md#detail-platform-and-environment)
- **Disk-full (ENOSPC):** `C:` saturating to 0 GB surfaces *indirectly* as truncated `node_modules` (npm exits 0 but a native binary is partial), not an obvious "disk full" error. If an install fails with a confusing downstream error, run `df -h /c` first. Recovery runbook + failure signature + dominant consumers: [`docs/REFERENCE.md` → Disk-Full (ENOSPC) Recovery](../docs/REFERENCE.md#disk-full-enospc-recovery). The `worktree-npm-install.py` gate now refuses a low-space install rather than truncate ([ADR-045](../docs/adr/045-pre-install-freespace-gate.md)).
- **Never start Docker Desktop from a session; just run the `docker` command you need.** A hook starts it through Task Scheduler and a watchdog stops it when sessions are done. Started inside the Claude app's process tree, its next start crashes with "initializing Inference manager ... The file cannot be accessed by the system". Never choose "Reset to factory defaults" for that error: it wipes images and volumes and doesn't fix the cause. If Docker won't start, run `py -3 ~/.claude/scripts/docker-watchdog.py --status`. `hypervisor present: False` means virtualization is off in the BIOS, which only the user can change. [ADR-158](../docs/adr/158-docker-lifecycle-outside-the-app-process-tree.md)
- **Claude Code has no user-scope `settings.local.json`, only a per-project one; project-scoped app-written content belongs there.** The user file loads for every project, so when the app writes a key embedding project-specific content (an auto-mode environment scan, a per-project credential hint), move it to that project's `.claude/settings.local.json`, after confirming `git check-ignore -v .claude/settings.local.json`. Plain global toggles stay in the user file. [Detail](../docs/REFERENCE.md#detail-platform-and-environment)
- **When an install or uninstall you start waits on a dialog the user must click, notify them, and repeat while they're at the computer.** Notify when it starts waiting, then about every 2 minutes while it waits and there was keyboard or mouse input since the last one (`GetLastInputInfo`). `PushNotification` is suppressed while the user is active in the app, so notify from the shell (a `NotifyIcon` balloon). [Detail](../docs/REFERENCE.md#detail-platform-and-environment), [ADR-164](../docs/adr/164-notify-while-an-install-waits-on-a-dialog.md)
- **Authenticated browser work uses Chrome's "Main" profile (`Default`), not another profile or the app's built-in browser pane, unless the task needs one.** [Detail](../docs/REFERENCE.md#detail-platform-and-environment)

---

## Known Entities

**An unfamiliar name (a hostname, repo or account) is probably one of the user's own.** Check this table, then `gh repo list brownm09` and the journal, before asking or guessing; `hostname` names the machine you're on. A name that recurs undefined gets a row here, through a dev-env PR, never only in memory. [Detail](../docs/REFERENCE.md#detail-known-entities)

| # | Name | Kind | What it is |
|---|---|---|---|
| 1 | LETHEON | machine | Windows 11 laptop. The scheduled routines are moving off it. |
| 2 | ANGSTROM-B | machine | Windows 11 desktop. It now runs the scheduled routines. |
| 3 | DEVIDRA-B | machine | Android phone, the user's only phone. |
| 4 | Switchyard | repo | `brownm09/switchyard`: coordinates teams of coding agents across repos, gated by checks that call no model, with merges a human approves. It parses `/review`'s acceptance-evidence table. |
| 5 | dev-env | repo | This global configuration: `CLAUDE.md`, hooks, skills, routines. |
| 6 | engineering-journal | repo | Session stubs and the daily composed journal. |
| 7 | lifting-logbook | repo | `merickvaughn/lifting-logbook`, the one repo not under `brownm09`. TypeScript training log with recommendations from performance, sleep and nutrition. |
| 8 | gas-lifting-logbook | repo | The Google Apps Script logbook that preceded lifting-logbook. |
| 9 | career-playbook | repo | Job-search materials: cover letters, interview prep, application tracking. |
| 10 | cover-letter-runtime | repo | Agent engine that generates cover letters from career-playbook's content. |
| 11 | job-search-agent | repo | Cover-letter and application workflow tool with local or API model backends. |
| 12 | agent-fleet | repo | Template for an orchestrator and worker fleet on the Claude API. |
| 13 | win11-init-tools | repo | Scripts that set up and maintain a fresh Windows 11 install. |
| 14 | tech-leadership-reference | repo | Engineering-leadership playbooks. |
| 15 | research-notes | repo | Local only, no remote: output of the `nightly-research` routine. |
| 16 | brownm09-switchyard | GitHub account | Switchyard's worker account. Its PRs and comments come from Switchyard runs, not a stranger. |

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
   - **Ref scoping.** A working tree, `HEAD`, and `origin/main` each answer only for themselves. Before claiming an identifier is free (an ADR number, a migration or fixture filename), check all **three** surfaces: `origin/main` (merged), the open-PR set (`gh pr list`, `gh pr diff <N> --name-only`), and remote branches (`git fetch origin`, `git for-each-ref --sort=-committerdate refs/remotes/origin`, `git ls-tree -r <ref> --name-only -- <dir>/` on recent ones; filter by recency, not ancestry). The third is the one concurrent sessions collide in. For an identifier inside a file's content, read the blob: `gh api "repos/{owner}/{repo}/contents/<path>?ref=<ref>" -H "Accept: application/vnd.github.raw"`.
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

Every project CLAUDE.md **must** also include a `## Observability` section: the logger and levels, structured or plain output, where errors and traces go, and what Pass 3's Observability dimension should verify there. A project with no runtime says so and names its equivalent check (a reference-integrity or link-check script). **If it's missing, note that in the plan and ask the user to add one; don't block the PR on it.**

Projects that run process experiments **should** add an `## Experiments` section: a corpus catalog, an instrument registry naming each check's known-good AND known-bad calibration references, the results home, and default tier triggers. Pass 3's Experimental-validity and Gate-calibration dimensions and `/experiment-audit` defer to it; **absence is advisory, not blocking** ([ADR-115](../docs/adr/115-experimental-rigor-protocol.md), [ADR-144](../docs/adr/144-gate-calibration-pass-3-dimension.md)).

---

## Git Workflow

- **Create an issue before changing files.** `gh issue create` describing the problem/goal, not the implementation, before any edit. Single-line change: ask first; anything longer warrants an issue without prompting. Exception: engineering-journal `draft/YYYY-MM-DD` branches. Every PR references the issue via `Closes #N`.
- **Every implementation issue has a `## Acceptance criteria` checklist, and every PR is verified against it.** Each `- [ ]` item names how it is checked: a command and its expected result, a test, a file and what it contains, or an observable behaviour. "Works", "is clean", and "docs updated" don't qualify. Investigation and tracking issues put `Exempt: <reason>` as the section's first line. `/review` Step 2g scores each criterion `met`, `not met`, or `not verifiable`, and a missing section is a finding, never a pass. A `not met` criterion is fixed in the PR, or deferred by amending the issue to link a follow-up issue that names the criterion as its own work. After merge, record the verification on each linked issue. Schema: [`claude/templates/acceptance-evidence.md`](templates/acceptance-evidence.md). [Detail](../docs/REFERENCE.md#detail-acceptance-criteria)
- **Check for an existing issue before filing a new one.** Before `gh issue create` for a bug or defect, search open and closed issues, as its own command: `gh issue list --search "<keywords>" --state all` (add `--repo <owner>/<repo>` when the defect lives elsewhere). Read the result before filing: never put the search and `gh issue create` in the same invocation, because the create runs whatever the search finds. An **open** match means it's tracked: reference it and comment with any new detail. A **closed** match means verify the fix (`gh issue view <N> --json state,closedAt` plus the closing PR's diff); if it didn't stick, reopen or reference it. [Detail](../docs/REFERENCE.md#detail-duplicate-checks)
- **Check for an existing open PR before implementing.** Before implementing any issue, confirm no open PR addresses it: `gh pr list --search "<issue-number-or-keywords>" --state open`, or `gh issue view <N> --json` for linked PRs. A match means **surface it to the user via `AskUserQuestion`**; closing, superseding or consolidating isn't yours to decide alone. See [ADR-125](../docs/adr/125-check-open-pr-before-implementing-issue.md).
- **Multi-PR decomposition: top-level issue + sub-issues.** When work maps to several PRs, file a **top-level issue** with the full scope and rationale, then a **sub-issue** per PR that references it. Each PR closes its sub-issue; the top-level issue closes after the last sub-PR merges. A tile spawned for one of them names **both** issues in its prompt. ([ADR-059](../docs/adr/059-multi-pr-issue-hierarchy.md))
- **Test before PR.** Before `gh pr create`, run the project's `## Testing` command; tests must pass (or the failure be explained). Put what was tested + the `Tests: N passed, N skipped, N failed (duration)` line + outcome in the PR body. **No `## Testing` section → stop and ask the user to add one before opening the PR.** Also run, before `gh pr create`:
  - **Coverage gate** — add tests for new testable behavior, or document the deferral in the PR body ([ADR-022](../docs/adr/022-test-coverage-gate-before-pr.md)).
  - **Suppression + test-integrity greps** from `## Code Quality` — any new suppression, skip marker, deleted test, lowered threshold, or bypass flag needs a PR-body justification or it blocks the PR; a non-zero skipped count needs a per-skip justification ([ADR-026](../docs/adr/026-suppression-policy.md), [ADR-029](../docs/adr/029-test-integrity-policy.md)).
  - **Pre-existing failure check** — if `baseline_test_failure_tracking` is on, run `baseline-tests diff`: new failures block; pre-existing failures in touched files are fixed inline (≤ ~20 LOC / ~15 min) or filed ([ADR-030](../docs/adr/030-baseline-test-failure-policy.md)).
- **ADR-warrant check** after plan approval, after `gh pr create`, and before `gh pr merge`. Warranted when the change touches a rule, hook, skill or setting documented in `claude/`, restructures a `claude/` directory, sets a workflow rule other CLAUDE.md files reference, or has rationale `git log` won't recover. Global rules → dev-env [`docs/adr/`](../docs/adr/INDEX.md) (scan its tags first); project decisions → that project's `docs/adr/`. **Write the ADR and its `INDEX.md` row before merging.** **Prefer the smallest durable home:** a new ADR for a decision reversible on its own, an amendment to refine or bound a recorded one, a plain CLAUDE.md line when the rule carries its own reasoning. **If you cannot name an alternative you rejected, it is not a new ADR.** [Detail](../docs/REFERENCE.md#detail-adr-warrant)
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
- **Commit before a change you plan to undo, and stop at a dirty file that isn't yours.** `git checkout -- <path>`, `git checkout <ref> --no-overlay -- .` and `git restore` overwrite uncommitted work with no way back, so commit first, even as a temporary commit, before a calibration run or a trial edit. In a shared checkout, a dirty file you didn't edit belongs to another session: check `git status --short` before any of these commands, name exact paths, and leave that file alone. The journal's *Stale edit* and orphaned shard deletion rules say when you may act on one. Nothing enforces this mechanically, and the guard above exempts the journal checkout. [Detail](../docs/REFERENCE.md#detail-canonical-checkout)
- **PR first, then merge.** Open the PR immediately after pushing; don't prompt the user to run `gh pr create`.
- **Turn on Auto-fix for every PR a session opens.** In the desktop app, right after opening the PR, call `mcp__ccd_pr__get_status` (`bind_pr` if it doesn't report the PR). If it reads off, call `mcp__ccd_pr__set_monitor` with `auto_fix: true` and the PR's URL; this rule is the user's request, so don't offer or ask first. In the default mode that call waits on an approval card. If it's refused because the app isn't sure this session opened the PR, don't retry: tell the user they can turn it on. Skip where the `ccd_pr` tools don't exist. Auto-merge stays off. A wake from your own `/review` comment needs only the normal findings disposition. [Detail](../docs/REFERENCE.md#detail-pr-monitoring-and-ci)
- **Write the journal stub immediately after `gh pr create`** — defer and a corrupted session loses all context. Then report the PR URL and run `/review <PR-URL>` at once, with no `/compact` pause. **Address all findings — fixed-in-PR or filed-and-linked, never "left as-is"** — and merge in the same session. Each finding's disposition (fixed in `<sha>` | filed `#N`) goes in a "Review findings disposition" PR-body section; the `pre-merge-findings-gate` hook blocks merge until it's present, and `/review` applies the `reviewed-by-claude` label ([ADR-028](../docs/adr/028-all-findings-merge-gate.md), [ADR-039](../docs/adr/039-merge-gate-findings-enforcement.md)).
- **Fix everything, always — never ask which findings to address.** Resolve all `/review` / `/code-review` findings as part of the same work; default to fixing in-PR, file-and-link only when genuinely out of scope. Prefer the root-cause fix over the smaller band-aid. Escalate only for a product/design decision the code can't settle or a breach of the *Code Quality → Fix errors on encounter* scope guard ([ADR-028 → Addendum](../docs/adr/028-all-findings-merge-gate.md)).
- **Run `/review` and dispose of every finding before asking the user to review a PR,** including one this session didn't open: fixed in the PR, or filed and linked. Their review then starts from a reviewed PR. [Detail](../docs/REFERENCE.md#detail-review-before-asking)
- **Write a stub on PR merge**; a merge is a session boundary. After `gh pr merge`, include the `PostToolUse` hook's `### Usage Snapshot (post-merge)` block verbatim in the stub; if it prints a `[usage-snapshot] Skipped:` line instead, paste that. Same session as the PR open: update the existing stub. New session: update the opening stub for minor merges, or write a new one for substantial follow-up. Either way set `prs_closed:[N]` in this session's manifest shard and delete `sessions/<project>/open-prs/<N>.json` (a PR predating shards: remove its one line from `open-prs.jsonl`). [Detail](../docs/REFERENCE.md#detail-stub-on-merge)
- **Multiple PRs/merges in one session:** defer stub writing until after the last `gh pr create` / `gh pr merge`; produce one consolidated stub. Never write an intermediate stub between PR opens or merges.
- **PR closed without merging:** the stub was already written at creation; stopping is optional.
- **After merging:** move the linked project board item to Done (command is project-specific — see the project's CLAUDE.md; for dev-env, its GitHub Project section). On each linked issue, record the acceptance-criteria verification: tick each box whose `/review` row is `met`, or post the review's evidence block as a comment beginning `Verified by <PR URL>:` (schema §5 of [`claude/templates/acceptance-evidence.md`](templates/acceptance-evidence.md)). When a work stream completes (milestone, issue group, feature sequence), move the item from Active Work to Shipped in the project roadmap — don't leave roadmaps contradicting shipping history.
- **Capture follow-ups as tiles.** A tile is a one-click session proposal made with `start_session` (`initiation: "own_initiative"`; older transcripts show `spawn_task`). A card starts in this project, so a tile for another repo names its clone path in the prompt. [Detail](../docs/REFERENCE.md#detail-tiles)
  - **Two checkpoints, as a floor:** (1) a PR reaches merged state, however it merged; (2) a session creates an issue still unresolved at session end. The same discipline fires the moment a genuine follow-up surfaces in any session.
  - **Write out what you considered:** scan for out-of-scope fixes, deferred work, tech debt and ideas, and record each as `→ tiled (task_id / #N)` or `→ not tiled, because <reason>`. "No follow-ups" is valid only as the visible result of that scan.
  - **For each genuine follow-up:** tile it yourself at once (unless the next bullet keeps it here), file a tracking issue in the same repo and reference it in the prompt, then write the shard `sessions/<project>/tiles/<issue-number>.json`. Without `start_session`, file the issue anyway.
  - **Do it here when this session can.** A follow-up this session already has the context for, that nothing blocks, and that would be its only remaining work is done in this session, not tiled; a tile makes the user start a fresh session to rediscover what this one knows. Tile only work that needs a fresh session or must run beside work still in flight. Never end a turn saying work is "waiting for you to start" when this session could do it. [Detail](../docs/REFERENCE.md#detail-tiles)
  - **Don't ask.** Never convert a known follow-up into a scheduling question ("should I start it now?"), even for the next unit of a multi-PR initiative. Plan files can't defer tiling, and plan approval doesn't waive it; only an explicit user instruction in this session (e.g. "skip tiles") does, and it waives all tiling for the session.
  - **A required cross-session hand-off is a tile, not a chat brief:** a blocked or corrupted environment, a required restart, or "finish this in a fresh session" gets a self-contained tile, even in plan mode.
  - **Blocked on another issue's future phase:** in the same session, add a forward reference on the blocking issue stating the trigger condition, in addition to the follow-on's own back-reference.
  - **Hard (finish-to-start) dependency:** never co-spawn the two. Spawn only the head of the chain, and have its prompt spawn the next tile (with its issue and shard) once its PR merges. Related but independently workable items spawn together.
  - **Look ahead at every spawn:** if this tile's completion will predictably surface a next thread, bundle it when same-subsystem, small and bounded. Otherwise add a prompt instruction to re-verify it after merge and spawn a separate tile, naming the issue now.
  - **A merge in an unchained session must not end idle.** When this session's opening prompt named no follow-on work (no issue/PR reference, no `=== CHAIN` marker): tile the next thread if one is determinable. Only when none is, rank open issues (1) `start-here`-labeled, (2) the first unchecked item of the newest open `retro-action` issue, (3) open issues in the merged PR's subsystem, (4) most recently updated; dedupe against `sessions/<project>/tiles/` shards and `list_sessions`; offer the top 3 via `AskUserQuestion`; tile the choice. That ask is the one exception to *Don't ask*. `stop-tile-enumeration-gate.py` enforces this floor at Stop.
- **Auto-merge is off by design; `--auto` is now mechanically gated when available.** Every merge is in-session via `gh pr merge --squash --delete-branch` after the post-PR checklist (`/review`, ADR-warrant, doc-reconciliation, board transition, roadmap move). `pre-auto-merge-checkpoint-gate.py` blocks `--auto` unless the PR's latest qualifying comment carries clean-or-disposed `review-findings` and complete `premerge-checkpoints` markers fresh for the head commit, failing closed with no override. `allow_auto_merge` is a separate per-repo setting that drifts silently, so check before relying on it: `gh api repos/brownm09/<repo> --jq .allow_auto_merge`. [Detail](../docs/REFERENCE.md#detail-auto-merge)
- **Wait for CI with one background watch, then merge.** Once only CI stands between a PR and the merge, run `gh pr checks <N> --watch --interval 60` once with `run_in_background: true`, and again after each later push. This one command overrides the app's no-polling instruction: no `CronCreate`, `ScheduleWakeup`, `/loop`, `Monitor` or `gh` loop. Exit 0 means every check passed; "no checks reported" exits 1 at once (not registered yet, or the PR conflicts). Skip it if the user said to hold the merge. With GraphQL exhausted, read `mcp__ccd_pr__get_status` or REST check-runs. A failure can wake the session twice with Auto-fix on; handle it once. [Detail](../docs/REFERENCE.md#detail-pr-monitoring-and-ci)
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

**GitHub Projects — single-select option mutation hazard (every project).** `updateProjectV2Field` with `singleSelectOptions` replaces the whole list: even unchanged options get new IDs, and every item loses its assignment for that field. Before adding, removing or renaming an option: (1) snapshot per-item assignments to a tracked file under the repo's `.claude/backups/` and commit it; (2) run the mutation with the full desired list; (3) in the same PR, update the project CLAUDE.md option-ID table and every machine-local cache of the old IDs (usually `.claude/hook-config.json` and `.claude/propose.json`; lifting-logbook tracks `hook-config.json`); (4) restore assignments with `gh project item-edit`, mapping option name to new ID. If a mutation ran without a committed snapshot, stop and recover from the latest snapshot first. [Detail](../docs/REFERENCE.md#detail-project-boards)

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

A *suppression* is `!` (non-null assertion), `?? null` coercing away `undefined`, `// @ts-ignore`, `// @ts-expect-error`, `eslint-disable`, or a type cast that silences an error rather than narrowing.

1. **No suppression without justification:** a PR-body note names the lines and the invariant each relies on.
2. **Pre-existing errors are filed, not silenced.** If the error exists on the base branch, file it and leave it; only an error this branch introduced may be suppressed, with rule 1's note.
3. **Pre-PR check (required before `gh pr create`),** from the repo root, plus a hand scan of added lines for any other `!`. Each match is justified in the PR body or replaced by a proper fix, or the PR isn't mergeable.
```bash
git diff origin/main -- . | grep -E '(ts-ignore|ts-expect-error|eslint-disable|![.[]|!\s*[;,)]|\?\? null)'
```

[Detail](../docs/REFERENCE.md#detail-suppression) · [ADR-026](../docs/adr/026-suppression-policy.md)

### Test integrity policy

A *test integrity violation* is adding a skip marker (`it.skip`, `xit`, `xdescribe`, `test.skip`, `describe.skip`, `.todo`, `pending`), deleting a test file or `describe`/`it` block, lowering a coverage threshold, adding `--passWithNoTests` / `--bail` / `--testPathIgnorePatterns` to a test run or CI, or hardcoding values to satisfy one test input rather than a general contract. ADR-022's coverage gate guards against *missing* tests; this guards against *degrading* them for a green run.

1. **No violation without justification:** a PR-body note names the tests or thresholds and why degrading them is right.
2. **Skipped counts are visible.** The PR body's Testing section carries the run's `Tests: N passed, N skipped, N failed (duration)` line verbatim and a note per skip, or says the project has no automated tests.
3. **Pre-PR check (required before `gh pr create`),** alongside the suppression grep. Each match is justified in the PR body or reverted, or the PR isn't mergeable. These target JS/TS: extend them to the language's idioms (`@pytest.mark.skip`, `pytest.skip(`, `t.Skip(`, `#[ignore]`) first, since a clean JS-only grep elsewhere proves nothing. `--bail` and the threshold grep over-match (an increase isn't a violation), so review by hand.
```bash
git diff origin/main -- . | grep -E '(it\.skip|\bxit\(|xdescribe\(|test\.skip|describe\.skip|\.todo\(|pending\(|passWithNoTests|--bail|testPathIgnorePatterns)'
git diff --diff-filter=D --name-only origin/main -- '*.test.*' '*.spec.*' 'tests/**' 'e2e/**'
git diff origin/main -- jest.config.* .nycrc vitest.config.* | grep -E 'threshold|coverage'
```
4. **A test must be able to fail.** Break what it claims to cover and watch it go red; for a lookup or resolver, change the data it reads as well as its logic. Nothing enforces this mechanically.

[Detail](../docs/REFERENCE.md#detail-test-integrity) · [ADR-029](../docs/adr/029-test-integrity-policy.md)

### Pre-existing test failure policy

This policy guards against *inherited red state*: tests already failing when the branch was cut. A *pre-existing failure*'s fingerprint is in the branch-start baseline; a *new failure*'s isn't. See [ADR-030](../docs/adr/030-baseline-test-failure-policy.md). [Detail](../docs/REFERENCE.md#detail-baseline-failures)

- **Rule 1: baseline at branch creation.** Opt in with `"baseline_test_failure_tracking": true` in `.claude/hook-config.json` (Jest only; `"test_command"` overrides `npx jest --json --silent`). `new-branch <name>` then snapshots automatically; raw `git checkout -b` skips it and the pre-PR hook flags that.
- **Rule 2: fix-on-touch.** A failure in a file this branch modified gets fixed inline if it's about 20 lines or 15 minutes; otherwise file an issue and reference it in the PR body. Failures in untouched files are noted in the PR body, not blocking.
- **Rule 3: pre-PR diff (required before `gh pr create`).** Run `baseline-tests diff` from the repo root. Any **new** failure blocks the PR (exit 1). A PR with new failures, or with no PR-body note for outstanding touched-file failures, is not mergeable.

### Privilege-restricted test defaults

When a project enforces a security or isolation guarantee through a specific runtime identity (a restricted DB role, a scoped service account, a tenant-scoped credential), tests and local dev must default to that restricted identity; the privileged one is the opt-in exception. A test passing under admin proves nothing about the boundary. Before adding coverage for a boundary, confirm the test runs under the identity it's scoped to, and through the real framework/DI machinery rather than a hand-built object graph. For any change touching such a boundary, state which identity the coverage runs under in the Pass 3 Security audit. See [ADR-089](../docs/adr/089-privilege-restricted-test-defaults.md). [Detail](../docs/REFERENCE.md#detail-privilege-tests)

### TypeScript build artifacts

**Purge `*.tsbuildinfo` along with `dist/`.** In a project using `composite: true`, deleting `dist/` alone leaves the build-info file behind, and `tsc` then skips emit, so later builds fail with a misleading error about missing declarations. That behaviour was observed in lifting-logbook, whose `CLAUDE.md` has the command; `composite` itself is in the [TypeScript documentation on project references](https://www.typescriptlang.org/docs/handbook/project-references.html).

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
- **Tier 1 — test.** Required before any adopt/reject of a standing process: a pre-registration frozen in the tracking issue *before* generation, naming one **primary outcome construct** (a secondary criterion never alone decides the verdict), one manipulated variable, a corpus fixed in advance with ≥1 known-good input, instruments **calibrated against known-good AND known-bad references before any arm is scored**, blinded scoring by someone other than the drafter, and a fixed win bar. Probe data never scores in the verdict. The full checklist: [Detail](../docs/REFERENCE.md#detail-experimental-rigor).
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

**Session length:** `turn-count-hook.py` warns at 8 prompts and every 4 after, and at 150k tokens of context growth since the session's first turn and every 100k after. Consider `/clear` or `/compact` if the task has shifted. Override the prompt threshold per project with `"turn_threshold": N` in `.claude/hook-config.json`. [Detail](../docs/REFERENCE.md#detail-context-efficiency)

**Token source:** Whenever you estimate or spend tokens, for any task in any session, say whether they come from the subscription or the API. [Detail](../docs/REFERENCE.md#detail-context-efficiency)

- **API use needs approval first.** Never use API billing until the user has approved its purpose, estimated expenditure and spend limit. An approval covers only the use it was given for.
- **Label list-price dollars.** On the subscription, a dollar figure at list price estimates how much of the plan limit something uses. It isn't a charge, so say so.
- **Confirm the source before a headless run.** `claude auth status` shows a claude.ai login, nothing that outranks it is set (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper`, a `CLAUDE_CODE_USE_*` provider variable, `ANTHROPIC_PROFILE`), and the run doesn't pass `--bare`, which accepts only an API key or helper. In `-p` mode an API key is always used when present ([authentication documentation](https://code.claude.com/docs/en/authentication)). Signed in with a plan, `claude -p` draws from subscription limits ([support article](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)).

**Mechanical operations:** If a task is fully scriptable with known inputs, write the script rather than running an interactive session. Candidate operations: stale PR remediation, branch cleanup, rebase-and-merge sequences. Use `~/.claude/scripts/merge-stale-pr.sh` for engineering-journal stale draft PRs.

**Always plan first — permission modes don't bypass it:** Plan mode (Shift+Tab cycles modes) is the session default for every substantive task. Bypass and auto modes reduce permission prompts only; they do not relax the plan-first discipline. **Retitle the session** (desktop app: `set_session_title`) once a plan is approved and whenever the work drifts far from its title.

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
8. **Gate calibration** — when the plan introduces or tightens an automated check over inputs it doesn't enumerate (a hook, CI guard, validator, linter rule, LLM judge, or threshold), state the **property it measures**, a **calibration corpus** of known-good *and* known-bad references with hit rates, **where each constant came from**, and the **margin** to the worst known-good case. **An uncalibrated check is a diagnostic: it may report, but not block, fail, or act destructively.** An *extracted* input (a regex, a section scan) also owes an assertion that the extraction is non-empty. Otherwise: `N/A — no gate introduced or tightened`. Worksheet: `/experiment-audit` Step D4 ([ADR-144](../docs/adr/144-gate-calibration-pass-3-dimension.md)). [Detail](../docs/REFERENCE.md#detail-gate-calibration)

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

- **Open with a refresher after a long idle gap.** When `idle-refresher.py` injects its cue (default 60 minutes; override with `idle_refresher_minutes` in `.claude/hook-config.json`), lead with a brief refresher of what we were working on, the current state, and pending to-dos and tiles. State the context size the cue gives, and if the new message starts a different task, suggest `/clear` with a short hand-off.

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

- **Cite primary sources, not summaries:** *official documentation* for technology choices, *specifications* for protocols and standards, *foundational writings* for architectural patterns. [Detail](../docs/REFERENCE.md#detail-documentation-and-citations)
- **Regulatory references** (GDPR, HIPAA, SOC 2) must link to the primary regulatory source, not a summary or blog post.
- **When making any technical recommendation in a response** — a technology, Claude Code feature, workflow pattern, or architectural approach — include a primary source link in that same response. If no authoritative primary source exists, explicitly label the recommendation as based on observed behavior or heuristic.
- **Research written to a file records its sources:** the queries run, the pages loaded, and each claim's page, or *snippet only* if just a search summary backs it.

---

## User-Actionable References

Whenever directing the user to view, test, run, or consume any resource, or naming work they must do, include the exact reference inline — never make the user ask for it:

- **Local files, scripts, and logs** — provide the absolute path or the exact invocation string. Never say "run the script" without the full path, or "see the log" without the file path.
- **Every web resource you mention gets its full URL, visible**: PRs, issues, Drive files, libraries, products, organizations, docs. Write the raw URL, or make the link text the URL; never hide it behind a title, which may not work over Remote Control. [Detail](../docs/REFERENCE.md#detail-user-references)
- **Give the steps for any work needing the user's hands, even a named follow-up or blocker:** in order, commands in code blocks, each step linking the exact page where they act (not a landing page), with an image where the screen isn't obvious: the job awaiting their deploy approval, not the run list. [Detail](../docs/REFERENCE.md#detail-user-references)

No "see the file" without the path. No "check the PR" or "see the issue" without the URL, in the message and in any question dialog or recap box. When your own action hands the user a step (an invitation sent, an approval requested), the line reporting it gives the filled-in link, never a `<placeholder>` URL, and the account to act as: the user doesn't see tool output.

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
- **Put decisions to the user one at a time,** one `AskUserQuestion` call each, sending the next after the answer, since an answer can change the next question. Each question says what is being decided, why it matters and the current default; its options are the real alternatives, each with its trade-off, so the user can decide from the dialog alone. Never ask for blanket approval of a set ("are these defaults good?"), in a question, a table or a stop summary; a conventional default you don't ask about is reported as picked, not put up for approval ([dev-env#1291](https://github.com/brownm09/dev-env/issues/1291)).
- **This rule says how to ask, not whether to.** Rules that say not to ask still stand, such as *Fix everything, always* and not asking when to tile a follow-up.
- **Limits.** An open-ended question has no options to click, so it goes in prose. `AskUserQuestion` also isn't available everywhere: in a background or headless session, put the questions in the final message, each with its context and alternatives, or park the session until someone can answer it.

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

**Day rollover: always cut `draft/<today>` from `main`, however many prior days' drafts are still unmerged.** A stub's filename date and branch date must match, or it is silently never composed. Cut the branch at the start: a shard deletion is durable only once its branch merges, so commit resurrected deletions on the new branch. [ADR-119](../docs/adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md), [Detail](../docs/REFERENCE.md#detail-journal-day-rollover)

**Date-mismatched stub** (filename date differs from its branch's): repair additively, never rewriting the shared branch. Cut `draft/<today>` from `main` for your own stub and tile the remediation; once `draft/<D>` merges, merge `origin/main` into `draft/<today>`. If it had already merged: `py -3 ~/.claude/scripts/reconcile-late-stubs.py draft/<D>`. [Detail](../docs/REFERENCE.md#detail-journal-day-rollover)

**The first session to see the late-commits warning runs the commands it prints, whatever that session's task.** If one is blocked, give it to the user and say why. The warning means content that may be in no journal. [Detail](../docs/REFERENCE.md#detail-journal-day-rollover)

**Stale edit in the canonical: apply the clean fix, and don't hold your stub back to ask.** An uncommitted journal edit you didn't make, whose file is at least 24 hours old (`stat -c '%y'`; newer is a live session's): commit it with an explicit pathspec to the branch matching its filename date and push, then move to today's branch and commit your stub. If that day is already composed, commit the edit renamed to today. If no clean fix exists, leave it and tell the user. Never use git plumbing. [Detail](../docs/REFERENCE.md#detail-journal-stale-edit)

**Never create a dedicated worktree to write a stub; always operate directly on the canonical via `-C`.** `draft/YYYY-MM-DD` can be checked out in only one place, so a session whose primary repo is another project still runs every step as `git -C C:/Users/brown/Git/engineering-journal <command>`, never `EnterWorktree` or `git worktree add`. `pre-tool-use-journal-draft-worktree-guard.py` blocks this. [Detail](../docs/REFERENCE.md#detail-journal-canonical)

**Stub filename:** `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`
where `YYYY-MM-DD` is the **local calendar date** and `HHMMSS` is the **local start time**
of the session (`date +%Y-%m-%d` / `date +%H%M%S`). Local time is used so stub filenames
and branch names always share the same calendar day. UTC is reserved for internal
operational artifacts (compose lock files, log file timestamps).

**Sync the canonical with `fetch` + `merge --ff-only origin/<branch>`, never `git pull`.** Concurrent sessions share `.git/FETCH_HEAD`, so `git pull` can merge the wrong branch. If the fast-forward fails on divergence, run the same `merge` without `--ff-only`. [Detail](../docs/REFERENCE.md#detail-journal-canonical)

**Writing a stub.** Every command is `git -C C:/Users/brown/Git/engineering-journal ...`. [Detail](../docs/REFERENCE.md#detail-journal-steps)
1. **Get on today's branch.** If `draft/YYYY-MM-DD` exists on origin: `fetch origin draft/YYYY-MM-DD`, `checkout` it, `merge --ff-only origin/draft/YYYY-MM-DD`. Otherwise: `fetch origin main`, `checkout main`, `merge --ff-only origin/main`, `checkout -b draft/YYYY-MM-DD`, and check `merge-base HEAD origin/main` equals `rev-parse origin/main` (if not, `branch -D` it and redo).
2. Read the open-PR records (`sessions/<project>/open-prs/*.json`, plus any legacy `open-prs.jsonl`), and for a later session only the `<!-- next-session-context -->` paragraph of the day's latest stub.
3. With the Write tool, create the stub (ending with a `<!-- tokens: input=N output=N cost≈$N -->` comment) and this session's manifest shard ([Engineering Journal Internals](../docs/REFERENCE.md#engineering-journal-internals)).
4. `git add <paths>`, `git commit -m "draft: YYYY-MM-DD session N" -- <paths>`, `git push` (`-u origin draft/YYYY-MM-DD` on a new branch). `<paths>` is the stub, the manifest, and only the exact `open-prs/<N>.json` and `tiles/<issue-number>.json` shards this session created or removed: never the bare directory, which sweeps in another session's shard, and never a path that doesn't exist, which aborts the whole `git add`.

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
