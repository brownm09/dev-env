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
/journal-compose [YYYY-MM-DD | draft/YYYY-MM-DD-recovery] [--force]
```

Composes the end-of-day engineering journal from the day's stub files. Isolates itself into a
dedicated, disposable, detached worktree of engineering-journal (`.claude/worktrees/compose-YYYY-MM-DD`,
built from `origin/draft/YYYY-MM-DD`) before touching anything — the shared canonical checkout is
never branch-switched or written to ([ADR-082](adr/082-journal-compose-worktree-isolation.md)).
Runs a field-completeness validator (Step 0.7) before any stub read — aborts with a per-entry error
listing if any manifest shard is missing a required field. Discovers all `YYYY-MM-DD_*.stub.md`
files, sorts and merges them, produces the canonical 11-section document (asserting the required
section headings before accepting a composed file as done), reconciles any of the project's
open-PR shards that have since merged or closed, commits inside the compose worktree, pushes, and
opens a PR — removing the worktree only after the PR is confirmed merged. Also refreshes the
marker-delimited `## Start here` block at the top of `engineering-journal/README.md` (freshness
stamp + top 3–5 cross-project priorities aggregated from manifest `priorities` arrays and
`open-prs.jsonl` — see [ADR-032](adr/032-journal-start-here-dashboard.md)).

**Constraint:** must run in a dedicated session with no prior task work. If other tasks were handled before invocation, the skill refuses with an error message.

**Source library:** greps `~/.claude/skills/sources.md` before spawning any research subagent (zero-cost cache hit path).

