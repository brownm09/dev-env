# ADR-038 — Durable Preferences Must Be Documented in the Repo, Not Only in Memory

**Date:** 2026-06-04
**Status:** Accepted
**Closes:** [dev-env#311](https://github.com/brownm09/dev-env/issues/311)
**Tags:** workflow, memory, claude-behavior, documentation, global-rule, code-quality
**Related:** [ADR-003](003-config-in-version-control.md), [ADR-008](008-plan-then-optimize-forcing-function.md)

---

## Context

Claude Code has a persistent, file-based agent memory (`~/.claude/projects/.../memory/`). When the user states a durable, cross-project preference — "fix any error you encounter, even unrelated ones" — the path of least resistance is to write a memory file and consider the request handled.

This has two failure modes the user named directly:

1. **Memory is invisible.** The user cannot read agent memory, and neither can any other person who works in these repos and processes. A standing instruction that lives only in memory means the source of truth for *how work should be done* sits somewhere no human can inspect, audit, or correct.
2. **Memory is unreliably consulted.** Recalled memories surface as background `<system-reminder>` context, not as instructions, and are skipped at inconvenient times. A rule that depends on the model choosing to honor a memory entry has soft enforcement at best.

The motivating exchange: the user gave a standing instruction (fix errors on encounter, separate PR if unrelated, discuss if scope grows >75%), it was saved only to memory, and the user pushed back — memory alone is not acceptable for durable rules because it is private and unreliable.

`claude/CLAUDE.md` is the version-controlled, human-readable source of truth, loaded every session and visible to every collaborator. It is where durable rules belong.

---

## Decision

Two additions to `claude/CLAUDE.md`:

1. **A new `## Durable Preferences & Memory` section** (placed immediately after the ADR note, before `## Platform & Environment`) stating the meta-rule: any durable user preference or workflow rule committed to memory **must also** be documented in the version-controlled repo (the appropriate `CLAUDE.md` or project docs) **or, at minimum, captured in a GitHub issue** — in the same session, with the memory entry linking back to the repo record. Memory is explicitly framed as a private cache, not the source of truth.

2. **A new `### Fix errors on encounter` subsection** under `## Code Quality` capturing the specific standing instruction that motivated this ADR: fix any error encountered in any project including unrelated ones; truly unrelated fixes go in a separate PR (following normal issue/test process); if an unrelated fix would grow scope by more than ~75%, stop and discuss; this comes up most often in lifting-logbook.

Both sections link to this ADR.

---

## Rationale

**Why a written rule rather than a memory entry about memory.** The whole point of the user's feedback is that memory is not a reliable or visible home for durable rules. Encoding "document durable rules in the repo" *as a memory entry* would reproduce the exact failure mode being fixed. The meta-rule must live in the repo to be self-consistent.

**Why "or at minimum a GitHub issue."** Not every preference has an obvious documentation home, and some surface mid-task when a full CLAUDE.md edit would derail the work. An issue is a low-friction, visible, durable fallback that keeps the commitment out of the private-memory-only state while deferring the proper write-up.

**Why keep the memory entry at all.** Memory still earns its place for recall ergonomics — it surfaces the preference proactively in future sessions. The rule does not abolish memory; it forbids memory being the *only* record. The memory entry links back to the repo so the two stay reconciled.

**Why place the meta-rule near the top.** It governs how the configuration system itself is maintained, so it belongs with the framing material (ADR note, platform) rather than buried among domain-specific rules.

**Why place the fix-errors rule under Code Quality.** It is a code-health practice — don't leave broken things broken — adjacent to the suppression and test-integrity policies that also govern how errors are handled in a change.

---

## Alternatives considered

- **Memory only (status quo).** Rejected by the user: invisible to humans, unreliably consulted.
- **Issue only, no CLAUDE.md edit.** Rejected: an issue is the *minimum* fallback, not the preferred home for a rule that should be loaded every session. A standing behavioral rule belongs in CLAUDE.md where it is read on every prompt.
- **One combined section.** Rejected: the meta-rule (how to record preferences) and the fix-errors rule (a specific preference) operate at different altitudes. Separating them lets each be found and cited independently; the meta-rule is general infrastructure, the fix-errors rule is one instance produced by it.
- **A hook that blocks on memory writes lacking a repo counterpart.** Rejected as over-engineering for now: detecting "is this preference also in the repo" reliably is hard, and the judgment of what counts as *durable* is exactly the kind of call a behavioral rule handles better than a grep.

---

## Consequences

**Positive:**
- Durable preferences become visible to the user and every collaborator, auditable in git history, and loaded every session rather than depending on memory recall.
- The meta-rule generalizes to every future preference, not just this one.
- The specific fix-errors instruction is now enforceable by reference in reviews and by the author.

**Negative:**
- Adds ~20 lines to global CLAUDE.md, read on every session.
- Slightly more process per stated preference (memory + repo edit/issue in the same session) — mitigated by the issue-as-minimum fallback.
- Enforcement is behavioral, not mechanical; compliance depends on session-by-session attention, like other CLAUDE.md rules.

---

## References

- [dev-env#311](https://github.com/brownm09/dev-env/issues/311) — issue tracking this change.
- [ADR-003](003-config-in-version-control.md) — config artifacts in version control; establishes the repo (not local/private state) as the source of truth for configuration.
- [Anthropic — Claude Code memory and CLAUDE.md](https://docs.anthropic.com/en/docs/claude-code/memory) — primary source for how `CLAUDE.md` is loaded into every session, which is why it is the right home for durable rules.
