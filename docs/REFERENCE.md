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

Composes the end-of-day engineering journal from the day's stub files. Discovers all `YYYY-MM-DD_*.stub.md` files, sorts and merges them, produces the canonical 11-section document, commits to the `draft/YYYY-MM-DD` branch, and opens a PR.

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

Reviews a PR or pasted diff for correctness, security, reliability, and maintainability. Produces a structured report with blocking findings, non-blocking findings, questions for the author, and optional style notes.

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

All hooks are **advisory** — they emit `systemMessage` reminders via exit 2 but do not block tool execution. No hook exits with a non-zero code that prevents the matched tool from running.

Configuration is in `claude/settings.json` (symlinked to `~/.claude/settings.json`).
See [ADR-007](adr/007-hook-command-invocation.md) for why hooks call `python3` directly rather than via a `bash -c` wrapper.

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
| `dev-env-sync.py` | Fast-forward pulls the dev-env repo to `origin/main` so symlinked tooling stays current. Skips if a feature branch is active in the main worktree. [ADR-006](adr/006-dev-env-sync-on-every-prompt.md) |
| `new-day-journal-check.py` | Checks for stale `draft/*` branches on `origin/engineering-journal`. Emits a one-line warning if any are found; continues silently otherwise. Suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd). |
| `journal-onboard-check.py` | Checks whether the active git repo has a `sessions/<repo-name>/` directory in engineering-journal. Emits a one-line advisory and `/journal-onboard` hint if not. Fires once per session. |
| `turn-count-hook.py` | Warns when session context accumulates past a threshold. Primary signal: token count; secondary: turn count. Configurable via `"turn_threshold"` in `.claude/hook-config.json` (default: 50). |
| `multi-worktree-alert.py` | When ≥2 git worktrees are active, emits a list in `repo:branch` format, starring the current one. Fires on every prompt. Suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd). |

---

### PreToolUse (Bash only)

Fires before each Bash tool call. Matched with `"matcher": "Bash"`.

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-commit-branch-check.py` | Command contains `git commit` | Emits the current branch name as a confirmation checkpoint before the commit runs. |
| `pre-pr-create-check.py` | Command contains `gh pr create` | Emits a test-verification checklist. Enforces the "test before PR" rule from CLAUDE.md. |

---

### PostToolUse (Bash only)

Fires after each Bash tool call completes. Matched with `"matcher": "Bash"`.

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pr-merge-reminder.py` | Command contains `gh pr create` or `gh pr merge` | Exits 2 with a `systemMessage` reminding Claude to write a journal stub. |
| `post-tool-use.py` | Command contains `gh issue create` or `gh pr create` | Auto-adds the created item to the configured GitHub Project. Opt-in via `"github_project_id"` in `.claude/hook-config.json`. |
| `post-pr-merge-pull.py` | Command contains `gh pr merge` | Fast-forwards the local `main` branch via `git fetch origin main:main` so the local clone stays current after a merge. |
| `post-pr-merge-project.py` | Command contains `gh pr merge` | Auto-moves the linked issue (`Closes/Fixes/Resolves #N` in PR body) to Done on the configured GitHub Project. Opt-in via `status_field_id` and `done_option_id` in `hook-config.json`. [ADR-014](adr/014-auto-move-project-item-done-on-merge.md) |
| `stub-push-archive-reminder.py` | `git push` to `engineering-journal` with a stub commit | Writes a sentinel file (`~/.claude/scratch/stub-pushed.flag`) and exits 0. Verifies the most-recent commit in the journal repo touches a `.stub.md` file before writing the flag. The Stop hook (`journal-stop-check.py`) consumes the sentinel and issues the archive reminder via exit 2. |

---

### Stop

Fires when the Claude Code session ends (user exits or `/stop`).

| Script | What it does |
|--------|-------------|
| `token-tracker.py` | Reads the session JSONL, aggregates token usage, and appends a record to `~/.claude/scratch/token-sessions.jsonl`. Supports Sonnet 4.6, Opus 4.6, and Haiku 4.5 pricing. |
| `journal-stop-check.py` | Checks for the stub-push sentinel flag (emits a closing message reminding the user to archive if found), then checks for stale open journal stubs and unmerged draft branches, emitting a closing message if any are found. Exit 0 always. |

---

### PostCompact

