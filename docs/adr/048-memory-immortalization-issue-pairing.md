# ADR-048 — Memory Writes Must Be Paired with an Immortalization Issue

**Date:** 2026-06-20
**Status:** Accepted
**Closes:** [dev-env#373](https://github.com/brownm09/dev-env/issues/373)
**Tags:** workflow, memory, claude-behavior, documentation, global-rule, hooks, skill
**Extends:** [ADR-038](038-durable-preferences-documented-in-repo.md)
**Related:** [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md), [ADR-024](024-worktree-path-guard-hook.md), [ADR-039](039-merge-gate-findings-enforcement.md)

---

## Context

[ADR-038](038-durable-preferences-documented-in-repo.md) established the rule that a durable preference committed to agent memory must *also* be documented in the repo "**or, at minimum, captured in a GitHub issue.**" In practice that "minimum" became a terminal state: an issue is filed (or nothing is) and the durable rule still rots in memory-only form. The user named the failure directly — "a lot of things in memory are often ignored or forgotten when needed" — and asked that no durable memory be written without *also* committing it to an issue that immortalizes the memory **into the instructions**.

A one-time audit of the current memory store (2026-06-20) confirmed three rot modes ADR-038's behavioral rule did not prevent:

1. **Stale notes.** `project_open_prs.md` cited PRs from 2026-05-10, all long merged; its `MEMORY.md` index line was *itself* inconsistent with the file it indexed.
2. **Resolved-but-lingering research.** `project_usage_alerting.md` had shipped as the post-merge usage snapshot, but the memo lingered with an unbuilt "next step."
3. **Index drift.** `feedback_post_merge_followup_tiles.md` was **missing from the `MEMORY.md` index** even though the rule itself was correctly ported (ADR-046).

The instructions (`CLAUDE.md` + project docs) are loaded every session and visible to every collaborator; memory is neither. The fix is to make the *path from memory to instructions* mandatory and observable, not optional.

This ADR deliberately revisits ADR-038's rejected alternative — "a hook that blocks on memory writes lacking a repo counterpart." ADR-038 rejected it because reliably auto-detecting whether a preference is *durable* (vs. session-local) is a behavioral judgment, not a grep. That objection holds for a **blocking** hook. It does **not** hold for a **non-blocking advisory** that fires only on a cheap, false-positive-tolerant signal and leaves the durability judgment to the agent.

## Decision

Three coordinated changes, extending — not superseding — ADR-038.

### 1. Strengthen `claude/CLAUDE.md` § Durable Preferences & Memory

A durable (`user`/`feedback`/`project`) memory write must, **in the same session**, be paired with a GitHub issue whose explicit job is to **immortalize it into the instructions** (the appropriate `CLAUDE.md` or project docs), linked from **both** the memory body and its `MEMORY.md` pointer. Filing the issue is the floor, not the finish — it exists to drive the rule into the instructions, not to substitute for doing so; prefer to make the instruction edit immediately and close the issue same-session. Transient/session-local context (open-PR lists, in-flight state) stays exempt.

### 2. Advisory write-time hook — `memory-write-advisory.py` (PostToolUse, `Write`)

A new hook, wired on a `PostToolUse` `Write` matcher, fires **only** when the pure `should_advise_memory_write(tool_name, file_path, content)` predicate holds: the tool is `Write`, the path is a `.md` file inside a `…/memory/` directory other than the `MEMORY.md` index, **and** the written body carries **no** immortalization link — no issue/PR ref (`#\d+`), no `ADR-\d+`, no `CLAUDE.md`, no "Documented in repo". On a match it emits a one-line reminder on **stderr** and exits **2** so Claude sees it and acts; every other case exits **0** silently. The tool has already run, so exit 2 here *surfaces* the reminder — it does not block the write (the same `PostToolUse`-exit-2 convention `post-tool-use.py` and `pr-merge-reminder.py` already use). The hook spawns no subprocess and fails open (top-level guard → exit 0).

The **link-absence heuristic** is what makes the advisory variant defensible where ADR-038 rejected a blocking one: the hook never decides whether a memory is durable — it only nudges when *no link of any kind* is present, and stays silent the moment one is. A transient note that genuinely needs no issue simply gets a one-line reminder it can ignore; a durable rule that already cites its issue/ADR is never nagged.

### 3. `/memory-audit` skill — `claude/skills/memory-audit/SKILL.md`

A report-first skill that reconciles the active project's memory against the repo and emits a table (per entry: type, durable?, instruction home?, disposition — `remain-as-cache` / `promote-to-instructions` / `delete-stale`), covering the three rot modes above (never-ported durables, stale notes, drift). It verifies that any instruction home a memory *claims* actually exists on current `origin/main` before trusting it — the audit that motivated this ADR initially produced a false "drift" finding from a stale worktree base, so freshness verification is built into the skill. This makes the one-time reconciliation repeatable and is the audit-time complement to the write-time rule ([dev-env#363](https://github.com/brownm09/dev-env/issues/363)).

## Rationale

- **Why mandatory issue, not "or at minimum an issue."** The optional framing is exactly the loophole that let durable rules sit in memory. Re-framing the issue as a *tracker toward the instructions* (not a terminal record) closes it without abolishing the low-friction fallback for mid-task captures.
- **Why advisory, not blocking.** ADR-038's objection (can't auto-detect durability) is real for a gate but not for a nudge. The link-absence signal is intentionally permissive: it tolerates false positives (a transient note gets a harmless reminder) and never produces a false *negative* that blocks a legitimate write.
- **Why exit 2.** For `PostToolUse`, the tool has already executed; exit 2 feeds stderr back to Claude as actionable feedback without undoing anything — the established convention in this repo. Exit 0 would route the text to the transcript only, where the model is less likely to act ([ADR-027](027-userpromptsubmit-blocking-hook-conventions.md)).
- **Why a skill for the audit.** The write-time rule prevents *new* memory-only rules; it does nothing about the backlog or about drift that accrues as the repo moves. A repeatable reconciliation pass is the only thing that catches stale notes and index drift after the fact.

