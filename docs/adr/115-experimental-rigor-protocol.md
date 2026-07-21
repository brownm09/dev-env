# ADR-115 — Experimental Rigor Protocol: Tiered Pre-Registration and Verdict Gating for Process Experiments

**Date:** 2026-07-21
**Status:** Accepted
**Closes:** [dev-env#860](https://github.com/brownm09/dev-env/issues/860)
**Tags:** workflow, experiments, calibration, pre-registration, verdict, rigor, skills, claude-md, pass-3, hooks, stop, global-rule, career-playbook, claude-behavior, adr-042, adr-089, adr-100, adr-109, adr-114
**Related:** [ADR-042](042-plan-risk-dimension-audit-checklist.md), [ADR-089](089-privilege-restricted-test-defaults.md), [ADR-100](100-stop-journal-stub-checkpoint-hook.md), [ADR-109](109-tile-gate-deferral-question-trigger.md), [ADR-103](103-shared-hookout-emitter.md), [ADR-114](114-slim-testing-section-index.md)

---

## Context

Across repos — most often career-playbook's cover-letter workflow — process experiments are run to decide whether a change to the *process itself* is an improvement: an A/B of a new generation method vs. the incumbent, a before/after of a rewritten gate, a challenger-vs-incumbent comparison. Nothing required these experiments to be *fair* before a conclusion was drawn from them, and the failure mode is silent: a challenger can be judged "failed" when the incumbent paradigm itself shaped both the challenger's output and the yardstick used to score it.

### Motivating incident (2026-07-21, career-playbook #806 / #809 / #811)

An A/B spike of "generate-then-decide" (Arm 2) vs. the incumbent plan-first flow (Arm 1) was initially concluded a **failure**. The user corrected it; re-analysis reframed it **INCONCLUSIVE (N=1, confounded)** with four named confounds:

1. **Treatment contamination** — Arm 2 absorbed the incumbent briefing's default close template (a Construction-5 mandate-reframe), plausibly *causing* the exact bookend mismatch Arm 2 was then flagged for. The incumbent's pipeline defaults leaked into the challenger's output.
2. **Uncalibrated instrument** — the "Part 4" audit that flagged Arm 2 had never been validated against a known-good reference, so its FLAG may not discriminate at all.
3. **Processing inequality** — Arm 2's raw draft was scored against Arm 1's fully-gated final letter.
4. **No power / no pre-registration** — N = 1 input × 1 generation × 1 judge pass, with no pre-registered win bar.

Deeper still: the evaluation criteria were the incumbent's own gates *plus* a check purpose-built around Arm 1's *known* failure — criteria derived from the incumbent paradigm, not from the goal of the change. The baseline (Arm 1) was itself "the documented failure to beat," selected non-neutrally. The corrected re-test (#811) specifies an emergent close, instrument calibration on known-good/known-bad references *first*, equal processing, k≥3 generations × ≥2 job descriptions, an ensemble verdict, and a pre-registered win bar.

The user asked that this be made durable and universal: auditable at plan time ("when generating a plan or any other sort of experiment"), testing the right things, with an "appropriate amount of rigor observed in all repos." Per [ADR-038](038-durable-preferences-documented-in-repo.md) a cross-repo working-style rule of this kind belongs in the version-controlled instructions, delivered through dev-env's global config surface (the junctioned `claude/` tree).

## Decision

Ship a cross-repo experimental-rigor protocol in four coordinated artifacts.

**1. `/experiment-audit` skill** (`claude/skills/experiment-audit/SKILL.md`, globally available via the `~/.claude/skills` junction). Two modes:
- **`design`** (pre-registration, run before any results): tier determination; a 10-field pre-registration (hypothesis + arm-agnostic goal; one manipulated variable + held-constant list; goal-traced criteria; a neutral fresh-run baseline; a corpus fixed in advance; an **incumbent-influence inventory**; **instrument calibration** against known-good AND known-bad references before any arm is scored; processing parity; a blinded judging protocol; a frozen decision rule) posted to the tracking issue as tamper-evident freeze.
- **`verdict`** (run before any conclusion): Gates 0–6 — registration-freeze-timestamp check, deviation ledger, instrument quarantine, achieved-power/complete-reporting, a T1–T9 threat sweep, verdict + scope + a decision-legality matrix, and recording.

**2. Global `## Experimental Rigor` section** in `claude/CLAUDE.md` (compact; the skill is the single source of truth): the one law, the Tier 0 / Tier 1 / adoption-rider structure, and the verdict/decision vocabulary.

**3. A 7th Pass-3 risk dimension, "Experimental validity"**, appended to the Plan-then-optimize → Pass 3 audit (the count edited "six" → "seven"), so the DESIGN half is enforced *at plan time* — the user's explicit requirement. Marks `N/A — no experiment` for the ~95% of plans making no comparative claim, exactly as the other dimensions dismiss cheaply. This extends the Pass-3 mechanism the same way [ADR-089](089-privilege-restricted-test-defaults.md) extended the Security dimension.

**4. An advisory Stop-hook backstop** (`stop-experiment-verdict-gate.py`) for the VERDICT half: it blocks once (exit 2 + stderr, the only model-visible Stop channel per [ADR-103](103-shared-hookout-emitter.md)) when an assistant message states an experiment conclusion (a bounded, high-precision set of operative idioms) with no `/experiment-audit` having run and no "skip experiment audit" override. Fail-open, once-per-session sentinel, dismissable in one line — modeled on [ADR-100](100-stop-journal-stub-checkpoint-hook.md)'s journal-stub checkpoint (intent + absence-of-artifact + override + sentinel) and [ADR-109](109-tile-gate-deferral-question-trigger.md)'s bounded natural-language trigger.

**Per-project `## Experiments` sections are optional/advisory** (a corpus catalog, an instrument registry with known-good/known-bad calibration references, a results home, tier triggers). The global dimension and the skill defer to them the way the Observability dimension defers to `## Observability` ([ADR-042](042-plan-risk-dimension-audit-checklist.md)); absence is advisory. career-playbook's own section is a follow-up in that repo, seeded from #811 (Fetch/Motion model letters as known-good references, the HungerRush plan-first letter as known-bad, `calibration/<slug>/` as the results home).

## Rationale

- **The one law does the work.** *No conclusion without a design that could have produced the opposite conclusion.* Every confound in the incident is a way the design could not have produced the opposite result — a contaminated arm, an instrument that only FLAGs, a baseline defined as the loser, N=1. Naming the law once and enforcing it at two moments (design, verdict) is cheaper than enumerating rules.
- **Verdict ≠ decision.** The incident's root error was collapsing an epistemic result ("inconclusive") into an operational one ("failure → don't adopt"). The protocol keeps `{supported, refuted, inconclusive}` separate from `{adopt, reject, iterate, escalate, shelve}` and bans "failure" as a verdict word.
- **Rigor scales to stakes, not to effort.** Tier 0 (a probe) costs three lines in the tracking issue the git-workflow rule already requires, and may only conclude "signal / infeasible / shelved" — never adopt/reject. Full pre-registration is required only before an adopt/reject of a standing process. This keeps the protocol followable for a solo developer with LLM agents; over-ceremony would get skipped.
- **Plan-time + conclusion-time, defense in depth.** Pass-3 dimension 7 is the primary enforcement (it fires while the plan is being written, before any expensive generation); the CLAUDE.md law and the Stop-hook backstop catch the conclusion moment. No single mechanism is load-bearing alone.
- **The freeze costs nothing new.** Pre-registration is structured content in the tracking issue the "issue before changing files" rule already mandates; GitHub's timestamp + edit history give tamper-evidence for the peeking/HARKing threat (T8) with no hook.

## Consequences

- Plans that generate or interpret a comparative result must now address the Experimental-validity dimension (or mark it `N/A — no experiment`); the audit is a table, not a re-run, so the cost is bounded.
- A 43rd wired hook joins the Stop event; it is covered by Testing item 77 and auto-discovered by the structural gates (heartbeat/safe-exit/output-contract/wiring, items 61/62/63/68).
- The Stop hook is a natural-language heuristic and can false-positive; the once-per-session + dismissable design (the [ADR-100](100-stop-journal-stub-checkpoint-hook.md) stance) bounds the cost to a single glance. It scans assistant *text* only, so verdict wording written into a file (an ADR, a report) never trips it — which is what lets a rigor-docs PR (including this one) not flag its own prose.
- Residual limitations, named so the scope statement can carry them rather than pretend they are closed: small-sample calibration establishes discrimination-in-principle, not a measured false-positive rate; the owner remains an unblinded final judge over time; rubric outcomes are not market (callback/interview) outcomes.

## Alternatives considered

- **Mechanical hook enforcement of the *design* half** (block a plan that lacks a pre-registration) — rejected: there is no reliable mechanical signature for "a plan is about to run an experiment," and a language-sniffing gate at plan time would be high-false-positive. The design half is enforced by the Pass-3 dimension (a Claude-facing plan rule) instead; only the *verdict* half gets a hook, and only because "an assistant just stated an experiment conclusion" has a narrow, bounded idiom set.
- **Full protocol inline in `claude/CLAUDE.md`** — rejected: violates the [ADR-114](114-slim-testing-section-index.md) slimming direction. The skill is the SSOT; CLAUDE.md carries a compact pointer.
- **A third tier for high-stakes adopt/reject** — rejected: the extra rigor properly attaches to the *irreversibility of the decision*, not the size of the experiment. Replaced by the adoption rider (held-out input + tripwire + rollback), keeping the tier count at the memorable minimum.
- **Per-repo skill copies** — rejected: the `claude/skills/` junction already distributes one skill globally.
- **Mandatory per-project `## Experiments` section everywhere (N/A when absent)** — rejected in favor of optional/advisory: most repos run no experiments, and forcing an N/A line into every project CLAUDE.md is churn for no signal. The skill asks for and proposes the section on demand.

## References

- career-playbook incident: issues #806 / #809 / #811 (the confounded spike and its corrected re-test design).
- [ADR-042](042-plan-risk-dimension-audit-checklist.md) — the Pass-3 audit + per-project-section deference pattern this mirrors.
- [ADR-089](089-privilege-restricted-test-defaults.md) — precedent for extending a Pass-3 dimension with a behavioral rule and no mechanical hook.
- [ADR-100](100-stop-journal-stub-checkpoint-hook.md) — the intent + absence-of-artifact + sentinel Stop-hook pattern the verdict gate is modeled on.
- [ADR-109](109-tile-gate-deferral-question-trigger.md) — the bounded natural-language trigger + advisory-vs-blocking reasoning.
- [ADR-103](103-shared-hookout-emitter.md) — the Stop-event output contract (exit 2 + stderr is the only model-visible channel).
