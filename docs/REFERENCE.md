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

Most hooks are **advisory** — they emit `systemMessage` reminders but do not block tool execution. The exception is `pre-tool-use-worktree-path-check.py` (a `PreToolUse` hook), which exits 2 with a `{"reason": "..."}` payload to block `Write`, `Edit`, and `NotebookEdit` calls that target the canonical repo root instead of the active worktree, or that are issued from an orphaned worktree whose `.git` link no longer resolves (so git silently operates on the canonical repo).

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
| `worktree-npm-install.py` | When the session `cwd` is a Claude-managed worktree (`.claude/worktrees/`) of an npm repo whose `node_modules` is absent, runs `npm ci` (or `npm install`) so tests don't fail on missing deps (ADR-016). **Pre-install free-space gate (ADR-045):** before installing it checks free `C:` space — at ≥10 GB it installs as before; below 10 GB it runs a synchronous reclamation ladder (Tier 1 `reclaim-worktree-disk.py --min-free-gb 10`, Tier 2 `npm cache clean --force`) and re-measures; if still below a 5 GB hard floor it **refuses the install** and emits a loud advisory rather than risk a silently-truncated `node_modules` (ENOSPC, dev-env#364). Reclamation is synchronous (the install it guards is synchronous, so a detached reclaim would race it). Fails open on any measurement error; advisory only — exit 0 always. The pure `install_decision()` helper is unit-tested by `tests/test_worktree_npm_install.py`. [ADR-045](adr/045-pre-install-freespace-gate.md) |
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
| `pre-tool-use-worktree-path-check.py` | Session `cwd` is inside a Claude-managed worktree and either (a) the worktree is **orphaned** — its `.git` link is missing or `git rev-parse --show-toplevel` does not resolve to the worktree root — or (b) `file_path`/`notebook_path` is absolute and starts with the canonical repo root | **Blocks** the tool call (exit 2). For an orphaned worktree, the message names the worktree + cwd and gives the recovery recipe `git worktree add --force <worktree_root> <branch>` (covers all writes from the orphan, not just canonical-root paths). Otherwise the message names the attempted path, the active worktree root, and the corrected path. No-op when the session is not in a worktree, or (for case b) when the path already targets the worktree root. The liveness check runs one `git rev-parse` per file write in a worktree, short-circuited when the `.git` link is already missing. **Bypass for intentional canonical edits:** use `Bash` with `node -e`, `sed`, or `python3` — the hook only covers the three file tools, not `Bash`. [ADR-024](adr/024-worktree-path-guard-hook.md) |

---

### PostToolUse (Bash only)