**Date argument:** defaults to today. Pass `YYYY-MM-DD` to compose a specific day's stubs, or the
full branch name `draft/YYYY-MM-DD-recovery` to source from a
[recovered draft branch](#engineering-journal-internals) instead of the plain `draft/YYYY-MM-DD`.

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

### /memory-audit

```
/memory-audit
```

Reconciles the active project's agent memory against the version-controlled instructions and emits a table — per entry: `type`, durable?, instruction home?, and a disposition (`remain-as-cache` / `promote-to-instructions` / `delete-stale`). Catches the three rot modes ADR-038's write-time rule does not: never-ported durables, stale notes (cited PRs/issues merged, "next steps" shipped), and `MEMORY.md` index drift.

**How it works:** reads every memory file and `MEMORY.md`, verifies any *claimed* instruction home actually exists on current `origin/main` (so a stale worktree base can't produce a false "drift" finding), classifies each entry, and prints the reconciliation table. Read-only by default — promotions and deletions are confirmed with the user before acting. Audit-time complement to the write-time rule and hook ([ADR-048](adr/048-memory-immortalization-issue-pairing.md), [ADR-038](adr/038-durable-preferences-documented-in-repo.md)).

---


## Hooks

Most hooks are **advisory** — they emit `systemMessage` reminders but do not block tool execution. The exception is `pre-tool-use-worktree-path-check.py` (a `PreToolUse` hook), which exits 2 with a `{"reason": "..."}` payload to block `Write`, `Edit`, and `NotebookEdit` calls that target the canonical repo root instead of the active worktree, or that are issued from an orphaned worktree whose `.git` link no longer resolves (so git silently operates on the canonical repo).

Configuration is in `claude/settings.json` (symlinked to `~/.claude/settings.json`).
See [ADR-007](adr/007-hook-command-invocation.md) for why hooks invoke scripts via `pyw -3` (the windowless variant of the Windows Python Launcher) rather than `python3` directly, wrapped in `bash -c`, or via `py -3` (which flashes a console window per spawn). Shell-invoked Python (the `## Testing` command, skill `py -3` examples, and the `pre-push` hook) continues to use `py -3`.

Any hook that spawns subprocesses (`git`, `gh`, `bash`, …) must `import _winsubp` near its imports — the helper patches `subprocess.Popen.__init__` to (1) set `CREATE_NO_WINDOW` so children don't flash a console window under `pythonw.exe`, and (2) default a text-mode call (`text=True` / `universal_newlines=True`) with no explicit `encoding=` to `encoding="utf-8", errors="replace"` rather than the Windows cp1252 default, which crashed `post-tool-use.py` reading `gh project item-add`'s output (dev-env#503). The static check in `claude/scripts/tests/test_pyw_stdio.py` fails the build if a subprocess-using hook ships without it. See ADR-007's 2026-06-01 and 2026-07-02 follow-up sections.

Any PostToolUse Bash hook that reads command output must use `read_command_output` from `claude/scripts/_hookio.py` rather than `tool_response["output"]`: Claude Code's payload carries output under `stdout`/`stderr`, so the legacy `output` read is always empty and silently disables the hook. See [ADR-049](adr/049-hook-payload-output-field.md) (root cause) and [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) (shared helper + sibling-hook fixes).

The worktree-maintenance scripts (`prune-merged-worktrees.py`, `reclaim-worktree-disk.py`) call `worktree_session_is_live` from `claude/scripts/_worktree_liveness.py` to skip a worktree with a live Claude session — it reads the worktree's transcript-dir mtime under `~/.claude/projects/`, the only signal by which an out-of-process routine can avoid severing an active session in another worktree. Windows are blast-radius-scaled (prune 24h, reclaim 6h) and override-able with `--liveness-window-min`. See [ADR-051](adr/051-worktree-liveness-guard.md).

The journal open-PR hooks (`reconcile-open-prs.py`, which `unlink`s merged/closed shards, and `post-compact.py`, which reads them to prompt a `/review`) enumerate the per-PR shards `sessions/<project>/open-prs/<N>.json` and the legacy `open-prs.jsonl` through one shared reader, `claude/scripts/_journal_shards.py` — `iter_pr_shards` returns `(path, entry)` pairs (numerically sorted; non-numeric-named, unparseable, and non-object shards skipped) and `read_legacy_entries` drains the legacy file. Centralising the read keeps the two hooks from drifting on the shard semantics and gives the legacy format a single retirement point (pairs with the engineering-journal#128 data migration). See [ADR-057](adr/057-shared-journal-shard-reader.md).

The manifest and open-PR shard **schemas** — as opposed to the open-PR shard *enumeration* above — live in `claude/scripts/_journal_schema.py`, shared between the compose-time gate `validate-manifest.py` and the write-time `journal-shard-write-advisory.py` PostToolUse hook so the required-field lists and BOM-decoding logic are defined once. It exposes `REQUIRED_FIELDS` / `OPEN_PR_REQUIRED_FIELDS`, `missing_required_fields()` / `missing_open_pr_fields()`, `find_entries_missing_fields()`, `parse_manifest_text()`, and `decode_shard_bytes()` (names a UTF-8/UTF-16 BOM rather than letting it surface as an opaque JSON parse failure on line 1). See [ADR-081](adr/081-write-time-journal-shard-validation-hook.md).

Per-session sentinel helpers, transcript-locate, and the transcript-record readers are extracted into `claude/scripts/_hookutil.py` (Stop / UserPromptSubmit hook family — the analogue of `_hookio.py` for the PostToolUse family). It exposes `cleanup_stale_sentinels(prefix)`, `sentinel_path(prefix, session_id) -> Path`, `find_transcript(session_id) -> Path | None`, and the transcript-record readers `load_records` / `_parse_records` / `iter_bash_calls` (pairs Bash tool_use/tool_result by id, returning `(command, output, cwd)`) / `_result_text` / `_content_items` — used by `posttooluse-inert-advisory.py`, `stop-tile-enumeration-gate.py`, `reconcile-open-prs.py`, and `token-tracker.py` so each no longer carries its own `SCRATCH` / `PROJECTS` constants and local copies of these helpers. The `scratch` / `projects` parameters are injectable for offline testing; `stop-tile-enumeration-gate.py` consumes `iter_bash_calls` through a thin 2-tuple adapter that drops `cwd` (it never needs it). See [ADR-064](adr/064-shared-hookutil-sentinel-transcript-locate.md) (sentinels / transcript-locate) and [ADR-090](adr/090-shared-transcript-readers-hookutil.md) (transcript-record readers).

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
| `dev-env-sync.py` | Fast-forward pulls the dev-env repo to `origin/main` so symlinked tooling stays current. When the canonical worktree is off `main`, diagnoses the worktree-on-`main` topology (`_worktree_topology.py`) and either **auto-returns a clean canonical to `main`** (then continues the pull), **warns naming a non-canonical worktree squatting `main`** plus the `git -C <wt> checkout -b claude/<slug>` park command that frees the ref, or **warns without switching when the canonical is dirty** (preserving drift). All stderr / exit-0 advisory; the worktree enumeration runs only on that rare off-main path. [ADR-006](adr/006-dev-env-sync-on-every-prompt.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md) |
| `new-day-journal-check.py` | Checks for stale `draft/*` branches on `origin/engineering-journal`. Emits a one-line warning if any are found; continues silently otherwise. Suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd). |
| `journal-onboard-check.py` | Checks whether the active git repo has a `sessions/<repo-name>/` directory in engineering-journal. Emits a one-line advisory and `/journal-onboard` hint if not. Fires once per session. |
| `turn-count-hook.py` | Warns when session context accumulates past a threshold. Primary signal: token count; secondary: turn count. Configurable via `"turn_threshold"` in `.claude/hook-config.json` (default: 50). |
| `multi-worktree-alert.py` | When ≥2 git worktrees are active, emits a list in `repo:branch` format, starring the current one. Fires on every prompt. Suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd). |
| `reconcile-open-prs.py` | Runs once per session (per-session sentinel in `scratch/`). Calls `gh pr view` for each tracked PR across every project in engineering-journal — both the per-PR shards `sessions/<project>/open-prs/<N>.json` ([ADR-056](adr/056-per-session-sharding-journal-companion-files.md)) and the legacy `sessions/<project>/open-prs.jsonl`. A MERGED/CLOSED shard is unlinked individually (no survivor rewrite; empty `open-prs/` dirs are removed); legacy entries are dropped via a safe read-filter-write. Emits a `systemMessage` listing surviving open PRs and any removals. Does not commit — the unlink stays load-bearing for `post-compact.py`'s same-checkout disk read, but nothing sweeps the deletion into a commit today ([ADR-018](adr/018-reconcile-open-prs-hook.md) + [ADR-056](adr/056-per-session-sharding-journal-companion-files.md) + [ADR-082](adr/082-journal-compose-worktree-isolation.md) together closed that path); a scoped `git status --porcelain` scan surfaces any currently-uncommitted `sessions/*/open-prs*` path in the same `systemMessage` instead, so Claude can fold it into its next stub commit's pathspec ([ADR-082 Addendum](adr/082-journal-compose-worktree-isolation.md), dev-env#578). Fails safe: `gh` errors leave the entry intact. [ADR-018](adr/018-reconcile-open-prs-hook.md), [ADR-056](adr/056-per-session-sharding-journal-companion-files.md) |
| `disk-space-check.py` | Free-space safety net for `C:`. Checks `shutil.disk_usage` on every prompt **and** — since dev-env#592/[ADR-087](adr/087-pretooluse-disk-space-check.md) — before every Bash call (also registered under `PreToolUse(Bash)`, closing the gap where a long tool-call-only stretch could outrun the once-per-prompt check). Below 20 GB free: emits a one-time `systemMessage` warning. Below 10 GB free: spawns `reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --min-free-gb 10 --protect-cwd <cwd>` **detached** (via `sys.executable`, never the `py` launcher — dev-env#300) so the heavy delete never blocks the prompt/call, and emits a `systemMessage`. Each band fires at most once per session via a `session_id`-keyed marker (`scratch/disk_space_check_<session_id>_<band>.flag`, ADR-027), shared across both hook registrations. Advisory only — exit 0 always; any exception is swallowed. Thresholds are hardcoded constants. The pure `classify_free_space()` helper is unit-tested by `tests/test_disk_space_check.py`. [ADR-037](adr/037-worktree-disk-reclamation.md), [ADR-087](adr/087-pretooluse-disk-space-check.md) |
| `worktree-npm-install.py` | When the session `cwd` is a Claude-managed worktree (`.claude/worktrees/`) of an npm repo whose `node_modules` is absent, runs `npm ci` (or `npm install`) so tests don't fail on missing deps (ADR-016). **Pre-install free-space gate (ADR-045):** before installing it checks free `C:` space — at ≥10 GB it installs as before; below 10 GB it runs a synchronous reclamation ladder (Tier 1 `reclaim-worktree-disk.py --min-free-gb 10`, Tier 2 `npm cache clean --force`) and re-measures; if still below a 5 GB hard floor it **refuses the install** and emits a loud advisory rather than risk a silently-truncated `node_modules` (ENOSPC, dev-env#364). Reclamation is synchronous (the install it guards is synchronous, so a detached reclaim would race it). Fails open on any measurement error; advisory only — exit 0 always. The pure `install_decision()` helper is unit-tested by `tests/test_worktree_npm_install.py`. [ADR-045](adr/045-pre-install-freespace-gate.md) |
| `awake-blocker.py` (start) | On UserPromptSubmit, spawns a detached watcher (if not already running) that holds a Windows system-sleep lock via `kernel32!SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED)`. Refreshes the sentinel heartbeat on every prompt. Watcher self-terminates if the sentinel is missing or older than 30 minutes (crash safety). Idempotent. Display sleep is not blocked — only system sleep. [ADR-033](adr/033-prevent-system-sleep-while-processing.md) |

---

### PreToolUse

Fires before matched tool calls. Matcher values are set per entry in `settings.json`.

#### Bash hooks

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-commit-branch-check.py` | Command contains `git commit` | Emits the current branch name as a confirmation checkpoint before the commit runs, plus a drift warning (`⚠ [cwd-drift]`) if the repo/branch recorded by `post-tool-use-cwd-track.py` after the session's last Bash call differs from the repo/branch right now. Advisory only. (ADR-085) |
| `pre-pr-create-check.py` | Command contains `gh pr create` | Emits a test-verification checklist, a documentation-gap warning (if `claude/skills/`, `claude/hooks/`, `claude/scripts/`, or `claude/routines/` were changed without updating `README.md` or `docs/REFERENCE.md`), a baseline-diff advisory when `baseline_test_failure_tracking` is enabled (ADR-030), and the current branch/repo plus a reminder to pass `--head <branch>` explicitly and a drift warning against the last-recorded Bash state (ADR-085). Enforces the "test before PR", doc-reconciliation, and pre-existing-failure rules from CLAUDE.md. |
| `pre-merge-message-check.py` | Command contains `gh pr merge` | Reads `C:/Users/brown/.claude/merge-queue.md`; if the file has non-whitespace content, **blocks the merge (exit 2)** and surfaces the messages on stderr so Claude can act on them. Intended for bypass/autonomous sessions where the user leaves feedback without interrupting. Claude clears the queue file after acting and re-runs `gh pr merge`. Fails open on any I/O error. (ADR-061) |
| `pre-merge-branch-check.py` | Command contains a top-level `gh pr merge` (via the shared `_hookio.scan_top_level` engine, same detection as `pre-merge-message-check.py`) | Emits the current branch/repo as a confirmation checkpoint before the merge runs, plus a drift warning against the repo/branch recorded after the session's last Bash call. Advisory only, mirrors `pre-commit-branch-check.py`'s pattern for `gh pr merge` instead of `git commit`. (ADR-085) |
| `pre-merge-findings-gate.py` | Command contains `gh pr merge`, excluding a `--help`/`-h`-only invocation (`is_merge_help_only`, dev-env#557 — it can never attempt a real merge, so it must not pay a live-PR lookup scoped to an unrelated PR) | Reads the target PR's last `/review` comment marker (`<!-- review-findings: blocking=N non_blocking=M -->`); if `N+M > 0` and the PR body records no "Review findings disposition" section (or `<!-- findings-disposed -->` sentinel), **blocks the merge (exit 2)** with a fix-or-file instruction. Mechanical enforcement of the all-findings merge gate (ADR-028/ADR-039). Fails open on any `gh`/parse error. Has a behavioral self-test: `bash claude/scripts/tests/test-merge-findings-gate.sh`. |
| `pre-auto-merge-checkpoint-gate.py` | Command contains `gh pr merge` carrying `--auto` (bare, or `--auto=<value>` where `<value>` isn't `false`/`0`/`no` — mirrors `is_mutating_gh_segment`'s `--delete-branch=false` handling; `--disable-auto` is never in scope), excluding a `--help`/`-h`-only invocation (`is_merge_help_only`, dev-env#557) | Extends the sibling gate's marker check with a second one, `<!-- premerge-checkpoints: adr_warrant=<written\|not-warranted\|missing> doc_reconciliation=<updated\|not-applicable\|missing> -->`, emitted by `/review` Step 2f/Step 8 alongside the existing `review-findings` marker in the same comment. Requires the PR's single most recent comment carrying **both** markers together, with the findings marker clean-or-disposed, both checkpoints fields holding a valid (non-`missing`) value, and that comment's `createdAt` no older than the PR's head commit's `committedDate` (`gh pr view --json comments,body,number,commits`). **Blocks the merge (exit 2)** on any gap — no qualifying comment, open findings with no disposition, an incomplete checkpoints marker, or a stale marker. Unlike the sibling gate, **fails CLOSED on any `gh`/network error** (a deliberate inversion — `--auto` removes every other in-session backstop the moment it succeeds) and ships with **no override token**. A bare `gh pr merge` (no `--auto`) is completely unaffected; reuses `pre-merge-findings-gate.py`'s `is_pr_merge_command`/`_parse_merge_target`/`_MARKER_RE`/`_DISPOSED_RE`/`_fetch_pr_json` via dynamic module load rather than duplicating them. Has a pure-function suite (`py -3 claude/scripts/tests/test_pre_auto_merge_checkpoint_gate.py`) and a behavioral self-test (`bash claude/scripts/tests/test-auto-merge-checkpoint-gate.sh`). [ADR-083](adr/083-auto-merge-checkpoint-gate.md) |
| `pre-merge-numbering-check.py` | Command contains `gh pr merge` (excluding a `--help`/`-h`-only invocation, `is_merge_help_only`, dev-env#557), `cwd` (or its `cd`-chain target, via `effective_merge_dir()`) resolves to the dev-env repo | Runs `git fetch origin main`, then reads the merge-base / branch-`HEAD` / `origin/main` snapshots of `CLAUDE.md`'s Testing section and `docs/adr/INDEX.md`'s ADR table. A number this branch newly introduces (absent at the merge-base) that `origin/main` has also claimed since the branch point **blocks the merge (exit 2)**, naming both colliding lines and the rebase-and-renumber fix. A non-colliding sequencing gap is advisory only (`systemMessage`, exit 0). No-op in every non-dev-env repo. Fails open on any git/network/parse error. (ADR-074) |
| `pre-tool-use-canonical-mutate-guard.py` | `cwd` resolves to a canonical (non-worktree) git checkout root — or a `-C`/`--git-dir`/`--work-tree` flag redirects the invocation at such a root from elsewhere, e.g. from a worktree (dev-env#576, ADR-071 Amendment 2) — and the command contains a git-mutating segment (`checkout`, `switch`, `commit`, `merge`, `rebase`, `reset`, `cherry-pick`, `revert`, `stash pop`/`apply`, `branch -d`/`-D`, or `pull` without `--ff-only`) or a `gh pr merge` invocation carrying `-d`/`--delete-branch` (dev-env#558, ADR-071 Amendment 1 — same harm model reached through a `gh` invocation instead of a `git` verb; a bare `gh pr merge` stays unblocked, since it merges only remotely via the GitHub API) | **Blocks the command (exit 2)** — two Claude Code sessions sharing one canonical checkout can otherwise collide (one session's `checkout`/`commit`/`reset` silently thrashes HEAD out from under the other; see dev-env#453). The block keys off the resolved *target* root (cwd's, or the `-C`/`--git-dir`/`--work-tree` redirect target), not cwd alone; the engineering-journal checkout is a temporary redirect-target carve-out (pending dev-env#346). No-op for an *ambient* (non-redirect) command from a `.claude/worktrees/<name>` cwd (ADR-024 covers that surface) or when git can't resolve a toplevel (fails open). Segments come from the shared `_hookio.split_top_level` engine (quote/subshell/heredoc-aware, dev-env#511/ADR-050 Amendment 7), so a quoted `&&`/`\|` or a heredoc body line (bare, or fed through a `$(cat <<'MARKER' ... MARKER)` command substitution) that merely *starts with* a mutating verb does not false-trigger (dev-env#481, generalized). Bypass with a genuine leading `ALLOW_CANONICAL_MUTATE=1` prefix. Has a behavioral self-test: `py -3 claude/scripts/tests/test_canonical_mutate_guard.py`. [ADR-071](adr/071-canonical-checkout-mutate-guard-hook.md) |
| `disk-space-check.py` (PreToolUse) | Every Bash call | Re-checks free `C:` space before each Bash call, not just once per prompt — closes the gap where a long tool-call-only stretch (e.g. an `npm install` mid-turn) could exhaust disk with no intervening prompt to re-trigger the `UserPromptSubmit` registration of this same script. Same thresholds, same messages, same detached-reclaim spawn, and the same per-session marker-file gate as the `UserPromptSubmit` entry (see the UserPromptSubmit table above) — whichever entry fires first for a session covers the other. `shutil.disk_usage()` is a syscall, not a subprocess spawn, so this adds negligible overhead to every Bash call. [ADR-087](adr/087-pretooluse-disk-space-check.md) |

#### Write / Edit / NotebookEdit hooks

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-tool-use-worktree-path-check.py` | Session `cwd` is inside a Claude-managed worktree and either (a) the worktree is **orphaned** — its `.git` link is missing or `git rev-parse --show-toplevel` does not resolve to the worktree root — or (b) `file_path`/`notebook_path` is absolute and starts with the canonical repo root | **Blocks** the tool call (exit 2). For an orphaned worktree, the message names the worktree + cwd and gives the recovery recipe `git worktree add --force <worktree_root> <branch>` (covers all writes from the orphan, not just canonical-root paths). Otherwise the message names the attempted path, the active worktree root, and the corrected path. No-op when the session is not in a worktree, or (for case b) when the path already targets the worktree root. The liveness check runs one `git rev-parse` per file write in a worktree, short-circuited when the `.git` link is already missing. **Bypass for intentional canonical edits:** use `Bash` with `node -e`, `sed`, or `python3` — the hook only covers the three file tools, not `Bash`. [ADR-024](adr/024-worktree-path-guard-hook.md) |

---

### PostToolUse

Fires after a matched tool call completes. Matcher values are set per entry in `settings.json`.

> **Background / SDK-launched sessions:** every PostToolUse hook below can be **silently inert** in a
> session launched as a background task / via `spawn_task` — while the `UserPromptSubmit`,
> `PreToolUse`, and `Stop` hooks from the same `settings.json` still fire. This is an upstream Claude
> Code Desktop limitation, not a hook defect: no change here can invoke an un-invoked hook. Detection
> signature (silent missing side-effects + `spawn_task` chips not rendering + `{"command":"callback"}`
> hooks in the `stop_hook_summary`) and the manual-fallback recovery are documented in
> [ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md).

#### Bash hooks

Matched with `"matcher": "Bash"`.

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pr-merge-reminder.py` | Command contains `gh pr create`, `gh pr merge`, or `git push` (when the pushed branch has an open PR); a `--help`/`-h`-only `gh pr merge` never reaches the merge reminder's live-confirmation fallback (`is_merge_help_only`, dev-env#557) | Exits 2 with a `systemMessage` reminding Claude to write a journal stub. For `gh pr create`, also emits steps `3a`/`3b` to write and stage the `open-prs/<N>.json` shard (ADR-056); parses the PR URL from `tool_response.stdout` via `_hookio.read_command_output` so the reminder can include the PR number and URL when available. For `git push`, scopes the lookup to the repo the push **actually targets** — `_effective_push_dir` honors a `cd <path> && git push` chain so a cross-repo push is evaluated against that repo, not the session cwd — then runs `git branch --show-current` and `gh pr list --head <branch>` there to confirm an open PR, and fires on **every qualifying push** to the correct repo (each carries new journalable content — scoping, not dedup, is what removes the cross-repo noise). Skips `engineering-journal` pushes (handled by `stub-push-archive-reminder.py`). For `gh pr merge`, the same scoping applies on the merge side: `effective_merge_dir` resolves a `cd <path> && gh pr merge` chain so the reminder's `repo:` field (and step 1's journal-path lookup) name the merged PR's actual repo, not the session cwd. Wrapped so any internal error exits 0 (never crashes the push flow); the pure `_effective_push_dir` and `effective_merge_dir` helpers are unit-tested by `tests/test_pr_merge_reminder.py`. [ADR-021](adr/021-auto-stub-on-pr-push.md), [ADR-065](adr/065-scope-push-reminder-to-target-repo.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md) |
| `post-merge-tile-checkpoint.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0 — mirrors `post-pr-merge-reclaim.py` / `post-pr-merge-pull.py`, dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557 — otherwise it could misattribute cwd's current branch's already-merged PR to a harmless `--help` check) | Exits 2 with a `systemMessage` reminding Claude to spawn follow-up tiles now via `spawn_task` for any out-of-scope fixes, deferred work, or ideas surfaced during the session. Only an explicit user "skip tiles" instruction exempts the checkpoint (ADR-046). Global — no opt-in. Handles worktree merges that exit non-zero via the output marker. The pure `is_successful_merge()` helper is unit-tested by `tests/test_post_merge_tile_checkpoint.py`. No subprocess calls — no `_winsubp` needed. [ADR-046](adr/046-post-merge-followup-tiles.md), [ADR-060](adr/060-post-merge-tile-checkpoint-hook.md) |
| `post-tool-use.py` | Command contains `gh issue create` or `gh pr create` | Auto-adds the created item to the configured GitHub Project, then exits 2 with a `systemMessage` listing the exact `gh project item-edit` commands to set any `required_fields` defined in `hook-config.json` — for a `single_select` field, the options shown are live-fetched via `gh api graphql` and only fall back to the cached `hook-config.json` value (labeled) if that fetch fails (ADR-076, dev-env#527). Opt-in via `project_number` + `project_owner` in `.claude/hook-config.json`. In a worktree session whose config copy is absent (a project that gitignores it, dev-env's own convention — not every project's), `load_config` resolves the **canonical checkout's** copy (regex `canonical_root_from_worktree`, shared with `reconcile-project-board.py` via `_worktree_canon.py`, for Claude-managed worktrees; `git rev-parse --git-common-dir` for siblings like `dev-env-188`) so the hook fires there too. When even that misses and the command names an explicit `--repo owner/name` for a DIFFERENT repo — a common cross-repo filing pattern that otherwise always silently no-oped, since `load_config` never inspected the command at all (dev-env#532, #537) — `load_config` looks for that repo as a sibling checkout under the same parent directory `reconcile-project-board.py --scan-dir` already scans, trusting it only when the sibling's own `hook-config.json` self-reports a matching `repo` field, never a directory-name guess alone (`extract_repo_flag` / `_sibling_repo_config`). Reads output via the shared `_hookio.read_command_output`; adds the item via the shared `_gh_project.add_to_project` (also used by `reconcile-project-board.py`). [ADR-023](adr/023-generic-required-fields-issue-hook.md), [ADR-049](adr/049-hook-payload-output-field.md), [ADR-052](adr/052-worktree-config-canonical-fallback.md), [ADR-073](adr/073-shared-worktree-canon-gh-project-modules.md), [ADR-076](adr/076-live-fetch-project-hook-single-select-options.md), [ADR-077](adr/077-cross-repo-config-resolution-for-issue-pr-create.md) |
| `post-pr-merge-pull.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0, and worktree merges exit non-zero on local cleanup despite succeeding — dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557) | Fast-forwards the local `main` branch via `git fetch origin main:main` so the local clone stays current after a merge, then **parks the just-merged worktree off `main`** if `gh --delete-branch` left it squatting the ref (only possible when the canonical had freed `main`) — recreating its `claude/<slug>` branch at HEAD via `merge_park_target` (`_worktree_topology.py`), acting on the hook's own session worktree so no liveness check is needed. Reads output via the shared `_hookio.read_command_output`; the pure `is_successful_merge()` helper is unit-tested by `tests/test_post_pr_merge_pull.py` and the park decision `merge_park_target` by `tests/test_worktree_topology.py`. `extract_repo` resolves the merged repo via `--repo` flag, GitHub PR URL, or (falling back) `effective_merge_dir`-scoped `git remote get-url origin` — so a merge run via a `cd <path> &&` chain still fast-forwards the right repo's `main`, not the session cwd's. [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md) |
| `post-pr-merge-reclaim.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0, and worktree merges exit non-zero on local cleanup despite succeeding — mirrors `post-pr-merge-pull.py`, dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557) | Spawns `reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --protect-cwd <cwd>` **detached** (via `sys.executable`, never the `py` launcher) to strip regenerable `node_modules`/`.turbo` from now-idle merged worktrees — the dominant `C:` consumer — at the idle event instead of waiting for the 6-hourly routine. No `--min-free-gb` (the trigger is the merge, not low space); `--protect-cwd` shields the active worktree. Does **not** remove the worktree directory/branch — that requires an out-of-process context (Windows cwd lock) and stays the daily `prune-stale-worktrees` job. Informational only — exit 0 always. Reads output via the shared `_hookio.read_command_output`; the pure `is_successful_merge()` helper is unit-tested by `tests/test_post_pr_merge_reclaim.py`. [ADR-045](adr/045-pre-install-freespace-gate.md), [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `post-pr-merge-project.py` | Command contains `gh pr merge` and the output confirms a completed merge; when the marker is absent, a `--help`/`-h`-only invocation is excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557 — the confirmed live incident: `--help` was previously misattributed as a completed merge, moving an unrelated issue's project item to Done) | Auto-moves the linked issue (`Closes/Fixes/Resolves #N` in PR body) to Done on the configured GitHub Project. Derives the PR number from the command (`gh pr merge <N>` / a `/pull/N` URL), falling back to gh's success marker in the output (`gh pr merge` output has no `/pull/N` URL); gated on a confirmed-merge marker so a queued `--auto` or a failed merge never moves an issue to Done. `extract_repo_from_command` also derives the repo from a PR URL argument, and the hook skips entirely when that differs from cwd's own configured `repo` — cwd's `project_number`/`project_node_id`/`status_field_id`/`done_option_id` are scoped to cwd's repo and don't apply to a different one, so a cross-repo merge is a safe no-op rather than a wrong-board move (dev-env#559; does not yet cover a `cd`-chained cross-repo merge with no URL — dev-env#569 — or resolve the correct repo's own config instead of skipping — dev-env#571). Opt-in via `status_field_id` and `done_option_id` in `hook-config.json`. Reads output via the shared `_hookio.read_command_output`; pure helpers unit-tested by `tests/test_post_pr_merge_project.py`. [ADR-014](adr/014-auto-move-project-item-done-on-merge.md), [ADR-049](adr/049-hook-payload-output-field.md), [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md) |
| `usage-snapshot.py` | Command contains `gh pr merge`, excluding a `--help`/`-h`-only invocation from the marker-absent live-confirmation fallback (`is_merge_help_only`, dev-env#557) | Queries `https://api.anthropic.com/api/oauth/usage` (via OAuth Bearer token from `~/.claude/.credentials.json`) and parses the session JSONL for the top-5 costliest exchanges. Emits a `### Usage Snapshot (post-merge)` markdown block showing weekly/5-hour utilisation vs. day-of-week soft targets (configured in `claude/usage-config.json`). Global — fires for all repos without opt-in. Include the emitted block verbatim in the post-merge journal stub. A still-valid "expiring" token is used (not skipped); an **expired** token is **refreshed on demand** at merge via the CLI (`keep-token-warm.ps1`) before fetching, so the snapshot only falls back to the stderr advisory ([#357](https://github.com/brownm09/dev-env/pull/357)) when the refresh token itself is dead ([ADR-044](adr/044-eliminate-usage-snapshot-gap-on-demand-refresh.md)). The `ClaudeKeepTokenWarm` scheduled task (see Utilities) keeps the token usually-fresh so on-demand refresh rarely fires ([ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md)). If the usage API is unreachable, the script retries once after 1 second; if both attempts fail it emits a stderr advisory and exits 2 (so the failure is visible rather than silently skipped — [#302](https://github.com/brownm09/dev-env/issues/302)). |
| `stub-push-archive-reminder.py` | `git push` to `engineering-journal` with a stub commit | Writes a sentinel file (`~/.claude/scratch/stub-pushed.flag`) and exits 0. Verifies the most-recent commit in the journal repo touches a `.stub.md` file before writing the flag. The Stop hook (`journal-stop-check.py`) consumes the sentinel and issues the archive reminder on **stderr with exit 2** — blocking the stop so the Claude-facing reminder actually reaches Claude, since a Stop hook's exit-0 stdout is not added to Claude's context ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)). The push-failure guard (`has_push_error`, reading output via the shared `_hookio.read_command_output`), the command-shape predicate (`is_git_push_command`, `scan_top_level`-anchored — ADR-050 Amendment 10), and the repo-reference predicate (`references_engineering_journal`, anchored to a `cd`/`git -C` directory argument rather than a CLI verb — ADR-050 Amendment 12) are unit-tested by `tests/test_stub_push_archive_reminder.py`. [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `journal-shard-write-advisory.py` | Command harvests a candidate `.manifest.jsonl` / `open-prs/<N>.json` path that resolves to a real file (also wired on `Write`/`Edit` — see below for the full entry) | See the full entry under **Write / Edit hooks** below. |
| `post-tool-use-cwd-track.py` | Every Bash call | Best-effort `git rev-parse --show-toplevel` + `git branch --show-current` against the payload's `cwd`, then writes `{repo_root, branch, cwd}` to a per-session state file (`~/.claude/scratch/bash_state_<session_id>.json` via the shared `_bash_state.py` module). Feeds the drift-warning check in `pre-commit-branch-check.py`, `pre-pr-create-check.py`, and `pre-merge-branch-check.py` — dev-env#573's mitigation for a session's tracked cwd/branch silently reverting with no error surfaced. A cwd that isn't a git repo, or a `git` call that fails/times out, records `None` rather than raising. Exit 0 always; no `systemMessage`, purely a side-channel write. [ADR-085](adr/085-bash-repo-branch-drift-detection.md) |

#### Write / Edit hooks

Matched with `"matcher": "Write"` and `"matcher": "Edit"` (also wired on `"matcher": "Bash"` — see the Bash table above).

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `memory-write-advisory.py` | The `Write` tool targets a `.md` file inside a `…/memory/` directory (not the `MEMORY.md` index) **and** the written body carries no immortalization link — no `#\d+` issue/PR ref, no `ADR-\d+`, no `CLAUDE.md`, no "Documented in repo" | Emits a one-line stderr reminder and exits 2 (the `Write` already ran — exit 2 *surfaces* the reminder, it does not block) telling Claude to pair the durable memory with an immortalization issue and link it from the memory body + `MEMORY.md`. The link-absence heuristic keeps it quiet on writes that already carry a link; the agent (not the hook) judges durability. Spawns no subprocess; fails open (exit 0). The pure `should_advise_memory_write()` helper is unit-tested by `tests/test_memory_write_advisory.py`. [ADR-048](adr/048-memory-immortalization-issue-pairing.md) |
| `journal-shard-write-advisory.py` | Write/Edit: `file_path` is an engineering-journal manifest shard (`sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`) or open-PR shard (`sessions/<project>/open-prs/<N>.json`) — classified by path component, not anchored to the canonical checkout, so a shard under a Claude-managed journal worktree still matches. Bash: any `.manifest.jsonl` / `open-prs/<N>.json`-shaped token harvested from the raw command text that resolves to a real file (against `cwd`, a harvested `cd`/`git -C`/`--git-dir=` directory, or the constant `~/Git/engineering-journal` fallback) | Reads the file's on-disk bytes (not the tool-call payload) and validates them against the schema shared with `validate-manifest.py` via `_journal_schema.py`: missing required fields (schema order), a UTF-8/UTF-16 BOM (named, not left as an opaque parse failure — Node `JSON.parse` and the Python shard readers both silently choke on it), an empty shard, non-JSON-object content, and — for open-PR shards, reusing `_journal_shards.shard_pr_number` — a non-numeric filename or a filename/embedded-`pr` mismatch. On any problem, exits 2 with a stderr advisory naming each file and its problems, plus both schema templates (the write already happened — this surfaces the gap immediately instead of at the next day's `/journal-compose` Step 0.7 gate). Silent (exit 0) otherwise. The Bash token harvest is a raw regex scan, deliberately **not** `_hookio.scan_top_level`-anchored — it validates on-disk data, not command intent, so a path merely mentioned inside a heredoc/quoted argument/subshell is harmless to check. Spawns no subprocess; fails open. Every pure helper is unit-tested by `tests/test_journal_shard_write_advisory.py`. [ADR-081](adr/081-write-time-journal-shard-validation-hook.md) |

---

### Stop

Fires each time Claude finishes responding (the end of every turn), not only at session end; it does not fire on user interrupts (per the [Claude Code hooks docs](https://code.claude.com/docs/en/hooks-guide)).

**All Stop hooks run in parallel — the list order below is not an execution order.** Per the same docs: *"all matching hooks run in parallel … every hook's command runs to completion before Claude Code merges the results. One hook returning `deny` doesn't stop sibling hooks from executing."* So a hook that exits 2 (`stop-tile-enumeration-gate.py`, and `journal-stop-check.py`'s archive-reminder branch — [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)) does **not** short-circuit the rest — `awake-blocker.py`'s sleep-lock release runs at every Stop regardless of a blocking hook, and reordering the list carries no meaning. See [ADR-088 → Stop-hook parallelism](adr/088-state-keyed-tile-enumeration-gate.md).

| Script | What it does |
|--------|-------------|
| `token-tracker.py` | Reads the session JSONL, aggregates token usage, and appends a record to `~/.claude/scratch/token-sessions.jsonl`. Supports Sonnet 4.6, Opus 4.6, and Haiku 4.5 pricing. |
| `journal-stop-check.py` | On the stub-push sentinel flag, **blocks the stop (exit 2, reminder on stderr)** so Claude actually archives the session: the reminder asks Claude to call the `ccd_session_mgmt__archive_session` MCP tool — a Claude-only action — and a Stop hook's exit-0 stdout is **not** added to Claude's context, so the former stdout emission was invisible to Claude ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)). Fires at most once (sentinel consumed on read) and honors the `stop_hook_active` loop guard. Then — **non-blocking** (exit 0, stdout) — checks for stale open journal stubs and unmerged draft branches (user-facing advisories pointing at later dedicated-session work, so they must not block), emitting a closing message if any are found, and cleans up orphaned draft files. Pure/fixture helpers + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_journal_stop_check.py`. [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) |
| `posttooluse-inert-advisory.py` | Reliable-event safety net for the [ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md) inert-PostToolUse limitation. Scans the just-ended transcript; if a dev-env (project #3) `gh issue/pr create` or `gh pr merge` ran but **no** `attachment` record carries `hookEvent == "PostToolUse"` (the inert signature — no `gh` call, no `project` scope), prints a one-line advisory pointing to the dev-env `CLAUDE.md` → GitHub Project → Fallback. Detection is dev-env-scoped (created URL / merged PR must be `brownm09/dev-env`) and any PostToolUse attachment all session keeps it silent, so the legitimate different-repo/no-config silent paths ([ADR-049](adr/049-hook-payload-output-field.md)) never trip it. Non-blocking (stdout, exit 0), once per session via a scratch sentinel. [ADR-055](adr/055-reliable-event-inert-posttooluse-advisory.md) |
| `stop-tile-enumeration-gate.py` | **State-keyed** post-merge tile-enumeration gate — the Stop-hook analog of `pre-merge-findings-gate` and the auto-merge-aware complement to the **command-keyed** `post-merge-tile-checkpoint.py` (ADR-060). Scans the just-ended transcript for a PR that reached MERGED state this session by **any** path (a `gh pr merge` success marker; a `gh api .../pulls/N/merge` with `"merged":true`; or a `gh pr view` MERGED state correlated with a PR the session created/enqueued — the auto-merge case the command-keyed hook is blind to). When such a merge is present but **no** tile-enumeration artifact was recorded — a `spawn_task` tool call, or the prescribed text (`Follow-ups considered … -> tiled (task_id / #N)` / `-> not tiled, because <reason>`); a bare "no follow-ups" does **not** satisfy it (the lifting-logbook#700 skip) — it **blocks the stop (exit 2)** with the reminder on stderr. Honors an explicit "skip tiles" user instruction and the `stop_hook_active` loop guard; fires at most once per session via a scratch sentinel. Pure transcript scan — no `gh`/network/subprocess (fail-open, exit 0 on any error); NOT inert in background/SDK sessions ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)), unlike the command-keyed sibling. Global — no opt-in. Pure helpers unit-tested + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_stop_tile_enumeration_gate.py`. [ADR-046](adr/046-post-merge-followup-tiles.md), [ADR-088](adr/088-state-keyed-tile-enumeration-gate.md) |
| `awake-blocker.py` (stop) | Removes the sleep-block sentinel; the detached watcher polls every second and exits within ~1s, releasing the system-sleep lock. Also registered on `Notification` for the same effect when Claude pauses for input/permission. [ADR-033](adr/033-prevent-system-sleep-while-processing.md) |

---

### PostCompact

Fires after `/compact` or auto-compact completes.

| Script | What it does |
|--------|-------------|
| `post-compact.py` | Emits a `[compact]` or `[auto-compact]` status line with the trigger type and remaining token count. Visible in all environments. On a manual `/compact`, also reads the project's open-PR records (per-PR `open-prs/<N>.json` shards plus any legacy `open-prs.jsonl`, deduped by PR — [ADR-056](adr/056-per-session-sharding-journal-companion-files.md)) and emits a `systemMessage` reminding Claude to run `/review` on each. Additionally prints a stderr advisory telling the user to type any reply to trigger the review, or press Enter to skip — because a `systemMessage` only activates on the next user-initiated turn; without the prompt users saw the output block but didn't know to reply ([#215](https://github.com/brownm09/dev-env/issues/215)). |

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

`hook-config.json` lives at `.claude/hook-config.json` in the project root. It is gitignored by dev-env's own convention (`.gitignore` ignores all of `.claude/`), but that's a per-project choice, not a universal rule — some onboarded projects track it in git instead (e.g. lifting-logbook, so its Epic-ID table stays reviewable in PRs; see that repo's CLAUDE.md Backup-and-restore procedure). Check the target project's own `.gitignore` before assuming either way.

| Field | Type | Default | Used by |
|-------|------|---------|---------|
| `repo` | string | — | `post-tool-use.py` — `"owner/repo"` filter; only acts when the created item URL contains this repo path. `post-pr-merge-project.py` — the repo to query for the merged PR's body (`gh pr view --repo`); also the baseline a merge command's own PR-URL argument is compared against, skipping entirely on a mismatch rather than mutating a different repo's board (dev-env#559) |
| `project_number` | string | — | `post-tool-use.py` — GitHub Project number; required for auto-add on issue/PR create |
| `project_owner` | string | — | `post-tool-use.py` — GitHub user/org that owns the project |
| `project_node_id` | string | — | `post-tool-use.py` — GraphQL node ID of the project; used in `gh project item-edit` commands shown in the reminder |
| `required_fields` | array | `[]` | `post-tool-use.py` — list of project fields to prompt for after issue/PR creation. Each entry: `{"name": string, "field_id": string, "type": "single_select"\|"text"\|"milestone", "options": {name: id}, "hint": string}`. The hook prints ready-to-run `gh project item-edit` commands for each field. For a `single_select` entry, `options` is a fallback only — the hook first tries a live `gh api graphql` fetch of that field's current options and uses the cached `options` map only if the live call fails (labeled in the printed reminder either way, so a stale cache is visible rather than silent — ADR-076, dev-env#527). |
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

4. **`import _winsubp` whenever a hook spawns subprocesses.** Under `pythonw.exe` (no console), every `subprocess.run`/`Popen` call that targets a console app (`git`, `gh`, `bash`, `py`, …) gets a fresh console window allocated by Windows unless `creationflags=CREATE_NO_WINDOW` is set. Separately, a text-mode call (`text=True`) with no explicit `encoding=` decodes using the Windows cp1252 default codepage instead of UTF-8, which crashes on any byte `gh`/`git` emits that cp1252 can't represent. The `_winsubp` helper (`claude/scripts/_winsubp.py`) patches both in once on import: `CREATE_NO_WINDOW` unconditionally, and `encoding="utf-8", errors="replace"` for any text-mode call that doesn't already specify its own encoding. Any new subprocess-using hook must add `import _winsubp  # noqa: F401` near its imports; the static check in `claude/scripts/tests/test_pyw_stdio.py` will fail the build otherwise. Root causes: [dev-env#297](https://github.com/brownm09/dev-env/issues/297) (console flash), [dev-env#503](https://github.com/brownm09/dev-env/issues/503) (UTF-8 decoding).

---

## Routines

Autonomous scheduled agents. They run on a cron schedule with no user interaction. Canonical source is authored and reviewed in `claude/routines/<name>/SKILL.md`, which is mirrored read-only at `~/.claude/routines/` via a directory junction — but the `scheduled-tasks` MCP tool never reads through that junction. It owns a separate, real, non-linked directory, `~/.claude/scheduled-tasks/<taskId>/SKILL.md`, holding the *live* prompt for each registered task. Merging a routine to `main` does not update or create a live task; that requires a separate `create_scheduled_task` / `update_scheduled_task` call, and nothing keeps the two copies in sync afterward short of repeating that step. Prefer having the live prompt read its own canonical file at run time and fall back to an embedded copy when unreachable (the pattern `weekly-memory-audit` uses) — see [ADR-003 amendment](adr/003-config-in-version-control.md) and [dev-env#344](https://github.com/brownm09/dev-env/issues/344).

---

### daily-journal-compose

**Schedule:** `0 7 * * *` (7:09am local, daily — the scheduler applies a small deterministic jitter on top of the base cron)

Assembles all `YYYY-MM-DD_*.stub.md` files across all configured projects into the canonical 11-section journal entries and opens PRs against `engineering-journal`.

**Retry wrapper:** `journal-compose-with-retry.sh` — wraps the routine for Windows Task Scheduler use. Retries up to 3 times with 5-minute delays on transient failures. Before each attempt except the last, also runs a liveness pre-check (`check-journal-compose-liveness.py`, below) against the shared `engineering-journal` checkout — a dirty working tree for the target date skips that attempt without spending a `claude -p` call; the final attempt proceeds regardless. Logs to `~/.claude/scratch/`. [ADR-086](adr/086-journal-compose-liveness-guard.md)

---

### prune-stale-worktrees

**Schedule:** `0 8 * * *` (8am local, daily)

Scans all primary git repos directly under `C:/Users/brown/Git` and removes worktrees whose branches are fully merged into `origin/main` — both `claude/*` branches and, via `--include-named`, hand-named branches (`feat/`, `fix/`, `docs/`, etc.) held to the identical merged/dirty/liveness bar ([ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md)) — and **parks any non-primary worktree squatting `main`** back onto its own `claude/<slug>` branch (recreated at HEAD via `git checkout -b` — non-destructive, frees the ref even for a dirty worktree the old `git worktree remove` refused; [ADR-058](adr/058-worktree-squatting-main-detection-correction.md)). Repos with no GitHub remote are skipped. Uses `git branch -d`, `git worktree remove` (no `--force`), and `git checkout -b` (parking). Skips the current worktree and dirty worktrees (for removal), and — since this routine runs out-of-process and cannot see other sessions via cwd — **any worktree with an active Claude session** (transcript activity within 24h; override with `--liveness-window-min`); the liveness guard runs before the park, so only an *idle* squatter is moved. A branch not otherwise detected as merged is still treated as merged if a repo opts in via `.claude/hook-config.json`'s `prune_ephemeral_patterns` and every file in the branch's diff vs. `origin/main` matches one of those regexes — off by default, additive only ([ADR-075](adr/075-ephemeral-diff-worktree-pruning.md)). Sends a push notification listing any unmerged branches that were skipped. [ADR-051](adr/051-worktree-liveness-guard.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-075](adr/075-ephemeral-diff-worktree-pruning.md), [ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md)

---

### reclaim-worktree-disk

**Schedule:** `0 */6 * * *` (every 6 hours)

Scans all primary git repos directly under `C:/Users/brown/Git` and strips regenerable `node_modules` and `.turbo` (top-level and nested monorepo packages) from **idle** Claude-managed worktrees — those under `.claude/worktrees/` whose working tree is clean **and** whose branch is merged into `origin/main` or has zero commits ahead of it. Complements `prune-stale-worktrees`: that removes merged worktree *directories*; this reclaims the heavy regenerable artifacts from worktrees that are idle but not yet eligible for removal, preventing `C:` saturation between the daily prune runs (dev-env#306). Reclamation is self-healing — `worktree-npm-install.py` (ADR-016) reinstalls `node_modules` on the next prompt in any Claude-managed worktree. Never touches dirty worktrees, the primary worktree, the protected/current worktree, manual sibling worktrees outside `.claude/worktrees/`, worktrees with unpushed commits ahead of `origin/main`, or **worktrees with an active Claude session** (transcript activity within 6h — shorter than prune's 24h because stripping `node_modules` is self-healing and the short window keeps reclamation aggressive against ENOSPC; override with `--liveness-window-min`). Runs `sync-routine-worktree` as Step 0. Push-notifies when ≥ 1 GB is reclaimed. [ADR-037](adr/037-worktree-disk-reclamation.md), [ADR-051](adr/051-worktree-liveness-guard.md)

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

### reconcile-project-board

**Schedule:** `0 6 * * *` (6am local, daily)

Reconciles **every git repo under `C:/Users/brown/Git` that has a `.claude/hook-config.json`**
against its own project board (today: dev-env's board #3 and lifting-logbook's board #2) via
`--scan-dir` ([ADR-070](adr/070-reconcile-project-board-scan-dir.md)): for each configured repo,
lists open issues and board items, computes the set difference (orphans = open issues not on the
board), adds each orphan via `gh project item-add`, then surfaces the orphans — and any
pre-existing **open** board items — still missing a required field (where the repo's config
declares `required_fields`, e.g. Impact / Why), printing the exact `gh project item-edit`
commands. **Add-only + report-only:** it never sets a field value (no guessing) and never mutates
single-select options, so it is safe unattended. Backstop for the gap
[ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md) documents — `post-tool-use.py`
can't board issues filed in background/`spawn_task`/SDK sessions. A repo with no
`.claude/hook-config.json` is silently skipped; a `gh` `project`-scope failure stops the whole scan
immediately (token-level, would repeat identically per repo), while any other single repo's `gh`
failure is isolated and the scan continues. Runs `sync-routine-worktree` as Step 0; push-notifies
when issues still need a required field or when any repo failed. The engine is the on-demand
`reconcile-project-board.py` (Utilities below). [ADR-068](adr/068-reconcile-project-board-orphan-issues.md), [ADR-070](adr/070-reconcile-project-board-scan-dir.md)

---

### weekly-memory-audit

**Schedule:** `0 9 * * 1` — Monday 09:00 **local** time (the `scheduled-tasks` scheduler evaluates
cron in local time). Weekly every Monday — **no parity gate** (unlike `biweekly-retro`).

Runs `sync-routine-worktree` as Step 0 (`REPO=engineering-journal`,
`VERIFY_FILE=sessions/meta/README.md`). Enumerates every project's memory store under
`~/.claude/projects/*/memory/`, excluding Claude-managed worktree project dirs
(`*--claude-worktrees-*`). Decodes each project dir to its repo working tree and GitHub slug via the
actual `git remote get-url origin` (not the dir name — handles mismatches like
`job-search` → `job-search-agent`). For each project, fans out one background `Explore` subagent
(all spawned in a single message — no synchronous preflight) to classify every non-`MEMORY.md`
memory entry. Dispositions: **remain** (durable + verified instruction home on `origin/main`),
**promote** (durable + no home + no open tracking issue = never-ported), **stale** (cites
merged/closed work or is contradicted by current code), **drift** (names a moved/renamed file or
flag), **transient** (session-local/fast-changing), **tracked-pending** (has an open issue but no
instruction home yet — report-only, not re-filed), or **index-drift** (missing from or disagreeing
with `MEMORY.md`).

**Read-only on memory.** The routine **never** edits or deletes a memory file or `MEMORY.md`.
Deletion and in-place fixes stay human-in-the-loop via the interactive `/memory-audit` skill.

**Outputs:**
- **Deduped promote issues, one per never-ported durable** (label `memory-audit`). The issue body
  carries the rule text, memory file path, suggested instruction home, and a machine-readable
  `memory-slug: <projdir>/<name>` line (project-qualified to prevent cross-project collisions when
  global rules from different projects all land in dev-env). A dedup guard reads each target repo's
  open `memory-audit` issues before filing, skipping slugs already present — the weekly cadence
  never re-files the same gap. Routing: project-specific durable → that project's repo;
  global/cross-cutting + no-remote + engineering-journal (no issue tracker by convention) → dev-env.
- A committed reconciliation report at
  `engineering-journal/sessions/meta/memory-audit/YYYY-MM-DD-audit.md` with a cross-project table
  (project · file · type · durable? · instruction home · drift · disposition), a "Promote issues
  filed" subsection, a "Stale / drift / index-drift (report-only)" subsection, and a "Projects not
  scanned (subagent failures)" subsection (omitted when every subagent returned `scanned: true` —
  absent section means no scan failures, not that failures were silently swallowed). Opened as a PR
  to `main` (never auto-merged — ADR-031; the user reviews and merges).

Stale, drift, index-drift, and tracked-pending findings are included in the report but are **not**
auto-actioned — they require human judgment.

**Resilience:** no-memory-stores exit (push-notify + EXIT 0), per-project subagent failure →
accumulated in a not-scanned list (project + reason) and surfaced as a "Projects not scanned"
table in the report (does not abort the run), git/PR failure → push-notify + keep draft for recovery.
A push notification summarizes the run on every completion or abort path.

**Dual-copy caveat (dev-env#344):** `claude/routines/weekly-memory-audit/` (version-controlled, via
junction) is the canonical definition. The live task copy at
`~/.claude/scheduled-tasks/weekly-memory-audit/SKILL.md` is written by the `create_scheduled_task`
MCP tool into a *separate real directory* and does **not** auto-sync — both must be updated on any
edit (PR for the canonical; re-register/update via MCP for the live copy).

**Origin:** dev-env#439 (child of dev-env#363); cadence and read-only-on-memory shape chosen by the
user 2026-06-30. [ADR-069](adr/069-weekly-memory-audit-routine.md)

---

## Utilities

On-demand scripts — not wired to any event. Run manually or from other scripts.

| Script | Invocation | What it does |
|--------|-----------|-------------|
| `token-report.py` | `py -3 token-report.py [--date YYYY-MM-DD] [--days N] [--project name] [--latest] [--show-subagents]` | Generates markdown and JSON token usage reports from `~/.claude/scratch/token-sessions.jsonl`. |
| `backfill-tokens.py` | `py -3 backfill-tokens.py` | Backfills token data for sessions predating the token-tracker hook. Idempotent — deduplicates on `session_id`. |
| `prune-merged-worktrees.py` | `py -3 prune-merged-worktrees.py [--dry-run] [--repo-path /path/to/repo\|--scan-dir /path/to/dir] [--liveness-window-min N] [--include-named]` | Manual equivalent of the prune routines. Auto-detects the GitHub repo slug from the origin remote URL. `--repo-path` targets a specific repo's worktrees (defaults to dev-env); `--scan-dir` discovers and prunes all git repos directly under the given directory. Removes merged `claude/*` worktrees and parks any worktree squatting `main` off onto its own branch ([ADR-058](adr/058-worktree-squatting-main-detection-correction.md)). Skips any worktree with an active Claude session (transcript activity within `--liveness-window-min`, default 1440 = 24h). A repo can opt into an additional prunability signal via `.claude/hook-config.json`'s `prune_ephemeral_patterns` — a branch whose entire diff vs. `origin/main` matches those regexes is treated as merged even without a formal merge ([ADR-075](adr/075-ephemeral-diff-worktree-pruning.md)). `--include-named` (off by default) extends the same merged/dirty/liveness checks to non-`claude/*` branches too, instead of skipping them unconditionally via the prefix guard ([ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md)). [ADR-051](adr/051-worktree-liveness-guard.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-075](adr/075-ephemeral-diff-worktree-pruning.md), [ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md) |
| `reclaim-worktree-disk.py` | `py -3 reclaim-worktree-disk.py [--dry-run] [--repo-path /path\|--scan-dir /path] [--min-free-gb N] [--protect-cwd /path] [--liveness-window-min N]` | Manual equivalent of the `reclaim-worktree-disk` routine (and the script the `disk-space-check.py` hook spawns). Strips `node_modules`/`.turbo` from idle Claude-managed worktrees (clean **and** merged-or-not-ahead). `--min-free-gb N` makes it a no-op unless the drive is below N GB; `--protect-cwd` shields the active worktree; `--liveness-window-min` (default 360 = 6h) additionally skips worktrees with an active session in *another* worktree the routine can't see via cwd. Deletes only regenerable dirs — never the worktree or git state. [ADR-037](adr/037-worktree-disk-reclamation.md), [ADR-051](adr/051-worktree-liveness-guard.md) |
| `new-branch.sh` | `new-branch <name>` (shell function; source `~/.claude/scripts/new-branch.sh` in `.bashrc`) | Creates a branch always rooted at `origin/main`. Warns when HEAD has diverged from the merge base. When `baseline_test_failure_tracking: true` is set in `.claude/hook-config.json`, also runs `baseline-tests snapshot` to capture pre-existing failures (ADR-030). |
| `baseline-tests.sh` | `baseline-tests <snapshot\|diff>` | Captures and diffs pre-existing test failures for the fix-on-touch policy ([ADR-030](adr/030-baseline-test-failure-policy.md)). `snapshot` runs the project test command (`test_command` in `hook-config.json`, default `npx jest --json --silent`) and writes failing-test fingerprints to `C:/Users/brown/.claude/scratch/baseline_<repo>_<branch>.json`. `diff` re-runs tests and classifies current failures into `new` (block PR), `preexisting-touched` (fix-on-touch or file), and `preexisting-untouched` (note only); exits 1 if any `new` failures are present. Jest-only in the first implementation. |
| `merge-stale-pr.sh` | `bash merge-stale-pr.sh <PR-URL>` | Remediates stale `engineering-journal` draft PRs: checks out the branch, warns on missing journal file, deletes orphaned drafts, rebases, and squash-merges with auto-conflict resolution. |
| `merge-ready.sh` | `bash merge-ready.sh [owner/repo ...]` | Lists, per repo, the open PRs that are green + mergeable + waiting on nothing (the merge-ready set) vs. those still open but not ready. Defaults to `brownm09/lifting-logbook`; accepts multiple `owner/repo` args. Read-only — `gh pr list` plus a `node` rollup of check states (`jq`-free, per the no-`jq` convention). |
| `get-project-item.sh` | `ITEM_ID=$(bash get-project-item.sh <issue-number> [project-number] [owner])` | Resolves a GitHub Project item node ID from an issue/PR number. Defaults to project 3, owner `brownm09`. Overridable via args or `PROJECT_NUMBER`/`PROJECT_OWNER` env vars. Requires `project` scope: `gh auth refresh -s project`. |
| `session-mode-report.py` | `py -3 session-mode-report.py [--since YYYY-MM-DD] [--interactive-only] [--non-plan-only] [--log PATH]` | Reports the startup permission mode per session by parsing the `session-mode-prompt.py` hook log (`scratch/session-mode-prompt.log`). For each `session_id` it takes the earliest entry as the startup mode, classifies sessions as interactive vs. automated (scheduled-task / `<tag>` prompts), and flags (`!`) interactive sessions that started outside `plan`. Desktop/web and spawn-task sessions launch in `bypassPermissions` by design (overriding `defaultMode: plan`); this surfaces that. Read-only; report to stdout, diagnostics to stderr. |
| `register-keep-token-warm.ps1` | `powershell -ExecutionPolicy Bypass -File register-keep-token-warm.ps1 [-IntervalHours N] [-Unregister]` | **Per-machine, run once.** Registers the non-elevated, hidden `ClaudeKeepTokenWarm` scheduled task (every 4h by default) that runs `keep-token-warm.ps1`. Idempotent (`-Force`); `-Unregister` removes it. Each machine needs its own registration. [ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md) |
| `keep-token-warm.ps1` | (scheduled-task payload — invoked by `ClaudeKeepTokenWarm`, not run by hand) | Runs `claude -p 'ok' --model haiku` to trigger the CLI's own OAuth-token refresh, keeping `~/.claude/.credentials.json` fresh so `usage-snapshot.py` works without a manual `claude` refresh. Logs token mtime + minutes-to-expiry before/after each run to `Documents\LOGS\keep-token-warm_<date>.txt` (never the token value); always exits 0. [ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md) |
| `validate-manifest.py` | `py -3 validate-manifest.py <manifest-path> [<manifest-path> ...]` | Pre-compose validator for engineering-journal manifest shards. Checks that each entry has all five required fields (`stub`, `topic`, `tokens`, `prs_opened`, `prs_closed`). Both ADR-056 per-session shards (single JSON object per file) and legacy per-day manifests (one JSON object per line) are handled — paths are parsed line-by-line. Absent/unmatched paths are skipped. Exit 0 — all entries valid; exit 1 — at least one entry is missing a required field or a line failed to parse, with file path, line number, and missing fields on stderr. Wired into `/journal-compose` as **Step 0.7** — runs before any stub read or subagent spawn so field gaps surface up front rather than mid-compose (dev-env [#423](https://github.com/brownm09/dev-env/issues/423)). |
| `reconcile-project-board.py` | `py -3 reconcile-project-board.py [--repo-root PATH\|--scan-dir PATH] [--dry-run]` | Engine behind the `reconcile-project-board` routine (and an on-demand board check). Reads `.claude/hook-config.json`, lists open issues + project items, computes orphans (open issues not on the board), adds each via `gh project item-add`, then reports the orphans + any pre-existing **open** board items still missing a required field, printing the exact `gh project item-edit` commands. **Add-only + report-only** — never sets a field value (no guessing) and never mutates single-select options. `--repo-root` targets a specific repo (defaults to the canonical checkout, so it works from a worktree); `--scan-dir` discovers and reconciles every git repo directly under the given directory that has a `.claude/hook-config.json`, skipping repos without one and isolating a single repo's `gh` failure from the rest of the scan (a `project`-scope failure still aborts the whole scan immediately). `--dry-run` reports without adding. Detects a missing `project` scope and prints the `gh auth refresh -s project` hint (exit 1). [ADR-068](adr/068-reconcile-project-board-orphan-issues.md), [ADR-070](adr/070-reconcile-project-board-scan-dir.md) |
| `check-journal-compose-liveness.py` | `git -C C:/Users/brown/Git/engineering-journal status --porcelain \| py -3 check-journal-compose-liveness.py YYYY-MM-DD` | Detects an in-flight session that may still be writing engineering-journal stubs for the date journal-compose is about to merge. Reads `git status --porcelain` output from stdin (stays pure I/O; the caller runs git) and exits 1 if any changed path is a stub/manifest shard (`YYYY-MM-DD_HHMMSS.stub.md` / `.manifest.jsonl`) for the given date, 0 otherwise; exits 2 on a malformed date argument. Called from `journal-compose-with-retry.sh` (primary, before each retry attempt) and `journal-compose/SKILL.md` Step 0.6 (defense-in-depth for manual invocations). [ADR-086](adr/086-journal-compose-liveness-guard.md) |

`prune-merged-worktrees.py`, `reclaim-worktree-disk.py`, and `reconcile-project-board.py` above all discover repos for their `--scan-dir` mode via the shared `find_git_repos()` helper in `claude/scripts/_repo_scan.py` — a non-invoked library module (like `_hookio.py` / `_worktree_liveness.py` / `_journal_shards.py` / `_hookutil.py`, none of which get their own table row) extracted from three near-identical copies. [ADR-072](adr/072-shared-repo-scan-module.md)

`post-tool-use.py` (PostToolUse hooks above) and `reconcile-project-board.py` above shared two more
near-identical copies before dev-env#454: the Claude-managed-worktree canonicalization regex (now
`canonical_root_from_worktree` / `canonical_repo_root` in `claude/scripts/_worktree_canon.py`, which
preserves each caller's own no-match contract — `None` for post-tool-use.py's fallback-chain check,
passthrough for reconcile-project-board.py's always-a-real-path caller) and the `gh project item-add`
subprocess wrapper (now `add_to_project` in `claude/scripts/_gh_project.py`, reconciled onto the
superset `(item_id, stderr)` return shape with `encoding="utf-8"` always applied — a deliberate fix for
post-tool-use.py's call site, which previously decoded with the OS default locale). Two more
non-invoked library modules in the same line as `_repo_scan.py` above. [ADR-073](adr/073-shared-worktree-canon-gh-project-modules.md)

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
| `tests/test_validate_manifest.py` | `py -3 claude/scripts/tests/test_validate_manifest.py` | Exercises the pure `missing_required_fields`, `find_entries_missing_fields`, and `parse_manifest_text` helpers in `validate-manifest.py` offline (no disk, no network, no subprocess): pins the all-five-fields-present case, each individually absent field returned in canonical order, non-dict entries treated as missing every field, `find_entries_missing_fields` order preservation and filtering, blank-line skipping, single-object ADR-056 shards, legacy multi-line manifests, invalid JSON, and JSON non-objects. `main()` is not covered (pure-helper convention). (dev-env [#423](https://github.com/brownm09/dev-env/issues/423)) |
| `tests/test-merge-stale-pr.sh` | `bash claude/scripts/tests/test-merge-stale-pr.sh` | Drives the real `merge-stale-pr.sh` against throwaway fixture repos (a bare "origin" + a working clone standing in for the shared engineering-journal checkout) with `gh` stubbed — no network, no auth. Asserts the Step 4 orphaned-draft commit's explicit pathspec ([dev-env#461](https://github.com/brownm09/dev-env/pull/461)) never sweeps in a file already staged by a simulated concurrent session; that a clean branch with no orphaned drafts skips Step 4 without a spurious commit and runs to completion; that multiple orphaned drafts across directories are all committed (guards `"${DRAFT_FILES[@]}"` array handling); and that a missing composed-journal file plus a declined prompt aborts before any mutation. Rebase and push run for real against the fixture remote; only `gh pr view`/`gh pr merge` are stubbed. (dev-env [#463](https://github.com/brownm09/dev-env/issues/463)) |
| `tests/test-setup-link-loop.sh` | `bash claude/scripts/tests/test-setup-link-loop.sh` | Sources `setup.sh` (a sourcing guard around its OS-dispatch block makes this safe) with `win_link`/`ln` stubbed to a call log, and runs the extracted `link_claude_windows()`/`link_claude_unix()` functions against a throwaway `$HOME` — no Administrator/Developer Mode privilege needed, no real `~/.claude` or global git config touched. Pins the shared `CLAUDE_FILE_LINKS`/`CLAUDE_DIR_LINKS` enumeration and each function's exact 8-target call sequence (file links, dir links, the `routines` junction, `~/bin`); also confirms the unstubbed `mkdir -p` calls create `~/.claude`/`~/.claude/scratch` for real. `setup_windows()`'s UAC elevation gate, the soft-prereq warnings, and `win_link`'s actual `cygpath`/`mklink` invocation are out of scope by design. (dev-env [#614](https://github.com/brownm09/dev-env/issues/614)) |

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
| `inputNeededNotifEnabled` | `true` | Fires a push notification when an agent session is blocked waiting on user input (e.g., a permission prompt or a question). |

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

**If the canonical was itself off `main` (the squat case).** The `fatal: 'main' is already checked out`
abort above is the *healthy* outcome — it keeps the worktree on its PR branch. If instead the canonical
checkout (`~/Git/dev-env`) was itself off `main` (violating the architecture rule — e.g. a stray
`gh pr checkout` run *in the canonical*), the `main` ref was free, so gh's local checkout **succeeds** and
the worktree is left **squatting `main`**. A squatter blocks every *other* worktree's local post-merge
checkout and stops the canonical returning to `main`, leaving the symlinked `~/.claude/` serving stale,
pre-merge tooling. This is now auto-corrected: `post-pr-merge-pull.py` parks the just-merged worktree off
`main` (recreating its `claude/<slug>` branch at HEAD), and `dev-env-sync.py` returns a *clean* canonical to
`main` on the next prompt. Manual recovery, if needed: `git -C <worktree> checkout -b claude/<slug>` (frees
`main`), then `git -C ~/Git/dev-env checkout main`. See
[ADR-058](adr/058-worktree-squatting-main-detection-correction.md) (incident: dev-env#396).

**Secondary effect — no post-merge usage snapshot (fixed by dev-env#474 / PR #477).** Before
2026-07-01, `usage-snapshot.py` gated on `tool_response.exitCode != 0`, so a worktree merge's
failed local-checkout tail (above) discarded the snapshot even though the remote merge had
succeeded. The hook now gates on gh's output success marker instead (`merge_confirmed()`,
matching `post-pr-merge-project.py`'s marker-based detection) and fires correctly on worktree
merges — confirmed in practice merging PR #477 itself, which hit this exact failure: the real
payload contained gh's success marker (`post-pr-merge-project.py`, using the same `_hookio`
dependency, correctly moved dev-env#474's linked issue to Done), and credentials/token were
independently healthy. `usage-snapshot.py`'s own output was not directly observed in chat (see
the next note), but it shares the identical marker-detection call. The snapshot can still be
legitimately absent for reasons unrelated to the worktree-exit-code case: `.credentials.json`
missing or unparseable, an expired refresh token, or the usage API unreachable after one retry —
all intentionally silent-or-advisory per the hook's own docstring, not a regression of this fix.
Separately: the observed *symptom* — no PostToolUse hook output surfacing in chat when the
*parent* `gh pr merge` call itself is shown as an error (non-zero exit) — has now recurred in at
least four occurrences examined (the two instances behind this fix, dev-env PR #512 on
2026-07-02, and career-playbook PR #635 on 2026-07-02 — the first occurrence outside dev-env
itself, confirming the gap is a property of the shared *global* hook architecture rather than
anything specific to dev-env's own worktree/board setup, since these hooks fire for every repo
without a `hook-config.json` opt-in requirement). The *mechanism* remains partially unconfirmed:
gh's own stdout success marker is independently known to be lost on this exact failure path
(dev-env#489, root-caused), and the `gh pr view` fallback that works around that now covers all
6 marker-gated hooks (dev-env#504, closed by the dev-env#504 rollout PR — [ADR-050 Amendment
8](adr/050-shared-hookio-sibling-hook-fixes.md)) — but even the original hook to receive that
fallback (`post-pr-merge-project.py`, Amendment 3) has inconclusive evidence of it actually firing
on its own: the fallback could race or fail silently rather than its stderr being dropped
(dev-env#498, open). No occurrence yet isolates a hook whose merge-detection is independently
known to have succeeded — not just inferred from a Done-status that GitHub's native close
automation equally explains — with its stderr still failing to surface (dev-env#521). Until
dev-env#498 resolves that ambiguity, confirm a hook actually ran by checking its side effect (e.g.
the linked issue's board status) rather than assuming absence of visible reminder text means the
hook didn't fire — and don't over-read that absence as proof the hook's stderr specifically was
dropped.

### A sibling worktree squatting `main` blocks a different merge's `--delete-branch`

The failure above is framed as "worktree's own merge blocked by the canonical holding `main`," but
the same `gh` mechanism fails in the mirror direction too: **any** worktree already holding `main` —
canonical or sibling — blocks whichever checkout is currently running `gh pr merge --delete-branch`,
because git allows a branch to be checked out in at most one worktree at a time. Confirmed on
lifting-logbook PR #664 (2026-07-03), merged from the canonical checkout:

```
failed to run git: fatal: 'main' is already checked out at
'C:/Users/brown/Git/lifting-logbook/.claude/worktrees/fix+issue-646-restrict-db-e2e-default-role'
```

`fix+issue-646-restrict-db-e2e-default-role` was an idle, already-merged worktree left squatting
`main`, most likely via the same root-cause chain as [ADR-058](adr/058-worktree-squatting-main-detection-correction.md)'s
original dev-env incident — unrelated to PR #664 itself. As before, the squash-merge had already
succeeded via the GitHub API; only the local checkout-and-delete step failed, so **both** the local
and remote branch deletes were skipped.

**Avoid the noisy failure — split the merge into two API-only calls up front**, rather than letting
`--delete-branch` fail and cleaning up after:

```bash
gh pr merge <N> --squash                                          # server-side only; always succeeds
gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"    # pure REST ref delete — see
                                                                    # "Deleting a remote branch in
                                                                    # Claude Code web sessions" below
```

This is preferable to the reactive "run with `--delete-branch`, let the local step fail, delete the
remote ref manually" pattern documented above whenever a squat is known or suspected — it produces no
failed-command output at all, and works identically regardless of which worktree currently holds
`main`.

**Un-squat on demand, rather than waiting for the next scheduled prune.** A squat is auto-corrected
by the daily `prune-stale-worktrees` routine (or by `post-pr-merge-pull.py` at the moment it is
created) in **any** repo, not just dev-env — [ADR-058](adr/058-worktree-squatting-main-detection-correction.md)'s
parking fix is repo-general. If a squat is actively blocking work, run the same script on demand
instead of waiting for the 8am run:

```bash
py -3 ~/.claude/scripts/prune-merged-worktrees.py --repo-path C:/Users/brown/Git/lifting-logbook
```

The squatter-park check runs unconditionally — before the `--include-named` branch-prefix gate — so
it parks an idle squatter regardless of its branch name; a *live* squatter (recent session activity)
is left alone per the [ADR-051](adr/051-worktree-liveness-guard.md) liveness guard.

Root cause, the parking mechanism, and this incident: [ADR-058](adr/058-worktree-squatting-main-detection-correction.md)
(2026-07-03 amendment). See also [ADR-066](adr/066-worktree-session-safety-rules.md) for the broader
worktree-session-safety rule set this runbook belongs to.

### `gh pr create` infers its head branch from cwd, not the pushed branch

**Trigger.** Running `gh pr create` with no `--head` flag from a cwd whose git checkout is not the
worktree branch that was just pushed — most commonly `cd`-ing into a repo's canonical checkout (kept
on `main` by the architecture rule above) to run the command from there instead of from the worktree
itself.

**Symptom.** `gh pr create` resolves head from the *current git checkout at cwd*, not from whatever
branch was most recently pushed. Only head resolution is cwd-dependent — base independently resolves
to the target repo's actual default branch via repo metadata. From a canonical checkout parked on
`main`, head wrongly resolves to `main` too, colliding with that real default, and the command fails
with an error to the effect of:

```
head branch 'main' is the same as base branch 'main', cannot create a pull request
```

**No git state is mutated by this failure** — confirmed via `git -C <canonical-path> status` staying
clean, still on `main` — so it is always safe to just retry with the fix below; there is nothing to
recover.

**Fix.** Pass `--head <branch> --repo <owner>/<repo>` explicitly so head resolution never depends on
cwd:

```bash
gh pr create --head <branch> --repo <owner>/<repo> --title "..." --body "..."
```

Distinct from the general Bash-`cd`-into-canonical rule ([ADR-066](adr/066-worktree-session-safety-rules.md))
— that rule covers `git`/`npm` commands silently acting on the wrong checkout; this is specifically
`gh pr create`'s head-branch inference, which trips on whatever the process cwd is at the moment
`gh pr create` runs (a one-off `cd <repo> && gh pr create` is enough to trigger it — the session's cwd
need not persist there). Motivating incident: dev-env PR #555.

### Stacked PR squash-merge sequencing — never `--delete-branch` a base with an open child

When a child PR's base branch is another (still-open) PR's branch — a *stacked PR* — merging the
parent with `gh pr merge --squash --delete-branch` **orphans the child, unrecoverably**:

- Deleting the base branch **auto-closes** the child PR (GitHub closes any PR whose base branch no
  longer exists).
- A closed PR's base **cannot be retargeted** — `gh pr edit <child> --base main` fails with
  *"Cannot change the base branch of a closed pull request"* — and the PR **cannot be reopened**
  (its base branch is gone). Both `gh pr edit --base` and `gh pr reopen` fail.
- The child's diff also goes `CONFLICTING`: `main` now carries the **squashed** base content, while
  the child branch still carries the base's original commits separately, underneath its own — so a
  3-way merge sees the base's changes on both sides at the same locations.

**Recovery (validated 2026-06-30).** The child branch's own commits are fine — only the PR object is
unrecoverable. Replay just the child's commits onto the new `main` and open a fresh PR:

```bash
git rebase --onto origin/main <parent-tip-SHA> <child-branch>
git push --force-with-lease origin <child-branch>
# then: gh pr create — the old PR number is lost, its base can't be fixed
```

`<parent-tip-SHA>` is the parent branch's tip commit before it was squashed into `main` (`git log
<child-branch>` to find where the parent's commits end and the child's begin). The rebase drops the
now-squashed parent commits and keeps only the child's, producing a clean single-purpose diff against
`main`.

**Prevention.** For a stacked PR pair (child's base = parent's branch), sequence the merge so the
child is never left pointing at a branch you're about to delete:

1. Merge the parent with `--squash` **without** `--delete-branch`.
2. Retarget the child to `main` while it's still open: `gh pr edit <child> --base main`.
3. `git rebase --onto origin/main <parent-tip-SHA> <child-branch>` and force-push, so the child's
   diff is clean against `main`.
4. Merge the child, *then* delete both branches.

Simplest alternative: don't stack when the two changes can ship as independent PRs off `main`.

Motivating incident: career-playbook [#587](https://github.com/brownm09/career-playbook/pull/587)
(Step 4.7) / [#591](https://github.com/brownm09/career-playbook/pull/591) (Step 4.8, which superseded
the orphaned #588).

### Separate clones for fully independent parallel work

Worktrees share the `.git` ref database (branches, stash, FETCH_HEAD, packed-refs). When two sessions
share no branches or PRs and you want full `.git/` isolation, use a local clone instead:

```bash
git clone --local C:/Users/brown/Git/<repo> C:/Users/brown/Git/<repo>-2
```

`--local` hardlinks the object store, so the clone is near-instant with no extra disk cost for existing
objects. Use worktrees (default) when sessions share context; a separate clone only when the two
workstreams are completely independent.

### Worktree deregistration recovery (lost `.git` link routes git to main)

**Trigger.** A disk-full event or worktree cleanup removes a worktree's `.git` link file (and its
`.git/worktrees/<name>/` admin dir under the main repo). git from that worktree dir then silently
walks up and resolves to the **main** repo.

**Symptoms.** `git rev-parse --git-dir` points at the main `.git`; `git ls-files` returns 0 from the
worktree; `git worktree list` omits it; a `git checkout -b` intended for the worktree lands the new
branch on **main**. Mid-session the harness surfaces it as
`PreToolUse:Edit hook error: [...worktree-path-check.py]: No stderr output` — the session's own cwd
worktree is orphaned, which blocks **every** Edit (the hook keys off session cwd, not the target path).

**Recovery** (validated 2026-06-04):

```bash
git -C <main-repo-path> checkout main                # frees the branch
git -C <main-repo-path> worktree prune               # drop stale admin entries
git -C <main-repo-path> worktree add --force .claude/worktrees/<name> <feature-branch>   # --force: orphaned dir still has files
npm install                                          # from the recreated worktree, no cd
```

**Root cause.** Disk pressure from many worktrees each carrying a full monorepo `node_modules`
(dev-env#306). Complements the orphan-liveness guard of
[ADR-024](adr/024-worktree-path-guard-hook.md) with the recovery procedure; decision:
[ADR-066](adr/066-worktree-session-safety-rules.md).

### Concurrent-session HEAD thrashing in a canonical (non-worktree) checkout

**Trigger.** Two Claude Code sessions both work directly in the same repo's canonical checkout at
once — no worktree involved on either side. One session's `git checkout` (branch switch, not
necessarily `-b`) silently moves HEAD and the working tree out from under the other, mid-session,
with no intervening user action on the affected side.

**Symptoms / detection tell.** `git branch --show-current` or `git log --oneline -1` returns a
**different branch or HEAD** than the one just created or committed on, across consecutive tool
calls in the same turn. A local `grep`/`Read` for content just committed returns nothing, while the
**remote** (`gh pr diff`, `git show origin/<branch>:<path>`) shows it correctly — that mismatch
(local absent, remote present) is the tell that the working tree has been thrashed onto a different
branch than the one the session's own commits actually landed on.

**Recovery — reconstruct first, never trust local state until diffed against `origin/main`**
(validated against both dev-env#453 incidents, 2026-07-01):

```bash
git -C <canonical-repo-path> reflog                              # reconstruct the true sequence of events
git -C <canonical-repo-path> branch --contains <your-commit-sha> # find which branch(es) actually carry your commit
git -C <canonical-repo-path> diff origin/main <your-branch> -- <touched files>   # confirm before trusting/opening a PR
```

Two recovery paths depending on what the diff shows:

1. **Attribution scrambled, but the change is intact somewhere upstream.** If `git cat-file -t
   <sha>` still resolves and `git branch --contains <sha>` shows it landed on someone else's branch
   (already merged or about to be), do not force-move it back — that repeats the same collision in
   reverse. Close the loop by editing the issue/PR record after the fact: close the original
   issue(s) with a resolution comment tracing the actual carrying commit/PR, rather than
   cherry-picking or resetting the local tree.
2. **Your branch is stale relative to `origin/main` and a PR may already be open on it.** Do **not**
   `git checkout` your branch back into the shared canonical tree to "fix" it — that is the
   reciprocal collision, yanking the *other* session's tree out from under it. Finish entirely via
   remote-only reads: `gh pr diff`, `git show origin/<branch>:<path>`, `gh api`. If the diff against
   `origin/main` is empty for your touched files, your change is already upstream — no action
   needed beyond closing your issue with a pointer to the carrying commit. If a PR is open and safe
   to merge, complete review and merge without ever touching the local displaced tree; prefer
   `gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/<branch>` over `gh pr merge
   --delete-branch` for branch cleanup in this situation, since `--delete-branch` also tries to
   switch the local checkout — exactly the touch you're avoiding.

**Second failure dimension — API rate-limit contention.** Two sessions sharing one checkout also
share (and can exhaust) the GitHub GraphQL API's 5,000/hr rate-limit bucket, disabling
`gh pr merge` / `gh pr comment` / `gh pr view --json` (all GraphQL-backed) for **both** sessions
mid-work. The REST `core` bucket is a separate quota and typically stays healthy — prefer
REST-backed `gh api` calls over GraphQL-backed `gh pr *` subcommands when the GraphQL bucket is
known to be exhausted (`gh api rate_limit` shows the remaining count per bucket).

**Prevention.** A `PreToolUse(Bash)` hook (`pre-tool-use-canonical-mutate-guard.py`) now hard-blocks
git-mutating commands issued with cwd at a canonical (non-worktree) root — isolate into a worktree
before this recovery sequence is ever needed. This runbook is the fallback for what the hook can't
catch: a manual terminal session outside Claude Code, or a bare `cd` into the canonical root from
elsewhere (the hook's sole remaining documented v1 gap — a `-C`/`--git-dir`/`--work-tree` redirect into
a canonical root is now caught, dev-env#576/ADR-071 Amendment 2). Decision: [ADR-071](adr/071-canonical-checkout-mutate-guard-hook.md).

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

### Remote git ops hang on the Git Credential Manager GUI (agent / worktree sessions)

**Symptom.** In Claude-managed worktree / non-interactive agent sessions on Windows, every *remote*
git operation — `git push`, `git fetch`, `git ls-remote` — hangs indefinitely. Git for Windows ships
**Git Credential Manager (GCM)** as the default `credential.helper`; on a credential lookup it launches
the `GitHub.UI.exe` GUI OAuth dialog, which never resolves in a session with no interactive desktop
driving it, so the command blocks until timeout. Stuck dialogs accumulate (~15 in one session) and
must be force-killed:

```bash
taskkill //F //IM GitHub.UI.exe
```

`gh` itself stays authenticated the whole time — its OAuth token lives in the OS keyring, not behind
the GUI — so only raw git-over-HTTPS is affected, never gh-mediated operations.

**Per-command workaround (fail-fast, no global change).** Point the single op at gh's token and
disable the prompt so it errors instead of hanging:

```bash
GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c 'credential.helper=!gh auth git-credential' <push|fetch|ls-remote|...>
```

The empty `-c credential.helper=` clears any inherited helper (so GCM does not run); the second `-c`
uses gh's credential helper; `GIT_TERMINAL_PROMPT=0` makes it fail fast if no credential is available.

**Persistent fix (standardized 2026-06-20).** Run once to point git's `github.com` credential helper
at gh's token globally, so *all* remote ops resolve credentials over authenticated HTTPS and never
invoke the GCM GUI:

```bash
gh auth setup-git
```

This sets the global config `credential.https://github.com.helper` to `!gh auth git-credential`
([gh manual](https://cli.github.com/manual/gh_auth_setup-git)). Verify with a no-hang smoke test —
it should return immediately instead of blocking:

```bash
git ls-remote --heads origin >/dev/null && echo OK
```

A fresh machine or a wiped git config must re-run `gh auth setup-git` (or use the per-command fallback
above). Revert if ever undesired — this restores GCM as the `github.com` helper, and the hang in agent
sessions:

```bash
git config --global --unset-all credential.https://github.com.helper
```

Root cause, decision, and alternatives: [ADR-047](adr/047-standardize-gh-credential-helper.md).

### Pre-push hook wiring (one-time setup)

Before setting, check for an existing value: `git config --system core.hooksPath` and
`git config --global core.hooksPath`. If a system-level path exists (enterprise-managed hooks),
migrate its hooks into `~/.claude/hooks/` rather than overriding. If another tool (Husky, Lefthook)
owns the global value, coordinate rather than overwrite — two tools cannot share `core.hooksPath`.
Once clear: `git config --global core.hooksPath ~/.claude/hooks`. The hook chains to any per-repo
`.git/hooks/pre-push`, so existing repo-level hooks are preserved.

### Post-merge follow-up tiles (chips)

The post-merge checklist ([`claude/CLAUDE.md`](../claude/CLAUDE.md) → Git Workflow) asks you to capture
any out-of-scope follow-ups the work surfaced. The harness mechanism for this is the `spawn_task`
background-task tool (full name `mcp__ccd_session__spawn_task`), which renders a clickable **tile**
(chip) in the UI. One click spins the follow-up into its own Claude Code session and git worktree,
seeded with the tile's prompt; otherwise the user dismisses it. The current turn continues
uninterrupted either way.

**When to use it.** At the post-merge follow-up checkpoint of
[ADR-046](adr/046-post-merge-followup-tiles.md) — when a PR reaches merged state (however it merged — a `gh pr merge` you ran, the two-step REST merge, or auto-merge), one tile per genuine,
actionable, out-of-scope item (a fix spotted in adjacent code, deferred work, tech debt, an idea worth
pursuing). The bar is the file-and-link bar ([ADR-028](adr/028-all-findings-merge-gate.md)): real
follow-ups, not speculative musings, so the tile surface stays signal-rich. (`spawn_task`'s own guidance
names other good moments too — right after verification passes, right before summarizing completed
work; ADR-046 formalizes the merge boundary specifically.)

**Tiles capture; they do not track.** A tile is *ephemeral* — chip IDs are not persisted across app
restarts, and a tile becomes real work only when the user clicks it. For a follow-up that must be
durably tracked, still file a GitHub issue; the tile's spawned session is a natural place to do that.
The tile and the issue are complementary, not redundant.

**Fallback where `spawn_task` is unavailable.** The tool is not present in every session (e.g. some
terminal CLI sessions). There, file a follow-up issue instead, so the capture still happens.

**Enforcement.** Two hooks back this checkpoint. `post-merge-tile-checkpoint.py`
([ADR-060](adr/060-post-merge-tile-checkpoint-hook.md)) is **command-keyed** — it fires the moment a
`gh pr merge` you run succeeds, but is blind to auto-merge and pure `gh api` merges.
`stop-tile-enumeration-gate.py` ([ADR-088](adr/088-state-keyed-tile-enumeration-gate.md)) is
**state-keyed** — a Stop hook that scans the transcript and blocks the stop when a PR reached merged
state this session by *any* path but no tile-enumeration was recorded (a bare "no follow-ups" does not
satisfy it). The two are complementary: the command-keyed hook is the immediate nudge, the state-keyed
hook is the Stop-time verification that also covers auto-merge and still fires in background/SDK
sessions where every PostToolUse hook is inert ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)).

Rationale, alternatives, and consequences: [ADR-046](adr/046-post-merge-followup-tiles.md).

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
| `lifting-logbook/.claude/worktrees/*/node_modules` | **dominant** — ~14 GB aggregate across ~60 worktrees (measured `du`; avg ~240 MB — a full install is ~1–2 GB, but idle trees get reclaimed so most are partial) | `reclaim-worktree-disk.py` (idle) → `prune-stale-worktrees` (merged) |
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

### Manifest shard format (`YYYY-MM-DD_HHMMSS.manifest.jsonl`)

Per [ADR-056](adr/056-per-session-sharding-journal-companion-files.md), each session writes its **own**
manifest shard — a single JSON object in `YYYY-MM-DD_HHMMSS.manifest.jsonl`, named to pair 1:1 with the
session's stub `YYYY-MM-DD_HHMMSS.stub.md`. Written after the token comment is known (end of session).
`/journal-compose` globs `YYYY-MM-DD_*.manifest.jsonl`, merges the shards in filename order (= session
order), and reads the session count, topics, token data, and PR lifecycle without opening the stubs.
Advisory: if the shard set is missing or smaller than the stub glob, stubs are authoritative. Commit
each shard with its stub — because shards are disjoint per-session files, two concurrent sessions never
write the same file and git merges their *committed content* cleanly. That guarantee covers file content,
not the shared git index in this checkout; the explicit-pathspec commit discipline that keeps one
session's commit from sweeping in another session's staged-but-uncommitted files is a behavioral rule,
not a file format — see `claude/CLAUDE.md` → Engineering Journal → Stub file workflow and
[ADR-056 → Addendum](adr/056-per-session-sharding-journal-companion-files.md).

The required-field list below is enforced twice: at compose time by `validate-manifest.py` (Step 0.7,
next-day gate) and at write time by the `journal-shard-write-advisory.py` PostToolUse hook (immediate,
in the writing session) — both against the same `REQUIRED_FIELDS` in `claude/scripts/_journal_schema.py`,
so a schema change updates one module instead of two gates drifting apart ([ADR-081](adr/081-write-time-journal-shard-validation-hook.md)).

```bash
echo '{"stub":"sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md","topic":"<H2 heading>","tokens":{"input":N,"output":N,"cost":N},"prs_opened":[],"prs_closed":[]}' \
  > "C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl"
```

- `prs_opened` / `prs_closed`: PR numbers opened / reviewed-or-merged this session (e.g., `[54]`); empty array if none.
- `priorities` (optional): array surfaced on the top-level README "Start here" dashboard. Each entry:
  `label` (required, short title); `ref` (optional, `owner/repo#N` or freeform key used for dedupe);
  `why` (optional, one-sentence rationale). Example:
  `"priorities":[{"label":"Staging gate fix","ref":"lifting-logbook#346","why":"blocks next deploy"}]`.
  `/journal-compose` aggregates these across projects (deduped by `ref`, capped at 5) — see
  [ADR-032](adr/032-journal-start-here-dashboard.md).

**Updating after a merge (no shared-file edit).** Setting `prs_closed:[N]` after a same-session merge
rewrites **this session's own shard** — a single-object file no other session touches — so there is no
concurrency hazard and no surgical-edit dance. Read the shard, mutate the field, write it back:

```bash
node -e "
  const fs = require('fs');
  const path = 'C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl';
  const o = JSON.parse(fs.readFileSync(path,'utf8'));
  o.prs_closed = [<PR_NUMBER>];
  fs.writeFileSync(path, JSON.stringify(o) + '\n');
"
```

**Legacy per-day manifest (`YYYY-MM-DD.manifest.jsonl`).** Days written before ADR-056 used a single
per-day file with one JSON line per session. Readers (`/journal-compose`, the Start-here dashboard
aggregation) union it with the shards during the transition, and it is deleted at compose alongside the
shards. No new writes go to it; the superseded ADR-054 surgical-update helper is no longer needed.

### Open-PR tracking shards (`sessions/<project>/open-prs/<N>.json`)

Tracks PRs whose full lifecycle (open → review → merge) spans multiple sessions. Per
[ADR-056](adr/056-per-session-sharding-journal-companion-files.md), each open PR is its **own** shard —
one JSON object in `sessions/<project>/open-prs/<N>.json`, keyed by PR number. Carried forward day to
day via the draft branch merge to main. `/journal-compose` does **not** blanket-delete the `open-prs/`
directory at compose — it deliberately reconciles it instead: each shard's PR state is checked via
`gh pr view` and only shards for a `MERGED`/`CLOSED` PR are removed, verified one at a time
([ADR-082](adr/082-journal-compose-worktree-isolation.md)); a shard for a still-`OPEN` PR carries
forward unchanged. Within a `sessions/<project>/` directory all PRs belong to that project's one
repo, so the bare PR number is a unique filename (the repo is still carried in `url`).
Schema:

```json
{"pr":54,"url":"https://github.com/brownm09/dev-env/pull/54","topic":"<H2 heading from stub>","stub":"YYYY-MM-DD_HHMMSS.stub.md","opened":"YYYY-MM-DD"}
```

All five fields are required (no optional fields for this shard kind) — enforced at write time by the
`journal-shard-write-advisory.py` PostToolUse hook, which also flags a non-numeric filename (invisible to
every reader below, which enumerates by numeric stem) and a filename/embedded-`pr` mismatch. The field
list lives in `OPEN_PR_REQUIRED_FIELDS` in `claude/scripts/_journal_schema.py` ([ADR-081](adr/081-write-time-journal-shard-validation-hook.md)).

`stub` is the filename that opened the PR — used to cross-reference the opening session when a PR spans
multiple days.

**When a session opens PR #N:** write the shard, commit it alongside the stub:

```bash
echo '{"pr":<N>,"url":"<url>","topic":"<H2 heading from stub>","stub":"YYYY-MM-DD_HHMMSS.stub.md","opened":"YYYY-MM-DD"}' \
  > "C:/Users/brown/Git/engineering-journal/sessions/<project>/open-prs/<N>.json"
```

**When a session merges/closes PR #N:** delete its shard. This is a per-PR `rm` that cannot touch any
other PR's record — even when a *different* session or the `reconcile-open-prs.py` hook does the
removal — so the superseded ADR-054 surgical-removal helper is no longer needed and no shared-file
read-modify-write is involved:

```bash
rm -f "C:/Users/brown/Git/engineering-journal/sessions/<project>/open-prs/<N>.json"
```

The `reconcile-open-prs.py` hook unlinks the shards of any PRs it finds MERGED/CLOSED at session start,
and removes the `open-prs/` directory once its last shard is gone.

**Legacy single file (`sessions/<project>/open-prs.jsonl`).** PRs opened before ADR-056 may still live
as lines in a single per-day-carried file. Readers union it with the shards; the reconcile hook drains
it (removing merged/closed lines via a safe read-filter-write, deleting the file when empty). To close a
PR that still lives there, remove its one line instead of deleting a shard.

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

### Report / analysis artifacts (`sessions/<project>/reports/`)

When a session produces a report or analysis the user requested (an audit, investigation
write-up, comparison, findings summary, etc.), the full output is saved as
`sessions/<project>/reports/YYYY-MM-DD-<slug>.md` and linked from that session's stub dialogue
section. The behavioral trigger — report/analysis generation is a journal boundary, no PR
required — lives in [`claude/CLAUDE.md`](../claude/CLAUDE.md) → Engineering Journal → Update
triggers → *Report / analysis generated*. The artifact is committed alongside the stub on the
day's `draft/YYYY-MM-DD` branch; `/journal-compose` does not inline it — the composed daily
document references it through the stub's link. Short analyses (≲ one screen) may be inlined in
the stub instead of linked.

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
5. Invoke `/journal-compose draft/YYYY-MM-DD-recovery` — pass the full branch name explicitly,
   not just the bare date. The skill isolates itself into its own detached worktree built from
   whatever source branch it's given ([ADR-082](adr/082-journal-compose-worktree-isolation.md));
   passing the full `-recovery` name is how you tell it to source from the recovery branch
   instead of the (already merged/deleted) plain `draft/YYYY-MM-DD`.

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
