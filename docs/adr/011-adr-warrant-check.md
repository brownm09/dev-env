# ADR 011 — ADR-Warrant Check at Plan, PR-Open, and PR-Merge Checkpoints

**Date:** 2026-05-03
**Status:** Accepted

---

## Context

ADRs in `docs/adr/` exist to capture *why* a non-obvious decision was made, so a future change cannot innocently reverse it. The practice has been ad-hoc: an ADR is written when I happen to remember to write one. Several decisions over the last quarter (per-session stub files, journal-compose isolation, `core.hooksPath` global wiring) were captured well; others were not, and the rationale is now reconstructable only via `git log` archaeology — which works for recent decisions and degrades fast.

The trigger for codifying this: while addressing review findings on [career-playbook#89](https://github.com/brownm09/career-playbook/pull/89), the multi-artifact writing-system decisions (universal/specific style split, per-artifact skills, project-level skill location, regression-detection requirement) had no ADR until the user explicitly prompted. Three of the four would have been hard to recover from `git log` alone — the *what* was in the diff, but not the alternatives considered or why they were rejected.

The risk is asymmetric. A missing ADR costs nothing today and a real amount of future-Mike's (or Claude's) time when the next refactor reverses an unfamiliar-but-deliberate choice. ADR effort is small (10–20 minutes); writing one against the wrong checkpoint risks either bloat (every commit gets one) or omission (only "big" changes get one, where "big" is subjective).

---

## Decision

Add an explicit "ADR-warrant check" rule to global `claude/CLAUDE.md` `## Git Workflow`. The rule fires at three checkpoints:

1. **Immediately after a plan is approved** (post-`ExitPlanMode`). Catches the most common case: a structural decision is made *during planning*, before any code lands. Writing the ADR with the plan still in context costs almost nothing.
2. **Immediately after `gh pr create` returns.** Catches decisions that emerged during implementation that the plan did not anticipate.
3. **Immediately before `gh pr merge`.** Last-mile catch for cases where review feedback shifted the decision (e.g., review surfacing an alternative the original change rejected for reasons worth recording).

The criteria for "warrants an ADR" are written to be auditable, not exhaustive:

- Changes a rule, hook, skill, or settings value documented in `claude/`.
- Introduces or restructures a directory under `claude/`.
- Establishes or changes a workflow rule that other CLAUDE.md files reference.
- Rationale would be hard to recover from `git log` alone six months later.

ADR placement follows scope: global rules → dev-env `docs/adr/`; project-specific decisions → that project's `docs/adr/`. Existing project ADR practice (career-playbook just added `docs/adr/`) is the precedent.

The rule is enforcement-style ("never merge a qualifying change without an ADR record") rather than advisory because advisory rules in CLAUDE.md silently degrade — the ones reliably followed are the ones with stop-the-merge teeth.

---

## Consequences

**Positive.**