Fires after `/compact` or auto-compact completes.

| Script | What it does |
|--------|-------------|
| `post-compact.py` | Emits a `[compact]` or `[auto-compact]` status line with the trigger type and remaining token count. Visible in all environments. |

---

### Git hook: `hooks/pre-push`

A global git pre-push hook installed via `core.hooksPath` (see [ADR-005](adr/005-global-core-hooks-path.md)).

**What it does:** before every `git push`, checks whether the branch's merge base diverges from `origin/main` in squash-merge repos. Warns when it detects a branch that was cut from a squash-merged ancestor (which would cause a rebase to fail). Chains to any existing per-repo `.git/hooks/pre-push` so repo-level hooks are preserved.

---

### Configuration

`hook-config.json` lives at `.claude/hook-config.json` in the project root (not version-controlled).

| Field | Type | Default | Used by |
|-------|------|---------|---------|
| `github_project_id` | string | — | `post-tool-use.py` — opt-in; item is added to this project when an issue or PR is created |
| `turn_threshold` | integer | `50` | `turn-count-hook.py` — warn after N turns; warns again every 25 turns thereafter |
| `status_field_id` | string | — | `post-pr-merge-project.py` — GitHub Project Status field ID; required to auto-move item to Done on merge |
| `done_option_id` | string | — | `post-pr-merge-project.py` — single-select option ID for "Done" status; required to auto-move item to Done on merge |

---

### Authoring rules

PreToolUse hooks that exit non-zero **block the matched tool call silently** — the user sees the tool refused with no error pointing to the hook. Three invariants prevent recurrence:

1. **Atomic commits.** A `settings.json` hook entry and its script file must land in the **same commit**. Never push a `settings.json` change that references a script not yet in `claude/scripts/` on main. Verify by running the script **from the dev-env repo root** (not via `~/.claude/scripts/` — that junction resolves against the main worktree checkout, not the branch being tested):
   ```bash
   python3 claude/scripts/<new-hook>.py < /dev/null; echo "exit: $?"
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

3. **No `bash -c` wrappers.** Hook commands call the interpreter directly: `python3 C:/Users/brown/.claude/scripts/foo.py` — never `bash -c 'python3 ...'`. The wrapper adds an exit-code-propagation layer and can fail independently on Windows (quoting issues, PATH differences). Root cause of [dev-env#81](https://github.com/brownm09/dev-env/issues/81).

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

**Schedule:** `0 0 * * 0` (midnight UTC, every Sunday)

Removes `claude/*` worktrees whose branches are fully merged into `origin/main`. Uses `git branch -d` and `git worktree remove` (no `--force`). Skips the current worktree, dirty worktrees, and any worktree not named `claude/*`. Sends a push notification listing any unmerged branches that were skipped.

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
| `token-report.py` | `python3 token-report.py [--date YYYY-MM-DD] [--days N] [--project name] [--latest] [--show-subagents]` | Generates markdown and JSON token usage reports from `~/.claude/scratch/token-sessions.jsonl`. |
| `backfill-tokens.py` | `python3 backfill-tokens.py` | Backfills token data for sessions predating the token-tracker hook. Idempotent — deduplicates on `session_id`. |
| `prune-merged-worktrees.py` | `python3 prune-merged-worktrees.py` | Manual equivalent of the `prune-stale-worktrees` routine. |
| `new-branch.sh` | `new-branch <name>` (shell function; source `~/.claude/scripts/new-branch.sh` in `.bashrc`) | Creates a branch always rooted at `origin/main`. Warns when HEAD has diverged from the merge base. |
| `merge-stale-pr.sh` | `bash merge-stale-pr.sh <PR-URL>` | Remediates stale `engineering-journal` draft PRs: checks out the branch, warns on missing journal file, deletes orphaned drafts, rebases, and squash-merges with auto-conflict resolution. |

---

## Model Selection

Route tasks to the least powerful model that can handle them reliably:

| Task type | Model |
|-----------|-------|
| Mechanical: search, format, summarize, diff, rename | Haiku |
| Standard dev: feature implementation, debugging | Sonnet |
| Complex: architectural decisions, novel problems, multi-file reasoning, writing test code, `/review` skill | Opus |

Default to Sonnet when uncertain. Never use Opus for tasks a Haiku prompt handles correctly on the first try.