Fires after each Bash tool call completes. Matched with `"matcher": "Bash"`.

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pr-merge-reminder.py` | Command contains `gh pr create`, `gh pr merge`, or `git push` (when the pushed branch has an open PR) | Exits 2 with a `systemMessage` reminding Claude to write a journal stub. For `git push`, runs `git branch --show-current` and `gh pr list --head <branch>` as subprocesses to confirm an open PR exists before emitting the reminder. Skips `engineering-journal` pushes (handled by `stub-push-archive-reminder.py`). |
| `post-tool-use.py` | Command contains `gh issue create` or `gh pr create` | Auto-adds the created item to the configured GitHub Project, then exits 2 with a `systemMessage` listing the exact `gh project item-edit` commands to set any `required_fields` defined in `hook-config.json`. Opt-in via `project_number` + `project_owner` in `.claude/hook-config.json`. [ADR-023](adr/023-generic-required-fields-issue-hook.md) |
| `post-pr-merge-pull.py` | Command contains `gh pr merge` | Fast-forwards the local `main` branch via `git fetch origin main:main` so the local clone stays current after a merge. |
| `post-pr-merge-reclaim.py` | Command contains `gh pr merge` and the merge succeeded (exit 0 or a stdout success marker — worktree merges exit non-zero on local cleanup, mirroring `post-pr-merge-pull.py`) | Spawns `reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --protect-cwd <cwd>` **detached** (via `sys.executable`, never the `py` launcher) to strip regenerable `node_modules`/`.turbo` from now-idle merged worktrees — the dominant `C:` consumer — at the idle event instead of waiting for the 6-hourly routine. No `--min-free-gb` (the trigger is the merge, not low space); `--protect-cwd` shields the active worktree. Does **not** remove the worktree directory/branch — that requires an out-of-process context (Windows cwd lock) and stays the daily `prune-stale-worktrees` job. Informational only — exit 0 always. The pure `is_successful_merge()` helper is unit-tested by `tests/test_post_pr_merge_reclaim.py`. [ADR-045](adr/045-pre-install-freespace-gate.md) |
| `post-pr-merge-project.py` | Command contains `gh pr merge` | Auto-moves the linked issue (`Closes/Fixes/Resolves #N` in PR body) to Done on the configured GitHub Project. Opt-in via `status_field_id` and `done_option_id` in `hook-config.json`. [ADR-014](adr/014-auto-move-project-item-done-on-merge.md) |
| `usage-snapshot.py` | Command contains `gh pr merge` | Queries `https://api.anthropic.com/api/oauth/usage` (via OAuth Bearer token from `~/.claude/.credentials.json`) and parses the session JSONL for the top-5 costliest exchanges. Emits a `### Usage Snapshot (post-merge)` markdown block showing weekly/5-hour utilisation vs. day-of-week soft targets (configured in `claude/usage-config.json`). Global — fires for all repos without opt-in. Include the emitted block verbatim in the post-merge journal stub. A still-valid "expiring" token is used (not skipped); an **expired** token is **refreshed on demand** at merge via the CLI (`keep-token-warm.ps1`) before fetching, so the snapshot only falls back to the stderr advisory ([#357](https://github.com/brownm09/dev-env/pull/357)) when the refresh token itself is dead ([ADR-044](adr/044-eliminate-usage-snapshot-gap-on-demand-refresh.md)). The `ClaudeKeepTokenWarm` scheduled task (see Utilities) keeps the token usually-fresh so on-demand refresh rarely fires ([ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md)). |
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

### biweekly-retro

**Schedule:** `0 9 * * 0` — Sunday 09:00 **local** time (the `scheduled-tasks` scheduler evaluates
cron in local time). The weekly trigger is gated to **even ISO week numbers** (`date +%V`) so the
effective cadence is every other Sunday. Known minor caveat: a year-boundary ISO 52→1 / 53→1
transition can nudge one cycle by a week.

Runs `sync-routine-worktree` as Step 0 (`REPO=engineering-journal`,
`VERIFY_FILE=sessions/meta/README.md`). Reads the trailing **28 days** of composed daily journals
(`YYYY-MM-DD-<slug>.md`) across every project under `engineering-journal/sessions/`, fans out one
background `Explore` subagent per active project to digest each project's window, then synthesizes a
retrospective in a fixed **v2 structure**: **§1** global cross-repo readout (+ global action items)
→ **§2** per-repo sections (each with its own action items) → **§3** a tracked
**process-to-product ratio** with the trend vs. the prior retro.

**Outputs:**
- A committed report at `engineering-journal/sessions/meta/retro/YYYY-MM-DD-retro.md`, opened as a PR
  to `main` (never auto-merged — ADR-031; the user reviews and merges).
- **Deduped action-item issues routed to the correct repo** (label `retro-action`): each repo's §2
  findings → that repo's tracker; §1 global/cross-cutting + meta + no-remote (research-notes) →
  dev-env (engineering-journal declares no issue tracker by convention). A **dedup guard** reads each
  repo's existing open `retro-action` issues and skips findings already covered, so the biweekly
  cadence never re-files the same item. Origin of this routing: dev-env#348.

**Resilience:** an off-week parity gate, an empty-window check, and the Step-0 sync ABORT all exit
cleanly with a push notification; a single project's subagent failure degrades to a partial report
rather than aborting the run.

**Origin:** dev-env#343; cadence and 4-week window chosen by the user 2026-06-09.

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
| `session-mode-report.py` | `py -3 session-mode-report.py [--since YYYY-MM-DD] [--interactive-only] [--non-plan-only] [--log PATH]` | Reports the startup permission mode per session by parsing the `session-mode-prompt.py` hook log (`scratch/session-mode-prompt.log`). For each `session_id` it takes the earliest entry as the startup mode, classifies sessions as interactive vs. automated (scheduled-task / `<tag>` prompts), and flags (`!`) interactive sessions that started outside `plan`. Desktop/web and spawn-task sessions launch in `bypassPermissions` by design (overriding `defaultMode: plan`); this surfaces that. Read-only; report to stdout, diagnostics to stderr. |
| `register-keep-token-warm.ps1` | `powershell -ExecutionPolicy Bypass -File register-keep-token-warm.ps1 [-IntervalHours N] [-Unregister]` | **Per-machine, run once.** Registers the non-elevated, hidden `ClaudeKeepTokenWarm` scheduled task (every 4h by default) that runs `keep-token-warm.ps1`. Idempotent (`-Force`); `-Unregister` removes it. Each machine needs its own registration. [ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md) |
| `keep-token-warm.ps1` | (scheduled-task payload — invoked by `ClaudeKeepTokenWarm`, not run by hand) | Runs `claude -p 'ok' --model haiku` to trigger the CLI's own OAuth-token refresh, keeping `~/.claude/.credentials.json` fresh so `usage-snapshot.py` works without a manual `claude` refresh. Logs token mtime + minutes-to-expiry before/after each run to `Documents\LOGS\keep-token-warm_<date>.txt` (never the token value); always exits 0. [ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md) |

### Script verification suite

Execution-level checks for the shell scripts themselves, run from the dev-env `## Testing`
section (the canonical list of when to run each). `bash -n` catches only syntax — these catch
runtime and environment bugs it misses, the motivating case being [dev-env#334](https://github.com/brownm09/dev-env/issues/334)
(a path-resolution bug that parsed cleanly yet failed on every run).

| Script | Invocation | What it does |
|--------|-----------|-------------|
| `tests/check-script-path-hygiene.sh` | `bash claude/scripts/tests/check-script-path-hygiene.sh` | Lints for the #334 class — a `$HOME`-rooted scratch/temp path passed to `node`, which Git Bash and Node-on-Windows resolve to different files. Scripts must use the literal `C:/Users/brown/.claude/scratch`. Hermetic; comment mentions of `$HOME` are ignored. Exit 1 on any offender. |
| `tests/test-get-project-item.sh` | `bash claude/scripts/tests/test-get-project-item.sh` | Smoke-tests `get-project-item.sh` end-to-end: asserts a known issue resolves to a `PVTI_` id, the no-match path exits 1 with a diagnostic, and the temp file is cleaned up. Network-dependent — SKIPs (exit 0) when `gh` is unauthenticated/offline. |
| `tests/run-shellcheck.sh` | `bash claude/scripts/tests/run-shellcheck.sh` | Runs shellcheck over all repo shell scripts/hooks. Blocking at `--severity=error` (tree is error-clean as of 2026-06-07); warnings/info printed advisorily. SKIPs (exit 0) with an install hint when shellcheck is absent — set `SHELLCHECK_BIN` to a [portable binary](https://github.com/koalaman/shellcheck/releases) to run it. |

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
| `permissions.defaultMode` | `plan` | **Fresh local CLI sessions** start in plan mode — no edits until the user approves a plan; override per-session with Shift+Tab. **This does not apply to Desktop/web-app or spawn-task / SDK-launched sessions:** the platform starts those in `bypassPermissions` with a startup flag that overrides `defaultMode` *by design*, so they begin off-plan regardless of this setting — `settings.json` has no lever over it, and restarting does not change it. This is expected, not a broken hook; the `session-mode-prompt` hook and `session-mode-report.py` (above) audit it. To start such a session in plan, Shift+Tab at the first prompt. See [ADR-025](adr/025-default-plan-mode.md). |
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

For the merge case, prefer `gh pr merge --squash --delete-branch` — its remote branch delete already
uses the API path, so it is unaffected by *this* send-pack proxy issue. Note the separate worktree
caveat: when the merge is run *from a worktree*, gh aborts at its local-checkout step before reaching
that API delete, so the remote branch is left in place regardless — see
[Merging a PR developed in a worktree](#merging-a-pr-developed-in-a-worktree) below. Alternatively,
defer deletion to GitHub's "automatically delete head branches" repo setting or the weekly
`prune-stale-worktrees` routine.

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
first — it is session-bound and becomes a no-op after `/compact` (the common case).

**The merge itself succeeds, but `--delete-branch` does not delete the remote branch from a worktree.**
`gh pr merge` performs the squash-merge first (a server-side API call that completes), then runs its
`--delete-branch` cleanup in *local-then-remote* order: it checks out the default branch and deletes
the **local** branch, *then* deletes the **remote** branch. From a worktree the local checkout step
fails — `gh` prints `failed to run git: fatal: 'main' is already checked out at C:/Users/brown/Git/dev-env`
(the canonical clone holds `main`, and the worktree holds the PR branch) — and `gh` aborts at that
point. Because the abort happens *before* the remote-delete step, **both the local branch delete and
the remote branch delete are skipped.** Confirmed on dev-env PRs #327 and #329 (2026-06-06): each
left the remote ref in place, requiring a manual delete that reported `[deleted]`.

After the merge, delete the remote ref manually:

```bash
git push origin --delete <branch>
# or, in a web/cloud session where send-pack is blocked (see the web-session runbook below):
gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"
```

The local worktree directory and branch are cleaned up by the weekly `prune-merged-worktrees.py` run.

**Secondary effect — no post-merge usage snapshot.** Because the `gh pr merge` invocation exits
non-zero on the failed local-checkout tail, the `PostToolUse` post-merge hook does **not** emit its
`### Usage Snapshot (post-merge)` block — it keys off a clean `gh pr merge`. When merging from a
worktree, expect the snapshot to be absent and capture usage by other means for the journal stub.

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

## Disk-Full (ENOSPC) Recovery

The `C:` drive has saturated to 0 bytes free more than once (dev-env#306, dev-env#364), each time
mid-`npm install` and each time surfacing *indirectly* as corrupted dependencies rather than an obvious
"disk full" error. This runbook captures the failure signature so it is recognized in seconds, the
dominant consumers so the right thing is cleaned, and the recovery steps. The *automated* defenses are
the `disk-space-check.py` and `worktree-npm-install.py` hooks plus the `reclaim-worktree-disk` /
`prune-stale-worktrees` routines ([ADR-037](adr/037-worktree-disk-reclamation.md),
[ADR-045](adr/045-pre-install-freespace-gate.md)); this section is the manual fallback.

### Recognizing an ENOSPC-truncated install

When `C:` runs out mid-install, **npm can still report exit 0** while leaving packages partially
extracted. The corruption then surfaces downstream as confidently-misleading errors — read past the
top-line message (Error-Message-Diligence rule):

- **Jest:** `Preset ts-jest not found` whose real cause is `bs-logger/dist/index.js` `MODULE_NOT_FOUND`
  deep in ts-jest's load chain — i.e. a *truncated* package, not a missing config.
- **Next.js:** `next dev` crashes on boot because a native binary is truncated —
  `@next/swc-win32-x64-msvc` at **32.5 MB** instead of the valid **136.8 MB** — producing a downstream
  `next.config.compiled.js` "Unexpected token 'export'". Cascades into every Playwright test failing
  with `ERR_CONNECTION_REFUSED`.

**First diagnostic step, always:** `df -h /c`. If free space is near zero (or was recently), suspect
truncation before chasing the named error.

**Distinguish from Node-24 incomplete tarballs (lifting-logbook#373):** that is a different root cause
with overlapping symptoms — it happens on Node 24 *regardless* of free space. If `node --version` is 24
and the disk has ample free space, it is the tarball issue, not ENOSPC.

**Confirm a suspected truncation:** compare a native binary's on-disk size to its published size, e.g.
`ls -la node_modules/@next/swc-win32-x64-msvc/*.node`.

### Dominant `C:` consumers (where the space goes)

| Consumer | Typical size | Reclaim with |
|---|---|---|
| `lifting-logbook/.claude/worktrees/*/node_modules` | **dominant** — ~14 GB aggregate across ~60 worktrees (measured `du`; avg ~230 MB — a full install is ~1–2 GB, but idle trees get reclaimed so most are partial) | `reclaim-worktree-disk.py` (idle) → `prune-stale-worktrees` (merged) |
| Docker Desktop (Testcontainers images/volumes) | ~5–6 GB | `docker system prune` (destructive — see below) |
| Playwright browser bundles | ~700 MB | `npx playwright uninstall` (reinstalled on next test run) |
| npm cache | ~700 MB | `npm cache clean --force` |
| `dev-env/.claude/worktrees` | ~tens of MB (no `node_modules`) | negligible |

### Recovery steps

```bash
df -h /c                                   # 1. confirm it really is disk exhaustion
npm cache clean --force                    # 2. ~700 MB, fully regenerable

# 3. reclaim regenerable artifacts from idle worktrees (the bulk), then remove merged ones
py -3 ~/.claude/scripts/reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git
git worktree prune                         #    drop stale worktree admin entries

docker system prune                        # 4. ~5–6 GB — DESTRUCTIVE: removes stopped containers,
                                           #    unused networks, dangling images. Re-pulled on next use.

# 5. re-extract any package confirmed truncated, then a clean reinstall
rm -rf node_modules/<pkg> && npm install <pkg> --no-save
npm ci                                     #    full clean reinstall once space is recovered
```

`docker system prune` is deliberately **not** run by any hook — it deletes images/volumes that may be
expensive to rebuild and is not transparently regenerable, so it stays a manual decision. The automated
ladder in `worktree-npm-install.py` (idle-worktree reclaim → `npm cache clean`) covers only the
regenerable tiers, then *refuses* a low-space install rather than risk truncation.

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