- Decisions get recorded at the cheapest possible moment — when the rationale is still in context. The 10–20 minutes spent writing an ADR at plan-approval time is recovered the first time a future change is paused to read it.
- Three checkpoints span the change lifecycle, so a decision that crystallizes mid-implementation isn't lost.
- Lookup cost is reduced by scanning `docs/adr/INDEX.md` tags first — individual ADR files are opened only on a tag match, reducing the common "not warranted" case to a single index read.
- The criteria are concrete enough to evaluate against (4 clauses), avoiding the "what counts as architectural" debate.
- Project-level ADRs (per the precedent set in career-playbook#89) keep project-specific decisions out of dev-env's catalog while still benefiting from the same checkpoint discipline.

**Negative.**

- Risk of ADR bloat if criteria 4 ("hard to recover from `git log`") is interpreted loosely. Mitigation: the other three criteria are concrete; criterion 4 is a backstop, not the primary trigger.
- Three checkpoints means three opportunities to remember — but also three opportunities to forget. Mitigation: the checkpoint rule is colocated with `gh pr create` and `gh pr merge` rules in `## Git Workflow`, so it's read alongside the operations that trigger it.
- Adds friction to small structural changes that don't actually need an ADR. Mitigation: criterion 1 ("any rule/hook/skill/settings change") combined with criterion 2 ("directory restructure") rule out routine bug fixes and feature additions inside an existing structure; the rule should not fire on most PRs.

---

## Alternatives Considered

**Annual ADR audit.** Periodically review `git log` against `docs/adr/` and write ADRs for missing decisions. Rejected: by the time the audit runs, the rationale is gone — the ADR becomes a guess about what was probably thought.

**ADR per PR (mandatory).** Every PR gets an ADR or an "ADR not warranted" comment. Rejected: bloat. Most PRs are routine and an explicit "no ADR" record is more noise than signal.

**Single checkpoint at PR open.** Drop the plan-time and merge-time checks. Rejected: misses two real cases — decisions made in planning that never get implemented in this PR (so plan-time capture is the only chance), and decisions that shift during review (so merge-time is the last chance to capture the final form).

**Soft suggestion only.** Phrase the rule as "consider whether..." rather than "evaluate at three checkpoints." Rejected: the existing practice was already a soft suggestion and produced the gap this rule addresses.

---

## Verification

- The rule appears in `claude/CLAUDE.md` `## Git Workflow` as a bullet between the existing "Test before PR" and "Never commit directly to `main`" bullets.
- Project-level counterpart already landed in [career-playbook CLAUDE.md `## Testing`](https://github.com/brownm09/career-playbook/blob/main/CLAUDE.md) via PR #89.
- Tracked in [dev-env#167](https://github.com/brownm09/dev-env/issues/167).

---

## Amendment 1 (2026-08-28, [dev-env#1079](https://github.com/brownm09/dev-env/issues/1079)) — the four criteria are triggers, not a granularity rule

The *Negative* section above anticipated "ADR bloat if criterion 4 is interpreted loosely" and
mitigated it by leaning on the concreteness of criteria 1–3. That mitigation addressed the wrong
axis. The observed bloat did not come from criterion 4 firing loosely; it came from criteria 1–3
firing **correctly** and there being no rule for *where the rationale goes* once they do.

The four criteria answer **"does this change need a written rationale?"** They never answered
**"which file does it belong in?"**, so every qualifying change defaulted to a new ADR file. Over
the 2026-07-11 … 2026-08-08 retro window that produced 28 ADRs across ~92 merged PRs — one per
~3.3 PRs — including **five on 2026-07-22** (by merge date; by their own `Date:` fields, two), and
several governing one-line rules. [dev-env#963](https://github.com/brownm09/dev-env/issues/963)
flagged the aggregate; [ADR-145](145-scope-discipline-self-referential-churn.md) §1 decided it.

**Once a change is warranted, choose the smallest home that preserves the reasoning:**

| Home | When |
|---|---|
| **A new ADR** | The change makes a decision that could later be *reversed on its own*: a new invariant, a mechanism whose alternatives were weighed, a rule other CLAUDE.md files will cite. |
| **An amendment to an existing ADR** | The change refines, corrects, extends, or bounds a decision **already recorded**. If the honest opening sentence is "as ADR-N says, except…", it is an amendment. A follow-up fix to machinery an ADR introduced is *always* an amendment; so is a newly discovered failure mode of a decision already taken. |
| **A CLAUDE.md line, no ADR** | The reasoning is fully contained in the rule as written, and `git log` plus the issue recover everything a reader needs. A one-line rule with no rejected alternative and no invariant to protect does not need a file. |

**Tie-break — if you cannot name at least one *alternative you rejected*, it is not a new ADR.**
This is the operative half: it is checkable in seconds, and it tests against a structure every ADR
already has (the *Alternatives Considered* section this one carries). An ADR whose alternatives
section would have to be invented is a rule, not a decision.

A numeric cap ("at most N ADRs per week") was rejected: it is an uncalibrated threshold applied to a
population — the defect [ADR-144](144-gate-calibration-pass-3-dimension.md)'s Pass-3 dimension 8
exists to catch — and it penalizes a genuinely dense day of real decisions identically to a day of
over-splitting.

The rule is stated in `claude/CLAUDE.md`'s ADR-warrant bullet, so it is read at the moment the
question arises. It is deliberately **not** mechanically enforced: a gate enforcing restraint about
gates would be self-refuting, and ADR-145 §3's audit found dev-env's existing gates largely healthy
without another one watching them.
