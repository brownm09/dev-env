# ADR-128 — Session-End Feedback-Retro Table for Sessions with Substantial Hands-On Correction

**Date:** 2026-08-04
**Status:** Accepted
**Closes:** [dev-env#942](https://github.com/brownm09/dev-env/issues/942)
**Tags:** workflow, session-boundary, retro, feedback, claude-md, claude-facing, global-rule, career-playbook, correction, adr-038, adr-046, adr-094, adr-095

**Related:** [ADR-095](095-session-boundary-summaries-and-idle-refresher.md), [ADR-038](038-durable-preferences-documented-in-repo.md), [ADR-046](046-post-merge-followup-tiles.md), [ADR-094](094-tile-tables-and-issue-per-tile.md)

---

## Context

A long career-playbook session (cover-letter drafting and audit work on PR #1031) involved a large amount of hands-on corrective feedback from the user across many small exchanges — diction and register catches, record-reconciliation judgment calls, a preference for reviewing proposed fixes rather than having them applied silently, and more. None of it was captured as a structured retrospective until the user explicitly asked, at the very end of the session: "can you tabulate the feedback I gave and how you might incorporate it into the process long-term so you can do it yourself."

The resulting 12-row table (what was said, paraphrased -> underlying pattern -> already-incorporated? -> long-term mechanism) was immediately useful: it showed that roughly 5 of 12 corrections had already been generalized into durable rules in-session (the catch -> generalize -> write-the-rule -> append-the-example loop career-playbook's own `CLAUDE.md` already documents extensively), while the other ~7 were still one-off, undocumented, or only partially addressed. The user picked one row ("corpus phrasing was available and went unused in favor of weaker fresh drafting") and had it durably fixed the same session — career-playbook#1069, [career-playbook ADR 147](https://github.com/brownm09/career-playbook/blob/main/docs/adr/147-phrasing-reuse-discovery-aid.md). The user then gave a standing instruction: "At a minimum, tabulated feedback/patterns/etc like what I requested should always be produced" — i.e. this should not require asking every time.

**Why the existing mechanisms don't already cover this:**

- **ADR-095** (Session-Boundary Summaries) governs the Completed / Context-ask / Remaining recap at every substantive stop. That is a task-status report — what got done, what's outstanding — not an analysis of corrective-feedback *patterns*. Different deliverable, different trigger condition, same section of `CLAUDE.md`.
- **`biweekly-retro`** (dev-env#343) and **`weekly-memory-audit`** (dev-env#439) both already do retrospective pattern-hunting and already draft rule diffs for review rather than applying them silently — but at a periodic (weekly / biweekly), cross-project cadence, explicitly hunting for *global / cross-cutting* signal. A narrow, single-repo, single-domain correction (e.g. "prefer the model corpus's attested diction over drafting fresh phrasing" — a career-playbook prose-voice preference) may never rise to the level of a cross-cutting biweekly item, but is exactly the kind of thing worth catching and formalizing *before* the session that surfaced it ends, while full context is still cheap to use and the person who gave the feedback is still in the room to confirm the generalization is correctly scoped.

## Decision

Add a bullet to global `claude/CLAUDE.md`'s existing **`## Session Summaries & Tile Tracking`** section, as a sibling to the ADR-095 substantive-stop-summary bullet (same section, same behavioral family, same "skip on trivial exchanges" carve-out):

> **Close a session that involved substantial hands-on correction with a feedback-retro table** ([ADR-128](128-session-end-feedback-retro-table.md)). When a session included a meaningful amount of corrective or judgment-call feedback from the user — not just task requests, but "no, not that," register/diction catches, record-reconciliation calls, process-preference corrections, a repeated theme flagged more than once — close with a table: **What was said** (paraphrased) / **Underlying pattern** / **Status** (already durable, partially addressed, or not yet formalized) / **Long-term mechanism** (how to make this durable). This is distinct from the substantive-stop summary above, which reports task status, not correction patterns — produce both when both apply, and don't let the retro table substitute for the Completed/Context/Remaining recap or vice versa. Any row landing on "not formalized" is a candidate for the existing memory + issue-pairing discipline under *Durable Preferences & Memory* above — the table is a discovery pass surfacing what to immortalize next, not a new persistence mechanism of its own, and it doesn't replace the periodic, cross-project `biweekly-retro` / `weekly-memory-audit` routines, which hunt for global signal at a different cadence and scope. Judging "substantial" is a judgment call, the same as "substantive" above — deliberately no automated skill or subagent scans for this yet (the risk of a rule-writer over-generalizing one correction into an overly broad rule, without a human confirming the generalization's scope, is real); apply this rule directly, the same way the summary bullet above already works without a dedicated skill behind it.

**Verbatim parity with `claude/CLAUDE.md`.** The blockquote above is byte-identical to the shipped bullet (self-referencing link included) — not a paraphrase or an earlier draft — so a reader of either can trust the other without cross-checking. If the `CLAUDE.md` bullet is edited later, update this blockquote in the same PR (found drifting once already, in `/review` on dev-env#943 — the first version of this ADR quoted an earlier draft of the bullet that had since gained three clauses in the shipped text).

**Deliberately not** proposing a new automated skill or subagent for this. The risk of an autonomous rule-writer over-generalizing a single correction into an overly broad rule is real and already visible in this repo's own discipline: several existing rules are careful, scoped sense-splits rather than blanket bans specifically because a blanket version would false-positive on legitimate future cases (e.g. career-playbook's `keep`/`against` word-family carve-outs, where the ban applies to one sense of the word and not others). Getting that scoping judgment right benefits from a human in the loop reviewing the proposed generalization, which is exactly what a Claude-judgment-driven table (reviewed by the user at the point it's produced) already provides, and what a fully autonomous rule-writer would remove. The rule plus Claude's own judgment — the same mechanism ADR-095's summary already relies on, with no dedicated skill behind it — is the proposed mechanism for now. Revisit with a dedicated skill (modeled on `weekly-memory-audit`'s scan-and-draft-for-review pattern) only if manual application turns out to be unreliable in practice.

## Rationale

- **Why session-end rather than continuous.** Individual corrections are already supposed to be captured continuously as `feedback`-type memories (see the auto-memory system in `claude/CLAUDE.md`). This rule is a *synthesis* pass over a session's corrections as a set — surfacing the pattern across multiple related corrections, and explicitly cross-checking which have and haven't been floated into durable instructions yet — which a stream of individual memory writes does not do on its own.
- **Why not fold into ADR-095's existing bullet.** The two deliverables have different shapes (a 4-column corrective-pattern table vs. a 3-part task-status prose recap), different trigger conditions (substantial correction occurred vs. any substantive stop), and different rationales. Keeping them as separate bullets in the same section — rather than merging into one — mirrors how this repo already keeps related-but-distinct session-boundary rules (the ADR-095 summary and its idle-refresher counterpart) as separate decisions under one section heading.
- **Why not a new ADR-095 amendment.** The "Amendment N" convention elsewhere in `claude/CLAUDE.md` is used to fix or narrow the *same* decision (a hook bug fix, a cutoff-date change) — not to add a materially new behavior with its own trigger and its own deliverable. This is closer in kind to how ADR-046 (post-merge tiles) and ADR-094 (tile tables) are two related-but-separate ADRs in the same family rather than one amending the other.
- **Why global.** Corrective feedback loops of this shape are not career-playbook-specific — any project with subjective, judgment-heavy work (prose voice, design review, code style debates) can produce the same pattern. [ADR-038](038-durable-preferences-documented-in-repo.md) already requires durable, cross-session behavioral preferences to live in the repo instructions rather than memory; this is exactly that kind of preference.
- **Why not a dedicated skill yet.** See the Decision section above — this is a deliberate, reasoned deferral, not an oversight. A skill is easy to add later (`weekly-memory-audit` is the direct template) once there's evidence manual judgment is insufficient.

## Alternatives considered

- **A dedicated `session-retro` skill, built now.** Rejected for now — no evidence yet that manual application (a rule + Claude's judgment) is unreliable, and building automation before that evidence exists risks locking in a scan heuristic that either over-fires (noise on ordinary sessions) or under-fires (missing the pattern in a different shape than whatever prompted this ADR). Revisit if this proves to be a recurring gap.
- **Fold this into ADR-095's existing summary bullet.** Rejected — different deliverable, different trigger; conflating them would make the existing bullet harder to apply correctly (an every-stop recap vs. a substantial-correction-only table).
- **Lower the `biweekly-retro` / `weekly-memory-audit` cadence instead of adding a new session-end rule.** Rejected — the value here is specifically *in-session, while the person who gave the feedback and full context are both still present*, not periodic after-the-fact review. A narrow single-repo pattern is also unlikely to clear either routine's "global / cross-cutting" bar even at a higher cadence.
- **Record the preference in agent memory only.** Rejected — contra [ADR-038](038-durable-preferences-documented-in-repo.md): invisible to the user, and not reliably consulted at the moments it matters.

## Consequences

**Positive:** corrective feedback given during a session gets a structured, reviewable synthesis before the session ends, rather than evaporating unless the user manually asks; the existing catch -> generalize -> durable-rule loop gets a forcing function instead of depending on the user remembering to invoke it.

**Negative / residual:**

- Like the ADR-095 summary, this is a behavioral convention whose *content* cannot be hook-verified — only Claude's judgment about when a session has crossed the "substantial correction" threshold, and whether the table is complete and honest. No mechanical enforcement is proposed.
- Judgment about what counts as "substantial" hands-on correction is left to Claude, deliberately, mirroring how "substantive stop" is already undefined precisely in ADR-095. A too-eager trigger produces noise on ordinary task-request sessions; a too-conservative one misses the pattern this ADR exists to catch. No numeric threshold is specified.
- Without the deferred skill, there's no mechanism to catch a session where the user gave substantial correction but Claude's own judgment fails to recognize it as such — the same residual risk ADR-095's un-enforced summary already carries.

## References

- [dev-env#942](https://github.com/brownm09/dev-env/issues/942) — this decision.
- [ADR-095](095-session-boundary-summaries-and-idle-refresher.md) — the sibling substantive-stop-summary rule this bullet sits alongside.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences belong in the repo, not memory; this ADR's core justification for going global.
- [ADR-046](046-post-merge-followup-tiles.md), [ADR-094](094-tile-tables-and-issue-per-tile.md) — the tile-capture discipline any "not formalized" row feeds into.
- dev-env#343 (`biweekly-retro`), dev-env#439 (`weekly-memory-audit`) — the periodic, cross-project retrospective mechanisms this rule deliberately does not duplicate or replace.
- Motivating session: career-playbook PR #1031 retro table; career-playbook#1069 / [career-playbook ADR 147](https://github.com/brownm09/career-playbook/blob/main/docs/adr/147-phrasing-reuse-discovery-aid.md) / [career-playbook PR #1074](https://github.com/brownm09/career-playbook/pull/1074) — the one row fixed the same session, as a worked example of the loop this ADR formalizes.
