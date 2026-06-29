# ADR-062 — Report / Analysis Generation as a Journal Update Trigger

**Date:** 2026-06-29
**Status:** Accepted
**Tags:** journal, stubs, reports, update-trigger, workflow, global-rule

---

## Context

The engineering journal's *Update triggers* (`claude/CLAUDE.md` → Engineering Journal)
auto-create a session stub at PR boundaries — PR opened, merged, closed, and (per
[ADR-021](021-auto-stub-on-pr-push.md)) pushed. Report- and analysis-only work was **not** a
journal boundary: a read-only `/review` session is explicitly excluded, and there was no
trigger for the common case where the user asks for an audit, investigation write-up,
comparison, or findings summary that produces no PR.

The result: substantial generated reports lived only in the chat transcript and vanished. The
2026-06-29 session that motivated this ADR produced a multi-incident silent-skip audit of the
hook system — exactly the kind of cross-session-valuable artifact the journal exists to
preserve — yet nothing in the workflow captured it.

Per the global *Durable Preferences & Memory* rule, a durable cross-session preference must
land in the version-controlled instructions, not only in agent memory. The user asked for this
behavior "now and henceforth, across all projects," so the rule belongs in the global
`claude/CLAUDE.md`, and this ADR records the rationale.

---

## Decision

Add a new **Report / analysis generated** trigger to the global `claude/CLAUDE.md` Update
triggers list:

> Whenever the user requests any report or analysis, save the full output as an artifact under
> `sessions/<project>/reports/YYYY-MM-DD-<slug>.md` and link it from the session stub (creating
> the stub if none exists). Report/analysis generation is itself a journal boundary — no PR
> required. Short analyses (≲ one screen) may be inlined in the stub instead of linked; anything
> longer must be a linked artifact so the stub stays scannable.

**Form — artifact file + stub link (not inline-only).** A standalone file under a new
`sessions/<project>/reports/` directory keeps long reports out of the stub body (preserving
stub scannability) while the stub's link pulls the artifact into the composed daily document at
day end. `/journal-compose` does not inline the artifact; it references it through the stub.

**Scope — all projects.** The trigger is global, applying to every project journal, consistent
with the other PR-boundary triggers.

The mechanical reference (path convention, compose behavior) is documented in
`docs/REFERENCE.md` → Engineering Journal Internals → *Report / analysis artifacts*.

---

## Consequences

**Positive:**
- Requested reports and analyses become durable, composable journal artifacts instead of
  ephemeral transcript output.
- Consistent with the existing trigger model: a recognizable event creates/updates a stub
  without a user prompt; `claude/CLAUDE.md` governs the behavior.
- The artifact-plus-link form keeps stubs short regardless of report length.

**Trade-off:**
- This trigger is **behavioral**, not hook-enforced — there is no PostToolUse signal for
  "a report was generated" the way there is for `gh pr create`. It relies on Claude recognizing
  the request class, the same way the "strategic decision mid-session" trigger does. A future
  enforcement mechanism is out of scope.
- Adds a new `sessions/<project>/reports/` directory to each active project's journal home.

---

## Alternatives considered

**Inline every report in the stub.** Rejected — long reports bloat the stub and the composed
daily document, defeating the stub's scannability. Inlining is retained only as an option for
short analyses.

**Only journal reports tied to a PR.** Rejected — the most valuable analyses (audits,
investigations) frequently produce no code change, which is precisely the gap this ADR closes.

**Leave it to manual `/journal-compose`-time capture.** Rejected — the report content is in the
session transcript, which compose does not read; without a stub artifact written at generation
time, the report is unrecoverable at day end.
