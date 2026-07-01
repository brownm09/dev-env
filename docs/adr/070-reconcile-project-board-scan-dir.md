# ADR 070 — Generalize reconcile-project-board to Multi-Repo via --scan-dir

**Date:** 2026-07-01
**Status:** Accepted
**Tags:** routines, github-project, hook-config, reconciliation, multi-repo, scan-dir, automation

---

## Context

ADR-068 shipped `reconcile-project-board.py` as a single-repo engine: it reads
`.claude/hook-config.json` from one repo root (default: the canonical checkout of whichever
repo the script lives in) and reconciles that repo's board alone. The nightly
`reconcile-project-board` routine only ever reconciled dev-env's own board (#3).

ADR-068's own "Alternatives Considered" section named this limitation and deferred it
explicitly:

> **Multi-repo scan now (`--scan-dir`, like prune/reclaim).** The same inert-hook gap affects
> every project board, not just dev-env. Deferred to a follow-up (#447) to keep this PR
> scoped to the board the brief names; the pure helpers are written to generalize cleanly.

[dev-env#462](https://github.com/brownm09/dev-env/issues/462) (follow-up to #447) is that
generalization. Two other dev-env scripts — `prune-merged-worktrees.py` and
`reclaim-worktree-disk.py` — already ship a `--repo-path` (single repo) / `--scan-dir`
(directory of repos) split, and their routines already invoke `--scan-dir C:/Users/brown/Git`.
This ADR mirrors that shape for `reconcile-project-board.py`.

---

## Decision

**Scope: any repo with a usable `.claude/hook-config.json`, not just `required_fields`
adopters.** Today only dev-env's config uses the `required_fields` schema (ADR-023);
lifting-logbook's config has `repo`/`project_number`/`project_owner` but still uses the
legacy `epic_field_id`/`milestones` keys with no `required_fields` array. The single-repo
engine already tolerates an absent/empty `required_fields` gracefully — orphans still get
added, the missing-field report is simply empty — so this is pre-existing behavior, not new
risk. `--scan-dir` reconciles any repo whose config carries `repo` + `project_number` +
`project_owner`, matching the literal discovery criterion ("every git repo ... with a
`.claude/hook-config.json` present") rather than narrowing to `required_fields` adopters
only. Repos with no config, or an incomplete one, are skipped — never fatal to the scan. This
was confirmed with the user explicitly during scoping, given the consequence (lifting-logbook
would get a first-run orphan-add batch) is user-visible: see Consequences.

**`find_git_repos(scan_dir)`** — a third, deliberately duplicated copy of the same
directory-scan helper already in `prune-merged-worktrees.py` and `reclaim-worktree-disk.py`
(primary repos = has a `.git` *directory*; worktrees, whose `.git` is a file, are excluded
automatically). Not extracted into a shared module in this PR — the brief asked to mirror the
existing pattern, and a cross-script shared-module refactor is a separable, lower-urgency
change (tracked as a follow-up tile rather than bundled here).

**`_reconcile_repo(config, dry_run)`** — the fetch/compute-orphans/add/report body factored
out of `main()`, reused by both single-repo and scan-dir paths, so there is exactly one
implementation of "how to reconcile one repo once its config is known to be valid." It never
raises; failures come back as a `{"status": ...}` dict so each caller can apply its own
fatal-vs-skip policy. `_validated_config(repo_root)` similarly factors out config loading and
validation, shared so single-repo and scan-dir modes can never validate a config differently.

**Fatal-vs-skip diverges by mode, on purpose:**
- **Single-repo mode is unchanged.** A missing/invalid config or a `gh` failure is still an
  operational failure (exit 1) — this is the routine's primary nightly invocation shape
  against one repo's own board, so a broken config there is a real problem worth a non-zero
  exit, exactly as ADR-068 designed it. Every stderr message single-repo mode can print is
  byte-for-byte identical to pre-this-PR output.
- **Scan-dir mode treats "no config" as expected, not an error** — most repos under
  `C:/Users/brown/Git` will never adopt board tracking, so silently skipping them (counted in
  `repos_skipped`, no per-repo noise) keeps the output focused on repos that opted in.
- **A `gh project`-scope error fails the whole scan immediately** (exit 1), rather than being
  isolated per repo. The `project` scope is a property of the authenticated token, not the
  repo — every remaining repo would fail identically, so continuing the loop would just
  repeat the same error N times for no benefit. This preserves the routine's existing "prints
  the `gh auth refresh -s project` hint and exits 1 → push-notify and stop" contract
  unchanged, even in scan-dir mode.
- **Any other `gh` failure (network blip, deleted repo, access change) is isolated to that
  repo** — logged, counted in `repos_failed`, and the scan continues. One repo's transient
  failure must not blank out the results for every other repo in the same nightly run (the
  general resilience pattern already used by `biweekly-retro`: a single project's failure
  degrades to a partial report, not an aborted run).

**Aggregate result line.** Scan-dir mode still ends in a `RESULT:` line (matching
single-repo mode's existing machine-readable contract), but as a pure, unit-tested
`render_scan_summary()` function — mirroring how the per-repo report's no-guessing contract
is pinned by the existing, tested `render_report()`. Per-repo reports each print their own
`RESULT:` line first; the aggregate prints last, so "read the final `RESULT:` line" (the
routine's existing instruction) is still literally correct in both modes — in scan-dir mode,
the *final* one is now the aggregate:

```
RESULT: repos_scanned=N repos_skipped=K repos_failed=F orphans_added=M add_failed=J needs_attention=L dry_run=<bool>
```

**Routine update.** `claude/routines/reconcile-project-board/SKILL.md` Step 1 now invokes
`--scan-dir C:/Users/brown/Git`; Step 3 reads the aggregate line and additionally
push-notifies when `repos_failed > 0` (previously only `needs_attention` and `add_failed`
triggered a notification).

---

## Alternatives Considered

**Gate scan-dir on `required_fields` presence, excluding lifting-logbook.** Would have kept
the nightly routine's blast radius unchanged (dev-env only, until other repos explicitly
migrate to `required_fields`). Rejected per explicit user direction during scoping: the
discovery criterion is `.claude/hook-config.json` presence, not `required_fields` presence
specifically — add-only + report-only is inherently safe/reversible (ADR-068's core
property), and the single-repo engine already behaves this way today if pointed at
lifting-logbook directly; `--scan-dir` just automates what was already possible by hand.

**Abort the whole scan on any `gh` failure, not just scope errors.** Simpler, but wrong: a
single repo being temporarily unreachable (deleted, renamed, rate-limited) would blank the
report for every other configured repo in the same run. Per-repo isolation for non-scope
errors was chosen instead.

**Extract `find_git_repos` into a shared `_repo_scan.py` module used by all three scripts.**
Consistent with this codebase's existing shared-module convention (`_worktree_liveness.py`,
`_worktree_topology.py`, `_hookio.py`, `_journal_shards.py`, `_hookutil.py`), and arguably the
"right" long-term shape now that a third copy exists. Deferred: the brief asked to mirror the
existing (duplicated) pattern, and refactoring two already-shipped, unrelated scripts is a
separable change with its own review surface. Flagged as a post-merge follow-up tile rather
than bundled into this PR.

---

## Consequences

- The nightly `reconcile-project-board` routine now reconciles every `C:/Users/brown/Git`
  repo with a valid `.claude/hook-config.json` (today: dev-env and lifting-logbook), not just
  dev-env — closing the gap ADR-068 deferred (#447).
- Verified via `--dry-run --scan-dir C:/Users/brown/Git` while authoring this ADR: 29 repos
  found, 27 skipped (no config), 2 reconciled (dev-env, lifting-logbook). lifting-logbook
  currently has zero orphans and zero missing-field gaps — no first-run surprise batch — but
  the mechanism means any repo that *does* have orphans gets a one-time, add-only, reversible
  catch-up the first time it's scanned, not a recurring surprise.
- A single repo's transient `gh` failure no longer blanks the nightly report for every other
  repo; a token-level scope failure still stops the scan immediately (unchanged cost: one
  failure, one hint, self-evident re-run).
- `find_git_repos` now exists in three near-identical copies across `claude/scripts/`.
  Tracked as a candidate for a shared-module extraction, not done here.
- `docs/REFERENCE.md`, `README.md`, and the `reconcile-project-board` routine are updated in
  the same PR per the doc-reconciliation checkpoint.
