# dev-env Reference

Full descriptions of every skill, hook, routine, and utility script managed by this repo.
For a compact overview see the [README](../README.md).

---

## Contents

- [Skills](#skills)
- [Hooks](#hooks)
- [Routines](#routines)
- [Utility Scripts](#utilities)
- [Model Selection](#model-selection)
- [Platform Constraints](#platform-constraints)
- [Git Workflow Runbooks](#git-workflow-runbooks)
- [Engineering Journal Internals](#engineering-journal-internals)

---

## Skills

Custom slash commands loaded from `claude/skills/`. Invoke with `/skill-name [args]`.

---

### /propose

```
/propose <one-line idea>
```

Expands a one-line idea into a full proposal document, creates a linked GitHub issue, and appends an entry to `ROADMAP.md`.

**Config:** reads `.claude/propose.json` in the project root. If the file is missing, the skill scaffolds it interactively. Keys: `proposals_dir`, `roadmap_file`, `prd_file`, `github_repo`, `milestones`, `epics`, `github_project`.

**Produces:** proposal document at `proposals_dir/`, a GitHub issue in `github_repo`, and a ROADMAP entry. If a `github_project` block is configured, the issue is added to the project and its fields are set.

---

### /journal-compose

```
/journal-compose [YYYY-MM-DD]
```

Composes the end-of-day engineering journal from the day's stub files. Discovers all `YYYY-MM-DD_*.stub.md` files, sorts and merges them, produces the canonical 11-section document, commits to the `draft/YYYY-MM-DD` branch, and opens a PR. Also refreshes the marker-delimited `## Start here` block at the top of `engineering-journal/README.md` (freshness stamp + top 3–5 cross-project priorities aggregated from manifest `priorities` arrays and `open-prs.jsonl` — see [ADR-032](adr/032-journal-start-here-dashboard.md)).

**Constraint:** must run in a dedicated session with no prior task work. If other tasks were handled before invocation, the skill refuses with an error message.

**Source library:** greps `~/.claude/skills/sources.md` before spawning any research subagent (zero-cost cache hit path).

**Date argument:** defaults to today. Pass `YYYY-MM-DD` to compose a specific day's stubs.

---

### /research

```
/research [<tag>:] <decision> [--compare <alternative>]
```

Finds 1–3 primary sources for an engineering decision or topic. Emits footnote-ready markdown.

**How it works:** greps the shared source library at `~/.claude/skills/sources.md` first (zero token cost). Spawns a Haiku subagent only on a cache miss.

**Arguments:**
- `tag:` — optional topic prefix (e.g., `architecture:`, `security:`) used to filter the source library
- `--compare <alternative>` — also finds sources for the rejected alternative

---

### /review

```
/review <PR-URL | --diff> [--no-style] [--author junior|mid|senior] [--focus security|correctness|perf] [--no-comment]
```

Reviews a PR or pasted diff for correctness, security, reliability, and maintainability. Runs three pre-checks before the main analysis:

- **Step 2b — Documentation Reconciliation:** flags as a blocking finding if `claude/skills/**`, `claude/hooks/**`, `claude/scripts/**`, or `claude/routines/**` were changed without updating `README.md` or `docs/REFERENCE.md` (applies to repos with a `Documentation Maintenance` table in their `CLAUDE.md`).
- **Step 2c — Documentation Coverage:** for every file added, deleted, renamed, or significantly rewritten, walks up ancestor directories and uses LLM judgment to determine whether a README at any of those levels should have been updated. Flags as a blocking finding when an existing README was not touched; suggests creation as a non-blocking finding when a directory would benefit from an index it lacks.
- **Step 2d — Test Coverage Gate:** enforces the global "Test before PR" rule (see [ADR-022](adr/022-test-coverage-gate-before-pr.md)). Inspects the diff for new testable behavior (new endpoints, pages, exported functions, CLI commands, bug fixes) and flags as a blocking finding when new behavior is present but no test files appear in the diff and no deferral rationale is documented in the PR body.
- **Step 2e — Test Integrity Gate:** enforces the global Test Integrity policy (see [ADR-029](adr/029-test-integrity-policy.md)). Scans the diff for skip markers (`it.skip`, `xit`, `xdescribe`, `test.skip`, `describe.skip`, `.todo`, `pending`), deleted test files or blocks, lowered coverage thresholds, bypass flags (`--passWithNoTests`, `--bail`, `--testPathIgnorePatterns`), and implementation branches that appear to skew toward specific test inputs. Flags as a blocking finding when any pattern matches without a PR-body justification, and when the PR body lacks the required `Tests: N passed, N skipped, N failed` summary line. **Language-scope preamble:** before running the JS/TS scanners, detects `*.py`, `*.go`, `*.rs`, or `*.rb` files in the diff and emits a non-blocking **maintainability** finding noting that the automated patterns cover JS/TS only — converting the silent bypass on non-JS repos into a visible reviewer prompt to verify pytest / Go / Rust / Ruby skip idioms by hand.

Produces a structured report with blocking findings, non-blocking findings, questions for the author, and optional style notes. Author questions are emitted as a **Question / Context / Tradeoffs** block (not a bare bullet) so the author can answer without a follow-up round-trip. Findings are uncapped — every finding that meets the four-question gate (what / why here / category / what to do) is reported, regardless of count.

**Flags:**
- `--no-style` — omit style/nit findings
- `--author <level>` — calibrate feedback depth (default: `mid`)
- `--focus <area>` — narrow to one review dimension (default: all)
- `--no-comment` — skip posting the report as a PR comment (default: posts)

**Default behavior:** posts the report as a GitHub PR comment and applies the `reviewed-by-claude` label.

---

### /journal-onboard

```
/journal-onboard [project-slug]
```

Scaffolds a new project's journal home (`sessions/<slug>/`) in engineering-journal and optionally creates `.claude/CLAUDE.md` in the project repo.

**Slug inference:** defaults to the active git repo's name. Pass an explicit slug to override (useful when the repo name and journal slug differ).

**Produces:** `sessions/<slug>/README.md` in engineering-journal (committed and pushed directly to `main`), and optionally `.claude/CLAUDE.md` in the current project repo.

**Template:** reads `~/.claude/templates/project-claude.md` when scaffolding a new CLAUDE.md, substituting `<REPO_NAME>` and `<PROJECT_SLUG>`.

**Detection:** the `journal-onboard-check.py` hook emits an advisory on the first prompt of any session in a repo that lacks a journal home.

---


## Hooks

Most hooks are **advisory** — they emit `systemMessage` reminders but do not block tool execution. The exception is `pre-tool-use-worktree-path-check.py` (a `PreToolUse` hook), which exits 2 with a `{"reason": "..."}` payload to block `Write`, `Edit`, and `NotebookEdit` calls that target the canonical repo root instead of the active worktree.

Configuration is in `claude/settings.json` (symlinked to `~/.claude/settings.json`).
See [ADR-007](adr/007-hook-command-invocation.md) for why hooks invoke scripts via `pyw -3` (the windowless variant of the Windows Python Launcher) rather than `python3` directly, wrapped in `bash -c`, or via `py -3` (which flashes a console window per spawn). Shell-invoked Python (the `## Testing` command, skill `py -3` examples, and the `pre-push` hook) continues to use `py -3`.

Any hook that spawns subprocesses (`git`, `gh`, `bash`, …) must `import _winsubp` near its imports — the helper patches `subprocess.Popen.__init__` to set `CREATE_NO_WINDOW` so children don't flash a console window under `pythonw.exe`. The static check in `claude/scripts/tests/test_pyw_stdio.py` fails the build if a subprocess-using hook ships without it. See ADR-007's 2026-06-01 follow-up section.

#### Machine-local permissions

The `permissions.allow` block in `claude/settings.json` contains paths with a hardcoded Windows username (`C:/Users/brown/...`). These rules are functionally correct on this machine but must be updated manually when bootstrapping dev-env on a new machine or account. If scratch-dir writes or edits start prompting for permission after a re-bootstrap, update the username in every `allow` entry.

**Known scope decisions:**

| Entry | Scope | Rationale |
|---|---|---|
| `Edit(C:/Users/brown/Git/**)` | All files in all local repos | Covers skill and config edits across career-playbook, lifting-logbook, dev-env, etc. without per-file prompts. Intentionally broad — includes `.env` and credential files — accepted tradeoff on a single-user personal machine. |

---

### UserPromptSubmit

Fires on every user prompt, before Claude processes it.

| Script | What it does |
|--------|-------------|
| `session-mode-prompt.py` | Fires on the first user prompt of each new session. Emits a one-time mode-confirmation reminder (plan / bypass / auto) to Claude as `hookSpecificOutput.additionalContext` JSON on **stdout** and exits 0 — per the hook contract, this delivers the reminder alongside the prompt without erasing it, and Claude surfaces a mode-confirmation line in its first response. A per-session marker file at `scratch/session_mode_ack_<session_id>.txt` records that the reminder has been injected for this session; subsequent prompts in the same session pass through silently. Cross-session contamination is impossible — session A's marker never affects session B. Suppressed for automated sessions whose prompt begins with an XML tag (e.g. `<scheduled-task>`, `<ci-monitor-event>`). Diagnostic JSON log at `scratch/session-mode-prompt.log`. [ADR-027](adr/027-userpromptsubmit-blocking-hook-conventions.md) (see 2026-05-27 amendment) |
| `dev-env-sync.py` | Fast-forward pulls the dev-env repo to `origin/main` so symlinked tooling stays current. Warns (stderr, exit 0) when the canonical worktree is on a feature branch — symlinked tooling will serve stale files until it returns to `main`. [ADR-006](adr/006-dev-env-sync-on-every-prompt.md) |
| `new-day-journal-check.py` | Checks for stale `draft/*` branches on `origin/engineering-journal`. Emits a one-line warning if any are found; continues silently otherwise. Suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd). |
| `journal-onboard-check.py` | Checks whether the active git repo has a `sessions/<repo-name>/` directory in engineering-journal. Emits a one-line advisory and `/journal-onboard` hint if not. Fires once per session. |
| `turn-count-hook.py` | Warns when session context accumulates past a threshold. Primary signal: token count; secondary: turn count. Configurable via `"turn_threshold"` in `.claude/hook-config.json` (default: 50). |
| `multi-worktree-alert.py` | When ≥2 git worktrees are active, emits a list in `repo:branch` format, starring the current one. Fires on every prompt. Suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd). |
| `reconcile-open-prs.py` | Runs once per session (per-session sentinel in `scratch/`). Calls `gh pr view` for each entry in every `sessions/*/open-prs.jsonl` in engineering-journal; removes entries whose PRs are MERGED or CLOSED; emits a `systemMessage` listing surviving open PRs and any removals. Does not commit — modified files are picked up by the next stub commit. Fails safe: `gh` errors leave the entry intact. [ADR-018](adr/018-reconcile-open-prs-hook.md) |
| `disk-space-check.py` | Free-space safety net for `C:`. Checks `shutil.disk_usage` on every prompt. Below 20 GB free: emits a one-time `systemMessage` warning. Below 10 GB free: spawns `reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --min-free-gb 10 --protect-cwd <cwd>` **detached** (via `sys.executable`, never the `py` launcher — dev-env#300) so the heavy delete never blocks the prompt, and emits a `systemMessage`. Each band fires at most once per session via a `session_id`-keyed marker (`scratch/disk_space_check_<session_id>_<band>.flag`, ADR-027). Advisory only — exit 0 always; any exception is swallowed. Thresholds are hardcoded constants. [ADR-037](adr/037-worktree-disk-reclamation.md) |
| `awake-blocker.py` (start) | On UserPromptSubmit, spawns a detached watcher (if not already running) that holds a Windows system-sleep lock via `kernel32!SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED)`. Refreshes the sentinel heartbeat on every prompt. Watcher self-terminates if the sentinel is missing or older than 30 minutes (crash safety). Idempotent. Display sleep is not blocked — only system sleep. [ADR-033](adr/033-prevent-system-sleep-while-processing.md) |

---

### PreToolUse

Fires before matched tool calls. Matcher values are set per entry in `settings.json`.

#### Bash hooks

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-commit-branch-check.py` | Command contains `git commit` | Emits the current branch name as a confirmation checkpoint before the commit runs. |
| `pre-pr-create-check.py` | Command contains `gh pr create` | Emits a test-verification checklist, a documentation-gap warning (if `claude/skills/`, `claude/hooks/`, `claude/scripts/`, or `claude/routines/` were changed without updating `README.md` or `docs/REFERENCE.md`), and — when `baseline_test_failure_tracking` is enabled — a baseline-diff advisory pointing at `baseline-tests diff` (ADR-030). Enforces the "test before PR", doc-reconciliation, and pre-existing-failure rules from CLAUDE.md. |
| `pre-merge-findings-gate.py` | Command contains `gh pr merge` | Reads the target PR's last `/review` comment marker (`<!-- review-findings: blocking=N non_blocking=M -->`); if `N+M > 0` and the PR body records no "Review findings disposition" section (or `<!-- findings-disposed -->` sentinel), **blocks the merge (exit 2)** with a fix-or-file instruction. Mechanical enforcement of the all-findings merge gate (ADR-028/ADR-039). Fails open on any `gh`/parse error. Has a behavioral self-test: `bash claude/scripts/tests/test-merge-findings-gate.sh`. |

#### Write / Edit / NotebookEdit hooks

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-tool-use-worktree-path-check.py` | Session `cwd` is inside a Claude-managed worktree and `file_path` (or `notebook_path`) is absolute and starts with the canonical repo root | **Blocks** the tool call (exit 2) with a message naming the attempted path, the active worktree root, and the corrected path. No-op when the session is not in a worktree or when the path already targets the worktree root. **Bypass for intentional canonical edits:** use `Bash` with `node -e`, `sed`, or `python3` — the hook only covers the three file tools, not `Bash`. [ADR-024](adr/024-worktree-path-guard-hook.md) |

---

### PostToolUse (Bash only)

Fires after each Bash tool call completes. Matched with `"matcher": "Bash"`.

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pr-merge-reminder.py` | Command contains `gh pr create`, `gh pr merge`, or `git push` (when the pushed branch has an open PR) | Exits 2 with a `systemMessage` reminding Claude to write a journal stub. For `git push`, runs `git branch --show-current` and `gh pr list --head <branch>` as subprocesses to confirm an open PR exists before emitting the reminder. Skips `engineering-journal` pushes (handled by `stub-push-archive-reminder.py`). |
| `post-tool-use.py` | Command contains `gh issue create` or `gh pr create` | Auto-adds the created item to the configured GitHub Project, then exits 2 with a `systemMessage` listing the exact `gh project item-edit` commands to set any `required_fields` defined in `hook-config.json`. Opt-in via `project_number` + `project_owner` in `.claude/hook-config.json`. [ADR-023](adr/023-generic-required-fields-issue-hook.md) |
| `post-pr-merge-pull.py` | Command contains `gh pr merge` | Fast-forwards the local `main` branch via `git fetch origin main:main` so the local clone stays current after a merge. |
| `post-pr-merge-project.py` | Command contains `gh pr merge` | Auto-moves the linked issue (`Closes/Fixes/Resolves #N` in PR body) to Done on the configured GitHub Project. Opt-in via `status_field_id` and `done_option_id` in `hook-config.json`. [ADR-014](adr/014-auto-move-project-item-done-on-merge.md) |
| `usage-snapshot.py` | Command contains `gh pr merge` | Queries `https://api.anthropic.com/api/oauth/usage` (via OAuth Bearer token from `~/.claude/.credentials.json`) and parses the session JSONL for the top-5 costliest exchanges. Emits a `### Usage Snapshot (post-merge)` markdown block showing weekly/5-hour utilisation vs. day-of-week soft targets (configured in `claude/usage-config.json`). Global — fires for all repos without opt-in. Include the emitted block verbatim in the post-merge journal stub. |
| `stub-push-archive-reminder.py` | `git push` to `engineering-journal` with a stub commit | Writes a sentinel file (`~/.claude/scratch/stub-pushed.flag`) and exits 0. Verifies the most-recent commit in the journal repo touches a `.stub.md` file before writing the flag. The Stop hook (`journal-stop-check.py`) consumes the sentinel and issues the archive reminder via exit 2. |

---

### Stop

Fires when the Claude Code session ends (user exits or `/stop`).

| Script | What it does |
|--------|-------------|
| `token-tracker.py` | Reads the session JSONL, aggregates token usage, and appends a record to `~/.claude/scratch/token-sessions.jsonl`. Supports Sonnet 4.6, Opus 4.6, and Haiku 4.5 pricing. |
| `journal-stop-check.py` | Checks for the stub-push sentinel flag (emits a closing message reminding the user to archive if found), then checks for stale open journal stubs and unmerged draft branches, emitting a closing message if any are found. Exit 0 always. |
| `awake-blocker.py` (stop) | Removes the sleep-block sentinel; the detached watcher polls every second and exits within ~1s, releasing the system-sleep lock. Also registered on `Notification` for the same effect when Claude pauses for input/permission. [ADR-033](adr/033-prevent-system-sleep-while-processing.md) |

---

### PostCompact

Fires after `/compact` or auto-compact completes.

| Script | What it does |
|--------|-------------|
| `post-compact.py` | Emits a `[compact]` or `[auto-compact]` status line with the trigger type and remaining token count. Visible in all environments. |

---

### Git hook: `hooks/pre-push`

A global git pre-push hook installed via `core.hooksPath` (see [ADR-005](adr/005-global-core-hooks-path.md)).

**What it does:** before every `git push` it (1) checks whether the branch's merge base diverges from `origin/main` in squash-merge repos and warns when it detects a branch cut from a squash-merged ancestor (which would cause a rebase to fail); (2) blocks engineering-journal pushes to already-merged `draft/` branches; and (3) when the push range touches a `package.json`, runs a non-destructive **lockfile-drift guard** that regenerates lockfile metadata and blocks the push if `package-lock.json` is out of sync (see [ADR-036](adr/036-lockfile-drift-prevention.md)). It chains to any existing per-repo `.git/hooks/pre-push` so repo-level hooks are preserved.

**Testing:** the lockfile-drift guard has a behavioral self-test that drives the real hook against fixture repos with a stubbed `npm`, asserting its BLOCK / PASS / SKIP paths, working-tree restoration, and repo-hook chaining. Run it after any change to the hook:

```bash
bash claude/hooks/tests/test-pre-push-lockfile.sh
```

---

### Configuration

`hook-config.json` lives at `.claude/hook-config.json` in the project root (not version-controlled).

| Field | Type | Default | Used by |
|-------|------|---------|---------|
| `repo` | string | — | `post-tool-use.py` / `post-pr-merge-project.py` — `"owner/repo"` filter; only acts when the created item URL contains this repo path |
| `project_number` | string | — | `post-tool-use.py` — GitHub Project number; required for auto-add on issue/PR create |
| `project_owner` | string | — | `post-tool-use.py` — GitHub user/org that owns the project |
| `project_node_id` | string | — | `post-tool-use.py` — GraphQL node ID of the project; used in `gh project item-edit` commands shown in the reminder |
| `required_fields` | array | `[]` | `post-tool-use.py` — list of project fields to prompt for after issue/PR creation. Each entry: `{"name": string, "field_id": string, "type": "single_select"\|"text"\|"milestone", "options": {name: id}, "hint": string}`. The hook prints ready-to-run `gh project item-edit` commands for each field. |
| `epic_field_id` | string | — | `post-tool-use.py` — **deprecated fallback**; use `required_fields` instead. Treated as a single `single_select` field named "Epic" when `required_fields` is absent. |
| `milestones` | array | — | `post-tool-use.py` — **deprecated fallback**; use `required_fields` with `"type": "milestone"` instead. |
| `turn_threshold` | integer | `50` | `turn-count-hook.py` — warn after N turns; warns again every 25 turns thereafter |
| `status_field_id` | string | — | `post-pr-merge-project.py` — GitHub Project Status field ID; required to auto-move item to Done on merge |
| `done_option_id` | string | — | `post-pr-merge-project.py` — single-select option ID for "Done" status; required to auto-move item to Done on merge |
| `baseline_test_failure_tracking` | boolean | `false` | `new-branch.sh` / `pre-pr-create-check.py` / `baseline-tests.sh` — opt-in to the pre-existing test failure baseline (ADR-030). When `true`, `new-branch` snapshots failing tests at branch creation and the pre-PR hook reminds Claude to run `baseline-tests diff`. |
| `test_command` | string | `npx jest --json --silent` | `baseline-tests.sh` — shell command emitting Jest `--json` stdout. Override when `npm test` wraps Jest through turbo/lerna and does not pass `--json` through. |

---

### Authoring rules

PreToolUse hooks that exit non-zero **block the matched tool call silently** — the user sees the tool refused with no error pointing to the hook. Three invariants prevent recurrence:

1. **Atomic commits.** A `settings.json` hook entry and its script file must land in the **same commit**. Never push a `settings.json` change that references a script not yet in `claude/scripts/` on main. Verify by running the script **from the dev-env repo root** (not via `~/.claude/scripts/` — that junction resolves against the main worktree checkout, not the branch being tested):
   ```bash
   py -3 claude/scripts/<new-hook>.py < /dev/null; echo "exit: $?"
   # Must print "exit: 0"
   ```

2. **Safe-exit guard.** Advisory hooks (hooks that emit a `systemMessage` reminder but do not intend to block) must exit 0 on **every** code path — happy path, empty stdin, malformed JSON, and unhandled exception. Use a top-level exception handler so no code path escapes:
   ```python
   if __name__ == "__main__":
       try:
           main()
       except Exception:
           sys.exit(0)
   ```
   Never add `sys.exit(N)` where N > 0 to an advisory hook.

3. **Invoke via `pyw -3`, never bare `python3`, never `bash -c`, never `py -3` (which flashes a console window per spawn).** Hook commands call the interpreter directly: `pyw -3 C:/Users/brown/.claude/scripts/foo.py`. `python3` resolves to the Microsoft Store App Execution Alias stub on Windows and exits 49 silently; the `bash -c` wrapper fails because `bash.exe` is not on the Windows system PATH; `py -3` allocates a console window on every spawn. Root causes of [dev-env#81](https://github.com/brownm09/dev-env/issues/81), [dev-env#261](https://github.com/brownm09/dev-env/issues/261), and [dev-env#294](https://github.com/brownm09/dev-env/issues/294). See [ADR-007](adr/007-hook-command-invocation.md).

4. **`import _winsubp` whenever a hook spawns subprocesses.** Under `pythonw.exe` (no console), every `subprocess.run`/`Popen` call that targets a console app (`git`, `gh`, `bash`, `py`, …) gets a fresh console window allocated by Windows unless `creationflags=CREATE_NO_WINDOW` is set. The `_winsubp` helper (`claude/scripts/_winsubp.py`) patches this in once on import. Any new subprocess-using hook must add `import _winsubp  # noqa: F401` near its imports; the static check in `claude/scripts/tests/test_pyw_stdio.py` will fail the build otherwise. Root cause: [dev-env#297](https://github.com/brownm09/dev-env/issues/297).

---

## Routines

Autonomous scheduled agents in `claude/routines/`. They run on a cron schedule with no user interaction. Managed via the `scheduled-tasks` MCP tool; stored in `claude/routines/` (directory junction to `~/.claude/scheduled-tasks/`).

---

### daily-journal-compose

**Schedule:** `0 0 * * *` (midnight UTC, daily)

Assembles all `YYYY-MM-DD_*.stub.md` files across all configured projects into the canonical 11-section journal entries and opens PRs against `engineering-journal`.

**Retry wrapper:** `journal-compose-with-retry.sh` — wraps the routine for Windows Task Scheduler use. Retries up to 3 times with 5-minute delays on transient failures. Logs to `~/.claude/scratch/`.

---

### prune-stale-worktrees

**Schedule:** `0 8 * * *` (8am local, daily)

Scans all primary git repos directly under `C:/Users/brown/Git` and removes `claude/*` worktrees whose branches are fully merged into `origin/main`, and removes any non-primary worktree accidentally checked out on `main`. Repos with no GitHub remote are skipped. Uses `git branch -d` and `git worktree remove` (no `--force`). Skips the current worktree, dirty worktrees, and any worktree not named `claude/*` (except `main`). Sends a push notification listing any unmerged branches that were skipped.

---

### reclaim-worktree-disk

**Schedule:** `0 */6 * * *` (every 6 hours)

Scans all primary git repos directly under `C:/Users/brown/Git` and strips regenerable `node_modules` and `.turbo` (top-level and nested monorepo packages) from **idle** Claude-managed worktrees — those under `.claude/worktrees/` whose working tree is clean **and** whose branch is merged into `origin/main` or has zero commits ahead of it. Complements `prune-stale-worktrees`: that removes merged worktree *directories*; this reclaims the heavy regenerable artifacts from worktrees that are idle but not yet eligible for removal, preventing `C:` saturation between the daily prune runs (dev-env#306). Reclamation is self-healing — `worktree-npm-install.py` (ADR-016) reinstalls `node_modules` on the next prompt in any Claude-managed worktree. Never touches dirty worktrees, the primary worktree, the protected/current worktree, manual sibling worktrees outside `.claude/worktrees/`, or worktrees with unpushed commits ahead of `origin/main`. Runs `sync-routine-worktree` as Step 0. Push-notifies when ≥ 1 GB is reclaimed. [ADR-037](adr/037-worktree-disk-reclamation.md)

---

### nightly-research

**Schedule:** `0 8 * * *` UTC (3:00 AM CDT; update to `0 9 * * *` for CST in winter)

Reads `C:/Users/brown/Git/research-notes/research-queue.md`, processes pending topics top-to-bottom using `WebSearch` and `WebFetch`, writes one structured markdown note per topic to `C:/Users/brown/Git/research-notes/notes/YYYY-MM-DD/`, updates the queue (completed items move to Done; topics with no confirmed sources are annotated but kept in Pending for manual review), and commits to the local research-notes repo.

**Model:** Sonnet. Research and synthesis run directly in the main agent — no subagent spawns, no approval gate.

**Time budget:** 5 hours wall clock. Topics that cannot start with < 10 minutes remaining are deferred to the next run.

**Failure handling:** a topic with zero confirmed primary sources is kept in Pending with an `<!-- attempted YYYY-MM-DD, no sources found -->` annotation so the user can review, rephrase, or remove it manually.

**Output path:** `C:/Users/brown/Git/research-notes/notes/YYYY-MM-DD/<slug>.md`

**Queue path:** `C:/Users/brown/Git/research-notes/research-queue.md`

---

## Utilities

On-demand scripts — not wired to any event. Run manually or from other scripts.

| Script | Invocation | What it does |
|--------|-----------|-------------|
| `token-report.py` | `py -3 token-report.py [--date YYYY-MM-DD] [--days N] [--project name] [--latest] [--show-subagents]` | Generates markdown and JSON token usage reports from `~/.claude/scratch/token-sessions.jsonl`. |
| `backfill-tokens.py` | `py -3 backfill-tokens.py` | Backfills token data for sessions predating the token-tracker hook. Idempotent — deduplicates on `session_id`. |
| `prune-merged-worktrees.py` | `py -3 prune-merged-worktrees.py [--dry-run] [--repo-path /path/to/repo\|--scan-dir /path/to/dir]` | Manual equivalent of the prune routines. Auto-detects the GitHub repo slug from the origin remote URL. `--repo-path` targets a specific repo's worktrees (defaults to dev-env); `--scan-dir` discovers and prunes all git repos directly under the given directory. Removes merged `claude/*` worktrees and stale `main` checkouts. |
| `reclaim-worktree-disk.py` | `py -3 reclaim-worktree-disk.py [--dry-run] [--repo-path /path\|--scan-dir /path] [--min-free-gb N] [--protect-cwd /path]` | Manual equivalent of the `reclaim-worktree-disk` routine (and the script the `disk-space-check.py` hook spawns). Strips `node_modules`/`.turbo` from idle Claude-managed worktrees (clean **and** merged-or-not-ahead). `--min-free-gb N` makes it a no-op unless the drive is below N GB; `--protect-cwd` shields the active worktree. Deletes only regenerable dirs — never the worktree or git state. [ADR-037](adr/037-worktree-disk-reclamation.md) |
| `new-branch.sh` | `new-branch <name>` (shell function; source `~/.claude/scripts/new-branch.sh` in `.bashrc`) | Creates a branch always rooted at `origin/main`. Warns when HEAD has diverged from the merge base. When `baseline_test_failure_tracking: true` is set in `.claude/hook-config.json`, also runs `baseline-tests snapshot` to capture pre-existing failures (ADR-030). |
| `baseline-tests.sh` | `baseline-tests <snapshot\|diff>` | Captures and diffs pre-existing test failures for the fix-on-touch policy ([ADR-030](adr/030-baseline-test-failure-policy.md)). `snapshot` runs the project test command (`test_command` in `hook-config.json`, default `npx jest --json --silent`) and writes failing-test fingerprints to `C:/Users/brown/.claude/scratch/baseline_<repo>_<branch>.json`. `diff` re-runs tests and classifies current failures into `new` (block PR), `preexisting-touched` (fix-on-touch or file), and `preexisting-untouched` (note only); exits 1 if any `new` failures are present. Jest-only in the first implementation. |
| `merge-stale-pr.sh` | `bash merge-stale-pr.sh <PR-URL>` | Remediates stale `engineering-journal` draft PRs: checks out the branch, warns on missing journal file, deletes orphaned drafts, rebases, and squash-merges with auto-conflict resolution. |
| `get-project-item.sh` | `ITEM_ID=$(bash get-project-item.sh <issue-number> [project-number] [owner])` | Resolves a GitHub Project item node ID from an issue/PR number. Defaults to project 3, owner `brownm09`. Overridable via args or `PROJECT_NUMBER`/`PROJECT_OWNER` env vars. Requires `project` scope: `gh auth refresh -s project`. |

---

## Model Selection

Route tasks to the least powerful model that can handle them reliably:

| Task type | Model |
|-----------|-------|
| Mechanical: search, format, summarize, diff, rename | Haiku |
| Standard dev: feature implementation, debugging | Sonnet |
| Complex: architectural decisions, novel problems, multi-file reasoning, writing test code, `/review` skill | Opus |

Default to Sonnet when uncertain. Never use Opus for tasks a Haiku prompt handles correctly on the first try.


### Configured defaults

The active defaults in `claude/settings.json`:

| Key | Value | Effect |
|-----|-------|--------|
| `model` | `claude-sonnet-4-6` | Default model for all session phases. See [ADR-025](adr/025-default-plan-mode.md). |
| `permissions.defaultMode` | `plan` | Every session starts in plan mode — no edits until the user approves a plan. Override per-session with Shift+Tab. See [ADR-025](adr/025-default-plan-mode.md). |
| `effortLevel` | `medium` | Applies to all model tiers. Increase to `high` or `xhigh` for intelligence-sensitive sessions (e.g., full cover letter workflow). |
| `agentPushNotifEnabled` | `true` | Fires a push notification when an agent session completes. |

---

## Platform Constraints

Environment-specific limitations and the workarounds the workflow rules in `claude/CLAUDE.md`
depend on.

### `git push --delete` fails in Claude Code web sessions

**Symptom.** In Claude Code **web/cloud sessions**, `git push origin --delete <branch>` (any
delete-only ref update) aborts mid-stream:

```
error: RPC failed; ... sideband ...
fatal: the remote end hung up unexpectedly
fatal: failed to push some refs to '<remote>'
```

The same command succeeds in local sessions.

**Root cause.** Web sessions run in a network-isolated sandbox: git traffic is relayed through an
**HTTP git proxy** (and repos are cloned shallow, `--depth 1`). The proxy is built for the
*fetch* path (clone/pull). A ref deletion exercises the *send-pack* (push) path, which sets the
new OID to the zero OID and POSTs an effectively empty packfile to `git-receive-pack`; the
server's `unpack ok` / per-ref status comes back over the **sideband-64k** channel. The proxy
closes the receive-pack POST connection before relaying that sideband status, so git reports a
**sideband disconnect** and the ref deletion never reaches GitHub. Clone depth is *not* a factor:
a delete-only push transfers no objects, so shallow vs. full clone is irrelevant — the failure is
purely in the proxy's handling of the receive-pack sideband response. (This mechanism is
reconstructed from the observed `the remote end hung up unexpectedly` symptom; the sandbox proxy
is not directly inspectable from this repo.)

**Workaround (use everywhere — safe in local *and* web sessions).** Delete the remote ref through
the GitHub REST API via `gh`, which goes over authenticated HTTPS and bypasses send-pack:

```bash
gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"
```

For the merge case, prefer `gh pr merge --squash --delete-branch` — its branch delete already uses
the API path and is unaffected. Alternatively, defer deletion to GitHub's "automatically delete
head branches" repo setting or the weekly `prune-stale-worktrees` routine.

**Upstream fix (Claude Code sandbox).** Proxy the send-pack sideband for delete-only ref updates
— relay the full `git-receive-pack` response before closing the POST. (A full-clone fallback would
help only object-carrying pushes; it does not address delete-only updates, which send no objects.)

Tracked in [dev-env#303](https://github.com/brownm09/dev-env/issues/303). See
[ADR-035](adr/035-git-push-delete-web-session-constraint.md).

---

## Git Workflow Runbooks

Operational runbooks pointed to from [`claude/CLAUDE.md`](../claude/CLAUDE.md) → Git Workflow. The
behavioral *rules* stay in CLAUDE.md; these are the step-by-step details.

### Merging a PR developed in a worktree

Run `gh pr merge --squash --delete-branch` directly from the worktree. Do **not** call `ExitWorktree`
first — it is session-bound and becomes a no-op after `/compact` (the common case). `gh` will fail to
check out main locally and fail to delete the local branch (the worktree holds it checked out), but
the remote merge and remote branch delete complete successfully — those errors are benign. The local
worktree directory and branch are cleaned up by the weekly `prune-merged-worktrees.py` run.

### Separate clones for fully independent parallel work

Worktrees share the `.git` ref database (branches, stash, FETCH_HEAD, packed-refs). When two sessions
share no branches or PRs and you want full `.git/` isolation, use a local clone instead:

```bash
git clone --local C:/Users/brown/Git/<repo> C:/Users/brown/Git/<repo>-2
```

`--local` hardlinks the object store, so the clone is near-instant with no extra disk cost for existing
objects. Use worktrees (default) when sessions share context; a separate clone only when the two
workstreams are completely independent.

### Deleting a remote branch in Claude Code web sessions

Never use `git push origin --delete <branch>` in a web/cloud session — the sandbox HTTP git proxy does
not relay the `git-receive-pack` sideband status for a delete-only send-pack, so the push aborts with
a sideband disconnect (`the remote end hung up unexpectedly`). (Clone depth is not a factor — a delete
transfers no objects.) Delete the ref through the GitHub REST API instead, which travels over
authenticated HTTPS and bypasses send-pack — the same path `gh pr merge --squash --delete-branch`
already uses, so it is unaffected:

```bash
gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"
```

Root cause and upstream fix: [ADR-035](adr/035-git-push-delete-web-session-constraint.md) /
[Platform Constraints](#platform-constraints).

### Pre-push hook wiring (one-time setup)

Before setting, check for an existing value: `git config --system core.hooksPath` and
`git config --global core.hooksPath`. If a system-level path exists (enterprise-managed hooks),
migrate its hooks into `~/.claude/hooks/` rather than overriding. If another tool (Husky, Lefthook)
owns the global value, coordinate rather than overwrite — two tools cannot share `core.hooksPath`.
Once clear: `git config --global core.hooksPath ~/.claude/hooks`. The hook chains to any per-repo
`.git/hooks/pre-push`, so existing repo-level hooks are preserved.

---

## Engineering Journal Internals

Mechanical reference for the engineering-journal stub/compose workflow. The **behavioral rules**
(when to auto-create a stub, composition guardrails, the per-session workflow steps) live in
[`claude/CLAUDE.md`](../claude/CLAUDE.md) → Engineering Journal. This section holds the file formats
and recovery procedures that section points to.

### Manifest format (`YYYY-MM-DD.manifest.jsonl`)

One JSON line per session, appended after the token comment is known (end of session). The manifest
lets `/journal-compose` see the session count, topics, token data, and PR lifecycle without reading
individual stubs. It is advisory: if missing or shorter than the stub glob, stubs are authoritative.
Never commit the manifest separately from its stubs.

```bash
echo '{"stub":"YYYY-MM-DD_HHMMSS.stub.md","topic":"<H2 heading>","tokens":{"input":N,"output":N,"cost":N},"prs_opened":[],"prs_closed":[]}' \
  >> "C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD.manifest.jsonl"
```

- `prs_opened` / `prs_closed`: PR numbers opened / reviewed-or-merged this session (e.g., `[54]`); empty array if none.
- `priorities` (optional): array surfaced on the top-level README "Start here" dashboard. Each entry:
  `label` (required, short title); `ref` (optional, `owner/repo#N` or freeform key used for dedupe);
  `why` (optional, one-sentence rationale). Example:
  `"priorities":[{"label":"Staging gate fix","ref":"lifting-logbook#346","why":"blocks next deploy"}]`.
  `/journal-compose` aggregates these across projects (deduped by `ref`, capped at 5) — see
  [ADR-032](adr/032-journal-start-here-dashboard.md).

### Open-PR tracking file (`sessions/<project>/open-prs.jsonl`)

Tracks PRs whose full lifecycle (open → review → merge) spans multiple sessions. Carried forward
day to day via the draft branch merge to main. `/journal-compose` preserves it unchanged in the
merge-to-main commit. Schema — one JSON line per open PR:

```json
{"pr":54,"url":"https://github.com/brownm09/dev-env/pull/54","topic":"<H2 heading from stub>","stub":"YYYY-MM-DD_HHMMSS.stub.md","opened":"YYYY-MM-DD"}
```

`stub` is the filename that opened the PR — used to cross-reference the opening session when a PR
spans multiple days. **When a session opens a PR:** append a line, commit alongside the stub.
**When a session merges/closes a PR:** remove the matching line, then commit:

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

### Stub structure

Each stub file contains exactly one session block. The `<!-- opening-brief -->` block appears
**only in the first stub of the day**; subsequent stubs begin directly at `<!-- session: <slug> -->`.

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

### Canonical 11-section structure (composed once at day end)

1. Header block (Topic, Repo/Branch, Issues closed, PRs merged)
2. Table of Contents
3. Opening Brief (paste the Next Session Context from the previous day verbatim)
4. Key Decisions (bullet list with links to sections, issues, PRs, ADRs)
5. Dialogue sections (one H2 per task or topic, drawn from draft)
6. Open Items / Next Steps (checkbox list)
7. Token Usage (per-session breakdown tables: model, est. input tokens, est. output tokens, est. cost
   — drawn from `<!-- tokens: ... -->` comments; when absent use retroactive estimates labeled as such;
   close with a Combined totals table)
8. Token Optimization Suggestions (2–4 per-session observations under a `### Session N` heading; close
   with a `### Cross-Session Patterns` subsection for generalizable findings)
9. Next Session Context (the final `<!-- next-session-context -->` block from the stubs)
10. Reflection (gaps, risks, strategic questions — written last)
11. Further Reading (1–3 primary sources per session; link + one sentence on why it matters)

### Draft branch recovery

If `draft/YYYY-MM-DD` was merged or deleted before end of day (e.g., an accidental mid-day
`/journal-compose` run):

1. Create a fresh recovery branch from `origin/main`:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal fetch origin
   git -C C:/Users/brown/Git/engineering-journal checkout -b draft/YYYY-MM-DD-recovery origin/main
   ```
2. Copy all session files from the stale local branch onto the recovery branch (this also removes
   from `main` any stubs that were accidentally merged):
   ```bash
   git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD -- sessions/
   git -C C:/Users/brown/Git/engineering-journal commit -m "draft: recover YYYY-MM-DD stubs (post-kerfuffle)"
   git -C C:/Users/brown/Git/engineering-journal push -u origin draft/YYYY-MM-DD-recovery
   ```
3. If any stub content was committed directly to `main` (e.g., via ad-hoc chore/* PR), revert each
   accidental commit via a PR to `main`, then re-add the observation to the recovery branch.
4. Write the current session's stub normally — commit to `draft/YYYY-MM-DD-recovery`.
5. When running `/journal-compose`, ensure the working tree is on `draft/YYYY-MM-DD-recovery`.

**Why the `-recovery` suffix:** the pre-push hook blocks pushing to a branch that already has a
merged PR (to prevent stale-branch noise); the suffix bypasses the check while keeping the date visible.

Orphaned `chore/*` or `late-stub/*` stub PRs (sessions that fell back to ad-hoc branches when the
draft was missing) can be closed — their content was already included via the `sessions/` checkout:

```bash
gh -R brownm09/engineering-journal pr close <N> \
  --comment "Content recovered onto draft/YYYY-MM-DD-recovery — closing without merge."
```

If the working tree is simply on the wrong branch (not the draft branch), no recovery is needed —
just `git checkout draft/YYYY-MM-DD && git pull`.