## Alternatives considered

- **Keep ADR-038 as-is.** Rejected — the optional-issue loophole is the reported problem.
- **A blocking hook on memory writes.** Rejected for the same reason ADR-038 gave (durability is a judgment), and because blocking a machine-local cache write is hostile to legitimate transient notes.
- **A hook that greps the repo / GitHub to confirm the issue exists.** Rejected — a network/`gh` round-trip on *every* `Write` is too costly and failure-prone for a global matcher; the link-presence proxy is good enough and fails safe.
- **Skill only, no hook.** Rejected — an on-demand audit never catches the write *at the moment it happens*, which is the "forgotten in the moment" failure the user named.

## Consequences

**Positive:**
- Durable rules reliably reach the instructions everyone reads; the memory→issue→instructions path is now both required and observable.
- The nudge is low-noise (gated on link-absence) and fail-safe (never blocks a write).
- `/memory-audit` makes reconciliation repeatable, catching the stale-note and index-drift modes the write-time rule cannot.

**Negative:**
- A `PostToolUse` `Write` matcher now runs `memory-write-advisory.py` on **every** `Write` globally. The cost is a single JSON parse + string check that exits 0 immediately for non-memory paths, and it fails open — but it is one more per-write hook.
- Adds an ADR, a hook + test, a skill, and ~6 lines to the global `CLAUDE.md`. Enforcement of the *issue-pairing* itself remains behavioral; the hook only reminds.

## References

- [ADR-038](038-durable-preferences-documented-in-repo.md) — the durable-preferences rule this extends, including the rejected blocking-hook alternative revisited here.
- [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md) — hook exit-code / safe-exit conventions (`PostToolUse` exit 2 surfaces stderr to Claude; advisory hooks fail open).
- [dev-env#373](https://github.com/brownm09/dev-env/issues/373) — issue tracking this change; [dev-env#363](https://github.com/brownm09/dev-env/issues/363) — the audit-time reconciliation direction the `/memory-audit` skill realizes.
- [Anthropic — Claude Code hooks reference](https://docs.anthropic.com/en/docs/claude-code/hooks) — primary source for the `PostToolUse` event payload and exit-code semantics the hook relies on.
- [Anthropic — Claude Code memory](https://docs.anthropic.com/en/docs/claude-code/memory) — primary source for why `CLAUDE.md` is loaded every session, which is why it is the right home for durable rules.

## Amendment (2026-07-09) — Search existing issues before filing the immortalization issue (dev-env#687)

The original decision paired a durable memory write with a GitHub issue that immortalizes it, but
said nothing about checking whether that content was already tracked. In practice this let the
identical gap get documented twice: [dev-env#610](https://github.com/brownm09/dev-env/issues/610)
and [dev-env#627](https://github.com/brownm09/dev-env/issues/627) both independently described the
same `EnterWorktree` cross-repo-targeting bug, filed the same day from two different incidents,
neither referencing the other. The overlap surfaced only when the session implementing #627 went to
add a `claude/CLAUDE.md` bullet for the gap and found #610's bullet already there — recovered by
extending it in place, but only after burning investigation time a one-line search would have saved.

**Fix:** § "Durable Preferences & Memory" now requires a search of **both open and closed** issues
(`gh issue list --search "<keywords>" --state all`) before filing the immortalization issue. A match
means the rule is already tracked: reference that issue from the memory body instead of filing a
fresh one, extending it with a comment if it's still open and missing detail this occurrence adds.
Filing a new issue is now the fallback for a confirmed miss, not the default first move.

**Why this doesn't need its own hook.** The link-absence heuristic behind `memory-write-advisory.py`
(Decision § 2) already tolerates false positives by design — it fires on *any* durable memory write
lacking a link, regardless of whether a duplicate exists upstream, and a network/`gh` round-trip on
every `Write` was already rejected in Alternatives considered above for cost and reliability reasons.
Detecting a duplicate *before* filing is a one-time judgment call (matching search results against
the memory's actual content) at the moment the agent is about to file — not a mechanical property of
the write itself — so it stays a documented step the agent follows rather than a new gate.
