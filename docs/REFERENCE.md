# dev-env Reference

Full descriptions of every skill, hook, routine, and utility script managed by this repo, and the reasoning behind each global rule ([Instruction Detail](#instruction-detail)).
For a compact overview see the [README](../README.md).

---

## Contents

- [Skills](#skills)
- [Hooks](#hooks)
- [Routines](#routines)
- [Utility Scripts](#utilities)
- [Model Selection](#model-selection)
- [Platform Constraints](#platform-constraints)
- [Adding a Second Machine](#adding-a-second-machine)
- [Git Workflow Runbooks](#git-workflow-runbooks)
- [Instruction Detail](#instruction-detail)
- [Project Instruction Detail](#project-instruction-detail)
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

**Multi-project mode:** when the day's stubs span two or more project directories other than
`meta` (meta is composed last, by the coordinator, in Step 6.7 — see below), the skill fans
out one Haiku composer subagent per project — all inside the single shared compose worktree — then
runs the README/commit/PR work once. Each subagent's `.draft-compose.lock` is **project-scoped**:
it checks only `sessions/<project>/.draft-compose.lock` and never globs, because every lock inside
that worktree belongs to the same run. A peer's lock is the expected signal that the fan-out is
working, and its own project's lock means it is a re-spawn taking over from a failed predecessor —
neither is a concurrency abort ([ADR-082 Addendum](adr/082-journal-compose-worktree-isolation.md),
dev-env#889). Step 0.6's cross-project lock glob is the separate, still-unqualified guard against a
genuinely concurrent *invocation*.

**Meta journal (Step 6.7):** meta triggers — `CLAUDE.md` changes, new platform constraints,
remediated workflow failures, cross-project conventions, `dev-env` PRs, journal-structure changes,
new canonical references — are **never asked about**. Attended or not, the skill composes
`sessions/meta/YYYY-MM-DD-<slug>.md` itself, in the same run and the same PR, after every other
project is composed. Each project's composer (or, in the single-project flow, Step 2b) reports
records `{project, type, stub, reason, evidence}`;
[`journal-compose-meta.py stub`](#utilities) verifies each against the stub it cites — `evidence`
must appear there verbatim, run to at least three words and come from a session block rather than the
opening brief, and a rejected record is named, with its own reason, never silently dropped — then
turns the survivors into compose-generated **derived stubs** (one per trigger category,
`sessions/meta/YYYY-MM-DD_2359NN.stub.md` plus a manifest shard). The coordinator composes those
together with any real meta stubs, titling one session per derived stub with its category label (a
session belongs to the category whose label its title *begins* with); `install` gates the result
(all eleven section headings, and in each category's own session every one of that stub's `Source:`
paths) before copying it into the worktree. It replaces the journal it installed earlier in the run
— so "expand the staged file and re-run" works when the real-stub share is under the fidelity floor
— but never one the draft branch already carries (`META_JOURNAL_EXISTS=`; where git cannot say,
`META_INSTALL_UNVERIFIED=`). Step 10's `check-clean` fails the compose if any stub,
manifest shard or `YYYY-MM-DD_draft.md` for the date remains, and its `check-staged` fails if the
index does not match the working tree — the commit is built from the index; both commit blocks
(Step 10's and Phase 2's) re-run `check-staged` first and stop on a failure, so nothing is
committed or pushed past it. A trigger report can never stop the compose: on any derived-side
failure the skill abandons the derived stubs, composes meta from its real stubs alone (if it has
any), and the PR body says `Meta journal: FAILED — …`, lists the triggers not captured, names the
real-stub journal if one was composed, and records the pre-compose draft tip;
[Late meta entry recovery](#late-meta-entry-recovery) is the fallback. The
old Step 2b prompt ("open a meta draft block? (y/n)") is gone, and so is the
`YYYY-MM-DD_draft.md` it wrote, which nothing composed (dev-env#52, #892;
[ADR-082 Addendum, 2026-10-02](adr/082-journal-compose-worktree-isolation.md)).

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

Reviews a PR or pasted diff for correctness, security, reliability, and maintainability. Runs six pre-checks before the main analysis:

- **Step 2b — Documentation Reconciliation:** flags as a blocking finding if `claude/skills/**`, `claude/hooks/**`, `claude/scripts/**`, or `claude/routines/**` were changed without updating `README.md` or `docs/REFERENCE.md` (applies to repos with a `Documentation Maintenance` table in their `CLAUDE.md`). Probes `.claude/CLAUDE.md` then `CLAUDE.md` as two **separate** API reads — never `||`-chained — and treats only a `Not Found (HTTP 404)` as absence; a `No commit found for the ref` 404 (wrong repo / missing ref) and every other failure stop the review instead of silently skipping the gate (see [ADR-120](adr/120-review-skill-absence-checks-over-api.md)).
- **Step 2c — Documentation Coverage:** for every file added, deleted, renamed, or significantly rewritten, walks up ancestor directories and uses LLM judgment to determine whether a README at any of those levels should have been updated. Flags as a blocking finding when an existing README was not touched; suggests creation as a non-blocking finding when a directory would benefit from an index it lacks. Ancestors are deduplicated before lookup, and each README probe classifies by exit status per [ADR-120](adr/120-review-skill-absence-checks-over-api.md).

  > **Remote reads (Steps 2b + 2c).** Both read blobs via `gh api "repos/<HEAD_OWNER>/<HEAD_REPO>/contents/<path>?ref=<headRefName>" -H "Accept: application/vnd.github.raw"`, using the PR's **head** repo (`headRepositoryOwner`/`headRepository` from Step 2) rather than the base repo parsed from the PR URL — on a fork PR the head ref does not exist in the base repo, and every probe would 404. This upholds [ADR-004](adr/004-pr-review-reads-from-remote.md) (read from the remote, never the local worktree) while avoiding `git show <ref>:<path>`, which MSYS deterministically mangles on Windows for any leading-dot path segment — the failure that made these two gates silently no-op for `.claude/` and `.github/` paths ([#602](https://github.com/brownm09/dev-env/issues/602), [#877](https://github.com/brownm09/dev-env/issues/877)). The API form also needs no prior `git fetch` and works when the reviewed repo is not the cwd.
- **Step 2d — Test Coverage Gate:** enforces the global "Test before PR" rule (see [ADR-022](adr/022-test-coverage-gate-before-pr.md)). Inspects the diff for new testable behavior (new endpoints, pages, exported functions, CLI commands, bug fixes) and flags as a blocking finding when new behavior is present but no test files appear in the diff and no deferral rationale is documented in the PR body.
- **Step 2e — Test Integrity Gate:** enforces the global Test Integrity policy (see [ADR-029](adr/029-test-integrity-policy.md)). Scans the diff for skip markers (`it.skip`, `xit`, `xdescribe`, `test.skip`, `describe.skip`, `.todo`, `pending`), deleted test files or blocks, lowered coverage thresholds, bypass flags (`--passWithNoTests`, `--bail`, `--testPathIgnorePatterns`), and implementation branches that appear to skew toward specific test inputs. Flags as a blocking finding when any pattern matches without a PR-body justification, and when the PR body lacks the required `Tests: N passed, N skipped, N failed` summary line. **Language-scope preamble:** before running the JS/TS scanners, detects `*.py`, `*.go`, `*.rs`, or `*.rb` files in the diff and emits a non-blocking **maintainability** finding noting that the automated patterns cover JS/TS only — converting the silent bypass on non-JS repos into a visible reviewer prompt to verify pytest / Go / Rust / Ruby skip idioms by hand.
- **Step 2f — ADR-Warrant Check:** enforces [ADR-011](adr/011-adr-warrant-check.md)'s four criteria for whether a change needs a new or amended Architectural Decision Record (touches a `claude/`-documented rule/hook/skill/setting, restructures a `claude/` directory, establishes a workflow rule other CLAUDE.md files reference, or has rationale hard to recover from `git log` alone). Flags a blocking `[documentation]` finding when a qualifying change has no ADR in the diff and the PR body cites no prior PR covering the decision. A citation satisfies the check only if the cited PR establishes precedent for the same class of change, not merely a superficially similar one — see [ADR-083](adr/083-auto-merge-checkpoint-gate.md)'s 2026-07-09 addendum.
- **Step 2g — Acceptance-Criteria Verification:** extracts each `- [ ]` item from the linked issue's acceptance-criteria section and emits a per-criterion evidence table: verbatim criterion, evidence, and `met` / `not met` / `not verifiable`. Each `not met` row is a blocking finding. A missing or empty section, or no linked issue, is reported as a non-blocking finding and never counts as a pass. Emits an `<!-- acceptance-criteria: ... -->` marker. The table format lives in the shared schema [`claude/templates/acceptance-evidence.md`](../claude/templates/acceptance-evidence.md), which Switchyard's Definition-of-Done gate also parses. Calibrated before blocking ([ADR-153](adr/153-acceptance-criteria-filing-and-verification.md)).

The main analysis (Step 4, and Subagent B in Step 5) also runs a **scope-of-change check**, which enforces the global *Code Quality → Scope of a change* rule ([ADR-149](adr/149-scope-of-a-change-rules.md)). It flags hunks that don't trace to the PR's issue, and speculative code such as unused options, single-caller abstractions, or handling for errors that cannot happen. These are non-blocking `maintainability` findings, and a hunk the PR body justifies is exempt.

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

**Slug inference:** defaults to the active git repo's name. Pass an explicit slug to override (useful when the repo name and journal slug differ). The slug is confirmed through `AskUserQuestion`. If nobody can answer, a run with an inferred slug stops before creating anything, and a run with an explicit slug continues.

**Produces:** `sessions/<slug>/README.md` in engineering-journal (committed and pushed directly to `main`), and optionally `.claude/CLAUDE.md` in the current project repo.

**Template:** reads `~/.claude/templates/project-claude.md` when scaffolding a new CLAUDE.md, substituting `<REPO_NAME>` and `<PROJECT_SLUG>`.

**Detection:** the `journal-onboard-check.py` hook emits an advisory on the first prompt of any session in a repo that lacks a journal home.

---

### /memory-audit

```
/memory-audit
```

Reconciles the active project's agent memory against the version-controlled instructions and emits a table with numbered rows (a leading `#` column, per the global *Table Row Numbers* rule) — per entry: `type`, durable?, instruction home?, and a disposition (`remain-as-cache` / `promote-to-instructions` / `delete-stale`). Catches the three rot modes ADR-038's write-time rule does not: never-ported durables, stale notes (cited PRs/issues merged, "next steps" shipped), and `MEMORY.md` index drift.

**How it works:** reads every memory file and `MEMORY.md`, verifies any *claimed* instruction home actually exists on current `origin/main` (so a stale worktree base can't produce a false "drift" finding), classifies each entry, and prints the reconciliation table. Read-only by default — promotions and deletions are confirmed with the user before acting. Audit-time complement to the write-time rule and hook ([ADR-048](adr/048-memory-immortalization-issue-pairing.md), [ADR-038](adr/038-durable-preferences-documented-in-repo.md)).

---

### /experiment-audit

```
/experiment-audit design <experiment description>
/experiment-audit verdict <results path | issue #N | description>
```

Enforces experimental rigor for a **process experiment** — a comparative claim about a process change (A/B arms, before/after, challenger vs. incumbent). `design` mode, run *before any results exist*, produces a tiered pre-registration frozen in the tracking issue; `verdict` mode, run *before any conclusion*, gates it through Gates 0–6. The one law: *no conclusion without a design that could have produced the opposite conclusion* — an unfair or unregistered experiment yields only a hypothesis for a proper run, never adopt/reject. "Failure" is banned as a verdict word (verdict ∈ {supported, refuted, inconclusive — confounded by X}).

**How it works:** loads the project's `## Experiments` section (corpus, instruments + known-good/known-bad calibration references, results home), determines the tier (Tier 0 probe → a three-line issue declaration whose only legal endings are signal/infeasible/shelved; Tier 1 test → the full 11-field pre-registration with an incumbent-influence inventory, instrument calibration before any arm is scored, processing parity, a frozen win bar + n/k, and a measured cost model with a pilot and a stage-boundary stop rule), and in `verdict` mode runs the T1–T10 threat sweep (T10 = criterion substitution — deciding on a measurable proxy instead of the primary construct) and reads the verdict off the primary construct via the decision-legality matrix, recording the audit to `sessions/<project>/reports/`. Every emitted block opens with an `[experiment-audit]` marker — the signal `stop-experiment-verdict-gate.py` reads. The design half is enforced at plan time by Pass-3 dimension 7 (**Experimental validity**); the verdict half is backstopped by the Stop hook ([ADR-115](adr/115-experimental-rigor-protocol.md), [ADR-042](adr/042-plan-risk-dimension-audit-and-observability-section.md)). **Scope:** this skill governs *experiments* — comparative claims between arms, producing a verdict. A standing **gate** is Pass-3 dimension 8 (**Gate calibration**), which applies the same calibration law (known-good *and* known-bad references; uncalibrated ⇒ diagnostic, not blocking) and reuses this skill's Step D4 worksheet, but carries none of the tier / pre-registration / blinding / verdict machinery ([ADR-144](adr/144-gate-calibration-pass-3-dimension.md)).

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
were the live anchor) or filing a new `retro-action`-labeled issue otherwise; proposes a tile with `start_session`
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
[`claude/scripts/README.md`](../claude/scripts/README.md)
([dev-env#830](https://github.com/brownm09/dev-env/issues/830)). Its rows stop at the scripts that
existed on 2026-11-07; a newer script carries a `Purpose:` line near its top instead
([dev-env#1311](https://github.com/brownm09/dev-env/issues/1311)). Neither file states a file
count, because every new script changed it. The tables below remain the
authoritative per-hook behavioral description; that index is a navigational map only.

Most hooks are **advisory** — they emit `systemMessage` reminders but do not block tool execution. The exception is `pre-tool-use-worktree-path-check.py` (a `PreToolUse` hook), which exits 2 with a `{"reason": "..."}` payload to block `Write`, `Edit`, and `NotebookEdit` calls that target the canonical repo root instead of the active worktree, or that are issued from an orphaned worktree whose `.git` link no longer resolves (so git silently operates on the canonical repo).

Configuration is in `claude/settings.shared.json`. It is **not** symlinked: `~/.claude/settings.json`
is a real, machine-local file the Claude Code app writes (theme, `tui`, notification flags,
`autoMode`), and `_settings_sync.py` applies the tracked `hooks` and `permissions` blocks *into* it
on every prompt. Symlinking it — the pre-[ADR-139](adr/139-machine-local-settings-with-shared-source-sync.md)
arrangement — meant the app dirtied a tracked file and blocked the canonical's fast-forward
permanently, serving stale hooks and skills machine-wide (dev-env#1049). The owned / seed /
machine-local key split is in the [dev-env `CLAUDE.md`](../CLAUDE.md) architecture section.

**Every hook runs through the hook launcher** ([ADR-148](adr/148-missing-hook-script-fails-open-via-launcher.md),
dev-env#1146): `pyw -3 C:/Users/brown/.claude/hook-launch.py C:/Users/brown/.claude/scripts/<name>.py`.
Without it, a wired script that is missing makes Python exit 2, which Claude Code reads as a block
on every matching tool call, in every session. Since the wiring is machine-local and the scripts
come through the junction, any lag between the two (a canonical drifted onto an older branch, a
sync run before a pull) used to lock the machine out of Bash, PowerShell and Write; it happened
live on 2026-10-09. The launcher (`claude/scripts/_hook_launch.py`, stdlib-only) runs a present
script in-process as `__main__` with stdio, argv and the exit code untouched. For a missing script
it exits 0 and prints a `systemMessage` naming PowerShell-safe recovery commands (absolute paths,
`;` rather than `&&`). The warning is throttled to once per script per 10 minutes, except for the
two fail-closed gates, which also fail open but are announced every time: a missing gate has no
code to scope its block, and both are wired on every Bash call. `_settings_sync` installs it to
`~/.claude/hook-launch.py`, outside every junction so it survives a regressed tree, on every sync
and before writing any hooks that name it. It refuses to install a source that does not compile.
The sync also withholds `hooks` (keeping the live wiring and reporting "pull first") while any path
a command names is absent.
**Residual risk:** if `~/.claude/hook-launch.py` itself is deleted, every hook exits 2 again, and
`dev-env-sync.py`, the hook that would reinstall it, cannot run. Recover from a hook-free terminal
(the app's Terminal panel) with `py -3 C:/Users/brown/.claude/scripts/_settings_sync.py`.
See [ADR-007](adr/007-hook-command-invocation.md) for why hooks invoke scripts via `pyw -3` (the windowless variant of the Windows Python Launcher) rather than `python3` directly, wrapped in `bash -c`, or via `py -3` (which flashes a console window per spawn). Shell-invoked Python (the `## Testing` command, skill `py -3` examples, and the `pre-push` hook) continues to use `py -3`.

Any hook that spawns subprocesses (`git`, `gh`, `bash`, …) must `import _winsubp` near its imports — the helper patches `subprocess.Popen.__init__` to (1) set `CREATE_NO_WINDOW` so children don't flash a console window under `pythonw.exe`, and (2) default a text-mode call (`text=True` / `universal_newlines=True`) with no explicit `encoding=` to `encoding="utf-8", errors="replace"` rather than the Windows cp1252 default, which crashed `post-tool-use.py` reading `gh project item-add`'s output (dev-env#503). The static check in `claude/scripts/tests/test_hook_static_scans.py` fails the build if a subprocess-using hook ships without it. See ADR-007's 2026-06-01 and 2026-07-02 follow-up sections.

Any PostToolUse Bash hook that reads command output must use `read_command_output` from `claude/scripts/_hookio.py` rather than `tool_response["output"]`: Claude Code's payload carries output under `stdout`/`stderr`, so the legacy `output` read is always empty and silently disables the hook. See [ADR-049](adr/049-hook-payload-output-field.md) (root cause) and [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) (shared helper + sibling-hook fixes).

The worktree-maintenance scripts (`prune-merged-worktrees.py`, `reclaim-worktree-disk.py`) call `worktree_session_is_live` from `claude/scripts/_worktree_liveness.py` to skip a worktree with a live Claude session — it reads the worktree's transcript-dir mtime under `~/.claude/projects/`, the only signal by which an out-of-process routine can avoid severing an active session in another worktree. Windows are blast-radius-scaled (prune 24h, reclaim 6h) and override-able with `--liveness-window-min`. The prune also calls `git_activity_skip_reason`, which skips a worktree whose own `index`, `HEAD`, `FETCH_HEAD` or `ORIG_HEAD` was written within its window, for a session working there from another folder, and keeps one whose git state can't be read. See [ADR-051](adr/051-worktree-liveness-guard.md) and its Amendment 1.

The journal open-PR hooks (`reconcile-open-prs.py`, which `unlink`s merged/closed shards, and `post-compact.py`, which reads them to prompt a `/review`) enumerate the per-PR shards `sessions/<project>/open-prs/<N>.json` and the legacy `open-prs.jsonl` through one shared reader, `claude/scripts/_journal_shards.py` — `iter_pr_shards` returns `(path, entry)` pairs (numerically sorted; non-numeric-named, unparseable, and non-object shards skipped) and `read_legacy_entries` drains the legacy file. Centralising the read keeps the two hooks from drifting on the shard semantics and gives the legacy format a single retirement point (pairs with the engineering-journal#128 data migration). See [ADR-057](adr/057-shared-journal-shard-reader.md). [ADR-118](adr/118-tile-persistence-shards.md) added tile shards (`sessions/<project>/tiles/<issue-number>.json`) on the identical numeric layout, so that enumeration now lives in `iter_numeric_shards` with `iter_pr_shards` and `iter_tile_shards` as named delegations to it (and `shard_pr_number` as an alias of a generic `shard_number`) — adding the second shard kind by *copying* the reader would have recreated the very drift ADR-057 was extracted to end. The module also owns `project_dirs` — the walk over every `sessions/<project>/` directory that both reconcile hooks run *before* reading either shard kind. It lived as a per-hook copy until dev-env#881: the tile reader duplicated `reconcile-open-prs.py`'s helper rather than conflict with an in-flight PR, and that third copy (plus the docstring note recording the deferral) was folded back in here. Each hook's test now carries a one-line identity pin asserting it still resolves to this copy, so a future re-duplication fails a test rather than waiting to drift.

The manifest, open-PR, and tile shard **schemas** — as opposed to the shard *enumeration* above — live in `claude/scripts/_journal_schema.py`, shared between the compose-time gate `validate-manifest.py` and the write-time `journal-shard-write-advisory.py` PostToolUse hook so the required-field lists and BOM-decoding logic are defined once. It exposes `REQUIRED_FIELDS` / `OPEN_PR_REQUIRED_FIELDS` / `TILE_REQUIRED_FIELDS`, `missing_required_fields()` / `missing_open_pr_fields()` / `missing_tile_fields()`, `find_entries_missing_fields()`, `parse_manifest_text()`, and `decode_shard_bytes()` (names a UTF-8/UTF-16 BOM rather than letting it surface as an opaque JSON parse failure on line 1). See [ADR-081](adr/081-write-time-journal-shard-validation-hook.md) and [ADR-118](adr/118-tile-persistence-shards.md).

Per-session sentinel helpers, transcript-locate, and the transcript-record readers are extracted into `claude/scripts/_hookutil.py` (Stop / UserPromptSubmit hook family — the analogue of `_hookio.py` for the PostToolUse family). It exposes `cleanup_stale_sentinels(prefix)`, `sentinel_path(prefix, session_id) -> Path`, `find_transcript(session_id) -> Path | None`, and the transcript-record readers `load_records` / `_parse_records` / `iter_bash_calls` (pairs Bash tool_use/tool_result by id, returning `(command, output, cwd)`; `iter_bash_call_details` adds the result's `is_error` and the issuing record's timestamp, dev-env#1331) / `_result_text` / `_content_items`, plus the user-text pair `_user_message_texts` / `_is_synthetic_user` and `first_user_prompt_text(records) -> str` ([ADR-140](adr/140-unchained-merge-workstream-gate.md), dev-env#1044 — the first genuine (non-synthetic, non-blank) user prompt, composing that pair; the opening-prompt scope test `stop-tile-enumeration-gate.py`'s unchained-merge trigger reads, and the first hook-facing reason anything needed the session's opening prompt) — used by `posttooluse-inert-advisory.py`, `stop-tile-enumeration-gate.py`, `reconcile-open-prs.py`, and `token-tracker.py` so each no longer carries its own `SCRATCH` / `PROJECTS` constants and local copies of these helpers. The `scratch` / `projects` parameters are injectable for offline testing; `stop-tile-enumeration-gate.py` consumes `iter_bash_calls` through a thin 2-tuple adapter that drops `cwd` (it never needs it). It also exposes `iter_records_reverse(transcript_path, chunk_size=DEFAULT_REVERSE_CHUNK_SIZE) -> Iterator[dict]` (dev-env#679, [ADR-090](adr/090-shared-transcript-readers-hookutil.md) Amendment 1) — reads the file from the end in bounded chunks, yielding JSON-object records most-recent-first, so a caller that only needs a small piece of tail state (`idle-refresher.py`'s last-assistant-record timestamp) can stop consuming the generator instead of paying `load_records`'s full parse; `load_records` itself is unchanged, kept for callers needing the whole transcript. `record_context_tokens(record) -> int | None` (dev-env#1217) is the one context-size definition `turn-count-hook.py` and `idle-refresher.py` share: input + cache read + cache write on a main-thread assistant record, `None` for anything else. See [ADR-064](adr/064-shared-hookutil-sentinel-transcript-locate.md) (sentinels / transcript-locate) and [ADR-090](adr/090-shared-transcript-readers-hookutil.md) (transcript-record readers).

`_hookutil.py` also exposes `record_heartbeat(hook_name, heartbeat_dir=None) -> None` ([ADR-106](adr/106-hook-heartbeat-liveness-ledger.md)) — writes `~/.claude/scratch/hook-heartbeat/<hook_name>.ts` (the current Unix timestamp) via a per-process tmp file + `os.replace` atomic swap, best-effort (swallows all I/O errors). A rare `os.replace` failure (e.g. a Windows sharing violation while `hook-liveness-check.py` is mid-read of the target) can orphan the `<hook_name>.ts.<pid>.tmp` in that directory; `hook-liveness-check.py` reaps such `.tmp` orphans older than 30 days on each run ([dev-env#802](https://github.com/brownm09/dev-env/issues/802)). Called as the unconditional **first statement of `main()`** — before any other logic, including the hook's own stdin read — by **every** currently-wired hook script (every one in the tables below, `hook-liveness-check.py` included), so a heartbeat is recorded on every real invocation regardless of what the rest of the hook does or whether it raises, but never on a bare `import` of the module (which every hook's own test file does to unit-test its pure helpers offline). `hook-liveness-check.py` is the reader side — see its `UserPromptSubmit` table entry below.

The hook advisory/block emitter is `claude/scripts/_hookout.py` — the analogue of `_hookio.py`/`_hookutil.py` for hook *output*, encoding Claude Code's per-event channel table once. It exposes `emit_advisory(event, text, *, audience="model"|"user"|"both", blocking=False)`, `emit_block(text)`, `ascii_sanitize(text)`, and the pure routing core `plan_emission(...) -> Emission`. The contract it encodes: plain stdout on exit 0 is model-visible only on the context events (`STDOUT_MODEL_VISIBLE_EVENTS` = UserPromptSubmit / SessionStart / UserPromptExpansion), exit-2 stderr reaches the model on any event, and `{"systemMessage"}` JSON on exit 0 reaches the user on any event — so a non-blocking, model-visible advisory is impossible on PreToolUse/PostToolUse/Stop, where `plan_emission` raises `ValueError` (naming `blocking=True` or `audience="user"`) rather than emitting into the void. JSON channels use `json.dumps(ensure_ascii=True)` for cp1252 wire-safety; raw exit-2 stderr text is `ascii_sanitize`-d (`.isascii()`-guaranteed). Generalizes the per-site channel fixes ADR-091/098/099/100 (PRs #701/#705) each made one hook at a time; additive in the introducing PR (no hook imports it yet — the enforcement gates and per-site migrations follow in later PRs of [dev-env#717](https://github.com/brownm09/dev-env/issues/717)). See [ADR-103](adr/103-shared-hookout-emitter.md).

The `gh`-command target resolver is `claude/scripts/_repo_target.py` — one pure module for extracting the `owner/repo` (and PR/issue number) a `gh pr merge`/`gh pr create`/`gh issue create`/`gh issue close` command targets, converged from five hooks that had drifted into three distinct `--repo`/`-R` regex shapes (a sixth, `post-tool-use.py`, joined in dev-env#838). It exposes `repo_from_flag(text)` (the `--repo`/`-R` flag in both `=` and space forms, strict GitHub slug — also normalizing a full-URL / `github.com/` host-prefixed value down to the bare `owner/repo`, dev-env#838 — standalone-token lookbehind, masking `mask_quoted_spans` internally), `merge_args(command)`/`create_args(command)`/`issue_create_args(command)` (the quote-aware statement-bounded argument region, so a chained sibling command's flag can't leak in), `repo_from_pr_url`/`pr_number_from_pr_url`/`iter_pr_urls` and `issue_number_from_issue_url`/`iter_issue_urls` (URL parsing — the caller decides masking, since a bare quoted URL must stay matchable where a prose-flag decoy must not), and `positional_number(text)` (the bare positional integer, decoy-safe). Also exposes `repo_from_rest_merge_path(command)` / `pr_number_from_rest_merge_path(command)` (dev-env#986, ADR-050 Amendment 23) — the `repos/<owner>/<repo>/pulls/<N>/merge` REST path of the two-step merge fallback used when `gh pr merge` itself is unavailable (e.g. a GitHub GraphQL rate-limit outage); the response body carries no PR number, so the command's own path is the sole source. Consumed by `post-pr-merge-project.py`, `pr-merge-reminder.py`, `posttooluse-inert-advisory.py`, `post-pr-merge-pull.py`, `stop-tile-enumeration-gate.py`, and `post-tool-use.py` (its `extract_repo_flag` cross-repo sibling-config lookup, dev-env#838); cd-chain/`-C` resolution stays in `_hookio.effective_merge_dir` (ADR-067), reused not re-implemented. This ends the per-site ADR-050 amendment treadmill (Amendments 14/15/17/18/19/20/21) for the repo-flag/URL/number concern. See [ADR-111](adr/111-shared-repo-target-resolution.md).

`_hookio.py` (see the ADR-050 rationale above) also exposes `is_rest_merge_command(command)` / `output_has_rest_merge_marker(output)` (dev-env#986, ADR-050 Amendment 23) — the command-shape (`gh api` targeting a `.../pulls/<N>/merge` path) and output-marker (`"merged":true`) halves of the same REST-merge-fallback recognition, mirroring `stop-tile-enumeration-gate.py`'s own pre-existing detection of this shape for session-merged-PR enumeration. Consumed by all five PostToolUse merge-consequence hooks (`usage-snapshot.py`, `post-pr-merge-project.py`, `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, `post-merge-tile-checkpoint.py`), each extending its own existing success predicate with an OR clause rather than sharing a new cross-file combinator.

`_hookio.py` also owns the one `gh pr merge` statement predicate, `is_pr_merge_command(command)`, and the pattern string `GH_PR_MERGE` (`gh` or `gh.exe`, any case) that every merge argument parser builds from; a leading `NAME=value` prefix such as `ALLOW_CANONICAL_MUTATE=1` still counts (`strip_env_prefix`). Ten hooks used to carry their own copies in four regex variants (dev-env#1168, ADR-050 Amendment 29). The stdin payload loader every hook shares, `load_hook_input(tools=None)` with `SHELL_TOOLS`, lives in the tiny `_hookinput.py` (re-exported by `_hookio`), so a hook needing only that skips `_hookio`'s import cost.

The orphaned-worktree recovery recipe is `claude/scripts/_worktree_recovery.py` — one pure, ASCII-only definition of *how to un-orphan a worktree*, exposed as `RECOVERY_STEPS` (ordered `RecoveryStep(command, note)` pairs using `<canonical>` / `<orphan>` / `<branch>` placeholders), `recovery_commands(orphan_root, canonical_root)`, and `recovery_recipe(orphan_root, canonical_root)` (the formatted block a blocking hook embeds in its reason). Consumed by `pre-tool-use-worktree-path-check.py`'s orphan block message, and pinned against the [Worktree deregistration recovery](#worktree-deregistration-recovery-lost-git-link-routes-git-to-main) runbook by `tests/test_worktree_recovery.py` — which also fails the build if the dev-env#751-disproven `worktree add --force` form reappears in any emittable string literal (comments and docstrings explaining *why* it fails are exempt; `docs/adr/` is exempt as a historical record). It exists because those two surfaces were hand-maintained copies that drifted: dev-env#751 corrected the runbook and left the hook message alone, so the disproven recipe stayed on the one surface a blocked session actually reads for another six weeks (dev-env#862). The current sequence — `worktree repair` first (non-destructive, preserves uncommitted work, and exits 1 whether or not it succeeded, which is why the `rev-parse` verification is a separate mandatory step), then `prune` → plain `add`, then a salvage copy and empty-in-place *only* if `add` reports `already exists` — was re-derived from a throwaway-fixture matrix rather than inherited. The destructive step is rendered outside the numbered sequence and flagged `destructive`/`conditional` in the data, so a reader working top-to-bottom never reaches it by default. See [ADR-116](adr/116-single-source-worktree-recovery-recipe.md).

#### Machine-local permissions

The `permissions.allow` block **and every hook command** in `claude/settings.shared.json` contain paths with a hardcoded Windows username (`C:/Users/brown/...`). They are correct on a machine whose profile is `C:\Users\brown`. On any other, the hook commands name missing scripts and block every prompt — `dev-env-doctor.py` reports it, and [dev-env#1113](https://github.com/brownm09/dev-env/issues/1113) tracks rewriting the prefix per machine; see [Adding a Second Machine](#adding-a-second-machine). If scratch-dir writes or edits start prompting for permission after a re-bootstrap, update the username in every `allow` entry.

**Known scope decisions:**

| Entry | Scope | Rationale |
|---|---|---|
| `Edit(C:/Users/brown/Git/**)` | All files in all local repos | Covers skill and config edits across career-playbook, lifting-logbook, dev-env, etc. without per-file prompts. Intentionally broad — includes `.env` and credential files — accepted tradeoff on a single-user personal machine. |
| `Bash(rm -f C:/Users/brown/.claude/scratch/*)` | A literal-path scratch delete | Kept as the cheap static case. Deletions written with variables, `cd`, or another drive spelling are approved instead by `pre-tool-use-scratch-rm-allow.py`, which resolves the paths itself; a broad `Bash(rm *)` rule was rejected because it would approve deletions anywhere ([ADR-147](adr/147-scratch-only-rm-auto-allow-hook.md)). |

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
| `session-start-auto-resume.py` | Registered separately, with `"matcher": "resume"` and `"asyncRewake": true`, so it runs in the background and exiting 2 wakes the model. It acts when a desktop session's CLI starts and, before the hook started, the transcript ended mid-turn. That means a reboot or app crash cut it off: a `/compact` or other local command doesn't count, and neither does the prompt that launched the CLI. It also acts when the transcript ended waiting on a background task that died with the old process (dev-env#1185). The resumed CLI's own synthetic placeholder, and its "stopped" task-notifications with the turns they set off, are set aside rather than counted as progress. It then checks three things: `~/.claude/auto-resume.json` opts the session in (no app metadata means no); the interruption is under `maxAgeHours` old (`maxWaitAgeHours` for a background wait, dev-env#1189); and the session has wakes left, meaning `maxAttempts` per app instance and at most 3 wakes in a row with no human prompt, across restarts. Then it waits `settleSeconds` and re-reads the transcript. A prompt, a message typed mid-turn, a line still being written, or a changed state cancels the wake. Without a stop notice, so does any other turn line. With one, the CLI's own notice turn is allowed, but it must finish first (one extra 30 s wait). So a session that is already running is never sent a second turn. Otherwise it wakes the model with a check-for-errors-and-continue message. Decisions are logged to `Documents\LOGS\claude-auto-resume_<date>.jsonl`. Fails open. `auto-resume-launcher.py` (scheduled task) opens the sessions this needs after an app restart. [ADR-152](adr/152-auto-resume-interrupted-desktop-sessions.md) |

---

### UserPromptSubmit

Fires on every user prompt, before Claude processes it.

| Script | What it does |
|--------|-------------|
| `session-mode-prompt.py` | Fires on the first user prompt of each new session. Emits a one-time mode-confirmation reminder (plan / bypass / auto) to Claude as `hookSpecificOutput.additionalContext` JSON on **stdout** and exits 0 — per the hook contract, this delivers the reminder alongside the prompt without erasing it, and Claude surfaces a mode-confirmation line in its first response. A per-session marker file at `scratch/session_mode_ack_<session_id>.txt` records that the reminder has been injected for this session; subsequent prompts in the same session pass through silently. Cross-session contamination is impossible — session A's marker never affects session B. Markers older than 30 days are swept via `_hookutil.cleanup_stale_sentinels` on every invocation (dev-env#768 — previously never cleaned up). Suppressed for automated sessions whose prompt begins with an XML tag (e.g. `<scheduled-task>`, `<ci-monitor-event>`). When `permission_mode` carries the `bypass` stem (matched case-insensitively on the stem, so a contract rename to a sibling spelling still lands the note), the injected context additionally carries the **content-authoring carve-out**: that mode's standing harness instruction to make file changes with `sed`, heredocs, or short scripts governs shell *work*, not authoring file *content*, for which `claude/CLAUDE.md` → Authoring File Content wins. The harness prompt is not editable from this repo, so the tiebreaker is delivered into the same context, in the same sessions — measured live from this hook's own log, bypass is 79% of all sessions (4579 of 5824), so the contradiction it resolves is present in four sessions out of five. Non-bypass modes get the base reminder unchanged. Diagnostic JSON log at `scratch/session-mode-prompt.log`. [ADR-027](adr/027-userpromptsubmit-blocking-hook-conventions.md) (see 2026-05-27 amendment), [ADR-138](adr/138-shell-content-write-guard.md) |
| `dev-env-sync.py` | Fast-forward pulls the dev-env repo to `origin/main` so symlinked tooling stays current. The fast-forward is `git merge --ff-only origin/main` after the hook's own fetch, never `git pull`, whose merge target is the canonical's `.git/FETCH_HEAD`, which concurrent sessions share (dev-env#1174, [ADR-056](adr/056-per-session-sharding-journal-companion-files.md) Addendum 2026-11-01). When the canonical worktree is off `main` — including a **detached HEAD**, routed into the same path via `resolve_current_branch()` since dev-env#619 (previously `git symbolic-ref` failing on detached HEAD caused a silent early exit before this diagnosis ever ran) — diagnoses the worktree-on-`main` topology (`_worktree_topology.py`) and either **auto-returns a clean canonical to `main`** (then continues the pull), **warns naming a non-canonical worktree squatting `main`** plus the `git -C <wt> checkout -b claude/<slug>` park command that frees the ref, or **warns without switching when the canonical is dirty** (preserving drift). **All advisories print to stdout, never stderr** (dev-env#694, [ADR-098](adr/098-dev-env-sync-advisories-to-stdout.md)): a `UserPromptSubmit` hook's exit-0 stderr is not added to Claude's context, only stdout is — a prior stderr-routed fast-forward-failure warning (e.g. a dirty working-tree file conflicting with an incoming commit) went unnoticed for 36+ hours and 21+ commits of drift before this fix. The fast-forward-related messages (pulled / diverged / pull-failed) also state local/remote short SHAs and the commit-behind count, computed once up front, so a future occurrence is self-diagnosing without a manual `git log`/`git fetch` comparison; the success message additionally flags a pre/post commit-count mismatch as a likely concurrent-process race against the same shared canonical checkout. A fast-forward-pull failure that **persists across prompts/sessions** — the recurring dirty-tracked-file-conflicting-with-an-incoming-commit case (dev-env#697, #795) — is tracked in a single repo-level scratch state file (`~/.claude/scratch/dev_env_sync_ff_failure.json`; `first_failure_at` + `consecutive_count`, cleared on the next up-to-date/successful pull, `_hookutil`-swept after 30 days) and, once it has recurred on ≥3 consecutive prompts **or** persisted ≥2h, **escalates** to a distinct, louder advisory naming the commits-behind count, the blocking file path(s) parsed from git's stderr, and how long it has been failing — so a stuck canonical can't silently fall many commits behind (leaving every merged fix inert, since `~/.claude/` is junctioned to this working tree) the way it did twice before. Escalation stays at exit 0 (no blocking), and the state file is repo-level, not per-session, so the persistence detection survives across sessions (the whole point — a per-session file would reset every session). **The off-main states are measured and escalate too** (dev-env#1140, ADR-110 Amendment 1). Each off-main warning (dirty canonical, squatter, unreadable worktree list, failed auto-return) states the commits-behind count (`rev-list --count HEAD..origin/main`) and how long ago HEAD last moved (the newest reflog entry). These are local git calls only: a fetch here could hang past the hook's timeout and lose the warning. It escalates to a distinct **STALE CANONICAL** advisory once HEAD hasn't moved for ≥2h, unless the checkout is a measured 0 behind, or once it is ≥10 commits behind. Both measurements are stateless, and a failed one reads "unmeasured" rather than 0. **Escalated advisories reach the user as well as the model.** `main()` collects every advisory into an `Output` and `render_output()` emits the batch once, choosing the channel per run. Normally that is ASCII-sanitized plain stdout text. Once anything has escalated, it is one JSON object: the full text as `additionalContext` for the model, and a one-line summary per escalation as `systemMessage` for the user. Every git call shares one time limit, sized under the hook's 30s timeout. Network calls run non-interactively. The crash fallback writes only its own line, and only if nothing was written yet. The incident behind this: the canonical sat on a June branch for 108 days while that branch's own pre-ADR-098 copy of this hook printed its off-main warning to stderr on every prompt. **Before any of the git work**, applies `claude/settings.shared.json` into the real, machine-local `~/.claude/settings.json` via `_settings_sync.py` — owned keys (`hooks`, `permissions`) replaced wholesale, seed keys (`model`, `effortLevel`, `autoCompactWindow`, `env`) written only if absent, every app-written key (`theme`, `tui`, `autoMode`, notification flags) left alone — and performs the one-time symlink->real-file migration. **The ordering is load-bearing in both directions:** the pull is what removes `claude/settings.json` from the working tree, and a still-symlinked live file would then resolve to nothing, so every hook — *including this one* — would stop firing with nothing able to self-heal; and until the live file stopped being the tracked file, the app's own writes dirtied it and the fast-forward below could never succeed, which is the failure this whole arrangement exists to end (14 consecutive failed pulls over 6h 20m on 2026-08-25, serving the pre-ADR-138 `/review` skill machine-wide). Settings-sync failures are reported to stdout and never block the prompt or the pull. Right after the settings sync it seeds dev-env's own `.claude/hook-config.json` from `claude/hook-config.shared.json` when that file is absent (`_settings_sync.seed_hook_config`, write-if-absent, dev-env#669, [ADR-052 Amendment 2](adr/052-worktree-config-canonical-fallback.md)). Exit 0 always (unhandled exceptions are caught at the `__main__` guard); the worktree enumeration runs only on the rare off-main path. [ADR-006](adr/006-dev-env-sync-on-every-prompt.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md) (2026-07-09 amendment), [ADR-098](adr/098-dev-env-sync-advisories-to-stdout.md), [ADR-110](adr/110-escalate-persistent-dev-env-sync-ff-failures.md) |
| `journal-canonical-guard.py` | Corrects the engineering-journal canonical checkout (`C:/Users/brown/Git/engineering-journal`) when it's sitting on the dev-env#630 hijack signature — detached HEAD, or a stray `claude/<slug>` branch belonging to no live worktree (`is_hijacked_branch()` in `_worktree_topology.py`) — caused by a scheduled-task worktree-provisioning defect reproduced 2 mornings running. Unlike `dev-env-sync.py`, does **not** treat "off `main`" as broken: engineering-journal's canonical is legitimately on `draft/YYYY-MM-DD` for most of every working day, so only the hijack signature specifically triggers correction. Re-checks the hijack condition against a fresh `git worktree list` read immediately before acting (a concurrent stub-writing session may have already fixed it between the cheap first read and the more expensive second one — TOCTOU). Reuses `diagnose_main_topology()`/`canonical_sync_action()` unchanged for the same **auto-return / warn-squatter / warn-dirty** decision `dev-env-sync.py` makes; non-destructive (`git checkout main` never deletes the hijacked branch). Test-seam: `JOURNAL_CANONICAL_GUARD_REPO_PATH` env var overrides the target repo path — resolution now shared with the other three engineering-journal carve-out hooks via `claude/scripts/_journal_canon.py` ([ADR-133](adr/133-shared-journal-canon-module.md)). **All advisories print to stdout, never stderr** (dev-env#699, [ADR-099](adr/099-journal-canonical-guard-advisories-to-stdout.md)): a `UserPromptSubmit` hook's exit-0 stderr is not added to Claude's context, only stdout is — the identical defect ADR-098 fixed in the sibling `dev-env-sync.py`. Exit 0 always. [ADR-093](adr/093-journal-canonical-hijack-guard.md), [ADR-099](adr/099-journal-canonical-guard-advisories-to-stdout.md) |
| `new-day-journal-check.py` | Five checks against `origin/engineering-journal`: stale draft artifacts, draft branches with no composed journal on `main`, composed days' draft branches with session files changed after the day's last merged PR (reads merged PRs through `gh`, dev-env#1262), (ADR-119) **day rollover** — the canonical resting on a `draft/<D>` branch dated other than today, plus any stubs on it dated *after* `D` — and (ADR-119 Amendment 1, dev-env#911) **stale-canonical self-healing**: when that same non-today `draft/<D>` is ALSO fully clean (whole working tree, not just `sessions/`) and HEAD hasn't moved — a checkout OR a commit, whichever is more recent, read from HEAD's own reflog rather than the branch's tip-commit time — in ≥`STALE_CANONICAL_IDLE_MINUTES` (15) minutes, the hook performs a real `git checkout main` — the one check here that mutates rather than only advises, bounding how long two concurrent sessions colliding on the canonical's shared HEAD (no coordination lock exists) can leave it silently stranded. Anchoring idle time on the branch's own last commit (rather than HEAD's reflog) was an earlier draft's bug, caught in review: a stale branch's last real commit is, by construction, already old, so a session freshly checking it out for legitimate work (e.g. an ADR-119 decision-3 shard-deletion hop) would find idle time already past threshold at the instant of checkout, with none of the intended headroom. A dirty tree is never auto-touched under any idle time, and a final re-read of branch/dirty state immediately precedes the checkout to narrow the TOCTOU window (mirrors `journal-canonical-guard.py`'s identical precaution); a failed checkout re-checks whether a concurrent process already resolved it before reporting a manual-fix advisory. Emits a warning if any check fires; continues silently otherwise. Checks 1-3 are suppressed in Claude-managed worktree sessions (`.claude/worktrees/` in cwd); checks 4 and 5 are **not** — worktree sessions write stubs into the canonical via `git -C` too, so they are exactly who needs the warning (and benefits from the auto-recovery). Checks 4 and 5 share one sentinel and checks 1-3 share another, each re-arming after `RECHECK_MINUTES`, so a rollover (or stranding) beginning mid-session is still caught without respawning git on every prompt. |
| `journal-onboard-check.py` | Checks whether the active git repo has a `sessions/<repo-name>/` directory in engineering-journal. Emits a one-line advisory and `/journal-onboard` hint if not. Fires once per session, gated by a `scratch/journal_onboard_<session_id>.flag` sentinel (`_hookutil.sentinel_path`) swept after 30 days via `_hookutil.cleanup_stale_sentinels` (dev-env#768 — previously hand-rolled and never cleaned up; this prefix alone accounted for 986 files at the 2026-07-10 assessment). |
| `turn-count-hook.py` | Warns when a session has really grown. Primary signal: cumulative context growth since the session's first turn (150k, then every 100k), so a large start alone stays silent ([ADR-150 Amendment 1](adr/150-context-growth-journal-stub-trigger.md#amendment-1-2026-11-06--the-size-warning-also-keys-on-growth), dev-env#1217); secondary: prompt count, configurable via `"turn_threshold"` in `.claude/hook-config.json` (default: 8, then every 4). Also prints a `[journal-stub]` nudge, once per band of cumulative context growth since the session's first turn (150k, then every 100k; configurable), to update the session's journal stub. It names the stub the session last wrote, or says to create one ([ADR-150](adr/150-context-growth-journal-stub-trigger.md), dev-env#1160). |
| `idle-refresher.py` | On the user's return after an idle gap exceeding the threshold (default 60 min), injects an `additionalContext` cue telling Claude to open its reply with a refresher (what we were working on, current state, pending to-dos/tiles) before addressing the new prompt. The cue states the context size the return re-caches and suggests `/clear` with a hand-off if the task has changed (dev-env#1217). Anchors the gap on the last assistant turn's timestamp in the transcript (immune to the just-submitted prompt being appended); skips automated/XML-prefixed prompts and the first prompt of a session; stateless and fail-open (exit 0, ASCII-only cue). Configurable via `"idle_refresher_minutes"` in `.claude/hook-config.json` ([ADR-095](adr/095-session-boundary-summaries-and-idle-refresher.md)). |
| `routine-findings.py` | Shows findings from scheduled routines whose push couldn't be delivered. Moves each file from `~/.claude/routine-findings/pending/` to `shown/` and injects one line per finding as `additionalContext` on stdout, marked as text from an unattended run, so a finding appears once, even if two sessions race. Shows at most 20 per prompt, oldest first, and the rest follow on the next prompt; a message over 300 characters is cut, and `shown/` keeps it whole. Skips automated/XML-prefixed prompts and headless `sdk-` entrypoints so a scheduled run can't use up a finding, and puts findings back if its output fails; fail-open. The `save` command that fills the folder is under [Utilities](#utilities) ([ADR-160](adr/160-routine-findings-delivery-fallback.md)). |
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
| `pre-merge-branch-check.py` | Command contains a top-level `gh pr merge` (`_hookio.is_pr_merge_command`, the predicate every merge hook shares) | Emits the current branch/repo as a confirmation checkpoint before the merge runs, plus a drift warning against the repo/branch recorded after the session's last Bash call. Advisory only, mirrors `pre-commit-branch-check.py`'s pattern for `gh pr merge` instead of `git commit`. (ADR-085) |
| `pre-merge-findings-gate.py` | Command contains `gh pr merge`, excluding a `--help`/`-h`-only invocation (`is_merge_help_only`, dev-env#557 — it can never attempt a real merge, so it must not pay a live-PR lookup scoped to an unrelated PR) | Reads the target PR's last `/review` comment marker (`<!-- review-findings: blocking=N non_blocking=M -->`); if `N+M > 0` and the PR body records no "Review findings disposition" section (or `<!-- findings-disposed -->` sentinel), **blocks the merge (exit 2)** with a fix-or-file instruction. Mechanical enforcement of the all-findings merge gate (ADR-028/ADR-039). Also warns through a `systemMessage`, without blocking, when the body says it doesn't close an issue in a form GitHub reads as closing it (`_closing_negation.py`, dev-env#1300, ADR-153). Fails open on any `gh`/parse error. Has a behavioral self-test: `bash claude/scripts/tests/test-merge-findings-gate.sh`. |
| `pre-auto-merge-checkpoint-gate.py` | Command contains `gh pr merge` carrying `--auto` (bare, or `--auto=<value>` where `<value>` isn't `false`/`0`/`no` — mirrors `is_mutating_gh_segment`'s `--delete-branch=false` handling; `--disable-auto` is never in scope), excluding a `--help`/`-h`-only invocation (`is_merge_help_only`, dev-env#557) | Extends the sibling gate's marker check with a second one, `<!-- premerge-checkpoints: adr_warrant=<written\|not-warranted\|missing> doc_reconciliation=<updated\|not-applicable\|missing> -->`, emitted by `/review` Step 2f/Step 8 alongside the existing `review-findings` marker in the same comment. Requires the PR's single most recent comment carrying **both** markers together, with the findings marker clean-or-disposed, both checkpoints fields holding a valid (non-`missing`) value, and that comment's `createdAt` no older than the PR's head commit's `committedDate` (`gh pr view --json comments,body,number,commits`). **Blocks the merge (exit 2)** on any gap — no qualifying comment, open findings with no disposition, an incomplete checkpoints marker, or a stale marker. Unlike the sibling gate, **fails CLOSED on any `gh`/network error** (a deliberate inversion — `--auto` removes every other in-session backstop the moment it succeeds) and ships with **no override token**. A bare `gh pr merge` (no `--auto`) is completely unaffected; takes `is_pr_merge_command` from `_hookio` and reuses `pre-merge-findings-gate.py`'s `_parse_merge_target`/`_MARKER_RE`/`_DISPOSED_RE`/`_fetch_pr_json` via dynamic module load rather than duplicating them. Has a pure-function suite (`py -3 claude/scripts/tests/test_pre_auto_merge_checkpoint_gate.py`) and a behavioral self-test (`bash claude/scripts/tests/test-auto-merge-checkpoint-gate.sh`). [ADR-083](adr/083-auto-merge-checkpoint-gate.md) |
| `pre-merge-numbering-check.py` | Command contains `gh pr merge` (excluding a `--help`/`-h`-only invocation, `is_merge_help_only`, dev-env#557), `cwd` (or its `cd`-chain target, via `effective_merge_dir()`) resolves to the dev-env repo | Runs `git fetch origin main`, then reads the merge-base / branch-`HEAD` / `origin/main` snapshots of `CLAUDE.md`'s Testing section and `docs/adr/INDEX.md`'s ADR table. A number this branch newly introduces (absent at the merge-base) that `origin/main` has also claimed since the branch point **blocks the merge (exit 2)**, naming both colliding lines and the fix: rebase and renumber an ADR; drop the number from a Testing item, since new tests take none (dev-env#1309). A non-colliding sequencing gap is advisory only (`systemMessage`, exit 0). No-op in every non-dev-env repo. Fails open on any git/network/parse error. (ADR-074) |
| `pre-tool-use-canonical-mutate-guard.py` | `cwd` resolves to a canonical (non-worktree) git checkout root — or a `-C`/`--git-dir`/`--work-tree` flag redirects the invocation at such a root from elsewhere, e.g. from a worktree (dev-env#576, ADR-071 Amendment 2) — and the command contains a git-mutating segment (`checkout`, `switch`, `commit`, `merge`, `rebase`, `reset`, `cherry-pick`, `revert`, `stash pop`/`apply`, `branch -d`/`-D`, or `pull` without `--ff-only`) or a `gh pr merge` invocation carrying `-d`/`--delete-branch` (dev-env#558, ADR-071 Amendment 1 — same harm model reached through a `gh` invocation instead of a `git` verb; a bare `gh pr merge` stays unblocked, since it merges only remotely via the GitHub API) | **Blocks the command (exit 2)** — two Claude Code sessions sharing one canonical checkout can otherwise collide (one session's `checkout`/`commit`/`reset` silently thrashes HEAD out from under the other; see dev-env#453). The block keys off the resolved *target* root (cwd's, or the `-C`/`--git-dir`/`--work-tree` redirect target), not cwd alone; the engineering-journal checkout is a **permanent** redirect-target carve-out, not pending dev-env#346 (corrected dev-env#747/ADR-105 — #346 is a narrower, unrelated `biweekly-retro`-specific issue); the `_REDIRECT_TARGET_ALLOWLIST` constant's env-var-override resolution and comparison normalization are now shared with the other three engineering-journal carve-out hooks via `claude/scripts/_journal_canon.py` ([ADR-133](adr/133-shared-journal-canon-module.md)). No-op for an *ambient* (non-redirect) command from a confirmed-LIVE `.claude/worktrees/<name>` (`EnterWorktree`) or `<repo>-worktrees/<name>` (sibling-directory, manual `git worktree add`, dev-env#760/ADR-071 Amendment 5) cwd (ADR-024 covers that surface) or when git can't resolve a toplevel (fails open) — a worktree-shaped cwd that isn't actually a live, registered worktree (e.g. an orphaned directory whose `.git` link is missing) is no longer exempted on shape alone and falls through to the same canonical-root resolution an ordinary cwd gets (dev-env#749, ADR-071 Amendment 3). The same applies to an already-*resolved* target root (cwd's, or a redirect target's): it is exempted only when `git worktree list --porcelain` confirms it a LINKED (non-canonical) entry of its own repository, not merely because its path string looks worktree-shaped — an independently-cloned canonical checkout that happens to sit at a worktree-shaped path is no longer wrongly exempted (dev-env#774, ADR-071 Amendment 6; falls back to the old shape check only when git itself can't answer). Segments come from the shared `_hookio.split_top_level` engine (quote/subshell/heredoc-aware, dev-env#511/ADR-050 Amendment 7), so a quoted `&&`/`\|` or a heredoc body line (bare, or fed through a `$(cat <<'MARKER' ... MARKER)` command substitution) that merely *starts with* a mutating verb does not false-trigger (dev-env#481, generalized). Bypass with a genuine leading `ALLOW_CANONICAL_MUTATE=1` prefix. Has a behavioral self-test: `py -3 claude/scripts/tests/test_canonical_mutate_guard.py`. [ADR-071](adr/071-canonical-checkout-mutate-guard-hook.md) |
| `pre-tool-use-journal-draft-worktree-guard.py` | The command contains a top-level `git worktree add <path> ... <branch>` where `<branch>` matches `draft/YYYY-MM-DD` (or its `-recovery` suffix), from ANY cwd — or a `checkout`/`switch` of such a branch, ambient or via a `-C`/`--git-dir`/`--work-tree` redirect, whose resolved target is NOT the engineering-journal canonical | **Blocks the command (exit 2)** — git allows a branch checked out in only one worktree at a time, so either shape locks the shared `draft/YYYY-MM-DD` branch to a throwaway worktree and blocks the canonical (and every other concurrent stub-writing session) from reaching it, confirmed live twice on 2026-07-12 (dev-env#747). The inverse-direction sibling of `pre-tool-use-canonical-mutate-guard.py` above: that hook blocks a mutation targeting a canonical; this one blocks a checkout of one specific shared branch targeting anywhere BUT the canonical. `git checkout <branch> -- <path>` (a file restore, not a branch switch) is exempt; `worktree add -b <newbranch> ... draft/YYYY-MM-DD` (the draft branch used only as a commit-ish startpoint for a differently-named new branch) is exempt. Deliberately duplicates (not imports) the sibling hook's git-redirect-parsing helpers — see that hook's own bug history (ADR-071 Amendment 2, five fixes) and ADR-105's Judgment calls for why. The `JOURNAL_REPO` constant's env-var-override resolution and comparison normalization ARE now shared with the other three engineering-journal carve-out hooks via `claude/scripts/_journal_canon.py` ([ADR-133](adr/133-shared-journal-canon-module.md)) — that duplication-tolerance judgment call applies only to the git-parsing helpers, not the journal-path pattern this ADR itself was the trigger to extract. Bypass with a genuine leading `ALLOW_JOURNAL_DRAFT_WORKTREE=1` prefix. Has a behavioral self-test: `py -3 claude/scripts/tests/test_journal_draft_worktree_guard.py`. [ADR-105](adr/105-draft-branch-worktree-squat-guard.md) |
| `pre-tool-use-journal-shell-write-guard.py` | A cheap pre-filter (`_might_write_journal_content` — plain `in` checks, not a regex; a first regex-with-lookarounds attempt measured *slower* than the walk it was meant to skip) passes, AND a top-level Bash/PowerShell segment's first physical line carries a genuine (non-quoted) `>`/`>>` redirect (either tool_name), a `tee [-a] <path>` invocation (tool_name=Bash only), a `node -e`/`py -c` invocation MENTIONING a journal path anywhere in its full segment text (either tool_name — the only detector not limited to the first physical line, since the retired serializer recipes place their hazardous path on a later line), or a PowerShell `Out-File`/`Set-Content`/`Add-Content`/`Tee-Object`/`New-Item ... -Value` invocation anchored to segment-start (tool_name=PowerShell only), whose target — the cmdlet's actual bound path argument for the PowerShell case, never any journal-shaped word merely mentioned elsewhere on the line — is shaped like a journal content file (`*.stub.md`, `*.manifest.jsonl`, `open-prs/<digits>.json`, `tiles/<digits>.json`) AND either contains a `sessions/` path component itself or is issued from a *cwd* resolving under the engineering-journal checkout (`_target_is_genuinely_journal` — an unrelated repo's own same-shaped file, e.g. an ML `*.manifest.jsonl`, does not block) | **Blocks the command (exit 2)** — every one of the four file kinds carries free prose or a path field that routinely breaks shell quoting (apostrophes, quotes, backticks, `$`, markdown code fences, a backslash-escaped quote in unquoted prose), either failing the write outright or silently corrupting the file (dev-env#904). Names the matched command/target and instructs the Write tool (create) or Edit tool (a targeted update) instead. `git add`/`commit -m "..." -- <path>`/`push` referencing these paths, `rm`/`Remove-Item` deleting a shard (the documented deletion mechanism), plain reads, directory-only scaffolding (`mkdir -p`, `New-Item -ItemType Directory` with no `-Value`), and a heredoc body merely mentioning a filename as prose are all unaffected — only a genuine redirect/cmdlet/tee/serializer-mention on the segment is ever inspected. A same-line heredoc opener (`cat <<'EOF' > path/to/x.stub.md`) is still detected: quote-masking neutralizes `<<` first so `_hookio`'s heredoc-opener handling doesn't consume the rest of the line as an unterminated declaration; a backslash-escaped quote in unquoted prose (`echo Claude\'s > ...`) is neutralized the same way, via a small quote-state-aware walker (not a blind substitution — that regressed a sibling case, a single-quoted string legitimately ending in a literal backslash). Bypass with a genuine leading `ALLOW_JOURNAL_SHELL_WRITE=1` Bash prefix (scoped to its own top-level segment only — an override on one segment does not exempt an unrelated hazard in a later one) or a standalone `$env:ALLOW_JOURNAL_SHELL_WRITE=1` PowerShell statement (applies forward to later segments, matching real PowerShell env-var semantics). No subprocess/git work — pure text detection, no `_winsubp` needed. Has a behavioral self-test: `py -3 claude/scripts/tests/test_journal_shell_write_guard.py` (63 cases). [ADR-129](adr/129-journal-shell-write-guard.md) + Amendment 1 (16 post-`/review` findings, dev-env#962) |
| `pre-tool-use-shell-content-write-guard.py` | A cheap pre-filter (`might_write_content` — plain `in` checks, not a regex, for the reason recorded under the sibling above) passes, AND a top-level Bash/PowerShell segment supplies file content as an **inline literal**: a heredoc/here-string body redirected to a file (or fed to `tee`), an `echo`/`printf` argument redirected to a file, a `node -e`/`py -3 -c`/`python[3] -c` script that either calls a recognized file-write (`writeFileSync`, `open(…, 'w')`, `write_text`, …) or is itself redirected to a file, a `sed -i`/`perl -pi -e` script (whose target *is* the file, so it needs no separate destination), or — tool_name=PowerShell only — an `Out-File`/`Set-Content`/`Add-Content`/`Tee-Object`/`New-Item -Value` cmdlet fed a `-Value` literal or a `@'…'@` here-string piped in from an earlier pipeline segment. `/dev/null`, `$null`, and fd duplications (`>&1`) are not file targets. | **Blocks the command (exit 2)** when the literal is multi-line or carries an apostrophe, backtick, or backslash — the same sentence `claude/CLAUDE.md` → Authoring File Content states, so guidance and mechanism cannot drift. Names the mechanism, command, target, and specific hazard, and instructs Write (create/replace) or Edit (targeted change). **Program-output redirection is never matched, structurally, not heuristically** (`gh … > "$TMPFILE"`, `npm test > out.log`, `git diff > patch`, `… \| tee f`): no inline literal is found, so no detector fires — this is what makes a fleet-wide guard safe, and it is exactly the "short, known-safe, machine-generated" carve-out the prose rule names. A short, single-line, marker-free literal (`echo done > flag.txt`) also passes at the detector layer, not by override. Two hazard tests, not one: a heredoc body is raw content tested literally (a **quoted** delimiter is deliberately *not* an exemption — two of dev-env#1041's three failures used it), while a shell-quoted argument is walked for quote state so delimiting quotes count as structure and a backslash is flagged only *outside* single quotes — which keeps `sed -i 's/a\.b/c/'` working while `sed -i "s/a\\b/c/"` blocks, as a consequence of the general rule rather than a `sed` special case. Two accepted scope gaps, pinned by explicit tests rather than left silent: a heredoc to a *command's stdin* (`gh pr create --body-file - <<'EOF'`) has no file destination and is not matched, and only the first heredoc opener on a line is inspected. Bypass with a genuine leading `ALLOW_SHELL_CONTENT_WRITE=1` Bash prefix (scoped to its own segment) or a standalone `$env:ALLOW_SHELL_CONTENT_WRITE=1` PowerShell statement (applies forward); ADR-129's `ALLOW_JOURNAL_SHELL_WRITE=1` is honoured too, so a deliberate journal override cleared at that hook is not then blocked here. Wired *after* the journal guard so a journal path keeps that hook's more specific message. Shares its shell-syntax primitives with that guard via `_shell_write_detect.py`. No subprocess/filesystem work — pure text detection, no `_winsubp`. Has a behavioral self-test: `py -3 claude/scripts/tests/test_shell_content_write_guard.py` (47 cases, including a verbatim reproduction of each dev-env#1041 occurrence, and — copied from live instruction files — the repo's own `echo "$(cmd)" > f` and multi-line read-only `node -e` shapes as must-allow cases). [ADR-138](adr/138-shell-content-write-guard.md) |
| `pre-tool-use-journal-compose-force-guard.py` | The command is a git `worktree`/`commit`/`push` invocation whose non-message tokens (a `-m`/`--message`/`-F`/`--file` flag's *value* is excluded from the scan, so a commit message merely mentioning "draft/2026-07-09" as prose can't false-trigger) reference a `draft/<today>` or `compose[-/]<today>` target — "today" is this process's own `datetime.date.today()`, never anything the command claims — **and** the command or cwd identifies a compose worktree context (`compose[-/]<today>` in the resolved cwd path or in a non-message scan token, via `_is_compose_op`); plain stub-write pushes that reference only `draft/<today>` pass through unchanged (dev-env#728) | **Blocks the command (exit 2)** unless a fresh, `force == true` marker exists for today — written only by `journal-compose-force-resolve.py`, run as the literal first Bash action of `/journal-compose` Step 0.6 with the harness-substituted `$ARGUMENTS` text (before any of the agent's own reasoning about the invocation's intent). Mechanical enforcement of the `/journal-compose` today-guard (ADR-017): a 2026-07-08 scheduled-task transcript showed an agent reasoning its way past the guard's *prose* without ever running or reading it (dev-env#631). Every past-day compose (the nightly routine's normal path, ADR-084) and every other git/gh command never reaches the trigger condition at all. Deliberately **fails closed** on a missing/corrupt/stale marker (a reversal of this hook family's usual fail-open convention — justified by this hook's narrow, already-rare trigger) and ships with **no override token** (recovery is re-running the resolve script, never a bypass). Freshness window is a generous 4 hours (`MAX_MARKER_AGE_SECONDS`), covering a realistic multi-project compose with subagent research. `journal-compose-force-resolve.py` also sweeps markers from earlier calendar dates (`_journal_compose_force.cleanup_stale_markers`, 30-day default) on each invocation — safe because a marker is only ever consulted on the same real day it's written (dev-env#768) — including a second sweep for any orphaned atomic-write `.tmp` a rare `os.replace` failure in `write_marker` could otherwise leave behind uncleaned (dev-env#806, mirroring the identical `record_heartbeat` `.tmp` sweep described above). Has pure command-classification tests plus a behavioral self-test: `py -3 claude/scripts/tests/test_pre_tool_use_journal_compose_force_guard.py`. [ADR-096](adr/096-journal-compose-mechanical-force-guard.md) |
| `disk-space-check.py` (PreToolUse) | Every Bash call | Re-checks free `C:` space before each Bash call, not just once per prompt — closes the gap where a long tool-call-only stretch (e.g. an `npm install` mid-turn) could exhaust disk with no intervening prompt to re-trigger the `UserPromptSubmit` registration of this same script. Same thresholds, same messages, same detached-reclaim spawn, and the same per-session marker-file gate as the `UserPromptSubmit` entry (see the UserPromptSubmit table above) — whichever entry fires first for a session covers the other. `shutil.disk_usage()` is a syscall, not a subprocess spawn, so this adds negligible overhead to every Bash call. [ADR-087](adr/087-pretooluse-disk-space-check.md) |
| `pre-bash-drift-check.py` | Every Bash call, gated by elapsed time (not command content) | A fourth cwd/branch drift checkpoint alongside `pre-commit-branch-check.py`/`pre-pr-create-check.py`/`pre-merge-branch-check.py` (dev-env#682): those three only compare recorded vs. current repo/branch state at `git commit`/`gh pr create`/`gh pr merge`, missing a drift affecting any other Bash command. This hook runs the same `_bash_state.py`-backed comparison on every Bash call, but only pays the `git rev-parse --show-toplevel --abbrev-ref HEAD` subprocess once at least 60s (`MIN_GAP_SECONDS`) have elapsed since the last recorded call — a cheap file-mtime stat (`state_age_seconds()`) gates it otherwise, so back-to-back calls never spawn a subprocess. Targets the gap-shaped trigger observed after long background `Agent` calls specifically. Advisory only (`systemMessage`, exit 0), same reasoning as the other three: can't distinguish a legitimate `EnterWorktree`/`cd` from a silent revert. [ADR-101](adr/101-bash-drift-check-every-call.md) |
| `pre-tool-use-scratch-rm-allow.py` | Every Bash call (Bash matcher only: an approve-only hook carries no safety check, so it is exempt by name from the Bash/PowerShell mirror in `test_settings_hook_wiring.py`) | **Emits a PreToolUse `permissionDecision: "allow"`** (stdout JSON via `_hookout.emit_allow`, exit 0) when the command is a provable scratch-only delete, so the permission prompt is skipped. Settings deny/ask rules still apply, and a sibling hook's deny/ask outranks it ([precedence deny > defer > ask > allow](https://code.claude.com/docs/en/hooks#pretooluse-decision-control)). Every top-level segment (split on `&&`, `;`, newline) must be one of: a pure literal assignment `NAME=value` (no command substitution, every `$VAR` resolved from an earlier in-command assignment or `$HOME`); an absolute `cd <dir>` resolving inside or to scratch, which sets the base for relative targets only through an unbroken `&&` chain (after `;` or a newline the next segment runs even if the `cd` failed); or `rm` with flags only from `-f`/`-r`/`-R`/`-rf`/`-fr`/`--force`/`--recursive`/`--` and at least one target, each resolving (after variable/`~` expansion, `/c/` and backslash drive-form normalization, and `realpath`, case-insensitively on Windows) **strictly inside** scratch's realpath. At least one segment must be an `rm`. **Anything else gets no decision** (exit 0, no output): a non-rm segment anywhere in the command, any `\|\|` (its right side runs only when the left failed, so assignments across it can't be tracked), a relative `cd` (`$CDPATH`), any `..` component, `$(`/backticks, `\|`, a lone `&`, `<`/`>`, `(`/`)`, `{`/`}`, `!`, unterminated quotes, an unresolved variable, an unquoted expansion whose value carries whitespace or glob characters, a single-quoted `'$S'` (a literal, so a relative path), a relative target with no in-command `cd`, a UNC or drive-relative path, a glob in a directory component, a recursive flag on scratch itself or on a glob final component, or a target outside scratch. It has its own fail-closed lexer rather than `_hookio.split_top_level`/`_shell_write_detect`, because those detection primitives fail permissive by design (an unterminated tail segment is dropped), which would be an approval here. `~` and `$HOME` resolve only when the `HOME` environment variable is unset or names the same directory Python resolves as home (Git Bash expands them from `HOME`, Python reads `USERPROFILE` on Windows); a divergent, empty, relative, or MSYS-root `HOME` leaves them unresolved, so no decision. Never blocks; any unexpected exception means no decision. **Approval log:** each approval appends one `<UTC ISO timestamp> <reason>` line (the reason names every resolved target) to `~/.claude/scratch/scratch-rm-allow.log`, written just before the allow is emitted; best effort (any write error is swallowed and never changes the decision or exit code), rotated to `scratch-rm-allow.log.1` past 1 MB. Scratch is `_hookutil.SCRATCH`; test-only override: `SCRATCH_RM_ALLOW_DIR_OVERRIDE` (the log follows it). Spawns no process and loads neither `subprocess` nor `_winsubp`, directly or through its imports: `_hookio` imports both lazily inside `confirm_merge_via_gh`, which this hook never calls (PR #1135 review finding 5; that cut `_hookio`'s import cost from ~27-40 ms to ~7 ms on every Bash call, pinned by `test_hookio.py`). Self-test: `py -3 claude/scripts/tests/test_scratch_rm_allow.py`. [ADR-147](adr/147-scratch-only-rm-auto-allow-hook.md) |

#### Write / Edit / NotebookEdit hooks

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-tool-use-worktree-path-check.py` | Session `cwd` is inside a Claude-managed worktree — either the nested `.claude/worktrees/<name>` (`EnterWorktree`) convention or the sibling-directory `<repo>-worktrees/<name>` convention reached via manual `git worktree add` (dev-env#760/ADR-024 addendum) — and either (a) the worktree is **orphaned** — its `.git` link is missing or `git rev-parse --show-toplevel` does not resolve to the worktree root — or (b) `file_path`/`notebook_path` is absolute and starts with the canonical repo root | **Blocks** the tool call (exit 2). For an orphaned worktree, the message names the worktree + cwd and renders the recovery recipe from `_worktree_recovery.RECOVERY_STEPS` — the same definition the [Worktree deregistration recovery](#worktree-deregistration-recovery-lost-git-link-routes-git-to-main) runbook is pinned against, so the message a blocked session reads and the runbook cannot drift apart (dev-env#862, [ADR-116](adr/116-single-source-worktree-recovery-recipe.md); before that this row, the hook, and the runbook were three hand-maintained copies, and the hook kept the `--force` recipe dev-env#751 had already disproven). Covers all writes from the orphan, not just canonical-root paths. Otherwise the message names the attempted path, the active worktree root, and the corrected path. No-op when the session is not in a worktree, or (for case b) when the path already targets the worktree root, or (for case c) when the path targets another worktree under the same canonical root (e.g., a compose session writing to `compose-YYYY-MM-DD` from within a different EJ worktree — dev-env#750), or (for case d) when the *session's own* resolved canonical root is the engineering-journal canonical path — every write under it is allowed regardless of target shape, since the Stub file workflow's primary write pattern (`sessions/<project>/<file>` with no worktree segment at all) is the case (c) carve-out never covered (dev-env#750, reopened; ADR-024 2026-08-15 addendum; mirrors `pre-tool-use-canonical-mutate-guard.py`'s `_REDIRECT_TARGET_ALLOWLIST`, ADR-071 — the env-var-override resolution behind `_JOURNAL_ROOT` is now single-sourced with the other three engineering-journal carve-out hooks via `claude/scripts/_journal_canon.py`, [ADR-133](adr/133-shared-journal-canon-module.md)). The path-shape regex is now only a cheap PRE-FILTER: once matched, `git worktree list --porcelain` confirms (or corrects) the candidate canonical/worktree roots before anything is enforced, so a repo whose own root directory name literally ends in `-worktrees` (e.g. `some-repo-worktrees`) no longer has every subdirectory misclassified as a worktree name (dev-env#774 gap (b), ADR-024 addendum; falls back to the regex + liveness check only when git itself can't answer). The liveness check runs one `git worktree list` (previously `git rev-parse`) per file write in a worktree, short-circuited when the `.git` link is already missing. **Bypass for intentional canonical edits:** use `Bash` with `node -e`, `sed`, or `python3` — the hook only covers the three file tools, not `Bash`. [ADR-024](adr/024-worktree-path-guard-hook.md) |
| `pre-tool-use-skill-file-size-guard.py` | `Write`/`Edit` (not `NotebookEdit`) targets a file whose basename is `SKILL.md` (case-insensitive, any project) | **Blocks** the tool call (exit 2) when the resulting file size would GROW past a configurable byte ceiling (`skill_file_size_limit_bytes` in `.claude/hook-config.json`, default 262144 / 256KB) — the predicate is `resulting_size > limit AND resulting_size > current_on_disk_size`, so an edit that shrinks an already-oversized file (but doesn't single-handedly land under the limit) is still allowed, letting an oversized file be trimmed incrementally across multiple edits. For `Write`, the size is the UTF-8 byte length of `tool_input.content`; for `Edit`, the hook reads the current on-disk file and applies the same `old_string`/`new_string`/`replace_all` substitution the tool itself will apply, on a line-ending-normalized copy (the real Edit tool matches `old_string` with line endings normalized to `\n`, then writes `new_string` back converted to the file's own line ending — comparing raw bytes instead would silently never match a multi-line `old_string` against a CRLF file, disabling the guard entirely). Fails open when the file can't be read, `old_string` is empty, `old_string` isn't found, or `old_string` occurs more than once with `replace_all` not set (the real Edit tool independently refuses all of these). Strictly greater-than the limit blocks — exactly at the limit passes. No published Anthropic byte limit exists for `SKILL.md`; the default reflects a real, observed runtime constraint rather than a documented one. Basename-match and `.claude/hook-config.json` loading are shared with `skill-file-size-advisory.py` via `_skill_file_size.py`. Has a behavioral self-test: `py -3 claude/scripts/tests/test_skill_file_size_guard.py`. [ADR-127](adr/127-skill-file-size-guard.md) |

#### Agent hooks

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pre-tool-use-nested-agent-background-guard.py` | The call's `tool_name` is `Agent`, `agent_id` is present (fires inside a subagent — the documented nesting signal), `tool_input.run_in_background` is omitted entirely, and an earlier nested call in the same session carried the field (recorded as `scratch/nested_agent_rib_seen_<session_id>.flag`; without that evidence an omission passes, since harness builds whose subagent `Agent` tool has no such parameter can never send it — dev-env#1158, ADR-126 Amendment 2026-10-24) | **Blocks the call (exit 2)** — the exact failure signature behind career-playbook's [ADR-090](https://github.com/brownm09/career-playbook/blob/main/docs/adr/090-synchronous-subagent-spawns.md)/PR #749 orphaned-subagent stalls: a nested spawn relying on the `Agent` tool's implicit `run_in_background: true` default does not block the spawning turn, and the orphaned child's completion routes to `general-purpose`/`main` instead of back to the subagent that spawned it, silently stalling until a human manually resumes it via `SendMessage` (confirmed recurring three times in career-playbook, not a one-off). The block message explains the mechanism and the one-line fix. An explicit `run_in_background` value (`true` *or* `false`) passes through untouched — a deliberate nested-parallel fan-out (Claude Code's own docs: "a reviewer subagent that dispatches a verifier per finding") is not punished, only an omitted field is. A top-level spawn (no `agent_id`) is untouched entirely; backgrounding there is the normal, documented default. No override token — the fix is always a one-line addition to the same call. Fails open on any payload-shape anomaly (malformed JSON, non-`Agent` tool, missing/non-dict `tool_input`). Preceded by an observation-only smoke-test stage (dev-env#936) that confirmed live, with real data, that this hook fires for both a top-level spawn (no `agent_id`) and a genuinely nested one (`agent_id` populated) before the enforcement logic shipped. Has a behavioral self-test: `py -3 claude/scripts/tests/test_pre_tool_use_nested_agent_background_guard.py`. [ADR-126](adr/126-nested-agent-spawn-background-guard.md) |

---

### PostToolUse

Fires after a matched tool call completes. Matcher values are set per entry in `settings.json`.

**One process for the Bash/PowerShell group.** The ten Bash/PowerShell hooks in the table below are
wired as a single command, `hook-launch.py hook-dispatch.py <ten handler paths>`. `hook-dispatch.py`
runs each handler as `__main__` from its own file, concurrently in threads, with its own stdin
payload, routed streams, time budget (the timeout it had as its own command) and heartbeat. It
combines their output: blocks (exit-2 texts, or an exit-0 JSON `decision: "block"`) on stderr with
exit 2, `systemMessage`s as one JSON message, or, when both occur (on a merge),
`{"decision": "block", "reason": ..., "systemMessage": ...}` so the user's messages aren't dropped. A
handler that misses its budget, exits 1, or emits a JSON key the dispatcher can't carry becomes a
warning; a missing handler gets the launcher's throttled warning; the dispatcher fails open.
For a dispatched handler, the duration ledger's `run_ms` spans compiling and importing the handler
too, and `startup_ms` is always null; compare the `hook-dispatch` row against the old per-hook rows. This cuts spawns per Bash call for this event from 10 to 1. The structural gates, the wiring lint
and `hook-liveness-check.py` read every handler path in the command. See
[ADR-151](adr/151-in-process-hook-dispatcher.md), dev-env#851.

> **Inert sessions:** every PostToolUse hook below can be **silently inert** in a session, while the
> `UserPromptSubmit`, `PreToolUse`, and `Stop` hooks from the same `settings.json` still fire. This is
> rare and has not been seen since 2026-10-14. It is an upstream Claude Code Desktop limitation, not a
> hook defect: no change here can invoke an un-invoked hook. No PostToolUse attachment doesn't mean
> this, because a hook only leaves one when it prints. To check, see whether
> `~/.claude/scratch/bash_state_<session-id>.json` was updated at the last successful shell call. The
> measurements and the manual-fallback recovery are in
> [ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md) and its 2026-11-07 amendment.

#### Bash hooks

Matched with `"matcher": "Bash"`.

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `pr-merge-reminder.py` | Command contains `gh pr create`, `gh pr merge`, or `git push` (when the pushed branch has an open PR); a `--help`/`-h`-only `gh pr merge` never reaches the merge reminder's live-confirmation fallback (`is_merge_help_only`, dev-env#557). Also recognizes the two-step REST merge fallback (`gh api -X PUT .../pulls/<N>/merge`, used when `gh pr merge` itself is unavailable, e.g. a GitHub GraphQL rate-limit outage) as an independent path to a fired merge reminder — gated on `is_rest_merge_command(command)` AND `output_has_rest_merge_marker(output)` finding `"merged":true`, added to `main()`'s top-level gate and `_build_messages`' `merge_ok` computation as an OR-branch alongside the original `is_merge`/marker check, but deliberately NOT wired into the live `gh pr view` confirmation fallback (that stays scoped to the original `gh pr merge` shape only — ADR-050 Amendment 23's scope decision) (dev-env#986, dev-env#991) | Exits 2 with a `systemMessage` reminding Claude to write a journal stub. For `gh pr create`, also emits steps `3a`/`3b` to write and stage the `open-prs/<N>.json` shard (ADR-056); parses the PR URL from `tool_response.stdout` via `_hookio.read_command_output` so the reminder can include the PR number and URL when available. A create counts only if it printed its URL alone on a line (`created_item_url`), because the real payload has no `exitCode` and a failed create in a chain otherwise looks like a success; empty output, as with `run_in_background`, keeps a generic reminder (dev-env#1243, ADR-050 Amendment 31). The reminder's `repo:` field resolves via `_effective_create_repo`: an explicit `--repo`/`-R` flag names the target repo directly, so a cross-repo `gh pr create` (e.g. run from a different project's cwd) is attributed correctly instead of always reporting cwd (dev-env#646, ADR-050 Amendment 18) — falls back to plain cwd when no flag is present, since this branch has no cd-chain-aware dir to fall back to. For `git push`, scopes the lookup to the repo the push **actually targets** — `_effective_push_dir` honors a `cd <path> && git push` chain so a cross-repo push is evaluated against that repo, not the session cwd — then runs `git branch --show-current` and `gh pr list --head <branch>` there to confirm an open PR, and fires on **every qualifying push** to the correct repo (each carries new journalable content — scoping, not dedup, is what removes the cross-repo noise). Skips `engineering-journal` pushes (handled by `stub-push-archive-reminder.py`). For `gh pr merge`, the same scoping applies on the merge side: `effective_merge_dir` resolves a `cd <path> && gh pr merge` chain so the reminder's `repo:` field (and step 1's journal-path lookup) name the merged PR's actual repo, not the session cwd; for the REST merge shape, `_effective_merge_repo` uses cwd directly instead — `effective_merge_dir` has no cd-chain convention for that shape and would wrongly read a `cd` occurring AFTER the REST call as governing it. Wrapped so any internal error exits 0 (never crashes the push flow); the pure `_effective_push_dir` and `effective_merge_dir` helpers are unit-tested by `tests/test_pr_merge_reminder.py`. [ADR-021](adr/021-auto-stub-on-pr-push.md), [ADR-065](adr/065-scope-push-reminder-to-target-repo.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md), [ADR-050 Amendment 23](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `post-merge-tile-checkpoint.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0 — mirrors `post-pr-merge-reclaim.py` / `post-pr-merge-pull.py`, dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557 — otherwise it could misattribute cwd's current branch's already-merged PR to a harmless `--help` check); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986) | Exits 2 with a `systemMessage` reminding Claude to spawn follow-up tiles now via `start_session` for any out-of-scope fixes, deferred work, or ideas surfaced during the session. Only an explicit user "skip tiles" instruction exempts the checkpoint (ADR-046). Global — no opt-in. Handles worktree merges that exit non-zero via the output marker. The pure `is_successful_merge()` helper is unit-tested by `tests/test_post_merge_tile_checkpoint.py`. No subprocess calls — no `_winsubp` needed. [ADR-046](adr/046-post-merge-followup-tiles.md), [ADR-060](adr/060-post-merge-tile-checkpoint-hook.md), [ADR-050 Amendment 23](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `post-tool-use.py` | Command contains `gh issue create` or `gh pr create` | Auto-adds the created item to the configured GitHub Project (only a URL the create printed alone on a line counts, `created_item_url`, dev-env#1243; with none, it exits 2 with an advisory and adds nothing), then exits 2 with a `systemMessage` listing the exact `gh project item-edit` commands to set any `required_fields` defined in `hook-config.json` — for a `single_select` field, the options shown are live-fetched via `gh api graphql` and only fall back to the cached `hook-config.json` value (labeled) if that fetch fails (ADR-076, dev-env#527). Opt-in via `project_number` + `project_owner` in `.claude/hook-config.json`. In a worktree session whose config copy is absent (a project that gitignores it, dev-env's own convention — not every project's), `load_config` resolves the **canonical checkout's** copy (regex `canonical_root_from_worktree`, shared with `reconcile-project-board.py` via `_worktree_canon.py`, for Claude-managed worktrees; `git rev-parse --git-common-dir` for siblings like `dev-env-188`) so the hook fires there too. When even that misses and the command names an explicit `--repo owner/name` for a DIFFERENT repo — a common cross-repo filing pattern that otherwise always silently no-oped, since `load_config` never inspected the command at all (dev-env#532, #537) — `load_config` looks for that repo as a sibling checkout under the same parent directory `reconcile-project-board.py --scan-dir` already scans, trusting it only when the sibling's own `hook-config.json` self-reports a matching `repo` field, never a directory-name guess alone (`extract_repo_flag` / `_sibling_repo_config`). When every lookup misses and the created item's URL is in the `repo` of dev-env's tracked `claude/hook-config.shared.json`, the hook exits 2 with a `NOT added to project` warning carrying the seed command and the manual `gh project item-add`, instead of the silent exit 0 every other config-less repo gets (`expected_opt_in_config`, dev-env#669). Reads output via the shared `_hookio.read_command_output`; adds the item via the shared `_gh_project.add_to_project` (also used by `reconcile-project-board.py`). [ADR-023](adr/023-generic-required-fields-issue-hook.md), [ADR-049](adr/049-hook-payload-output-field.md), [ADR-052](adr/052-worktree-config-canonical-fallback.md), [ADR-073](adr/073-shared-worktree-canon-gh-project-modules.md), [ADR-076](adr/076-live-fetch-project-hook-single-select-options.md), [ADR-077](adr/077-cross-repo-config-resolution-for-issue-pr-create.md), [ADR-050 Amendment 31](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `post-pr-merge-pull.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0, and worktree merges exit non-zero on local cleanup despite succeeding — dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986) | Fast-forwards the local `main` branch via `git fetch origin main:main` so the local clone stays current after a merge, then **parks the just-merged worktree off `main`** if `gh --delete-branch` left it squatting the ref (only possible when the canonical had freed `main`) — recreating its `claude/<slug>` branch at HEAD via `merge_park_target` (`_worktree_topology.py`), acting on the hook's own session worktree so no liveness check is needed. Reads output via the shared `_hookio.read_command_output`; the pure `is_successful_merge()` helper is unit-tested by `tests/test_post_pr_merge_pull.py` and the park decision `merge_park_target` by `tests/test_worktree_topology.py`. `extract_repo` resolves the merged repo via `--repo` flag, GitHub PR URL, the REST merge fallback's own path (dev-env#986 — checked before the cd-chain/git-remote fallbacks below, since the REST path always names its target repo explicitly), or (falling back) `effective_merge_dir`-scoped `git remote get-url origin` — so a merge run via a `cd <path> &&` chain still fast-forwards the right repo's `main`, not the session cwd's. [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md) |
| `post-pr-merge-reclaim.py` | Command contains `gh pr merge` and the output confirms a completed merge (gh's success marker; the exit code is not consulted — `--help` and a queued `--auto` also exit 0, and worktree merges exit non-zero on local cleanup despite succeeding — mirrors `post-pr-merge-pull.py`, dev-env#485); when the marker is absent, a `--help`/`-h`-only invocation is also excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986) | Spawns `reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --protect-cwd <cwd>` **detached** (via `sys.executable`, never the `py` launcher) to strip regenerable `node_modules`/`.turbo` from now-idle merged worktrees — the dominant `C:` consumer — at the idle event instead of waiting for the 6-hourly routine. No `--min-free-gb` (the trigger is the merge, not low space); `--protect-cwd` shields the active worktree. Does **not** remove the worktree directory/branch — that requires an out-of-process context (Windows cwd lock) and stays the daily `prune-stale-worktrees` job. Informational only — exit 0 always. Reads output via the shared `_hookio.read_command_output`; the pure `is_successful_merge()` helper is unit-tested by `tests/test_post_pr_merge_reclaim.py`. [ADR-045](adr/045-pre-install-freespace-gate.md), [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `post-pr-merge-project.py` | Command contains `gh pr merge` and the output confirms a completed merge; when the marker is absent, a `--help`/`-h`-only invocation is excluded from the live-confirmation fallback (`is_merge_help_only`, dev-env#557 — the confirmed live incident: `--help` was previously misattributed as a completed merge, moving an unrelated issue's project item to Done); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986 — this widened the initial command-shape gate, which previously exited before the marker check ever ran for a REST-only merge) | Auto-moves the linked issue (`Closes/Fixes/Resolves #N` in PR body) to Done on the configured GitHub Project. Derives the PR number from the command (`gh pr merge <N>` / a `/pull/N` URL / the REST fallback's own `.../pulls/<N>/merge` path — `resolve_command_pr_number`, dev-env#986), falling back to gh's success marker in the output (`gh pr merge` output has no `/pull/N` URL); gated on a confirmed-merge marker so a queued `--auto` or a failed merge never moves an issue to Done. `resolve_command_repo` (dev-env#986) also derives the repo from a PR URL argument or the REST fallback's own path, and the hook skips entirely when that differs from cwd's own configured `repo` — cwd's `project_number`/`project_node_id`/`status_field_id`/`done_option_id` are scoped to cwd's repo and don't apply to a different one, so a cross-repo merge is a safe no-op rather than a wrong-board move (dev-env#559). A `cd`-chained cross-repo merge with no URL is now covered too: `load_config` resolves via `effective_merge_dir(command, cwd)`, so the merged repo's own `hook-config.json` is read rather than cwd's (dev-env#569, ADR-111 — matching this hook's two merge-triggered siblings); resolving the correct repo's own config to complete the move *instead of* skipping remains a separate enhancement (dev-env#571). `extract_repo_from_command`/`extract_pr_number_from_command` delegate to the shared `_repo_target` resolver (ADR-111); `resolve_command_repo`/`resolve_command_pr_number` layer the REST-path fallback on top, split out of `main()` for independent testability. Opt-in via `status_field_id` and `done_option_id` in `hook-config.json`. Reads output via the shared `_hookio.read_command_output`; pure helpers unit-tested by `tests/test_post_pr_merge_project.py`. [ADR-014](adr/014-auto-move-project-item-done-on-merge.md), [ADR-049](adr/049-hook-payload-output-field.md), [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md), [ADR-111](adr/111-shared-repo-target-resolution.md) |
| `usage-snapshot.py` | Command contains `gh pr merge`, excluding a `--help`/`-h`-only invocation from the marker-absent live-confirmation fallback (`is_merge_help_only`, dev-env#557); also recognizes the two-step REST merge fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge` whose response body carries `"merged":true`, dev-env#986) | Queries `https://api.anthropic.com/api/oauth/usage` (via OAuth Bearer token from `~/.claude/.credentials.json`) and parses the session JSONL for the top-5 costliest exchanges. Emits a `### Usage Snapshot (post-merge)` markdown block showing weekly/5-hour utilisation vs. day-of-week soft targets (configured in `claude/usage-config.json`). Global — fires for all repos without opt-in. Include the emitted block verbatim in the post-merge journal stub, or the `[usage-snapshot] Skipped:` line if it prints that instead. A still-valid "expiring" token is used (not skipped). A missing credentials file, a missing, blank or malformed token, or an expired or rejected (HTTP 401 or 403) token skips the snapshot with a `[usage-snapshot] Skipped:` stderr advisory naming the command that fixes it (`claude auth login`, or for an expired token also running `claude` in a terminal). An unexpected error while building the snapshot also prints a `Skipped:` advisory, naming only the exception's type, so a confirmed merge is never silent. The hook makes no model call to refresh a token, because a status readout isn't worth one ([dev-env#1299](https://github.com/brownm09/dev-env/issues/1299), [dev-env#1139](https://github.com/brownm09/dev-env/issues/1139); amendments to [ADR-044](adr/044-eliminate-usage-snapshot-gap-on-demand-refresh.md) and [ADR-124](adr/124-usage-snapshot-desktop-app-keychain-deadend.md)). If the usage API is unreachable, the script retries once after 1 second; if both attempts fail it emits a stderr advisory and exits 2 (so the failure is visible rather than silently skipped — [#302](https://github.com/brownm09/dev-env/issues/302)). The merge-confirmation decision itself (`resolve_merge()`) is a pure function returning one of seven `reason`s (`marker`/`rest_marker`/`not_merge_shape`/`help_only`/`no_confirm_needed`/`gh_view_confirmed`/`gh_view_unconfirmed`); every merge-shaped invocation, confirmed or not, is appended as one best-effort JSON line to `C:/Users/brown/.claude/scratch/usage-snapshot-merge-trace.log` (`_log_merge_trace`, never raises) — added after two live reproductions post-dating the original [dev-env#474](https://github.com/brownm09/dev-env/issues/474) fix (PR #954, PR #988) both saw no snapshot with no record of which fallback branch ran, so the next occurrence has permanent forensic evidence instead of requiring a live-instrumented reproduction. |
| `stub-push-archive-reminder.py` | `git push` to `engineering-journal` with a stub commit and no unresolved open PR in the touched manifest(s) | Writes a **per-session** sentinel file (`~/.claude/scratch/stub-pushed-<session_id>.flag`, via `_hookutil.sentinel_path` — dev-env#980, [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) Amendment 2) and exits 0; a request with no `session_id`, or one containing anything outside `[A-Za-z0-9_-]`, is forgone entirely rather than falling back to a shared/degenerate path (the pre-fix global `stub-pushed.flag` let ANY concurrent session's Stop consume it, causing both false-positive archive instructions and missed reminders — the old filename is also opportunistically unlinked once, since it predates the per-session naming and the new cleanup glob never matches it). Verifies the most-recent commit in the journal repo touches a `.stub.md` file before writing the flag. Also requires that none of the touched stub(s)' paired manifest shard(s) show an unresolved open PR (a PR number in `prs_opened` not also in `prs_closed`) — a stub is pushed immediately after `gh pr create` and again after each subsequent push, well before `/review` and `gh pr merge` ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) Amendment 1); each touched stub's paired manifest is read from its **current** on-disk content via `head_commit_has_unresolved_pr`/`manifest_path_for_stub`, not gated on whether the triggering commit's own diff touched that manifest, since an ordinary mid-session stub-only push doesn't re-touch it. Also runs `_hookutil.cleanup_stale_sentinels` on its own (rare) success path — `journal-stop-check.py` runs the same sweep on every Stop, which is the more reliable backstop for a crashed/never-Stopped session's orphaned flag. The Stop hook (`journal-stop-check.py`) consumes only the sentinel matching its **own** `session_id` and issues the archive reminder on **stderr with exit 2** — blocking the stop so the Claude-facing reminder actually reaches Claude, since a Stop hook's exit-0 stdout is not added to Claude's context ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)). The push-failure guard (`has_push_error`, reading output via the shared `_hookio.read_command_output`), the command-shape predicate (`is_git_push_command`, `scan_top_level`-anchored — ADR-050 Amendment 10), the repo-reference predicate (`references_engineering_journal`, anchored to a `cd`/`git -C` directory argument rather than a CLI verb — ADR-050 Amendment 12), and the now-pure `most_recent_commit_has_stub`/`manifest_path_for_stub`/`head_commit_has_unresolved_pr` (ADR-091 Amendment 1) are unit-tested by `tests/test_stub_push_archive_reminder.py`. [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `journal-shard-write-advisory.py` | Command harvests a candidate `.manifest.jsonl` / `open-prs/<N>.json` / `tiles/<N>.json` path that resolves to a real file (also wired on `Write`/`Edit` — see below for the full entry) | See the full entry under **Write / Edit hooks** below. |
| `post-tool-use-cwd-track.py` | Every Bash call | Best-effort `git rev-parse --show-toplevel` + `git branch --show-current` against the payload's `cwd`, then writes `{repo_root, branch, cwd}` to a per-session state file (`~/.claude/scratch/bash_state_<session_id>.json` via the shared `_bash_state.py` module). Feeds the drift-warning check in `pre-commit-branch-check.py`, `pre-pr-create-check.py`, `pre-merge-branch-check.py` — dev-env#573's mitigation for a session's tracked cwd/branch silently reverting with no error surfaced — and, via the same file's mtime, the elapsed-time gate in `pre-bash-drift-check.py` (dev-env#682, [ADR-101](adr/101-bash-drift-check-every-call.md)). A cwd that isn't a git repo, or a `git` call that fails/times out, records `None` rather than raising. Exit 0 always; no `systemMessage`, purely a side-channel write. [ADR-085](adr/085-bash-repo-branch-drift-detection.md) |

#### Write / Edit hooks

Matched with `"matcher": "Write"` and `"matcher": "Edit"` (also wired on `"matcher": "Bash"` — see the Bash table above).

| Script | Trigger condition | What it does |
|--------|------------------|-------------|
| `memory-write-advisory.py` | The `Write` tool targets a `.md` file inside a `…/memory/` directory (not the `MEMORY.md` index) **and** the written body carries no immortalization link — no `#\d+` issue/PR ref, no `ADR-\d+`, no `CLAUDE.md`, no "Documented in repo" | Emits a one-line stderr reminder and exits 2 (the `Write` already ran — exit 2 *surfaces* the reminder, it does not block) telling Claude to pair the durable memory with an immortalization issue and link it from the memory body + `MEMORY.md`. The link-absence heuristic keeps it quiet on writes that already carry a link; the agent (not the hook) judges durability. Spawns no subprocess; fails open (exit 0). The pure `should_advise_memory_write()` helper is unit-tested by `tests/test_memory_write_advisory.py`. [ADR-048](adr/048-memory-immortalization-issue-pairing.md) |
| `journal-shard-write-advisory.py` | Write/Edit: `file_path` is an engineering-journal manifest shard (`sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`), open-PR shard (`sessions/<project>/open-prs/<N>.json`), or tile shard (`sessions/<project>/tiles/<issue-number>.json`) — classified by path component, not anchored to the canonical checkout, so a shard under a Claude-managed journal worktree still matches. Bash: any `.manifest.jsonl` / `open-prs/<N>.json` / `tiles/<N>.json`-shaped token harvested from the raw command text that resolves to a real file (against `cwd`, a harvested `cd`/`git -C`/`--git-dir=` directory, or the constant `~/Git/engineering-journal` fallback) | Reads the file's on-disk bytes (not the tool-call payload) and validates them against the schema shared with `validate-manifest.py` via `_journal_schema.py`: missing required fields (schema order), a UTF-8/UTF-16 BOM (named, not left as an opaque parse failure — Node `JSON.parse` and the Python shard readers both silently choke on it), an empty shard, non-JSON-object content, and — for the two numeric kinds (open-PR and tile), reusing `_journal_shards.shard_number` — a non-numeric filename or a filename/embedded-number mismatch. **Tile shards** (`sessions/<project>/tiles/<issue-number>.json`, [ADR-118](adr/118-tile-persistence-shards.md), added in dev-env#870) are the kind whose write is otherwise wholly unverified: a manifest or open-PR shard is written by a session already doing PR bookkeeping, whereas a tile shard is written right after the tile proposal whose payload it preserves — so a malformed one fails *silently* (`iter_numeric_shards` skips it without a word) *and* late, surfacing only when someone needs the payload back after a crash, i.e. exactly when it can no longer be reconstructed. They validate against `TILE_REQUIRED_FIELDS` / `missing_tile_fields` (`issue`, `url`, `title`, `tldr`, `prompt`, `cwd`, `spawned`; `stub` optional), and a filename/embedded-`issue` disagreement is flagged with the consequence spelled out — `reconcile-pending-tiles.py` treats such a shard as corrupt and refuses to reconcile it, which silently exempts that tile from pruning forever. Presence alone proved too weak a bar for `cwd`, the one required field that is a filesystem path: a Windows path written through a double-quoted `node -e` serializer crosses a JS string literal that eats its backslashes (`C:\Users\brown\Git\dev-env` → `C:Users<U+0008>rownGitdev-env`), producing a shard that exists, parses, and carries all seven fields while naming no directory — so the exact re-spawn it exists to enable is already lost and nothing downstream would ever notice (`reconcile-pending-tiles.py` reads `url` and the filename, never `cwd`, and no compose-time gate reads tile shards at all). `malformed_tile_fields` therefore also checks `cwd` *shape* — non-string, empty, any control character (reported alone, since it names the cause), surrounding whitespace, or not absolute (a drive-letter root, a UNC root, or POSIX absolute; a single-backslash `\Users\…` is drive-relative and stays flagged) — while deliberately **not** flagging a correctly-escaped backslash path (a valid value; the forward-slash prescription is a docs rule, not a validation one) or a well-formed path that does not exist on this machine (shards are read on other machines, and this module is offline/import-only). Three live shards, dev-env#904 / [ADR-081](adr/081-write-time-journal-shard-validation-hook.md) Amendment 2. On any problem, exits 2 with a stderr advisory naming each file and its problems, plus all three schema templates and the rewrite-with-the-Write-tool warning ([ADR-129](adr/129-journal-shell-write-guard.md) — never a shell mechanism at all, retired from this message alongside the corresponding `pre-tool-use-journal-shell-write-guard.py` PreToolUse block) (the write already happened — this surfaces the gap immediately instead of at the next day's `/journal-compose` Step 0.7 gate). Silent (exit 0) otherwise. The Bash token harvest is a raw regex scan, deliberately **not** `_hookio.scan_top_level`-anchored — it validates on-disk data, not command intent, so a path merely mentioned inside a heredoc/quoted argument/subshell is harmless to check. Spawns no subprocess; fails open. Every pure helper is unit-tested by `tests/test_journal_shard_write_advisory.py`. [ADR-081](adr/081-write-time-journal-shard-validation-hook.md) |
| `skill-file-size-advisory.py` | Write/Edit: `file_path`'s basename is `SKILL.md` (case-insensitive, any project) | Non-blocking nudge once a `SKILL.md` write/edit lands at/above a lower watermark (`skill_file_size_warn_bytes`, default 204800 / 200KB), independently configurable from the guard hook's hard limit (`skill_file_size_limit_bytes`, reused here only to show "N% of the hard limit" in the message; both loaded via the shared `_skill_file_size.py`). The write already happened by `PostToolUse` time, so `_hookout.emit_block()`'s exit 2 here only *surfaces* the message — there is nothing left to block. Simpler than the guard hook: stats the real on-disk file via `os.path.getsize()`, no encoding/newline estimation needed. Kept as a separate `PostToolUse` hook rather than folded into the guard's own decision, since it reports the *actual* post-write size rather than the guard's *pre-write* prediction. Gated to one nudge per session per file via a `_hookutil.sentinel_path()` marker (keyed on `session_id` + a hash of `file_path`) so a multi-edit session fixing an oversized file doesn't get the same nudge on every edit; a payload with no `session_id` skips dedup and always advises. Inclusive boundary — exactly at the watermark advises. Not wired to `Bash`, unlike its neighbor above. Fails open (`OSError` on a missing/racing file). Has a behavioral self-test: `py -3 claude/scripts/tests/test_skill_file_size_advisory.py`. [ADR-127](adr/127-skill-file-size-guard.md) |

---

### Stop

Fires each time Claude finishes responding (the end of every turn), not only at session end; it does not fire on user interrupts (per the [Claude Code hooks docs](https://code.claude.com/docs/en/hooks-guide)).

**All Stop hooks run in parallel — the list order below is not an execution order.** Per the same docs: *"all matching hooks run in parallel … every hook's command runs to completion before Claude Code merges the results. One hook returning `deny` doesn't stop sibling hooks from executing."* So a hook that exits 2 (`stop-tile-enumeration-gate.py`, and `journal-stop-check.py`'s archive-reminder branch — [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)) does **not** short-circuit the rest — `awake-blocker.py`'s sleep-lock release runs at every Stop regardless of a blocking hook, and reordering the list carries no meaning. See [ADR-088 → Stop-hook parallelism](adr/088-state-keyed-tile-enumeration-gate.md).

| Script | What it does |
|--------|-------------|
| `token-tracker.py` | Reads the session JSONL, aggregates token usage, and appends a record to `~/.claude/scratch/token-sessions.jsonl`. Prices through the shared `_token_pricing.py`; a session on an unknown model is recorded with `model_unpriced: true` rather than costed at another model's rates. |
| `journal-stop-check.py` | On the stub-push sentinel flag **matching this Stop event's own `session_id`**, **blocks the stop (exit 2, reminder on stderr)** so Claude actually archives the session: the reminder asks Claude to call the `ccd_session_mgmt__archive_session` MCP tool — a Claude-only action — and a Stop hook's exit-0 stdout is **not** added to Claude's context, so the former stdout emission was invisible to Claude ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)). A `session_id` that is missing or contains anything outside `[A-Za-z0-9_-]` never resolves to any sentinel (dev-env#980, ADR-091 Amendment 2 — the pre-fix global sentinel let ANY concurrent session's Stop consume it, causing false-positive archive instructions and missed reminders). Also runs `_hookutil.cleanup_stale_sentinels` on every Stop, sweeping orphaned per-session flags a crashed/never-Stopped session left behind. Fires at most once per session (sentinel consumed on read) and honors the `stop_hook_active` loop guard. The reminder itself unconditionally states that `archive_session` requires the user's explicit agreement and must never be called speculatively — that invariant doesn't depend on any transcript scan, so it survives a detection miss — and is best-effort augmented (never suppressed, never re-fired) with a count-derived caveat when the session's own transcript still shows pending/`in_progress` tasks tracked via `TaskCreate`/`TaskUpdate` (this harness has no `TodoWrite` tool at all) or a backgrounded `Agent` call with no observed completion notification (a `<task-notification>` block whose `<tool-use-id>` matches the call's own id, searched across every record type confirmed to carry it — `user`, `queue-operation`, `attachment` — not `user` alone). An `Agent` call counts as backgrounded when `run_in_background` is `true` **or omitted** (the documented default for a top-level spawn) — only an explicit `false` excludes it — and `isSidechain` records are excluded from both scans so a subagent's own task/agent activity is never attributed to the main session (dev-env#1002, ADR-091 Amendment 3 — corrected during `/review` from an initial version that scanned for a nonexistent `TodoWrite` tool and required `run_in_background is True` strictly, both empirically confirmed against real transcripts to miss most real in-flight work). Degrades to the unconditional-invariant base reminder (no count-derived sentence) on any transcript-resolution or parse failure; a cheap substring pre-filter (matching the sibling Stop hooks' pattern) skips the full parse when neither `TaskCreate`/`TaskUpdate` nor `Agent` appears in the raw text at all — deliberately the tool name, not the `run_in_background` flag text, since an omitted flag never appears as literal `run_in_background` text. Then — **non-blocking** — checks for stale open journal stubs and unmerged draft branches (user-facing advisories pointing at later dedicated-session work, so they must not block; the draft-branch list comes from the local `refs/remotes/origin/draft/*` refs, so a Stop makes no network call — dev-env#628), emitting any closing message via the `_hookout` **systemMessage** channel (exit 0) rather than plain stdout (a Stop hook's exit-0 stdout is invisible, so the former `print()` surfaced nothing — dev-env#740, [ADR-103](adr/103-shared-hookout-emitter.md)). It deletes no journal files: an untracked stale stub may be a session's only copy of its work, so it is reported like any other stale artifact (dev-env#1221, ADR-091 Amendment 5). Pure/fixture helpers + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_journal_stop_check.py`. [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) |
| `posttooluse-inert-advisory.py` | Reliable-event safety net for the [ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md) inert-PostToolUse limitation. Scans the just-ended transcript; if a dev-env (project #3) `gh issue/pr create` or `gh pr merge` ran but **no** `attachment` record carries `hookEvent == "PostToolUse"` (no `gh` call, no `project` scope; a firing is a prompt to check, not proof that hooks didn't run, per ADR-053's 2026-11-07 amendment), and that action's call either is marked `is_error` (PostToolUse never runs for a failed call) or started after the session's `~/.claude/scratch/bash_state_<session-id>.json` was last written, or there is no such file (dev-env#1331, ADR-055's 2026-11-07 amendment; a merge refused by an API rate limit doesn't count), surfaces the manual fallback pointing to the dev-env `CLAUDE.md` → GitHub Project → Fallback. `gh pr merge` detection also recognizes the two-step REST merge fallback (`gh api -X PUT .../pulls/<N>/merge`, dev-env#986, dev-env#991): `detect_board_actions` gates a REST merge on `is_rest_merge_command(command) AND output_has_rest_merge_marker(output)` finding `"merged":true` (a stronger positive-confirmation signal than the `gh pr merge` branch's absence-of-hard-failure-text check, since `gh api`'s JSON response body is ordinary stdout rather than the stderr-only success line that check works around); `_devenv_merge_pr`'s own REST branch resolves the merged PR's repo/number directly from the REST path (`_repo_target.repo_from_rest_merge_path`/`pr_number_from_rest_merge_path`), falling back to cwd-based dev-env detection for gh's unresolved `{owner}`/`{repo}` URL-templating placeholder — mirroring `post-pr-merge-project.py`'s identical REST resolution (ADR-050 Amendment 23). Detection is dev-env-scoped (created URL / merged PR must be `brownm09/dev-env`) and any PostToolUse attachment all session keeps it silent, so the legitimate different-repo/no-config silent paths ([ADR-049](adr/049-hook-payload-output-field.md)) never trip it. **Blocks the stop once (exit 2, advisory on stderr)** — a Stop hook's exit-0 stdout is invisible, so exit-2 stderr is the only channel that reaches the model (dev-env#740, [ADR-103](adr/103-shared-hookout-emitter.md)); a `stop_hook_active` loop guard + per-session sentinel keep it to one fire, with `mark_resolved` after the emission so a failed delivery retries (dev-env#629). Pure helpers + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_posttooluse_inert_advisory.py`. [ADR-055](adr/055-reliable-event-inert-posttooluse-advisory.md), [ADR-050 Amendment 23](adr/050-shared-hookio-sibling-hook-fixes.md) |
| `stop-tile-enumeration-gate.py` | **State-keyed** tile-enumeration gate — the Stop-hook analog of `pre-merge-findings-gate`, covering **six independent triggers** that share one skip-override/sentinel mechanism (triggers (1)/(2) additionally share the enumeration signal). **(1) Merged PR** (the auto-merge-aware complement to the **command-keyed** `post-merge-tile-checkpoint.py`, ADR-060): scans the just-ended transcript for a PR that reached MERGED state this session by **any** path (a `gh pr merge` success marker; a `gh api .../pulls/N/merge` with `"merged":true`; or a `gh pr view` MERGED state correlated with a PR the session created/enqueued — the auto-merge case the command-keyed hook is blind to). **(2) Dangling created issue** (ADR-092): a `gh issue create` ran this session and the issue was **not resolved** by Stop — resolved means closed via a same-session merged PR's Closes/Fixes/Resolves keyword, or explicitly closed via `gh issue close` — covering the pure-investigation session that files issues but implements nothing, which trigger (1) never catches. When either trigger fires with **no** tile-enumeration artifact recorded — a card or a started session ([how each call counts](#detail-tiles); `spawn_task` in transcripts recorded before the app changed counts as a card), or the prescribed text (`Follow-ups considered … -> tiled (task_id / #N)` / `-> not tiled, because <reason>`); a bare "no follow-ups" does **not** satisfy it (the lifting-logbook#700 skip) — it **blocks the stop (exit 2)** with the reminder(s) on stderr (combined into one message if multiple fire). **(3) Tiles spawned without a table** (ADR-094 addendum, dev-env#656): a tile was spawned this session but no assistant text carries the stable heading `### Tiles spawned this session` (line-anchored, case/heading-level insensitive) that PR1/#663 introduced in `claude/CLAUDE.md` → Session Summaries & Tile Tracking. Trigger (3) is a **stricter, independent** bar than "an enumeration happened": a spawned tile satisfies triggers (1)/(2)'s enumeration check, but does **not** by itself satisfy (3) — a session can resolve (1)/(2) via the spawn while (3) still fires because the table itself was never emitted. The pre-filter that gates the full JSON parse now also OR-matches the bare substrings `start_session` and `spawn_task` (not the exact fully-qualified tool name, matching the real detector's own namespace-agnostic "any namespacing hits" philosophy — a fixed review finding, dev-env#674). What each way a `start_session` call can end counts as is in [Detail: Tiles](#detail-tiles) (dev-env#1298). **(3b) Tile spawned without its shard** ([ADR-118](adr/118-tile-persistence-shards.md), dev-env#870): a tile was spawned this session but nothing evidences a write of `sessions/<project>/tiles/<issue-number>.json`. Trigger (3)'s sibling — same spawn, different loss: the table tells the **user** a tile exists; the shard is what lets the **tile** be re-spawned once the chip dies, which it does on every app restart with no API to re-create it (the paired issue survives either way, so what is actually lost is the one-click restart the tile existed to provide, precisely in the crash case where restarting by hand costs most). Blocking, like (1)-(3), because whether a path was written is objectively verifiable. Evidence is looked for in a `Write`/`Edit` `file_path`, a Bash/PowerShell `command`, **and Bash tool output** — the last is load-bearing, not belt-and-braces: the documented recipe writes shards through a *serializer script*, so a multi-tile session legitimately runs one `py -3 <script>` whose command text names no shard path at all and whose paths appear only in its output (observed live in the session that shipped the reader — three shards, one call). An input-only scan would have blocked that session for a write it performed correctly. Detection is deliberately **session-global, not per-tile**: a tile proposal's input carries no issue number, so a spawn cannot be matched to the shard filename that preserves it, and counting (N spawns ⇒ N shard paths) breaks on that same serializer recipe — so it fires only on the total skip. Over-matching is the chosen failure direction throughout (a stray `tiles/12.json` mention merely means no fire; a missed real write would be a false block). See ADR-118 Amendment 2. **(4) Deferral-question** (ADR-109, dev-env#772): scoped to sessions where (1) or (2)'s context already applies (a merge or issue-create happened), the assistant's own final response matches one of a bounded set of scheduling/permission phrasings ("let me know if you want me to start it now", "should I implement this now", "want me to ... now") instead of tiling the follow-up directly — the motivating incident was the next PR in a multi-PR initiative (ADR-059) asked about instead of tiled, even though other genuine follow-ups WERE correctly tiled in the same turn. Unlike (1)-(3), trigger (4) is a heuristic natural-language match, not an objective fact, so it does **not** block: it deliberately does **not** accept `enumeration_recorded` as resolution (only an explicit "skip tiles" override) — reusing the enumeration signal, as a first draft did, would have let those same unrelated tiles silently resolve it without ever firing for the incident it exists to catch — and it rides `_hookout.emit_advisory("Stop", ..., audience="user")` (a `systemMessage`, exit 0) instead of blocking; it is silently skipped in favor of the harder block whenever a blocking trigger also fires the same turn (only one exit code per invocation). **(5) Unchained merge** ([ADR-140](adr/140-unchained-merge-workstream-gate.md), dev-env#1044): a PR merged this session, the session's **own opening prompt** named no follow-on work, and nothing was queued AT OR AFTER that merge — no tile (`start_session`), `AskUserQuestion`, or `gh issue create` (the session that merges and then ends idle; a tile/`AskUserQuestion`/`gh issue create` from *before* the merge, for unrelated reasons, does not count — dev-env review of PR #1053). "Chain-bearing" is read from the **first genuine (non-synthetic, non-wrapper) user prompt** via the shared `_hookutil.first_user_prompt_text` — which also skips a `<command-name>` slash-command wrapper's own machinery text, not just synthetic records (same review): a `#N` reference, a `github.com/.../issues|pull/N` URL, or the `=== CHAIN` marker means the session was *handed* its next thread (the `retro-chain-refill` / ADR-132 / ADR-137 chained-tile convention — not ADR-094, which mandates only that a tile prompt reference its tracking issue, the case the `#N`/URL forms already detect), so what it chains after that is [ADR-137](adr/137-proactive-tile-forward-chaining.md)'s prose rule to judge, not this hook's. A mid-session mention of an issue number is deliberately **not** consulted — it says nothing about what the session was *asked* to do, and consulting it would let any passing `#N` in a diff or `gh` output silently disarm the trigger; a bare `#N` immediately after a common ordinal/step word ("step #2") is excluded from counting as a reference at all, for the same disarm-the-trigger reason. `session_merged_prs` is reused verbatim (no second copy of the merge detection, per ADR-090's drift rationale) and the pre-filter reuses trigger (1)'s own `"merged"` clause, so the trigger costs no new scan. **Blocking**, unlike (4): all inputs are objectively verifiable (a merge marker, three `tool_use`/command names, one regex over one fixed string), and a Stop hook has no non-blocking *model-visible* channel at all. Resolution is a card or a started session, a real `AskUserQuestion`, or a same-session `gh issue create` (the CLAUDE.md-documented fallback for a session where `start_session` itself is unavailable), each counted only after the merge — or "skip tiles" — enumeration text does **not** resolve it, exactly as in (4), since "Follow-ups considered: none → not tiled" is the very decision it questions. A chain-bearing **or unreadable** opening prompt resolves as *out of scope* rather than firing (unreadable ⇒ never block: the conservative direction for a blocking gate), and marking the sentinel there is safe because the first genuine user prompt is immutable for the session. Note a spawn resolves (5) while independently *arming* (3) and (3b) — the same asymmetry those already have with (1). The ranking order for the survey path lives in `claude/CLAUDE.md` → Capture follow-ups as tiles, not here — reading it needs live `gh` calls this deliberately network-free hook does not make, and restating the list here would just be a second, driftable copy of one ADR-140's own Rationale explicitly keeps in prose. `post-merge-tile-checkpoint.py` carries a one-line pointer to the same path at merge time, but the Stop trigger is the real gate (ADR-053). Honors an explicit "skip tiles" user instruction and the `stop_hook_active` loop guard; each trigger fires at most once per session via its own scratch sentinel — one recorded enumeration satisfies either or both of (1)/(2), the table marker independently satisfies (3), a shard write independently satisfies (3b), only "skip tiles" satisfies (4), and a post-merge spawn / ask / issue-create / "skip tiles" satisfies (5). Pure transcript scan — no `gh`/network/subprocess (fail-open, exit 0 on any error); NOT inert in background/SDK sessions ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)), unlike the command-keyed sibling. Global — no opt-in. Pure helpers unit-tested + a subprocess end-to-end layer: `py -3 claude/scripts/tests/test_stop_tile_enumeration_gate.py`. [ADR-046](adr/046-post-merge-followup-tiles.md), [ADR-088](adr/088-state-keyed-tile-enumeration-gate.md), [ADR-092](adr/092-dangling-issue-tile-enumeration-gate.md), [ADR-094](adr/094-tile-tables-and-issue-per-tile.md), [ADR-109](adr/109-tile-gate-deferral-question-trigger.md), [ADR-118](adr/118-tile-persistence-shards.md), [ADR-137](adr/137-proactive-tile-forward-chaining.md), [ADR-140](adr/140-unchained-merge-workstream-gate.md) |
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

**What it does:** before every `git push` it (1) checks whether the branch's merge base diverges from `origin/main` in squash-merge repos and warns when it detects a branch cut from a squash-merged ancestor (which would cause a rebase to fail), except on engineering-journal `draft/YYYY-MM-DD` pushes, where falling behind `main` is normal; (2) blocks engineering-journal pushes to a past `draft/<D>` branch whose day already merged, from either `draft/<D>` or `compose/<D>`; and (3) when the push range touches a `package.json`, runs a non-destructive **lockfile-drift guard** that regenerates lockfile metadata and blocks the push if `package-lock.json` is out of sync (see [ADR-036](adr/036-lockfile-drift-prevention.md)). It chains to any existing per-repo `.git/hooks/pre-push` so repo-level hooks are preserved.

**Testing:** the lockfile-drift guard has a behavioral self-test that drives the real hook against fixture repos with a stubbed `npm`, asserting its BLOCK / PASS / SKIP paths, working-tree restoration, and repo-hook chaining. It also covers the merged-draft block with a stubbed `gh`. Run it after any change to the hook:

```bash
bash claude/hooks/tests/test-pre-push-lockfile.sh
```

---

### Configuration

`hook-config.json` lives at `.claude/hook-config.json` in the project root. It is gitignored by dev-env's own convention (`.gitignore` ignores all of `.claude/`), but that's a per-project choice, not a universal rule — some onboarded projects track it in git instead (e.g. lifting-logbook, so its Epic-ID table stays reviewable in PRs; see that repo's CLAUDE.md Backup-and-restore procedure). Check the target project's own `.gitignore` before assuming either way.

dev-env's own copy has a tracked source, `claude/hook-config.shared.json`. `_settings_sync.seed_hook_config()` copies it into the canonical's `.claude/hook-config.json` when that file is absent and never overwrites an existing one, so machine-local keys such as `turn_threshold` stick. `dev-env-sync.py` runs it on every prompt and `setup.sh` runs it once. Change dev-env's project IDs in the tracked file. On a machine that already has a live copy, apply the change there by hand, or delete the live copy so the next prompt re-seeds it ([ADR-052 Amendment 2](adr/052-worktree-config-canonical-fallback.md), dev-env#669).

| Field | Type | Default | Used by |
|-------|------|---------|---------|
| `repo` | string | — | `post-tool-use.py` — `"owner/repo"` filter; only acts when the created item URL contains this repo path. `post-pr-merge-project.py` — the repo to query for the merged PR's body (`gh pr view --repo`); also the baseline a merge command's own PR-URL argument is compared against, skipping entirely on a mismatch rather than mutating a different repo's board (dev-env#559) |
| `project_number` | string | — | `post-tool-use.py` — GitHub Project number; required for auto-add on issue/PR create |
| `project_owner` | string | — | `post-tool-use.py` — GitHub user/org that owns the project |
| `project_node_id` | string | — | `post-tool-use.py` — GraphQL node ID of the project; used in `gh project item-edit` commands shown in the reminder |
| `required_fields` | array | `[]` | `post-tool-use.py` — list of project fields to prompt for after issue/PR creation. Each entry: `{"name": string, "field_id": string, "type": "single_select"\|"text"\|"milestone", "options": {name: id}, "hint": string}`. The hook prints ready-to-run `gh project item-edit` commands for each field. For a `single_select` entry, `options` is a fallback only — the hook first tries a live `gh api graphql` fetch of that field's current options and uses the cached `options` map only if the live call fails (labeled in the printed reminder either way, so a stale cache is visible rather than silent — ADR-076, dev-env#527). |
| `epic_field_id` | string | — | `post-tool-use.py` — **deprecated fallback**; use `required_fields` instead. Treated as a single `single_select` field named "Epic" when `required_fields` is absent. |
| `milestones` | array | — | `post-tool-use.py` — **deprecated fallback**; use `required_fields` with `"type": "milestone"` instead. |
| `turn_threshold` | integer | `8` | `turn-count-hook.py` — warn after N user prompts; warns again every 4 prompts thereafter |
| `journal_stub_context_first_tokens` | integer | `150000` | `turn-count-hook.py` — cumulative context growth since the session's first turn at which the first `[journal-stub]` nudge fires (ADR-150). A non-positive or non-integer value falls back to the default. |
| `journal_stub_context_step_tokens` | integer | `100000` | `turn-count-hook.py` — further growth per additional `[journal-stub]` nudge after the first (ADR-150). A non-positive or non-integer value falls back to the default. |
| `idle_refresher_minutes` | integer | `60` | `idle-refresher.py` — inject a return-after-idle refresher cue when the gap since the last turn exceeds N minutes. A value under 60 can fire while the prompt cache is still live, where the cue's re-cache statement overstates the cost. |
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

3. **Invoke via `pyw -3`, never bare `python3`, never `bash -c`, never `py -3` (which flashes a console window per spawn).** Hook commands call the interpreter directly, through the hook launcher: `pyw -3 C:/Users/brown/.claude/hook-launch.py C:/Users/brown/.claude/scripts/foo.py`. The hook script follows the launcher, which fails open when it is missing ([ADR-148](adr/148-missing-hook-script-fails-open-via-launcher.md)); for a dispatched group, `hook-dispatch.py` follows it with every handler after it ([ADR-151](adr/151-in-process-hook-dispatcher.md)). `test_settings_hook_wiring.py` gates both exact forms. `python3` resolves to the Microsoft Store App Execution Alias stub on Windows and exits 49 silently; the `bash -c` wrapper fails because `bash.exe` is not on the Windows system PATH; `py -3` allocates a console window on every spawn. Root causes of [dev-env#81](https://github.com/brownm09/dev-env/issues/81), [dev-env#261](https://github.com/brownm09/dev-env/issues/261), and [dev-env#294](https://github.com/brownm09/dev-env/issues/294). See [ADR-007](adr/007-hook-command-invocation.md).

4. **`import _winsubp` whenever a hook spawns subprocesses.** Under `pythonw.exe` (no console), every `subprocess.run`/`Popen` call that targets a console app (`git`, `gh`, `bash`, `py`, …) gets a fresh console window allocated by Windows unless `creationflags=CREATE_NO_WINDOW` is set. Separately, a text-mode call (`text=True`) with no explicit `encoding=` decodes using the Windows cp1252 default codepage instead of UTF-8, which crashes on any byte `gh`/`git` emits that cp1252 can't represent. The `_winsubp` helper (`claude/scripts/_winsubp.py`) patches both in once on import: `CREATE_NO_WINDOW` unconditionally, and `encoding="utf-8", errors="replace"` for any text-mode call that doesn't already specify its own encoding. Any new subprocess-using hook must add `import _winsubp  # noqa: F401` near its imports; the static check in `claude/scripts/tests/test_hook_static_scans.py` will fail the build otherwise. Root causes: [dev-env#297](https://github.com/brownm09/dev-env/issues/297) (console flash), [dev-env#503](https://github.com/brownm09/dev-env/issues/503) (UTF-8 decoding).

5. **Declared fail direction — advisory hooks fail *open*, blocking gates fail *closed*.** Every hook must know which way it fails when its *own* code crashes (at import time or in `main()`) and enforce it, because Claude Code reads the exit code, not the intent: for a PreToolUse hook, exit 2 blocks the tool and *anything else* (0, or a traceback's 1) lets it through.
   - **Advisory hooks** (rule 2) fail **open** — a top-level `except: sys.exit(0)` on `main()`. A crash must never block the user.
   - **Blocking / fail-closed gates** fail **closed** — a crash must exit **2**, not the default 1 (which Claude Code treats as "allowed"). This means guarding **both** surfaces: a top-level `try/except → sys.exit(2)` around `main()` (re-raising `SystemExit` so the gate's own deliberate exit 0/2 verdicts are preserved), **and** any risky module-level dependency load — a dynamic `importlib` `exec_module`, a `from _sibling import …` — wrapped in `try/except → exit 2`, since an import-time crash fires before `main()` and no `__main__` guard can catch it. A gate that crashed to exit 1 = fail-OPEN silently disabled the very check it exists to enforce (the class dev-env#717/#718 closed). The two current fail-closed gates are `pre-auto-merge-checkpoint-gate.py` (`gh pr merge --auto`, [ADR-083](adr/083-auto-merge-checkpoint-gate.md)) and `pre-tool-use-journal-compose-force-guard.py` (same-day `/journal-compose`, [ADR-096](adr/096-journal-compose-mechanical-force-guard.md)).

   **Caveat — a globally-registered gate must not fail its *imports* closed.** A PreToolUse(Bash) gate runs on *every* Bash call, so if its top-level imports fail-closed one broken sibling blocks *all* Bash. Weigh the load surface: `pre-auto-merge-checkpoint-gate.py`'s dependency load fails **closed** (a broken sibling blocking all Bash is loud and CI-caught; silently ungating `--auto` is the worse outcome), whereas `pre-tool-use-journal-compose-force-guard.py`'s plain sibling imports stay **fail-open** — only a crash *inside* its `main()`, reached solely once a same-day compose target has already matched, fails closed. State the chosen direction (and, for a blocking gate, this import-vs-runtime split) in the hook's module docstring, and ASCII-sanitize any crash-reason text (rule 4's cp1252 concern applies to exit-2 stderr too).

6. **Explicit per-hook `timeout`, and output on the channel the event actually delivers.** Every `settings.json` hook entry declares an explicit `timeout` (seconds) at or above its budget floor — `usage-snapshot.py` → 90, a hook importing `_winsubp` (subprocess work, rule 4) → 30, pure-Python → 10; a dispatched handler's budget is its `hook-dispatch.py` `BUDGET_SECONDS` entry, at least that floor, and the dispatcher entry's timeout is its largest budget plus 10 s for process start (ADR-151) — so Claude Code's default timeout never silently truncates a slow hook mid-run (dev-env#720, gotcha that a too-tight bound re-creates the very invisibility being fixed). And every emission goes through the channel its event + exit code actually surfaces: use `_hookout.emit_advisory` / `emit_block` (ADR-103) rather than a hand-rolled `print` / `sys.stderr.write`, since exit-0 stdout is model-visible only on the context events (`_hookout.STDOUT_MODEL_VISIBLE_EVENTS`), exit-0 stderr is invisible everywhere, and exit 2 ignores stdout. **These four invariants — output contract, ASCII wire-safety, the rule-5 safe-exit `__main__` guard + fail direction, and the wiring/`timeout` requirement — are now mechanically gated** by `test_hook_output_contract.py`, `test_hook_safe_exit_guard.py`, and `test_settings_hook_wiring.py` (dev-env CLAUDE.md `## Testing` items 61–63), each landing green with current offenders in a two-sided allowlist the `_hookout` migrations (PRs 5–7) shrink.

---

## Routines

Autonomous scheduled agents. They run on a cron schedule with no user interaction. Canonical source is authored and reviewed in `claude/routines/<name>/SKILL.md`, which is mirrored read-only at `~/.claude/routines/` via a directory junction — but the `scheduled-tasks` MCP tool never reads through that junction. It owns a separate, real, non-linked directory, `~/.claude/scheduled-tasks/<taskId>/SKILL.md`, holding the *live* prompt for each registered task. Merging a routine to `main` does not update or create a live task; that requires a separate `create_scheduled_task` / `update_scheduled_task` call, and nothing keeps the two copies in sync afterward short of repeating that step. Prefer having the live prompt read its own canonical file at run time and fall back to an embedded copy when unreachable (the pattern `weekly-memory-audit` uses) — see [ADR-003 amendment](adr/003-config-in-version-control.md) and [dev-env#344](https://github.com/brownm09/dev-env/issues/344).

**Model selection (applies to every routine below).** The scheduler runs each task under the app's *global* model default — **not** the model in any project `settings.json`, which it ignores. That default can change out from under a task and **silently drift** (e.g. `claude-opus-4-8` → `claude-sonnet-5`), and a lower-capability model can misread an XML-wrapped autonomous prompt and *greet* ("what would you like to work on?") instead of executing, with **no error and no notification** ([dev-env#698](https://github.com/brownm09/dev-env/issues/698)). Mitigation: pin the model in the routine's frontmatter (`model: claude-opus-4-8`) — but **confirmed inert** (dev-env#703 item 2, 2026-07-14: all four `prune-stale-worktrees` runs 07-11→07-14 came up on `claude-sonnet-5` despite the pin; the scheduler ignores frontmatter `model:`, consistent with the `scheduled-tasks` MCP tools exposing no model parameter and `list_scheduled_tasks` no model field) — so, model-agnostically, place an **execute-now / do-not-greet imperative** at the very top *and* bottom of the *live* prompt (the greeting fires before any Step-0 canonical read-through). That imperative is the **sole effective mitigation** and is confirmed working: all four inert-pin runs executed the routine in full rather than greeting. As of [dev-env#767](https://github.com/brownm09/dev-env/issues/767) (2026-07-14) it is deployed to **every** registered routine's live copy (top *and* bottom), with the verbatim strings captured in each canonical routine's *Restorable live-copy imperative* section for deterministic restore. See the [ADR-003 amendment (2026-07-10)](adr/003-config-in-version-control.md).

---

### daily-journal-compose

**Schedule:** `0 7 * * *` (7:09am local, daily — the scheduler applies a small deterministic jitter on top of the base cron)

Assembles all `YYYY-MM-DD_*.stub.md` files across all configured projects into the canonical 11-section journal entries and opens PRs against `engineering-journal`. When any project's stubs contain meta triggers it also composes `sessions/meta/YYYY-MM-DD-<slug>.md` in the same run and the same PR, without asking (`/journal-compose` Step 6.7); the PR body's `Meta journal:` line is the only place an unattended run reports that outcome. [ADR-082 Addendum, 2026-10-02](adr/082-journal-compose-worktree-isolation.md)

**Retry wrapper:** `journal-compose-with-retry.sh` — wraps the routine for Windows Task Scheduler use. Retries up to 3 times with 5-minute delays on transient failures. Before each attempt except the last, also runs a liveness pre-check (`check-journal-compose-liveness.py`, below) against the shared `engineering-journal` checkout — a dirty working tree for the target date skips that attempt without spending a `claude -p` call; the final attempt proceeds regardless. Logs to `~/.claude/scratch/`. [ADR-086](adr/086-journal-compose-liveness-guard.md)

---

### prune-stale-worktrees

**Schedule:** `0 4 * * *` (4am local, daily)

Scans all primary git repos directly under `C:/Users/brown/Git` and removes worktrees whose branches are fully merged into `origin/main` — both `claude/*` branches and, via `--include-named`, hand-named branches (`feat/`, `fix/`, `docs/`, etc.) held to the identical merged/dirty/liveness bar ([ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md)) — and **parks any non-primary worktree squatting `main`** back onto its own `claude/<slug>` branch (recreated at HEAD via `git checkout -b` — non-destructive, frees the ref even for a dirty worktree the old `git worktree remove` refused; [ADR-058](adr/058-worktree-squatting-main-detection-correction.md)). Also **parks (and, when safe, removes outright) any non-primary worktree squatting an engineering-journal `draft/YYYY-MM-DD` branch** — checked unconditionally across every scanned repo, since a squatter here blocks the canonical (and every concurrent stub-writing session) from reaching that day's draft branch at all, not just this repo's own `main`; idle + clean + fully pushed relative to the branch's own `origin/<branch>` is parked and removed in one pass, idle-but-dirty (or not provably fully pushed) is parked only, leaving the worktree's contents untouched for review ([dev-env#747](https://github.com/brownm09/dev-env/issues/747), [ADR-105](adr/105-draft-branch-worktree-squat-guard.md)). Repos with no GitHub remote are skipped. Uses `git branch -d`, `git worktree remove` (no `--force`), and `git checkout -b` (parking). Skips the current worktree and dirty worktrees (for removal), and — since this routine runs out-of-process and cannot see other sessions via cwd — **any worktree with an active Claude session** (transcript activity within 24h; override with `--liveness-window-min`); the liveness guard runs before the park, so only an *idle* squatter is moved (the draft-branch-squat check does not additionally consult liveness itself — see ADR-105's Judgment calls for why the transcript-mtime signal cannot apply to that specific shape of worktree). A branch not otherwise detected as merged is still treated as merged if a repo opts in via `.claude/hook-config.json`'s `prune_ephemeral_patterns` and every file in the branch's diff vs. `origin/main` matches one of those regexes — off by default, additive only ([ADR-075](adr/075-ephemeral-diff-worktree-pruning.md)). Lists only the skipped worktrees and fetch failures that are new or changed since its last run, and sends a push notification only for those, committing the findings only after notifying; `--full` prints the whole list ([ADR-161](adr/161-maintenance-routines-report-what-changed.md)). [ADR-051](adr/051-worktree-liveness-guard.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-075](adr/075-ephemeral-diff-worktree-pruning.md), [ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md), [ADR-105](adr/105-draft-branch-worktree-squat-guard.md)

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
can't board an issue when PostToolUse doesn't run for its call. A repo with no
`.claude/hook-config.json` is silently skipped; a `gh` `project`-scope failure stops the whole scan
immediately (token-level, would repeat identically per repo), while any other single repo's `gh`
failure is isolated and the scan continues. Runs `sync-routine-worktree` as Step 0. Lists only the
issues that are new or changed since its last run, and push-notifies only for those or for a repo
that started failing, then commits those findings so a failed push re-reports them;
`--full` prints the whole list ([ADR-161](adr/161-maintenance-routines-report-what-changed.md)).
The engine is the on-demand `reconcile-project-board.py` (Utilities below). [ADR-068](adr/068-reconcile-project-board-orphan-issues.md), [ADR-070](adr/070-reconcile-project-board-scan-dir.md)

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
  (# · project · file · type · durable? · instruction home · drift · disposition), a "Promote issues
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
`git merge --ff-only '@{upstream}'` against the canonical checkout (never `git pull`, which merges
the shared `.git/FETCH_HEAD`, dev-env#1166) — safely advances the branch to its own
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
hooks and shared modules that serve the same workflow, rather than split across two sections. Its
rows stop at the scripts that existed on 2026-11-07, so a newer utility appears only here.

| Script | Invocation | What it does |
|--------|-----------|-------------|
| `routine-findings.py save` | `py -3 routine-findings.py save --routine NAME --file PATH` | Run by a routine right after each push notification. The file holds the tool's result text on its first line and the push text after it, so no text passes through shell quoting. Saves the finding to `~/.claude/routine-findings/pending/` unless the result is one of the two "Mobile push requested." forms; an empty or unrecognised result counts as not delivered. Exits 1 if it can't save. The same script's [hook mode](#userpromptsubmit) shows saved findings ([ADR-160](adr/160-routine-findings-delivery-fallback.md)). |
| `token-report.py` | `py -3 token-report.py [--date YYYY-MM-DD] [--days N] [--project name] [--latest] [--show-subagents]`<br>`py -3 token-report.py --attribution [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--project name] [--format json]` | Generates markdown and JSON token usage reports from `~/.claude/scratch/token-sessions.jsonl`. `--attribution` instead reads session transcripts and prices each API call, to show spend by billing bucket, model, context size, idle re-caching, starting context and tool result. It prints aggregates only, at published API prices rather than what a subscription bills (dev-env#1214). |
| `backfill-tokens.py` | `py -3 backfill-tokens.py` | Backfills token data for sessions predating the token-tracker hook. Idempotent — deduplicates on `session_id`. |
| `prune-merged-worktrees.py` | `py -3 prune-merged-worktrees.py [--dry-run] [--repo-path /path/to/repo\|--scan-dir /path/to/dir] [--liveness-window-min N] [--include-named] [--full] [--state-file PATH]` | Manual equivalent of the prune routines. **`--scan-dir` lists only the skipped worktrees and fetch failures new or changed since the last `--scan-dir` run**, ending in a `RESULT:` line with the totals and `new_skipped`, `no_longer_skipped`, `new_fetch_failed`, `new_remote_failed` and `baseline`. A run writes its findings to `<state>.pending`; only `py -3 _last_run.py commit <state-file>`, which the routine runs after notifying, makes them the state, so a hand run never uses up the next routine run's news. **To read the whole list, run it with `--full`**, which neither reads nor writes the state. A missing or unreadable state file (default `~/.claude/scratch/last-run/prune-merged-worktrees.json`, override with `--state-file`) also gives the full report; `--dry-run` compares but doesn't update it; `--repo-path` always prints the full report ([ADR-161](adr/161-maintenance-routines-report-what-changed.md)). Auto-detects the GitHub repo slug from the origin remote URL. `--repo-path` targets a specific repo's worktrees (defaults to dev-env); `--scan-dir` discovers and prunes all git repos directly under the given directory. Removes merged `claude/*` worktrees and parks any worktree squatting `main` off onto its own branch ([ADR-058](adr/058-worktree-squatting-main-detection-correction.md)). Skips any worktree with an active Claude session (transcript activity within `--liveness-window-min`, default 1440 = 24h), or whose own git state was written within that window (ADR-051 Amendment 1). A repo can opt into an additional prunability signal via `.claude/hook-config.json`'s `prune_ephemeral_patterns` — a branch whose entire diff vs. `origin/main` matches those regexes is treated as merged even without a formal merge ([ADR-075](adr/075-ephemeral-diff-worktree-pruning.md)). `--include-named` (off by default) extends the same merged/dirty/liveness checks to non-`claude/*` branches too, instead of skipping them unconditionally via the prefix guard ([ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md)). [ADR-051](adr/051-worktree-liveness-guard.md), [ADR-058](adr/058-worktree-squatting-main-detection-correction.md), [ADR-075](adr/075-ephemeral-diff-worktree-pruning.md), [ADR-078](adr/078-opt-in-named-branch-worktree-pruning.md) |
| `reclaim-worktree-disk.py` | `py -3 reclaim-worktree-disk.py [--dry-run] [--repo-path /path\|--scan-dir /path] [--min-free-gb N] [--protect-cwd /path] [--liveness-window-min N]` | Manual equivalent of the `reclaim-worktree-disk` routine (and the script the `disk-space-check.py` hook spawns). Strips `node_modules`/`.turbo` from idle Claude-managed worktrees (clean **and** merged-or-not-ahead). `--min-free-gb N` makes it a no-op unless the drive is below N GB; `--protect-cwd` shields the active worktree; `--liveness-window-min` (default 360 = 6h) additionally skips worktrees with an active session in *another* worktree the routine can't see via cwd. Deletes only regenerable dirs — never the worktree or git state. [ADR-037](adr/037-worktree-disk-reclamation.md), [ADR-051](adr/051-worktree-liveness-guard.md) |
| `replay-shell-content-guard.py` | `py -3 replay-shell-content-guard.py [--gap] [--json] [--samples N] [--scan-dir DIR] [--scripts-dir DIR]` | Replays `pre-tool-use-shell-content-write-guard.py` ([ADR-138](adr/138-shell-content-write-guard.md)) over recorded session transcripts under `~/.claude/projects/` and reports what it would block: block rate, mechanism mix, hazard reasons, override-token use, truncated samples, and — with `--gap` — the size of ADR-138's remaining accepted gap. **The headline metric is ENRICHMENT**: the blocked set's shell-parse-failure rate divided by the corpus baseline rate. `>1` means the guard concentrates real failures; `~1` means it is firing on ordinary traffic and is over-matching. Measured 11.7x at Amendment 1 over 54,330 unique commands, with a per-mechanism breakdown so a single arm's rate is re-derivable rather than quoted from memory. This is ADR-138 Amendment 1's answer to dev-env#1046 item 1 — a **reader**, not a new log, because session transcripts already record every command *and its outcome*, which is strictly more than a hook-side block log could hold, and because a replay re-runs the *current* code over the *same* corpus and so doubles as a regression instrument. Read-only; commands are truncated in all output (transcripts can carry secrets). Re-run after any detector change to the guard, and treat enrichment trending toward `~1x`, or routine `ALLOW_SHELL_CONTENT_WRITE` use, as the tripwire to re-scope it. |
| `replay-scratch-rm-allow.py` | `py -3 replay-scratch-rm-allow.py [--json] [--width N] [--reasons N] [--scan-dir DIR] [--scripts-dir DIR]` | Replays `pre-tool-use-scratch-rm-allow.py` ([ADR-147](adr/147-scratch-only-rm-auto-allow-hook.md)) over recorded session transcripts under `~/.claude/projects/`: every Bash `tool_use` command, deduplicated, run through the hook's own `check_command` in-process. Reports transcripts read, Bash commands seen, unique commands, the rm+scratch candidate population, how many the hook would approve (each listed with its resolved targets, for a human to spot-check), approvals outside the candidate set (an anomaly counter), and the hook's own rejection reasons for the candidates it would not approve. This is ADR-147 section 6's calibration measurement, committed so its success signal (a non-zero approval count once the "clean up scratch in its own call" guidance has been live, every approval spot-checked) is reproducible; re-run it after any change to the hook's lexer. Complements the hook's live approval log (`~/.claude/scratch/scratch-rm-allow.log`, written by the hook's `main()` only): the log records what was actually approved since deployment, the replay answers the counterfactual over the whole history. Read-only; it never writes the approval log. Commands are truncated (`--width`, default 200) because transcripts can carry secrets. Test: `py -3 claude/scripts/tests/test_replay_scratch_rm_allow.py`. |
| `hook-timing-report.py` | `py -3 hook-timing-report.py [--since-hours N] [--dir PATH]` | Summarizes the per-fire duration lines every wired hook's heartbeat appends to `~/.claude/scratch/hook-heartbeat/<hook>.dur.jsonl` (dev-env#848, [ADR-106 Amendment 2](adr/106-hook-heartbeat-liveness-ledger.md)): one `#`-numbered row per hook with fires, median and 90th-percentile `run_ms` (the hook body, `main()` to exit) and `startup_ms` (py launcher creation to `main()`, covering launcher, interpreter and module imports), slowest first. `startup_ms` is sampled on about one fire in 16 because measuring it needs `ctypes`, which costs 10-75 ms to import on this machine; a column says how many samples back it. Each file keeps its newest 2,000 lines, so a hot hook's data may span only a day or two: the last column says how many hours back each hook's data reaches, and a baseline should be snapshotted before the change it measures ships. Fires killed at their hook timeout write nothing, so the 90th percentile is a lower bound. Default window one week. Read-only. The before/after evidence for hot-path gating (dev-env#849) and the hook dispatcher (dev-env#851), which removes a launch per hook. Test: `py -3 claude/scripts/tests/test_hook_timing_report.py`. |
| `sweep-scratch-debris.py` | `py -3 sweep-scratch-debris.py [--apply] [--max-age-days N]` | One-time/on-demand force-sweep of every known per-session/per-day sentinel and marker family in `~/.claude/scratch/` (`journal_onboard_*.flag`, `session_mode_ack_*.txt`, `disk_space_check_*.flag`, plus the already-self-cleaning families, to clear any backlog predating their own cleanup) older than `--max-age-days` (default 30, matching `_hookutil.MAX_AGE_DAYS`). Dry-run by default; `--apply` actually deletes. Reports per-family file counts and bytes. Deliberately excludes singleton/long-lived files (`awake.lock`, `hook-heartbeat/*.ts`, `token-sessions.jsonl`, `baseline_<repo>_<branch>.json`) — see the script's own module docstring for why each is unsafe to sweep by raw age. Motivated by the 2026-07-10 hook-reliability assessment (dev-env#768): ~5,687 sentinel files / ~285 MB / 771 files >30 days old, `journal_onboard_*` alone accounting for 986. |
| `new-branch.sh` | `new-branch <name>` (shell function; source `~/.claude/scripts/new-branch.sh` in `.bashrc`) | Creates a branch always rooted at `origin/main`. Warns when HEAD has diverged from the merge base. When `baseline_test_failure_tracking: true` is set in `.claude/hook-config.json`, also runs `baseline-tests snapshot` to capture pre-existing failures (ADR-030). |
| `baseline-tests.sh` | `baseline-tests <snapshot\|diff\|gc>` | Captures and diffs pre-existing test failures for the fix-on-touch policy ([ADR-030](adr/030-baseline-test-failure-policy.md)). `snapshot` runs the project test command (`test_command` in `hook-config.json`, default `npx jest --json --silent`) and writes failing-test fingerprints to `C:/Users/brown/.claude/scratch/baseline_<repo>_<branch>.json`, then calls `gc` (best-effort) to sweep this repo's own stale baselines. `diff` re-runs tests and classifies current failures into `new` (block PR), `preexisting-touched` (fix-on-touch or file), and `preexisting-untouched` (note only); exits 1 if any `new` failures are present. `gc` removes `baseline_<repo>_*.json` files for the current repo whose recorded branch (read from the JSON envelope) no longer exists locally or on `origin`; kept whenever the branch is still live in either place, or whenever the remote existence check itself fails (dev-env#778). Jest-only in the first implementation. |
| `merge-stale-pr.sh` | `bash merge-stale-pr.sh <PR-URL>` | Remediates stale `engineering-journal` draft PRs: checks out the branch, warns on missing journal file, deletes orphaned drafts, rebases, and squash-merges with auto-conflict resolution. |
| `journal-compose-meta.py` | `py -3 journal-compose-meta.py stub\|install\|abandon\|check-clean\|check-staged <worktree> <YYYY-MM-DD> [<records.jsonl>\|<staged.md> <slug>]` (`/journal-compose` Step 6.7 and Step 10) | The mechanical half of the meta journal pass. `stub` verifies each trigger record against the stub it cites (`evidence` must appear there verbatim, run to at least three words and come from a session block, not the opening brief; every rejection is named, with its reason, and a malformed JSON Lines line costs only itself) and writes the survivors as compose-generated *derived stubs* (`sessions/meta/YYYY-MM-DD_2359NN.stub.md` plus a manifest shard checked with `_journal_schema`) into the compose worktree. `install` gates the coordinator's composed meta journal — all eleven section headings, and in each category's session every one of that derived stub's `Source:` paths — before copying it in; it replaces a journal it installed earlier in the run (untracked in the worktree) and never one the draft branch already carries (tracked: it prints `META_JOURNAL_EXISTS=<path>` and exits 1; where git cannot say, `META_INSTALL_UNVERIFIED=<why>` and exits 1). Each category's session is the one whose title begins with the category's label. `abandon` removes derived files and only derived files. `check-clean` fails if any stub, manifest shard, `YYYY-MM-DD_draft.md` or temp file for the date remains in a `sessions/<project>/` directory; `check-staged` fails on anything unstaged or untracked in `git status`, because the commit is built from the index. Why a script: the harness refuses the coordinator's Write/Edit into the compose worktree ([dev-env#1119](https://github.com/brownm09/dev-env/issues/1119)), and a Bash write would have the coordinator author stub and manifest content by hand, which is what the verification exists to prevent; a derived stub is generated from records the script has already verified, with no inline literal (the shell-write hook also blocks most such Bash redirects; one quoting form is a known gap, [dev-env#1127](https://github.com/brownm09/dev-env/issues/1127)). Exit 0 ok, 1 usage or precondition error, 2 verification failure. [ADR-082 Addendum, 2026-10-02](adr/082-journal-compose-worktree-isolation.md), [ADR-129 Amendment 2](adr/129-journal-shell-write-guard.md) |
| `journal-project-repo-map.py` | `py -3 journal-project-repo-map.py <engineering-journal-root> [--json <outfile>]` | Resolves each `sessions/<project>/` directory to a GitHub `owner/repo` slug — the input `/journal-compose` Step 8a **Source 3** needs before it can query a repo for `start-here`-labeled issues. Primary source is the **root** `README.md`, pairing each `### ` section's `**Repo:**` bullet with its `**Journal:** [sessions/<project>/` bullet (the Journal bullet is what names the *directory*; section titles deliberately differ, e.g. `### Job Search` → `sessions/job-search/`); falls back to the project's own README accepting `Repo:` **or** `Repository:`, with or without `**` bold markers. Every slug is shape-validated before it reaches the caller's `gh issue list --repo <slug>` command line, so a malformed README produces a reported skip rather than a shell argument. Emits `SOURCE3_RESOLVED=`/`SOURCE3_SKIPPED=` counts, one named `SOURCE3_SKIP <project> -- <reason>` per unresolved project, and a distinct `SOURCE3_MAPPING_EMPTY` (exit 1) when projects exist and none resolve. Exit 0 with skips is information, not failure; exit 2 is a usage error. Replaces an inline regex that matched **zero** of the 11 project READMEs, leaving Source 3 inert — and silently so, since the loop's bare `continue` made a broken mapping indistinguishable from a repo with no labeled issues ([dev-env#1045](https://github.com/brownm09/dev-env/issues/1045), [ADR-032 Amendment 1](adr/032-journal-start-here-dashboard.md)). Run it directly to diagnose why a `start-here` label isn't surfacing. |
| `merge-ready.sh` | `bash merge-ready.sh [owner/repo ...]` | Lists, per repo, the open PRs that are green + mergeable + waiting on nothing (the merge-ready set) vs. those still open but not ready. Defaults to `merickvaughn/lifting-logbook`; accepts multiple `owner/repo` args. Read-only — `gh pr list` plus a `node` rollup of check states (`jq`-free, per the no-`jq` convention). |
| `get-project-item.sh` | `ITEM_ID=$(bash get-project-item.sh <issue-number> [project-number] [owner])` | Resolves a GitHub Project item node ID from an issue/PR number. Checks a local item-ID cache first (dev-env#1057, [ADR-141](adr/141-project-item-id-creation-time-cache.md)) — a hit costs **zero** `gh` calls, so it succeeds even when `gh` is offline/unauthenticated. On a miss it asks GitHub for that one issue or PR's board items, filtered on board number and owner (1 GraphQL point, dev-env#1246), and writes the result back into the cache. Exits 1 with `no item found` when the item isn't on that board, and 2 with gh's own message when the lookup itself fails, for example on a rate limit. Defaults to project 3, owner `brownm09`, repo `dev-env`. Overridable via args or `PROJECT_NUMBER`/`PROJECT_OWNER` env vars (repo via `PROJECT_REPO`). Requires `project` scope for the fallback path: `gh auth refresh -s project`. |
| `session-mode-report.py` | `py -3 session-mode-report.py [--since YYYY-MM-DD] [--interactive-only] [--non-plan-only] [--log PATH]` | Reports the startup permission mode per session by parsing the `session-mode-prompt.py` hook log (`scratch/session-mode-prompt.log`). For each `session_id` it takes the earliest entry as the startup mode, classifies sessions as interactive vs. automated (scheduled-task / `<tag>` prompts), and flags (`!`) interactive sessions that started outside `plan`. Desktop/web and spawn-task sessions launch in `bypassPermissions` by design (overriding `defaultMode: plan`); this surfaces that. Read-only; report to stdout, diagnostics to stderr. |
| `dev-env-doctor.py` | `py -3 dev-env-doctor.py [--offline] [--settings PATH]` | **Read-only health check of this machine's install.** `setup.sh` runs it last; run it any time the tooling misbehaves. Prints one PASS/WARN/FAIL/INFO line per check and exits 1 on any FAIL; never writes anything. Checks: the `~/.claude` links and `~/bin` exist, resolve, and point into the dev-env checkout — a dangling link FAILs (the lists are pinned against `setup.sh`'s link arrays) — which should exist and be `~/Git/dev-env` on `main`; every file each hook command in the live `~/.claude/settings.json` names exists — the hook launcher and the script ([ADR-148](adr/148-missing-hook-script-fails-open-via-launcher.md)), or the launcher, a dispatcher and each handler it runs ([ADR-151](adr/151-in-process-hook-dispatcher.md)), read with the sync's own `py_tokens` extraction and the launcher recognized by name, as `_hook_wiring` recognizes it; a missing launcher is reported as blocking every prompt, a missing script, dispatcher or handler as a hook that isn't running — and the interpreter they start with is on `PATH`, asserting at least one script path was extracted, so an empty or unparseable settings file FAILs instead of passing vacuously ([ADR-144](adr/144-gate-calibration-pass-3-dimension.md)); every `claude/scripts/*.py` compiles on this Python (an invalid escape is a WARN, since a future Python makes it an error); `py`/`git`/`gh`/`node`, gh sign-in (decided locally by `gh auth token`) and its `project` scope, the gh credential helper ([ADR-047](adr/047-standardize-gh-credential-helper.md)) and git identity; the global `core.hooksPath` (read with `--type=path`, and required to exist), plus every `~/Git` clone whose effective hooks dir overrides it ([dev-env#1108](https://github.com/brownm09/dev-env/issues/1108)); the journal clone and whether it contains `origin/draft/<today>`; dev-env's gitignored `.claude/hook-config.json`; and the routine-host flag. Every child runs with prompts off (`GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`) and stdin closed, and a timeout kills its whole process tree, so a credential prompt can't hang it; a check that crashes becomes a FAIL line. `--offline` skips the two network reads, `gh auth status` and `git ls-remote`. See [Adding a Second Machine](#adding-a-second-machine). |
| `register-auto-resume.ps1` | `powershell -ExecutionPolicy Bypass -File register-auto-resume.ps1 [-Include all\|pinned\|'cwd:<glob>'\|'session:<local_id>'] [-IntervalMinutes N] [-Unregister]` | **Per-machine, run once.** Turns on auto-resume for desktop sessions a reboot or app crash cut off mid-turn. It creates `~/.claude/auto-resume.json` (the opt-in rules) only if that file is absent, then registers the non-elevated, hidden `ClaudeAutoResumeLauncher` task. The task runs `auto-resume-launcher.py` through `pyw.exe` every 3 minutes around the clock (a daily trigger repeating for 24 hours), plus once at sign-in plus 1 minute. The launcher opens each interrupted, opted-in session with `claude://code/continue?session=`, and the `session-start-auto-resume.py` hook then continues it. Log: `Documents\LOGS\claude-auto-resume_<date>.jsonl`. `-Unregister` is the undo. It backs up the task definition to `Documents\LOGS\ClaudeAutoResumeLauncherBackup.xml` (write-if-absent, [ADR-079](adr/079-backup-restore-convention.md)), removes the task, and sets `"enabled": false` in the config, which also stops the hook. [ADR-152](adr/152-auto-resume-interrupted-desktop-sessions.md) |
| `register-window-flash-monitor.ps1` | `powershell -ExecutionPolicy Bypass -File register-window-flash-monitor.ps1 [-ScriptPath PATH] [-Unregister]` | **Per-machine, run once.** Registers the non-elevated, hidden `ClaudeWindowFlashMonitor` task, which starts `window-flash-monitor.py` through `pyw.exe` at sign-in, with no time limit, and tries again every 5 minutes so a monitor that has died comes back. Registering again stops the running monitor and starts the named script, so run it after a change to pick up the new code. `-Unregister` backs up the task definition to `Documents\LOGS\ClaudeWindowFlashMonitorBackup.xml` (write-if-absent, [ADR-079](adr/079-backup-restore-convention.md)), then stops and removes the task. The logs stay. [ADR-155](adr/155-record-console-flashes-before-fixing-them.md) |
| `window-flash-monitor.py` | `py -3 window-flash-monitor.py --report [YYYY-MM-DD] [--log-dir PATH]` | Records console flashes and reports them by day: when, how long the window stayed, how long it held focus, the program, its parent chain, and whether that chain reaches `claude.exe`. A flash is a classic console or Windows Terminal window shown and gone within 5 seconds. One with no identifiable source is reported as unattributed, and "From Claude" says `unknown` when a chain doesn't reach a known root. The report also lists other titled windows opened under Claude, any gaps in recording, and that day's monitor errors. Logs go to `~/.claude/scratch/window-flash-monitor/YYYY-MM-DD.jsonl`, kept 14 days and capped at 200 MB in total; errors go to `monitor-errors.log` beside them. No window titles are kept: a console window keeps only the program its title names. [ADR-155](adr/155-record-console-flashes-before-fixing-them.md) |
| `validate-manifest.py` | `py -3 validate-manifest.py <manifest-path> [<manifest-path> ...]` | Pre-compose validator for engineering-journal manifest shards. Checks that each entry has all five required fields (`stub`, `topic`, `tokens`, `prs_opened`, `prs_closed`). Both ADR-056 per-session shards (single JSON object per file) and legacy per-day manifests (one JSON object per line) are handled — paths are parsed line-by-line. Absent/unmatched paths are skipped. Exit 0 — all entries valid; exit 1 — at least one entry is missing a required field or a line failed to parse, with file path, line number, and missing fields on stderr. Wired into `/journal-compose` as **Step 0.7** — runs before any stub read or subagent spawn so field gaps surface up front rather than mid-compose (dev-env [#423](https://github.com/brownm09/dev-env/issues/423)). |
| `validate-composed-output.py` | `py -3 validate-composed-output.py <markdown-path> [<markdown-path> ...]` | Scans composed journal output for raw terminal text that leaked into the prose. Two checks: a short list of git usage/error signatures (`See git-rebase(1)`, `--set-upstream-to`, `Please specify which branch`, `There is no tracking information`, `Everything up-to-date`, `nothing added to commit`, plus `hint: `/`fatal: ` **anchored to line start**), and any indented line inside a `## Progress Summary` section (that prose is never indented). **Fenced code blocks and inline code spans are exempt** — journal entries legitimately quote git errors as documented content, and a journal *about* this bug names the signatures in prose; the span matcher uses a backreferenced tick run so a ``` ``double-tick span containing `single` ticks`` ``` is one span, which a naive regex would split and leak. Indented lines are *not* exempt from the signature check — this corpus fences its code rather than indenting it. Absent/unmatched paths are skipped. Exit 0 — clean; exit 1 — at least one region needs review, printed as `file:line`, which check fired, and the full offending line. **Advisory: it never edits a file** — the motivating paste ate the middle of a sentence and welded a surviving real fragment onto the tail of a `git branch --set-upstream-to` line, so blind deletion loses content. Pure logic lives in `_composed_output_scan.py`. Wired into `/journal-compose` as **Step 8b** — after every write (Steps 6/7/8/8a) and before Step 9 deletes the stubs, so overwritten prose can still be recovered from its source. Does not duplicate Step 6.5's structural assertion (dev-env#467), which checks *headings* on journal *entries* only. Measured against the live corpus: 431 markdown files, zero findings other than the known corruption. [ADR-121](adr/121-composed-output-stray-terminal-scan.md) (dev-env [#894](https://github.com/brownm09/dev-env/issues/894)). |
| `reconcile-late-stubs.py` | `py -3 reconcile-late-stubs.py <draft/YYYY-MM-DD[-suffix]>` | Moves late stubs from a composed day's draft branch to the earliest uncomposed branch, tags the source tip `reconciled/<branch>` so a repeat run moves nothing, then deletes the source branch. See [ADR-119](adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md) Amendment 3. |
| `reconcile-project-board.py` | `py -3 reconcile-project-board.py [--repo-root PATH\|--scan-dir PATH] [--dry-run] [--full] [--state-file PATH]` | Engine behind the `reconcile-project-board` routine (and an on-demand board check). **`--scan-dir` lists only the needs-attention issues new or changed since the last `--scan-dir` run**, and its final `RESULT:` line adds `new_needs_attention`, `no_longer_needs_attention`, `new_repos_failed`, `no_longer_repos_failed` and `baseline` to the totals. A run writes its findings to `<state>.pending`; only `py -3 _last_run.py commit <state-file>`, which the routine runs after notifying, makes them the state, and a scope abort writes nothing, so a hand run or a failed night never uses up the next routine run's news. **To read the whole list, run it with `--full`**, which neither reads nor writes the state. A missing or unreadable state file (default `~/.claude/scratch/last-run/reconcile-project-board.json`, override with `--state-file`) also gives the full list; `--dry-run` compares but doesn't update it; `--repo-root` always prints the full list ([ADR-161](adr/161-maintenance-routines-report-what-changed.md)). Reads `.claude/hook-config.json`, lists open issues + project items, computes orphans (open issues not on the board), adds each via `gh project item-add`, then reports the orphans + any pre-existing **open** board items still missing a required field, printing the exact `gh project item-edit` commands. **Add-only + report-only** — never sets a field value (no guessing) and never mutates single-select options. `--repo-root` targets a specific repo (defaults to the canonical checkout, so it works from a worktree); `--scan-dir` discovers and reconciles every git repo directly under the given directory that has a `.claude/hook-config.json`, skipping repos without one and isolating a single repo's `gh` failure from the rest of the scan (a `project`-scope failure still aborts the whole scan immediately). `--dry-run` reports without adding. Detects a missing `project` scope and prints the `gh auth refresh -s project` hint (exit 1). [ADR-068](adr/068-reconcile-project-board-orphan-issues.md), [ADR-070](adr/070-reconcile-project-board-scan-dir.md) |
| `check-journal-compose-liveness.py` | `git -C C:/Users/brown/Git/engineering-journal status --porcelain \| py -3 check-journal-compose-liveness.py YYYY-MM-DD` | Detects an in-flight session that may still be writing engineering-journal stubs for the date journal-compose is about to merge. Reads `git status --porcelain` output from stdin (stays pure I/O; the caller runs git) and exits 1 if any changed path is a stub/manifest shard (`YYYY-MM-DD_HHMMSS.stub.md` / `.manifest.jsonl`) for the given date, 0 otherwise; exits 2 on a malformed date argument. Called from `journal-compose-with-retry.sh` (primary, before each retry attempt) and `journal-compose/SKILL.md` Step 0.6 (defense-in-depth for manual invocations). [ADR-086](adr/086-journal-compose-liveness-guard.md) |
| `journal-compose-replay.sh` | `bash journal-compose-replay.sh <worktree> <prev-commit> <pathspec> [<pathspec> ...]` | The Step 10.5 conflict-recovery replay for `/journal-compose`, invoked once the compose worktree has been switched onto a fresh `compose/YYYY-MM-DD` branch cut from `origin/main`. Replays everything the draft branch added/modified/deleted since the merge base (which it derives from `<prev-commit>` itself, so the pre-push-rejection route that skips the merge-tree probe still works), and owns the open-PR shard-integrity restore. Every path is first partitioned by whether `origin/main` **also** changed it: uncontested paths replay wholesale from `<prev-commit>`; a contested one is 3-way merged from blobs (never the work-tree file — mixing blob LF with a CRLF checkout makes every line look changed) and, if that conflicts, left holding `origin/main`'s content and reported for manual reconciliation. Prints `REPLAY_SAFE` / `BOTH_CHANGED` / `AUTO_MERGED` / `MANUAL_RECONCILE` / `SHARD_INTEGRITY_RESTORED` / `SHARD_RESTORE_SKIPPED`. Exit 0 — all resolved, caller may commit; exit 2 — manual reconciliation required, **do not commit**; exit 1 — usage/precondition error, or a git failure partway through (the tree is then partially replayed; re-cut the disposable recovery branch rather than committing it). Temp files go to the scratch dir, falling back to a self-removing `mktemp -d` where that path is absent (`JOURNAL_COMPOSE_REPLAY_SCRATCH` overrides the root — test-only). [ADR-104](adr/104-journal-compose-conflict-recovery-diff-and-replay.md) (+ Amendment 1) |
| `run-hook-tests.py` | `py -3 run-hook-tests.py [--list] [--timeout N] [--max-retries N]` | Discovers and runs the whole hook/script test suite — every `test_*.py` plus every bash `*.sh` gate across the two test directories (`claude/scripts/tests/` and `claude/hooks/tests/`) — reporting pass/fail/skip and exiting non-zero iff any failed. A zero-Python-test discovery (broken `REPO_ROOT`/glob) is a loud failure, never a silent green. `--list` prints what would run without running it; `--timeout` overrides the 300 s per-test cap; `--max-retries` (default 2) re-runs a failing test file that many additional times before counting it a final failure — absorbs transient Windows-runner resource contention without masking a real regression, since a deterministic failure still fails on every retry; `0` disables retries and every retry is visibly printed (`RETRY` lines, `[retried Nx]`, a `Retried:` summary line), never silently folded into a plain `PASS` (dev-env#994, [ADR-134](adr/134-run-hook-tests-retry-mechanism.md)). Runner-skips only `test_pyw_stdio.py` (a real-`pyw` Windows-subsystem stdio probe a non-interactive runner can't host — run it locally) and passes each bash gate's own `SKIP:` self-exit (no shellcheck, unauthenticated gh) through as non-failing. The engine behind the `hook-tests` CI workflow (`.github/workflows/hook-tests.yml`, `windows-latest`, `pull_request`). See Script verification suite below. [ADR-103](adr/103-shared-hookout-emitter.md) |
| `retro-chain-status.py` | `py -3 retro-chain-status.py --repo <owner/repo> [--repo <owner/repo> ...] [--journal-repo PATH]` | Read-only, per-repo classifier for the retro-action chained-tile backlog (dev-env#967): reports one of `ALIVE` / `UNRESOLVED` / `NO_QUEUE_FOUND` / `QUEUE_EXHAUSTED` / `ALL_TILED` / `AMBIGUOUS` / `NEEDS_REFILL` per repo. Never mutates anything; a per-repo failure (including a total `gh` transport failure while fetching queue issues) lands in that repo's own `{"status": "ERROR", "error": "..."}` entry rather than aborting the batch or masquerading as a false `NO_QUEUE_FOUND`. Identifies the queue issue **structurally** (the open, `retro-action`-labeled issue whose body contains real `- [ ]`/`* [ ]`/`+ [ ]` checklist lines, not an "Escalations" bullet) rather than by "newest labeled issue," since a repo can carry several open queue issues at once (career-playbook: five). Flags AMBIGUOUS rather than guessing when an untagged shard was spawned on/after the most recent chain-tagged shard's own `spawned` date (or the queue's `createdAt` only when no chain shard exists yet for this queue) — narrower than "since the queue was created" unconditionally, which would flag routine tile activity across a queue issue's entire ~2-week life. Engine behind the `retro-chain-refill` skill. [ADR-131](adr/131-retro-chain-idempotent-refill.md) |
| `deferred-followup-sweep.py` | `py -3 deferred-followup-sweep.py --pr <owner/repo#N> \| --repo <owner/repo> [--closed-since YYYY-MM-DD]` | Report-only (dev-env#1292). Reads merged PRs' deferred acceptance criteria and reports a follow-up closed after the merge as not planned, or by a PR whose body says it doesn't close that issue. Any other closure counts as "not judged", not delivered: it finds 2 of the 4 silent drops in its corpus. Exits 1 on a failed read, and warns when it found no deferral at all. Run by `biweekly-retro` Step 3.5. [ADR-153](adr/153-acceptance-criteria-filing-and-verification.md) |

`prune-merged-worktrees.py`, `reclaim-worktree-disk.py`, and `reconcile-project-board.py` above all discover repos for their `--scan-dir` mode via the shared `find_git_repos()` helper in `claude/scripts/_repo_scan.py` — a non-invoked library module (like `_hookio.py` / `_worktree_liveness.py` / `_journal_shards.py` / `_hookutil.py` / `_hookout.py` / `_repo_target.py`, none of which get their own table row) extracted from three near-identical copies. [ADR-072](adr/072-shared-repo-scan-module.md)

`prune-merged-worktrees.py` and `reclaim-worktree-disk.py` find and fetch each repo's default branch through `fetch_default_branch()` in `claude/scripts/_default_branch.py`, another non-invoked library module: `refs/remotes/origin/HEAD` first, then `git ls-remote --symref origin HEAD` when that is unset, or when fetching the branch it names fails (a stale `origin/HEAD` after a rename). It fetches `refs/heads/<branch>`, so a branch name can't be read as a `git fetch` option. When no branch can be found, the merge checks fall back to `main` and prune protects both `main` and `master` from removal. Wherever those two scripts' entries above say `main` or `origin/main`, read that repo's default branch. A hung remote comes back as a result rather than an exception, so a `--scan-dir` run reports it and carries on ([dev-env#1087](https://github.com/brownm09/dev-env/issues/1087), [dev-env#1088](https://github.com/brownm09/dev-env/issues/1088)).

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
section (the index of when to run each test up to item 118, with full per-item behavioral detail in
[`docs/TESTING.md`](TESTING.md) under the same item numbers, ADR-114; a newer test says so in its own
first lines, ADR-074 Amendment 1). `bash -n` catches only syntax — these catch
runtime and environment bugs it misses, the motivating case being [dev-env#334](https://github.com/brownm09/dev-env/issues/334)
(a path-resolution bug that parsed cleanly yet failed on every run).

The table below is a curated subset (the files with the least self-explanatory behavior). For a
one-line-per-file index of `claude/scripts/tests/` up to item 118 — grouped by shared
module, structural gate, or which hook/script each test covers — see
[`claude/scripts/tests/README.md`](../claude/scripts/tests/README.md)
([dev-env#822](https://github.com/brownm09/dev-env/issues/822)).

**Running the whole suite at once.** `py -3 claude/scripts/run-hook-tests.py` (Utilities table
above) discovers every `test_*.py` and every bash `*.sh` gate across both test directories
(`claude/scripts/tests/` and `claude/hooks/tests/` — the same dir set for Python and bash, so a test
is never silently missed by living in the "wrong" one), runs each as a subprocess, and exits
non-zero iff any failed. Glob discovery means it needs no maintained list, so it also runs the tests
the dev-env `## Testing` section doesn't name. It runner-skips only `test_pyw_stdio.py` (a
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
| `tests/test-setup-link-loop.sh` | `bash claude/scripts/tests/test-setup-link-loop.sh` | Sources `setup.sh` (its dispatch runs only when executed, never when sourced — pinned by scenario 11) against a throwaway `$HOME`, with global git config redirected to a temp file; no real `~/.claude` or global git config is touched. Scenarios 1–3 stub `win_link`/`ln` to a call log and pin the four link arrays and each link function's exact 8-step sequence (file links, dir links, the `routines` junction, the settings seed, `~/bin`). Scenarios 4–15 run the rest for real, with junctions on Windows (no privilege needed): backing up real items, recording links that point outside dev-env, and never overwriting a backup; `--restore` putting items, recorded links, `settings.json` and `core.hooksPath` (unset included) back, converging, and refusing a directory setup didn't write; `win_cmd` actually running its command with a spaced path intact; the symlink probe, the `cmd.exe`-special-character guard and the HOME-vs-profile guard; the dispatch guard; a failed `mklink` naming the backup and `--restore`; and nested links surviving a restore. A case this machine can't run (no 8.3 names, no symlink right) counts as skipped, never passed. `setup_windows()` itself only sequences these functions and was run end-to-end in a sandbox instead. (dev-env [#614](https://github.com/brownm09/dev-env/issues/614), [#1114](https://github.com/brownm09/dev-env/issues/1114)) |
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
| `model` | `sonnet` | Default model for terminal sessions. The desktop app's Code tab ignores it: its model picker remembers its own choice. See [ADR-025](adr/025-default-plan-mode.md) and [ADR-156](adr/156-compact-early-and-default-subagents-to-sonnet.md). |
| `autoCompactWindow` | `400000` | Compact at about 400k instead of about 967K. Override per session with `/autocompact`. See [ADR-156](adr/156-compact-early-and-default-subagents-to-sonnet.md). |
| `env.CLAUDE_CODE_SUBAGENT_MODEL` | `sonnet` | Default model for subagents that nothing else assigns one. A model named by the call or the agent definition wins, so `/review` keeps its Opus pin. Explore and Plan don't move. See [ADR-156](adr/156-compact-early-and-default-subagents-to-sonnet.md). |
| `permissions.defaultMode` | `plan` | **Fresh local CLI sessions** start in plan mode — no edits until the user approves a plan; override per-session with Shift+Tab. **This does not apply to Desktop/web-app or spawn-task / SDK-launched sessions:** the platform starts those in `bypassPermissions` with a startup flag that overrides `defaultMode` *by design*, so they begin off-plan regardless of this setting — `settings.json` has no lever over it, and restarting does not change it. This is expected, not a broken hook; the `session-mode-prompt` hook and `session-mode-report.py` (above) audit it. To start such a session in plan, Shift+Tab at the first prompt. See [ADR-025](adr/025-default-plan-mode.md). |
| `effortLevel` | `xhigh` | The settings key doesn't accept `max`. In the user settings file it doesn't apply to Opus 5.5 or later models, which start at their own default until a level is saved with `/effort`. |
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

## Adding a Second Machine

Claude Code syncs nothing under `~/.claude/` between machines: not settings, not `CLAUDE.md`, not
auto-memory, not session transcripts ([settings](https://code.claude.com/docs/en/settings),
[sessions](https://code.claude.com/docs/en/sessions), [memory](https://code.claude.com/docs/en/memory)).
Cloud sessions load only a repo's own `.claude/`, never user-scope config
([Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web)), so they are not a
substitute for this setup. A *session* therefore can't move between machines; *work* crosses
through git and GitHub — branches, PRs, issues, journal stubs and tile shards. dev-env is how the
tooling itself crosses: each machine gets its own clone and its own `~/.claude/` links. Umbrella
issue: [dev-env#1107](https://github.com/brownm09/dev-env/issues/1107).

### Before you start

- **Check the profile path.** In PowerShell: `echo $env:USERPROFILE`. Every hook command is
  written as `pyw -3 C:/Users/brown/.claude/hook-launch.py C:/Users/brown/.claude/scripts/...`
  ([ADR-148](adr/148-missing-hook-script-fails-open-via-launcher.md)), and a missing launcher
  blocks every prompt. On a profile other than `C:\Users\brown` those files aren't there, so the
  settings sync withholds the hooks, and sessions there run without dev-env's hooks until
  [dev-env#1113](https://github.com/brownm09/dev-env/issues/1113) lands. Setup also refuses to run
  when Git Bash's `HOME` isn't the profile (a Windows-level `HOME` variable), since Claude Code
  reads `~/.claude` from the profile, and it can't link under a path containing one of cmd.exe's
  special characters (`& ^ % ! , ; =`).
- **Enable Developer Mode** (Settings → System → For developers). `setup.sh` needs it — or the
  "Create symbolic links" right, or an elevated Git Bash — to create symlinks. It tries creating
  one first, and stops with that instruction rather than prompting through UAC
  ([ADR-041](adr/041-no-terminal-spawn-in-windows-scripts.md)).
- **Install** Git for Windows, Python with the `py`/`pyw` launcher, the GitHub CLI, nvm for Windows
  (then `nvm install 20.11.1`), and the Claude desktop app, signed in to the same account.

### Steps (Git Bash)

1. Sign in to GitHub, make `gh` git's credential helper
   ([ADR-047](adr/047-standardize-gh-credential-helper.md)), and add the `project` scope the board
   commands need:

   ```bash
   gh auth login
   gh auth setup-git
   gh auth refresh -s project
   ```

2. On a fresh machine, set the git identity: `git config --global user.name "<name>"` and
   `git config --global user.email "<email>"`.
3. Clone into `~/Git` — the hooks assume that layout — starting with dev-env and the journal, then
   the project repos you work in:

   ```bash
   mkdir -p ~/Git
   git clone https://github.com/brownm09/dev-env.git ~/Git/dev-env
   git clone https://github.com/brownm09/engineering-journal.git ~/Git/engineering-journal
   ```

4. Run setup. It links `~/.claude` and `~/bin` into the clone, seeds `~/.claude/settings.json`
   and dev-env's gitignored `.claude/hook-config.json` (the project-board IDs, copied from the
   tracked `claude/hook-config.shared.json` only when absent, dev-env#669), sets the global
   `core.hooksPath`, and runs the doctor, exiting non-zero while the doctor reports a FAIL. Nothing
   is lost: into `~/.claude/backups/setup-<timestamp>/` go any real file or
   directory at a link location (moved, not deleted), where any replaced link pointed if it pointed
   outside dev-env, the `settings.json` the seed changed, and the previous global `core.hooksPath`
   (or the fact that it was unset). `bash setup.sh --restore <that dir>` puts all of it back, never
   overwrites something else it finds in the way, and can be repeated:

   ```bash
   bash ~/Git/dev-env/setup.sh
   ```

5. Re-run the doctor until it reports no FAIL:

   ```bash
   py -3 ~/.claude/scripts/dev-env-doctor.py
   ```

### What stays per machine

| # | Item | On the second machine |
|---|---|---|
| 1 | Scheduled routines | Register only `prune-stale-worktrees` and `reclaim-worktree-disk`, which clean that machine's own worktrees. Keep the shared-state routines — `daily-journal-compose`, `biweekly-retro`, `weekly-memory-audit`, `retro-chain-backstop`, `reconcile-project-board`, `nightly-cover-letters` — on one machine: every lock they take is a local file, so two registrations double-run them ([dev-env#1110](https://github.com/brownm09/dev-env/issues/1110) adds a guard). |
| 2 | `ClaudeKeepTokenWarm` | Retired, so don't register it ([dev-env#1299](https://github.com/brownm09/dev-env/issues/1299)). Its scripts, `keep-token-warm.ps1` and `register-keep-token-warm.ps1`, are gone. A machine still holding the task can remove it with `Unregister-ScheduledTask -TaskName ClaudeKeepTokenWarm -Confirm:$false`. That needs no backup under [ADR-079](adr/079-backup-restore-convention.md): the task holds only what its registration script wrote, and reverting the PR that removed the script restores both. |
| 3 | Auto-memory | Not shared, and the weekly memory audit only sees the machine it runs on. Durable rules belong in `CLAUDE.md` regardless ([ADR-038](adr/038-durable-preferences-documented-in-repo.md)). |
| 4 | `merge-queue.md`, baseline-test snapshots, the project-item cache | Local. A branch started on one machine has no baseline snapshot on the other. |
| 5 | Session transcripts | Local. To steer a session still running on the other machine, use Remote Control from claude.ai/code ([remote-control](https://code.claude.com/docs/en/remote-control)). |
| 6 | Auto-resume (`ClaudeAutoResumeLauncher`, `~/.claude/auto-resume.json`) | Off until you set it up there. The resume hook arrives with the synced settings, but it resumes nothing without that machine's own opt-in file, and without the scheduled task an interrupted session continues only once it's opened. Run `register-auto-resume.ps1` on that machine to turn it on ([ADR-152](adr/152-auto-resume-interrupted-desktop-sessions.md)). |
| 7 | Console-flash recorder (`ClaudeWindowFlashMonitor`) | Off until you run `register-window-flash-monitor.ps1` on that machine. Its logs stay local ([ADR-155](adr/155-record-console-flashes-before-fixing-them.md)). |

### Using both machines on the same day

The journal's `draft/<today>` branch is shared through `origin`. Until
[dev-env#1111](https://github.com/brownm09/dev-env/issues/1111) automates it, before the first stub
of the day on either machine:

```bash
git -C ~/Git/engineering-journal fetch origin --prune
git -C ~/Git/engineering-journal checkout draft/<today>   # no -b when origin already has it
```

If `draft/<today>` already existed locally — this machine's first session cut it from `main` before
the other machine pushed — or a push is rejected as non-fast-forward, merge origin's branch in and
push with its upstream set. Merge the remote-tracking branch by name: a branch cut from `main` has
no upstream, and `git pull` merges `.git/FETCH_HEAD`, which concurrent sessions share (the
[ADR-056](adr/056-per-session-sharding-journal-companion-files.md) addendum of 2026-10-31). The
branches have diverged here, so the merge runs without `--ff-only`.

```bash
git -C ~/Git/engineering-journal fetch origin draft/<today>
git -C ~/Git/engineering-journal merge --no-edit origin/draft/<today>
git -C ~/Git/engineering-journal push -u origin draft/<today>
```

Merge rather than rebase: the checkout is shared by concurrent sessions and may hold their
uncommitted files. **Never force-push a draft branch** — it deletes the other machine's stubs. The
doctor warns when `origin/draft/<today>` exists but the local checkout doesn't contain it.

### Handing a session to the other machine

Push the branch, spawn a hand-off tile ([ADR-113](adr/113-cross-session-handoff-tiles.md)), and
commit its shard (`sessions/<project>/tiles/<N>.json`) as usual. On the other machine, fetch the
journal and merge the branch that holds the shard by its remote-tracking name, as above, then read
the shard and re-propose it, with that machine's clone path at the top of the prompt, because a card
can't carry a folder. To start it there right away, on the user's word, pass the path as `cwd` with
`initiation: "user_asked"` instead —
[dev-env#1112](https://github.com/brownm09/dev-env/issues/1112) adds a helper that resolves `cwd`
and checks whether the tile was already started elsewhere.

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
missing or unparseable, an expired or rejected token, or the usage API unreachable after one retry.
Each of those now prints a `[usage-snapshot] Skipped:` advisory instead of the block
([dev-env#1299](https://github.com/brownm09/dev-env/issues/1299)), and none is a regression of this fix.
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
REST-backed `gh api` calls over GraphQL-backed `gh pr *` subcommands when a GraphQL call fails with
a rate-limit error. A `gh api rate_limit` reading can't confirm or refute that error; see
[`claude/CLAUDE.md`](../claude/CLAUDE.md), *Never mutate git state directly*.

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

A clone's local `core.hooksPath` overrides the global value, and none of the global guards run in
that clone. `setup.sh` never sets a local value, so a fresh machine has none. `dev-env-doctor.py`
warns about each clone that has one and names the command that removes it. Before removing a value,
save it so you can put it back. See [ADR-005](adr/005-global-core-hooks-path.md) Amendment 1 for
where these values came from.

### Post-merge follow-up tiles (chips)

The post-merge checklist ([`claude/CLAUDE.md`](../claude/CLAUDE.md) → Git Workflow) asks you to capture
any out-of-scope follow-ups the work surfaced. The harness mechanism for this is
`mcp__ccd_session__start_session` with `initiation: "own_initiative"` (the app's older name for the
tool was `spawn_task`; transcripts recorded before the change still show it), which renders a
clickable **tile** (a proposal card) in the UI. Nothing runs until the user starts it; one click then
spins the follow-up into its own Claude Code session, seeded with the tile's prompt, and otherwise
the user dismisses it. The current turn continues uninterrupted either way. A fresh proposal takes a
`title`, a `prompt` and a `background` (required, at least 80 characters; the shard's `tldr` is its
start), and the result returns a `task_id`. A card cannot carry a folder, host, environment or preset
(the app refuses it), so it always starts in the proposing session's own project: a tile for another
repo names that repo's local clone path at the top of its `prompt`. The shard's `cwd` still records
the tile's target project.

**When to use it.** At the post-merge follow-up checkpoint of
[ADR-046](adr/046-post-merge-followup-tiles.md) — when a PR reaches merged state (however it merged — a `gh pr merge` you ran, the two-step REST merge, or auto-merge), one tile per genuine,
actionable, out-of-scope item (a fix spotted in adjacent code, deferred work, tech debt, an idea worth
pursuing). The bar is the file-and-link bar ([ADR-028](adr/028-all-findings-merge-gate.md)): real
follow-ups, not speculative musings, so the tile surface stays signal-rich. (`start_session`'s own guidance
names other good moments too — right after verification passes, right before summarizing completed
work; ADR-046 formalizes the merge boundary specifically.)

**Every genuine tile also gets a tracking issue.** A tile is still *ephemeral* — tile cards don't last
(they expire after 7 days, and the older chips died on an app restart), and a tile becomes real work
only when the user starts it. Under
[ADR-046](adr/046-post-merge-followup-tiles.md)'s original default, a GitHub issue was filed only for
a follow-up that "must" be tracked; [ADR-094](adr/094-tile-tables-and-issue-per-tile.md) overrides
that — every genuine tile now gets a tracking issue filed alongside it, in the same repo, referenced
in the tile prompt, giving the ephemeral chip a durable, linkable, status-trackable anchor. The
session also closes with an end-of-session table of the tiles spawned (the third checkpoint below);
the tile, the issue, and the table are complementary, not redundant.

**Fallback where `start_session` is unavailable.** The tool is not present in every session (e.g. some
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
hook is the Stop-time verification that also covers auto-merge and still fires in a session where
every PostToolUse hook is inert ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)).

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
addendum, dev-env#656) when a tile (`start_session`; `spawn_task` in older transcripts) was spawned this session but no assistant message
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
session whose *own opening prompt* named no follow-on work, with neither a `start_session` tile nor an
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

## Instruction Detail

The reasoning, incidents and edge cases behind the rules in [`claude/CLAUDE.md`](../claude/CLAUDE.md). Moved here verbatim from that file by [dev-env#1216](https://github.com/brownm09/dev-env/issues/1216) so they don't load in every session; [ADR-159](adr/159-instruction-files-hold-rules-not-stories.md) has the reasoning. `claude/CLAUDE.md` holds the current rule, so where the two differ, it wins, and where a runbook under [Git Workflow Runbooks](#git-workflow-runbooks) covers the same case, follow the runbook. Position words such as "above" and "below" refer to the old file's order. Headings inside each section are demoted from the original.

### Detail: Memory and issue pairing

- **Search before filing — check for an existing issue first.** Before filing the immortalization issue, run the same search *Git Workflow → Check for an existing issue before filing a new one* requires of any bug-report issue — the immortalization issue is a specific case of that general rule, searched and filed against **dev-env for a global rule, the project's own repo otherwise** (a distinction ordinary bug reports don't need; use that general bullet's `--repo` flag when this differs from the session's cwd). A match means the rule is already tracked — reference that issue from the memory body instead of filing a fresh one, and extend it with a comment if it's still open and missing detail this occurrence adds. Only file a new issue when the search comes back empty. Skipping this step risks a near-duplicate landing right next to the original: [dev-env#610](https://github.com/brownm09/dev-env/issues/610) and [dev-env#627](https://github.com/brownm09/dev-env/issues/627) independently documented the identical `EnterWorktree` cross-repo-targeting gap on the same day, and the overlap was only discovered when the session implementing the second issue found the first one's bullet already in place.

### Detail: Platform and environment

- **Scratch directory:** `C:/Users/brown/.claude/scratch/` — all processing tmp files (`gh` output, JSON parsing intermediaries, etc.) go here regardless of which project is active. Never write tmp files into a project repo working directory. Cleaning up scratch is optional, since leftovers are harmless; if you do, run it as its own standalone `rm -f` Bash call, never chained onto `gh`, `git`, or any other command, because only a command that does nothing but delete scratch files is auto-approved ([ADR-147](adr/147-scratch-only-rm-auto-allow-hook.md)). In that call, name the file by its literal path (`rm -f "C:/Users/brown/.claude/scratch/pr_body_123.md"`) or a non-recursive glob (`rm -f C:/Users/brown/.claude/scratch/tmp_item_*.json`): shell state does not persist between Bash calls, so a variable like `$TMPFILE` set in an earlier call is not defined there, and a `$$`-named file can't be retyped. A file you mean to delete later therefore needs a deterministic name (a date, PR number, or other fixed suffix) rather than `$$`.

  - **A failing command's exit code comes back through `sudo`.** Tested 2026-11-05: `MSYS_NO_PATHCONV=1 sudo cmd /c "exit 7"` returned 7 and `sudo powershell -NoProfile -Command "exit 3"` returned 3. A command that doesn't exist printed `Command not found` and returned 49. So a non-zero code can come from `sudo` itself; read stderr before blaming the command.
  - **Each call goes through a UAC prompt, and answering it is the user's approval.** [Microsoft's `sudo` documentation](https://learn.microsoft.com/en-us/windows/advanced-settings/sudo/) says "a prompt will open asking you to verify that you want to continue", and `consent.exe`, the process behind that prompt, was seen running during a test call. If the user declines, `sudo` prints a cancelled message and exits non-zero ([source](https://github.com/microsoft/sudo/blob/main/sudo/src/main.rs); not run). Report that and don't retry. A scheduled routine shouldn't count on `sudo`, since nobody may be there to answer the prompt.
  - **From the Bash tool, prefix `MSYS_NO_PATHCONV=1` when an argument starts with `/`.** Git Bash otherwise rewrites `/c` to `C:/`, so `sudo cmd /c "exit 7"` opened an interactive elevated prompt and hung until the tool timed out. `</dev/null` doesn't prevent this: the elevated process still reads the console. Killing `sudo` can leave the elevated child running, so after a hang look for it with `Get-CimInstance Win32_Process`. Its `CommandLine` reads as null, as it does for any elevated process, so identify it by name and by a start time during the hung call, then end it with `sudo taskkill /PID <pid> /F`. If that doesn't single it out, leave it and tell the user. See [ADR-041](adr/041-no-terminal-spawn-in-windows-scripts.md) Amendment 1 and [dev-env#1231](https://github.com/brownm09/dev-env/issues/1231).

  - **A session's own reads can't settle it.** Tested 2026-11-06 with throwaway keys: a task never saw a session's write, and a session did see a task's write to a key it hadn't written. But once a session has written a key, it keeps reading its own value after the real one changes, and a session's delete doesn't reach the real key. Microsoft says these writes are kept through app upgrades and deleted only when the app is removed. So after a fix from outside, a session can still show the old value; check from a task.
  - **A Terminal panel tab isn't private.** Tested 2026-11-06 with throwaway keys: a tab's write reached a task and the session, and a tab didn't see a session's write. The cause isn't known and this was one run, so the task stays the route to rely on.
  - **What this doesn't cover.** An `HKLM` policy written through `sudo` from a session, a file deleted under `AppData`, and scheduled tasks and a package removed from a session all took effect for both sides. Registering a task from a session works, which is what makes the route possible. New files in a new `AppData` folder stay private too, as the same Microsoft page says and [win11-init-tools#108](https://github.com/brownm09/win11-init-tools/issues/108) saw.
  - **`sudo` doesn't get out of the private `HKCU` view.** A read-only probe run through `sudo` from a session, confirmed administrator, gave the session's answer on every value that differed, not Task Scheduler's ([measured](https://github.com/brownm09/win11-init-tools/issues/108#issuecomment-6693690165)). So treat an `HKCU` write through `sudo` as private, even though its `HKLM` writes are real. For an elevated script that writes `HKCU`, use the same one-shot task with `-RunLevel Highest`; that form hasn't been run yet.
  - **Detecting it:** walk the parent processes for `\WindowsApps\Claude_*`, which was true from a session and false from a scheduled task. It's also false under `sudo`, because the chain ends at the elevation, so `sudo .\script.ps1` passes this check while its `HKCU` writes stay private. It's true in a Terminal panel tab, whose writes are real, so a script that refuses on this check refuses there too. Package identity doesn't work: `GetCurrentPackageFullName` returned 15700 (no package) inside the app. Measured in [win11-init-tools#107](https://github.com/brownm09/win11-init-tools/issues/107), where a session's changes to known folder paths, a `Run` entry, Explorer sidebar keys and an uninstall key never reached Windows ([dev-env#1287](https://github.com/brownm09/dev-env/issues/1287)). Nothing enforces this mechanically.

- **Claude Code has no user-scope `settings.local.json` — only a per-project one; project-scoped app-written content belongs there.** Verified against the [Claude Code settings documentation](https://code.claude.com/docs/en/settings) and the shipped binary: the four settings files are User (`~/.claude/settings.json`, every project on this machine), Shared project (`.claude/settings.json`), **Project local** (`.claude/settings.local.json`, "you, in this one project only"), and Managed. There is no fifth, user-scope local file — a `~/.claude/settings.local.json` would simply never be read. So when the Claude Code app writes a key that embeds **project-specific** content (an auto-mode environment scan, a per-project credential hint — anything naming a specific repo's paths, visibility, or policies), relocate it to *that project's own* `.claude/settings.local.json` rather than leaving it in the user-scope file: the user file loads for **every** project's session on this machine, so project-scoped state there is both wrong in scope and needlessly broad in what it exposes to unrelated sessions. Project-local is the one real mechanism for personal, project-scoped, git-excluded state (confirm the project's `.gitignore` actually excludes it — `git check-ignore -v .claude/settings.local.json` — since Claude Code only auto-excludes a file *it* created). An ordinary global preference toggle carrying no project-specific content (a boolean like `theme`/`tui`/`*NotifEnabled`) is unaffected and stays in the user file. Incident: a career-playbook `autoMode.environment` scan — a written description of the user's personal career-search data — sat in the user-scope file, loading for every project ([dev-env#1049](https://github.com/brownm09/dev-env/issues/1049), [dev-env#1050](https://github.com/brownm09/dev-env/issues/1050)). See [ADR-139](adr/139-machine-local-settings-with-shared-source-sync.md).

### Detail: Tool discovery

`ToolSearch` finds tools that are *deferred* — announced by name only in a system-reminder, needing a schema fetch before they're callable. It does not index every tool available in a session: many tools are already fully defined in the system prompt's tool list and never appear in `ToolSearch` results, by design — that's not a gap in the search, it's tools that don't need finding.

**Before concluding any tool is "unavailable in this session," check the full tool list already present in the system prompt first** — both directly-defined tools and the deferred-tool names surfaced in system-reminders. A zero-result `ToolSearch` only means the tool isn't in the *deferred* set; it says nothing about whether the tool is already directly callable. Incident: a session searched `ToolSearch("spawn_task")` (the tile tool's older name; it is `mcp__ccd_session__start_session` now), got nothing, wrongly concluded the tile-spawning tool was absent, and silently skipped a tile the tile-checkpoint rule required ([dev-env#754](https://github.com/brownm09/dev-env/issues/754)) — `mcp__ccd_session__spawn_task` was directly available the whole time. See [ADR-107](adr/107-toolsearch-is-not-a-tool-availability-check.md).

### Detail: Authoring file content

Content routed through a shell is parsed by the shell before it ever reaches the file: an apostrophe closes a single-quoted string early, a backtick runs a command, a backslash is eaten as an escape, a markdown code fence collides with a heredoc delimiter. That either fails the write outright — one wasted turn — or, worse, silently writes something subtly different from what you intended.

- **A PR body, issue body, or commit message piped into a command's stdin is content too.** `gh pr create --body-file - <<'EOF'`, `gh issue comment --body-file -`, `git commit -F - <<'EOF'` — same prose, same shell, and *no file left behind to inspect* when it corrupts. Write the body to a file, then pass that path: `--body-file "$TMPFILE"`, `git commit -F "$TMPFILE"`. This does **not** extend to a heredoc feeding an *interpreter's* stdin (`py -3 - <<'PY'`, `python - <<'EOF'`) — that body is a program, not file content, and stays entirely fine. ([ADR-138](adr/138-shell-content-write-guard.md) Amendment 1, on 54,330 measured commands.)

- **"I quoted the heredoc delimiter" is not evidence of safety.** Two of the three [dev-env#1041](https://github.com/brownm09/dev-env/issues/1041) failures used the `<<'EOF'` form — the mitigation the old guidance pointed you to — and failed anyway: one collapsed `'\\'` to `'\'` so node rejected the file it had just "written," the other died on `unexpected EOF while looking for matching '''`. Do not reach for quoting as the fix; reach for Write.

**Precedence over the bypass-permissions instruction.** A bypass-permissions session receives a standing instruction to work through the Bash tool and *"make file changes with `sed`, heredocs, or short scripts, rather than using the dedicated Read, Edit, or Write tools."* That instruction governs **shell work** — running commands, inspecting state, reading files, invoking tooling — where Bash genuinely is the right default and remains so. It does **not** govern **authoring file content**. For content, this section wins, in every permission mode. The two only ever looked contradictory because nobody had said which half of "file changes" each one owns; that is now stated, so there is nothing left to arbitrate. (`session-mode-prompt.py` delivers this same carve-out into bypass sessions directly, since the instruction it reconciles is injected by the harness and is not editable here.)

Mechanically enforced by `pre-tool-use-shell-content-write-guard.py`, which blocks a Bash/PowerShell command that routes an inline literal to a file — or, since ADR-138 Amendment 1, into a `gh`/`git` prose argument (`--body-file -`, `--notes-file -`, `-F -`) — when that literal is multi-line or carries an apostrophe, backtick, or backslash. Program-output redirection and an interpreter's stdin are never matched — structurally, not heuristically. Genuine exception: prefix `ALLOW_SHELL_CONTENT_WRITE=1` (Bash) or precede with `$env:ALLOW_SHELL_CONTENT_WRITE=1` (PowerShell). Replaying the guard over 54,330 real commands measured its block set at **~12× the baseline rate of real shell-parse failures** — it concentrates the harm rather than over-matching; re-check with `py -3 claude/scripts/replay-shell-content-guard.py --gap`. See [ADR-138](adr/138-shell-content-write-guard.md).

### Detail: CLI scripting checklist

5. **An absence claim needs an absolute path, the right ref — and, after a write, a fresh read.** Before concluding a file, pattern, or config is *not present*, re-run the check rooted at the repo root (an absolute path, or `git -C <root>`) and let its stderr through. `session-start-sync.py` now fetches automatically at session start ([ADR-130](adr/130-session-start-fetch-ff-only-or-warn.md)), so `origin/main` itself is more often fresh going into an investigation — but it fires once per session, not before each individual absence claim, so the five mechanisms below (and this whole rule) still apply mid-session. Five independent mechanisms each turn a partial or stale view into output indistinguishable from a genuine miss:
   - **cwd scoping.** `git ls-files`, `git ls-tree`, `git grep`, `find`, and `ls` report only what is under the cwd, and a plain `cd` from an *earlier, separate* Bash call can silently persist into later ones — so a subtree-scoped miss looks exactly like a repo-wide miss. The only tell is often a `../../` prefix in `git status --short`, which most commands never print. This is the inverse of the Git Workflow warning that cwd does *not* reliably persist after a worktree tool fires — assume neither, and scope every command explicitly. Incident: [dev-env#864](https://github.com/brownm09/dev-env/issues/864) — a persisted `cd apps/web` produced a confident "this repo tracks no lockfile anywhere," in a repo with a tracked `package-lock.json` and a CI step verifying it.
   - **Ref scoping.** The same miss happens on the *ref* axis, where an absolute path does not help: a working tree, `HEAD`, and `origin/main` each answer only for themselves. Before claiming a name/number/identifier is free — an ADR number, a migration or fixture filename — check all **three** surfaces, because they are three different points in a branch's lifecycle and none of them sees the others: `origin/main` (**merged**), the open-PR set (`gh pr list`, `gh pr diff <N> --name-only`) (**PR'd**), and the remote branches (`git fetch origin`, then `git for-each-ref --sort=-committerdate refs/remotes/origin` and `git ls-tree -r <ref> --name-only -- <dir>/` on the recent ones) (**pushed, no PR yet**). Filter that third surface by recency, not ancestry — in a squash-merge repo `git merge-base --is-ancestor` calls every *merged* branch unmerged, so it separates nothing. `ls-tree` answers only for identifiers that are *filenames*; for one embedded in a file's **content** (an ADR's row in `docs/adr/INDEX.md`) read the blob — `gh api "repos/{owner}/{repo}/contents/<path>?ref=<ref>" -H "Accept: application/vnd.github.raw"`, never `git show <ref>:<path>`, which mangles per *Suppressed failure* below. The third surface is the one usually skipped and the one that matters most: **pushed-but-not-yet-PR'd is exactly the window two concurrent sessions collide in**, and it is invisible to the other two. Incidents: `ls docs/adr/` and `INDEX.md` both showed 115 as the highest ADR, so 116 looked free — open PR [dev-env#863](https://github.com/brownm09/dev-env/pull/863) had already claimed it; and on 2026-08-28 two sessions each claimed ADR-143, **both having run the checkout-plus-open-PR check correctly**, because each other's branch was pushed without a PR — caught only by the luck of a shared scratch filename ([dev-env#1080](https://github.com/brownm09/dev-env/issues/1080)).
   - **Visibility blind spots.** `Glob` and plain `git status` silently skip gitignored files, and `git ls-tree` only shows committed content (never untracked files, ignored or not) — before declaring a directory empty or fully processed, verify with `ls -la` / `Get-ChildItem -Force` or `git status --ignored` instead.
   - **Suppressed failure.** Never pair an absence check with `2>/dev/null` — the failure you most need to see is the one it hides. MSYS path-conversion mangles `git show <ref>:<path>` into `origin\main;.github\…`; git exits non-zero, the redirect swallows the `fatal:`, and the empty output reads exactly like "the pattern is not present" ([dev-env#602](https://github.com/brownm09/dev-env/issues/602)). This is **deterministic, not intermittent** — the trigger is a leading-dot path segment immediately after the `:`, independent of path depth (`origin/main:.gitignore` mangles; `origin/main:claude/skills/review/SKILL.md` does not); full detail in the `/review` skill's [Remote reads on Windows](../claude/skills/review/SKILL.md) note. Prefix `MSYS_NO_PATHCONV=1`, or read the blob over the API: `gh api "repos/{owner}/{repo}/contents/<path>?ref=<ref>" -H "Accept: application/vnd.github.raw"`.
   - **Read-after-write staleness.** A *filtered list* read is eventually consistent, so a query issued moments after a write can under-report with a clean `200` — a short list indistinguishable from "the write did not land." Never take one as evidence a write failed: re-read after a delay, or confirm against the single-object endpoint (`gh api "repos/{owner}/{repo}/issues/{n}"`), which returned correct state immediately in the observed incident — GitHub publishes no read-your-writes guarantee for it, so treat the delayed re-read as the reliable check and the direct read as the fast one. This belongs to the same false-absent family as the pipe-decode/raw-bytes trap (item 6) and the `$`-anchored-CRLF trap under Git Workflow, but is **alone in it: the obvious diagnosis is wrong *and* self-confirming** — a retry with an unrelated flag bolted on (`--paginate`) returns the full set, so the flag looks causal when the variable that actually changed was elapsed time. A fix that passes its own retry test is the kind that sticks once written down, so confirm the mechanism, not just that the retry went green. Incident: [dev-env#1047](https://github.com/brownm09/dev-env/issues/1047) — six `POST .../issues/{n}/labels` calls each echoed the label back, then `gh api "repos/…/issues?labels=start-here&state=open&per_page=100"` returned five; the omitted issue read correctly from its own endpoint, and minutes later the identical query returned all six.
   - Related but distinct: a partial *pathspec commit you just made* can itself create a working-tree-vs-HEAD divergence (e.g. a rename where only the new path was committed) — see Engineering Journal → Stub file workflow → "An explicit pathspec can also drop half of a rename."

6. **A locale-decoded pipe both misreports corruption and creates it — check raw bytes before believing either.** Piping matched or filtered text through an intermediate interpreter's stdin — e.g. `rg`'s matches relayed into `py -3 -c "..."` — or reading a subprocess with `text=True` and no `encoding=`, introduces a decode step whose codepage need not match the source encoding: on this Windows/Git-Bash setup `locale.getpreferredencoding(False)` is **cp1252**, so correctly UTF-8-encoded bytes are silently reinterpreted — an em dash (U+2014, UTF-8 `E2 80 94`) becomes the three codepoints U+00E2, U+20AC, U+201D, the textbook signature of UTF-8 misread as windows-1252. That decode has **two outcomes, and only the loud one is already handled**: a byte cp1252 leaves *undefined* (`0x81`, `0x8D`, `0x8F`, `0x90`, `0x9D`) raises `UnicodeDecodeError` ([dev-env#503](https://github.com/brownm09/dev-env/issues/503)), while a byte cp1252 *can* map produces wrong characters and no error at all. `_winsubp.py` forces UTF-8 and closes both for dev-env's own committed hooks ([ADR-007](adr/007-hook-command-invocation.md)); an ad-hoc script written inline in a session imports nothing and is protected from neither. Two directions, reaching opposite conclusions:
   - **Read direction — the pipe misreports.** A genuinely clean file relayed through such a decode reads exactly like real corruption. Before concluding the file itself is corrupted, verify against its raw bytes directly — `open(path, 'rb')` in Python, or equivalent — bypassing the pipe entirely; a pipe-relayed "corruption" that a direct byte read doesn't reproduce is a diagnostic-pipeline artifact, not a real defect. Incident: [dev-env#952](https://github.com/brownm09/dev-env/issues/952), surfaced mid-investigation of [career-playbook#1139](https://github.com/brownm09/career-playbook/issues/1139).
   - **Write direction — the pipe creates the corruption and you ship it.** **Never round-trip remote content through a locale-decoded pipe.** A read-modify-write of any GitHub body (issue, PR, comment) whose read is `subprocess.run([...], capture_output=True, text=True)` writes the *mangled* text back to the live resource — both `gh` calls return `200`, and nothing surfaces an error. Read bytes and decode explicitly: `subprocess.run(..., capture_output=True)` **without** `text=True`, then `.decode("utf-8")` — or redirect to a file and read it with `encoding="utf-8"`. **This direction inverts the read direction's conclusion**, so a session carrying only "mojibake through a pipe is probably an artifact" is primed to dismiss corruption it just caused. Settle it **numerically, never visually** — a console-printed sample is re-encoded on its way to the terminal, so it cannot distinguish corrupt *data* from a corrupt *display*; count codepoints instead: `collections.Counter(ord(ch) for ch in body if ord(ch) > 127)`. The transform is deterministic, so recovery is `corrupted.encode("cp1252").decode("utf-8")` — lossless only while every mangled codepoint still round-trips through cp1252; a U+FFFD left by an `errors="replace"` decode has already destroyed the original, and note that `errors="replace"` is exactly what `_winsubp` installs. Incident: [dev-env#1073](https://github.com/brownm09/dev-env/issues/1073) — 33 × U+2014 in [#963](https://github.com/brownm09/dev-env/issues/963)'s body written back as 38 × U+00E2, 35 × U+20AC, 33 × U+201D; see [ADR-143](adr/143-locale-decoded-pipe-writes-corruption.md).

### Detail: Acceptance criteria

- **Every implementation issue has a `## Acceptance criteria` checklist, and every PR is verified against it.** Each `- [ ]` item names how it is checked: a command and its expected result, a test, a file and what it contains, or an observable behaviour. "Works", "is clean", and "docs updated" don't qualify. Investigation and tracking/parent issues instead put `Exempt: <reason>` as the section's first line. `/review` Step 2g scores each criterion `met`, `not met`, or `not verifiable` and reports a missing section as a finding, never as a pass. A `not met` criterion is fixed in the PR, or deferred by amending the issue to link a follow-up issue that names the criterion as its own work. Leaving it unfixed and just filing an issue is not enough. After merge, record the verification on each linked issue (see *After merging* below). Extraction rules, the table format, and the status rules live in one schema file, [`claude/templates/acceptance-evidence.md`](../claude/templates/acceptance-evidence.md), shared with Switchyard's Definition-of-Done gate ([dev-env#1195](https://github.com/brownm09/dev-env/issues/1195), [ADR-153](adr/153-acceptance-criteria-filing-and-verification.md)).

### Detail: Duplicate checks

- **Check for an existing issue before filing a new one.** Before `gh issue create` for a bug report or defect, search both open and closed issues for the same problem: `gh issue list --search "<keywords>" --state all` (add `--repo <owner>/<repo>` when the defect lives in a different repo than the session's cwd — see *CLI Scripting Checklist* for why a cwd/target mismatch here silently searches the wrong repo's issues). A match against an **open** issue means it's already tracked — reference it instead of filing a duplicate, adding a comment with any new detail this occurrence surfaces. A match against a **closed** issue means verify it was actually fixed — `gh issue view <N> --json state,closedAt` plus the linked closing PR's diff — before treating a new report as valid; if the old fix didn't stick or missed a case, reopen it or reference it rather than filing fresh. This is the filing-time sibling of two other checks, not a repeat of either: the memory-immortalization issue in *Durable Preferences & Memory → Search before filing* is a specific case of this same rule; *Check for an existing open PR before implementing* (next bullet) is the same discipline one stage later — after an issue exists, before implementing it. Incident: career-playbook [#1575](https://github.com/brownm09/career-playbook/issues/1575) (filed 2026-09-08) and [#1608](https://github.com/brownm09/career-playbook/issues/1608) (filed 2026-09-14) independently diagnosed the identical `scripts/lib/changed.sh` test-override-seam bug from scratch, six days apart, each while running the pre-PR `validate.sh` gate for an unrelated PR — fixed together in career-playbook PR [#1610](https://github.com/brownm09/career-playbook/pull/1610); the overlap surfaced only by chance, via an unrelated session's journal note. See [ADR-048](adr/048-memory-immortalization-issue-pairing.md) (Amendment, 2026-09-15).

- **Check for an existing open PR before implementing.** Before starting implementation work on any issue — freshly filed or picked up from the backlog — confirm no open PR already addresses it: `gh pr list --search "<issue-number-or-keywords>" --state open`, or `gh issue view <N> --json` and check its linked-PRs/timeline. A match means the work is already in flight — **surface it to the user via `AskUserQuestion`** (closing, superseding, or consolidating onto an existing PR is a visible action, not one to resolve unilaterally) rather than silently duplicating the effort. This is the mirror image of the preceding bullet, not a repeat of it: *Check for an existing issue before filing a new one* confirms no duplicate issue exists before one is filed at all; this bullet confirms no fix is already in flight under an open PR once an issue exists, before implementing it. Distinct too from *CLI Scripting Checklist → Ref scoping*, which confirms all three ref surfaces (`origin/main`, open PRs, and remote branches) before claiming an identifier (an ADR number, a filename) is *free*. None of the three covers starting work on an issue that already has a fix in flight under an open PR — possibly filed against a different, older duplicate issue. Incident: a session fixing dev-env#918 discovered, only while writing the journal stub, that PR [dev-env#770](https://github.com/brownm09/dev-env/pull/770) (open since 2026-07-14) already fixed the identical bug via the older duplicate issue [dev-env#734](https://github.com/brownm09/dev-env/issues/734). See [ADR-125](adr/125-check-open-pr-before-implementing-issue.md).

- **Search and create in separate invocations.** career-playbook [#1590](https://github.com/brownm09/career-playbook/issues/1590) duplicated [#1575](https://github.com/brownm09/career-playbook/issues/1575) on 2026-09-14, from a `gh issue list --search` chained with `gh issue create`: the create ran whatever the search returned. Later retros said this recurred "despite the CLAUDE.md rule", but no such rule existed until [#1302](https://github.com/brownm09/dev-env/pull/1302) added "as its own command". [dev-env#1306](https://github.com/brownm09/dev-env/issues/1306) added the reason.

### Detail: ADR warrant

- **ADR-warrant check** at three checkpoints — (1) after a plan is approved / first edit, (2) after `gh pr create`, (3) before `gh pr merge`. Warranted when the change touches a rule/hook/skill/settings documented in `claude/`, restructures a `claude/` directory, sets a workflow rule other CLAUDE.md files reference, or has rationale `git log` won't recover. Scan `docs/adr/INDEX.md` tags first; open ADR files only on a tag match. Global rules → dev-env [`docs/adr/`](adr/INDEX.md); project decisions → that project's `docs/adr/`. **Write the ADR + update `INDEX.md` before merging a qualifying change.** For clearly ADR-worthy multi-file work, create the template at checkpoint 1 ([ADR-011](adr/011-adr-warrant-check.md)). **Once warranted, prefer the smallest durable home.** The four criteria above are *trigger* conditions — they answer whether the rationale must be written down, not *where* it goes, and defaulting every trigger to a new file is how one day produced five ADRs (2026-07-22). A **new ADR** is for a decision that could later be reversed on its own; an **amendment** is for anything that refines, corrects, extends, or bounds a decision already recorded — if the honest opening sentence is "as ADR-N says, except…", it is an amendment, and a follow-up fix to machinery an ADR introduced is *always* one; a **plain CLAUDE.md line with no ADR** is for a rule whose reasoning is fully contained in the rule as written. Tie-break: **if you cannot name an alternative you rejected, it is not a new ADR** — every ADR carries an *Alternatives Considered* section, so this is checkable in seconds ([ADR-145](adr/145-scope-discipline-self-referential-churn.md) §1).

### Detail: Branch checks

- **Verify branch before editing and before every commit** with `git branch --show-current` — worktrees and `git checkout` shift context silently. If wrong: switch if no edits are on disk; else `git stash`, switch, `git stash pop`. This extends to `gh pr create` and `gh pr merge` too: both silently infer their target branch/PR from the current checkout when not given one explicitly, and a session's tracked cwd/branch can revert without any error surfaced (e.g. after an intermittent Git Bash crash) — prefer passing `--head <branch>` explicitly to `gh pr create` rather than relying on implicit current-branch detection, as a cheap defense-in-depth measure regardless of root cause. `pre-commit-branch-check.py`, `pre-pr-create-check.py`, and `pre-merge-branch-check.py` show the current branch as a visible checkpoint at each of these three commands and flag a loud (non-blocking) warning when it differs from the repo/branch recorded after the session's last Bash call ([ADR-085](adr/085-bash-repo-branch-drift-detection.md); incident: [dev-env#573](https://github.com/brownm09/dev-env/issues/573)). A fourth checkpoint, `pre-bash-drift-check.py`, extends the same comparison to *every* Bash call rather than just those three commands — gated on elapsed time (≥60s since the last recorded call, not command content) so a drift affecting any other command (a `grep`, a test runner, a build) also gets flagged, at the cost of a `git rev-parse` subprocess only after a genuine gap. Motivated by a drift observed immediately after two long-running background `Agent` calls completed — a trigger shape the original three checkpoints don't cover at all ([ADR-101](adr/101-bash-drift-check-every-call.md); incident: [dev-env#682](https://github.com/brownm09/dev-env/issues/682)).

- **Worktree Bash commands must not `cd` to the main repo.** In a worktree session (cwd is `.claude/worktrees/<name>`), do **not** prefix Bash git/npm commands with `cd C:/Users/brown/Git/<repo>`. Edit/Read/Write operate on the worktree by absolute path, but a `cd`-ed Bash command runs git against the **main** repo — a different working dir on a different branch — creating split-brain: edits land in the worktree while commits, pulls, and `git status` act on main. Run git/npm in the worktree with the default cwd (no `cd`); use `git -C <path>` to touch another repo (e.g. engineering-journal). Tell: `git status` clean but Read shows uncommitted edits → two different checkouts ([ADR-066](adr/066-worktree-session-safety-rules.md); incident: [lifting-logbook#502](https://github.com/merickvaughn/lifting-logbook/pull/502)).

- **A bare local `main` ref can be silently stale — read `origin/main`, not `main`, from a worktree or compose flow.** `git fetch` only updates the `origin/main` remote-tracking ref; it does not fast-forward the local `main` branch ref, which only moves when something explicitly merges/resets/pulls it. A worktree session that runs `git show main:<path>` (or any other `main`-targeted read) can silently act on a commit several merges behind actual current state, with no error surfaced. This produced a false "my changes vanished" alarm mid-recovery — a just-merged PR's content appeared missing moments later because the read used the stale local ref, not `origin/main`. Use `git show origin/main:<path>` (or fast-forward local `main` first via `git fetch origin main:main`, safe only when `main` isn't checked out anywhere) whenever reading "current main state" from within a worktree, a `/journal-compose`-style compose flow, or any cwd that isn't itself actively tracking `main`. Incident: [dev-env#648](https://github.com/brownm09/dev-env/issues/648). `session-start-sync.py` now automates exactly this fetch-and-ff-or-warn at session start for any repo a session works in, backing this rule with a mechanical check rather than relying solely on remembering it every time ([ADR-130](adr/130-session-start-fetch-ff-only-or-warn.md), dev-env#966) — it fires once at session start, so a `main` ref can still go stale again mid-session; the manual `origin/main` discipline above still applies.

- **Missing-file investigation: verify `origin/main` before concluding a feature is unbuilt.** When a brief names a file/path absent from the active worktree, do not assume it's unimplemented — the worktree base may be commits behind `origin/main`, making an already-merged fix look unbuilt. At investigation start run `git fetch origin`, then `git ls-tree -r origin/main --name-only | grep <file>` and `git show origin/main:<path>` before building. Cheap insurance against duplicating just-merged work; extends the remote-read principle of [ADR-004](adr/004-pr-review-reads-from-remote.md) from PR review to investigation-start reads ([ADR-066](adr/066-worktree-session-safety-rules.md); incident: [lifting-logbook#485](https://github.com/merickvaughn/lifting-logbook/pull/485)). The session-start `git fetch origin` step above is now also run automatically by `session-start-sync.py` ([ADR-130](adr/130-session-start-fetch-ff-only-or-warn.md), dev-env#966), so a session beginning a missing-file investigation is more often already working from a fresh `origin/main` — the `ls-tree`/`show` verification against it is still a manual step this rule requires.

### Detail: Cross-repo git

- **`EnterWorktree` targets the session's primary repo only — it cannot be pointed at a different repo.** Before calling it, compare the task's target repo against the session's own primary working directory (the `Primary working directory` line in the system prompt). When a session's primary working directory is repo A but the task needs isolated work in repo B, `EnterWorktree` still silently creates the new worktree under repo A; nothing about the call signals the mismatch, and the tool's own `ExitWorktree` counterpart is scoped to user-initiated exits only, so a session can't self-correct by backing out. For repo B, use `git worktree add <path> -b <branch> origin/<default-branch>` directly against repo B's path instead, and interact with it via explicit paths or `git -C <path>` — never `cd` there, per the bullet above ([dev-env#610](https://github.com/brownm09/dev-env/issues/610)).

- **Two more scoping traps compound the fallback above once you're doing manual cross-repo git.** First, once any worktree tool (`EnterWorktree`/`ExitWorktree`) has fired at least once in a session, the Bash tool's cwd does not reliably persist a plain `cd` across separate Bash calls afterward — observed directly as `Shell cwd was reset to <original session cwd>`. Prefix every subsequent Bash command touching a manually-created worktree with its own explicit `cd`/`-C`, even for back-to-back commands, rather than relying on cwd persistence for the rest of the session. Second, `-C <repo>` (and a leading `cd <path> &&`) scopes only the single command it's attached to — it does **not** propagate across `&&`, `;`, or `||` to later commands in the same invocation: `git -C <dev-env-path> fetch origin && git worktree add <path> -b <branch> origin/main` silently runs `worktree add` against the Bash tool's actual cwd, not the `-C` target, whenever that cwd is a different repo. (This single-invocation `cd <path> &&` prefix is not the persistent `cd` the bullet above warns against — it dies with the command it's chained to rather than lingering for later, separate Bash calls, which is exactly why it's a safe substitute for `-C` here.) Give every command in a multi-command invocation that targets a non-primary repo its own explicit `-C`/`cd` — or split into separate Bash calls — rather than assuming one scoping flag covers the whole chain. Either failure mode can create a worktree, branch, or commit in the wrong repo with no error surfaced; sanity-check the result of any `worktree add`/`checkout`/`clone` before trusting it (`git remote -v`, or a commit-hash comparison against the target repo's known `origin/<default-branch>` tip). ([dev-env#627](https://github.com/brownm09/dev-env/issues/627)) A persisted `cd` also has a third failure mode beyond running a command in the wrong repo — it silently scopes `git ls-files`/`git grep`/`find` to a subtree, so a miss reads as repo-wide absence; see *CLI Scripting Checklist* item 5 ([dev-env#864](https://github.com/brownm09/dev-env/issues/864)).

### Detail: Canonical checkout

- **Never mutate git state directly in a repo's canonical (non-worktree) checkout.** Two Claude Code sessions can end up working directly in the same shared canonical checkout at once — nothing about the harness prevents it. When they do, one session's `git checkout`/`commit`/`reset`/etc. silently thrashes HEAD out from under the other: a commit can land on the wrong branch with attribution scrambled, or a session's now-stale branch can revert a concurrent session's already-merged work if opened as a PR without first diffing against `origin/main`. A shared checkout also shares — and can exhaust — the GitHub API rate limit, disabling `gh pr merge`/`gh pr comment`/`gh pr view --json` mid-session. Those `gh pr` verbs are all GraphQL calls — the separate **REST (core)** bucket is often still healthy when GraphQL is exhausted, so fall back to REST-equivalent `gh api` calls rather than waiting out the reset (lifting-logbook's [REST-endpoint fallback when the shared GraphQL rate limit is exhausted](https://github.com/merickvaughn/lifting-logbook/blob/main/CLAUDE.md#rest-endpoint-fallback-when-the-shared-graphql-rate-limit-is-exhausted) has the concrete commands). **Judge a GraphQL rate-limit error from the failing call.** One token has two rate-limit counters with separate reset times, and `gh api rate_limit` shows only one per read, often not the one rejecting calls ([finding on dev-env#1238](https://github.com/brownm09/dev-env/issues/1238#issuecomment-6671406131), measured 2026-11-05; why two exist isn't known). So the error from the failing call decides, and a reading doesn't confirm or refute it. A later pass doesn't mean the budget is back: while the busier counter was at 0, 37 of 53 calls were rejected and the rest passed. Recovery comes at the reset of a counter reading 0, so a reading that shows budget left gives the wrong time; if no read shows 0, the time isn't known. This isn't a secondary limit, whose error [says that it is one](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api). REST stays the fallback throughout. Mechanically enforced: a `PreToolUse(Bash/PowerShell)` hook (`pre-tool-use-canonical-mutate-guard.py`, wired in `claude/settings.shared.json` under both matchers since PowerShell is an equally sanctioned way to run these commands; dev-env#620, ADR-071 Amendment 4) hard-blocks mutating git commands (`checkout`, `switch`, `commit`, `merge`, `rebase`, `reset`, `cherry-pick`, `revert`, `stash pop`/`apply`, `branch -d`/`-D`, bare `pull`) and `gh pr merge` carrying `-d`/`--delete-branch` (same harm model reached through a `gh` invocation instead of a `git` verb — a bare `gh pr merge` stays unblocked, since it merges only remotely) issued with cwd at a canonical root — or redirected at a canonical checkout from elsewhere via a `-C`/`--git-dir`/`--work-tree` flag (so `git -C <other-repo> checkout` from a worktree is caught too; dev-env#576, ADR-071 Amendment 2). Read-only commands, an *ambient* command inside a confirmed-live worktree (an orphaned worktree-shaped directory whose `.git` link is missing or broken doesn't count — the hook confirms liveness rather than trusting the path shape alone; dev-env#749, ADR-071 Amendment 3; the same applies to an already-resolved target root, cwd's or a redirect's — it is exempted only when `git worktree list --porcelain` confirms it a linked, non-canonical entry, not merely because its path string looks worktree-shaped, so an independently-cloned canonical checkout sitting at a worktree-shaped path is no longer wrongly exempted either; dev-env#774, ADR-071 Amendment 6), and a redirect at the engineering-journal checkout stay unaffected — permanently, by design: the Stub file workflow's whole premise is one shared canonical every concurrent session reaches via `-C` (see Engineering Journal → Stub file workflow, and its anti-pattern warning against isolating that workflow into a worktree instead). [dev-env#346](https://github.com/brownm09/dev-env/issues/346) is a narrower, unrelated case (only the `biweekly-retro` routine's own report-writing step) and does not eliminate this exemption — see [dev-env#747](https://github.com/brownm09/dev-env/issues/747) for the actual worktree-locking bug this exemption's flip side (a worktree squatting `draft/YYYY-MM-DD` instead of the canonical mutating it) now guards against. Isolate into a worktree first (`EnterWorktree` or `git worktree add`) for any other repo's canonical, or for engineering-journal work that is *not* the daily stub workflow (e.g. `journal-compose`'s own isolated worktree, ADR-082) — never for the Stub file workflow's own checkout/commit/push steps. Or override with the visible `ALLOW_CANONICAL_MUTATE=1 <command>` prefix after confirming no other session is active in that checkout. See [ADR-071](adr/071-canonical-checkout-mutate-guard-hook.md); incidents: [dev-env#453](https://github.com/brownm09/dev-env/issues/453), [dev-env#558](https://github.com/brownm09/dev-env/issues/558), [dev-env#576](https://github.com/brownm09/dev-env/issues/576), [dev-env#749](https://github.com/brownm09/dev-env/issues/749), [dev-env#620](https://github.com/brownm09/dev-env/issues/620), [dev-env#774](https://github.com/brownm09/dev-env/issues/774).

- **Commit before a change you plan to undo.** On 2026-08-29 a `git checkout main --no-overlay -- .` in the shared engineering-journal checkout destroyed another session's 14 uncommitted lines. `git fsck` found no dangling blob, so they could not be recovered. The same `git checkout --` undo cost recovery work twice more on 2026-08-28. Source: the [2026-09-19 retro report](https://github.com/brownm09/engineering-journal/pull/260), and [dev-env#1306](https://github.com/brownm09/dev-env/issues/1306).

### Detail: PR monitoring and CI

- **Turn on Auto-fix for every PR a session opens.** In the Claude desktop app, right after opening the PR (`gh pr create` or its REST fallback), call `mcp__ccd_pr__get_status` (`bind_pr` if it doesn't report the PR). If it reads Auto-fix on, skip the next call: the "Auto-fix pull requests" setting turns it on for some PRs but not all. If it reads off, call `mcp__ccd_pr__set_monitor` with `auto_fix: true` and the PR's URL. In the default mode that call waits on an approval card until the user answers it, and the `permissions.allow` entry doesn't stop the card ([dev-env#1244](https://github.com/brownm09/dev-env/issues/1244)). If it's refused because the app isn't sure this session opened the PR, don't retry: tell the user they can turn Auto-fix on for that PR themselves ([dev-env#1253](https://github.com/brownm09/dev-env/issues/1253)). Don't offer the call or ask first: this rule is the user's request for that switch. Auto-fix wakes the session on CI failures, merge conflicts and review comments. Auto-merge stays off, and the post-PR checklist and merge gates below don't change. Skip this where the `ccd_pr` tools don't exist (terminal and cloud sessions). A wake caused by the session's own `/review` comment needs nothing beyond the normal findings disposition. Stated by the user on 2026-11-05; a session then found no global default for it that a session can set ([dev-env#1228](https://github.com/brownm09/dev-env/issues/1228)).

- **Wait for CI with one background watch, then merge.** Once a PR's pre-merge steps are done and only CI stands between it and the merge, run `gh pr checks <N> --watch --interval 60` once with `run_in_background: true`, and once more after each later push to the PR. The session is re-invoked when the command exits and spends no tokens while it waits. With Auto-fix on, a failure can wake the session twice, once from the watch and once from the app's event: handle it once. This overrides the desktop app's instruction not to poll CI, because that instruction leaves nothing to wake a session when CI passes: Auto-fix reports failures, conflicts and comments, not success. The override covers this one command only. Don't use `CronCreate`, `ScheduleWakeup`, `/loop`, `Monitor` or a repeated `gh` loop. Keep `--interval 60`: `gh pr checks` spends the GraphQL budget concurrent sessions share, and the default is every 10 seconds. Skip the watch if the user said to hold the merge. When the watch exits, read the result. Exit 0 means every check passed; anything else needs a look, and "no checks reported" exits 1 immediately, which can mean checks haven't registered yet or that the PR is conflicting (see *CI not firing* below). If GraphQL is exhausted the watch fails with a rate-limit error, so read state from `mcp__ccd_pr__get_status` or REST (`gh api "repos/{owner}/{repo}/commits/<sha>/check-runs"`) and start the watch again after the reset on a counter reading 0 (see *Never mutate git state directly* above for why a reading with budget left gives the wrong time), rather than looping on either. Merge gates are unchanged, and auto-merge stays off ([ADR-031](adr/031-auto-merge-disabled.md) Addendum 2026-11-05, [dev-env#1229](https://github.com/brownm09/dev-env/issues/1229)).

- **Sequence merges around a large refactor.** When a branch is a sizable refactor, land it *ahead* of smaller concurrent PRs — or hold the small ones and rebase them once, after it lands — rather than reactively rebasing the large diff every time `main` advances. The big diff is the expensive one to replay, so move it to the front of the queue instead of letting it absorb every intervening merge. This bites most in high-throughput repos (lifting-logbook), where `main` can move several times during one PR's lifetime. Auto-merge being off by design (above) means this is a manual sequencing judgment, not a queue setting.

### Detail: Stub on merge

- **Write a stub on PR merge** — a merge is a session boundary. After `gh pr merge`, the `PostToolUse` hook emits a `### Usage Snapshot (post-merge)` block; include it verbatim in the stub (preserved in journal Section 7). If it prints a `[usage-snapshot] Skipped:` line instead, paste that. Same session as the PR open: update the existing stub, set `prs_closed:[N]` in this session's manifest shard, and delete the PR's `sessions/<project>/open-prs/<N>.json` shard. New session: update the opening stub in place for minor merges, or write a new stub for substantial follow-up. Either way set `prs_closed:[N]` in this session's manifest shard and delete the PR's open-PR shard. **Both updates are now structurally clobber-safe — the manifest shard is this session's own file and the open-PR shard is a per-PR `rm` — so the superseded ADR-054 surgical-edit discipline is no longer required (just delete `open-prs/<N>.json`; if the PR predates the shard transition and still lives in a legacy `open-prs.jsonl`, remove its single line instead). See Stub file workflow → Sharded companion files and [ADR-056](adr/056-per-session-sharding-journal-companion-files.md).**

### Detail: Tiles

- **Capture follow-ups as tiles.** Two checkpoints, one discipline. **(1) A PR reaches merged state** — however it merged: a `gh pr merge` you ran, the two-step REST merge, or auto-merge landing it while you were away ([ADR-046](adr/046-post-merge-followup-tiles.md)). **(2) A session creates a GitHub issue that remains unresolved at session end** — not closed via a same-session merged PR's Closes/Fixes/Resolves keyword, nor explicitly closed via `gh issue close` — the pure-investigation-session case: well-scoped issues filed with nothing implemented get no other mechanical nudge ([ADR-092](adr/092-dangling-issue-tile-enumeration-gate.md); incident: [dev-env#638](https://github.com/brownm09/dev-env/issues/638)). Either is an explicit checkpoint — a forcing-function floor that guarantees an enumeration pass, not a ceiling on when tiling is allowed: the same discipline fires the instant a genuine follow-up surfaces in *any* session, checkpoint or not — mid-investigation, while answering a question, in passing during unrelated work ([dev-env#642](https://github.com/brownm09/dev-env/issues/642)). Before the session's post-checkpoint work is done, **write out the follow-ups you considered** — scan for out-of-scope fixes, deferred work, tech debt, and ideas noticed in passing, and record each as `→ tiled (task_id / #N)` or `→ not tiled, because <reason>`. **"No follow-ups" is valid only as the visible result of that scan — never as a bare assertion.** Create a tile for each genuine follow-up the moment you identify it — checkpoint or not — tiling it yourself, never asking the user whether or when to do it; the tile *is* the low-friction ask, so a chat question in front of it defeats the point. Tiles are low-friction but ephemeral, so **file a tracking issue for each genuine tile in the same repo** and reference that issue in the tile prompt — this gives every tile a durable, linkable, status-trackable anchor (**overrides** ADR-046's tiles-are-capture-not-tracking default; [ADR-094](adr/094-tile-tables-and-issue-per-tile.md)), and the tiles are surfaced together in the end-of-session table (see the **Session Summaries & Tile Tracking** section). **Then write the tile's shard** `sessions/<project>/tiles/<issue-number>.json` — the issue is the durable *anchor*, the shard is the durable *payload*, and without it a crash before the chip is clicked costs the one-click restart even though the issue survives (see Stub file workflow → Sharded companion files, and [ADR-118](adr/118-tile-persistence-shards.md)). Where `start_session` is unavailable (e.g. some terminal sessions), file the follow-up issue anyway (no chip, but the durable anchor still exists). Plan files, session notes, or carry-over context that defer tile creation to a later merge (e.g. "spawn tiles at PR3") do not override this — only an explicit user instruction anywhere in the current session (e.g. "skip tiles") does, and it waives tiling entirely for that session (both checkpoints and any ad hoc follow-up spawning), not one mechanism at a time. **Plan approval is not that instruction:** even when the approved plan explicitly scopes the follow-up work in-session, the tile checkpoint still fires; spawn the tiles and let the user dismiss any whose work is already underway ([dev-env#413](https://github.com/brownm09/dev-env/issues/413)). **Do not convert a known follow-up into a scheduling/permission question back to the user** ("let me know if you want me to start it now", "should I implement this now or leave it for a fresh session") — this applies even to the next not-yet-started unit of a multi-PR initiative (ADR-059): once the current unit merges, that next unit is a genuine follow-up subject to this same discipline, not "the immediate next step of the task in progress" (the task in progress is, by definition, done). A `stop-tile-enumeration-gate.py` trigger now catches a recurrence of this exact shape mechanically — a bounded natural-language match, advisory (not blocking) since it is a heuristic rather than an objective fact ([ADR-109](adr/109-tile-gate-deferral-question-trigger.md); incident: this exact anti-pattern recurred across multiple sessions despite the plain-language rule above already existing). **A required cross-session hand-off is itself a tile, not a chat brief.** When in-scope work cannot be finished in the current session — a blocked or corrupted environment (an orphaned or otherwise unusable worktree, a write-blocked cwd), a required session restart, a session boundary, or any "let's finish this in a fresh session" — spawn a tile whose prompt is the self-contained hand-off, rather than leaving a paste-it-yourself brief in chat and asking the user to restart manually. The tile chip *is* the one-click restart, so it is strictly lower-friction than a manual restart-and-paste — and a blocked or ending session is exactly when it matters most. The *"merely list (don't tile) the immediate next steps of the task in progress"* carve-out (see the **Session Summaries & Tile Tracking** section) does **not** apply once the work must move to a *different* session: at that point it is a genuine cross-session follow-up, not a next step of the task in progress (the current session, by definition, is not continuing it). The user choosing to "restart in a clean session" is a reason to tile the restart, never a reason to skip the tile — and this holds even in plan mode when the user has asked for the hand-off, since spawning the capture chip is an inert proposal, not a mutation of the user's system — the paired tracking issue is itself a normal mutation, filed once the hand-off is actioned ([ADR-113](adr/113-cross-session-handoff-tiles.md); incident: 2026-07-16 career-playbook Clarium assessment handed off as a chat brief from an orphaned worktree instead of tiled). **When a follow-on is blocked on another open issue's future phase or completion, forward-link it *from that blocking issue*, not only back to it.** A tile — or any deferred work item — whose trigger is another open issue reaching a later phase (e.g. *"actionable once #905 merges and #810 reaches Phase 3"*) must, **in the same session**, be given a **forward-reference from that blocking issue**: a "Follow-ons" note, a checklist item, or a linked comment stating the trigger condition. This is **in addition to** the existing back-reference discipline — issue-per-tile ([ADR-094](adr/094-tile-tables-and-issue-per-tile.md)), the tile shard ([ADR-118](adr/118-tile-persistence-shards.md)), and the parent reference carried in the follow-on's own body/prompt — and it **complements, does not duplicate, the shard mechanism**: the shard survives an app restart via a file on disk (extra-issue evidence that can be lost, and that nobody working the parent issue naturally encounters), whereas the forward-reference survives via the **issue graph — the durable, human-navigable discovery surface** — so whoever works the blocking issue and reaches the triggering phase finds the follow-on *from that issue alone*, regardless of whether the chip or shard still exists (GitHub's auto-generated "mentioned this issue" cross-reference — a weak forward link — is easy to miss in a long timeline and states no trigger condition). Extends [ADR-059](adr/059-multi-pr-issue-hierarchy.md)'s "navigable from both directions" from decomposition-time hierarchy to temporally phase-dependent follow-ons discovered later ([ADR-123](adr/123-forward-link-phase-dependent-followons.md); first instance: career-playbook #904 depends on #810 reaching Phase 3 — #810 given a *"Follow-ons discoverable from this issue"* comment 2026-07-27). **Never co-spawn two tiles that have a hard (finish-to-start) dependency on each other — spawn only the head of the chain.** When enumerating follow-ups to tile in the same pass, check whether any two have a genuine finish-to-start dependency: the downstream item's session could not productively start work right now because it needs code, a schema, or an artifact only the upstream item's PR will produce. When that's true, spawn only the first tile in the chain; its tile prompt must explicitly instruct it to spawn the next tile(s) in the chain — with their own paired tracking issue and shard, per the discipline above — once its own PR has merged, rather than leaving the hand-off for a future session to rediscover. This is not a reason to sequence tiles that are merely related or part of the same initiative: most sub-issues of a multi-PR decomposition ([ADR-059](adr/059-multi-pr-issue-hierarchy.md)) are independently workable and should still be spawned together — the test is whether the downstream session could do real work with nothing from the upstream tile's output; spawn together if yes, spawn only the head if no. ([ADR-132](adr/132-sequential-tile-spawning-for-dependency-chains.md); caught proactively before it recurred: cover-letter-runtime [#52](https://github.com/brownm09/cover-letter-runtime/issues/52), a sub-issue of the Phase 2 decomposition, states in its own body "Depends on 1/3" — co-spawning it with [#51](https://github.com/brownm09/cover-letter-runtime/issues/51) would have let #52's session start before the schemas #51 defines existed.) **Every tile spawn is also a look-ahead moment for what comes after it, not only a capture moment for what's already known.** Before finalizing any tile's prompt, ask whether that tile's own completion will predictably surface or unblock a next high-priority thread — even with no hard finish-to-start dependency on it (the next thread doesn't need this tile's code, schema, or artifact to start; it would just be the natural next thing worth doing once this one lands). When the answer is yes and the next thread is in the same subsystem, small, and genuinely bounded in scope, fold it into the same tile as explicitly-sequenced bundled work, the same way a tile can sequence a smaller fix ahead of a larger design pass within itself. Otherwise — a different subsystem, or work that would meaningfully grow the spawned session's own context — do not bundle it in; instead add an explicit instruction to the tile's own prompt: once its work merges, re-verify the next thread is still open and unaddressed (state may have drifted) and, if so, spawn a new, separate follow-up tile for it — with its own paired tracking issue and shard, per the discipline above — rather than continuing in the same session. Name the specific next issue(s) in the prompt now, while the priority reasoning is fresh, rather than leaving "what's next" for the chained session to rediscover from scratch. This is a proactive step for every tile spawn, independent of whether multiple follow-ups happen to be enumerated together — that narrower same-pass, hard-dependency case is [ADR-132](adr/132-sequential-tile-spawning-for-dependency-chains.md)'s. ([ADR-137](adr/137-proactive-tile-forward-chaining.md), extending ADR-132's "spawn only the head of the chain" mechanism to the no-hard-dependency case; motivating incident: cover-letter-runtime, 2026-08-18 — a tile spawned for [#93](https://github.com/brownm09/cover-letter-runtime/issues/93)+[#92](https://github.com/brownm09/cover-letter-runtime/issues/92) left out an already-ranked, already-known second-priority thread ([#76](https://github.com/brownm09/cover-letter-runtime/issues/76)/[#77](https://github.com/brownm09/cover-letter-runtime/issues/77)) the same session had surfaced minutes earlier via `AskUserQuestion`; only a direct user follow-up asking whether a high-priority thread should be integrated into the tile caught the gap.) **A merge in a session that was not itself handed a next thread must not end idle — chain one, or put the choice to the user.** ADR-137's look-ahead governs what you carry *into* a tile you are already spawning; this is the floor at the other end, for a session that would otherwise spawn nothing at all. When a PR reaches merged state and this session's **own opening prompt** named no follow-on work — no issue/PR reference, no `=== CHAIN` marker (the `retro-chain-refill` / [ADR-132](adr/132-sequential-tile-spawning-for-dependency-chains.md) / [ADR-137](adr/137-proactive-tile-forward-chaining.md) chained-tile convention — not ADR-094, which mandates only that a tile prompt reference its tracking issue, the case the `#N`/URL forms above already detect — so this is specifically the un-chained, started-from-scratch session) — do one of exactly two things before the turn ends. **First, chain the known thread:** if this work predictably surfaces or unblocks a next one, tile it now — tracking issue, `start_session` proposal, shard — per the discipline above. **Only when none is determinable, survey and ask:** rank the repo's open issues — (1) `start-here`-labeled issues, (2) the first unchecked item of the newest open `retro-action` checklist issue, (3) open issues in the merged PR's own subsystem, (4) most-recently-updated — then dedupe the top candidates against the `sessions/<project>/tiles/` shards and `list_sessions` (a tile whose work has already started keeps its issue open, so it still reads as pending), offer the top 3 via `AskUserQuestion`, and tile the choice. **That ask is the ONE bounded carve-out to the "never ask the user whether or when to tile" rule above** — it is legitimate *only* when no follow-up is determinable, and never as a substitute for chaining one you already know about (converting a *known* follow-up into a question remains exactly the anti-pattern ADR-109's trigger flags). Mechanically enforced at the Stop boundary by `stop-tile-enumeration-gate.py`'s sixth trigger, which blocks on precisely this shape — merged **and** unchained opening prompt **and** no tile proposal **and** no `AskUserQuestion` **and** no same-session `gh issue create` (the fallback named above for a session where `start_session` itself is unavailable) — with `post-merge-tile-checkpoint.py` carrying the same pointer at merge time, when context is freshest; the Stop trigger is the real gate, since PostToolUse hooks are inert in background sessions ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md)). ([ADR-140](adr/140-unchained-merge-workstream-gate.md); ADR-137's own *Alternatives considered* deferred a hook as premature — "a hook can follow later" — and this is that hook, scoped deliberately to the mechanically-checkable floor rather than to the semantic look-ahead judgment ADR-137 rightly left to prose.)

- **What the Stop gate counts as a tile.** `stop-tile-enumeration-gate.py` reads how a `start_session` call ended, not just that it was made, because a refusal can come back with no error flag ("Nothing was proposed") and an offer starts nothing by itself. It also follows a card after it's proposed, by the `task_id` in the proposal's result ([dev-env#1298](https://github.com/brownm09/dev-env/issues/1298), [ADR-088](adr/088-state-keyed-tile-enumeration-gate.md) Amendment). Only a proposal card is a tile. A started session satisfies the merge and issue floors but owes no table or shard, since those guard a card that can be lost. `hand_off_to_session` counts as nothing: it moves the task in hand and queues nothing new. `spawn_task`, the older name, still counts as a card for transcripts recorded before the app changed.

  | # | How the call ended | Satisfies the merge and issue floors | Owes a table and a shard |
  |---|---|---|---|
  | 1 | `own_initiative`, proposed | Yes | Yes |
  | 2 | `own_initiative`, refused or "Nothing was proposed" | No | No |
  | 3 | `user_asked`, started | Yes | No |
  | 4 | `user_asked`, refused | No | No |
  | 5 | `offer`, user chose "New session", before the `proposal_id` call its result asks for | No | No |
  | 6 | `offer`, user chose "Do it here" or didn't answer | No | No |
  | 7 | `proposal_id`, started; if it was a card, the card is gone | Yes | No |
  | 8 | A card withdrawn with `dismiss_task`, or found expired | No | No |
  | 9 | A card whose `dismiss_task` failed, or said "Nothing was changed" | Yes | Yes |

### Detail: Auto-merge

- **Auto-merge is off by design; `--auto` is now mechanically gated when available.** Every merge is in-session via `gh pr merge --squash --delete-branch` after the post-PR checklist (`/review`, ADR-warrant, doc-reconciliation, board transition, roadmap move). `gh pr merge --auto` is additionally gated by `pre-auto-merge-checkpoint-gate.py`: it blocks (fails closed, no override) unless the target PR's single most recent qualifying comment carries both a clean-or-disposed `review-findings` marker and a complete `premerge-checkpoints` marker (`adr_warrant`, `doc_reconciliation` both valid), fresh relative to the PR's current head commit — see `/review` Step 2f/Step 8. This restores `--auto`'s "impossible to skip" property without a human in the loop. **It does not, by itself, unlock `--auto` anywhere by default:** `allow_auto_merge` is a separate, per-repo GitHub setting (the item-7 decision tracked in ADR-083's Follow-up section) — it is **not** guaranteed to be `false` everywhere and drifts silently, since a web-UI toggle leaves no git history. **Verify live before relying on either assumption:** `gh api repos/brownm09/<repo> --jq .allow_auto_merge`. As of this writing at least one `brownm09/*` repo already differs from the `false` default — see ADR-083's dated addenda for which repo, why, and current status, rather than trusting a value copied here; a second copy of the same fact would just drift out of sync with the addenda the same way the original blanket claim drifted from live GitHub state. Where the toggle is `false`, `gh pr merge --auto` fails at the GitHub API regardless of the hook. Wait for CI, then run plain `gh pr merge --squash --delete-branch` as the standard path ([ADR-031](adr/031-auto-merge-disabled.md), [ADR-083](adr/083-auto-merge-checkpoint-gate.md)).

### Detail: CI not firing

#### CI not firing — merge conflict silences GitHub Actions

**Pattern:** A PR in `CONFLICTING` (merge conflict) state causes GitHub Actions `pull_request` events to never fire. GitHub cannot create the virtual merge commit at `refs/pull/N/merge`, so the event is silently dropped — no checks are queued, no runs appear.

**Symptom:** `gh pr checks` returns "no checks reported" and `gh run list --branch <branch-name>` shows only stale runs from before the conflict.

**Diagnosis:**
```bash
gh pr view <N> --json mergeable,mergeStateStatus
# mergeable: "CONFLICTING" — direct conflict indicator; mergeStateStatus will also be "DIRTY"
```

**Fix:** Rebase (or squash-rebase) the branch onto `origin/main` and force-push. Once the conflict is resolved, GitHub recreates the merge ref and CI fires normally on the next push.

Motivating incident: [lifting-logbook PR #604](https://github.com/merickvaughn/lifting-logbook/pull/604).

### Detail: Base branch worktree

#### Worktree holding the base branch blocks `gh pr merge --delete-branch`'s local step

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

Confirmed as a **general git-worktree mechanic**, not a quirk of any one repo's worktree count: [dev-env#575](https://github.com/brownm09/dev-env/pull/575) (canonical correctly on `main`) and [lifting-logbook#664](https://github.com/merickvaughn/lifting-logbook/pull/664) (an idle worktree left squatting `main` by an earlier merge; that repo's CLAUDE.md "Standard Issue Workflow" step 8 carries its own copy of this same two-step pattern). Full detection, root cause, and non-destructive auto-correction (parking idle squatters instead of removing them): dev-env [ADR-058](adr/058-worktree-squatting-main-detection-correction.md); complete runbook: dev-env [`docs/REFERENCE.md` → Git Workflow Runbooks](#git-workflow-runbooks).

### Detail: Bare force

#### Bare `--force` after rebase auto-closes any open PR on the target branch

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

Motivating incident: [win11-init-tools PR #46](https://github.com/brownm09/win11-init-tools/pull/46) — PR #34 was auto-closed and replaced. Full runbook: [`docs/REFERENCE.md` → Git Workflow Runbooks](#git-workflow-runbooks).

### Detail: Stacked squash

#### Squash-merging a stacked PR's base leaves the child `CONFLICTING` — a plain rebase fixes it

**Pattern:** When a child PR's base is another (still-open) PR's branch and that parent gets squash-merged, the child's diff goes `CONFLICTING`/`DIRTY` the moment the parent's branch is deleted (whether via an explicit `gh api -X DELETE .../git/refs/heads/<branch>` call or a repo's own auto-delete-on-merge setting) — `main` now carries the parent's **squashed** content, while the child branch still carries the parent's **original, unsquashed** commits underneath its own, so a 3-way merge sees the parent's changes on both sides at once. GitHub auto-retargets the child's `baseRefName` to the repo's default branch when its old base branch disappears, but retargeting alone doesn't fix the diff.

**This is the recoverable case, not the orphan case** [`docs/REFERENCE.md` → Stacked PR squash-merge sequencing](#git-workflow-runbooks) already documents: if the parent was merged *without* `--delete-branch` (that runbook's Prevention step 1), the child PR is never auto-closed, so no new PR is needed — the fix is just a rebase.

**Symptom:** `gh pr view <child> --json mergeable,mergeStateStatus` reports `"mergeable":"CONFLICTING"`, `"mergeStateStatus":"DIRTY"` right after the parent merges, even though the child's own file changes don't actually overlap the parent's.

**Fix:**
```bash
git fetch origin main
git rebase origin/main   # no --onto, no SHA-hunting — patch-id matching drops the
                         # now-squashed parent commits on its own ("patch contents already upstream")
git fetch origin && git push --force-with-lease   # fetch first — see the bare-`--force` runbook above
```

Motivating incident: career-playbook [#923](https://github.com/brownm09/career-playbook/pull/923), stacked on parent [#878](https://github.com/brownm09/career-playbook/pull/878), 2026-07-27 — full detail: [dev-env#457](https://github.com/brownm09/dev-env/issues/457).

### Detail: EOL retrofit

#### `.gitattributes` eol retrofit: `git checkout`/`checkout-index` silently no-ops on already-existing files

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

### Detail: CRLF regex

#### `$`-anchored regex (grep / ripgrep / Grep tool) silently fails to match on CRLF-terminated lines

**Pattern:** A `$`-anchored regex passed to `grep`, ripgrep, or the `Grep` tool requires the match to end immediately before the line terminator. On a CRLF-terminated line the `\r` sits between the anchor position and the `\n`, so `$` never matches where an LF-only mental model expects — the query returns a clean "no matches," indistinguishable from a genuine absence.

**Symptom:** An end-anchored pattern (e.g. `^### Session \d+$`) returns zero matches against a file that plainly contains the text; the identical pattern with the trailing `$` dropped matches every occurrence.

**Fix:** Before trusting a zero-match result from a `$`-anchored pattern as evidence of absence, re-run without the trailing `$`, or confirm the file's line-ending convention directly (the `.gitattributes` eol retrofit section above has a ready-to-run byte-level scan; `git diff --stat` noise on an otherwise-unrelated edit is also a tell) whenever the file could plausibly be CRLF. Same CRLF root-cause family as the `.gitattributes` eol retrofit gotcha above; same "confident false negative from a clean-looking empty result" shape as the `git show <ref>:<path>` MSYS path-mangling note in the `/review` skill (CLI Scripting Checklist item 5 / [ADR-117](adr/117-absence-claims-need-absolute-paths.md)) — but a distinct trigger (a tool's own anchor semantics, not a git/shell path-conversion issue) that neither existing entry would lead a session to suspect.

**Motivating incident:** [dev-env#1006](https://github.com/brownm09/dev-env/issues/1006) — confirmed live 2026-08-16 while editing an un-normalized, 100%-CRLF engineering-journal session file (fixing [engineering-journal#225](https://github.com/brownm09/engineering-journal/issues/225)): `^### Session \d+$` returned zero matches against 23 present occurrences; dropping the trailing `$` matched all 23.

### Detail: Project boards

5. Refresh every machine-local cache of the old option IDs, in the same PR, with the regenerated IDs from step 4 — most commonly `.claude/hook-config.json` (the `required_fields`/`epic_options` the `post-tool-use.py` project-board hook reads) and `.claude/propose.json` (the `epics` array `/propose` reads). `post-tool-use.py` now live-fetches `single_select` field options at hook-fire time and falls back to this cache only when the live call fails ([ADR-076](adr/076-live-fetch-project-hook-single-select-options.md)), so a stale cache degrades to a labeled fallback rather than blocking — refreshing it here still matters for that fallback path and for `/propose`, which has no live-fetch equivalent. Don't assume `hook-config.json` is gitignored: that's dev-env's own convention (`.gitignore` ignores all of `.claude/`), not a universal one — e.g. lifting-logbook deliberately tracks it in git, so refreshing it there means a normal commit in this PR, not a local-only edit.

### Detail: No terminal spawn

For PowerShell or batch scripts on Windows, do not author or accept patterns that spawn a new console window or trigger UAC re-launch from inside the script: `Start-Process -Verb RunAs` self-relaunch, `Start-Process ... -NoExit` sibling consoles, `cmd /c start ... powershell ...`, or any equivalent round-trip. In agent-driven and non-interactive contexts the UAC dialog may have no desktop to render against, the prompt hangs the session or is silently dismissed, the new console dies before producing output, and the user pays cleanup tokens to disentangle the orphan processes.

### Detail: Test integrity

**Scope note.** The grep patterns above target JavaScript/TypeScript test frameworks (Jest, Vitest, Mocha). For pytest, Go `testing`, or Rust projects, extend the patterns to cover the language's idioms (`@pytest.mark.skip`, `pytest.skip(`, `t.Skip(`, `#[ignore]`, etc.) before running. A clean grep against JS-only patterns in a non-JS repo is not evidence the policy was satisfied.

**Known false-positive classes.** Two patterns over-match by design — review matches manually:
- `--bail` matches any CLI bail flag, including non-test-runner contexts.
- The threshold/coverage grep flags **any** change to those fields, including threshold *increases* (which are improvements, not violations). Only decreased thresholds are violations.

**Rule 4 incidents.** The 2026-09-19 retro counted tests that cannot fail at least 8 times in lifting-logbook, across PRs [#959](https://github.com/merickvaughn/lifting-logbook/pull/959) (three times), [#964](https://github.com/merickvaughn/lifting-logbook/pull/964), [#967](https://github.com/merickvaughn/lifting-logbook/pull/967) and [#968](https://github.com/merickvaughn/lifting-logbook/pull/968), and also in gas-lifting-logbook and cover-letter-runtime. `/review` caught them, not the tests. The rule is about a single test; ADR-144's Gate calibration covers standing checks. See [ADR-029](adr/029-test-integrity-policy.md) Amendment 2026-11-06 and [dev-env#1306](https://github.com/brownm09/dev-env/issues/1306).

### Detail: Baseline failures

The Suppression Policy and Test Integrity Policy guard against *new* and *degraded* tests, respectively. This policy guards against *inherited red state* — tests that were already failing when the branch was cut and that drift forever across branches because each session correctly declines to fix unrelated breakage. See [ADR-030](adr/030-baseline-test-failure-policy.md) for rationale and rejected alternatives.

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

### Detail: Privilege tests

When a project enforces a security or isolation guarantee via a specific runtime identity distinct from the default/admin one — a restricted DB role, a scoped service account, a sandboxed permission set, a tenant-scoped API credential — test suites and local dev must default to that restricted identity. The privileged/bypass identity is the opt-in exception, not the default. A test that passes under an admin/superuser/root connection proves nothing about whether the boundary it's supposed to exercise actually enforces anything.

Before adding coverage for a permission or isolation boundary, confirm the test actually runs under the identity the boundary is scoped to — not just that the code path is exercised. A test harness that resolves the dependency graph by hand (manually constructing objects) rather than through the real framework/DI machinery is equally blind to bugs that live specifically in that machinery's resolution order or timing.

**Why:** lifting-logbook's Postgres Row-Level Security was inert in production for 3+ weeks ([issue #644](https://github.com/merickvaughn/lifting-logbook/issues/644)) because every test and local-dev environment ran as the Postgres superuser, which bypasses RLS by design — the one enforcement bug that existed was invisible everywhere except real user traffic, and cost a multi-hour investigation to find. See [ADR-089](adr/089-privilege-restricted-test-defaults.md) for the full incident and rationale.

**How to apply:** this extends the Plan-then-optimize → Pass 3 Security dimension (below) — for any change touching a permission/authorization/isolation boundary, state explicitly whether test coverage runs under the restricted identity or the admin one, rather than leaving it implicit. Not mechanically greppable the way the suppression/test-integrity checks are — this is a judgment call during the Security audit, not a static pattern.

### Detail: Lockfile

In any npm repo, `package-lock.json` must stay in sync with the dependency ranges declared in `package.json`. When they drift, `npm ci` refuses to install (it requires an in-sync lockfile) and CI fails at the install step with a cryptic `EUSAGE` error — and `main` can go red. The motivating incident is lifting-logbook #432/#433, where clerk-backend, clerk-shared, and react drift reddened `main` and required reactive recovery PRs. See [ADR-036](adr/036-lockfile-drift-prevention.md) for the full defense-in-depth rationale.

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

### Detail: Experimental rigor

- **Tier 1 — test.** Required before any adopt/reject of a standing process. Pre-registration frozen in the tracking issue *before* generation: hypothesis + the one **primary outcome construct** the change exists to improve (stated arm-agnostically, as the outcome — *cohesion*, not a mechanism/proxy of it like *bookend correspondence*); one manipulated variable + held-constant list; success criteria classified **primary vs. secondary/mechanism** and traced to that construct — a secondary criterion can diagnose but **never alone decide the verdict** (that is criterion substitution, threat T10); a check built from one arm's *known* failure is a diagnostic, not verdict-bearing, until calibrated (T3); a neutral fresh-run baseline; a corpus fixed in advance (n≥3 or all-available, not solely incumbent-failure cases, with ≥1 known-good input); an **incumbent-influence inventory** (neutralize or log every default the challenger inherits — the challenger runs its own natural defaults); instruments **calibrated against known-good AND known-bad references before any arm is scored** (uncalibrated ⇒ quarantined, non-verdict-bearing); stage-matched processing; drafter ≠ scorer, blinded, order-randomized, fresh context per artifact; and a win bar + aggregation + n and k fixed — no generate-until-win. Probe data may inform the design; it never scores in the verdict.

**Scope boundary — this section governs *experiments*; standing *gates* are Pass 3's Gate-calibration dimension.** The calibration law is one law with two application sites: an instrument calibrated against known-good **and** known-bad references before it bears weight, and an uncalibrated check demoted to a diagnostic. What is experiment-only, and must not be imported into a gate review, is everything built around *comparing arms to reach a verdict* — tiers, the pre-registration freeze, the incumbent-influence inventory, blinding and order-randomization, the win bar, the T1–T10 sweep, and the verdict/decision vocabulary. A gate has no arms, no baseline, and no verdict; it has a threshold, and what it owes is the provenance of that threshold ([ADR-144](adr/144-gate-calibration-pass-3-dimension.md)).

### Detail: Context efficiency

**Session length:** `turn-count-hook.py`, a `UserPromptSubmit` hook, warns at 8 prompts and every 4 after, and at 150k tokens of context growth since the session's first turn and every 100k after. When warned, consider running `/clear` or `/compact` if the task scope has shifted — accumulated context inflates cost toward the end of long sessions. The prompt threshold can be overridden per-project: add `"turn_threshold": N` to `.claude/hook-config.json`. The growth thresholds replaced 40k and 20k of absolute context, which fired in every session, and a replay showed keeping those numbers on growth would have fired more often, not less ([ADR-150 Amendment 1](adr/150-context-growth-journal-stub-trigger.md#amendment-1-2026-11-06--the-size-warning-also-keys-on-growth)).

### Detail: Gate calibration

8. **Gate calibration** — when the plan introduces or tightens an automated check that classifies inputs the plan does not enumerate (a hook, CI guard, validator, linter rule, LLM judge, or any threshold/ratio/count applied to a population), state: the **measured property** it keys on, not the shape the author expects to see; the **calibration corpus** — known-good *and* known-bad references, with counts and the check's measured hit rate on each (the known-bad reference is also what proves the check does not pass *vacuously*); **where each constant came from** — the corpus member *and its class*, since a bare "calibrated: yes" hides a threshold read off the wrong class; and the **margin** to the worst known-good observation, *that observation named* — naming it is what makes a baseline sourced from the wrong class collide visibly with the corpus listing, where a bare ratio would not. **An uncalibrated check is a diagnostic: it may report, but it may not block, fail, or take a destructive action.** Same calibration law as `## Experimental Rigor`, applied to standing gates rather than experiments — worksheet form is `/experiment-audit` Step D4, references live in the project `## Experiments` registry where one exists, and a gate carries none of the tier / pre-registration / blinding / verdict machinery. A fixture-pinned unit test is out of scope: it enumerates its own inputs. **A check whose input is *extracted* — a section scan, a regex, a glob, a parse that degrades to `[]` rather than raising — additionally owes an assertion that the extraction is non-empty, separate from the comparison itself**, because a comparison against nothing succeeds and reports a confident passing count; ADR-116's own gate is the worked example (`test_runbook_section_exists` / `test_runbook_has_runnable_lines`, whose failure message reads *"a vacuous pass, not a clean one"*), and running this dimension backwards over the existing inventory found the two live instances in [ADR-145](adr/145-scope-discipline-self-referential-churn.md) §3. Plans introducing or tightening no such check: `N/A — no gate introduced or tightened` (see [ADR-144](adr/144-gate-calibration-pass-3-dimension.md)).

### Detail: Session summaries

Every session boundary — a completed task, a pause for input, or a return after a long gap — is a moment to orient the user. Four behaviors, all global. They are Claude-facing *output* rules, not mechanics; a targeted `Stop` hook — landing in [#656](https://github.com/brownm09/dev-env/issues/656), and dormant until it merges — reminds only when tiles were spawned but no table was emitted (the one mechanically verifiable half). See [ADR-095](adr/095-session-boundary-summaries-and-idle-refresher.md), [ADR-094](adr/094-tile-tables-and-issue-per-tile.md), and [ADR-128](adr/128-session-end-feedback-retro-table.md).

*Corrected 2026-11-06: the tile-table check has landed; [#656](https://github.com/brownm09/dev-env/issues/656) is closed and `stop-tile-enumeration-gate.py` is wired.*

- **Close each substantive stop with a summary.** When a stop follows real work or leaves something for the user, end the turn with three parts: **Completed** — what got done; **Context / ask** — what's being asked of the user now, if anything (decisions needed, what to review or test, each with its exact path/URL per *User-Actionable References*); **Remaining** — outstanding to-dos. Tile the *genuine out-of-scope follow-ups* among them — using the *Capture follow-ups as tiles* definition of what counts as genuine, and deduplicating against tiles/issues already created earlier in this session so a multi-stop session never re-files the same follow-up; merely list (don't tile) the immediate next steps of the task in progress — but once that remaining work must move to a *different* session (a blocked/orphaned worktree, a required restart, a session boundary), it is a cross-session follow-up: tile the self-contained hand-off per *Capture follow-ups as tiles*, never a paste-it-yourself chat brief ([ADR-113](adr/113-cross-session-handoff-tiles.md)). **Skip the summary on trivial exchanges** — a greeting, a one-line acknowledgment, a single clarifying question — where it would be pure noise (the same judgment the session-mode preamble uses).

- **Close a session that involved substantial hands-on correction with a feedback-retro table** ([ADR-128](adr/128-session-end-feedback-retro-table.md)). When a session included a meaningful amount of corrective or judgment-call feedback from the user — not just task requests, but "no, not that," register/diction catches, record-reconciliation calls, process-preference corrections, a repeated theme flagged more than once — close with a table: **#** (per *Table Row Numbers*) / **What was said** (paraphrased) / **Underlying pattern** / **Status** (already durable, partially addressed, or not yet formalized) / **Long-term mechanism** (how to make this durable). This is distinct from the substantive-stop summary above, which reports task status, not correction patterns — produce both when both apply, and don't let the retro table substitute for the Completed/Context/Remaining recap or vice versa. Any row landing on "not formalized" is a candidate for the existing memory + issue-pairing discipline under *Durable Preferences & Memory* above — the table is a discovery pass surfacing what to immortalize next, not a new persistence mechanism of its own, and it doesn't replace the periodic, cross-project `biweekly-retro` / `weekly-memory-audit` routines, which hunt for global signal at a different cadence and scope. Judging "substantial" is a judgment call, the same as "substantive" above — deliberately no automated skill or subagent scans for this yet (the risk of a rule-writer over-generalizing one correction into an overly broad rule, without a human confirming the generalization's scope, is real); apply this rule directly, the same way the summary bullet above already works without a dedicated skill behind it.

- **File an issue per tile, and end the session with a tile table.** When you spawn a tile, also file a tracking issue for it in the same repo and reference that issue in the tile prompt — every genuine tile gets a durable, linkable, status-trackable anchor (this **overrides** ADR-046's tiles-are-capture-not-tracking default; [ADR-094](adr/094-tile-tables-and-issue-per-tile.md)). Then, whenever one or more tiles were spawned this session, close with a table under the exact heading **`### Tiles spawned this session`** (the stable marker the enforcement hook keys on):

  **Status, honestly:** the live chip is the one-click "start the session" control and the issue is the durable anchor; there is no per-chip "was it started" API, so **Status** is the issue's open/closed state plus a *best-effort* "started" note from `list_sessions` (title/branch/PR match). A dismissed or lost chip is re-spawnable on request; the issue persists regardless.

- **Open with a refresher after a long idle gap.** When you return to a session after an extended idle period (default ~60 min; the `idle-refresher.py` `UserPromptSubmit` hook measures the gap and injects the cue — Claude cannot measure elapsed idle time on its own — so until that hook lands in [#655](https://github.com/brownm09/dev-env/issues/655), no cue fires and this rule is dormant, not wrong), lead your reply with a brief refresher — what we were working on, the current state, and any pending to-dos/tiles — before addressing the new prompt. The threshold is per-project–overridable via `idle_refresher_minutes` in `.claude/hook-config.json`.

*Corrected 2026-11-06: `idle-refresher.py` has landed and is wired; [#655](https://github.com/brownm09/dev-env/issues/655) is closed.*

### Detail: Error diligence

Error messages emitted by CI workflow guards, hooks, bots, and library exceptions are written by the *author of the guard* — they describe what the author thinks went wrong, not what actually went wrong. When a single check stands in for multiple upstream states (a job skipped because its `needs:` failed, a downstream output that defaults to empty when an earlier step never ran, a generic catch-block re-raise), the message can be confidently misleading. Restating that message as a diagnosis to the user, to a bot thread, or in a PR comment propagates the wrong root cause and produces follow-up corrections that cost more than the original verification would have.

Before acting on any automation- or library-emitted error message not covered by the Exemption below, complete three diligence steps:

1. **Locate the emitting line.** Find the file and line that printed the message. Read the conditional or `raise` site directly — do not infer it from the message text.
2. **Read the condition the code actually evaluates.** The literal expression (`if X != 'true'`, `if not config.get('key')`) is the ground truth. The message is a human-readable label for that expression and may have drifted.
3. **Distinguish the upstream signal from the message's narrative.** If the evaluated condition can be false for multiple reasons — secret missing, upstream job skipped, network 5xx during a fetch, schema mismatch — trace one level up before accepting the message's framing. For CI: check the parent job's status and `needs:` chain. For hooks/scripts: check the input that produced the falsy value, not just the falsy value itself.

When uncertain after the three steps, surface the uncertainty explicitly: "the guard printed X; I have not yet confirmed the underlying condition" rather than restating X as fact. This applies equally to user-facing messages, PR comments, and bot replies.

**Anti-pattern:** quoting an emitted error message back to the user as the diagnosis without having read the emitting line. The message is evidence of *what was reported*, not evidence of *what is true*.

**Exemption:** local errors where the message is reliable by construction — syntax errors, file-not-found at a path you just wrote, static type-checker errors that name the offending symbol (TypeScript `TS2304`, Pyright `reportUndefinedVariable`, etc.) — do not require the three-step trace. The rule targets guard messages that summarize composite upstream state.

**Rationale incident.** lifting-logbook [PR #395](https://github.com/merickvaughn/lifting-logbook/pull/395) (2026-06-01). The `staging.yml` deploy-prereq guard printed a "Staging Clerk secret key is not configured" error and pointed to `docs/deploy.md` Step 4. The actual upstream chain was a transient 504 from Artifact Registry in `build-images` → `deploy-api` skipped because of `needs: build-images` → empty `clerk_configured` output → integration-tests' `!= 'true'` check fired the misconfig message. The secret was already correctly set. Both the github-actions bot and I reported the wrong root cause until the user pushed back. Full trace: [PR #395 comment](https://github.com/merickvaughn/lifting-logbook/pull/395#issuecomment-4594434736). See [ADR-034](adr/034-error-message-diligence.md).

### Detail: User references

**Reopen an application yourself before asking the user to test something in it.** When the session closed the application, or had the user close it, launch it and confirm the process and its window exist, then ask. Don't ask the user to reopen it, and don't make the launch wait on an action of theirs. The exception is a step that needs the user's own hands first, such as a sign-in, or elevation once `sudo` has failed (see *Platform & Environment*): say so and state exactly what is needed. Stated by the user on 2026-11-05, after a session asked for a RetroArch test while RetroArch was still closed ([dev-env#1211](https://github.com/brownm09/dev-env/issues/1211)). Nothing enforces this mechanically.

### Detail: Abbreviations

Incident: a 2026-09-30 career-playbook interview-prep session coined "R2P" (Round 2 technical prep), "R1"/"R4" (interview rounds), "SD" (system design), and "ACC:51"-style row citations, and used them across prep docs, chat, commits, and a PR body. The user had never seen them defined and had not agreed to them ([dev-env#1120](https://github.com/brownm09/dev-env/issues/1120)). Nothing enforces this mechanically; a Stop hook or doc lint is warranted only if it recurs now that the rule is written down.

### Detail: Asking the user

Stated by the user on 2026-11-05, after a session closed with a numbered list of decisions in prose. Asked again with clicks, the same decisions took seconds and the answers were unambiguous ([dev-env#1230](https://github.com/brownm09/dev-env/issues/1230)). Nothing enforces this mechanically.

### Detail: Journal composition

- **PR grouping heuristic.** When two or more stubs share a PR number (one stub has it in
  `prs_opened`, another in `prs_closed`), compose them under a single H2 dialogue section
  rather than producing a separate H2 per stub. Annotate with "→ merged in session N" (where
  N is the 1-based ordinal of the closing stub for that day) at the end of the section. Any
  stub written on a day where the open-PR records (`open-prs/<N>.json` shards or a legacy
  `open-prs.jsonl`) show the same PR as open should also be grouped under that H2, even if neither
  `prs_opened` nor `prs_closed` is set for that PR in its manifest shard — this covers PRs that span
  more than two sessions. This prevents the composed
  journal from fragmenting create-iterate-review sequences into unrelated-looking sections.

- **Meta triggers are never asked about.** `/journal-compose` composes the meta journal itself —
  attended or unattended, with no y/n prompt — from compose-generated *derived stubs* that are
  consumed in the same run (its Step 6.7), and it never writes `sessions/meta/YYYY-MM-DD_draft.md`
  (nothing composes that file). User decision, 2026-10-02: "In the future, please do the meta
  journal entries." A miss is recoverable by hand:
  [`docs/REFERENCE.md` → Late meta entry recovery](#late-meta-entry-recovery).
  See [ADR-082](adr/082-journal-compose-worktree-isolation.md) Addendum (2026-10-02),
  [dev-env#52](https://github.com/brownm09/dev-env/issues/52) and
  [#892](https://github.com/brownm09/dev-env/issues/892).

### Detail: Journal shards

**Sharded companion files — no shared-file editing.** The stub's two companion files are also sharded
per session / per PR ([ADR-056](adr/056-per-session-sharding-journal-companion-files.md)), so
the stub isolation above extends to them and **no session ever writes a file another session also
writes**:

- **Manifest:** each session writes its own shard `sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`
  (one JSON object, paired 1:1 with the stub) **with the Write tool**; a later same-session rewrite
  (e.g. setting `prs_closed:[N]`) uses the **Edit tool** — never a shell heredoc/`echo`/redirect,
  mechanically blocked by `pre-tool-use-journal-shell-write-guard.py`
  ([ADR-129](adr/129-journal-shell-write-guard.md)). Setting `prs_closed:[N]` after a merge
  edits *this session's own shard* — never a file another session touches. A shard **re-created after a
  compose has consumed the original** (e.g. late PR-merge bookkeeping) must carry the **full five-field
  set** (`stub`, `topic`, `tokens`, `prs_opened`, `prs_closed`) — never just the field being updated; the
  `journal-shard-write-advisory.py` hook flags violations (and BOMs) at write time (dev-env #556,
  [ADR-081](adr/081-write-time-journal-shard-validation-hook.md)).
- **Open PRs:** each open PR is its own shard `sessions/<project>/open-prs/<N>.json`. Opening PR #N
  writes `<N>.json` **with the Write tool** (never a shell heredoc/`echo`/redirect —
  [ADR-129](adr/129-journal-shell-write-guard.md)); merging/closing it **deletes** that file
  (`rm`/`Remove-Item` — a deletion, not a content-write, stays outside that guard's scope) — a per-PR
  delete that cannot touch any other PR's record, even when a *different* session or the
  `reconcile-open-prs.py` hook does the removal.
- **Tiles:** each spawned tile is its own shard `sessions/<project>/tiles/<issue-number>.json`, keyed by
  the tile's paired GitHub issue and filed under the tile's **target** project (the repo the work
  belongs in, which the shard records as `cwd`), not the
  spawning session's. **Write it immediately after each tile proposal** — the payload is what lets a
  lost or lapsed card be re-proposed, which the card itself cannot do
  (ADR-094); closing the paired issue is the completion signal. **Write it with the Write tool** — the
  general rule for all four journal content-file kinds (stub, manifest, open-PR, tile), mechanically
  enforced by `pre-tool-use-journal-shell-write-guard.py` rather than restated per-kind here (`prompt` is
  free prose and `cwd` is the one required field that is a path — see
  [ADR-129](adr/129-journal-shell-write-guard.md)). These four kinds are the **unconditional**
  special case of the global `## Authoring File Content` rule above: elsewhere a short, single-line,
  hazard-free literal may still be shell-written, but here it may not, because every one of these files
  always carries free prose or a path field.
  Schema, the write recipe, and current phase:
  [REFERENCE → Tile shards](#tile-shards-sessionsprojecttilesissue-numberjson);
  rationale, and why a headless process *cannot* respawn tiles instead, in [ADR-118](adr/118-tile-persistence-shards.md).

Because shards are disjoint files, git merges concurrent sessions' writes cleanly and removal is a
per-file delete — the pull-first + surgical-edit discipline of the superseded ADR-054 is **no longer
needed** (ordinary pull-before-push git hygiene still applies). That disjointness is a content-merge
guarantee only — it does not extend to the local git index, which every concurrent session in this
checkout shares. **Commit with an explicit pathspec, not a bare `git commit`:** a bare `git commit`
commits the *entire* staged index, not just this session's `git add`-ed files, and can sweep a concurrent
session's already-staged shard into this session's commit — see the `--` pathspec in the commit steps
below ([ADR-056 → Addendum](adr/056-per-session-sharding-journal-companion-files.md)). Readers
still accept the legacy single-file `YYYY-MM-DD.manifest.jsonl` / `open-prs.jsonl` during the transition;
writers emit only shards. Schemas and write/delete steps: [REFERENCE → Engineering Journal Internals](#engineering-journal-internals).

**An explicit pathspec can also drop half of a rename — check `git status --short` after committing.** `git mv` (or an equivalent manual rename) stages a delete at the old path and an add at the new one; a pathspec naming only the new path commits the add and leaves the delete staged, so `HEAD` keeps the old file while the working tree does not. Every gate that scans the working tree then passes while CI, which checks out the commit, fails — often with nothing but an artifact/file count off by one in the CI log as the tell. Include the **old** path in the `git commit -- <paths>` list for a rename (after `git mv` specifically, `git add` on that same old path fails outright — `git mv` stages the removal atomically, leaving nothing on disk or in the index for a subsequent `git add` to act on — so `git add` and `git commit` take different path lists for the same rename). More generally: after **any** explicit-pathspec commit, in this checkout or any other, confirm `git status --short` prints nothing — a leftover `D `/`M ` line (the letter in the *first* column: staged, not yet committed) is the part of the change that did not get committed. Incident: career-playbook PR #955, 2026-07-28 ([dev-env#927](https://github.com/brownm09/dev-env/issues/927); [ADR-056 → Addendum](adr/056-per-session-sharding-journal-companion-files.md)).

### Detail: Journal orphaned shards

**An orphaned open-PR shard *deletion* is yours to commit — the one deliberate exception to "commit only your own files."** `reconcile-open-prs.py` unlinks the shard of every PR it finds MERGED/CLOSED at session start but never commits, and a session that opens no PR writes no stub — so an unlink can sit uncommitted in the shared canonical indefinitely, leaving permanent `git status` noise every later session must re-triage, one `git restore` away from resurrecting the stale entries, and a *committed* branch that still lists merged PRs as open until a compose reconciles it. When the hook reports a **deletion whose PR it confirmed MERGED/CLOSED**, commit it immediately with an explicit pathspec, whether or not you write a stub:

```bash
git -C C:/Users/brown/Git/engineering-journal add -- sessions/<project>/open-prs/<N>.json
git -C C:/Users/brown/Git/engineering-journal commit -m "journal: close open-pr shard #<N> (merged)" -- sessions/<project>/open-prs/<N>.json
```

Safe precisely because ADR-056 made each shard a disjoint per-PR file: the delete cannot touch another PR's record, and the state was verified live. **The converse still holds** — a dirty open-PR path that is *added or modified* is a concurrent session's in-flight shard: leave it alone, and never sweep it into your pathspec. The hook reports those two classes separately, plus a third — a deletion whose PR state it could **not** confirm (a `gh` failure, e.g. an exhausted GraphQL budget) — which you do not commit blind. The hook never commits on its own: it is an advisory `UserPromptSubmit` hook that must fail open, it runs in a checkout whose git index every concurrent session shares, and it would be committing onto whatever branch the canonical happens to hold. See [ADR-119](adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md).

**An orphaned tile shard *deletion* is yours to commit too — the open-PR exemption above extends to tile shards.** This resolves the ambiguity in [dev-env#950](https://github.com/brownm09/dev-env/issues/950): two sessions on 2026-08-06 read the bullet above and reached opposite conclusions about whether it covered tile shards. It does. `reconcile-pending-tiles.py` unlinks the shard of every tile whose paired issue it finds `CLOSED` at session start but never commits, and a session that spawns no tile writes no stub — so exactly the open-PR gap recurs: an unlink can sit uncommitted in the shared canonical indefinitely, is one `git restore` away from resurrecting a finished tile, and a committed branch keeps listing it as pending until a compose reconciles it — which is exactly what over-reports the session-start "pending tiles" count. `reconcile-pending-tiles.py` now also scans `git status --porcelain -- sessions` for a tile-shard path already deleted from the working tree but not yet committed, recovers its paired issue from `git show HEAD:<path>` (the file itself is gone), and re-confirms that issue's state with its own live lookup before reporting. When it reports a **deletion whose issue it confirmed CLOSED**, commit it immediately with an explicit pathspec, whether or not you write a stub:

```bash
git -C C:/Users/brown/Git/engineering-journal add -- sessions/<project>/tiles/<issue-number>.json
git -C C:/Users/brown/Git/engineering-journal commit -m "journal: close tile shard #<issue-number> (issue closed)" -- sessions/<project>/tiles/<issue-number>.json
```

More than one confirmed shard in the same session: one combined command, every path space-separated in both the `add` and the `commit -- <paths>` pathspec — never the bare `tiles/` directory. Safe for the identical reason the open-PR case is safe: each shard is a disjoint per-issue file ([ADR-118](adr/118-tile-persistence-shards.md)), so the delete cannot touch another tile's record, and the state was verified live. **The converse still holds** — a dirty tile path that is *added or modified* is a concurrent session's in-flight shard: leave it alone, never sweep it into your pathspec. The hook reports that class separately, plus a deletion whose issue state it could **not** confirm (a `gh`/`git` failure, or a filename/embedded-`issue` mismatch) and one that came back unexpectedly **open** (an anomaly — never commit; restore instead with `git checkout HEAD -- <path>`) — neither is committed blind. The hook never commits on its own, for the same reasons its `reconcile-open-prs.py` sibling doesn't, and the whole pass is suppressed while the canonical is mid-merge. See [ADR-118](adr/118-tile-persistence-shards.md) Amendment 5 and [ADR-119](adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md).

### Detail: Journal day rollover

**Day rollover: always cut `draft/<today>` from `main`, however many prior days' drafts are still unmerged.** A stub's *filename date* and its *branch date* must always match, because every discovery path keys on **both**: `/journal-compose` resolves `SOURCE_BRANCH=draft/<DATE>` *and* globs `sessions/*/<DATE>_*.stub.md` **on that branch**, and the nightly `daily-journal-compose` routine gates on `show-ref --verify refs/remotes/origin/draft/${DATE} || exit 0`. A stub committed to a branch named for a different day is therefore invisible to both — and the routine's miss is **silent**, not an error. So never join yesterday's still-unmerged branch to "keep the day together": that guarantees the newer day is never composed *and* never reported, and when the stale branch finally composes (it composes only its own date) the newer day's stubs ride into `main` uncomposed, where nothing ever looks for them again. Several unmerged `draft/*` branches coexisting is the **normal** steady state — they are independent per-day units, and `new-day-journal-check.py` already enumerates them — so it is not a reason to reuse one. Composing the stale branch first is the right *remediation*, but it cannot be the rule: `/journal-compose` is a dedicated-session operation, so a session that merely needs to write a stub cannot perform it. The bounded cost of cutting fresh: `draft/<today>` taken from `main` does not carry yesterday's open-PR shard edits, so its `open-prs/` view is only as current as the last merge to `main` — `reconcile-open-prs.py` corrects that live at session start via `gh pr view`. **That includes shard *deletions*, and the invariant is about branch lineage, not session ordering: a shard deletion committed to draft branch A stays invisible to any branch B cut from `main` until A merges.** A merged PR's shard removed only on an unmerged branch is still present on `main`, so a branch cut from `main` **resurrects** it — until the reconcile hook re-unlinks it on disk and someone commits that deletion *on the new branch*. The corollary for a single session: cut the branch at the start, not after doing shard cleanup, or you leave the resurrected deletions uncommitted behind you (the hook's sentinel re-arms on age, so the same session may not re-report them). But the corollary is not the whole rule — verified live on 2026-07-22, `draft/2026-07-22` was cut correctly, at the start of a session, from a `main` that still carried all four shards [dev-env#866](https://github.com/brownm09/dev-env/issues/866) had just cleaned up, and all four resurrected anyway; no session ordering would have prevented it. **A deletion is durable only once its carrying branch merges to `main`** — so land the day's draft branch rather than treating a re-commit as the fix.

**Date-mismatched stub — the divergence state, named and handled.** A stub whose filename date differs from the date of the draft branch it is committed on. When you arrive and find today's stubs already sitting on an older `draft/<D>`, repair **additively** — never rewrite that branch's history, since every concurrent session shares it:

1. Still cut `draft/<today>` from `main` for your own stub. This bounds the damage to the already-misfiled set instead of adding to it.
2. Surface it and tile the remediation (a cross-session hand-off per *Capture follow-ups as tiles*) — never leave it implicit.
3. Once `draft/<D>` composes and merges, the mismatched stubs are on `main` uncomposed. Bring them into their own day's branch: `git -C C:/Users/brown/Git/engineering-journal fetch origin`, then `checkout draft/<today>` and `merge origin/main`. `/journal-compose <today>` then discovers them by filename date.
4. If `draft/<D>` had already merged before you noticed, this is the resurrected-branch path instead: `py -3 ~/.claude/scripts/reconcile-late-stubs.py draft/<D>`. The script renames each moved stub and manifest to the target branch's date and updates the manifest's `stub` field, so that branch's compose finds them. It marks the moved commits with the tag `reconciled/<branch>`, so a repeat run moves nothing. After moving stubs by hand, mark them yourself: `git push origin <tip sha>:refs/tags/reconciled/<branch>`.

**The first session to see the late-commits warning runs the commands it prints, whatever that session's task.** If a command is blocked, for example because auto mode won't delete a remote branch, give it to the user and say why. `new-day-journal-check.py` flags a composed day's branch only when session files changed after that day's last merged PR, so a warning means content that may be in no journal. A branch whose tip already has its `reconciled/<branch>` tag is not flagged, even while its content is being composed under a tracking issue. The earlier form of this warning appeared 57 times across 29 sessions while 25 commits stayed out of the journal, because each session treated it as another session's job ([dev-env#1262](https://github.com/brownm09/dev-env/issues/1262)).

Detection is advisory-only, in `new-day-journal-check.py` — which for this one check is deliberately **not** suppressed in worktree sessions, since those write stubs into the canonical via `git -C` too and are exactly who needs the warning. Residue of the pre-rule behavior: 26 stubs across 5 dates (2026-05-11 … 2026-07-03) sat uncomposed on `origin/main` as of 2026-07-22. See [ADR-119](adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md); incident: [dev-env#866](https://github.com/brownm09/dev-env/issues/866).

### Detail: Journal stale edit

**Stale edit in the canonical: apply the clean fix, and don't hold your stub back to ask.** This covers an uncommitted journal edit you did not make, found when you need the canonical for your own stub. An edit is **stale** when its file was last modified at least 24 hours ago (`stat -c '%y' <path>`); anything newer counts as a live session's and stays untouched. A stale added or modified shard follows this rule; a newer one stays under the leave-it-alone rules above. Uncommitted deletions have no file to check and stay under the open-PR and tile shard rules above. mtime is used because it can be checked from any session, while no check can reliably tell whether another session is still running. A wrong call costs little, since the edit lands unchanged on its own day's branch. Then:

1. Commit the stale edit with an explicit pathspec to the branch whose date matches its filename, and push. If the canonical is on another branch, `git checkout draft/<D>` first; git carries the edit across. A file with no date in its name, such as a shard, stays on the current branch.
2. Move the canonical to today's draft branch, or cut it from `main` if it doesn't exist yet.
3. Commit your own stub there.

Two cases change the steps:

- **Day `<D>` is already composed.** Check for its composed files on `main`: `git -C C:/Users/brown/Git/engineering-journal ls-tree -r origin/main --name-only -- sessions | grep -E "^sessions/[^/]+/<D>-"` prints at least one path. Don't look for a merged PR from `draft/<D>`, because a draft with conflicts merges from `compose/<D>` instead. Commit the stale edit on today's branch, renamed to today's date, and say so in your stub. The journal may repeat a little content, which is better than losing it. Your own stub follows the same rule: it takes the date of the branch it lands on, even if the session started the day before, because compose finds stubs by filename date.
- **No clean fix exists.** If `git checkout` refuses, a merge is in progress, or the edit is live, leave it alone and write your stub on disk. Tell the user what is blocking the commit.

Never use git plumbing to push around a stuck checkout. See [ADR-119](adr/119-day-rollover-draft-branch-and-orphaned-shard-deletions.md) Amendment 2.

### Detail: Journal canonical

**Never create a dedicated worktree to write a stub — always operate directly on the canonical via `-C`.** `draft/YYYY-MM-DD` can be checked out in exactly one place at a time (git's one-worktree-per-branch rule) — the canonical-direct exemption above exists precisely so every concurrent session reaches the *same* checkout instead of racing for the branch. A session whose own primary repo is a different project — most often a tile-launched session finishing its real work and writing a stub as a side effect — must still run every step below as `git -C C:/Users/brown/Git/engineering-journal <command>`: never `EnterWorktree` (it cannot target a different repo — see the bullet above) and never a manually-created `git worktree add <path> ... draft/YYYY-MM-DD`. That `git worktree add <path> -b <branch> origin/<default-branch>` pattern is the right way to isolate cross-repo work in general — the Stub file workflow is the one deliberate exception to it. Applying the general isolation instinct here instead locks `draft/YYYY-MM-DD` to a throwaway worktree and blocks the canonical, and every other concurrent session, from reaching it until that worktree is parked or removed. Confirmed live twice simultaneously on 2026-07-12 (`stub-823-120134`, `stub-829-165612`, each locking that day's draft branch away from the canonical); a `PreToolUse` hook (`pre-tool-use-journal-draft-worktree-guard.py`, ADR-105) now blocks this at the tool-call level. See [dev-env#747](https://github.com/brownm09/dev-env/issues/747).

**Sync the canonical with `fetch` + `merge --ff-only origin/<branch>`, never `git pull`.** `git pull` (with or without `--ff-only`) fetches into `.git/FETCH_HEAD` and then merges whatever that file holds, and every concurrent session shares that one file. Another session's fetch can rewrite it in between, so the merge fails with `fatal: not something we can merge in .git/FETCH_HEAD` or, worse, merges the wrong branch into `draft/YYYY-MM-DD`. `merge origin/<branch>` reads the remote-tracking ref instead, which each fetch updates atomically. If the fast-forward fails because the branch has diverged, run the same `merge` without `--ff-only`. See [ADR-056](adr/056-per-session-sharding-journal-companion-files.md) Addendum (2026-10-31), [dev-env#1166](https://github.com/brownm09/dev-env/issues/1166).

### Detail: Journal steps

**First session of the day:**
1. `git -C C:/Users/brown/Git/engineering-journal fetch origin main && git -C C:/Users/brown/Git/engineering-journal checkout main && git -C C:/Users/brown/Git/engineering-journal merge --ff-only origin/main`
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
   (see [REFERENCE → Engineering Journal Internals](#engineering-journal-internals)
   and [ADR-129](adr/129-journal-shell-write-guard.md))
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Write this session's manifest shard `sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl` **with the
   Write tool** (never a shell heredoc/`echo`/redirect — same mandate as step 4,
   [ADR-129](adr/129-journal-shell-write-guard.md)) — one JSON object (see
   [REFERENCE → Engineering Journal Internals](#engineering-journal-internals))
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
1. `git -C C:/Users/brown/Git/engineering-journal fetch origin draft/YYYY-MM-DD && git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD && git -C C:/Users/brown/Git/engineering-journal merge --ff-only origin/draft/YYYY-MM-DD`
2. Read the open-PR records (`sessions/<project>/open-prs/*.json` shards, plus any legacy `open-prs.jsonl`) if present — include their PR list as session context before starting work (the `reconcile-open-prs.py` hook also surfaces this at session start).
3. Find the most recent stub and read only its `<!-- next-session-context -->` paragraph:
   ```bash
   ls C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.stub.md | sort | tail -1
   ```
4. Create a new `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` **with the Write tool** (never a shell
   heredoc/`echo`/redirect — see step 4 of "First session of the day" above,
   [ADR-129](adr/129-journal-shell-write-guard.md)) with the current session block
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Write this session's manifest shard `sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl` **with the
   Write tool** (never a shell heredoc/`echo`/redirect — same mandate as step 4,
   [ADR-129](adr/129-journal-shell-write-guard.md)) — one JSON object (see
   [REFERENCE → Engineering Journal Internals](#engineering-journal-internals))
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

### Detail: Journal triggers

  - **Report / analysis generated** — whenever the user requests any report or analysis (an audit, an investigation write-up, a **verification or deploy-check**, a comparison, a findings summary, etc.), capture it in the journal: save the full output as an artifact under `sessions/<project>/reports/YYYY-MM-DD-<slug>.md` and link it from the session stub (create the stub if none exists yet for the session). Report/analysis generation is itself a journal boundary — it does not require a PR. Short analyses (≲ one screen) may be inlined in the stub instead of linked; anything longer must be a linked artifact so the stub stays scannable. Applies to all projects. (A `/review <PR-URL>` session stays exempt per the exclusion below — its findings live on the PR, not in a free-standing report.) This trigger is mechanically backstopped by `stop-journal-stub-checkpoint.py` (a Stop hook): when a session with report/analysis/verification intent does substantive work but ends with no stub — and opened no PR, isn't a `/review` session, and carries no "skip journal" override — it blocks the stop once with an advisory reminder ([ADR-100](adr/100-stop-journal-stub-checkpoint-hook.md)).

  - **Context growth band** — when `turn-count-hook.py` prints a `[journal-stub]` nudge, update this session's stub in place (Edit tool), or create one if the nudge says none was written (Write tool, per the Stub file workflow below), then commit with an explicit pathspec and push. The nudge fires once per band of cumulative context growth since the session's first turn: first at 150k tokens of growth, then every further 100k (override with `journal_stub_context_first_tokens` / `journal_stub_context_step_tokens` in `.claude/hook-config.json`). Growth is measured from the first turn because this machine's session-start context is already ~130–185k, and a compaction's drop never resets it, so work done after a compaction still counts toward the next band. This bounds how much work a crash, compaction, or app restart can cost between the event triggers above. If the stub already covers everything since its last update, no edit is needed ([ADR-150](adr/150-context-growth-journal-stub-trigger.md)).

## Project Instruction Detail

The reasoning, incidents and procedures behind rules in the repo-root [`CLAUDE.md`](../CLAUDE.md), dev-env's project file, which loads in every dev-env session. They moved here from that file in [dev-env#1297](https://github.com/brownm09/dev-env/issues/1297), the same split [ADR-159](adr/159-instruction-files-hold-rules-not-stories.md) made for the global file. Only what `CLAUDE.md` doesn't say is kept here, so each rule lives in one place: read the rule there, and its story here. The per-item detail of its `## Testing` index is in [TESTING.md](TESTING.md).

### Project detail: Hook output streams

Why the stream rule in `## Observability` matters: an advisory on the wrong stream is silent by construction. `dev-env-sync.py`'s exit-0 warnings sat on stderr for months, hiding a fast-forward-pull failure for 36+ hours and 21+ commits of drift before anyone noticed (dev-env#694, [ADR-098](adr/098-dev-env-sync-advisories-to-stdout.md)). `journal-stop-check.py`'s archive reminder had the mirror-image `Stop`-hook bug: an always-exit-0 advisory on an event that forwards neither stream on exit 0 ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md)). Stderr with exit 2 works for a blocking hook because it is the one channel every hook event forwards to Claude on a non-zero exit.

The suite items that stand in for boundary observability are the hook-script syntax check (item 1), the `pyw -3` stdio test (item 2) and the pre-push self-test (item 3).

### Project detail: Dev-env architecture

**Why the launcher is outside the junctions.** When the canonical regresses to an older tree, everything under `~/.claude/scripts` regresses with it, but the launcher does not. Without it, a missing script makes Python exit 2, which Claude Code reads as a block on every matching tool call, machine-wide (dev-env#1146). `ensure_launcher` copies it *before* the sync writes any hooks that name it, and when the sync refuses `hooks` because a named script is absent, it keeps the live wiring and says "pull first". The restore command uses an absolute path so it works in Windows PowerShell 5.1, the Terminal panel's shell, which expands neither `~` nor `&&`.

**Why `settings.json` stopped being a symlink.** It used to be symlinked, which made the app dirty a tracked file and blocked the canonical's fast-forward *permanently*, serving stale hooks and skills machine-wide, since `~/.claude/{scripts,skills,hooks}` are links into that same checkout (dev-env#1049, [ADR-139](adr/139-machine-local-settings-with-shared-source-sync.md)). An unclassified key in `settings.shared.json` is reported rather than silently ignored.

**Why project-specific keys leave the user file.** Machine-local is not the same as correctly scoped. A key the app writes carrying project-specific content (an auto-mode environment scan, anything naming a repo's paths, visibility or policies) loads for every project's session on this machine, even though the file is out of git. `.claude/settings.local.json` is the only per-project personal-settings slot Claude Code has; check that the project's `.gitignore` excludes it first. The rule is stated in full in [`claude/CLAUDE.md`](../claude/CLAUDE.md) → Platform & Environment.

**Why the canonical stays on `main`.** `~/.claude/scripts/`, `skills/` and `hooks/` are links into the canonical's working tree, so a feature branch checked out there makes newly merged hooks and scripts invisible until it returns to `main`. (`settings.json` was one of those links until ADR-139.)

**Routines.** `~/.claude/routines/` exists so a routine can read its own canonical source at run time, not so the scheduler registers it. The `scheduled-tasks` MCP tool never reads or writes through it: its own `~/.claude/scheduled-tasks/` holds one subdirectory per registered task, reusable routines and one-off tasks side by side, deliberately not a version-controlled mirror, since one-offs have no business in git. The self-referencing prompt keeps a live task self-healing against drift instead of silently diverging, as `daily-journal-compose` did until [dev-env#464](https://github.com/brownm09/dev-env/issues/464).

**Why routines sync first.** Scheduled tasks fire into Claude-managed worktrees whose branches were cut from whatever `main` was at worktree creation. Without an explicit sync, the routine reads stale files, or aborts because a recently merged file is missing on the worktree branch. The `sync-routine-worktree` skill (`claude/skills/sync-routine-worktree/SKILL.md`) handles the fetch, a sync that depends on the branch class (Claude-managed worktree, `main`, other), file existence verification, and an abort with a push notification on conflict.

### Project detail: GitHub Project board

**Hook config.** `dev-env-sync.py` copies `claude/hook-config.shared.json` into the canonical's `.claude/hook-config.json` on any prompt where that file is missing, and never overwrites an existing one. If it's missing anyway, `post-tool-use.py` prints `[project-hook] WARNING: ... NOT added to project #3` with the seed command and the manual `gh project item-add`, instead of exiting silently ([ADR-052 Amendment 2](adr/052-worktree-config-canonical-fallback.md), dev-env#669).

**Fallback commands.** Run these four steps when the hook didn't fire or its add failed, substituting the PR URL for an issue URL. A chain whose last command exits non-zero runs no PostToolUse hook at all, even when its `gh` command succeeded. To check for the rare inert state where every PostToolUse hook is silent, look at `~/.claude/scratch/bash_state_<session-id>.json`: a file updated at the last successful shell call means hooks ran ([ADR-053's 2026-11-07 amendment](adr/053-posttooluse-hooks-inert-in-background-sessions.md)). Either way, these steps are the recovery. The node IDs and option IDs are in the Field IDs table in the repo-root `CLAUDE.md` → GitHub Project.

```bash
# 1. Add issue/PR to project, capture item ID
URL="<issue-or-pr-url>"
TMPFILE="C:/Users/brown/.claude/scratch/tmp_item_$$.json"
gh project item-add 3 --owner brownm09 --url "$URL" --format json > "$TMPFILE"
ITEM_ID=$(node -e "const d=JSON.parse(require('fs').readFileSync('$TMPFILE','utf8')); console.log(d.id);")
# No cleanup here: scratch leftovers are harmless, and an `rm` chained onto
# `gh` is never auto-approved (ADR-147). To clean up, use a separate call:
#   rm -f C:/Users/brown/.claude/scratch/tmp_item_*.json

# 2. Set Impact   (08de2558=High  6320e8a6=Medium  d8a85c2f=Low)
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkNc --single-select-option-id <option-id>

# 3. Set Why (one sentence — the cost of not fixing it)
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTF_lAHOAjEKvM4BWKFezhRgkN0 --text "<why this matters>"

# 4. Cache the item ID (dev-env#1057, ADR-141) so a later lookup (e.g. to move
#    Status) is a zero-cost hit instead of another full-board fetch. The regex and
#    key format must stay byte-identical to _gh_project.py's _ISSUE_URL_RE /
#    _cache_key (project-owner/project-number then repo#number, both lower-cased) —
#    a drifted copy here would silently never hit what that module writes.
GPI_URL="$URL" GPI_ITEM_ID="$ITEM_ID" node -e "
  const fs = require('fs'), path = require('path');
  const m = process.env.GPI_URL.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+)\/(?:issues|pull)\/(\d+)\/?\$/);
  if (!m) process.exit(0);
  const [, owner, repo, number] = m;
  const cacheFile = 'C:/Users/brown/.claude/scratch/project-item-cache.json';
  let cache = {};
  try { cache = JSON.parse(fs.readFileSync(cacheFile, 'utf8')); } catch (e) {}
  cache['brownm09/3|' + (owner + '/' + repo).toLowerCase() + '#' + number] = process.env.GPI_ITEM_ID;
  fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
  const tmp = cacheFile + '.' + process.pid + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cache, null, 2), 'utf8');
  fs.renameSync(tmp, cacheFile);
"
```

**Why REST works when GraphQL doesn't.** Every `gh project` command spends the GraphQL bucket, and so does the fallback in `get-project-item.sh`. GitHub's REST API for Projects v2 spends the separate `core` bucket (the response header says `X-RateLimit-Resource: core`), so a GraphQL rate-limit error doesn't block the board. One token has two rate-limit counters, a read shows only one, and some calls still pass while the busier counter is at 0 ([finding on dev-env#1238](https://github.com/brownm09/dev-env/issues/1238#issuecomment-6671406131)): on 2026-11-05 a call was rejected with 3717/5000 remaining on the reading. The rule for judging the error is in [`claude/CLAUDE.md`](../claude/CLAUDE.md), *Never mutate git state directly*. See GitHub's [Project items](https://docs.github.com/en/rest/projects/items) and [Project fields](https://docs.github.com/en/rest/projects/fields) REST documentation.

REST takes the numeric field IDs listed in `CLAUDE.md`, not the `PVTI_`/`PVTSSF_`/`PVTF_` node IDs, so an ID from `get-project-item.sh` or the local item-ID cache won't work here, and these commands neither read nor write that cache. If a field ID stops working, list the current ones with `gh api users/brownm09/projectsV2/3/fields`.

```bash
N=<issue-or-pr-number>

# Add an issue or PR to the board; prints the numeric item ID. Use type=PullRequest for a PR.
gh api -X POST users/brownm09/projectsV2/3/items -f type=Issue -f owner=brownm09 -f repo=dev-env -F number=$N --jq .id

# Look up the numeric item ID of an item already on the board.
ITEM_ID=$(gh api --paginate "users/brownm09/projectsV2/3/items?q=$N&per_page=100" \
  --jq ".[] | select(.content.number == $N and (.content.html_url | contains(\"/brownm09/dev-env/\"))) | .id")

# Set Status (Todo=f75ad846, In Progress=47fc9ee4, Done=98236657).
echo '{"fields":[{"id":341872838,"value":"98236657"}]}' | gh api -X PATCH "users/brownm09/projectsV2/3/items/$ITEM_ID" --input - --jq .id

# Read it back; prints the option name, such as Done.
gh api "users/brownm09/projectsV2/3/items/$ITEM_ID?fields=341872838" --jq '.fields[] | select(.id == 341872838) | .value.name.raw'
```

Impact and Why use the same `PATCH` with their own field IDs; Why's `value` is the text itself. Four behaviours, each seen on 2026-11-05, that a first attempt would trip over:

- **`q` is a loose text filter, not a number lookup.** `q=1219` also returned #1212, and `q=12` returned #120 and #121, so keep the `select`.
- **Filter on `html_url`, not `repository_url`.** A pull request's `content.repository_url` is null, so a filter on it never matches a PR.
- **`q` can lag a new item.** `q=1239` returned nothing right after the add and found the item about 35 seconds later. An empty result just after an add doesn't mean the add failed; use the ID the `POST` printed.
- **The `POST` isn't idempotent.** Adding an item already on the board fails with HTTP 422, `Content already exists in this project`, which only means there's nothing to add.

This corrects [dev-env#769](https://github.com/brownm09/dev-env/issues/769), which said Projects v2 lacked a REST API; see [dev-env#1239](https://github.com/brownm09/dev-env/issues/1239).

**Why `get-project-item.sh` is cheap.** Its cache is populated automatically at creation time and backfilled wholesale by `reconcile-project-board.py`'s sweeps ([ADR-141](adr/141-project-item-id-creation-time-cache.md), dev-env#1057). A miss falls back to a query for that one item, which costs 1 GraphQL point ([dev-env#1246](https://github.com/brownm09/dev-env/issues/1246)).

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

Persists a tile's payload so a lapsed tile card (they expire after 7 days) can be re-proposed. Per
[ADR-118](adr/118-tile-persistence-shards.md), each tile is its **own** shard — one JSON object keyed by
the **paired GitHub issue number** (issue-per-tile, [ADR-094](adr/094-tile-tables-and-issue-per-tile.md),
guarantees one exists). Same numeric-filename layout as the open-PR shards above, read through the same
`_journal_shards` core (`iter_tile_shards`).

`<project>` is the tile's **target** project — the repo the work belongs in, recorded as `cwd`, *not* the spawning session's
project — so a tile filed from one repo against another lands in the target's directory and is surfaced
there. As with open-PR shards, the bare number is unique within that directory and the repo is still
carried in `url`.

Schema:

```json
{"issue":868,"url":"https://github.com/brownm09/dev-env/issues/868","title":"<chip label, <=60 chars>","tldr":"<1-2 sentence tooltip>","prompt":"<full self-contained tile prompt>","cwd":"C:/Users/brown/Git/dev-env","stub":"sessions/dev-env/YYYY-MM-DD_HHMMSS.stub.md","spawned":"YYYY-MM-DD"}
```

Seven fields are required: `issue`, `url`, `title`, `tldr`, `prompt`, `cwd`, `spawned`. The field list
lives in `TILE_REQUIRED_FIELDS` in `claude/scripts/_journal_schema.py`.

**To re-propose a shard,** call `start_session` with `initiation: "own_initiative"`, its `title` and
`prompt`, and a `background` of at least 80 characters built from `tldr`. Don't send `cwd`: a card can't
carry a folder, and it starts in the proposing session's project. When `cwd` is another repo, put that
path at the top of the prompt first, because most older prompts don't name it.

**`cwd` is written with forward slashes** — `C:/Users/brown/Git/dev-env`, never
`C:\Users\brown\Git\dev-env`. This is a correctness rule, not a style one: a forward-slash path is
valid on Windows, valid in JSON, and contains no character that any escaping layer between a command
line and `JSON.stringify`/`json.dump` will consume, so it cannot be mangled in transit. `cwd` is the
only required field that is a filesystem path, and it says *which repo the work belongs in* —
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

**When a session spawns a tile:** write the shard immediately after the tile proposal, and commit it
alongside the stub (explicit per-file pathspec, never the bare `tiles/` directory).

**Do not build this JSON with `echo` (or any other shell redirect/heredoc).** Unlike every other shard,
`prompt` holds free prose — the whole tile prompt — so string-interpolating it into
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
{"issue": <N>, "url": "<issue-url>", "title": "<chip label>", "tldr": "<tooltip>", "prompt": "<the full self-contained tile prompt, verbatim>", "cwd": "<target repo path>", "stub": "sessions/<spawning-project>/YYYY-MM-DD_HHMMSS.stub.md", "spawned": "YYYY-MM-DD"}
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
5000/hr, near-untouched, and *not* what the `gh project` commands contend for — those spend
GraphQL ([#769](https://github.com/brownm09/dev-env/issues/769); the REST alternative is in the
root `CLAUDE.md` GitHub Project section, [#1239](https://github.com/brownm09/dev-env/issues/1239)). REST-only, with no
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
> for a tile proposal (`spawn_task` in older transcripts) that never wrote a shard. A tile shard is therefore now checked at write
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

**Derived stubs are the one script-written exception to the Write-tool rule.** `/journal-compose`
Step 6.7 generates *derived stubs* — compose-internal files, not session records — to carry verified
meta triggers into the same run's meta journal. `journal-compose-meta.py stub` writes them, because
the harness refuses the coordinator's Write/Edit into the compose worktree
([dev-env#1119](https://github.com/brownm09/dev-env/issues/1119)) and the Bash alternatives would
have the coordinator author stub and manifest content by hand, which is what the verification exists
to prevent (the ADR-129 guard also blocks most such Bash writes; one quoting form is a known gap,
[dev-env#1127](https://github.com/brownm09/dev-env/issues/1127);
[ADR-129 Amendment 2](adr/129-journal-shell-write-guard.md)). A derived stub is
named `YYYY-MM-DD_2359NN.stub.md` (NN = the trigger category's row in the skill's Step 2b table),
opens with `<!-- derived-meta: … -->`, carries one H2 and an H3 per trigger (the `Source:` path, the
source session, the trigger, and the evidence quoted with `> `), ends with the placeholder
`<!-- tokens: input=0 output=0 cost≈$0 -->`, and has **neither** an opening brief **nor** a
next-session-context paragraph, so it can never displace a real stub's. Its manifest shard has the
usual five fields, with `topic` `Derived meta (<category>): <projects>`. Derived files are untracked,
never pushed, and deleted by Step 9 in the same run. Never write one by hand.

### Late meta entry recovery

For a compose that finished without the meta journal it should have written: a compose that predates
Step 6.7 and only logged its triggers, a pass whose PR body says `Meta journal: FAILED — …`, or a day
nobody composed meta for. Author the entry afterwards. This is the procedure that produced
engineering-journal [#274](https://github.com/brownm09/engineering-journal/pull/274) (the 2026-10-01
entry), following the post-compose correction pattern of
[#152](https://github.com/brownm09/engineering-journal/pull/152).

1. **Recover the source stubs.** A compose deletes its stubs from the branch it merges, so read them
   from the **pre-compose draft tip**, the last commit that still holds them. The compose PR body
   records it (`Pre-compose draft tip …`, Step 11). A compose PR from before that line existed does
   not: take the commit just before its `[docs] Add YYYY-MM-DD journal…` commit.
   ```bash
   gh pr view <compose-PR> --repo brownm09/engineering-journal --json body --jq .body
   gh pr view <compose-PR> --repo brownm09/engineering-journal --json commits --jq '.commits[] | "\(.oid) \(.messageHeadline)"'
   ```
   The commit list alone fails on the conflict path: the PR's head is then `compose/YYYY-MM-DD`, cut
   from `origin/main` with one replay commit, so the commit before it is `main`, which holds no stubs,
   and the draft branch was deleted. Only the SHA in the PR body reaches them there (the canonical
   checkout's local `draft/YYYY-MM-DD` ref usually survives the remote deletion and holds the same
   stubs). Fetch that SHA if it is not in the local object store, find the day's stubs, and read each one:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal fetch origin <pre-compose-sha>
   git -C C:/Users/brown/Git/engineering-journal ls-tree -r --name-only <pre-compose-sha> -- sessions | grep 'YYYY-MM-DD_.*stub.md'
   git -C C:/Users/brown/Git/engineering-journal show <pre-compose-sha>:sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md
   ```
   Do not add `2>/dev/null` to the `git show`: a failure there reads exactly like "nothing to
   recover" ([dev-env#602](https://github.com/brownm09/dev-env/issues/602)).
2. **Cut a disposable worktree, never touch the canonical checkout.** The branch is
   `compose/YYYY-MM-DD-late-meta`, cut from `origin/main`:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal fetch origin
   git -C C:/Users/brown/Git/engineering-journal worktree add -b compose/YYYY-MM-DD-late-meta C:/Users/brown/Git/engineering-journal/.claude/worktrees/late-meta-YYYY-MM-DD origin/main
   ```
   Confirm the new branch sits on the tip of `origin/main` (its `merge-base` with `origin/main` equals
   `rev-parse origin/main`) before editing anything. Write and Edit refuse another worktree's paths
   ([dev-env#1119](https://github.com/brownm09/dev-env/issues/1119)), so every file below is drafted or
   edited in the session's own scratch directory and moved with Bash `cp`.
3. **Author the composed meta journal**, modelled on
   `sessions/meta/2026-10-01-tooling-constraints-and-number-collisions.md`: one
   `## Session N — <trigger category>` per category, an Opening Brief linking the source projects'
   journals, a Token Usage section that says the entry has no session of its own, and all eleven
   required headings (the `chk()` block in the skill's Step 6.5). Write it to scratch, check the
   headings with that block, then `cp` it to `sessions/meta/YYYY-MM-DD-<slug>.md` in the new worktree.
4. **Update both READMEs** the same way (`cp` each out of the worktree, Edit the copy, `cp` it back):
   `sessions/meta/README.md` (a Progress Summary paragraph and an Entries row) and the `### meta`
   section of the top-level `README.md` (Recent, Open, Next).
5. **Validate before the PR**, from the worktree: `node scripts/validate-jsonl.js`, and
   `py -3 C:/Users/brown/.claude/scripts/validate-composed-output.py sessions/meta/YYYY-MM-DD-<slug>.md sessions/meta/README.md README.md`
   (the stray-terminal-output scan of the skill's Step 8b; it does not check headings).
6. **Land it**: commit with an explicit pathspec, push, open the PR, and merge exactly as the skill's
   Step 11 does (two calls, never `--delete-branch`). Write the PR body to a file with the Write tool
   and pass it with `--body-file`; it should name the compose PR, the trigger categories recovered,
   and the pre-compose draft tip the stubs were read from.
   ```bash
   WT=C:/Users/brown/Git/engineering-journal/.claude/worktrees/late-meta-YYYY-MM-DD
   git -C "$WT" add -- sessions/meta/YYYY-MM-DD-<slug>.md sessions/meta/README.md README.md
   git -C "$WT" commit -m "[docs] Add YYYY-MM-DD meta journal: <slug> (late)" -- sessions/meta/YYYY-MM-DD-<slug>.md sessions/meta/README.md README.md
   git -C "$WT" push -u origin compose/YYYY-MM-DD-late-meta
   gh pr create --repo brownm09/engineering-journal --base main --head compose/YYYY-MM-DD-late-meta --title "YYYY-MM-DD: meta journal (late)" --body-file "<pr-body-file>"
   gh pr merge <PR-URL> --repo brownm09/engineering-journal --squash
   gh pr view <PR-URL> --repo brownm09/engineering-journal --json state --jq .state   # expect MERGED
   gh api -X DELETE "repos/brownm09/engineering-journal/git/refs/heads/compose/YYYY-MM-DD-late-meta"
   git -C C:/Users/brown/Git/engineering-journal worktree remove "$WT"
   git -C C:/Users/brown/Git/engineering-journal branch -D compose/YYYY-MM-DD-late-meta
   ```
   Delete the ref only after the state reads `MERGED`. The worktree goes before the local branch,
   because a branch checked out in a worktree cannot be deleted.

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
just `git fetch origin draft/YYYY-MM-DD && git checkout draft/YYYY-MM-DD && git merge --ff-only
origin/draft/YYYY-MM-DD` (not `git pull`; see dev-env#1166). If the wrong branch specifically looks like a
hijack (detached HEAD, or a stray `claude/<slug>` branch matching no live worktree —
dev-env#630), `journal-canonical-guard.py` now auto-corrects this to `main` on the next prompt of
any session, before you'd typically reach for this manual step; see
[ADR-093](adr/093-journal-canonical-hijack-guard.md). This manual command is still the right move
for any other "wrong branch" cause, or if you need it immediately rather than waiting for the
guard's next prompt.
