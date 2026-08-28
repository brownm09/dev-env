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

**Multi-project mode:** when the day's stubs span more than one project directory, the skill fans
out one Haiku composer subagent per project — all inside the single shared compose worktree — then
runs the README/commit/PR work once. Each subagent's `.draft-compose.lock` is **project-scoped**:
it checks only `sessions/<project>/.draft-compose.lock` and never globs, because every lock inside
that worktree belongs to the same run. A peer's lock is the expected signal that the fan-out is
working, and its own project's lock means it is a re-spawn taking over from a failed predecessor —
neither is a concurrency abort ([ADR-082 Addendum](adr/082-journal-compose-worktree-isolation.md),
dev-env#889). Step 0.6's cross-project lock glob is the separate, still-unqualified guard against a
genuinely concurrent *invocation*.

**Source library:** greps `~/.claude/skills/sources.md` before spawning any research subagent (zero-cost cache hit path). A new source found on a cache miss is queued via `queue-source-library-entry` into a dedicated dev-env worktree (`chore/research-sources-queue`), not written to `~/.claude/skills/sources.md` directly — that path is a junction onto the canonical dev-env checkout, unaffected by this skill's own engineering-journal worktree isolation, and writing through it from Section 11's nightly, unattended runs left the canonical dirty and blocked every session's sync hook on this machine ([ADR-102](adr/102-source-library-writes-through-worktree.md)).

**Date argument:** defaults to today. Pass `YYYY-MM-DD` to compose a specific day's stubs, or the
full branch name `draft/YYYY-MM-DD-recovery` to source from a
[recovered draft branch](#engineering-journal-internals) instead of the plain `draft/YYYY-MM-DD`.

**Today-guard:** composing today's date requires `--force` (ADR-017). `FORCE` is resolved
mechanically, as the very first action of Step 0.6, by `journal-compose-force-resolve.py` reading
the literal `$ARGUMENTS` text — not by the skill's own reasoning — and the
`pre-tool-use-journal-compose-force-guard.py` `PreToolUse` hook then hard-blocks the worktree-add /
commit / push commands for a same-day target unless that resolution produced a fresh `force=true`
marker, so the guard can no longer be silently reasoned past (dev-env#631,
[ADR-096](adr/096-journal-compose-mechanical-force-guard.md)).

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

**Source-library writes:** a new source found on a cache miss is never written to
`~/.claude/skills/sources.md` directly — that path is a junction onto the canonical dev-env
checkout, and writing through it left the canonical dirty and blocked every session's sync hook,
twice ([PR #649](https://github.com/brownm09/dev-env/pull/649),
[dev-env#697](https://github.com/brownm09/dev-env/issues/697)). Instead it's queued via
`queue-source-library-entry` into a dedicated worktree (`chore/research-sources-queue`, not
auto-PR'd) — see [ADR-102](adr/102-source-library-writes-through-worktree.md).

---

### /review

```
/review <PR-URL | --diff> [--no-style] [--author junior|mid|senior] [--focus security|correctness|perf] [--no-comment]
```

Reviews a PR or pasted diff for correctness, security, reliability, and maintainability. Runs five pre-checks before the main analysis:

- **Step 2b — Documentation Reconciliation:** flags as a blocking finding if `claude/skills/**`, `claude/hooks/**`, `claude/scripts/**`, or `claude/routines/**` were changed without updating `README.md` or `docs/REFERENCE.md` (applies to repos with a `Documentation Maintenance` table in their `CLAUDE.md`). Probes `.claude/CLAUDE.md` then `CLAUDE.md` as two **separate** API reads — never `||`-chained — and treats only a `Not Found (HTTP 404)` as absence; a `No commit found for the ref` 404 (wrong repo / missing ref) and every other failure stop the review instead of silently skipping the gate (see [ADR-120](adr/120-review-skill-absence-checks-over-api.md)).
- **Step 2c — Documentation Coverage:** for every file added, deleted, renamed, or significantly rewritten, walks up ancestor directories and uses LLM judgment to determine whether a README at any of those levels should have been updated. Flags as a blocking finding when an existing README was not touched; suggests creation as a non-blocking finding when a directory would benefit from an index it lacks. Ancestors are deduplicated before lookup, and each README probe classifies by exit status per [ADR-120](adr/120-review-skill-absence-checks-over-api.md).

  > **Remote reads (Steps 2b + 2c).** Both read blobs via `gh api "repos/<HEAD_OWNER>/<HEAD_REPO>/contents/<path>?ref=<headRefName>" -H "Accept: application/vnd.github.raw"`, using the PR's **head** repo (`headRepositoryOwner`/`headRepository` from Step 2) rather than the base repo parsed from the PR URL — on a fork PR the head ref does not exist in the base repo, and every probe would 404. This upholds [ADR-004](adr/004-pr-review-reads-from-remote.md) (read from the remote, never the local worktree) while avoiding `git show <ref>:<path>`, which MSYS deterministically mangles on Windows for any leading-dot path segment — the failure that made these two gates silently no-op for `.claude/` and `.github/` paths ([#602](https://github.com/brownm09/dev-env/issues/602), [#877](https://github.com/brownm09/dev-env/issues/877)). The API form also needs no prior `git fetch` and works when the reviewed repo is not the cwd.
- **Step 2d — Test Coverage Gate:** enforces the global "Test before PR" rule (see [ADR-022](adr/022-test-coverage-gate-before-pr.md)). Inspects the diff for new testable behavior (new endpoints, pages, exported functions, CLI commands, bug fixes) and flags as a blocking finding when new behavior is present but no test files appear in the diff and no deferral rationale is documented in the PR body.
- **Step 2e — Test Integrity Gate:** enforces the global Test Integrity policy (see [ADR-029](adr/029-test-integrity-policy.md)). Scans the diff for skip markers (`it.skip`, `xit`, `xdescribe`, `test.skip`, `describe.skip`, `.todo`, `pending`), deleted test files or blocks, lowered coverage thresholds, bypass flags (`--passWithNoTests`, `--bail`, `--testPathIgnorePatterns`), and implementation branches that appear to skew toward specific test inputs. Flags as a blocking finding when any pattern matches without a PR-body justification, and when the PR body lacks the required `Tests: N passed, N skipped, N failed` summary line. **Language-scope preamble:** before running the JS/TS scanners, detects `*.py`, `*.go`, `*.rs`, or `*.rb` files in the diff and emits a non-blocking **maintainability** finding noting that the automated patterns cover JS/TS only — converting the silent bypass on non-JS repos into a visible reviewer prompt to verify pytest / Go / Rust / Ruby skip idioms by hand.
- **Step 2f — ADR-Warrant Check:** enforces [ADR-011](adr/011-adr-warrant-check.md)'s four criteria for whether a change needs a new or amended Architectural Decision Record (touches a `claude/`-documented rule/hook/skill/setting, restructures a `claude/` directory, establishes a workflow rule other CLAUDE.md files reference, or has rationale hard to recover from `git log` alone). Flags a blocking `[documentation]` finding when a qualifying change has no ADR in the diff and the PR body cites no prior PR covering the decision. A citation satisfies the check only if the cited PR establishes precedent for the same class of change, not merely a superficially similar one — see [ADR-083](adr/083-auto-merge-checkpoint-gate.md)'s 2026-07-09 addendum.

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

### /experiment-audit

```
/experiment-audit design <experiment description>
/experiment-audit verdict <results path | issue #N | description>
```

Enforces experimental rigor for a **process experiment** — a comparative claim about a process change (A/B arms, before/after, challenger vs. incumbent). `design` mode, run *before any results exist*, produces a tiered pre-registration frozen in the tracking issue; `verdict` mode, run *before any conclusion*, gates it through Gates 0–6. The one law: *no conclusion without a design that could have produced the opposite conclusion* — an unfair or unregistered experiment yields only a hypothesis for a proper run, never adopt/reject. "Failure" is banned as a verdict word (verdict ∈ {supported, refuted, inconclusive — confounded by X}).

**How it works:** loads the project's `## Experiments` section (corpus, instruments + known-good/known-bad calibration references, results home), determines the tier (Tier 0 probe → a three-line issue declaration whose only legal endings are signal/infeasible/shelved; Tier 1 test → the full 10-field pre-registration with an incumbent-influence inventory, instrument calibration before any arm is scored, processing parity, and a frozen win bar + n/k), and in `verdict` mode runs the T1–T10 threat sweep (T10 = criterion substitution — deciding on a measurable proxy instead of the primary construct) and reads the verdict off the primary construct via the decision-legality matrix, recording the audit to `sessions/<project>/reports/`. Every emitted block opens with an `[experiment-audit]` marker — the signal `stop-experiment-verdict-gate.py` reads. The design half is enforced at plan time by Pass-3 dimension 7 (**Experimental validity**); the verdict half is backstopped by the Stop hook ([ADR-115](adr/115-experimental-rigor-protocol.md), [ADR-042](adr/042-plan-risk-dimension-audit-and-observability-section.md)). **Scope:** this skill governs *experiments* — comparative claims between arms, producing a verdict. A standing **gate** is Pass-3 dimension 8 (**Gate calibration**), which applies the same calibration law (known-good *and* known-bad references; uncalibrated ⇒ diagnostic, not blocking) and reuses this skill's Step D4 worksheet, but carries none of the tier / pre-registration / blinding / verdict machinery ([ADR-144](adr/144-gate-calibration-pass-3-dimension.md)).

---

### retro-chain-refill

**Invocation is indirect** — modeled on `sync-routine-worktree` as a reusable building block, this
skill is invoked from `biweekly-retro`'s Step 6.5 and from the standalone `retro-chain-backstop`
routine, not typically run directly by a user.

The shared mutating step for the retro-action chained-tile backlog burn-down mechanism
(dev-env#967): checks whether each configured repo's chain is alive and, when not, refills it.

**How it works:** runs `retro-chain-status.py` (Utilities below) for the caller-supplied repo list
and parses its per-repo classification (ALIVE / UNRESOLVED / NO_QUEUE_FOUND / QUEUE_EXHAUSTED /
ALL_TILED / AMBIGUOUS / NEEDS_REFILL). A NEEDS_REFILL repo is cross-checked against `list_sessions` (title/branch/cwd
match) before acting — a session already working it reclassifies as ALIVE rather than being
double-seeded. For each repo still NEEDS_REFILL: resolves the anchor issue, verifying any inline
`#NNN` reference already named in the queue item is OPEN and not a pull request before reusing it
(closes the `merickvaughn/lifting-logbook#814` failure mode — an already-merged PR cited as if it
were the live anchor) or filing a new `retro-action`-labeled issue otherwise; `spawn_task`s a tile
carrying the updated CHAIN block; and writes its shard with a `chain` field
(`{"queue_issue": "<url>", "seeded_by": "<caller-supplied label>"}`, see [Tile shards](#tile-shards-sessionsprojecttilesissue-numberjson))
before committing it directly to today's `draft/YYYY-MM-DD` branch in the engineering-journal
**canonical** checkout via `git -C` — never a dedicated worktree, the same disjoint-per-issue-file
exemption an ordinary stub/manifest/open-PR/tile shard write already gets.

**Owns the canonical 6-repo participant list** (career-playbook, dev-env, cover-letter-runtime,
win11-init-tools, gas-lifting-logbook, `merickvaughn/lifting-logbook`) — this is the one place that
list lives; deliberately not unified with `biweekly-retro`'s own, separately-maintained 8-repo
issue-routing list in its Step 6, since issue routing and chain participation are different
concerns.

**AMBIGUOUS is a deliberate, documented limitation, not a bug:** an untagged shard spawned on or
after the most recent chain-tagged shard's own `spawned` date (or the queue issue's `createdAt`
only when this project has never had a chain shard for this queue at all) blocks an automatic
refill and reports the repo for human review instead of guessing — over-flagging costs a few
seconds of review, while under-flagging risks a duplicate spawn. Anchoring to the chain's own last
movement, rather than the queue's (much earlier) creation date unconditionally, keeps the window
scoped to the period since the current gap actually opened instead of a queue issue's entire
~2-week life.

See [ADR-131](adr/131-retro-chain-idempotent-refill.md).

---


## Hooks

For a one-line-per-file index of the files directly in `claude/scripts/` — wired hooks, shared
`_foo.py` modules, and utility scripts, grouped by workflow domain — see
[`claude/scripts/README.md`](../claude/scripts/README.md), which carries the gated, authoritative
file count itself so this sentence can't drift out of sync with it by restating a second,
differently-scoped number (`docs/REFERENCE.md`'s prior count here covered `.py` files only,
while the README's covers `.py`/`.sh`/`.ps1` — two correct-for-their-own-definition numbers that
nonetheless read as contradictory side by side; dev-env#966 review finding)
([dev-env#830](https://github.com/brownm09/dev-env/issues/830)). The tables below remain the
authoritative per-hook behavioral description; that index is a navigational map only.

Most hooks are **advisory** — they emit `systemMessage` reminders but do not block tool execution. The exception is `pre-tool-use-worktree-path-check.py` (a `PreToolUse` hook), which exits 2 with a `{"reason": "..."}` payload to block `Write`, `Edit`, and `NotebookEdit` calls that target the canonical repo root instead of the active worktree, or that are issued from an orphaned worktree whose `.git` link no longer resolves (so git silently operates on the canonical repo).

Configuration is in `claude/settings.shared.json`. It is **not** symlinked: `~/.claude/settings.json`
is a real, machine-local file the Claude Code app writes (theme, `tui`, notification flags,
`autoMode`), and `_settings_sync.py` applies the tracked `hooks` and `permissions` blocks *into* it
on every prompt. Symlinking it — the pre-[ADR-139](adr/139-machine-local-settings-with-shared-source-sync.md)
arrangement — meant the app dirtied a tracked file and blocked the canonical's fast-forward
permanently, serving stale hooks and skills machine-wide (dev-env#1049). The owned / seed /
machine-local key split is in the [dev-env `CLAUDE.md`](../CLAUDE.md) architecture section.
See [ADR-007](adr/007-hook-command-invocation.md) for why hooks invoke scripts via `pyw -3` (the windowless variant of the Windows Python Launcher) rather than `python3` directly, wrapped in `bash -c`, or via `py -3` (which flashes a console window per spawn). Shell-invoked Python (the `## Testing` command, skill `py -3` examples, and the `pre-push` hook) continues to use `py -3`.

Any hook that spawns subprocesses (`git`, `gh`, `bash`, …) must `import _winsubp` near its imports — the helper patches `subprocess.Popen.__init__` to (1) set `CREATE_NO_WINDOW` so children don't flash a console window under `pythonw.exe`, and (2) default a text-mode call (`text=True` / `universal_newlines=True`) with no explicit `encoding=` to `encoding="utf-8", errors="replace"` rather than the Windows cp1252 default, which crashed `post-tool-use.py` reading `gh project item-add`'s output (dev-env#503). The static check in `claude/scripts/tests/test_pyw_stdio.py` fails the build if a subprocess-using hook ships without it. See ADR-007's 2026-06-01 and 2026-07-02 follow-up sections.

Any PostToolUse Bash hook that reads command output must use `read_command_output` from `claude/scripts/_hookio.py` rather than `tool_response["output"]`: Claude Code's payload carries output under `stdout`/`stderr`, so the legacy `output` read is always empty and silently disables the hook. See [ADR-049](adr/049-hook-payload-output-field.md) (root cause) and [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) (shared helper + sibling-hook fixes).

The worktree-maintenance scripts (`prune-merged-worktrees.py`, `reclaim-worktree-disk.py`) call `worktree_session_is_live` from `claude/scripts/_worktree_liveness.py` to skip a worktree with a live Claude session — it reads the worktree's transcript-dir mtime under `~/.claude/projects/`, the only signal by which an out-of-process routine can avoid severing an active session in another worktree. Windows are blast-radius-scaled (prune 24h, reclaim 6h) and override-able with `--liveness-window-min`. See [ADR-051](adr/051-worktree-liveness-guard.md).

The journal open-PR hooks (`reconcile-open-prs.py`, which `unlink`s merged/closed shards, and `post-compact.py`, which reads them to prompt a `/review`) enumerate the per-PR shards `sessions/<project>/open-prs/<N>.json` and the legacy `open-prs.jsonl` through one shared reader, `claude/scripts/_journal_shards.py` — `iter_pr_shards` returns `(path, entry)` pairs (numerically sorted; non-numeric-named, unparseable, and non-object shards skipped) and `read_legacy_entries` drains the legacy file. Centralising the read keeps the two hooks from drifting on the shard semantics and gives the legacy format a single retirement point (pairs with the engineering-journal#128 data migration). See [ADR-057](adr/057-shared-journal-shard-reader.md). [ADR-118](adr/118-tile-persistence-shards.md) added tile shards (`sessions/<project>/tiles/<issue-number>.json`) on the identical numeric layout, so that enumeration now lives in `iter_numeric_shards` with `iter_pr_shards` and `iter_tile_shards` as named delegations to it (and `shard_pr_number` as an alias of a generic `shard_number`) — adding the second shard kind by *copying* the reader would have recreated the very drift ADR-057 was extracted to end. The module also owns `project_dirs` — the walk over every `sessions/<project>/` directory that both reconcile hooks run *before* reading either shard kind. It lived as a per-hook copy until dev-env#881: the tile reader duplicated `reconcile-open-prs.py`'s helper rather than conflict with an in-flight PR, and that third copy (plus the docstring note recording the deferral) was folded back in here. Each hook's test now carries a one-line identity pin asserting it still resolves to this copy, so a future re-duplication fails a test rather than waiting to drift.

The manifest, open-PR, and tile shard **schemas** — as opposed to the shard *enumeration* above — live in `claude/scripts/_journal_schema.py`, shared between the compose-time gate `validate-manifest.py` and the write-time `journal-shard-write-advisory.py` PostToolUse hook so the required-field lists and BOM-decoding logic are defined once. It exposes `REQUIRED_FIELDS` / `OPEN_PR_REQUIRED_FIELDS` / `TILE_REQUIRED_FIELDS`, `missing_required_fields()` / `missing_open_pr_fields()` / `missing_tile_fields()`, `find_entries_missing_fields()`, `parse_manifest_text()`, and `decode_shard_bytes()` (names a UTF-8/UTF-16 BOM rather than letting it surface as an opaque JSON parse failure on line 1). See [ADR-081](adr/081-write-time-journal-shard-validation-hook.md) and [ADR-118](adr/118-tile-persistence-shards.md).

Per-session sentinel helpers, transcript-locate, and the transcript-record readers are extracted into `claude/scripts/_hookutil.py` (Stop / UserPromptSubmit hook family — the analogue of `_hookio.py` for the PostToolUse family). It exposes `cleanup_stale_sentinels(prefix)`, `sentinel_path(prefix, session_id) -> Path`, `find_transcript(session_id) -> Path | None`, and the transcript-record readers `load_records` / `_parse_records` / `iter_bash_calls` (pairs Bash tool_use/tool_result by id, returning `(command, output, cwd)`) / `_result_text` / `_content_items`, plus the user-text pair `_user_message_texts` / `_is_synthetic_user` and `first_user_prompt_text(records) -> str` ([ADR-140](adr/140-unchained-merge-workstream-gate.md), dev-env#1044 — the first genuine (non-synthetic, non-blank) user prompt, composing that pair; the opening-prompt scope test `stop-tile-enumeration-gate.py`'s unchained-merge trigger reads, and the first hook-facing reason anything needed the session's opening prompt) — used by `posttooluse-inert-advisory.py`, `stop-tile-enumeration-gate.py`, `reconcile-open-prs.py`, and `token-tracker.py` so each no longer carries its own `SCRATCH` / `PROJECTS` constants and local copies of these helpers. The `scratch` / `projects` parameters are injectable for offline testing; `stop-tile-enumeration-gate.py` consumes `iter_bash_calls` through a thin 2-tuple adapter that drops `cwd` (it never needs it). It also exposes `iter_records_reverse(transcript_path, chunk_size=DEFAULT_REVERSE_CHUNK_SIZE) -> Iterator[dict]` (dev-env#679, [ADR-090](adr/090-shared-transcript-readers-hookutil.md) Amendment 1) — reads the file from the end in bounded chunks, yielding JSON-object records most-recent-first, so a caller that only needs a small piece of tail state (`idle-refresher.py`'s last-assistant-record timestamp) can stop consuming the generator instead of paying `load_records`'s full parse; `load_records` itself is unchanged, kept for callers needing the whole transcript. See [ADR-064](adr/064-shared-hookutil-sentinel-transcript-locate.md) (sentinels / transcript-locate) and [ADR-090](adr/090-shared-transcript-readers-hookutil.md) (transcript-record readers).

`_hookutil.py` also exposes `record_heartbeat(hook_name, heartbeat_dir=None) -> None` ([ADR-106](adr/106-hook-heartbeat-liveness-ledger.md)) — writes `~/.claude/scratch/hook-heartbeat/<hook_name>.ts` (the current Unix timestamp) via a per-process tmp file + `os.replace` atomic swap, best-effort (swallows all I/O errors). A rare `os.replace` failure (e.g. a Windows sharing violation while `hook-liveness-check.py` is mid-read of the target) can orphan the `<hook_name>.ts.<pid>.tmp` in that directory; `hook-liveness-check.py` reaps such `.tmp` orphans older than 30 days on each run ([dev-env#802](https://github.com/brownm09/dev-env/issues/802)). Called as the unconditional **first statement of `main()`** — before any other logic, including the hook's own stdin read — by **every** currently-wired hook script (every one in the tables below, `hook-liveness-check.py` included), so a heartbeat is recorded on every real invocation regardless of what the rest of the hook does or whether it raises, but never on a bare `import` of the module (which every hook's own test file does to unit-test its pure helpers offline). `hook-liveness-check.py` is the reader side — see its `UserPromptSubmit` table entry below.

The hook advisory/block emitter is `claude/scripts/_hookout.py` — the analogue of `_hookio.py`/`_hookutil.py` for hook *output*, encoding Claude Code's per-event channel table once. It exposes `emit_advisory(event, text, *, audience="model"|"user"|"both", blocking=False)`, `emit_block(text)`, `ascii_sanitize(text)`, and the pure routing core `plan_emission(...) -> Emission`. The contract it encodes: plain stdout on exit 0 is model-visible only on the context events (`STDOUT_MODEL_VISIBLE_EVENTS` = UserPromptSubmit / SessionStart / UserPromptExpansion), exit-2 stderr reaches the model on any event, and `{"systemMessage"}` JSON on exit 0 reaches the user on any event — so a non-blocking, model-visible advisory is impossible on PreToolUse/PostToolUse/Stop, where `plan_emission` raises `ValueError` (naming `blocking=True` or `audience="user"`) rather than emitting into the void. JSON channels use `json.dumps(ensure_ascii=True)` for cp1252 wire-safety; raw exit-2 stderr text is `ascii_sanitize`-d (`.isascii()`-guaranteed). Generalizes the per-site channel fixes ADR-091/098/099/100 (PRs #701/#705) each made one hook at a time; additive in the introducing PR (no hook imports it yet — the enforcement gates and per-site migrations follow in later PRs of [dev-env#717](https://github.com/brownm09/dev-env/issues/717)). See [ADR-103](adr/103-shared-hookout-emitter.md).

The `gh`-command target resolver is `claude/scripts/_repo_target.py` — one pure module for extracting the `owner/repo` (and PR/issue number) a `gh pr merge`/`gh pr create`/`gh issue create`/`gh issue close` command targets, converged from five hooks that had drifted into three distinct `--repo`/`-R` regex shapes (a sixth, `post-tool-use.py`, joined in dev-env#838). It exposes `repo_from_flag(text)` (the `--repo`/`-R` flag in both `=` and space forms, strict GitHub slug — also normalizing a full-URL / `github.com/` host-prefixed value down to the bare `owner/repo`, dev-env#838 — standalone-token lookbehind, masking `mask_quoted_spans` internally), `merge_args(command)`/`create_args(command)`/`issue_create_args(command)` (the quote-aware statement-bounded argument region, so a chained sibling command's flag can't leak in), `repo_from_pr_url`/`pr_number_from_pr_url`/`iter_pr_urls` and `issue_number_from_issue_url`/`iter_issue_urls` (URL parsing — the caller decides masking, since a bare quoted URL must stay matchable where a prose-flag decoy must not), and `positional_number(text)` (the bare positional integer, decoy-safe). Also exposes `repo_from_rest_merge_path(command)` / `pr_number_from_rest_merge_path(command)` (dev-env#986, ADR-050 Amendment 23) — the `repos/<owner>/<repo>/pulls/<N>/merge` REST path of the two-step merge fallback used when `gh pr merge` itself is unavailable (e.g. a GitHub GraphQL rate-limit outage); the response body carries no PR number, so the command's own path is the sole source. Consumed by `post-pr-merge-project.py`, `pr-merge-reminder.py`, `posttooluse-inert-advisory.py`, `post-pr-merge-pull.py`, `stop-tile-enumeration-gate.py`, and `post-tool-use.py` (its `extract_repo_flag` cross-repo sibling-config lookup, dev-env#838); cd-chain/`-C` resolution stays in `_hookio.effective_merge_dir` (ADR-067), reused not re-implemented. This ends the per-site ADR-050 amendment treadmill (Amendments 14/15/17/18/19/20/21) for the repo-flag/URL/number concern. See [ADR-111](adr/111-shared-repo-target-resolution.md).

`_hookio.py` (see the ADR-050 rationale above) also exposes `is_rest_merge_command(command)` / `output_has_rest_merge_marker(output)` (dev-env#986, ADR-050 Amendment 23) — the command-shape (`gh api` targeting a `.../pulls/<N>/merge` path) and output-marker (`"merged":true`) halves of the same REST-merge-fallback recognition, mirroring `stop-tile-enumeration-gate.py`'s own pre-existing detection of this shape for session-merged-PR enumeration. Consumed by all five PostToolUse merge-consequence hooks (`usage-snapshot.py`, `post-pr-merge-project.py`, `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, `post-merge-tile-checkpoint.py`), each extending its own existing success predicate with an OR clause rather than sharing a new cross-file combinator — consistent with this file's existing per-caller `_check_merge_stmt` duplication.

The orphaned-worktree recovery recipe is `claude/scripts/_worktree_recovery.py` — one pure, ASCII-only definition of *how to un-orphan a worktree*, exposed as `RECOVERY_STEPS` (ordered `RecoveryStep(command, note)` pairs using `<canonical>` / `<orphan>` / `<branch>` placeholders), `recovery_commands(orphan_root, canonical_root)`, and `recovery_recipe(orphan_root, canonical_root)` (the formatted block a blocking hook embeds in its reason). Consumed by `pre-tool-use-worktree-path-check.py`'s orphan block message, and pinned against the [Worktree deregistration recovery](#worktree-deregistration-recovery-lost-git-link-routes-git-to-main) runbook by `tests/test_worktree_recovery.py` — which also fails the build if the dev-env#751-disproven `worktree add --force` form reappears in any emittable string literal (comments and docstrings explaining *why* it fails are exempt; `docs/adr/` is exempt as a historical record). It exists because those two surfaces were hand-maintained copies that drifted: dev-env#751 corrected the runbook and left the hook message alone, so the disproven recipe stayed on the one surface a blocked session actually reads for another six weeks (dev-env#862). The current sequence — `worktree repair` first (non-destructive, preserves uncommitted work, and exits 1 whether or not it succeeded, which is why the `rev-parse` verification is a separate mandatory step), then `prune` → plain `add`, then a salvage copy and empty-in-place *only* if `add` reports `already exists` — was re-derived from a throwaway-fixture matrix rather than inherited. The destructive step is rendered outside the numbered sequence and flagged `destructive`/`conditional` in the data, so a reader working top-to-bottom never reaches it by default. See [ADR-116](adr/116-single-source-worktree-recovery-recipe.md).

#### Machine-local permissions

The `permissions.allow` block in `claude/settings.shared.json` contains paths with a hardcoded Windows username (`C:/Users/brown/...`). These rules are functionally correct on this machine but must be updated manually when bootstrapping dev-env on a new machine or account. If scratch-dir writes or edits start prompting for permission after a re-bootstrap, update the username in every `allow` entry.

**Known scope decisions:**

| Entry | Scope | Rationale |
|---|---|---|
| `Edit(C:/Users/brown/Git/**)` | All files in all local repos | Covers skill and config edits across career-playbook, lifting-logbook, dev-env, etc. without per-file prompts. Intentionally broad — includes `.env` and credential files — accepted tradeoff on a single-user personal machine. |

---

### SessionStart

Registered with `"matcher": "startup|resume"` — the two sources where "is this checkout
stale" is a meaningful question. Deliberately does **not** match `clear`/`compact`: `/compact`
is a routine mid-session operation (triggered by context growth, not elapsed time), and
synchronously blocking it on a remote `git fetch` was found disproportionate to the staleness
risk, and widened the window for the concurrency check below to false-match this same
session's own just-written transcript (dev-env#966 review finding).

| Script | What it does |
|--------|-------------|
| `session-start-sync.py` | Generalizes `dev-env-sync.py`'s fetch -> compare -> fast-forward mechanic to any repo a session starts in, resolved dynamically from the session's own `cwd` rather than a hardcoded path. Resolves the repo root (`git rev-parse --show-toplevel`) and its **canonical** root (`_worktree_canon.canonical_repo_root`, so a worktree session's opt-out and the dev-env exclusion both resolve correctly even though `.claude/` is commonly gitignored per-worktree); skips every dev-env checkout — canonical or worktree — (already covered more thoroughly by `dev-env-sync.py`'s topology auto-correction + persistent-failure escalation) and any repo whose canonical `.claude/hook-config.json` sets `"session_start_sync_disabled": true`; then `git fetch origin --quiet` (all remote-tracking branches, not just the default — also fixes a wrong-branch-pull-scattered-files class of incident, not only default-branch staleness). Classifies the checkout as canonical/sole vs. a linked worktree (compares resolved path *value* against `canonical_worktree()`'s own path — not the `find_worktree_by_path` identity shortcut that module's own docstring disclaims as caller-reliable), resolves the repo's actual default branch (`git symbolic-ref --short refs/remotes/origin/HEAD`, stripping the `origin/` prefix that `--short` retains — verified live that this command's output is `origin/main`, not `main` — falling back to `"main"` when unset), and compares HEAD against its own upstream if tracked, else `origin/<default-branch>` (the fallback is what covers a detached HEAD, which has no upstream by definition; a not-yet-pushed local branch gets a correspondingly softer advisory wording, since that case is expected rebase distance, not staleness). Already up to date, or strictly ahead, -> silent exit (don't spam the healthy path); an unmeasurable drift count (a `git rev-list` failure) warns rather than exiting silently, since a failed measurement must never read as "confirmed up to date." Otherwise **auto-fast-forwards** (`git merge --ff-only <the same ref the comparison measured>` — not a separately hardcoded `origin/<default-branch>`, which could silently diverge from what was actually measured whenever the branch's real upstream isn't literally that ref) only when the checkout is canonical/sole, on exactly its own default branch, a true fast-forward (zero local-only commits, where an unmeasurable count is treated the same as a positive one), a clean *tracked* working tree (untracked files never block a fast-forward and are not counted as dirty), and no other session's transcript — checked against both the repo root and the session's own `cwd`, and against nested subagent transcripts too, not just top-level ones — active in the last 5 minutes (`_worktree_liveness.worktree_session_is_live`, extended with an `exclude_session_id` parameter so this hook does not see itself, or its own subagents, as "live"; skipped entirely when no `session_id` is available, defaulting to "no concurrency detected" rather than letting a parsing failure silently disable auto-fix). A successful merge re-reads `HEAD` afterward and reports the actual result, flagging a mismatch against the pre-merge measurement explicitly (a concurrent-process race) rather than reporting the pre-merge value as confirmed. Any single ineligible condition instead emits a **loud advisory** naming the repo, branch, how far behind, and precisely why it was not auto-fixed. Every one of the ~12 git subprocess calls in one firing shares a single time budget (a `time.monotonic()` deadline computed once, under this hook's own `claude/settings.shared.json` timeout) rather than each independently claiming up to 15s, and a resolved ref is validated against a conservative name pattern before being used as a command argument (defense in depth — a leading-dash ref is format-valid to git and would otherwise be parsed as an option). All advisories go through `_hookout.emit_advisory("SessionStart", ..., audience="both")` — `SessionStart` is one of the three events whose exit-0 stdout is model-visible (`STDOUT_MODEL_VISIBLE_EVENTS`), and `audience="both"` also surfaces a `systemMessage` toast to the user. Fails open unconditionally on every subprocess failure (not a git repo, a failed fetch, a failed rev-parse, an exhausted time budget) — this is a drift *detector*, not a gate. The first hook in this repo to use the `SessionStart` event: every prior "fires early in a session" hook (`dev-env-sync.py`, `reconcile-open-prs.py`, `reconcile-pending-tiles.py`, `journal-onboard-check.py`) instead rides `UserPromptSubmit` with a once-per-session sentinel — `SessionStart` was chosen deliberately for the semantic fit and because it also fires natively on `resume`, not only a brand-new session. [ADR-130](adr/130-session-start-fetch-ff-only-or-warn.md) |

---

### UserPromptSubmit

Fires on every user prompt, before Claude processes it.

| Script | What it does |
|--------|-------------|
| `session-mode-prompt.py` | Fires on the first user prompt of each new session. Emits a one-time mode-confirmation reminder (plan / bypass / auto) to Claude as `hookSpecificOutput.additionalContext` JSON on **stdout** and exits 0 — per the hook contract, this delivers the reminder alongside the prompt without erasing it, and Claude surfaces a mode-confirmation line in its first response. A per-session marker file at `scratch/session_mode_ack_<session_id>.txt` records that the reminder has been injected for this session; subsequent prompts in the same session pass through silently. Cross-session contamination is impossible — session A's marker never affects session B. Markers older than 30 days are swept via `_hookutil.cleanup_stale_sentinels` on every invocation (dev-env#768 — previously never cleaned up). Suppressed for automated sessions whose prompt begins with an XML tag (e.g. `<scheduled-task>`, `<ci-monitor-event>`). When `permission_mode` carries the `bypass` stem (matched case-insensitively on the stem, so a contract rename to a sibling spelling still lands the note), the injected context additionally carries the **content-authoring carve-out**: that mode's standing harness instruction to make file changes with `sed`, heredocs, or short scripts governs shell *work*, not authoring file *content*, for which `claude/CLAUDE.md` → Authoring File Content wins. The harness prompt is not editable from this repo, so the tiebreaker is delivered into the same context, in the same sessions — measured live from this hook's own log, bypass is 79% of all sessions (4579 of 5824), so the contradiction it resolves is present in four sessions out of five. Non-bypass modes get the base reminder unchanged. Diagnostic JSON log at `scratch/session-mode-prompt.log`. [ADR-027](adr/027-userpromptsubmit-blocking-hook-conventions.md) (see 2026-05-27 amendment), [ADR-138](adr/138-shell-content-write-guard.md) |
| `dev-env-sync.py` | Fast-forward pulls the dev-env repo to `origin/main` so symlinked tooling stays current. When the canonical worktree is off `main` — including a **detached HEAD**, routed into the same path via `resolve_current_branch()` since dev-env#619 (previously `git symbolic-ref` failing on detached HEAD caused a silent early exit before this diagnosis ever ran) — diagnoses the worktree-on-`main` topology (`_worktree_topology.py`) and either **auto-returns a clean canonical to `main`** (then continues the pull), **warns naming a non-canonical worktree squatting `main`** plus the `git -C <wt> checkout -b claude/<slug>` park command that frees the ref, or **warns without switching when the canonical is dirty** (preserving drift). **All advisories print to stdout, never stderr** (dev-env#694, [ADR-098](adr/098-dev-env-sync-advisories-to-stdout.md)): a `UserPromptSubmit` hook's exit-0 stderr is not added to Claude's context, only stdout is — a prior stderr-routed fast-forward-failure warning (e.g. a dirty working-tree file conflicting with an incoming commit) went unnoticed for 36+ hours and 21+ commits of drift before this fix. The fast-forward-related messages (pulled / diverged / pull-failed) also state local/remote short SHAs and the commit-behind count, computed once up front, so a future occurrence is self-diagnosing without a manual `git log`/`git fetch` comparison; the success message additionally flags a pre/post commit-count mismatch as a likely concurrent-process race against the same shared canonical checkout. A fast-forward-pull failure that **persists across prompts/sessions** — the recurring dirty-tracked-file-conflicting-with-an-incoming-commit case (dev-env#697, #795) — is tracked in a single repo-level scratch state file (`~/.claude/scratch/dev_env_sync_ff_failure.json`; `first_failure_at` + `consecutive_count`, cleared on the next up-to-date/successful pull, `_hookutil`-swept after 30 days) and, once it has recurred on ≥3 consecutive prompts **or** persisted ≥2h, **escalates** to a distinct, louder advisory naming the commits-behind count, the blocking file path(s) parsed from git's stderr, and how long it has been failing — so a stuck canonical can't silently fall many commits behind (leaving every merged fix inert, since `~/.claude/` is junctioned to this working tree) the way it did twice before. Escalation stays on stdout at exit 0 (the same channel; no blocking), and the state file is repo-level, not per-session, so the persistence detection survives across sessions (the whole point — a per-session file would reset every session). **Before any of the git work**, applies `claude/settings.shared.json` into the real, machine-local `~/.claude/settings.json` via `_settings_sync.py` — owned keys (`hooks`, `permissions`) replaced wholesale, seed keys (`model`, `effortLevel`) written only if absent, every app-written key (`theme`, `tui`, `autoMode`, notification flags) left alone — and performs the one-time symlink->real-file migration. **The ordering is load-bearing in both directions:** the pull is what removes `claude/settings.json` from the working tree, and a still-symlinked live file would then resolve to nothing, so every hook — *including this one* — would stop firing with nothing able to self-heal; and until the live file stopped being the tracked file, the app's own writes dirtied it and the fast-forward below could never succeed, which is the failure this whole arrangement exists to end (14 consecutive failed pulls over 6h 20m on 2026-08-25, serving the pre-ADR-138 `/review` skill machine-wide). Settings-sync failures are reported to stdout and never block the prompt or the pull. Exit 0 always (unhandled exceptions are caught at the `__main__` guard); the worktree enumeration runs only on the rare off-main path. [ADR-006](adr/006-dev-env-sync-on-every-prompt.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md) (2026-07-09 amendment), [ADR-098](adr/098-dev-env-sync-advisories-to-stdout.md), [ADR-110](adr/110-escalate-persistent-dev-env-sync-ff-failures.md) |
| `journal-canonical-guard.py` | Corrects the engineering-journal canonical checkout (`C:/Users/brown/Git/engineering-journal`) when it's sitting on the dev-env#630 hijack signature — detached HEAD, or a stray `claude/<slug>` branch belonging to no live worktree (`is_hijacked_branch()` in `_worktree_topology.py`) — caused by a scheduled-task worktree-provisioning defect reproduced 2 mornings running. Unlike `dev-env-sync.py`, does **not** treat "off `main`" as broken: engineering-journal's canonical is legitimately on `draft/YYYY-MM-DD` for most of every working day, so only the hijack signature specifically triggers correction. Re-checks the hijack condition against a fresh `git worktree list` read immediately before acting (a concurrent stub-writing session may have already fixed it between the cheap first read and the more expensive second one — TOCTOU). Reuses `diagnose_main_topology()`/`canonical_sync_action()` unchanged for the same **auto-return / warn-squatter / warn-dirty** decision `dev-env-sync.py` makes; non-destructive (`git checkout main` never deletes the hijacked branch). Test-seam: `JOURNAL_CANONICAL_GUARD_REPO_PATH` env var overrides the target repo path — resolution now shared with the other three engineering-journal carve-out hooks via `claude/scripts/_journal_canon.py` ([ADR-133](adr/133-shared-journal-canon-module.md)). **All advisories print to stdout, never stderr** (dev-env#699, [ADR-099](adr/099-journal-canonical-guard-advisories-to-stdout.md)): a `UserPromptSubmit` hook's exit-0 stderr is not added to Claude's context, only stdout is — the identical defect ADR-098 fixed in the sibling `dev-env-sync.py`. Exit 0 always. [ADR-093](adr/093-journal-canonical-hijack-guard.md), [ADR-099](adr/099-journal-canonical-guard-advisories-to-stdout.md) |
| `new-day-journal-check.py` | Five checks against `origin/engineering-journal`: stale draft artifacts, draft branches with no composed journal on `main`, resurrected draft branches, (ADR-119) **day rollover** — the canonical resting on a `draft/<D>` branch dated other than today, plus any stubs on it dated *after* `D` — and (ADR-119 Amendment 1, dev-env#911) **stale-canonical self-healing**: when that same non-today `draft/<D>` is ALSO fully clean (whole working tree, not just `sessions/`) and HEAD hasn't moved — a checkout OR a commit, whichever is more recent, read from HEAD's own reflog rather than the branch's tip-commit time — in ≥`STALE_CANONICAL_IDLE_MINUTES` (15) minutes, the hook performs a real `git checkout main` — the one check here that mutates rather than only advises, bounding how long two concurrent sessions colliding on the canonical's shared HEAD (no coordination lock exists) can leave it silently stranded. Anchoring idle time on the branch's own last commit (rather than HEAD's reflog) was an earlier draft's bug, caught in review: a stale branch's last real commit is, by construction, already old, so a session freshly checking it out for legitimate work (e.g. an ADR-119 decision-3 shard-deletion hop) would find idle time already past threshold at the instant of checkout, with none of the intended headroom. A dirty tree is never auto-touched under any idle time, and a final re-read of branch/dirty state immediately precedes the checkout to narrow the TOCTOU window (mirrors `journal-canonical-guard.py`'s identical precaution); a failed checkout re-checks whether a concurrent process already resolved it before reporting a manual-fix advisory. Emits a warning if any check fires; continues silently otherwise. Checks 1-3 are suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd); checks 4 and 5 are **not** — worktree sessions write stubs into the canonical via `git -C` too, so they are exactly who needs the warning (and benefits from the auto-recovery). Checks 4 and 5 share one sentinel and checks 1-3 share another, each re-arming after `RECHECK_MINUTES`, so a rollover (or stranding) beginning mid-session is still caught without respawning git on every prompt. |
| `journal-onboard-check.py` | Checks whether the active git repo has a `sessions/<repo-name>/` directory in engineering-journal. Emits a one-line advisory and `/journal-onboard` hint if not. Fires once per session, gated by a `scratch/journal_onboard_<session_id>.flag` sentinel (`_hookutil.sentinel_path`) swept after 30 days via `_hookutil.cleanup_stale_sentinels` (dev-env#768 — previously hand-rolled and never cleaned up; this prefix alone accounted for 986 files at the 2026-07-10 assessment). |
| `turn-count-hook.py` | Warns when session context accumulates past a threshold. Primary signal: token count; secondary: turn count. Configurable via `"turn_threshold"` in `.claude/hook-config.json` (default: 50). |
| `idle-refresher.py` | On the user's return after an idle gap exceeding the threshold (default 60 min), injects an `additionalContext` cue telling Claude to open its reply with a refresher (what we were working on, current state, pending to-dos/tiles) before addressing the new prompt. Anchors the gap on the last assistant turn's timestamp in the transcript (immune to the just-submitted prompt being appended); skips automated/XML-prefixed prompts and the first prompt of a session; stateless and fail-open (exit 0, ASCII-only cue). Configurable via `"idle_refresher_minutes"` in `.claude/hook-config.json` ([ADR-095](adr/095-session-boundary-summaries-and-idle-refresher.md)). |
| `multi-worktree-alert.py` | When ≥2 git worktrees are active, emits a list in `repo:branch` format, starring the current one. Fires on every prompt. Suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd). |
| `reconcile-open-prs.py` | Runs once per session (per-session sentinel in `scratch/`). Resolves the state of each tracked PR across every project in engineering-journal — both the per-PR shards `sessions/<project>/open-prs/<N>.json` ([ADR-056](adr/056-per-session-sharding-journal-companion-files.md)) and the legacy `sessions/<project>/open-prs.jsonl`. State comes from **one REST `core` read per PR** (`gh api repos/<owner>/<repo>/pulls/<n>`), never GraphQL — `gh pr view --json state` is a GraphQL call, and this repo's repeated measured exhaustion of that bucket (dev-env#769/#773, PR #872, `graphql 0/5000` while `core` sat at `4999/5000`) used to stop all pruning outright, since an unresolved state is conservatively *kept*. REST-only, superseding ADR-119's GraphQL-then-REST fallback: the contended bucket (shared with the GraphQL-only Projects v2 operations, dev-env#769) is never touched rather than merely retried past, a hanging lookup costs one timeout instead of two, and the REST parsing is the exercised path rather than a rarely-reached one (dev-env#888, [ADR-018 Amendment 1](adr/018-reconcile-open-prs-hook.md)). Deliberately **not** batched, unlike its `reconcile-pending-tiles.py` sibling: a pending tile's issue is recent by construction, whereas a lingering open-PR shard is exactly what this hook exists to prune, so a bounded paged walk misses the oldest and most stale one (measured: a live shard tracked a PR merged 2026-05-05, while page 2 of `GET /pulls?state=all` bottomed out at #406). Two REST rules live in the pure, unit-tested `pr_state_from_row` rather than in the untested boundary or the `--jq` projection: `state` is upper-cased (REST answers lowercase and `should_remove` is deliberately case-sensitive — skipping this leaves the hook *inert* rather than fixed), and **MERGED is recovered from the separate `merged`/`merged_at` signal**, since REST has no `MERGED` state (collapsing it would not change what is pruned — which is why it would go unnoticed). A `WorkBudget` gates the start of every `gh`/`git` lookup so N sequential ones cannot exhaust the 30s `settings.json` timeout and get the hook killed before it prints anything (the hook's ungated local work is an explicit term in that budget, not the leftover), and any survivor it could not confirm — a `gh` failure or a spent budget, both of which conservatively *keep* — is disclosed as an explicit unresolved count, so the `Open PRs:` line never reports a partial reconciliation as a clean one. A MERGED/CLOSED shard is unlinked individually (no survivor rewrite; empty `open-prs/` dirs are removed); legacy entries are dropped via a safe read-filter-write. Emits a `systemMessage` listing surviving open PRs and any removals. Does not commit — the unlink stays load-bearing for `post-compact.py`'s same-checkout disk read, but nothing sweeps the deletion into a commit today ([ADR-018](adr/018-reconcile-open-prs-hook.md) + [ADR-056](adr/056-per-session-sharding-journal-companion-files.md) + [ADR-082](adr/082-journal-compose-worktree-isolation.md) together closed that path); a scoped `git status --porcelain` scan surfaces any currently-uncommitted `sessions/*/open-prs*` path in the same `systemMessage` instead ([ADR-082 Addendum](adr/082-journal-compose-worktree-isolation.md), dev-env#578). Those paths are **classified**, never emitted as one list ([ADR-119](adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md)): only an exact ` D`/`D ` porcelain code counts as a deletion (a `D` elsewhere in the two-char field means `AD`/`RD` — a concurrent session's *staged* shard — or the unmerged `DD`/`DU`/`UD`), and a deletion is recommended for commit only once `gh` confirms its PR MERGED/CLOSED **and** the shard's embedded `pr` matches its filename stem. Buckets: `merged` (ready-to-run, shell-quoted, shape-validated pathspec), `open` (anomaly), `unverified`, `skipped` (past the probe count/deadline budget). PR state falls back from GraphQL (`gh pr view`) to REST (`gh api`), which is on a separate rate-limit budget. The whole deletion advisory is suppressed while the canonical is mid-merge. Fails safe: `gh` errors leave the entry intact. [ADR-018](adr/018-reconcile-open-prs-hook.md), [ADR-056](adr/056-per-session-sharding-journal-companion-files.md) |
| `reconcile-pending-tiles.py` | Runs once per session (per-session sentinel in `scratch/`, matching `reconcile-open-prs.py`). Walks every project's `sessions/<project>/tiles/<issue-number>.json` shards ([ADR-118](adr/118-tile-persistence-shards.md)) via the shared `_journal_shards.iter_tile_shards`, resolves each paired issue's state with **one paged REST `GET /repos/<owner>/<repo>/issues` read per distinct repo** (not per shard — shards accumulate un-pruned between reconciliations, so a per-shard lookup would fan out into N sequential subprocess spawns), and unlinks the shard of any issue confirmed `CLOSED` (removing the `tiles/` dir once its last shard is gone). `url` is validated (`repo_from_issue_url`: `https` scheme, exact `github.com` host case-insensitively, a conservative owner/repo character class, rejection of `.`/`..` segments) before it can reach `gh --repo`, because — unlike its `reconcile-open-prs.py` model, which does a bare unchecked URL split — this reconciler's remove branch destroys the shard on a mis-resolved `CLOSED`; the issue number always comes from the **filename**, never `url`, and a shard whose embedded `issue` field disagrees with its filename is treated as corrupt and kept, never unlinked. Emits its index as `additionalContext` via `_hookout.emit_advisory("UserPromptSubmit", ..., audience="model")` — **not** the `systemMessage` its sibling uses, since the pending-tile index is for Claude to act on, not a user-facing toast (ADR-098). Since [dev-env#958](https://github.com/brownm09/dev-env/issues/958)/[dev-env#950](https://github.com/brownm09/dev-env/issues/950) ([ADR-118](adr/118-tile-persistence-shards.md) Amendment 5), the same pass also detects an **already-deleted** tile shard sitting uncommitted in the canonical (`git status --porcelain -- sessions`, exact ` D`/`D ` porcelain codes only — never a `"D" in status` substring test), recovers `{issue, url}` from `git show HEAD:<path>` (the file is gone from the working tree), and — deliberately **not** folded into the batched per-repo fetch above — re-confirms its state with **one single-issue REST call per candidate** (`gh api repos/<repo>/issues/<n>`, reusing the same `issue_states_from_rows` parser the batched path uses), because an orphaned deletion's issue has no "recent by construction" guarantee the way a pending tile's does: while a deletion sits uncommitted the repo keeps creating issues, pushing an already-resolved one deeper into the batch's recency window until it silently falls outside it. Bounded by `MAX_TILE_DELETION_PROBES`/`TILE_PROBE_DEADLINE_SECONDS` once probing starts; the whole deletion-advisory pass (the `git status` scan included) additionally runs only when `deletion_advisory_time_remains` finds enough slack left against `HOOK_TIMEOUT_SECONDS`, given how long the primary lookup already took — the primary loop's own realistic worst case (`LOOKUP_BUDGET_SECONDS + MAX_ISSUE_PAGES * GH_CALL_TIMEOUT`) can already exceed `HOOK_TIMEOUT_SECONDS` on its own, so there is no fixed slack to assume and "borrow" from; this pass reacts to actual elapsed time instead (a correction made during `/review`, which caught an earlier draft's wrong claim that `LOOKUP_BUDGET_SECONDS + GH_CALL_TIMEOUT` equaled `HOOK_TIMEOUT_SECONDS` exactly). Classified into four buckets mirroring [ADR-119](adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md) §3 (renamed for tiles' single terminal state): `closed` (issue confirmed `CLOSED` — a ready-to-run, shell-safety-validated `git add`/`git commit` pathspec plus the canonical's current branch), `open` (anomaly — a still-open issue's shard was deleted — flagged, never recommended), `unverified` (identity or state unconfirmable, or a filename/`issue` mismatch — never committed blind; a *missing* embedded `issue` field is not itself treated as a mismatch), and `skipped` (beyond the probe budget, reported not dropped). A dirty tile path that is *added or modified* (a concurrent session's in-flight shard) is reported separately, hands-off — including while mid-merge, since that report needs no git mutation. The whole deletion-*classification* pass is suppressed outright while the canonical is mid-merge. Fails safe throughout: an exception anywhere in the deletion-advisory logic never suppresses the pre-existing pending-tile index, which is built first. [ADR-118](adr/118-tile-persistence-shards.md), [ADR-119](adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md) |
| `disk-space-check.py` | Free-space safety net for `C:`. Checks `shutil.disk_usage` on every prompt **and** — since dev-env#592/[ADR-087](adr/087-pretooluse-disk-space-check.md) — before every Bash call (also registered under `PreToolUse(Bash)`, closing the gap where a long tool-call-only stretch could outrun the once-per-prompt check). Below 20 GB free: emits a one-time `systemMessage` warning. Below 10 GB free: spawns `reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --min-free-gb 10 --protect-cwd <cwd>` **detached** (via `sys.executable`, never the `py` launcher — dev-env#300) so the heavy delete never blocks the prompt/call, and emits a `systemMessage`. Each band fires at most once per session via a `session_id`-keyed marker (`scratch/disk_space_check_<session_id>_<band>.flag`, ADR-027), shared across both hook registrations. Markers older than 30 days are swept via `_hookutil.cleanup_stale_sentinels`, gated to the `UserPromptSubmit` registration only (`should_cleanup_sentinels()`) so the scratch/ directory scan doesn't run on every single Bash call under the `PreToolUse` registration (dev-env#768). Advisory only — exit 0 always; any exception is swallowed. Thresholds are hardcoded constants. The pure `classify_free_space()`/`should_cleanup_sentinels()` helpers are unit-tested by `tests/test_disk_space_check.py`. [ADR-037](adr/037-worktree-disk-reclamation.md), [ADR-087](adr/087-pretooluse-disk-space-check.md) |
| `worktree-npm-install.py` | When the session `cwd` is a Claude-managed worktree (`.claude/worktrees/`) of an npm repo whose `node_modules` is absent, runs `npm ci` (or `npm install`) so tests don't fail on missing deps (ADR-016). **Pre-install free-space gate (ADR-045):** before installing it checks free `C:` space — at ≥10 GB it installs as before; below 10 GB it runs a synchronous reclamation ladder (Tier 1 `reclaim-worktree-disk.py --min-free-gb 10`, Tier 2 `npm cache clean --force`) and re-measures; if still below a 5 GB hard floor it **refuses the install** and emits a loud advisory rather than risk a silently-truncated `node_modules` (ENOSPC, dev-env#364). Reclamation is synchronous (the install it guards is synchronous, so a detached reclaim would race it). Fails open on any measurement error; advisory only — exit 0 always. The pure `install_decision()` helper is unit-tested by `tests/test_worktree_npm_install.py`. **Truncation audit (ADR-142):** presence of `node_modules` is no longer sufficient — a tree that exists can still be incomplete (dev-env#945/#721). Once per session per worktree the hook classifies every package directory and acts on the result: a **PARTIAL** package (non-empty, not a junction, no `package.json` of its own — measured 0 false positives across 38 known-good trees, calibrated against the `@langchain/core` truncation dev-env#945 named) triggers a repair `npm ci` **through the same free-space gate**; the uncalibrated arms (≥50% empty shells, or no package directories at all) only advise, since an uncalibrated check is a diagnostic rather than a verdict. npm workspace junctions and skipped optional platform deps are excluded by construction, not by threshold, and any unrecognised dot-*directory* (npm's `.<pkg>-XXXXXXXX` staging shape) means an install is running right now, so the audit defers rather than reinstalling over it — bounded by a defer marker so a crashed install costs one scan per 10 min, not one per prompt. A repair also takes a per-worktree install lock (`npm ci` deletes `node_modules` first, so without one a prompt landing mid-repair would start a second concurrent install) and refuses a tree git does not ignore (a vendored `node_modules` is not disposable). Intra-package *file* absence (`std-env/dist/index.mjs`) is a different layer, out of scope — see dev-env#242. [ADR-045](adr/045-pre-install-freespace-gate.md), [ADR-142](adr/142-node-modules-truncation-gate.md) |
| `awake-blocker.py` (start) | On UserPromptSubmit, spawns a detached watcher (if not already running) that holds a Windows system-sleep lock via `kernel32!SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED)`. Refreshes the sentinel heartbeat on every prompt. Watcher self-terminates if the sentinel is missing or older than 30 minutes (crash safety). Idempotent. Display sleep is not blocked — only system sleep. [ADR-033](adr/033-prevent-system-sleep-while-processing.md) |
| `hook-liveness-check.py` | Parses `claude/settings.shared.json`'s `hooks` block (`wired_hook_events`) to discover which scripts are currently wired and under which events, reads each non-exempt one's heartbeat file under `~/.claude/scratch/hook-heartbeat/` (written by every wired hook's own `_hookutil.record_heartbeat` call — see above), and warns via `_hookout.emit_advisory("UserPromptSubmit", ..., audience="model")` when one or more are missing or older than 7 days (`DEFAULT_CADENCE_DAYS`). Exempt: a hook wired **exclusively** to `PostCompact` and/or `Notification` (rare-firing events where long silence is expected, not a symptom) — a hook also wired to any other event stays subject to the normal cadence even if a rare event is among its registrations. Derives the checked set from `settings.json` itself rather than a hardcoded list, so a newly-wired hook is covered automatically. Heartbeats itself (the literal `"hook-liveness-check"`) as the first statement of its own `main()`. **Self-check** (`/review` finding, [ADR-106 addendum](adr/106-hook-heartbeat-liveness-ledger.md)): if this hook's own name is missing from its parsed wired-hook set — settings.json unreadable/malformed, or the parse silently degraded to something wrong — that is itself anomalous and emits a distinct `format_self_check_failure()` advisory rather than a silent exit, since the heartbeat call already having run means this hook's own ledger entry would otherwise stay fresh regardless. **Debounced to once per session** via a `_hookutil` sentinel (matching `reconcile-open-prs.py`'s pattern) — the 7-day cadence needs nowhere near per-prompt resolution, and the full check is real synchronous I/O (settings.json parse + one heartbeat-file read per non-exempt wired hook) that would otherwise repeat on every prompt. This is the mechanism that would have surfaced `post-tool-use.py`'s months-long silent death (dev-env#377) and `usage-snapshot.py`'s 8-day gap (dev-env#355) — the output-contract gates (ADR-103) verify a hook's code is *correct*; this verifies it is *running*. A structural gate (`test_hook_heartbeat_guard.py`) enforces that every wired hook — including this one — actually calls `record_heartbeat` correctly, so the invariant survives future hook additions/edits without manual re-auditing. [ADR-106](adr/106-hook-heartbeat-liveness-ledger.md) |

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
| `pre-tool-use-canonical-mutate-guard.py` | `cwd` resolves to a canonical (non-worktree) git checkout root — or a `-C`/`--git-dir`/`--work-tree` flag redirects the invocation at such a root from elsewhere, e.g. from a worktree (dev-env#576, ADR-071 Amendment 2) — and the command contains a git-mutating segment (`checkout`, `switch`, `commit`, `merge`, `rebase`, `reset`, `cherry-pick`, `revert`, `stash pop`/`apply`, `branch -d`/`-D`, or `pull` without `--ff-only`) or a `gh pr merge` invocation carrying `-d`/`--delete-branch` (dev-env#558, ADR-071 Amendment 1 — same harm model reached through a `gh` invocation instead of a `git` verb; a bare `gh pr merge` stays unblocked, since it merges only remotely via the GitHub API) | **Blocks the command (exit 2)** — two Claude Code sessions sharing one canonical checkout can otherwise collide (one session's `checkout`/`commit`/`reset` silently thrashes HEAD out from under the other; see dev-env#453). The block keys off the resolved *target* root (cwd's, or the `-C`/`--git-dir`/`--work-tree` redirect target), not cwd alone; the engineering-journal checkout is a **permanent** redirect-target carve-out, not pending dev-env#346 (corrected dev-env#747/ADR-105 — #346 is a narrower, unrelated `biweekly-retro`-specific issue); the `_REDIRECT_TARGET_ALLOWLIST` constant's env-var-override resolution and comparison normalization are now shared with the other three engineering-journal carve-out hooks via `claude/scripts/_journal_canon.py` ([ADR-133](adr/133-shared-journal-canon-module.md)). No-op for an *ambient* (non-redirect) command from a confirmed-LIVE `.claude/worktrees/<name>` (`EnterWorktree`) or `<repo>-worktrees/<name>` (sibling-directory, manual `git worktree add`, dev-env#760/ADR-071 Amendment 5) cwd (ADR-024 covers that surface) or when git can't resolve a toplevel (fails open) — a worktree-shaped cwd that isn't actually a live, registered worktree (e.g. an orphaned directory whose `.git` link is missing) is no longer exempted on shape alone and falls through to the same canonical-root resolution an ordinary cwd gets (dev-env#749, ADR-071 Amendment 3). The same applies to an already-*resolved* target root (cwd's, or a redirect target's): it is exempted only when `git worktree list --porcelain` confirms it a LINKED (non-canonical) entry of its own repository, not merely because its path string looks worktree-shaped — an independently-cloned canonical checkout that happens to sit at a worktree-shaped path is no longer wrongly exempted (dev-env#774, ADR-071 Amendment 6; falls back to the old shape check only when git itself can't answer). Segments come from the shared `_hookio.split_top_level` engine (quote/subshell/heredoc-aware, dev-env#511/ADR-050 Amendment 7), so a quoted `&&`/`\|` or a heredoc body line (bare, or fed through a `$(cat <<'MARKER' ... MARKER)` command substitution) that merely *starts with* a mutating verb does not false-trigger (dev-env#481, generalized). Bypass with a genuine leading `ALLOW_CANONICAL_MUTATE=1` prefix. Has a behavioral self-test: `py -3 claude/scripts/tests/test_canonical_mutate_guard.py`. [ADR-071](adr/071-canonical-checkout-mutate-guard-hook.md) |
| `pre-tool-use-journal-draft-worktree-guard.py` | The command contains a top-level `git worktree add <path> ... <branch>` where `<branch>` matches `draft/YYYY-MM-DD` (or its `-recovery` suffix), from ANY cwd — or a `checkout`/`switch` of such a branch, ambient or via a `-C`/`--git-dir`/`--work-tree` redirect, whose resolved target is NOT the engineering-journal canonical | **Blocks the command (exit 2)** — git allows a branch checked out in only one worktree at a time, so either shape locks the shared `draft/YYYY-MM-DD` branch to a throwaway worktree and blocks the canonical (and every other concurrent stub-writing session) from reaching it, confirmed live twice on 2026-07-12 (dev-env#747). The inverse-direction sibling of `pre-tool-use-canonical-mutate-guard.py` above: that hook blocks a mutation targeting a canonical; this one blocks a checkout of one specific shared branch targeting anywhere BUT the canonical. `git checkout <branch> -- <path>` (a file restore, not a branch switch) is exempt; `worktree add -b <newbranch> ... draft/YYYY-MM-DD` (the draft branch used only as a commit-ish startpoint for a differently-named new branch) is exempt. Deliberately duplicates (not imports) the sibling hook's git-redirect-parsing helpers — see that hook's own bug history (ADR-071 Amendment 2, five fixes) and ADR-105's Judgment calls for why. The `JOURNAL_REPO` constant's env-var-override resolution and comparison normalization ARE now shared with the other three engineering-journal carve-out hooks via `claude/scripts/_journal_canon.py` ([ADR-133](adr/133-shared-journal-canon-module.md)) — that duplication-tolerance judgment call applies only to the git-parsing helpers, not the journal-path pattern this ADR itself was the trigger to extract. Bypass with a genuine leading `ALLOW_JOURNAL_DRAFT_WORKTREE=1` prefix. Has a behavioral self-test: `py -3 claude/scripts/tests/test_journal_draft_worktree_guard.py`. [ADR-105](adr/105-draft-branch-worktree-squat-guard.md) |
| `pre-tool-use-journal-shell-write-guard.py` | A cheap pre-filter (`_might_write_journal_content` — plain `in` checks, not a regex; a first regex-with-lookarounds attempt measured *slower* than the walk it was meant to skip) passes, AND a top-level Bash/PowerShell segment's first physical line carries a genuine (non-quoted) `>`/`>>` redirect (either tool_name), a `tee [-a] <path>` invocation (tool_name=Bash only), a `node -e`/`py -c` invocation MENTIONING a journal path anywhere in its full segment text (either tool_name — the only detector not limited to the first physical line, since the retired serializer recipes place their hazardous path on a later line), or a PowerShell `Out-File`/`Set-Content`/`Add-Content`/`Tee-Object`/`New-Item ... -Value` invocation anchored to segment-start (tool_name=PowerShell only), whose target — the cmdlet's actual bound path argument for the PowerShell case, never any journal-shaped word merely mentioned elsewhere on the line — is shaped like a journal content file (`*.stub.md`, `*.manifest.jsonl`, `open-prs/<digits>.json`, `tiles/<digits>.json`) AND either contains a `sessions/` path component itself or is issued from a *cwd* resolving under the engineering-journal checkout (`_target_is_genuinely_journal` — an unrelated repo's own same-shaped file, e.g. an ML `*.manifest.jsonl`, does not block) | **Blocks the command (exit 2)** — every one of the four file kinds carries free prose or a path field that routinely breaks shell quoting (apostrophes, quotes, backticks, `$`, markdown code fences, a backslash-escaped quote in unquoted prose), either failing the write outright or silently corrupting the file (dev-env#904). Names the matched command/target and instructs the Write tool (create) or Edit tool (a targeted update) instead. `git add`/`commit -m "..." -- <path>`/`push` referencing these paths, `rm`/`Remove-Item` deleting a shard (the documented deletion mechanism), plain reads, directory-only scaffolding (`mkdir -p`, `New-Item -ItemType Directory` with no `-Value`), and a heredoc body merely mentioning a filename as prose are all unaffected — only a genuine redirect/cmdlet/tee/serializer-mention on the segment is ever inspected. A same-line heredoc opener (`cat <<'EOF' > path/to/x.stub.md`) is still detected: quote-masking neutralizes `<<` first so `_hookio`'s heredoc-opener handling doesn't consume the rest of the line as an unterminated declaration; a backslash-escaped quote in unquoted prose (`echo Claude\'s > ...`) is neutralized the same way, via a small quote-state-aware walker (not a blind substitution — that regressed a sibling case, a single-quoted string legitimately ending in a literal backslash). Bypass with a genuine leading `ALLOW_JOURNAL_SHELL_WRITE=1` Bash prefix (scoped to its own top-level segment only — an override on one segment does not exempt an unrelated hazard in a later one) or a standalone `$env:ALLOW_JOURNAL_SHELL_WRITE=1` PowerShell statement (applies forward to later segments, matching real PowerShell env-var semantics). No subprocess/git work — pure text detection, no `_winsubp` needed. Has a behavioral self-test: `py -3 claude/scripts/tests/test_journal_shell_write_guard.py` (63 cases). [ADR-129](adr/129-journal-shell-write-guard.md) + Amendment 1 (16 post-`/review` findings, dev-env#962) |
| `pre-tool-use-shell-content-write-guard.py` | A cheap pre-filter (`might_write_content` — plain `in` checks, not a regex, for the reason recorded under the sibling above) passes, AND a top-level Bash/PowerShell segment supplies file content as an **inline literal**: a heredoc/here-string body redirected to a file (or fed to `tee`), an `echo`/`printf` argument redirected to a file, a `node -e`/`py -3 -c`/`python[3] -c` script that either calls a recognized file-write (`writeFileSync`, `open(…, 'w')`, `write_text`, …) or is itself redirected to a file, a `sed -i`/`perl -pi -e` script (whose target *is* the file, so it needs no separate destination), or — tool_name=PowerShell only — an `Out-File`/`Set-Content`/`Add-Content`/`Tee-Object`/`New-Item -Value` cmdlet fed a `-Value` literal or a `@'…'@` here-string piped in from an earlier pipeline segment. `/dev/null`, `$null`, and fd duplications (`>&1`) are not file targets. | **Blocks the command (exit 2)** when the literal is multi-line or carries an apostrophe, backtick, or backslash — the same sentence `claude/CLAUDE.md` → Authoring File Content states, so guidance and mechanism cannot drift. Names the mechanism, command, target, and specific hazard, and instructs Write (create/replace) or Edit (targeted change). **Program-output redirection is never matched, structurally, not heuristically** (`gh … > "$TMPFILE"`, `npm test > out.log`, `git diff > patch`, `… \| tee f`): no inline literal is found, so no detector fires — this is what makes a fleet-wide guard safe, and it is exactly the "short, known-safe, machine-generated" carve-out the prose rule names. A short, single-line, marker-free literal (`echo done > flag.txt`) also passes at the detector layer, not by override. Two hazard tests, not one: a heredoc body is raw content tested literally (a **quoted** delimiter is deliberately *not* an exemption — two of dev-env#1041's three failures used it), while a shell-quoted argument is walked for quote state so delimiting quotes count as structure and a backslash is flagged only *outside* single quotes — which keeps `sed -i 's/a\.b/c/'` working while `sed -i "s/a\\b/c/"` blocks, as a consequence of the general rule rather than a `sed` special case. Two accepted scope gaps, pinned by explicit tests rather than left silent: a heredoc to a *command's stdin* (`gh pr create --body-file - <<'EOF'`) has no file destination and is not matched, and only the first heredoc opener on a line is inspected. Bypass with a genuine leading `ALLOW_SHELL_CONTENT_WRITE=1` Bash prefix (scoped to its own segment) or a standalone `$env:ALLOW_SHELL_CONTENT_WRITE=1` PowerShell statement (applies forward); ADR-129's `ALLOW_JOURNAL_SHELL_WRITE=1` is honoured too, so a deliberate journal override cleared at that hook is not then blocked here. Wired *after* the journal guard so a journal path keeps that hook's more specific message. Shares its shell-syntax primitives with that guard via `_shell_write_detect.py`. No subprocess/filesystem work — pure text detection, no `_winsubp`. Has a behavioral self-test: `py -3 claude/scripts/tests/test_shell_content_write_guard.py` (47 cases, including a verbatim reproduction of each dev-env#1041 occurrence, and — copied from live instruction files — the repo's own `echo "$(cmd)" > f` and multi-line read-only `node -e` shapes as must-allow cases). [ADR-138](adr/138-shell-content-write-guard.md) |
| `pre-tool-use-journal-compose-force-guard.py` | The command is a git `worktree`/`commit`/`push` invocation whose non-message tokens (a `-m`/`--message`/`-F`/`--file` flag's *value* is excluded from the scan, so a commit message merely mentioning "draft/2026-07-09" as prose can't false-trigger) reference a `draft/<today>` or `compose[-/]<today>` target — "today" is this process's own `datetime.date.today()`, never anything the command claims — **and** the command or cwd identifies a compose worktree context (`compose[-/]<today>` in the resolved cwd path or in a non-message scan token, via `_is_compose_op`); plain stub-write pushes that reference only `draft/<today>` pass through unchanged (dev-env#728) | **Blocks the command (exit 2)** unless a fresh, `force == true` marker exists for today — written only by `journal-compose-force-resolve.py`, run as the literal first Bash action of `/journal-compose` Step 0.6 with the harness-substituted `$ARGUMENTS` text (before any of the agent's own reasoning about the invocation's intent). Mechanical enforcement of the `/journal-compose` today-guard (ADR-017): a 2026-07-08 scheduled-task transcript showed an agent reasoning its way past the guard's *prose* without ever running or reading it (dev-env#631). Every past-day compose (the nightly routine's normal path, ADR-084) and every other git/gh command never reaches the trigger condition at all. Deliberately **fails closed** on a missing/corrupt/stale marker (a reversal of this hook family's usual fail-open convention — justified by this hook's narrow, already-rare trigger) and ships with **no override token** (recovery is re-running the resolve script, never a bypass). Freshness window is a generous 4 hours (`MAX_MARKER_AGE_SECONDS`), covering a realistic multi-project compose with subagent research. `journal-compose-force-resolve.py` also sweeps markers from earlier calendar dates (`_journal_compose_force.cleanup_stale_markers`, 30-day default) on each invocation — safe because a marker is only ever consulted on the same real day it's written (dev-env#768) — including a second sweep for any orphaned atomic-write `.tmp` a rare `os.replace` failure in `write_marker` could otherwise leave behind uncleaned (dev-env#806, mirroring the identical `record_heartbeat` `.tmp` sweep described above). Has pure command-classification tests plus a behavioral self-test: `py -3 claude/scripts/tests/test_pre_tool_use_journal_compose_force_guard.py`. [ADR-096](adr/096-journal-compose-mechanical-force-guard.md) |
| `disk-space-check.py` (PreToolUse) | Every Bash call | Re-checks free `C:` space before each Bash call, not just once per prompt — closes the gap where a long tool-call-only stretch (e.g. an `npm install` mid-turn) could exhaust disk with no intervening prompt to re-trigger the `UserPromptSubmit` registration of this same script. Same thresholds, same messages, same detached-reclaim spawn, and the same per-session marker-file gate as the `UserPromptSubmit` entry (see the UserPromptSubmit table above) — whichever entry fires first for a session covers the other. `shutil.disk_usage()` is a syscall, not a subprocess spawn, so this adds negligible overhead to every Bash call. [ADR-087](adr/087-pretooluse-disk-space-check.md) |
| `pre-bash-drift-check.py` | Every Bash call, gated by elapsed time (not command content) | A fourth cwd/branch drift checkpoint alongside `pre-commit-branch-check.py`/`pre-pr-create-check.py`/`pre-merge-branch-check.py` (dev-env#682): those three only compare recorded vs. current repo/branch state at `git commit`/`gh pr create`/`gh pr merge`, missing a drift affecting any other Bash command. This hook runs the same `_bash_state.py`-backed comparison on every Bash call, but only pays the `git rev-parse --show-toplevel --abbrev-ref HEAD` subprocess once at least 60s (`MIN_GAP_SECONDS`) have elapsed since the last recorded call — a cheap file-mtime stat (`state_age_seconds()`) gates it otherwise, so back-to-back calls never spawn a subprocess. Targets the gap-shaped trigger observed after long background `Agent` calls specifically. Advisory only (`systemMessage`, exit 0), same reasoning as the other three: can't distinguish a legitimate `EnterWorktree`/`cd` from a silent revert. [ADR-101](adr/101-bash-drift-check-every-call.md) |

#### Write / Edit / NotebookEdit hooks

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-tool-use-worktree-path-check.py` | Session `cwd` is inside a Claude-managed worktree — either the nested `.claude/worktrees/<name>` (`EnterWorktree`) convention or the sibling-directory `<repo>-worktrees/<name>` convention reached via manual `git worktree add` (dev-env#760/ADR-024 addendum) — and either (a) the worktree is **orphaned** — its `.git` link is missing or `git rev-parse --show-toplevel` does not resolve to the worktree root — or (b) `file_path`/`notebook_path` is absolute and starts with the canonical repo root | **Blocks** the tool call (exit 2). For an orphaned worktree, the message names the worktree + cwd and renders the recovery recipe from `_worktree_recovery.RECOVERY_STEPS` — the same definition the [Worktree deregistration recovery](#worktree-deregistration-recovery-lost-git-link-routes-git-to-main) runbook is pinned against, so the message a blocked session reads and the runbook cannot drift apart (dev-env#862, [ADR-116](adr/116-single-source-worktree-recovery-recipe.md); before that this row, the hook, and the runbook were three hand-maintained copies, and the hook kept the `--force` recipe dev-env#751 had already disproven). Covers all writes from the orphan, not just canonical-root paths. Otherwise the message names the attempted path, the active worktree root, and the corrected path. No-op when the session is not in a worktree, or (for case b) when the path already targets the worktree root, or (for case c) when the path targets another worktree under the same canonical root (e.g., a compose session writing to `compose-YYYY-MM-DD` from within a different EJ worktree — dev-env#750), or (for case d) when the *session's own* resolved canonical root is the engineering-journal canonical path — every write under it is allowed regardless of target shape, since the Stub file workflow's primary write pattern (`sessions/<project>/<file>` with no worktree segment at all) is the case (c) carve-out never covered (dev-env#750, reopened; ADR-024 2026-08-15 addendum; mirrors `pre-tool-use-canonical-mutate-guard.py`'s `_REDIRECT_TARGET_ALLOWLIST`, ADR-071 — the env-var-override resolution behind `_JOURNAL_ROOT` is now single-sourced with the other three engineering-journal carve-out hooks via `claude/scripts/_journal_canon.py`, [ADR-133](adr/133-shared-journal-canon-module.md)). The path-shape regex is now only a cheap PRE-FILTER: once matched, `git worktree list --porcelain` confirms (or corrects) the candidate canonical/worktree roots before anything is enforced, so a repo whose own root directory name literally ends in `-worktrees` (e.g. `some-repo-worktrees`) no longer has every subdirectory misclassified as a worktree name (dev-env#774 gap (b), ADR-024 addendum; falls back to the regex + liveness check only when git itself can't answer). The liveness check runs one `git worktree list` (previously `git rev-parse`) per file write in a worktree, short-circuited when the `.git` link is already missing. **Bypass for intentional canonical edits:** use `Bash` with `node -e`, `sed`, or `python3` — the hook only covers the three file tools, not `Bash`. [ADR-024](adr/024-worktree-path-guard-hook.md) |
| `pre-tool-use-skill-file-size-guard.py` | `Write`/`Edit` (not `NotebookEdit`) targets a file whose basename is `SKILL.md` (case-insensitive, any project) | **Blocks** the tool call (exit 2) when the resulting file size would GROW past a configurable byte ceiling (`skill_file_size_limit_bytes` in `.claude/hook-config.json`, default 262144 / 256KB) — the predicate is `resulting_size > limit AND resulting_size > current_on_disk_size`, so an edit that shrinks an already-oversized file (but doesn't single-handedly land under the limit) is still allowed, letting an oversized file be trimmed incrementally across multiple edits. For `Write`, the size is the UTF-8 byte length of `tool_input.content`; for `Edit`, the hook reads the current on-disk file and applies the same `old_string`/`new_string`/`replace_all` substitution the tool itself will apply, on a line-ending-normalized copy (the real Edit tool matches `old_string` with line endings normalized to `\n`, then writes `new_string` back converted to the file's own line ending — comparing raw bytes instead would silently never match a multi-line `old_string` against a CRLF file, disabling the guard entirely). Fails open when the file can't be read, `old_string` is empty, `old_string` isn't found, or `old_string` occurs more than once with `replace_all` not set (the real Edit tool independently refuses all of these). Strictly greater-than the limit blocks — exactly at the limit passes. No published Anthropic byte limit exists for `SKILL.md`; the default reflects a real, observed runtime constraint rather than a documented one. Basename-match and `.claude/hook-config.json` loading are shared with `skill-file-size-advisory.py` via `_skill_file_size.py`. Has a behavioral self-test: `py -3 claude/scripts/tests/test_skill_file_size_guard.py`. [ADR-127](adr/127-skill-file-size-guard.md) |

#### Agent hooks

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-tool-use-nested-agent-background-guard.py` | The call's `tool_name` is `Agent`, `agent_id` is present (fires inside a subagent — the documented nesting signal), and `tool_input.run_in_background` is omitted entirely | **Blocks the call (exit 2)** — the exact failure signature behind career-playbook's [ADR-090](https://github.com/brownm09/career-playbook/blob/main/docs/adr/090-synchronous-subagent-spawns.md)/PR #749 orphaned-subagent stalls: a nested spawn relying on the `Agent` tool's implicit `run_in_background: true` default does not block the spawning turn, and the orphaned child's completion routes to `general-purpose`/`main` instead of back to the subagent that spawned it, silently stalling until a human manually resumes it via `SendMessage` (confirmed recurring three times in career-playbook, not a one-off). The block message explains the mechanism and the one-line fix. An explicit `run_in_background` value (`true` *or* `false`) passes through untouched — a deliberate nested-parallel fan-out (Claude Code's own docs: "a reviewer subagent that dispatches a verifier per finding") is not punished, only an omitted field is. A top-level spawn (no `agent_id`) is untouched entirely; backgrounding there is the normal, documented default. No override token — the fix is always a one-line addition to the same call. Fails open on any payload-shape anomaly (malformed JSON, non-`Agent` tool, missing/non-dict `tool_input`). Preceded by an observation-only smoke-test stage (dev-env#936) that confirmed live, with real data, that this hook fires for both a top-level spawn (no `agent_id`) and a genuinely nested one (`agent_id` populated) before the enforcement logic shipped. Has a behavioral self-test: `py -3 claude/scripts/tests/test_pre_tool_use_nested_agent_background_guard.py`. [ADR-126](adr/126-nested-agent-spawn-background-guard.md) |

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
| `pr-merge-reminder.py` | Command contains `gh pr create`, `gh pr merge`, or `git push` (when the pushed branch has an open PR); a `--help`/`-h`-only `gh pr merge` never reaches the merge reminder's live-confirmation fallback (`is_merge_help_only`, dev-env#557). Also recognizes the two-step REST merge fallback (`gh api -X PUT .../pulls/<N>/merge`, used when `gh pr merge` itself is unavailable, e.g. a GitHub GraphQL rate-limit outage) as an independent path to a fired merge reminder — gated on `is_rest_merge_command(command)` AND `output_has_rest_merge_marker(output)` finding `"merged":true`, added to `main()`'s top-level gate and `_build_messages`' `merge_ok` computation as an OR-branch alongside the original `is_merge`/marker check, but deliberately NOT wired into the live `gh pr view` confirmation fallback (that stays scoped to the original `gh pr merge` shape only — ADR-050 Amendment 23's scope decision) (dev-env#986, dev-env#991) | Exits 2 with a `systemMessage` reminding Claude to write a journal stub. For `gh pr create`, also emits steps `3a`/`3b` to write and stage the `open-prs/<N>.json` shard (ADR-056); parses the PR URL from `tool_response.stdout` via `_hookio.read_command_output` so the reminder can include the PR number and URL when available. The reminder's `repo:` field resolves via `_effective_create_repo`: an explicit `--repo`/`-R` flag names the target repo directly, so a cross-repo `gh pr create` (e.g. run from a different project's cwd) is attributed correctly instead of always reporting cwd (dev-env#646, ADR-050 Amendment 18) — falls back to plain cwd when no flag is present, since this branch has no cd-chain-aware dir to fall back to. For `git push`, scopes the lookup to the repo the push **actually targets** — `_effective_push_dir` honors a `cd <path> && git push` chain so a cross-repo push is evaluated against that repo, not the session cwd — then runs `git branch --show-current` and `gh pr list --head <branch>` there to confirm an open PR, and fires on **every qualifying push** to the correct repo (each carries new journalable content — scoping, not dedup, is what removes the cross-repo noise). Skips `engineering-journal` pushes (handled by `stub-push-archive-reminder.py`). For `gh pr merge`, the same scoping applies on the merge side: `effective_merge_dir` resolves a `cd <path> && gh pr merge` chain so the reminder's `repo:` field (and step 1's journal-path lookup) name the merged PR's actual repo, not the session cwd; for the REST merge shape, `_effective_merge_repo` uses cwd directly instead — `effective_merge_dir` has no cd-chain convention for that shape and would wrongly read a `cd` occurring AFTER the REST call as governing it. Wrapped so any internal error exits 0 (never crashes the push flow); the pure `_effective_push_dir` and `effective_merge_dir` helpers are unit-tested by `tests/test_pr_merge_reminder.py`. [ADR-021](adr/021-auto-stub-on-pr-push.md), [ADR-065](adr/065-scope-push-reminder-to-target-repo.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md), [ADR-050 Amendment 23](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `post-merge-tile-checkpoint.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0 — mirrors `post-pr-merge-reclaim.py` / `post-pr-merge-pull.py`, dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557 — otherwise it could misattribute cwd's current branch's already-merged PR to a harmless `--help` check); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986) | Exits 2 with a `systemMessage` reminding Claude to spawn follow-up tiles now via `spawn_task` for any out-of-scope fixes, deferred work, or ideas surfaced during the session. Only an explicit user "skip tiles" instruction exempts the checkpoint (ADR-046). Global — no opt-in. Handles worktree merges that exit non-zero via the output marker. The pure `is_successful_merge()` helper is unit-tested by `tests/test_post_merge_tile_checkpoint.py`. No subprocess calls — no `_winsubp` needed. [ADR-046](adr/046-post-merge-followup-tiles.md), [ADR-060](adr/060-post-merge-tile-checkpoint-hook.md), [ADR-050 Amendment 23](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `post-tool-use.py` | Command contains `gh issue create` or `gh pr create` | Auto-adds the created item to the configured GitHub Project, then exits 2 with a `systemMessage` listing the exact `gh project item-edit` commands to set any `required_fields` defined in `hook-config.json` — for a `single_select` field, the options shown are live-fetched via `gh api graphql` and only fall back to the cached `hook-config.json` value (labeled) if that fetch fails (ADR-076, dev-env#527). Opt-in via `project_number` + `project_owner` in `.claude/hook-config.json`. In a worktree session whose config copy is absent (a project that gitignores it, dev-env's own convention — not every project's), `load_config` resolves the **canonical checkout's** copy (regex `canonical_root_from_worktree`, shared with `reconcile-project-board.py` via `_worktree_canon.py`, for Claude-managed worktrees; `git rev-parse --git-common-dir` for siblings like `dev-env-188`) so the hook fires there too. When even that misses and the command names an explicit `--repo owner/name` for a DIFFERENT repo — a common cross-repo filing pattern that otherwise always silently no-oped, since `load_config` never inspected the command at all (dev-env#532, #537) — `load_config` looks for that repo as a sibling checkout under the same parent directory `reconcile-project-board.py --scan-dir` already scans, trusting it only when the sibling's own `hook-config.json` self-reports a matching `repo` field, never a directory-name guess alone (`extract_repo_flag` / `_sibling_repo_config`). Reads output via the shared `_hookio.read_command_output`; adds the item via the shared `_gh_project.add_to_project` (also used by `reconcile-project-board.py`). [ADR-023](adr/023-generic-required-fields-issue-hook.md), [ADR-049](adr/049-hook-payload-output-field.md), [ADR-052](adr/052-worktree-config-canonical-fallback.md), [ADR-073](adr/073-shared-worktree-canon-gh-project-modules.md), [ADR-076](adr/076-live-fetch-project-hook-single-select-options.md), [ADR-077](adr/077-cross-repo-config-resolution-for-issue-pr-create.md) |
| `post-pr-merge-pull.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0, and worktree merges exit non-zero on local cleanup despite succeeding — dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986) | Fast-forwards the local `main` branch via `git fetch origin main:main` so the local clone stays current after a merge, then **parks the just-merged worktree off `main`** if `gh --delete-branch` left it squatting the ref (only possible when the canonical had freed `main`) — recreating its `claude/<slug>` branch at HEAD via `merge_park_target` (`_worktree_topology.py`), acting on the hook's own session worktree so no liveness check is needed. Reads output via the shared `_hookio.read_command_output`; the pure `is_successful_merge()` helper is unit-tested by `tests/test_post_pr_merge_pull.py` and the park decision `merge_park_target` by `tests/test_worktree_topology.py`. `extract_repo` resolves the merged repo via `--repo` flag, GitHub PR URL, the REST merge fallback's own path (dev-env#986 — checked before the cd-chain/git-remote fallbacks below, since the REST path always names its target repo explicitly), or (falling back) `effective_merge_dir`-scoped `git remote get-url origin` — so a merge run via a `cd <path> &&` chain still fast-forwards the right repo's `main`, not the session cwd's. [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md) |
| `post-pr-merge-reclaim.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0, and worktree merges exit non-zero on local cleanup despite succeeding — mirrors `post-pr-merge-pull.py`, dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986) | Spawns `reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --protect-cwd <cwd>` **detached** (via `sys.executable`, never the `py` launcher) to strip regenerable `node_modules`/`.turbo` from now-idle merged worktrees — the dominant `C:` consumer — at the idle event instead of waiting for the 6-hourly routine. No `--min-free-gb` (the trigger is the merge, not low space); `--protect-cwd` shields the active worktree. Does **not** remove the worktree directory/branch — that requires an out-of-process context (Windows cwd lock) and stays the daily `prune-stale-worktrees` job. Informational only — exit 0 always. Reads output via the shared `_hookio.read_command_output`; the pure `is_successful_merge()` helper is unit-tested by `tests/test_post_pr_merge_reclaim.py`. [ADR-045](adr/045-pre-install-freespace-gate.md), [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `post-pr-merge-project.py` | Command contains `gh pr merge` and the output confirms a completed merge; when the marker is absent, a `--help`/`-h`-only invocation is excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557 — the confirmed live incident: `--help` was previously misattributed as a completed merge, moving an unrelated issue's project item to Done); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986 — this widened the initial command-shape gate, which previously exited before the marker check ever ran for a REST-only merge) | Auto-moves the linked issue (`Closes/Fixes/Resolves #N` in PR body) to Done on the configured GitHub Project. Derives the PR number from the command (`gh pr merge <N>` / a `/pull/N` URL / the REST fallback's own `.../pulls/<N>/merge` path — `resolve_command_pr_number`, dev-env#986), falling back to gh's success marker in the output (`gh pr merge` output has no `/pull/N` URL); gated on a confirmed-merge marker so a queued `--auto` or a failed merge never moves an issue to Done. `resolve_command_repo` (dev-env#986) also derives the repo from a PR URL argument or the REST fallback's own path, and the hook skips entirely when that differs from cwd's own configured `repo` — cwd's `project_number`/`project_node_id`/`status_field_id`/`done_option_id` are scoped to cwd's repo and don't apply to a different one, so a cross-repo merge is a safe no-op rather than a wrong-board move (dev-env#559). A `cd`-chained cross-repo merge with no URL is now covered too: `load_config` resolves via `effective_merge_dir(command, cwd)`, so the merged repo's own `hook-config.json` is read rather than cwd's (dev-env#569, ADR-111 — matching this hook's two merge-triggered siblings); resolving the correct repo's own config to complete the move *instead of* skipping remains a separate enhancement (dev-env#571). `extract_repo_from_command`/`extract_pr_number_from_command` delegate to the shared `_repo_target` resolver (ADR-111); `resolve_command_repo`/`resolve_command_pr_number` layer the REST-path fallback on top, split out of `main()` for independent testability. Opt-in via `status_field_id` and `done_option_id` in `hook-config.json`. Reads output via the shared `_hookio.read_command_output`; pure helpers unit-tested by `tests/test_post_pr_merge_project.py`. [ADR-014](adr/014-auto-move-project-item-done-on-merge.md), [ADR-049](adr/049-hook-payload-output-field.md), [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md), [ADR-111](adr/111-shared-repo-target-resolution.md) |
| `usage-snapshot.py` | Command contains `gh pr merge`, excluding a `--help`/`-h`-only invocation from the marker-absent live-confirmation fallback (`is_merge_help_only`, dev-env#557); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986) | Queries `https://api.anthropic.com/api/oauth/usage` (via OAuth Bearer token from `~/.claude/.credentials.json`) and parses the session JSONL for the top-5 costliest exchanges. Emits a `### Usage Snapshot (post-merge)` markdown block showing weekly/5-hour utilisation vs. day-of-week soft targets (configured in `claude/usage-config.json`). Global — fires for all repos without opt-in. Include the emitted block verbatim in the post-merge journal stub. A still-valid "expiring" token is used (not skipped); a token that's **missing/unparseable in an otherwise-valid creds file** or **expired** is **refreshed on demand** at merge via the CLI (`keep-token-warm.ps1`) before fetching (both cases share the same `attempt_token_refresh()` retry-and-recheck helper — [dev-env#819](https://github.com/brownm09/dev-env/issues/819)), so the snapshot only falls back to the stderr advisory ([#357](https://github.com/brownm09/dev-env/pull/357)) when the refresh token itself is dead ([ADR-044](adr/044-eliminate-usage-snapshot-gap-on-demand-refresh.md)). Under the MSIX Claude **desktop app** the token is unreachable to any subprocess — OAuth lives in the OS keychain and is injected in-process, no readable `.credentials.json` is ever written, a CLI subprocess reports `loggedIn:false`, and `setup-token` is 403 at the usage endpoint — so both the read and the refresh are permanently futile; the missing-token branch detects this via a `claude auth status` probe and emits an accurate advisory naming [dev-env#915](https://github.com/brownm09/dev-env/issues/915) *without* the doomed ~35s refresh ([ADR-124](adr/124-usage-snapshot-desktop-app-keychain-deadend.md)). The `ClaudeKeepTokenWarm` scheduled task (see Utilities) keeps the token usually-fresh on npm-CLI installs so on-demand refresh rarely fires there; under the desktop app the task now self-gates to a fast, logged no-op instead of running futilely every cycle ([ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md) addendum, [dev-env#917](https://github.com/brownm09/dev-env/issues/917)). If the usage API is unreachable, the script retries once after 1 second; if both attempts fail it emits a stderr advisory and exits 2 (so the failure is visible rather than silently skipped — [#302](https://github.com/brownm09/dev-env/issues/302)). The merge-confirmation decision itself (`resolve_merge()`) is a pure function returning one of seven `reason`s (`marker`/`rest_marker`/`not_merge_shape`/`help_only`/`no_confirm_needed`/`gh_view_confirmed`/`gh_view_unconfirmed`); every merge-shaped invocation, confirmed or not, is appended as one best-effort JSON line to `C:/Users/brown/.claude/scratch/usage-snapshot-merge-trace.log` (`_log_merge_trace`, never raises) — added after two live reproductions post-dating the original [dev-env#474](https://github.com/brownm09/dev-env/issues/474) fix (PR #954, PR #988) both saw no snapshot with no record of which fallback branch ran, so the next occurrence has permanent forensic evidence instead of requiring a live-instrumented reproduction. |
| `stub-push-archive-reminder.py` | `git push` to `engineering-journal` with a stub commit and no unresolved open PR in the touched manifest(s) | Writes a **per-session** sentinel file (`~/.claude/scratch/stub-pushed-<session_id>.flag`, via `_hookutil.sentinel_path` — dev-env#980, [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) Amendment 2) and exits 0; a request with no `session_id`, or one containing anything outside `[A-Za-z0-9_-]`, is forgone entirely rather than falling back to a shared/degenerate path (the pre-fix global `stub-pushed.flag` let ANY concurrent session's Stop consume it, causing both false-positive archive instructions and missed reminders — the old filename is also opportunistically unlinked once, since it predates the per-session naming and the new cleanup glob never matches it). Verifies the most-recent commit in the journal repo touches a `.stub.md` file before writing the flag. Also requires that none of the touched stub(s)' paired manifest shard(s) show an unresolved open PR (a PR number in `prs_opened` not also in `prs_closed`) — a stub is pushed immediately after `gh pr create` and again after each subsequent push, well before `/review` and `gh pr merge` ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) Amendment 1); each touched stub's paired manifest is read from its **current** on-disk content via `head_commit_has_unresolved_pr`/`manifest_path_for_stub`, not gated on whether the triggering commit's own diff touched that manifest, since an ordinary mid-session stub-only push doesn't re-touch it. Also runs `_hookutil.cleanup_stale_sentinels` on its own (rare) success path — `journal-stop-check.py` runs the same sweep on every Stop, which is the more reliable backstop for a crashed/never-Stopped session's orphaned flag. The Stop hook (`journal-stop-check.py`) consumes only the sentinel matching its **own** `session_id` and issues the archive reminder on **stderr with exit 2** — blocking the stop so the Claude-facing reminder actually reaches Claude, since a Stop hook's exit-0 stdout is not added to Claude's context ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)). The push-failure guard (`has_push_error`, reading output via the shared `_hookio.read_command_output`), the command-shape predicate (`is_git_push_command`, `scan_top_level`-anchored — ADR-050 Amendment 10), the repo-reference predicate (`references_engineering_journal`, anchored to a `cd`/`git -C` directory argument rather than a CLI verb — ADR-050 Amendment 12), and the now-pure `most_recent_commit_has_stub`/`manifest_path_for_stub`/`head_commit_has_unresolved_pr` (ADR-091 Amendment 1) are unit-tested by `tests/test_stub_push_archive_reminder.py`. [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `journal-shard-write-advisory.py` | Command harvests a candidate `.manifest.jsonl` / `open-prs/<N>.json` / `tiles/<N>.json` path that resolves to a real file (also wired on `Write`/`Edit` — see below for the full entry) | See the full entry under **Write / Edit hooks** below. |
| `post-tool-use-cwd-track.py` | Every Bash call | Best-effort `git rev-parse --show-toplevel` + `git branch --show-current` against the payload's `cwd`, then writes `{repo_root, branch, cwd}` to a per-session state file (`~/.claude/scratch/bash_state_<session_id>.json` via the shared `_bash_state.py` module). Feeds the drift-warning check in `pre-commit-branch-check.py`, `pre-pr-create-check.py`, `pre-merge-branch-check.py` — dev-env#573's mitigation for a session's tracked cwd/branch silently reverting with no error surfaced — and, via the same file's mtime, the elapsed-time gate in `pre-bash-drift-check.py` (dev-env#682, [ADR-101](adr/101-bash-drift-check-every-call.md)). A cwd that isn't a git repo, or a `git` call that fails/times out, records `None` rather than raising. Exit 0 always; no `systemMessage`, purely a side-channel write. [ADR-085](adr/085-bash-repo-branch-drift-detection.md) |

#### Write / Edit hooks

Matched with `"matcher": "Write"` and `"matcher": "Edit"` (also wired on `"matcher": "Bash"` — see the Bash table above).

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `memory-write-advisory.py` | The `Write` tool targets a `.md` file inside a `…/memory/` directory (not the `MEMORY.md` index) **and** the written body carries no immortalization link — no `#\d+` issue/PR ref, no `ADR-\d+`, no `CLAUDE.md`, no "Documented in repo" | Emits a one-line stderr reminder and exits 2 (the `Write` already ran — exit 2 *surfaces* the reminder, it does not block) telling Claude to pair the durable memory with an immortalization issue and link it from the memory body + `MEMORY.md`. The link-absence heuristic keeps it quiet on writes that already carry a link; the agent (not the hook) judges durability. Spawns no subprocess; fails open (exit 0). The pure `should_advise_memory_write()` helper is unit-tested by `tests/test_memory_write_advisory.py`. [ADR-048](adr/048-memory-immortalization-issue-pairing.md) |
| `journal-shard-write-advisory.py` | Write/Edit: `file_path` is an engineering-journal manifest shard (`sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`), open-PR shard (`sessions/<project>/open-prs/<N>.json`), or tile shard (`sessions/<project>/tiles/<issue-number>.json`) — classified by path component, not anchored to the canonical checkout, so a shard under a Claude-managed journal worktree still matches. Bash: any `.manifest.jsonl` / `open-prs/<N>.json` / `tiles/<N>.json`-shaped token harvested from the raw command text that resolves to a real file (against `cwd`, a harvested `cd`/`git -C`/`--git-dir=` directory, or the constant `~/Git/engineering-journal` fallback) | Reads the file's on-disk bytes (not the tool-call payload) and validates them against the schema shared with `validate-manifest.py` via `_journal_schema.py`: missing required fields (schema order), a UTF-8/UTF-16 BOM (named, not left as an opaque parse failure — Node `JSON.parse` and the Python shard readers both silently choke on it), an empty shard, non-JSON-object content, and — for the two numeric kinds (open-PR and tile), reusing `_journal_shards.shard_number` — a non-numeric filename or a filename/embedded-number mismatch. **Tile shards** (`sessions/<project>/tiles/<issue-number>.json`, [ADR-118](adr/118-tile-persistence-shards.md), added in dev-env#870) are the kind whose write is otherwise wholly unverified: a manifest or open-PR shard is written by a session already doing PR bookkeeping, whereas a tile shard is written right after the `spawn_task` call whose payload it preserves — so a malformed one fails *silently* (`iter_numeric_shards` skips it without a word) *and* late, surfacing only when someone needs the payload back after a crash, i.e. exactly when it can no longer be reconstructed. They validate against `TILE_REQUIRED_FIELDS` / `missing_tile_fields` (`issue`, `url`, `title`, `tldr`, `prompt`, `cwd`, `spawned`; `stub` optional), and a filename/embedded-`issue` disagreement is flagged with the consequence spelled out — `reconcile-pending-tiles.py` treats such a shard as corrupt and refuses to reconcile it, which silently exempts that tile from pruning forever. Presence alone proved too weak a bar for `cwd`, the one required field that is a filesystem path: a Windows path written through a double-quoted `node -e` serializer crosses a JS string literal that eats its backslashes (`C:\Users\brown\Git\dev-env` → `C:Users<U+0008>rownGitdev-env`), producing a shard that exists, parses, and carries all seven fields while naming no directory — so the exact re-spawn it exists to enable is already lost and nothing downstream would ever notice (`reconcile-pending-tiles.py` reads `url` and the filename, never `cwd`, and no compose-time gate reads tile shards at all). `malformed_tile_fields` therefore also checks `cwd` *shape* — non-string, empty, any control character (reported alone, since it names the cause), surrounding whitespace, or not absolute (a drive-letter root, a UNC root, or POSIX absolute; a single-backslash `\Users\…` is drive-relative and stays flagged) — while deliberately **not** flagging a correctly-escaped backslash path (a valid value; the forward-slash prescription is a docs rule, not a validation one) or a well-formed path that does not exist on this machine (shards are read on other machines, and this module is offline/import-only). Three live shards, dev-env#904 / [ADR-081](adr/081-write-time-journal-shard-validation-hook.md) Amendment 2. On any problem, exits 2 with a stderr advisory naming each file and its problems, plus all three schema templates and the rewrite-with-the-Write-tool warning ([ADR-129](adr/129-journal-shell-write-guard.md) — never a shell mechanism at all, retired from this message alongside the corresponding `pre-tool-use-journal-shell-write-guard.py` PreToolUse block) (the write already happened — this surfaces the gap immediately instead of at the next day's `/journal-compose` Step 0.7 gate). Silent (exit 0) otherwise. The Bash token harvest is a raw regex scan, deliberately **not** `_hookio.scan_top_level`-anchored — it validates on-disk data, not command intent, so a path merely mentioned inside a heredoc/quoted argument/subshell is harmless to check. Spawns no subprocess; fails open. Every pure helper is unit-tested by `tests/test_journal_shard_write_advisory.py`. [ADR-081](adr/081-write-time-journal-shard-validation-hook.md) |
| `skill-file-size-advisory.py` | Write/Edit: `file_path`'s basename is `SKILL.md` (case-insensitive, any project) | Non-blocking nudge once a `SKILL.md` write/edit lands at/above a lower watermark (`skill_file_size_warn_bytes`, default 204800 / 200KB), independently configurable from the guard hook's hard limit (`skill_file_size_limit_bytes`, reused here only to show "N% of the hard limit" in the message; both loaded via the shared `_skill_file_size.py`). The write already happened by `PostToolUse` time, so `_hookout.emit_block()`'s exit 2 here only *surfaces* the message — there is nothing left to block. Simpler than the guard hook: stats the real on-disk file via `os.path.getsize()`, no encoding/newline estimation needed. Kept as a separate `PostToolUse` hook rather than folded into the guard's own decision, since it reports the *actual* post-write size rather than the guard's *pre-write* prediction. Gated to one nudge per session per file via a `_hookutil.sentinel_path()` marker (keyed on `session_id` + a hash of `file_path`) so a multi-edit session fixing an oversized file doesn't get the same nudge on every edit; a payload with no `session_id` skips dedup and always advises. Inclusive boundary — exactly at the watermark advises. Not wired to `Bash`, unlike its neighbor above. Fails open (`OSError` on a missing/racing file). Has a behavioral self-test: `py -3 claude/scripts/tests/test_skill_file_size_advisory.py`. [ADR-127](adr/127-skill-file-size-guard.md) |

---

### Stop

Fires each time Claude finishes responding (the end of every turn), not only at session end; it does not fire on user interrupts (per the [Claude Code hooks docs](https://code.claude.com/docs/en/hooks-guide)).

**All Stop hooks run in parallel — the list order below is not an execution order.** Per the same docs: *"all matching hooks run in parallel … every hook's command runs to completion before Claude Code merges the results. One hook returning `deny` doesn't stop sibling hooks from executing."* So a hook that exits 2 (`stop-tile-enumeration-gate.py`, and `journal-stop-check.py`'s archive-reminder branch — [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)) does **not** short-circuit the rest — `awake-blocker.py`'s sleep-lock release runs at every Stop regardless of a blocking hook, and reordering the list carries no meaning. See [ADR-088 → Stop-hook parallelism](adr/088-state-keyed-tile-enumeration-gate.md).

| Script | What it does |
|--------|-------------|
| `token-tracker.py` | Reads the session JSONL, aggregates token usage, and appends a record to `~/.claude/scratch/token-sessions.jsonl`. Supports Sonnet 4.6, Opus 4.6, and Haiku 4.5 pricing. |
| `journal-stop-check.py` | On the stub-push sentinel flag **matching this Stop event's own `session_id`**, **blocks the stop (exit 2, reminder on stderr)** so Claude actually archives the session: the reminder asks Claude to call the `ccd_session_mgmt__archive_session` MCP tool — a Claude-only action — and a Stop hook's exit-0 stdout is **not** added to Claude's context, so the former stdout emission was invisible to Claude ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)). A `session_id` that is missing or contains anything outside `[A-Za-z0-9_-]` never resolves to any sentinel (dev-env#980, ADR-091 Amendment 2 — the pre-fix global sentinel let ANY concurrent session's Stop consume it, causing false-positive archive instructions and missed reminders). Also runs `_hookutil.cleanup_stale_sentinels` on every Stop, sweeping orphaned per-session flags a crashed/never-Stopped session left behind. Fires at most once per session (sentinel consumed on read) and honors the `stop_hook_active` loop guard. The reminder itself unconditionally states that `archive_session` requires the user's explicit agreement and must never be called speculatively — that invariant doesn't depend on any transcript scan, so it survives a detection miss — and is best-effort augmented (never suppressed, never re-fired) with a count-derived caveat when the session's own transcript still shows pending/`in_progress` tasks tracked via `TaskCreate`/`TaskUpdate` (this harness has no `TodoWrite` tool at all) or a backgrounded `Agent` call with no observed completion notification (a `<task-notification>` block whose `<tool-use-id>` matches the call's own id, searched across every record type confirmed to carry it — `user`, `queue-operation`, `attachment` — not `user` alone). An `Agent` call counts as backgrounded when `run_in_background` is `true` **or omitted** (the documented default for a top-level spawn) — only an explicit `false` excludes it — and `isSidechain` records are excluded from both scans so a subagent's own task/agent activity is never attributed to the main session (dev-env#1002, ADR-091 Amendment 3 — corrected during `/review` from an initial version that scanned for a nonexistent `TodoWrite` tool and required `run_in_background is True` strictly, both empirically confirmed against real transcripts to miss most real in-flight work). Degrades to the unconditional-invariant base reminder (no count-derived sentence) on any transcript-resolution or parse failure; a cheap substring pre-filter (matching the sibling Stop hooks' pattern) skips the full parse when neither `TaskCreate`/`TaskUpdate` nor `Agent` appears in the raw text at all — deliberately the tool name, not the `run_in_background` flag text, since an omitted flag never appears as literal `run_in_background` text. Then — **non-blocking** — checks for stale open journal stubs and unmerged draft branches (user-facing advisories pointing at later dedicated-session work, so they must not block), emitting any closing message via the `_hookout` **systemMessage** channel (exit 0) rather than plain stdout (a Stop hook's exit-0 stdout is invisible, so the former `print()` surfaced nothing — dev-env#740, [ADR-103](adr/103-shared-hookout-emitter.md)), and cleans up orphaned draft files. Pure/fixture helpers + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_journal_stop_check.py`. [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) |
| `posttooluse-inert-advisory.py` | Reliable-event safety net for the [ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md) inert-PostToolUse limitation. Scans the just-ended transcript; if a dev-env (project #3) `gh issue/pr create` or `gh pr merge` ran but **no** `attachment` record carries `hookEvent == "PostToolUse"` (the inert signature — no `gh` call, no `project` scope), surfaces the manual fallback pointing to the dev-env `CLAUDE.md` → GitHub Project → Fallback. `gh pr merge` detection also recognizes the two-step REST merge fallback (`gh api -X PUT .../pulls/<N>/merge`, dev-env#986, dev-env#991): `detect_board_actions` gates a REST merge on `is_rest_merge_command(command) AND output_has_rest_merge_marker(output)` finding `"merged":true` (a stronger positive-confirmation signal than the `gh pr merge` branch's absence-of-hard-failure-text check, since `gh api`'s JSON response body is ordinary stdout rather than the stderr-only success line that check works around); `_devenv_merge_pr`'s own REST branch resolves the merged PR's repo/number directly from the REST path (`_repo_target.repo_from_rest_merge_path`/`pr_number_from_rest_merge_path`), falling back to cwd-based dev-env detection for gh's unresolved `{owner}`/`{repo}` URL-templating placeholder — mirroring `post-pr-merge-project.py`'s identical REST resolution (ADR-050 Amendment 23). Detection is dev-env-scoped (created URL / merged PR must be `brownm09/dev-env`) and any PostToolUse attachment all session keeps it silent, so the legitimate different-repo/no-config silent paths ([ADR-049](adr/049-hook-payload-output-field.md)) never trip it. **Blocks the stop once (exit 2, advisory on stderr)** — a Stop hook's exit-0 stdout is invisible, so exit-2 stderr is the only channel that reaches the model (dev-env#740, [ADR-103](adr/103-shared-hookout-emitter.md)); a `stop_hook_active` loop guard + per-session sentinel keep it to one fire, with `mark_resolved` after the emission so a failed delivery retries (dev-env#629). Pure helpers + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_posttooluse_inert_advisory.py`. [ADR-055](adr/055-reliable-event-inert-posttooluse-advisory.md), [ADR-050 Amendment 23](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `stop-tile-enumeration-gate.py` | **State-keyed** tile-enumeration gate — the Stop-hook analog of `pre-merge-findings-gate`, covering **six independent triggers** that share one skip-override/sentinel mechanism (triggers (1)/(2) additionally share the enumeration signal). **(1) Merged PR** (the auto-merge-aware complement to the **command-keyed** `post-merge-tile-checkpoint.py`, ADR-060): scans the just-ended transcript for a PR that reached MERGED state this session by **any** path (a `gh pr merge` success marker; a `gh api .../pulls/N/merge` with `"merged":true`; or a `gh pr view` MERGED state correlated with a PR the session created/enqueued — the auto-merge case the command-keyed hook is blind to). **(2) Dangling created issue** (ADR-092): a `gh issue create` ran this session and the issue was **not resolved** by Stop — resolved means closed via a same-session merged PR's Closes/Fixes/Resolves keyword, or explicitly closed via `gh issue close` — covering the pure-investigation session that files issues but implements nothing, which trigger (1) never catches. When either trigger fires with **no** tile-enumeration artifact recorded — a `spawn_task` tool call, or the prescribed text (`Follow-ups considered … -> tiled (task_id / #N)` / `-> not tiled, because <reason>`); a bare "no follow-ups" does **not** satisfy it (the lifting-logbook#700 skip) — it **blocks the stop (exit 2)** with the reminder(s) on stderr (combined into one message if multiple fire). **(3) Tiles spawned without a table** (ADR-094 addendum, dev-env#656): a `spawn_task` tile was spawned this session but no assistant text carries the stable heading `### Tiles spawned this session` (line-anchored, case/heading-level insensitive) that PR1/#663 introduced in `claude/CLAUDE.md` → Session Summaries & Tile Tracking. Trigger (3) is a **stricter, independent** bar than "an enumeration happened": a spawned tile satisfies triggers (1)/(2)'s enumeration check, but does **not** by itself satisfy (3) — a session can resolve (1)/(2) via the spawn while (3) still fires because the table itself was never emitted. The pre-filter that gates the full JSON parse now also OR-matches the bare substring `spawn_task` (not the exact fully-qualified tool name, matching the real detector's own namespace-agnostic "any namespacing hits" philosophy — a fixed review finding, dev-env#674). **(3b) Tile spawned without its shard** ([ADR-118](adr/118-tile-persistence-shards.md), dev-env#870): a `spawn_task` tile was spawned this session but nothing evidences a write of `sessions/<project>/tiles/<issue-number>.json`. Trigger (3)'s sibling — same spawn, different loss: the table tells the **user** a tile exists; the shard is what lets the **tile** be re-spawned once the chip dies, which it does on every app restart with no API to re-create it (the paired issue survives either way, so what is actually lost is the one-click restart the tile existed to provide, precisely in the crash case where restarting by hand costs most). Blocking, like (1)-(3), because whether a path was written is objectively verifiable. Evidence is looked for in a `Write`/`Edit` `file_path`, a Bash/PowerShell `command`, **and Bash tool output** — the last is load-bearing, not belt-and-braces: the documented recipe writes shards through a *serializer script*, so a multi-tile session legitimately runs one `py -3 <script>` whose command text names no shard path at all and whose paths appear only in its output (observed live in the session that shipped the reader — three shards, one call). An input-only scan would have blocked that session for a write it performed correctly. Detection is deliberately **session-global, not per-tile**: `spawn_task`'s input carries no issue number, so a spawn cannot be matched to the shard filename that preserves it, and counting (N spawns ⇒ N shard paths) breaks on that same serializer recipe — so it fires only on the total skip. Over-matching is the chosen failure direction throughout (a stray `tiles/12.json` mention merely means no fire; a missed real write would be a false block). See ADR-118 Amendment 2. **(4) Deferral-question** (ADR-109, dev-env#772): scoped to sessions where (1) or (2)'s context already applies (a merge or issue-create happened), the assistant's own final response matches one of a bounded set of scheduling/permission phrasings ("let me know if you want me to start it now", "should I implement this now", "want me to ... now") instead of tiling the follow-up directly — the motivating incident was the next PR in a multi-PR initiative (ADR-059) asked about instead of tiled, even though other genuine follow-ups WERE correctly tiled in the same turn. Unlike (1)-(3), trigger (4) is a heuristic natural-language match, not an objective fact, so it does **not** block: it deliberately does **not** accept `enumeration_recorded` as resolution (only an explicit "skip tiles" override) — reusing the enumeration signal, as a first draft did, would have let those same unrelated tiles silently resolve it without ever firing for the incident it exists to catch — and it rides `_hookout.emit_advisory("Stop", ..., audience="user")` (a `systemMessage`, exit 0) instead of blocking; it is silently skipped in favor of the harder block whenever a blocking trigger also fires the same turn (only one exit code per invocation). **(5) Unchained merge** ([ADR-140](adr/140-unchained-merge-workstream-gate.md), dev-env#1044): a PR merged this session, the session's **own opening prompt** named no follow-on work, and nothing was queued AT OR AFTER that merge — no `spawn_task`, `AskUserQuestion`, or `gh issue create` (the session that merges and then ends idle; a `spawn_task`/`AskUserQuestion`/`gh issue create` from *before* the merge, for unrelated reasons, does not count — dev-env review of PR #1053). "Chain-bearing" is read from the **first genuine (non-synthetic, non-wrapper) user prompt** via the shared `_hookutil.first_user_prompt_text` — which also skips a `<command-name>` slash-command wrapper's own machinery text, not just synthetic records (same review): a `#N` reference, a `github.com/.../issues|pull/N` URL, or the `=== CHAIN` marker means the session was *handed* its next thread (the `retro-chain-refill` / ADR-132 / ADR-137 chained-tile convention — not ADR-094, which mandates only that a tile prompt reference its tracking issue, the case the `#N`/URL forms already detect), so what it chains after that is [ADR-137](adr/137-proactive-tile-forward-chaining.md)'s prose rule to judge, not this hook's. A mid-session mention of an issue number is deliberately **not** consulted — it says nothing about what the session was *asked* to do, and consulting it would let any passing `#N` in a diff or `gh` output silently disarm the trigger; a bare `#N` immediately after a common ordinal/step word ("step #2") is excluded from counting as a reference at all, for the same disarm-the-trigger reason. `session_merged_prs` is reused verbatim (no second copy of the merge detection, per ADR-090's drift rationale) and the pre-filter reuses trigger (1)'s own `"merged"` clause, so the trigger costs no new scan. **Blocking**, unlike (4): all inputs are objectively verifiable (a merge marker, three `tool_use`/command names, one regex over one fixed string), and a Stop hook has no non-blocking *model-visible* channel at all. Resolution is a real `spawn_task`, a real `AskUserQuestion`, or a same-session `gh issue create` (the CLAUDE.md-documented fallback for a session where `spawn_task` itself is unavailable), each counted only after the merge — or "skip tiles" — enumeration text does **not** resolve it, exactly as in (4), since "Follow-ups considered: none → not tiled" is the very decision it questions. A chain-bearing **or unreadable** opening prompt resolves as *out of scope* rather than firing (unreadable ⇒ never block: the conservative direction for a blocking gate), and marking the sentinel there is safe because the first genuine user prompt is immutable for the session. Note a spawn resolves (5) while independently *arming* (3) and (3b) — the same asymmetry those already have with (1). The ranking order for the survey path lives in `claude/CLAUDE.md` → Capture follow-ups as tiles, not here — reading it needs live `gh` calls this deliberately network-free hook does not make, and restating the list here would just be a second, driftable copy of one ADR-140's own Rationale explicitly keeps in prose. `post-merge-tile-checkpoint.py` carries a one-line pointer to the same path at merge time, but the Stop trigger is the real gate (ADR-053). Honors an explicit "skip tiles" user instruction and the `stop_hook_active` loop guard; each trigger fires at most once per session via its own scratch sentinel — one recorded enumeration satisfies either or both of (1)/(2), the table marker independently satisfies (3), a shard write independently satisfies (3b), only "skip tiles" satisfies (4), and a post-merge spawn / ask / issue-create / "skip tiles" satisfies (5). Pure transcript scan — no `gh`/network/subprocess (fail-open, exit 0 on any error); NOT inert in background/SDK sessions ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)), unlike the command-keyed sibling. Global — no opt-in. Pure helpers unit-tested + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_stop_tile_enumeration_gate.py`. [ADR-046](adr/046-post-merge-followup-tiles.md), [ADR-088](adr/088-state-keyed-tile-enumeration-gate.md), [ADR-092](adr/092-dangling-issue-tile-enumeration-gate.md), [ADR-094](adr/094-tile-tables-and-issue-per-tile.md), [ADR-109](adr/109-tile-gate-deferral-question-trigger.md), [ADR-118](adr/118-tile-persistence-shards.md), [ADR-137](adr/137-proactive-tile-forward-chaining.md), [ADR-140](adr/140-unchained-merge-workstream-gate.md) |
| `stop-journal-stub-checkpoint.py` | **Journal-stub checkpoint** for the previously prose-only "Report / analysis generated" journal trigger ([ADR-062](adr/062-journal-report-analysis-trigger.md)): scans the just-ended transcript and **blocks the stop (exit 2, reminder on stderr)** when a genuine user prompt carries a report/analysis or verify/deploy keyword **AND** the session made ≥ 5 substantive tool calls (Read/Grep/Glob/Bash/Edit/Write/… — reads count, since report sessions are read-dominated) **AND** opened/merged no PR (those already nudge via `pr-merge-reminder.py`) **AND** wrote no `*.stub.md` **AND** is not a `/review` session **AND** has no "skip journal" override. Exit-2 + stderr is the only Stop delivery that reaches Claude ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)); advisory in spirit — a once-per-session sentinel fires it at most once and the reminder carries a one-line dismissal for false positives. Detection = keyword intent + substantive-work threshold, chosen for low false-positive noise (a false exit-2 costs one dismissable nudge; a false negative misses the stub this fixes). Report intent is scoped to real user-typed text (skips `isMeta`/`isCompactSummary` and slash-command wrappers), so a keyword in tool output or assistant text never counts. Pure transcript scan — no `gh`/network/subprocess (fail-open, exit 0 on any error); honors `stop_hook_active`; **NOT** inert in background/SDK sessions ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)), unlike a PostToolUse hook. Global — no opt-in. Pure helpers unit-tested + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_stop_journal_stub_checkpoint.py`. [ADR-100](adr/100-stop-journal-stub-checkpoint-hook.md) |
| `stop-experiment-verdict-gate.py` | **Experiment-verdict gate** — the Stop-time backstop for the verdict half of the `## Experimental Rigor` protocol (ADR-115; Pass-3 dimension 7 enforces the design half at plan time). Scans the just-ended transcript and **blocks the stop (exit 2, reminder on stderr)** when an assistant text item states a process-experiment conclusion — a bounded, high-precision set of operative idioms (an experiment noun tightly anchored to an outcome / adopt-reject verb: "the spike failed", "the experiment was a success", "adopt the challenger", "the challenger outperformed …") — **AND** no `/experiment-audit` ran this session (neither the `[experiment-audit]` marker the skill emits nor a `/experiment-audit` command invocation) **AND** there is no "skip experiment audit" override. Scans assistant **text only**, so verdict wording written into a file (a `Write`/`Edit` `tool_use` input — an ADR, a report) never trips it; a bare unit-test "the test failed" and meta-discussion that keeps words between the noun and the outcome are excluded by the tight idioms. Advisory in spirit — a once-per-session sentinel fires it at most once and the reminder carries a one-line dismissal for false positives; exit-2 + stderr is the only Stop delivery that reaches Claude ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md), [ADR-103](adr/103-shared-hookout-emitter.md)). Pure transcript scan — no `gh`/network/subprocess (fail-open, exit 0 on any error); honors `stop_hook_active`; **NOT** inert in background/SDK sessions ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)). Global — no opt-in. Pure helpers unit-tested + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_stop_experiment_verdict_gate.py`. [ADR-115](adr/115-experimental-rigor-protocol.md) |
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
| `idle_refresher_minutes` | integer | `60` | `idle-refresher.py` — inject a return-after-idle refresher cue when the gap since the last turn exceeds N minutes |
| `status_field_id` | string | — | `post-pr-merge-project.py` — GitHub Project Status field ID; required to auto-move item to Done on merge |
| `done_option_id` | string | — | `post-pr-merge-project.py` — single-select option ID for "Done" status; required to auto-move item to Done on merge |
| `baseline_test_failure_tracking` | boolean | `false` | `new-branch.sh` / `pre-pr-create-check.py` / `baseline-tests.sh` — opt-in to the pre-existing test failure baseline (ADR-030). When `true`, `new-branch` snapshots failing tests at branch creation and the pre-PR hook reminds Claude to run `baseline-tests diff`. |
| `test_command` | string | `npx jest --json --silent` | `baseline-tests.sh` — shell command emitting Jest `--json` stdout. Override when `npm test` wraps Jest through turbo/lerna and does not pass `--json` through. |
| `skill_file_size_limit_bytes` | integer | `262144` | `pre-tool-use-skill-file-size-guard.py` — hard-block byte ceiling for a `SKILL.md` Write/Edit; also read (advisory-only) by `skill-file-size-advisory.py` to show "N% of the hard limit" |
| `skill_file_size_warn_bytes` | integer | `204800` | `skill-file-size-advisory.py` — lower watermark for the non-blocking nudge, independent of `skill_file_size_limit_bytes` |
| `session_start_sync_disabled` | boolean | `false` | `session-start-sync.py` — per-project opt-out from the session-start fetch/fast-forward-or-warn drift check (ADR-130). Read from the checkout's **canonical** root's `.claude/hook-config.json` (via `_worktree_canon.canonical_repo_root`), not a worktree's own copy — `.claude/` is commonly gitignored, so a worktree checkout usually has no copy of this file to read at all. |

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

5. **Declared fail direction — advisory hooks fail *open*, blocking gates fail *closed*.** Every hook must know which way it fails when its *own* code crashes (at import time or in `main()`) and enforce it, because Claude Code reads the exit code, not the intent: for a PreToolUse hook, exit 2 blocks the tool and *anything else* (0, or a traceback's 1) lets it through.
   - **Advisory hooks** (rule 2) fail **open** — a top-level `except: sys.exit(0)` on `main()`. A crash must never block the user.
   - **Blocking / fail-closed gates** fail **closed** — a crash must exit **2**, not the default 1 (which Claude Code treats as "allowed"). This means guarding **both** surfaces: a top-level `try/except → sys.exit(2)` around `main()` (re-raising `SystemExit` so the gate's own deliberate exit 0/2 verdicts are preserved), **and** any risky module-level dependency load — a dynamic `importlib` `exec_module`, a `from _sibling import …` — wrapped in `try/except → exit 2`, since an import-time crash fires before `main()` and no `__main__` guard can catch it. A gate that crashed to exit 1 = fail-OPEN silently disabled the very check it exists to enforce (the class dev-env#717/#718 closed). The two current fail-closed gates are `pre-auto-merge-checkpoint-gate.py` (`gh pr merge --auto`, [ADR-083](adr/083-auto-merge-checkpoint-gate.md)) and `pre-tool-use-journal-compose-force-guard.py` (same-day `/journal-compose`, [ADR-096](adr/096-journal-compose-mechanical-force-guard.md)).

   **Caveat — a globally-registered gate must not fail its *imports* closed.** A PreToolUse(Bash) gate runs on *every* Bash call, so if its top-level imports fail-closed one broken sibling blocks *all* Bash. Weigh the load surface: `pre-auto-merge-checkpoint-gate.py`'s dependency load fails **closed** (a broken sibling blocking all Bash is loud and CI-caught; silently ungating `--auto` is the worse outcome), whereas `pre-tool-use-journal-compose-force-guard.py`'s plain sibling imports stay **fail-open** — only a crash *inside* its `main()`, reached solely once a same-day compose target has already matched, fails closed. State the chosen direction (and, for a blocking gate, this import-vs-runtime split) in the hook's module docstring, and ASCII-sanitize any crash-reason text (rule 4's cp1252 concern applies to exit-2 stderr too).

6. **Explicit per-hook `timeout`, and output on the channel the event actually delivers.** Every `settings.json` hook entry declares an explicit `timeout` (seconds) at or above its budget floor — `usage-snapshot.py` → 90, a hook importing `_winsubp` (subprocess work, rule 4) → 30, pure-Python → 10 — so Claude Code's default timeout never silently truncates a slow hook mid-run (dev-env#720, gotcha that a too-tight bound re-creates the very invisibility being fixed). And every emission goes through the channel its event + exit code actually surfaces: use `_hookout.emit_advisory` / `emit_block` (ADR-103) rather than a hand-rolled `print` / `sys.stderr.write`, since exit-0 stdout is model-visible only on the context events (`_hookout.STDOUT_MODEL_VISIBLE_EVENTS`), exit-0 stderr is invisible everywhere, and exit 2 ignores stdout. **These four invariants — output contract, ASCII wire-safety, the rule-5 safe-exit `__main__` guard + fail direction, and the wiring/`timeout` requirement — are now mechanically gated** by `test_hook_output_contract.py`, `test_hook_safe_exit_guard.py`, and `test_settings_hook_wiring.py` (dev-env CLAUDE.md `## Testing` items 61–63), each landing green with current offenders in a two-sided allowlist the `_hookout` migrations (PRs 5–7) shrink.

---

## Routines

Autonomous scheduled agents. They run on a cron schedule with no user interaction. Canonical source is authored and reviewed in `claude/routines/<name>/SKILL.md`, which is mirrored read-only at `~/.claude/routines/` via a directory junction — but the `scheduled-tasks` MCP tool never reads through that junction. It owns a separate, real, non-linked directory, `~/.claude/scheduled-tasks/<taskId>/SKILL.md`, holding the *live* prompt for each registered task. Merging a routine to `main` does not update or create a live task; that requires a separate `create_scheduled_task` / `update_scheduled_task` call, and nothing keeps the two copies in sync afterward short of repeating that step. Prefer having the live prompt read its own canonical file at run time and fall back to an embedded copy when unreachable (the pattern `weekly-memory-audit` uses) — see [ADR-003 amendment](adr/003-config-in-version-control.md) and [dev-env#344](https://github.com/brownm09/dev-env/issues/344).

**Model selection (applies to every routine below).** The scheduler runs each task under the app's *global* model default — **not** the model in any project `settings.json`, which it ignores. That default can change out from under a task and **silently drift** (e.g. `claude-opus-4-8` → `claude-sonnet-5`), and a lower-capability model can misread an XML-wrapped autonomous prompt and *greet* ("what would you like to work on?") instead of executing, with **no error and no notification** ([dev-env#698](https://github.com/brownm09/dev-env/issues/698)). Mitigation: pin the model in the routine's frontmatter (`model: claude-opus-4-8`) — but **confirmed inert** (dev-env#703 item 2, 2026-07-14: all four `prune-stale-worktrees` runs 07-11→07-14 came up on `claude-sonnet-5` despite the pin; the scheduler ignores frontmatter `model:`, consistent with the `scheduled-tasks` MCP tools exposing no model parameter and `list_scheduled_tasks` no model field) — so, model-agnostically, place an **execute-now / do-not-greet imperative** at the very top *and* bottom of the *live* prompt (the greeting fires before any Step-0 canonical read-through). That imperative is the **sole effective mitigation** and is confirmed working: all four inert-pin runs executed the routine in full rather than greeting. As of [dev-env#767](https://github.com/brownm09/dev-env/issues/767) (2026-07-14) it is deployed to **every** registered routine's live copy (top *and* bottom), with the verbatim strings captured in each canonical routine's *Restorable live-copy imperative* section for deterministic restore. See the [ADR-003 amendment (2026-07-10)](adr/003-config-in-version-control.md).

---

### daily-journal-compose

**Schedule:** `0 7 * * *` (7:09am local, daily — the scheduler applies a small deterministic jitter on top of the base cron)

Assembles all `YYYY-MM-DD_*.stub.md` files across all configured projects into the canonical 11-section journal entries and opens PRs against `engineering-journal`.

**Retry wrapper:** `journal-compose-with-retry.sh` — wraps the routine for Windows Task Scheduler use. Retries up to 3 times with 5-minute delays on transient failures. Before each attempt except the last, also runs a liveness pre-check (`check-journal-compose-liveness.py`, below) against the shared `engineering-journal` checkout — a dirty working tree for the target date skips that attempt without spending a `claude -p` call; the final attempt proceeds regardless. Logs to `~/.claude/scratch/`. [ADR-086](adr/086-journal-compose-liveness-guard.md)

---

### prune-stale-worktrees

**Schedule:** `0 4 * * *` (4am local, daily)

Scans all primary git repos directly under `C:/Users/brown/Git` and removes worktrees whose branches are fully merged into `origin/main` — both `claude/*` branches and, via `--include-named`, hand-named branches (`feat/`, `fix/`, `docs/`, etc.) held to the identical merged/dirty/liveness bar ([ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md)) — and **parks any non-primary worktree squatting `main`** back onto its own `claude/<slug>` branch (recreated at HEAD via `git checkout -b` — non-destructive, frees the ref even for a dirty worktree the old `git worktree remove` refused; [ADR-058](adr/058-worktree-squatting-main-detection-correction.md)). Also **parks (and, when safe, removes outright) any non-primary worktree squatting an engineering-journal `draft/YYYY-MM-DD` branch** — checked unconditionally across every scanned repo, since a squatter here blocks the canonical (and every concurrent stub-writing session) from reaching that day's draft branch at all, not just this repo's own `main`; idle + clean + fully pushed relative to the branch's own `origin/<branch>` is parked and removed in one pass, idle-but-dirty (or not provably fully pushed) is parked only, leaving the worktree's contents untouched for review ([dev-env#747](https://github.com/brownm09/dev-env/issues/747), [ADR-105](adr/105-draft-branch-worktree-squat-guard.md)). Repos with no GitHub remote are skipped. Uses `git branch -d`, `git worktree remove` (no `--force`), and `git checkout -b` (parking). Skips the current worktree and dirty worktrees (for removal), and — since this routine runs out-of-process and cannot see other sessions via cwd — **any worktree with an active Claude session** (transcript activity within 24h; override with `--liveness-window-min`); the liveness guard runs before the park, so only an *idle* squatter is moved (the draft-branch-squat check does not additionally consult liveness itself — see ADR-105's Judgment calls for why the transcript-mtime signal cannot apply to that specific shape of worktree). A branch not otherwise detected as merged is still treated as merged if a repo opts in via `.claude/hook-config.json`'s `prune_ephemeral_patterns` and every file in the branch's diff vs. `origin/main` matches one of those regexes — off by default, additive only ([ADR-075](adr/075-ephemeral-diff-worktree-pruning.md)). Sends a push notification listing any unmerged branches that were skipped. [ADR-051](adr/051-worktree-liveness-guard.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-075](adr/075-ephemeral-diff-worktree-pruning.md), [ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md), [ADR-105](adr/105-draft-branch-worktree-squat-guard.md)

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

**Step 6.5 — chain refill (dev-env#967).** Immediately after Step 6 finishes filing/updating this
run's `retro-action` queue issues, invokes the shared `retro-chain-refill` skill against the
standard 6-repo participant list (`seeded_by="biweekly-retro <run-date>"`) — the same liveness
check the daily `retro-chain-backstop` routine uses, so a repo whose chain is already alive is
classified ALIVE and skipped rather than double-seeded. This routine also newly carries the
self-referencing dual-copy pattern (reads its own canonical `SKILL.md` at run time, falls back to
an embedded copy) as an opportunistic side effect of wiring Step 6.5 — but per the same
registration caveat as `retro-chain-backstop` above, the live task copy is not re-synced by editing
the canonical file alone; `update_scheduled_task` against the live `biweekly-retro` task is a
separate, outstanding post-merge step. See [ADR-131](adr/131-retro-chain-idempotent-refill.md).

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

### retro-chain-backstop

**Schedule:** `0 21 * * *` — 21:00 **local** time, daily.

Self-healing daily backstop for the retro-action chained-tile backlog burn-down mechanism
(dev-env#967). The mechanism is otherwise entirely prompt-carried (each tile's CHAIN block spawns
the next link itself) and breaks silently and permanently on a dismissed chip, a compacted
session, an early exit, an API failure, or the item being finished by hand outside the tile — this
routine is what makes a break same-day-recoverable instead of permanent.

Step 0 is deliberately **not** `sync-routine-worktree` — that skill's own branch-class logic
rebases (or `reset --hard`s) any non-`main` branch onto `origin/main`, which is correct for a
routine's own isolated worktree but unsafe against the engineering-journal path here: the *shared
canonical checkout*, which normally sits on `draft/YYYY-MM-DD`, a branch every concurrent session
commits stub/manifest/tile-shard shards to throughout the day (`daily-journal-compose` dropped this
identical call for this identical reason). Step 0 instead runs a plain `git fetch` followed by
`git pull --ff-only` against the canonical checkout — safely advances the branch to its own
upstream when possible, and fails non-destructively (no check-out, no rewrite) on any divergence or
dirty tree, aborting the run cleanly rather than risking a concurrent session's work. Step 1 then
invokes the `retro-chain-refill` skill against the standard 6-repo participant list
(`seeded_by="retro-chain-backstop <date>"`). 21:00 local was chosen over the crowded 4–7am block
three other routines already occupy (which has its own documented reliability history — see the
2026-07-10 Amendment above), and gives same-day repair latency for a chain that breaks mid-day, at
identical "daily" cost.

**Outputs:** a push notification summarizing the per-repo status and any action taken (issue
filed, tile spawned, shard committed) — no committed report file. This routine's output *is* the
filed issues/tiles/shards, matching `reconcile-project-board`'s report-via-notification shape
rather than `biweekly-retro`'s PR'd-report shape.

**Dual-copy registration caveat:** the live task copy is written by `create_scheduled_task` /
`update_scheduled_task` into the separate, non-synced `~/.claude/scheduled-tasks/` directory (see
the Routines intro above) and does not auto-sync from the canonical. The live prompt reads its own
canonical `claude/routines/retro-chain-backstop/SKILL.md` at run time and falls back to an
embedded copy when unreachable — the same self-healing pattern `weekly-memory-audit` uses. **Unlike
that already-registered sibling, this routine is not yet registered at all as of the PR that added
it** (dev-env#967's own PR deliberately does not call `create_scheduled_task` — a session-only
mutating action requiring explicit user sign-off) — registration is an explicit, outstanding
post-merge step, not implied by the code landing on `main`.

**Origin:** dev-env#967. [ADR-131](adr/131-retro-chain-idempotent-refill.md)

---

## Utilities

On-demand scripts — not wired to any event. Run manually or from other scripts. See
[`claude/scripts/README.md`](../claude/scripts/README.md) for the same content grouped with the
hooks and shared modules that serve the same workflow, rather than split across two sections.

| Script | Invocation | What it does |
|--------|-----------|-------------|
| `token-report.py` | `py -3 token-report.py [--date YYYY-MM-DD] [--days N] [--project name] [--latest] [--show-subagents]` | Generates markdown and JSON token usage reports from `~/.claude/scratch/token-sessions.jsonl`. |
| `backfill-tokens.py` | `py -3 backfill-tokens.py` | Backfills token data for sessions predating the token-tracker hook. Idempotent — deduplicates on `session_id`. |
| `prune-merged-worktrees.py` | `py -3 prune-merged-worktrees.py [--dry-run] [--repo-path /path/to/repo\|--scan-dir /path/to/dir] [--liveness-window-min N] [--include-named]` | Manual equivalent of the prune routines. Auto-detects the GitHub repo slug from the origin remote URL. `--repo-path` targets a specific repo's worktrees (defaults to dev-env); `--scan-dir` discovers and prunes all git repos directly under the given directory. Removes merged `claude/*` worktrees and parks any worktree squatting `main` off onto its own branch ([ADR-058](adr/058-worktree-squatting-main-detection-correction.md)). Skips any worktree with an active Claude session (transcript activity within `--liveness-window-min`, default 1440 = 24h). A repo can opt into an additional prunability signal via `.claude/hook-config.json`'s `prune_ephemeral_patterns` — a branch whose entire diff vs. `origin/main` matches those regexes is treated as merged even without a formal merge ([ADR-075](adr/075-ephemeral-diff-worktree-pruning.md)). `--include-named` (off by default) extends the same merged/dirty/liveness checks to non-`claude/*` branches too, instead of skipping them unconditionally via the prefix guard ([ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md)). [ADR-051](adr/051-worktree-liveness-guard.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-075](adr/075-ephemeral-diff-worktree-pruning.md), [ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md) |
| `reclaim-worktree-disk.py` | `py -3 reclaim-worktree-disk.py [--dry-run] [--repo-path /path\|--scan-dir /path] [--min-free-gb N] [--protect-cwd /path] [--liveness-window-min N]` | Manual equivalent of the `reclaim-worktree-disk` routine (and the script the `disk-space-check.py` hook spawns). Strips `node_modules`/`.turbo` from idle Claude-managed worktrees (clean **and** merged-or-not-ahead). `--min-free-gb N` makes it a no-op unless the drive is below N GB; `--protect-cwd` shields the active worktree; `--liveness-window-min` (default 360 = 6h) additionally skips worktrees with an active session in *another* worktree the routine can't see via cwd. Deletes only regenerable dirs — never the worktree or git state. [ADR-037](adr/037-worktree-disk-reclamation.md), [ADR-051](adr/051-worktree-liveness-guard.md) |
| `replay-shell-content-guard.py` | `py -3 replay-shell-content-guard.py [--gap] [--json] [--samples N] [--scan-dir DIR] [--scripts-dir DIR]` | Replays `pre-tool-use-shell-content-write-guard.py` ([ADR-138](adr/138-shell-content-write-guard.md)) over recorded session transcripts under `~/.claude/projects/` and reports what it would block: block rate, mechanism mix, hazard reasons, override-token use, truncated samples, and — with `--gap` — the size of ADR-138's remaining accepted gap. **The headline metric is ENRICHMENT**: the blocked set's shell-parse-failure rate divided by the corpus baseline rate. `>1` means the guard concentrates real failures; `~1` means it is firing on ordinary traffic and is over-matching. Measured 11.7x at Amendment 1 over 54,330 unique commands, with a per-mechanism breakdown so a single arm's rate is re-derivable rather than quoted from memory. This is ADR-138 Amendment 1's answer to dev-env#1046 item 1 — a **reader**, not a new log, because session transcripts already record every command *and its outcome*, which is strictly more than a hook-side block log could hold, and because a replay re-runs the *current* code over the *same* corpus and so doubles as a regression instrument. Read-only; commands are truncated in all output (transcripts can carry secrets). Re-run after any detector change to the guard, and treat enrichment trending toward `~1x`, or routine `ALLOW_SHELL_CONTENT_WRITE` use, as the tripwire to re-scope it. |
| `sweep-scratch-debris.py` | `py -3 sweep-scratch-debris.py [--apply] [--max-age-days N]` | One-time/on-demand force-sweep of every known per-session/per-day sentinel and marker family in `~/.claude/scratch/` (`journal_onboard_*.flag`, `session_mode_ack_*.txt`, `disk_space_check_*.flag`, plus the already-self-cleaning families, to clear any backlog predating their own cleanup) older than `--max-age-days` (default 30, matching `_hookutil.MAX_AGE_DAYS`). Dry-run by default; `--apply` actually deletes. Reports per-family file counts and bytes. Deliberately excludes singleton/long-lived files (`awake.lock`, `hook-heartbeat/*.ts`, `token-sessions.jsonl`, `baseline_<repo>_<branch>.json`) — see the script's own module docstring for why each is unsafe to sweep by raw age. Motivated by the 2026-07-10 hook-reliability assessment (dev-env#768): ~5,687 sentinel files / ~285 MB / 771 files >30 days old, `journal_onboard_*` alone accounting for 986. |
| `new-branch.sh` | `new-branch <name>` (shell function; source `~/.claude/scripts/new-branch.sh` in `.bashrc`) | Creates a branch always rooted at `origin/main`. Warns when HEAD has diverged from the merge base. When `baseline_test_failure_tracking: true` is set in `.claude/hook-config.json`, also runs `baseline-tests snapshot` to capture pre-existing failures (ADR-030). |
| `baseline-tests.sh` | `baseline-tests <snapshot\|diff\|gc>` | Captures and diffs pre-existing test failures for the fix-on-touch policy ([ADR-030](adr/030-baseline-test-failure-policy.md)). `snapshot` runs the project test command (`test_command` in `hook-config.json`, default `npx jest --json --silent`) and writes failing-test fingerprints to `C:/Users/brown/.claude/scratch/baseline_<repo>_<branch>.json`, then calls `gc` (best-effort) to sweep this repo's own stale baselines. `diff` re-runs tests and classifies current failures into `new` (block PR), `preexisting-touched` (fix-on-touch or file), and `preexisting-untouched` (note only); exits 1 if any `new` failures are present. `gc` removes `baseline_<repo>_*.json` files for the current repo whose recorded branch (read from the JSON envelope) no longer exists locally or on `origin`; kept whenever the branch is still live in either place, or whenever the remote existence check itself fails (dev-env#778). Jest-only in the first implementation. |
| `merge-stale-pr.sh` | `bash merge-stale-pr.sh <PR-URL>` | Remediates stale `engineering-journal` draft PRs: checks out the branch, warns on missing journal file, deletes orphaned drafts, rebases, and squash-merges with auto-conflict resolution. |
| `journal-project-repo-map.py` | `py -3 journal-project-repo-map.py <engineering-journal-root> [--json <outfile>]` | Resolves each `sessions/<project>/` directory to a GitHub `owner/repo` slug — the input `/journal-compose` Step 8a **Source 3** needs before it can query a repo for `start-here`-labeled issues. Primary source is the **root** `README.md`, pairing each `### ` section's `**Repo:**` bullet with its `**Journal:** [sessions/<project>/` bullet (the Journal bullet is what names the *directory*; section titles deliberately differ, e.g. `### Job Search` → `sessions/job-search/`); falls back to the project's own README accepting `Repo:` **or** `Repository:`, with or without `**` bold markers. Every slug is shape-validated before it reaches the caller's `gh issue list --repo <slug>` command line, so a malformed README produces a reported skip rather than a shell argument. Emits `SOURCE3_RESOLVED=`/`SOURCE3_SKIPPED=` counts, one named `SOURCE3_SKIP <project> -- <reason>` per unresolved project, and a distinct `SOURCE3_MAPPING_EMPTY` (exit 1) when projects exist and none resolve. Exit 0 with skips is information, not failure; exit 2 is a usage error. Replaces an inline regex that matched **zero** of the 11 project READMEs, leaving Source 3 inert — and silently so, since the loop's bare `continue` made a broken mapping indistinguishable from a repo with no labeled issues ([dev-env#1045](https://github.com/brownm09/dev-env/issues/1045), [ADR-032 Amendment 1](adr/032-journal-start-here-dashboard.md)). Run it directly to diagnose why a `start-here` label isn't surfacing. |
| `merge-ready.sh` | `bash merge-ready.sh [owner/repo ...]` | Lists, per repo, the open PRs that are green + mergeable + waiting on nothing (the merge-ready set) vs. those still open but not ready. Defaults to `merickvaughn/lifting-logbook`; accepts multiple `owner/repo` args. Read-only — `gh pr list` plus a `node` rollup of check states (`jq`-free, per the no-`jq` convention). |
| `get-project-item.sh` | `ITEM_ID=$(bash get-project-item.sh <issue-number> [project-number] [owner])` | Resolves a GitHub Project item node ID from an issue/PR number. Checks a local item-ID cache first (dev-env#1057, [ADR-141](adr/141-project-item-id-creation-time-cache.md)) — a hit costs **zero** `gh` calls, so it succeeds even when `gh` is offline/unauthenticated. Falls back to the original full `gh project item-list --limit 1000` fetch-and-scan on a miss, and writes the result back into the cache. Defaults to project 3, owner `brownm09`, repo `dev-env`. Overridable via args or `PROJECT_NUMBER`/`PROJECT_OWNER` env vars (repo via `PROJECT_REPO`). Requires `project` scope for the fallback path: `gh auth refresh -s project`. |
| `session-mode-report.py` | `py -3 session-mode-report.py [--since YYYY-MM-DD] [--interactive-only] [--non-plan-only] [--log PATH]` | Reports the startup permission mode per session by parsing the `session-mode-prompt.py` hook log (`scratch/session-mode-prompt.log`). For each `session_id` it takes the earliest entry as the startup mode, classifies sessions as interactive vs. automated (scheduled-task / `<tag>` prompts), and flags (`!`) interactive sessions that started outside `plan`. Desktop/web and spawn-task sessions launch in `bypassPermissions` by design (overriding `defaultMode: plan`); this surfaces that. Read-only; report to stdout, diagnostics to stderr. |
| `register-keep-token-warm.ps1` | `powershell -ExecutionPolicy Bypass -File register-keep-token-warm.ps1 [-IntervalHours N] [-Unregister]` | **Per-machine, run once.** Registers the non-elevated, hidden `ClaudeKeepTokenWarm` scheduled task (every 4h by default) that runs `keep-token-warm.ps1`. Idempotent (`-Force`); `-Unregister` backs up the live task definition to `Documents\LOGS\ClaudeKeepTokenWarmBackup.xml` first (write-if-absent, [ADR-079](adr/079-backup-restore-convention.md)), refuses to proceed if the backup can't be captured, then removes the task and verifies removal by read-back. Restoring is re-running the script with no switches (the task carries no state the script itself didn't define). Each machine needs its own registration. [ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md) |
| `keep-token-warm.ps1` | (scheduled-task payload — invoked by `ClaudeKeepTokenWarm`, not run by hand) | Runs `claude -p 'ok' --model haiku` to trigger the CLI's own OAuth-token refresh, keeping `~/.claude/.credentials.json` fresh so `usage-snapshot.py` works without a manual `claude` refresh — unless a `<claude.exe> auth status --json` probe first reports the MSIX desktop-app dead-end (`loggedIn:false`, mirroring `usage-snapshot.py`'s `cli_auth_status`), in which case it exits early logging `desktop-app: nothing to refresh` instead of spawning a doomed refresh call ([dev-env#917](https://github.com/brownm09/dev-env/issues/917)). Logs token mtime + minutes-to-expiry before/after each run to `Documents\LOGS\keep-token-warm_<date>.txt` (never the token value); always exits 0. [ADR-043](adr/043-keep-warm-scheduled-task-for-token-freshness.md) |
| `validate-manifest.py` | `py -3 validate-manifest.py <manifest-path> [<manifest-path> ...]` | Pre-compose validator for engineering-journal manifest shards. Checks that each entry has all five required fields (`stub`, `topic`, `tokens`, `prs_opened`, `prs_closed`). Both ADR-056 per-session shards (single JSON object per file) and legacy per-day manifests (one JSON object per line) are handled — paths are parsed line-by-line. Absent/unmatched paths are skipped. Exit 0 — all entries valid; exit 1 — at least one entry is missing a required field or a line failed to parse, with file path, line number, and missing fields on stderr. Wired into `/journal-compose` as **Step 0.7** — runs before any stub read or subagent spawn so field gaps surface up front rather than mid-compose (dev-env [#423](https://github.com/brownm09/dev-env/issues/423)). |
| `validate-composed-output.py` | `py -3 validate-composed-output.py <markdown-path> [<markdown-path> ...]` | Scans composed journal output for raw terminal text that leaked into the prose. Two checks: a short list of git usage/error signatures (`See git-rebase(1)`, `--set-upstream-to`, `Please specify which branch`, `There is no tracking information`, `Everything up-to-date`, `nothing added to commit`, plus `hint: `/`fatal: ` **anchored to line start**), and any indented line inside a `## Progress Summary` section (that prose is never indented). **Fenced code blocks and inline code spans are exempt** — journal entries legitimately quote git errors as documented content, and a journal *about* this bug names the signatures in prose; the span matcher uses a backreferenced tick run so a ``` ``double-tick span containing `single` ticks`` ``` is one span, which a naive regex would split and leak. Indented lines are *not* exempt from the signature check — this corpus fences its code rather than indenting it. Absent/unmatched paths are skipped. Exit 0 — clean; exit 1 — at least one region needs review, printed as `file:line`, which check fired, and the full offending line. **Advisory: it never edits a file** — the motivating paste ate the middle of a sentence and welded a surviving real fragment onto the tail of a `git branch --set-upstream-to` line, so blind deletion loses content. Pure logic lives in `_composed_output_scan.py`. Wired into `/journal-compose` as **Step 8b** — after every write (Steps 6/7/8/8a) and before Step 9 deletes the stubs, so overwritten prose can still be recovered from its source. Does not duplicate Step 6.5's structural assertion (dev-env#467), which checks *headings* on journal *entries* only. Measured against the live corpus: 431 markdown files, zero findings other than the known corruption. [ADR-121](adr/121-composed-output-stray-terminal-scan.md) (dev-env [#894](https://github.com/brownm09/dev-env/issues/894)). |
| `reconcile-project-board.py` | `py -3 reconcile-project-board.py [--repo-root PATH\|--scan-dir PATH] [--dry-run]` | Engine behind the `reconcile-project-board` routine (and an on-demand board check). Reads `.claude/hook-config.json`, lists open issues + project items, computes orphans (open issues not on the board), adds each via `gh project item-add`, then reports the orphans + any pre-existing **open** board items still missing a required field, printing the exact `gh project item-edit` commands. **Add-only + report-only** — never sets a field value (no guessing) and never mutates single-select options. `--repo-root` targets a specific repo (defaults to the canonical checkout, so it works from a worktree); `--scan-dir` discovers and reconciles every git repo directly under the given directory that has a `.claude/hook-config.json`, skipping repos without one and isolating a single repo's `gh` failure from the rest of the scan (a `project`-scope failure still aborts the whole scan immediately). `--dry-run` reports without adding. Detects a missing `project` scope and prints the `gh auth refresh -s project` hint (exit 1). [ADR-068](adr/068-reconcile-project-board-orphan-issues.md), [ADR-070](adr/070-reconcile-project-board-scan-dir.md) |
| `check-journal-compose-liveness.py` | `git -C C:/Users/brown/Git/engineering-journal status --porcelain \| py -3 check-journal-compose-liveness.py YYYY-MM-DD` | Detects an in-flight session that may still be writing engineering-journal stubs for the date journal-compose is about to merge. Reads `git status --porcelain` output from stdin (stays pure I/O; the caller runs git) and exits 1 if any changed path is a stub/manifest shard (`YYYY-MM-DD_HHMMSS.stub.md` / `.manifest.jsonl`) for the given date, 0 otherwise; exits 2 on a malformed date argument. Called from `journal-compose-with-retry.sh` (primary, before each retry attempt) and `journal-compose/SKILL.md` Step 0.6 (defense-in-depth for manual invocations). [ADR-086](adr/086-journal-compose-liveness-guard.md) |
| `journal-compose-replay.sh` | `bash journal-compose-replay.sh <worktree> <prev-commit> <pathspec> [<pathspec> ...]` | The Step 10.5 conflict-recovery replay for `/journal-compose`, invoked once the compose worktree has been switched onto a fresh `compose/YYYY-MM-DD` branch cut from `origin/main`. Replays everything the draft branch added/modified/deleted since the merge base (which it derives from `<prev-commit>` itself, so the pre-push-rejection route that skips the merge-tree probe still works), and owns the open-PR shard-integrity restore. Every path is first partitioned by whether `origin/main` **also** changed it: uncontested paths replay wholesale from `<prev-commit>`; a contested one is 3-way merged from blobs (never the work-tree file — mixing blob LF with a CRLF checkout makes every line look changed) and, if that conflicts, left holding `origin/main`'s content and reported for manual reconciliation. Prints `REPLAY_SAFE` / `BOTH_CHANGED` / `AUTO_MERGED` / `MANUAL_RECONCILE` / `SHARD_INTEGRITY_RESTORED` / `SHARD_RESTORE_SKIPPED`. Exit 0 — all resolved, caller may commit; exit 2 — manual reconciliation required, **do not commit**; exit 1 — usage/precondition error, or a git failure partway through (the tree is then partially replayed; re-cut the disposable recovery branch rather than committing it). Temp files go to the scratch dir, falling back to a self-removing `mktemp -d` where that path is absent (`JOURNAL_COMPOSE_REPLAY_SCRATCH` overrides the root — test-only). [ADR-104](adr/104-journal-compose-conflict-recovery-diff-and-replay.md) (+ Amendment 1) |
| `run-hook-tests.py` | `py -3 run-hook-tests.py [--list] [--timeout N] [--max-retries N]` | Discovers and runs the whole hook/script test suite — every `test_*.py` plus every bash `*.sh` gate across the two test directories (`claude/scripts/tests/` and `claude/hooks/tests/`) — reporting pass/fail/skip and exiting non-zero iff any failed. A zero-Python-test discovery (broken `REPO_ROOT`/glob) is a loud failure, never a silent green. `--list` prints what would run without running it; `--timeout` overrides the 300 s per-test cap; `--max-retries` (default 2) re-runs a failing test file that many additional times before counting it a final failure — absorbs transient Windows-runner resource contention without masking a real regression, since a deterministic failure still fails on every retry; `0` disables retries and every retry is visibly printed (`RETRY` lines, `[retried Nx]`, a `Retried:` summary line), never silently folded into a plain `PASS` (dev-env#994, [ADR-134](adr/134-run-hook-tests-retry-mechanism.md)). Runner-skips only `test_pyw_stdio.py` (a real-`pyw` Windows-subsystem stdio probe a non-interactive runner can't host — run it locally) and passes each bash gate's own `SKIP:` self-exit (no shellcheck, unauthenticated gh) through as non-failing. The engine behind the `hook-tests` CI workflow (`.github/workflows/hook-tests.yml`, `windows-latest`, `pull_request`). See Script verification suite below. [ADR-103](adr/103-shared-hookout-emitter.md) |
| `retro-chain-status.py` | `py -3 retro-chain-status.py --repo <owner/repo> [--repo <owner/repo> ...] [--journal-repo PATH]` | Read-only, per-repo classifier for the retro-action chained-tile backlog (dev-env#967): reports one of `ALIVE` / `UNRESOLVED` / `NO_QUEUE_FOUND` / `QUEUE_EXHAUSTED` / `ALL_TILED` / `AMBIGUOUS` / `NEEDS_REFILL` per repo. Never mutates anything; a per-repo failure (including a total `gh` transport failure while fetching queue issues) lands in that repo's own `{"status": "ERROR", "error": "..."}` entry rather than aborting the batch or masquerading as a false `NO_QUEUE_FOUND`. Identifies the queue issue **structurally** (the open, `retro-action`-labeled issue whose body contains real `- [ ]`/`* [ ]`/`+ [ ]` checklist lines, not an "Escalations" bullet) rather than by "newest labeled issue," since a repo can carry several open queue issues at once (career-playbook: five). Flags AMBIGUOUS rather than guessing when an untagged shard was spawned on/after the most recent chain-tagged shard's own `spawned` date (or the queue's `createdAt` only when no chain shard exists yet for this queue) — narrower than "since the queue was created" unconditionally, which would flag routine tile activity across a queue issue's entire ~2-week life. Engine behind the `retro-chain-refill` skill. [ADR-131](adr/131-retro-chain-idempotent-refill.md) |

`prune-merged-worktrees.py`, `reclaim-worktree-disk.py`, and `reconcile-project-board.py` above all discover repos for their `--scan-dir` mode via the shared `find_git_repos()` helper in `claude/scripts/_repo_scan.py` — a non-invoked library module (like `_hookio.py` / `_worktree_liveness.py` / `_journal_shards.py` / `_hookutil.py` / `_hookout.py` / `_repo_target.py`, none of which get their own table row) extracted from three near-identical copies. [ADR-072](adr/072-shared-repo-scan-module.md)

`post-tool-use.py` (PostToolUse hooks above) and `reconcile-project-board.py` above shared two more
near-identical copies before dev-env#454: the Claude-managed-worktree canonicalization regex (now
`canonical_root_from_worktree` / `canonical_repo_root` in `claude/scripts/_worktree_canon.py`, which
preserves each caller's own no-match contract — `None` for post-tool-use.py's fallback-chain check,
passthrough for reconcile-project-board.py's always-a-real-path caller) and the `gh project item-add`
subprocess wrapper (now `add_to_project` in `claude/scripts/_gh_project.py`, reconciled onto the
superset `(item_id, stderr)` return shape with `encoding="utf-8"` always applied — a deliberate fix for
post-tool-use.py's call site, which previously decoded with the OS default locale). Two more
non-invoked library modules in the same line as `_repo_scan.py` above. [ADR-073](adr/073-shared-worktree-canon-gh-project-modules.md)
`_gh_project.py` also now owns a best-effort item-ID cache (dev-env#1057,
[ADR-141](adr/141-project-item-id-creation-time-cache.md)) — `read_item_cache` /
`write_item_cache_entry` / `write_item_cache_entries` (single-entry vs. one-atomic-write-per-batch)
/ `lookup_cached_item_id` / `evict_item_cache_entry` (caller-driven invalidation on a confirmed-dead
ID), consulted by `add_to_project` on every successful add, `reconcile-project-board.py`'s sweep
(opportunistic full backfill, gated behind `not dry_run`), `get-project-item.sh`, and
`post-pr-merge-project.py`'s `find_project_item`, all keyed
`"<project-owner>/<project-number>|<owner>/<repo>#<number>"` (lower-cased) against
`C:/Users/brown/.claude/scratch/project-item-cache.json`.
`_worktree_canon.py`'s regex additionally recognizes the sibling-directory `<repo>-worktrees/<name>`
worktree convention alongside the original nested `.claude/worktrees/<name>` shape (dev-env#760, see
ADR-071 Amendment 5 for the full rationale, shared across all three files that independently define this
pattern).

`usage-snapshot.py` (PostToolUse hooks above) is a third consumer of `_worktree_canon.py`'s
worktree-resolution capability — its `find_session_jsonl()` used to hardcode its own
nested-convention-only marker check instead of importing the shared resolver, so a
sibling-convention cwd fell through to a full project-directory scan instead of the
direct canonical-retry step. Fixed the same way `post-tool-use.py` already resolves it:
import `canonical_root_from_worktree` and delegate directly, no regex change (dev-env#775,
see [ADR-073](adr/073-shared-worktree-canon-gh-project-modules.md) Amendment 1 for the
full rationale, including how `reconcile-project-board.py` differs — it consumes the
module via the separate `canonical_repo_root` wrapper, not this function directly).

`pre-tool-use-canonical-mutate-guard.py`, `journal-canonical-guard.py`,
`pre-tool-use-journal-draft-worktree-guard.py`, and `pre-tool-use-worktree-path-check.py`
(all above) each independently duplicated the "resolve the engineering-journal canonical
path from an env-var override or a hardcoded default" pattern, three of the four also
duplicating a normalize-for-comparison scheme. Single-sourced in
`claude/scripts/_journal_canon.py` (`resolve_journal_path` for the raw, unnormalized value;
`normalize_journal_path` for the one canonical equality-comparison normalization,
`os.path.normcase(os.path.normpath(...))` — chosen over the `.replace/rstrip/lower` scheme
two hooks used because it also collapses `.`/`..` segments and repeated separators). Each
hook keeps its own env-var name and local constant shape (frozenset / `Path` / str / str)
for backward compatibility with its own test suite — another non-invoked library module in
the same line as `_repo_scan.py`/`_worktree_canon.py` above. [ADR-133](adr/133-shared-journal-canon-module.md)

### Script verification suite

Execution-level checks for the shell scripts themselves, run from the dev-env `## Testing`
section (the canonical index of when to run each; full per-item behavioral detail lives in
[`docs/TESTING.md`](TESTING.md) under the same item numbers, ADR-114). `bash -n` catches only syntax — these catch
runtime and environment bugs it misses, the motivating case being [dev-env#334](https://github.com/brownm09/dev-env/issues/334)
(a path-resolution bug that parsed cleanly yet failed on every run).

The table below is a curated subset (the files with the least self-explanatory behavior). For a
complete, one-line-per-file index of every file in `claude/scripts/tests/` — grouped by shared
module, structural gate, or which hook/script each test covers — see
[`claude/scripts/tests/README.md`](../claude/scripts/tests/README.md)
([dev-env#822](https://github.com/brownm09/dev-env/issues/822)).

**Running the whole suite at once.** `py -3 claude/scripts/run-hook-tests.py` (Utilities table
above) discovers every `test_*.py` and every bash `*.sh` gate across both test directories
(`claude/scripts/tests/` and `claude/hooks/tests/` — the same dir set for Python and bash, so a test
is never silently missed by living in the "wrong" one), runs each as a subprocess, and exits
non-zero iff any failed — the same set the dev-env `## Testing` section enumerates by hand, kept in
sync by glob discovery rather than a maintained list. It runner-skips only `test_pyw_stdio.py` (a
real-`pyw` Windows-subsystem stdio probe a non-interactive CI runner can't host faithfully; run it
locally) and treats a bash gate's own `SKIP:` self-exit (no shellcheck, unauthenticated gh) as
non-failing. This runner is the engine behind **`.github/workflows/hook-tests.yml`** — a
`windows-latest`, `pull_request`-only GitHub Actions job (faithful to `py -3` / Git Bash / cp1252,
[ADR-007](adr/007-hook-command-invocation.md)) that gates every PR on the full suite. `pull_request`
only, never on pushes to `main`, per the same scoping lesson the lockfile drift gate learned
(dev-env `CLAUDE.md` → Dependency and lockfile policy). The workflow sets a throwaway git identity
so the many tests that spin up fixture repos can commit, and grants no gh token, so the network- and
shellcheck-dependent gates self-skip. Unlike shellcheck (no admin rights to `choco install` on a
hosted runner), pylint is a plain pip package — the workflow installs `pylint==4.0.6` explicitly, so
`run-pylint-unreachable.sh` runs for real in CI instead of self-skipping ([ADR-112](adr/112-unreachable-code-lint-check.md)).
The runner's own pure helpers are unit-tested by
`tests/test_run_hook_tests.py` (below); the runner's end-to-end acceptance test is the first green
CI run ([ADR-103](adr/103-shared-hookout-emitter.md)).

| Script | Invocation | What it does |
|--------|-----------|-------------|
| `tests/check-script-path-hygiene.sh` | `bash claude/scripts/tests/check-script-path-hygiene.sh` | Lints for the #334 class — a `$HOME`-rooted scratch/temp path passed to `node`, which Git Bash and Node-on-Windows resolve to different files. Scripts must use the literal `C:/Users/brown/.claude/scratch`. Hermetic; comment mentions of `$HOME` are ignored. Exit 1 on any offender. |
| `tests/check-remote-read-hygiene.sh` | `bash claude/scripts/tests/check-remote-read-hygiene.sh` | Lints for the [#602](https://github.com/brownm09/dev-env/issues/602) / [#877](https://github.com/brownm09/dev-env/issues/877) class — a `git show <ref>:<path>` paired with `2>/dev/null`, where MSYS mangling makes git's swallowed `fatal:` indistinguishable from "file absent". Scans every tracked file under `claude/` except the two dedicated test directories (a gate asserting a pattern is absent necessarily contains it — the ADR-116 precedent); keys on the co-occurrence, so the sanctioned `MSYS_NO_PATHCONV=1` form never trips. Hermetic; comment/blockquote mentions ignored. Exit 1 on any offender. Verify changes to it with the file **staged** — `git ls-files` cannot see an untracked script. See [ADR-120](adr/120-review-skill-absence-checks-over-api.md). |
| `tests/test-get-project-item.sh` | `bash claude/scripts/tests/test-get-project-item.sh` | Smoke-tests `get-project-item.sh` end-to-end: a hermetic Test 0 proves a cache hit needs zero `gh` calls (by pointing `GH_CONFIG_DIR` at an empty dir and confirming the script still succeeds — dev-env#1057), running unconditionally before the rest of the suite's network preflight. The remaining tests are network-dependent: asserts a known issue resolves to a `PVTI_` id via the fallback fetch (and that the fetch populates the cache), the no-match path exits 1 with a diagnostic, and the temp file is cleaned up. SKIPs (exit 0) when `gh` is unauthenticated/offline. |
| `tests/run-shellcheck.sh` | `bash claude/scripts/tests/run-shellcheck.sh` | Runs shellcheck over all repo shell scripts/hooks. Blocking at `--severity=error` (tree is error-clean as of 2026-06-07); warnings/info printed advisorily. SKIPs (exit 0) with an install hint when shellcheck is absent — set `SHELLCHECK_BIN` to a [portable binary](https://github.com/koalaman/shellcheck/releases) to run it. |
| `tests/run-pylint-unreachable.sh` | `bash claude/scripts/tests/run-pylint-unreachable.sh` | Runs pylint's `unreachable` (W0101) check alone (`--disable=all --enable=unreachable`) over `claude/scripts/*.py` and `claude/scripts/tests/*.py` — a pure control-flow dead-code check, independent of type annotations, catching the dev-env#813 class of bug (a trailing `return` that could never execute). mypy's `--warn-unreachable` was rejected: it skips untyped function bodies by default and there is no clean way to isolate just unreachability without either a mypy-clean tree or grep-filtering unrelated type errors; ruff has no unreachable-code rule at all. SKIPs (exit 0) with an install hint when pylint is absent (tries `py -3 -m pylint`, falls back to `python -m pylint`) — but unlike shellcheck, CI installs `pylint==4.0.6` explicitly, so it is NOT self-skipped there. Widened from its original `claude/scripts/*.py`-only scope (dev-env#815 / PR #818) to also cover `claude/scripts/tests/*.py` by dev-env#821, a follow-up ADR-112 explicitly deferred. [ADR-112](adr/112-unreachable-code-lint-check.md) |
| `tests/test_validate_manifest.py` | `py -3 claude/scripts/tests/test_validate_manifest.py` | Exercises the pure `missing_required_fields`, `find_entries_missing_fields`, and `parse_manifest_text` helpers in `validate-manifest.py` offline (no disk, no network, no subprocess): pins the all-five-fields-present case, each individually absent field returned in canonical order, non-dict entries treated as missing every field, `find_entries_missing_fields` order preservation and filtering, blank-line skipping, single-object ADR-056 shards, legacy multi-line manifests, invalid JSON, and JSON non-objects. `main()` is not covered (pure-helper convention). (dev-env [#423](https://github.com/brownm09/dev-env/issues/423)) |
| `tests/test-merge-stale-pr.sh` | `bash claude/scripts/tests/test-merge-stale-pr.sh` | Drives the real `merge-stale-pr.sh` against throwaway fixture repos (a bare "origin" + a working clone standing in for the shared engineering-journal checkout) with `gh` stubbed — no network, no auth. Asserts the Step 4 orphaned-draft commit's explicit pathspec ([dev-env#461](https://github.com/brownm09/dev-env/pull/461)) never sweeps in a file already staged by a simulated concurrent session; that a clean branch with no orphaned drafts skips Step 4 without a spurious commit and runs to completion; that multiple orphaned drafts across directories are all committed (guards `"${DRAFT_FILES[@]}"` array handling); and that a missing composed-journal file plus a declined prompt aborts before any mutation. Rebase and push run for real against the fixture remote; only `gh pr view`/`gh pr merge` are stubbed. (dev-env [#463](https://github.com/brownm09/dev-env/issues/463)) |
| `tests/test-journal-compose-replay.sh` | `bash claude/scripts/tests/test-journal-compose-replay.sh` | Drives the real `journal-compose-replay.sh` against throwaway fixture repos (`mktemp -d`, with `git update-ref refs/remotes/origin/main` standing in for a remote — no network, no `gh`). Fixture A pins the mechanical paths and a disjoint 3-way merge in which **both** sides' edits survive; fixture B pins the contested ones (overlapping `M` — the literal [#890](https://github.com/brownm09/dev-env/issues/890) shape — plus add/add, delete/modify, and a shard `origin/main` deleted), each asserted to leave `origin/main`'s content on disk with exit 2; fixture C pins exit 1 on every precondition failure. Both fixtures grep the tree for conflict markers, and all set `core.autocrlf true` so the blob-vs-work-tree line-ending trap stays covered on every platform. |
| `tests/test-setup-link-loop.sh` | `bash claude/scripts/tests/test-setup-link-loop.sh` | Sources `setup.sh` (a sourcing guard around its OS-dispatch block makes this safe) with `win_link`/`ln` stubbed to a call log, and runs the extracted `link_claude_windows()`/`link_claude_unix()` functions against a throwaway `$HOME` — no Administrator/Developer Mode privilege needed, no real `~/.claude` or global git config touched. Pins the shared `CLAUDE_FILE_LINKS`/`CLAUDE_DIR_LINKS` enumeration and each function's exact 8-target call sequence (file links, dir links, the `routines` junction, `~/bin`); also confirms the unstubbed `mkdir -p` calls create `~/.claude`/`~/.claude/scratch` for real. `setup_windows()`'s UAC elevation gate, the soft-prereq warnings, and `win_link`'s actual `cygpath`/`mklink` invocation are out of scope by design. (dev-env [#614](https://github.com/brownm09/dev-env/issues/614)) |
| `tests/test_run_hook_tests.py` | `py -3 claude/scripts/tests/test_run_hook_tests.py` | Exercises `run-hook-tests.py`'s pure helpers offline (tempfile fixtures; no subprocess/network): `discover_python_tests`/`discover_bash_tests` (glob + `test_`-prefix / `_`-exclusion filtering, multi-dir, missing-dir), `runner_skip_reason`/`SKIP_TESTS` (the pinned single-entry runner-skip list), `_command_for` (interpreter argv incl. the bash-missing and non-test-suffix cases), and `classify_result` (pass / self-skip / fail, with a non-zero exit beating a `SKIP:` marker). `main`/`_run_one` (which shell out) are not covered — the runner's end-to-end acceptance test is the first green CI run. (dev-env [#721](https://github.com/brownm09/dev-env/issues/721)) |

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

The active defaults in `claude/settings.shared.json`:

| Key | Value | Effect |
|-----|-------|--------|
| `model` | `claude-sonnet-4-6` | Default model for all session phases. See [ADR-025](adr/025-default-plan-mode.md). |
| `permissions.defaultMode` | `plan` | **Fresh local CLI sessions** start in plan mode — no edits until the user approves a plan; override per-session with Shift+Tab. **This does not apply to Desktop/web-app or spawn-task / SDK-launched sessions:** the platform starts those in `bypassPermissions` with a startup flag that overrides `defaultMode` *by design*, so they begin off-plan regardless of this setting — `settings.json` has no lever over it, and restarting does not change it. This is expected, not a broken hook; the `session-mode-prompt` hook and `session-mode-report.py` (above) audit it. To start such a session in plan, Shift+Tab at the first prompt. See [ADR-025](adr/025-default-plan-mode.md). |
| `effortLevel` | `max` | Applies to all model tiers. Lower to `low`/`medium`/`high`/`xhigh` per-session for lighter-weight tasks. |
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
instead of waiting for the 4am run:

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

**Two different outcomes depending on whether Prevention step 1 was followed.** The
unrecoverable-orphan case above happens specifically when the parent is merged with
`--delete-branch` (or the branch is otherwise deleted while the child still points at it as its
base) *before* the child is retargeted. If Prevention step 1 was followed instead — parent merged
with plain `--squash`, no `--delete-branch` — the child PR is **not** auto-closed; it stays open,
and GitHub auto-retargets its base to `main` on its own once the parent branch is later deleted (by
a separate `gh api -X DELETE .../git/refs/heads/<branch>` call, or by a repo's own
auto-delete-branch-on-merge setting). The child still goes `CONFLICTING`/`DIRTY` at that point —
same 3-way-merge symptom as the orphan case, since `main` now carries the parent's squashed content
while the child branch still carries the parent's original commits underneath its own — but
recovery is much simpler, because the PR object itself was never lost:

```bash
git fetch origin main
git rebase origin/main   # no --onto, no SHA-hunting needed
```

Git's patch-id matching recognizes the parent's now-squashed commits as content-identical to what's
already on `main` (`dropping <sha> ... -- patch contents already upstream`) and drops them on its
own, leaving only the child's own commit(s) — no need to locate `<parent-tip-SHA>` by hand. Fetch
again and force-push with `--force-with-lease` (see the bare-`--force` runbook below for why the
fetch has to come first), and the *same* PR goes back to `CLEAN`/`MERGEABLE` — no new PR needed,
unlike the orphan case above.

Motivating incident: career-playbook [#587](https://github.com/brownm09/career-playbook/pull/587)
(Step 4.7) / [#591](https://github.com/brownm09/career-playbook/pull/591) (Step 4.8, which superseded
the orphaned #588) for the orphan case; career-playbook [#923](https://github.com/brownm09/career-playbook/pull/923)
stacked on parent [#878](https://github.com/brownm09/career-playbook/pull/878) (2026-07-27) for the
survives-and-simple-rebase case — full incident trace: [dev-env#457](https://github.com/brownm09/dev-env/issues/457).

### Bare `--force` after rebase auto-closes any open PR on the target branch

**Trigger.** After a `git rebase origin/main`, the remote-tracking ref (`refs/remotes/origin/<branch>`)
is not updated. If the remote branch has also advanced since the last fetch — a push from another
machine or a concurrent collaborator push — running `git push --force-with-lease` rejects with
`(stale info)`: the lease comparison (local tracking ref vs. actual remote tip) finds a mismatch.
Falling back to bare `git push origin HEAD:<branch> --force` (or `git push --force`) succeeds at the
wire level, but GitHub fires a `head_ref_deleted` event for the PR's head branch when processing the
force-push, which triggers GitHub's PR auto-close logic — even though the branch itself still exists
at the new SHA. The inferred event-level mechanism is that GitHub may process a non-fast-forward force-push whose
new tip shares no common ancestry with the previous tip (i.e., a fully-disjoint rewrite produced by
`git rebase`) as equivalent to a branch delete+recreate for event-firing purposes (not officially
documented by GitHub; inferred from the timeline correlation).
(Confirmed from the GitHub API event timeline of the win11-init-tools incident: `closed` at
03:55:11Z, `head_ref_deleted` at 03:55:12Z, one second later; see
[dev-env#724](https://github.com/brownm09/dev-env/issues/724).)
The result: `mergedAt: null`, `mergeCommit: null`, and `gh pr reopen` fails.

**Symptom.**

```
! [rejected]  HEAD -> <branch> (stale info)
error: failed to push some refs
```

followed by a bare `--force` push that exits 0, and then:

```bash
gh pr view <N> --json state,mergedAt,mergeCommit
# {"state":"CLOSED","mergedAt":null,"mergeCommit":null}
gh pr reopen <N>
# Could not open the pull request: <N>
```

**Diagnosis.** The `(stale info)` rejection from `--force-with-lease` means the local remote-tracking ref
(`refs/remotes/origin/<branch>`) differs from what the remote actually holds — either because the remote
advanced since the last fetch (a push from another machine or a concurrent collaborator push), or because
the tracking ref was simply never refreshed. Both cases look identical in the rejection message. After
running `git fetch origin`, inspect `git log HEAD..origin/<branch>` to check for concurrent commits
before retrying. Use this sequence instead of resorting to bare `--force`:

```bash
git fetch origin                  # update the tracking ref
git log origin/<branch>..HEAD     # should show only your rebased commits; empty = nothing to push
git push --force-with-lease       # retry now — should succeed
```

**Fix (always use this sequence after a rebase).**

```bash
git rebase origin/main            # rewrites history; tracking ref becomes stale
git fetch origin                  # refreshes the remote-tracking ref to match the actual remote
git push --force-with-lease       # now the lease check passes; push succeeds; PR stays open
```

Never use bare `git push --force` / `git push origin HEAD:<branch> --force` as a fallback for a
`--force-with-lease` stale-info rejection. The stale-info rejection is a solvable local state problem,
not a signal to bypass the safety check.

**Recovery — PR already auto-closed.**

`gh pr reopen <N>` fails unconditionally when the PR was auto-closed by a `head_ref_deleted` event
(GitHub does not allow reopening a PR whose head branch has been deleted or treated as deleted).

**Why it blocks:** GitHub checks whether the current branch head is a descendant of the SHA
the branch held when the PR was closed. After a rebase, the rebased commits are disjoint
from the original commits, so this check always fails (isaacs/github#361, GitHub staff).

**Reopen workaround** (avoids creating a replacement PR):
1. Find the close-time SHA from `git reflog show <branch-name>` (the pre-rebase tip) or from the PR's Commits tab on GitHub (GitHub preserves commit history on closed PRs).
2. `git push -f origin <old-sha>:branch-name` (restores the branch to the close-time SHA).
3. Reopen the PR via `gh pr reopen <N>` or from the GitHub UI (now unblocked — head IS a descendant of itself).
4. `git push -f origin <rebased-sha>:branch-name` (pushes your actual work).

**Alternatively, create a replacement PR** (when the close-time SHA is unavailable):

1. Confirm the PR is truly auto-closed (not intentionally closed):
   ```bash
   gh pr view <N> --json state,closedAt,mergedAt,mergeCommit
   # state=CLOSED, mergedAt=null, mergeCommit=null confirms the auto-close
   ```
2. The branch itself is fine — the commits are still intact on the pushed branch; only the PR object
   is unrecoverable. Create a replacement PR:
   ```bash
   gh pr create --head <branch> --base main \
     --title "<same title>" \
     --body $'<same body>\n\nReplaces #N (auto-closed by bare --force after rebase; see <original PR URL>)'
   ```
3. Reference the original PR number in the new body to preserve review history context. The old PR
   number is permanently gone — do not try to reuse it.

Motivating incident: win11-init-tools [PR #34](https://github.com/brownm09/win11-init-tools/pull/34)
was auto-closed and replaced by [PR #46](https://github.com/brownm09/win11-init-tools/pull/46)
(2026-07-11). Tracking issue: [dev-env#724](https://github.com/brownm09/dev-env/issues/724). Summary
rule in [`claude/CLAUDE.md`](../claude/CLAUDE.md) → Git Workflow → "Bare `--force` after rebase
auto-closes any open PR on the target branch".

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

**Recovery** (validated 2026-06-04; `--force` caveat corrected 2026-07-13 — dev-env#751; recipe
re-derived from a throwaway-fixture matrix and single-sourced 2026-07-22 — dev-env#862).

The block below is **pinned to `claude/scripts/_worktree_recovery.py`'s `RECOVERY_STEPS`** — the same
definition `pre-tool-use-worktree-path-check.py` renders into its block message, so a stuck session and
this runbook can never again disagree. `claude/scripts/tests/test_worktree_recovery.py` fails if they
drift. **Edit the module, not this block.** [ADR-116](adr/116-single-source-worktree-recovery-recipe.md)

Run **steps 1–4 in order** and stop as soon as the worktree is live again. The last two commands
are a **conditional trailer**, not part of the sequence — run them only if step 4 fails:

```bash
# 1. Non-destructive and preserves uncommitted work, so always try it first.
#    Do NOT judge it by its exit code or its message: it exits 1 and prints
#    `error: unable to locate repository; .git file broken` BOTH when it succeeds and
#    when it cannot help. Step 2 is the only reliable signal.
git -C "<canonical>" worktree repair "<orphan>"

# 2. Verification, not a fix — and the real decision point. Prints <orphan> -> recovered,
#    you are done. ANYTHING ELSE -> still orphaned, continue: that includes the canonical
#    root, and includes `fatal: not a git repository` (what a sibling-convention orphan
#    prints, since it sits outside any repo for git to walk up to).
git -C "<orphan>" rev-parse --show-toplevel

# 3. Drop the stale registration, else step 4 fails with
#    `missing but already registered worktree`.
git -C "<canonical>" worktree prune

# 4. Plain add — NOT --force/-f (see "Why not --force" below).
git -C "<canonical>" worktree add "<orphan>" <branch>

# --- Conditional trailer: ONLY if step 4 died with `already exists`. Not part of the
#     sequence above. Run both, then repeat step 4. ---

# 5. Capture BEFORE emptying. Step 1 cannot recover the shape where BOTH the `.git` link
#    and the admin dir are gone — at this point the orphan's uncommitted work exists
#    nowhere else. (PowerShell: Copy-Item -Recurse <orphan> <orphan>.salvage)
cp -r "<orphan>" "<orphan>.salvage"

# 6. Git Bash. IRREVERSIBLE. Empties the directory IN PLACE, then repeat step 4.
#    In PowerShell `find` is find.exe (the text-search tool) and fails with
#    `FIND: Parameter format not correct` — use
#    `Get-ChildItem -Force <orphan> | Remove-Item -Recurse -Force` instead.
find "<orphan>" -mindepth 1 -delete

npm install                                          # from the recreated worktree, no cd
```

`<branch>` is typically `claude/<worktree-name>`; confirm with `git branch -a`.

**Why not `--force`, and why not `rm -rf`** (all verified against git 2.37.1.windows.1 on throwaway
fixtures, dev-env#862 — the earlier guidance was wrong on both counts):

- **`git worktree add --force` (or `-f`) does not help with a leftover directory.** git checks
  `file_exists(path) && !is_empty_dir(path)` and dies `fatal: '<path>' already exists` **before** it
  ever consults the flag; it overrides only the *stale-registration* and
  *branch-checked-out-elsewhere* safeguards. It genuinely does fix the narrow case of an **empty**
  directory that is still registered — which is why the original recipe looked right — but that is the
  one case `worktree prune` already handles. Emptying the directory (step 6) is the real fix, and then
  a plain `add` suffices.
- **`rm -rf <orphan>` is the wrong removal.** The orphan is typically the blocked session's *own cwd* —
  the shell cwd resets back to it between Bash calls — and a held handle fails with
  `Device or resource busy`, which cannot be worked around from inside the session that needs it.
  Emptying in place (`find … -mindepth 1 -delete`) leaves the directory itself in place, so it works
  whether or not the directory can be removed, and keeps the shell's cwd valid.
- **Step 1 does not always save you, which is why step 5 exists.** `worktree repair` can only relink
  when one side of the link survived. When *both* the `.git` link and
  `<canonical>/.git/worktrees/<name>/` are gone, it cannot help — and then step 6 destroys uncommitted
  work that exists nowhere else. Capture it first. (This restores, in a form that works when the orphan
  is your own cwd, the inspect-before-you-delete gate the pre-2026-07-22 runbook had.)
- **Step 1's exit code tells you nothing.** It exits 1 whether it succeeded or not, and the
  both-sides-gone failure prints the *same* `.git file broken` message as the success case. Step 2 is
  the only reliable signal — do not skip it.
- **No `git checkout main` step.** The old recipe opened with `git -C <main-repo-path> checkout main`
  "to free the branch". `worktree prune` already frees it, and that command is now hard-blocked by
  `pre-tool-use-canonical-mutate-guard.py` ([ADR-071](adr/071-canonical-checkout-mutate-guard-hook.md))
  — a `-C` redirect of a mutating verb at a canonical root. `worktree repair` / `prune` / `add` are
  **not** blocked, so the sequence above needs no `ALLOW_CANONICAL_MUTATE=1` override.

**Root cause.** Disk pressure from many worktrees each carrying a full monorepo `node_modules`
(dev-env#306). Complements the orphan-liveness guard of
[ADR-024](adr/024-worktree-path-guard-hook.md) with the recovery procedure; decisions:
[ADR-066](adr/066-worktree-session-safety-rules.md),
[ADR-116](adr/116-single-source-worktree-recovery-recipe.md).

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

**Prevention.** A `PreToolUse(Bash/PowerShell)` hook (`pre-tool-use-canonical-mutate-guard.py`,
dev-env#620/ADR-071 Amendment 4) now hard-blocks git-mutating commands issued with cwd at a
canonical (non-worktree) root — isolate into a worktree before this recovery sequence is ever
needed. This runbook is the fallback for what the hook can't catch: a manual terminal session
outside Claude Code, or a bare `cd`/`Set-Location` into the canonical root from elsewhere (the
hook's sole remaining documented v1 gap along that axis — a `-C`/`--git-dir`/
`--work-tree` redirect into a canonical root is now caught, dev-env#576/ADR-071 Amendment 2). A
*different* axis — a worktree-shaped cwd that only looks like a worktree without being a live,
registered one (e.g. an orphaned directory left behind by an incomplete `git worktree add`/`remove`) —
is also now closed: the hook no longer trusts that shape alone, so an orphaned worktree directory that
git resolves up to a real canonical root is correctly blocked rather than silently exempted
(dev-env#749, ADR-071 Amendment 3). The hook (and `pre-tool-use-worktree-path-check.py`/ADR-024,
`_worktree_canon.py`/ADR-073) also recognize a second worktree-path convention — the sibling-directory
`<repo>-worktrees/<name>` shape reached via manual `git worktree add`, alongside the nested
`.claude/worktrees/<name>` (`EnterWorktree`) shape — so a genuine live worktree at either shape gets the
same zero-friction treatment (dev-env#760, ADR-071 Amendment 5). A further gap in that same shape-only
trust remained even after Amendment 3: the two exemption checks that examine an already git-*resolved*
root (cwd's own, or a `-C`/`--git-dir`/`--work-tree` redirect target's) still re-checked the path STRING
alone, so a genuine, independently-cloned canonical checkout that merely happens to sit at a
worktree-shaped path was wrongly exempted. Both now confirm via `git worktree list --porcelain`
membership instead — is the resolved root a LINKED (non-canonical) entry of its own repository, not just
shaped like one — falling back to the shape check only when git itself can't answer (dev-env#774, ADR-071
Amendment 6). `pre-tool-use-worktree-path-check.py` gained the mirror-image fix for its own regex
candidate (gap (b): a repo literally named `<x>-worktrees` broke it) in the same PR — see ADR-024's own
addendum. Decision: [ADR-071](adr/071-canonical-checkout-mutate-guard-hook.md).

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

**Every genuine tile also gets a tracking issue.** A tile is still *ephemeral* — chip IDs are not
persisted across app restarts, and a tile becomes real work only when the user clicks it. Under
[ADR-046](adr/046-post-merge-followup-tiles.md)'s original default, a GitHub issue was filed only for
a follow-up that "must" be tracked; [ADR-094](adr/094-tile-tables-and-issue-per-tile.md) overrides
that — every genuine tile now gets a tracking issue filed alongside it, in the same repo, referenced
in the tile prompt, giving the ephemeral chip a durable, linkable, status-trackable anchor. The
session also closes with an end-of-session table of the tiles spawned (the third checkpoint below);
the tile, the issue, and the table are complementary, not redundant.

**Fallback where `spawn_task` is unavailable.** The tool is not present in every session (e.g. some
terminal CLI sessions). There, file a follow-up issue instead, so the capture still happens.

**Enforcement.** Two hooks back this checkpoint. `post-merge-tile-checkpoint.py`
([ADR-060](adr/060-post-merge-tile-checkpoint-hook.md)) is **command-keyed** — it fires the moment a
`gh pr merge` you run succeeds (as of dev-env#986, [ADR-050 Amendment 23](adr/050-shared-hookio-sibling-hook-fixes.md),
also the two-step REST merge fallback `gh api -X PUT .../pulls/N/merge`), but is still blind to
auto-merge, which has no command-text signature at all for a command-keyed hook to match on.
`stop-tile-enumeration-gate.py` ([ADR-088](adr/088-state-keyed-tile-enumeration-gate.md)) is
**state-keyed** — a Stop hook that scans the transcript and blocks the stop when a PR reached merged
state this session by *any* path but no tile-enumeration was recorded (a bare "no follow-ups" does not
satisfy it). The two are complementary: the command-keyed hook is the immediate nudge, the state-keyed
hook is the Stop-time verification that also covers auto-merge and still fires in background/SDK
sessions where every PostToolUse hook is inert ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)).

**A second checkpoint: dangling created issues.** `stop-tile-enumeration-gate.py` also fires
([ADR-092](adr/092-dangling-issue-tile-enumeration-gate.md)) when a `gh issue create` ran this
session and the created issue remains unresolved at Stop — not closed via a same-session merged PR's
Closes/Fixes/Resolves keyword, nor explicitly closed via `gh issue close`. This covers the
pure-investigation session that files well-scoped issues but implements and merges nothing, which
has no merge event for either of the two merge-keyed hooks above to key on. Both triggers share the
same enumeration/skip-override machinery — one recorded enumeration satisfies either or both, and a
session that both merges a PR and leaves an issue dangling gets one combined stderr message naming
both, not two separate blocking turns.

**A third, independent checkpoint: tiles spawned without a table.** ADR-094 (see below) overrides
ADR-046's tiles-are-capture-not-tracking default: every genuine tile now also gets a tracking issue,
and the session closes with a table under the stable heading `### Tiles spawned this session`.
`stop-tile-enumeration-gate.py` fires this third trigger ([ADR-094](adr/094-tile-tables-and-issue-per-tile.md)
addendum, dev-env#656) when a `spawn_task` tile was spawned this session but no assistant message
carries that heading. Unlike triggers (1)/(2), a bare spawn does **not** satisfy this trigger — the
spawn resolves the *enumeration* check those two share, but the table is a stricter, separate bar, so
a session can merge a PR, spawn a tile, and still see this trigger fire because the table itself was
never emitted.

**Three further checkpoints.** The narrative above predates them; the hooks table entry earlier in
this file carries each one's full detail. **(3b) A tile spawned without its shard**
([ADR-118](adr/118-tile-persistence-shards.md), dev-env#870) — trigger (3)'s sibling on the same
spawn, guarding a different loss: the table tells the *user* a tile exists; the shard is what lets
the *tile* be re-spawned once the chip dies. **(4) The deferral question**
([ADR-109](adr/109-tile-gate-deferral-question-trigger.md), dev-env#772) — a scheduling/permission
question asked about known follow-up work instead of tiling it; the one **advisory** trigger, since
its input is a bounded natural-language match rather than an objective fact. **(5) An unchained
merge** ([ADR-140](adr/140-unchained-merge-workstream-gate.md), dev-env#1044) — a PR merged in a
session whose *own opening prompt* named no follow-on work, with neither a `spawn_task` nor an
`AskUserQuestion` before the stop: the session that merges and then ends idle. It enforces the
mechanically checkable floor under [ADR-137](adr/137-proactive-tile-forward-chaining.md)'s prose
look-ahead rule — which that ADR deliberately left to prose, since "will this predictably surface a
next thread" is a semantic judgment no hook can detect — and its CLAUDE.md half defines what ADR-137
never did: what to do when *no* next thread is determinable (rank the open issues, offer the top 3
via `AskUserQuestion`, tile the choice — the one bounded carve-out to the otherwise absolute
"never ask, tile it" rule).

**Per-trigger sentinels.** Every trigger above fires/resolves via its own independent sentinel
file (`ADR-097`, dev-env#677), not one shared sentinel. Under the original shared design, whichever
trigger fired or resolved first silently suppressed evaluating the other two for the rest of the
session — including one whose own condition (e.g. a tile spawned in a later, separate turn) had not
even occurred yet. Splitting the sentinel per trigger fixes this while preserving the fast path where
a session with every trigger already resolved skips reading the transcript at all; the cheap
pre-filter that gates the full parse is likewise gated per trigger, so a resolved trigger's stale
transcript signal (e.g. `"merged"`, permanent once written) no longer forces a reparse on every
remaining Stop of the session.

Rationale, alternatives, and consequences: [ADR-046](adr/046-post-merge-followup-tiles.md) (the
merge checkpoint), [ADR-092](adr/092-dangling-issue-tile-enumeration-gate.md) (the dangling-issue
checkpoint), [ADR-094](adr/094-tile-tables-and-issue-per-tile.md) (the tile-table checkpoint),
[ADR-118](adr/118-tile-persistence-shards.md) (the tile-shard checkpoint),
[ADR-109](adr/109-tile-gate-deferral-question-trigger.md) (the deferral-question advisory),
[ADR-140](adr/140-unchained-merge-workstream-gate.md) (the unchained-merge checkpoint and its
open-issue workstream fallback),
[ADR-097](adr/097-per-trigger-tile-gate-sentinels.md) (the per-trigger sentinel correction, and its
amendment collapsing the five parallel structures into the `_TriggerSpec` table).

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

**The package-level signature — and the two lookalikes that are not it.** Truncation at *package*
granularity shows up as a package directory that exists, contains something, but has **no
`package.json` of its own** (`npm ls` reports it `invalid`). Measured across all 48 `node_modules`
trees on this machine on 2026-08-27, that shape never occurs in a healthy tree — but two harmless
shapes look similar enough to send an investigation the wrong way:

- **An empty package directory is usually fine.** npm creates one for every `optionalDependencies`
  platform variant it skips, so a healthy tree routinely carries 40–70 empty `@esbuild/<platform>`,
  `@rollup/<platform>` directories — up to 15% of all package directories. Only the *matching*
  platform's binary (e.g. `@esbuild/win32-x64`) is expected to be populated.
- **`@scope/.name-XXXXXXXX` means an install is running right now.** npm extracts to that staging
  name and renames on completion, so a live `npm install` transiently looks exactly like widespread
  truncation. Check for a running npm before concluding anything.

`worktree-npm-install.py` now detects the real signature automatically and repairs it with a gated
`npm ci` ([ADR-142](adr/142-node-modules-truncation-gate.md)); this paragraph is the manual read.

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
`prs_opened`/`prs_closed` specifically are also *read* (not schema-enforced) by
`stub-push-archive-reminder.py` to gate the post-stub-push archive reminder on a same-session PR still
being open ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) Amendment 1) — a third
consumer of this module, alongside the two schema gates above.

Write the shard with the **Write tool** — never `echo`/a heredoc/redirect, the anti-pattern
`pre-tool-use-journal-shell-write-guard.py` mechanically blocks for this path shape
([ADR-129](adr/129-journal-shell-write-guard.md)):

- **Path:** `C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`
- **Content** (the file's entire, one-line content):
  `{"stub":"sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md","topic":"<H2 heading>","tokens":{"input":N,"output":N,"cost":N},"prs_opened":[],"prs_closed":[]}`

- `prs_opened` / `prs_closed`: PR numbers opened / reviewed-or-merged this session (e.g., `[54]`); empty array if none.
  A PR number in `prs_opened` not also in `prs_closed` is read as still-open by
  `stub-push-archive-reminder.py`'s `has_unresolved_open_pr()` gate.
- `priorities` (optional): array surfaced on the top-level README "Start here" dashboard. Each entry:
  `label` (required, short title); `ref` (optional, `owner/repo#N` or freeform key used for dedupe);
  `why` (optional, one-sentence rationale). Example:
  `"priorities":[{"label":"Staging gate fix","ref":"lifting-logbook#346","why":"blocks next deploy"}]`.
  `/journal-compose` aggregates these across projects (deduped by `ref`, capped at 5) — see
  [ADR-032](adr/032-journal-start-here-dashboard.md).

**Updating after a merge (no shared-file edit).** Setting `prs_closed:[N]` after a same-session merge
rewrites **this session's own shard** — a single-object file no other session touches — so there is no
concurrency hazard and no surgical-edit dance. Use the **Edit tool** — replace the current
`"prs_closed":[...]` value with `"prs_closed":[<PR_NUMBER>]` in place. (Every write to this path now
goes through the Write/Edit tool uniformly across all four journal content-file kinds, mechanically
enforced by `pre-tool-use-journal-shell-write-guard.py`; the `node -e` recipe this section used to
prescribe is retired — see [ADR-129](adr/129-journal-shell-write-guard.md).)

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

**When a session opens PR #N:** write the shard with the **Write tool** — never `echo`/a
heredoc/redirect ([ADR-129](adr/129-journal-shell-write-guard.md)) — then commit it alongside the stub:

- **Path:** `C:/Users/brown/Git/engineering-journal/sessions/<project>/open-prs/<N>.json`
- **Content** (the file's entire content):
  `{"pr":<N>,"url":"<url>","topic":"<H2 heading from stub>","stub":"YYYY-MM-DD_HHMMSS.stub.md","opened":"YYYY-MM-DD"}`

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

### Tile shards (`sessions/<project>/tiles/<issue-number>.json`)

Persists a `spawn_task` tile's payload so a lost chip can be re-spawned after an app restart. Per
[ADR-118](adr/118-tile-persistence-shards.md), each tile is its **own** shard — one JSON object keyed by
the **paired GitHub issue number** (issue-per-tile, [ADR-094](adr/094-tile-tables-and-issue-per-tile.md),
guarantees one exists). Same numeric-filename layout as the open-PR shards above, read through the same
`_journal_shards` core (`iter_tile_shards`).

`<project>` is the tile's **target** project — derived from the tile's `cwd`, *not* the spawning session's
project — so a tile filed from one repo against another lands in the target's directory and is surfaced
there. As with open-PR shards, the bare number is unique within that directory and the repo is still
carried in `url`.

Schema:

```json
{"issue":868,"url":"https://github.com/brownm09/dev-env/issues/868","title":"<chip label, <=60 chars>","tldr":"<1-2 sentence tooltip>","prompt":"<full self-contained spawn_task prompt>","cwd":"C:/Users/brown/Git/dev-env","stub":"sessions/dev-env/YYYY-MM-DD_HHMMSS.stub.md","spawned":"YYYY-MM-DD"}
```

Seven fields are required: `issue`, `url`, `title`, `tldr`, `prompt`, `cwd`, `spawned`. The field list
lives in `TILE_REQUIRED_FIELDS` in `claude/scripts/_journal_schema.py`. `title`/`tldr`/`prompt`/`cwd`
are the four `spawn_task` arguments; together they are what makes an *exact* re-spawn possible.

**`cwd` is written with forward slashes** — `C:/Users/brown/Git/dev-env`, never
`C:\Users\brown\Git\dev-env`. This is a correctness rule, not a style one: a forward-slash path is
valid on Windows, valid in JSON, and contains no character that any escaping layer between a command
line and `JSON.stringify`/`json.dump` will consume, so it cannot be mangled in transit. `cwd` is the
only required field that is a filesystem path, and it decides *which repo the re-spawn happens in* —
a corrupt value does not degrade the payload, it voids it. `malformed_tile_fields` in
`_journal_schema.py` flags an implausible `cwd` at write time via the hook below, but that is the
backstop; the slash direction is what removes the failure mode. See
[ADR-118](adr/118-tile-persistence-shards.md) Amendment 4 and
[ADR-081](adr/081-write-time-journal-shard-validation-hook.md) Amendment 2 (incident: three live
shards, dev-env#904).

`stub` is **optional** (the manifest schema's `priorities` is the same shape). An open-PR shard is
always written by a session that also writes a stub, but a tile is not: the tiling rule fires the moment
a follow-up is identified, while the stub triggers are PR-open / PR-merge / report-generation — so a
session that tiles something in passing may legitimately write no stub at all, and requiring the field
would force it to invent a value. When present, `stub` must be **project-qualified**
(`sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`, the manifest convention) rather than the open-PR
shard's bare filename — a tile shard is filed under its *target* project, so the spawning session's stub
may live under a different one and a bare filename would not resolve. `malformed_tile_fields` flags a
present-but-unqualified `stub` at write time via the hook below (dev-env#907,
[ADR-081](adr/081-write-time-journal-shard-validation-hook.md) Amendment 3) — found live in
`sessions/career-playbook/tiles/849.json`, which carried a bare `"2026-07-23_021500.stub.md"`.

**`chain` is an optional field** — `{"queue_issue": "<url>", "seeded_by": "<string>"}` — present only
when the tile is one link in the retro-action chained-tile backlog burn-down mechanism (dev-env#967,
[ADR-131](adr/131-retro-chain-idempotent-refill.md)). `queue_issue` is the `retro-action` queue issue
this tile's work item was pulled from; `seeded_by` is a free-form label identifying what spawned this
link (a routine name + date, e.g. `retro-chain-backstop 2026-08-15`, or a human session, e.g.
`biweekly-retro 2026-08-09`). `retro-chain-status.py`'s classifier reads this field — alongside the
tile's own paired issue number and `spawned` date — to tell a live chain link apart from an ordinary,
unrelated tile shard when deciding whether a repo's chain needs a refill (`retro-chain-status.py`'s
AMBIGUOUS classification is exactly the "can't tell" case this field exists to narrow — see Utilities
below). Not added to `TILE_REQUIRED_FIELDS`: most shards, including every historical one written
before this field existed, are not chain tiles at all. See
[ADR-118](adr/118-tile-persistence-shards.md) Amendment 6.

**`task_id` is deliberately not stored.** Chip IDs do not survive an app restart (ADR-094), so persisting
one would save a value that is dead exactly when the shard is needed — this is ADR-094's rejected
"task_id record only" alternative. `malformed_tile_fields` flags a present `task_id` at write time via
the hook below (dev-env#907, [ADR-081](adr/081-write-time-journal-shard-validation-hook.md) Amendment
3) — the same live `849.json` shard also carried `"task_id": "task_cdc4d05c"`, simultaneously with the
bare `stub` above and passing every other check.

**When a session spawns a tile:** write the shard immediately after the `spawn_task` call, and commit it
alongside the stub (explicit per-file pathspec, never the bare `tiles/` directory).

**Do not build this JSON with `echo` (or any other shell redirect/heredoc).** Unlike every other shard,
`prompt` holds free prose — the whole `spawn_task` prompt — so string-interpolating it into
`echo '{...}'` breaks in three ways, two of them silent: a `"` or a Windows `\` path produces invalid
JSON that `iter_numeric_shards` skips without a word (the payload is lost precisely when a restart
needs it), and an apostrophe closes the shell's single-quoted string, making any following
metacharacters executable — tile prompts routinely quote text Claude did not author (issue bodies, `gh`
output, error text). Write it with the **Write tool** — the single method for this file (and the other
three journal content-file kinds), mechanically enforced by `pre-tool-use-journal-shell-write-guard.py`
([ADR-129](adr/129-journal-shell-write-guard.md)); the shell/heredoc recipe this section used to
prescribe is retired.

`tiles/` still needs to exist first — it exists for no project until its first tile, and the reconciler
deletes it again whenever a project's last shard is pruned, so the missing-directory case recurs, it is
not a one-time bootstrap:

```bash
mkdir -p "C:/Users/brown/Git/engineering-journal/sessions/<project>/tiles"
```

(or PowerShell `New-Item -ItemType Directory -Force "C:/Users/brown/Git/engineering-journal/sessions/<project>/tiles"`)
— this creates no content, so it stays a legitimate shell step, outside the new guard's scope. Then
write `sessions/<project>/tiles/<N>.json` with the Write tool, with this content:

```json
{"issue": <N>, "url": "<issue-url>", "title": "<chip label>", "tldr": "<tooltip>", "prompt": "<the full self-contained spawn_task prompt, verbatim>", "cwd": "<target repo path>", "stub": "sessions/<spawning-project>/YYYY-MM-DD_HHMMSS.stub.md", "spawned": "YYYY-MM-DD"}
```

The Write tool takes this content as a parameter, with no shell interpretation at all — the payload
never crosses a command-line boundary, so none of the three `echo` failure modes above can occur, and
none of the serializer-string-literal hazard the next section describes can occur either.

**"Use a serializer" was not by itself enough — mind the serializer's own string literals.** This is
now historical context: the rule above used to be read as being about `prompt`, and about the *shell*.
Both readings were too narrow. Every field also has to survive the string-literal layer of whatever
language a serializer is written in, and that layer is where the `node -e "…"` form silently destroyed
`cwd`:

```
node -e "…JSON.stringify({cwd: "C:\Users\brown\Git\dev-env"})…"
                                    ^^      ^^      ^^
              the JS literal eats \U and \G and turns \b into U+0008, yielding
              C:Users<U+0008>rownGitdev-env  — valid JSON, seven fields present, names no directory
```

Nothing downstream reports this: the shard exists, parses, keeps its numeric filename, and satisfies
every presence check, so it reads as healthy while the payload it exists to preserve is already gone.
Three shards were live in this state on 2026-07-23 (dev-env#904), written by two independent sessions.
The Write tool removes this hazard entirely, alongside the shell-quoting hazard above — its `content`
parameter is never passed through any string-literal layer (JS, Python, or otherwise) at all, so there
is no serializer left to mis-escape `cwd`. Writing `cwd` with forward slashes (see the schema notes
above) remains good practice regardless — a defense-in-depth pin, not the primary fix now that the
Write tool has removed the mechanism that caused the corruption in the first place.

**When the tile's work completes:** closing the paired issue is the signal. `reconcile-pending-tiles.py`
unlinks the shard of any tile whose issue it finds `CLOSED` at session start, and removes the `tiles/`
directory once its last shard is gone. A `gh` failure or an indeterminate state keeps the shard —
conservative, matching `reconcile-open-prs.py`.

**Who commits that deletion** ([dev-env#958](https://github.com/brownm09/dev-env/issues/958)/[#950](https://github.com/brownm09/dev-env/issues/950),
[ADR-118](adr/118-tile-persistence-shards.md) Amendment 5). The unlink above is a raw filesystem
delete — `reconcile-pending-tiles.py` never runs `git add`/`git commit` itself, for the identical
reasons its `reconcile-open-prs.py` sibling doesn't (an advisory hook that must fail open; a shared
git index every concurrent session in the canonical touches; it would be committing onto whatever
branch the canonical happens to hold; and the pass is suppressed outright while a merge is in
progress). Left alone, the deletion sits as an unstaged ` D` line in the shared canonical
indefinitely — exactly the shape dev-env#958 found live for three career-playbook tiles, one of
them still uncommitted roughly fifteen hours after its issue closed. Since that issue, the same
session-start pass also scans `git status --porcelain -- sessions` for an already-deleted tile
shard, recovers its `{issue, url}` from `git show HEAD:<path>` (the file is gone from the working
tree), and re-confirms the issue's state with **one `gh api repos/<repo>/issues/<n>` call per
candidate** — deliberately *not* folded into the batched per-repo lookup above, because an
orphaned deletion has no "recent by construction" guarantee: while it sits uncommitted the repo
keeps creating issues, and every one created pushes an already-resolved issue one position deeper
into that lookup's recency window. Four buckets, reported separately, never as one list: `closed`
(issue confirmed closed — a ready-to-run, shell-safety-validated `git add`/`git commit` pathspec),
`open` (anomaly — a still-open issue's shard was deleted — flagged, never recommended),
`unverified` (identity or state unconfirmable, or a filename/`issue` mismatch — never committed
blind), and `skipped` (beyond the probe budget, reported rather than silently dropped). A dirty
tile path that is *added or modified* (a concurrent session's in-flight shard) is reported
separately, hands-off, regardless of the above. The whole deletion-classification pass is
suppressed while the canonical is mid-merge. A deletion whose issue is confirmed **closed** is
**yours to commit immediately**, per the `claude/CLAUDE.md` Stub file workflow rule mirroring the
open-PR-shard exemption — see that rule for the exact commands.

**Reader requirements (implemented in `reconcile-pending-tiles.py`, [#869](https://github.com/brownm09/dev-env/issues/869)).**
`url` is a git-committed, cross-machine, free-form string that the reader parses to derive `--repo` for
its `gh` lookup. The open-PR precedent it is otherwise modeled on (`reconcile-open-prs.py`) does a bare
`urlparse(...).path.split("/")` with no host or character check, which is tolerable there but not here
(and is now doubly so: since dev-env#888 that hook interpolates the derived repo into a REST *path*,
`repos/<owner>/<repo>/pulls/<n>`, which cannot name a host at all — the `--repo` surface described
below is retired there, though every check remains required here):
the tile reconciler's remove branch **unlinks the shard**, so a mis-resolved lookup that returns
`CLOSED` destroys the payload. `gh --repo` also accepts a `HOST/OWNER/REPO` form, so a crafted path can
aim it at another host carrying the user's credentials. `repo_from_issue_url` therefore:

- requires an `https` scheme — a check on `netloc` alone would pass `ssh://github.com/o/r`;
- requires `urlparse(url).netloc == "github.com"`, compared case-insensitively (host names are
  case-insensitive per [RFC 3986 §3.2.2](https://www.rfc-editor.org/rfc/rfc3986#section-3.2.2), so this
  is normalization, not a relaxation — a `userinfo@` prefix, a port, or a subdomain all still fail,
  since the whole `netloc` must match);
- matches owner/repo against `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`, **and** rejects a `.` or `..`
  segment — both are spelled entirely from characters that class allows, so the regex alone accepts
  `https://github.com/../..` and hands `../..` to `gh --repo`;
- takes the issue number from the **filename** (`shard_number`), not from `url` — so even a URL that
  passes every check cannot redirect the lookup to a different issue in the same repo. A shard whose
  `issue` field contradicts its filename is treated as corrupt and skipped;
- **skips and keeps** the shard — never unlinks — when any check fails.

The scheme and dot-segment checks go beyond ADR-118's original three; both were found by this
function's own tests during implementation, and both fail closed (skip-and-keep), consistent with the
rest of the reader.

**Lookup batching.** State is resolved with **one paged read per repo** — `gh api
"repos/<owner>/<repo>/issues?state=all&per_page=100&page=<n>"` — not one lookup per shard: shards
accumulate un-pruned between reconciliations, so a per-shard lookup would fan out into N sequential
subprocess spawns. This supersedes ADR-118's Consequences note ("one `gh issue view` per pending
tile"). Pagination is bounded (2 pages) and stops as soon as every requested number resolves, the
page comes back short, or a row below the lowest requested number appears — so the normal case is a
single page, a pending tile's issue being recent by construction. An issue outside that window
resolves to `None` → kept and counted unresolved, never silently dropped. **The one place this
hook deliberately does *not* batch:** re-confirming an already-*deleted* shard's issue state (the
orphaned-deletion advisory below) uses a single-issue lookup per candidate instead, because an
orphaned deletion has no "recent by construction" guarantee — see "Who commits that deletion"
above and the Hooks-table entry in `## Hooks` for why.

**Lookup transport.** GraphQL (`gh issue list`) until
[#882](https://github.com/brownm09/dev-env/issues/882); now the REST `core` bucket, which is
5000/hr, near-untouched, and *not* what Projects v2 operations contend for — those have no REST
alternative at all ([#769](https://github.com/brownm09/dev-env/issues/769)). REST-only, with no
GraphQL fallback: one code path, and a `core` failure almost always means auth/network is down,
which GraphQL would not survive either. Three properties a future edit must preserve:

- **REST models pull requests as issues**, so rows carrying a `pull_request` key are dropped.
  Issues and PRs share one number sequence per repo, so this is not a collision between two live
  objects: the hazard is a shard whose number names a PR, which would otherwise resolve to that
  PR's state and be unlinked on a closed one. Key *presence* classifies, never its value — if the
  projection below changes shape, that direction degrades to everything-kept rather than to a
  mis-prune.
- **REST returns `state` lowercase** (`open`/`closed`) where GraphQL returned uppercase.
  `issue_states_from_rows` upper-cases at the boundary and `should_remove_tile` stays
  case-sensitive. Drop the normalization and the hook goes *inert* rather than merely degraded —
  fail-safe in direction, but a total and unreported loss of pruning.
- Both rules live in the pure `issue_states_from_rows`, not in the `--jq` projection or the
  subprocess call, so both stay unit-testable while the `gh` boundary itself remains untested per
  the fixture-only convention. The projection exists **only** to shrink the payload (~40 B/row
  versus ~9 KB unprojected, on a wire that stalls the session's first prompt) and deliberately
  preserves the `pull_request` key rather than acting on it.

Interpolating the validated repo into a REST **path** also retires the `HOST/OWNER/REPO` redirect
surface `--repo` carried. `repo_from_issue_url`'s validation stays regardless — defence in depth,
and still how the repo is derived in the first place.

> **Phasing — complete.** All three phases of [#867](https://github.com/brownm09/dev-env/issues/867)
> are live: the shard format, the shared reader support (`iter_tile_shards`), and the write rule
> ([#868](https://github.com/brownm09/dev-env/issues/868)); `reconcile-pending-tiles.py` plus
> `post-compact.py`'s read-only listing ([#869](https://github.com/brownm09/dev-env/issues/869)); and
> enforcement ([#870](https://github.com/brownm09/dev-env/issues/870)) — write-time validation of the
> tile kind in `journal-shard-write-advisory.py`, and trigger (3b) in `stop-tile-enumeration-gate.py`
> for a `spawn_task` call that never wrote a shard. A tile shard is therefore now checked at write
> time (schema, BOM, filename/`issue` agreement) and its *absence* is caught at Stop. This section is
> the current-state record; ADR-118 is the decision-time rationale, amended only where the
> implementation contradicted it.

**Known limitation.** There is no non-destructive API to learn whether a chip was actually clicked
(`dismiss_task` reveals it only by consuming it), so "still pending" is approximated by "issue still
open." A tile whose work already started but whose issue is open will be re-surfaced; the worst case is a
duplicate chip the user dismisses.

### Stub structure

**Creation mechanism: the Write tool, always.** A stub is 100% free-form prose — session summaries
routinely contain apostrophes, quotes, backticks, `$`, and markdown code fences — so a shell
heredoc/`echo`/redirect is exactly the wrong tool: quoting breaks on this content, either failing the
write outright or (worse) silently corrupting an already-written file. Create the file with the
**Write tool**; for a same-session in-place update, use the **Edit tool**. Mechanically enforced by
`pre-tool-use-journal-shell-write-guard.py`, which blocks a Bash/PowerShell content-write (`>`/`>>`
redirect, or a PowerShell `Out-File`/`Set-Content`/`Add-Content`/`Tee-Object`/`New-Item -Value`)
targeting a `*.stub.md` path — see [ADR-129](adr/129-journal-shell-write-guard.md).

Each stub file contains exactly one session block — the filename's `HHMMSS` component is what
delimits sessions (ADR-056 per-session sharding), so no header marker is needed for that job. The
`<!-- opening-brief -->` block appears **only in the first stub of the day**; subsequent stubs
begin directly at the `## Session: ...` heading.

```
<!-- opening-brief (first stub of the day only) -->
Opening brief: <paste the Next Session Context from the previous day's published journal verbatim;
               use "First session — no prior context." only if this is the project's very first entry>

## Session: YYYY-MM-DD HH:MM — <Topic>
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
just `git checkout draft/YYYY-MM-DD && git pull`. If the wrong branch specifically looks like a
hijack (detached HEAD, or a stray `claude/<slug>` branch matching no live worktree —
dev-env#630), `journal-canonical-guard.py` now auto-corrects this to `main` on the next prompt of
any session, before you'd typically reach for this manual step; see
[ADR-093](adr/093-journal-canonical-hijack-guard.md). This manual command is still the right move
for any other "wrong branch" cause, or if you need it immediately rather than waiting for the
guard's next prompt.
