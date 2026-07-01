# ADR-069 — Weekly Memory Audit Routine

**Date:** 2026-06-30
**Status:** Accepted
**Tags:** routines, memory, scheduled-tasks, autonomy, dedup, ADR-038, ADR-048, ADR-013

---

## Context

ADR-038 and ADR-048 provide write-time guardrails for agent memory: every durable preference
committed to a memory file must be paired with a GitHub issue and ported into the version-controlled
instructions in the same session. The write-time advisory hook (`memory-write-advisory.py`) flags
new memory writes that lack an immortalization link.

However, both guards are write-time only. The *existing* body of memory entries accumulated before
these rules were in force — and any entry where the issue was filed but the port to instructions was
never completed — sits silently in invisible memory with no backstop. Three failure classes
accumulate over time across every project:

1. **Never-ported durables** — durable (`user`/`feedback`/`project`) rules with no current
   instruction home and no open tracking issue: the forbidden state ADR-038 targets.
2. **Stale notes** — entries claiming something is still pending or unresolved long after it shipped
   or was superseded.
3. **Drift** — entries that name a specific file, function, or flag that has since moved or been
   removed.

Issue [#363](https://github.com/brownm09/dev-env/issues/363) (2026-06-30) performed the first
*manual* cross-project sweep and found all three classes. Sub-issue
[#439](https://github.com/brownm09/dev-env/issues/439) automates the recurring version.

The `/memory-audit` skill handles single-project reconciliation interactively (human-in-the-loop,
can delete). It cannot run on a schedule and does not cover multiple projects. A complementary
audit-time, cross-project, **non-destructive** backstop is needed.

## Decision

Implement the `weekly-memory-audit` routine in
`claude/routines/weekly-memory-audit/SKILL.md` with the following shape:

### Core shape

| Property | Value |
|---|---|
| Schedule | `0 9 * * 1` — Monday 09:00 **local** time, every week |
| Parity gate | **None** — runs every Monday (deliberate divergence from `biweekly-retro`) |
| Memory mutation | **Read-only** — never edits or deletes any memory file or `MEMORY.md` |
| Memory scope | `~/.claude/projects/*/memory/`, excluding `*--claude-worktrees-*` project dirs |
| Report home | `engineering-journal/sessions/meta/memory-audit/YYYY-MM-DD-audit.md` |
| Issue label | `memory-audit` |

### Per-step decisions

**Step 0 — Sync.** `sync-routine-worktree` with `REPO=engineering-journal`. Other repos are read via
`git fetch` + `git show origin/main:<path>` for instruction-home verification — no working-tree sync
needed. Memory dirs are machine-local live state, not a repo.

**Step 2 — Classification.** Parallel background `Explore` subagents (one per project, all spawned
in a single message — no synchronous preflight). `Explore` cannot Edit/Write, structurally enforcing
the read-only-on-memory guarantee. Classification subset of `/memory-audit`: for each non-`MEMORY.md`
entry, parse frontmatter (accept `type:` or `metadata.type:` — both spellings exist), detect an
immortalization link using the `memory-write-advisory.py` patterns (`#\d+`, `ADR-\d+`, `CLAUDE.md`,
"Documented in repo"), verify claimed instruction homes on `origin/main` (not the local worktree, to
avoid false gaps when the worktree is a commit behind), and assign a disposition: **remain**,
**promote**, **stale**, **drift**, **transient**, **tracked-pending**, or **index-drift**.

**Step 3 — Routing.** Project-specific durable → that project's GitHub repo. Global/cross-cutting
durable, projects with no remote, and engineering-journal (no issue tracker by convention) → dev-env.
The GitHub slug is decoded from the actual `git remote get-url origin` (not the dir name — handles
mismatches like `job-search` → `job-search-agent`).

**Step 4 — One promote issue per never-ported durable.** This is the intentional divergence from
`biweekly-retro`'s consolidated-per-repo model: ADR-048's immortalization model is per-rule, so each
never-ported durable gets its own issue that drives it into the instructions. The
project-qualified slug `<projdir>/<name>` avoids cross-project collisions in dev-env when global
rules from different projects all land there. The dedup guard reads each target repo's open
`memory-audit` issues before filing any, skipping findings whose slug already appears.

**Step 5 — Report.** Branch `memory-audit/YYYY-MM-DD` off `origin/main`, commit the report, open a
PR (no auto-merge — ADR-031). Stale, drift, index-drift, and tracked-pending findings are included in
the report as report-only; they are **never** auto-actioned (they need human judgment).

**Step 6 — Push notification** on every completion or abort path.

### Dual-copy registration caveat

The scheduler reads a **live** copy at `~/.claude/scheduled-tasks/weekly-memory-audit/SKILL.md`,
materialized by the `create_scheduled_task` MCP tool into a *separate real directory* (not a
junction) from `claude/routines/` (the version-controlled canonical). The two do not auto-sync.
Any edit to this routine must be applied to **both** copies (PR to update `claude/routines/`, then
re-register via the MCP). This follows the same pattern as `biweekly-retro` and is documented
explicitly in the canonical SKILL.md's Constraints block. See [dev-env#344](https://github.com/brownm09/dev-env/issues/344).

## Considered alternatives

**Delete-capable autonomous audit.** Allow the routine to edit or delete stale/drift entries in
addition to promoting. Rejected: an unattended cron with write access to the user's memory is
unsafe — it can destroy context that hasn't yet been acted upon. Deletion stays human-in-the-loop
via the interactive `/memory-audit` skill, which can reason about ambiguous cases.

**Consolidated promote issue per repo** (one issue = all the repo's never-ported durables). Rejected:
ADR-048's immortalization model is per-rule. A consolidated issue produces one large, hard-to-track
task instead of discrete, closable action items — the user would have to sub-track manually anyway.

**Parity gate (biweekly cadence).** Apply an even-ISO-week gate like `biweekly-retro` to make this
routine run every other Monday. Rejected: the user chose weekly cadence on 2026-06-30 as the right
balance for a memory audit (the write-time gap can accumulate quickly across sessions; catching it
weekly is proportionate).

**Thin-pointer live registration.** Register the live copy as a one-liner that reads the canonical
file from disk, instead of a full self-contained copy. Rejected: the `biweekly-retro` precedent is a
self-contained copy with a canonical-pointer note ("prefer the canonical file if present") for
resilience against connectivity or file-resolution failures at scheduled-task fire time.

## Consequences

- **Every Monday:** never-ported durables are surfaced with an auto-filed promote issue in the
  correct repo, labelled `memory-audit`, with the rule text and suggested instruction home. Stale,
  drift, and index-drift findings are reported without auto-actioning.
- **Safe by construction:** read-only on memory — no unattended writes. The dedup guard prevents the
  weekly cadence from re-filing the same gap. A bad run produces only a draft PR that the user can
  close.
- **Weekly cadence risk:** if a project's memory dir grows very large, the parallel subagents become
  many. Bounded in practice: each project's `memory/` is small (a few `.md` files), and the
  subagents do only read classification.
- **Dual-copy drift risk:** the live registered copy can get out of sync with the canonical version
  if edited without updating both. Mitigated by the explicit caveat in the SKILL.md Constraints block
  and in this ADR.
- **No automated instruction-home patching:** the routine files the issue but does not make the
  CLAUDE.md edit. That remains a human-approved action, consistent with ADR-038's intent that
  instructions are deliberate.

## References

- [dev-env#439](https://github.com/brownm09/dev-env/issues/439) — the sub-issue this ADR addresses.
- [dev-env#363](https://github.com/brownm09/dev-env/issues/363) — the parent initiative (manual sweep that motivated this automation).
- [dev-env#344](https://github.com/brownm09/dev-env/issues/344) — the dual-copy registration caveat.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — write-time durable-preference rule.
- [ADR-048](048-memory-immortalization-issue-pairing.md) — memory-write immortalization-issue pairing.
- [ADR-013](013-sync-routine-worktree-skill.md) — `sync-routine-worktree` prerequisite pattern.
- [ADR-031](031-auto-merge-disabled.md) — no auto-merge on report PRs.
- `claude/routines/weekly-memory-audit/SKILL.md` — the canonical routine definition.
- `claude/routines/biweekly-retro/SKILL.md` — the style model this routine follows.
