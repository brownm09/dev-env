# ADR-143: Gate Calibration as a Standing Pass-3 Dimension — Measured Properties, Named Provenance, Uncalibrated Means Diagnostic

**Date:** 2026-08-28
**Status:** Accepted
**Closes:** [dev-env#1074](https://github.com/brownm09/dev-env/issues/1074)
**Tags:** workflow, gates, calibration, measured-properties, thresholds, provenance, false-positives, known-bad-reference, vacuous-gate, diagnostic, pass-3, claude-md, global-rule, claude-facing, career-playbook, cover-letter-runtime, adr-011, adr-042, adr-089, adr-115, adr-116, adr-142
**Related:** [ADR-042](042-plan-risk-dimension-audit-and-observability-section.md), [ADR-089](089-privilege-restricted-test-defaults.md), [ADR-115](115-experimental-rigor-protocol.md), [ADR-116](116-single-source-worktree-recovery-recipe.md), [ADR-142](142-node-modules-truncation-gate.md)

---

## Context

Three repos independently walked back over-specified enforcement in the 2026-07-26..08-07 window, and
the biweekly retro ([dev-env#963](https://github.com/brownm09/dev-env/issues/963)) named the convergence
as an action item:

- **career-playbook** retired roughly nine fixed criteria across ADRs
  [155](https://github.com/brownm09/career-playbook/blob/main/docs/adr/155-philosophy-is-a-property-not-a-slot.md)–[159](https://github.com/brownm09/career-playbook/blob/main/docs/adr/159-fixed-criteria-become-properties.md)
  — *philosophy is a property not a slot*, *opener and close criteria not shape*, *dimension-4 quotas
  become properties*, *fixed criteria become properties* — once measurement showed the corpus bands do
  not overlap. The opener rule rejected a third of its own exemplars; gates rejected the user's own
  letters.
- **cover-letter-runtime** reached the same conclusion independently, without knowledge of the above:
  [ADR-0004, *properties over shapes in the letter-plan port*](https://github.com/brownm09/cover-letter-runtime/blob/main/docs/adr/0004-properties-over-shapes-in-the-letter-plan-port.md).
- **dev-env** shipped a parity gate later found to pass **vacuously**. [ADR-116](116-single-source-worktree-recovery-recipe.md)'s
  section scanner was fence-blind: the runbook's own `# 1. …` shell comments read as headings, the
  scanner ended the section early, and the check compared an empty code block against the recipe and
  passed. Nothing was being enforced, and nothing said so.

The shared failure is not "the gate was wrong." It is that **a threshold was chosen from intuition or
from a single example, shipped, and only then discovered to reject known-good input** — and that
nothing in the plan protocol asks the question while it is still cheap to ask.

### The nearest existing rule is scoped to the wrong object

The global `## Experimental Rigor` section and [ADR-115](115-experimental-rigor-protocol.md) already
carry the exact sentence this needs — *an uncalibrated check is a diagnostic, not verdict-bearing* —
but scoped to **experiments**: a comparative claim between arms, producing a verdict. A standing
**gate** is a different object with the same failure mode: it classifies a population of inputs
nobody enumerated, on a constant nobody sourced, and it does so on every run rather than once. ADR-115
governs the moment a *conclusion* is drawn. Nothing governed the moment a *gate ships*.

### The worked example cuts both ways, and is what sets the bar

[ADR-142](142-node-modules-truncation-gate.md) / [dev-env#1071](https://github.com/brownm09/dev-env/pull/1071)
— the `node_modules`-truncation gate, item 1 of the same retro queue — is unusually direct evidence in
**both** directions.

**For the dimension.** The obvious discriminator was npm's own install receipt,
`node_modules/.package-lock.json`. It is authoritative-looking — it is npm's own record of what it
installed — and it is wrong: measured against all 48 real trees on the machine it flagged **12/48**,
essentially all false positives (a branch that no longer has a workspace reads as truncated;
transitively-optional wasm fallbacks read as missing), at up to 2.6 s. A second candidate — treat a
*missing* receipt as "the install never completed" — fired on **16/48**, most of them healthy. Both
were rejected on their numbers. The shipped signal (a package dir that is non-empty, is not a
junction, and lacks its own `package.json`) was chosen because it measured **0 false positives across
38 known-good trees**. Only measurement separated them; plausibility ranked them the other way round.

**Against a naive version of the dimension.** That same ADR then justified its 0.50 empty-shell floor
against a "benign ceiling" of **21.3%** — which was `reverent-kowalevski-79b384`'s own
`(empty + partial) / total`. That tree is the corpus's **known-bad** reference. Calibration had
genuinely happened, against genuine known-good and known-bad references; the *provenance of one
constant* was mislabelled, sourcing a known-**good** ceiling from a known-**bad** tree. The corrected
figure is 15.0% and the margin 3.3×, not 2.3×. It was caught in review, not at calibration time, in an
ADR that cites ADR-115 by name.

**So a "calibrated: yes" checkbox would have passed ADR-142.** That is the observation this ADR is
built around: asking *whether* calibration happened is not the load-bearing question. Asking **where
each number came from** is.

## Decision

Add an eighth dimension, **Gate calibration**, to the *Plan-then-optimize → Pass 3* risk audit in
`claude/CLAUDE.md`, alongside the existing seven and under the same `N/A — <reason>` convention. It
fires when a plan **introduces or tightens an automated check that classifies inputs the plan does not
enumerate** — a hook, CI guard, validator, linter rule, LLM judge, or any threshold / ratio / count
applied to a population. The plan must state four things:

1. **The measured property** the check keys on — not the shape the author expects to see. This is the
   "properties over shapes" conclusion all three repos reached independently, stated once, globally.
2. **The calibration corpus** — known-good *and* known-bad references, with counts and the check's
   measured hit rate on each.
3. **Where each constant came from** — the corpus member *and its class*. Not "calibrated: yes."
4. **The margin** between the threshold and the worst *known-good* observation — **with that
   observation named.** Requirements 3 and 4 interlock: naming the baseline is what makes a margin
   sourced from the wrong class collide visibly with the corpus listing. Stating "3.3× over the worst
   benign tree" is unfalsifiable; stating "3.3× over `confident-mcnulty-ad4e52`, known-good" can be
   checked against the line that classified it.

And one rule about what an uncheck may do: **an uncalibrated check is a diagnostic — it may report, but
it may not block, fail, or take a destructive action.**

### Three scoping decisions, all deliberate

**"Classifies inputs the plan does not enumerate"** is what keeps a fixture-pinned unit test out of
scope. `assert f(2) == 4` is a check that blocks CI, but it has no population, no threshold, and
nothing to calibrate; the wording says so explicitly rather than leaving a reader to infer it. Without
this boundary every PR that adds a test would owe an audit line — which is precisely the checklist
fatigue [ADR-042](042-plan-risk-dimension-audit-and-observability-section.md) designed the `N/A` escape
to avoid, and it would train readers to rubber-stamp the whole list.

**"Introduces or tightens"** covers lowering a tolerance, widening a matcher, and adding a required
field to an existing check — not only net-new gates. A tightening is where an already-shipped gate
acquires a false-positive rate it did not have, and it typically arrives with no calibration discussion
at all because the gate "already exists."

**The known-bad reference does double duty.** ADR-115 requires it to prove an instrument
*discriminates*. Here it additionally proves the check is **not passing vacuously** — dev-env's own
ADR-116 failure, where a gate with no threshold at all was inert because its input had been silently
truncated to nothing. A check that fires correctly on a known-bad reference cannot be vacuous. This
costs nothing extra: it is the reference the calibration already requires, read for a second property.

### Relationship to ADR-115, stated so the two cannot drift

The calibration law is **one law with two application sites**. What is shared, and what is
experiment-only:

| | Experiments (ADR-115, `## Experimental Rigor`) | Standing gates (this ADR, Pass-3 dimension 8) |
|---|---|---|
| Calibrate against known-good **and** known-bad before the check bears weight | yes | yes |
| Uncalibrated ⇒ diagnostic, not verdict-bearing / not blocking | yes | yes |
| Provenance of each constant named to corpus member **and class** | implied by calibration, never stated | **stated explicitly** |
| Tiers (0 / 1), pre-registration freeze, adoption rider | yes | **no** |
| Incumbent-influence inventory, blinding, order-randomization, n and k | yes | **no** |
| Win bar, verdict vocabulary, decision-legality matrix, T1–T10 sweep | yes | **no** |

A gate has no arms, no baseline, and no verdict. It has a threshold, and what it owes is that
threshold's provenance. Everything ADR-115 builds around *comparing arms to reach a conclusion* is
inapplicable, and importing it would be over-ceremony of exactly the kind ADR-115's own Tier-0
reasoning warns gets skipped.

**The worksheet form stays single-sourced.** Dimension 8 points at `/experiment-audit` **Step D4**
(the instrument-calibration table: instrument / known-good ref → expected / known-bad ref → expected /
runs before scoring) rather than restating it, and gate calibration references live in the project's
existing `## Experiments` instrument registry rather than in a new section. Two cross-references make
the boundary readable from either end: the `## Experimental Rigor` section states what is
experiment-only, and the per-project `## Experiments` bullet names both dimensions as deferring to it.

### Enforcement is advisory — no hook

The gate is the CLAUDE.md rule. This follows [ADR-089](089-privilege-restricted-test-defaults.md)
(a Pass-3 dimension extended with a behavioral rule and no mechanical check) and ADR-115's own
rejection of mechanical enforcement for the *design* half: there is no reliable mechanical signature
for "this plan is about to ship a gate," and a language-sniffing hook at plan time would be
high-false-positive — which would make it, by its own rule, a diagnostic.

## Rationale

- **Provenance is the requirement that would have caught ADR-142; "was it calibrated?" is not.** The
  21.3% constant survived a real calibration exercise, real known-good and known-bad references, and
  an ADR whose §1 heading is *"the discriminator is chosen by measurement, not by plausibility."* What
  it did not survive was a reader asking which tree the number came from. Requiring the corpus member
  *and its class* is a one-clause ask that closes the specific gap the strongest available worked
  example demonstrates.
- **Measurement is what separated the candidates, and plausibility ranked them backwards.** The
  rejected `.package-lock.json` heuristic is npm's own authoritative record; the shipped signal is a
  scrappy structural property. A dimension that only asked "did you think carefully about the signal?"
  would have blessed the wrong one.
- **The failure recurred in three repos that were not talking to each other**, which is the signature
  of a missing global rule rather than three local mistakes. Per
  [ADR-038](038-durable-preferences-documented-in-repo.md) a cross-repo working-style rule belongs in
  the version-controlled instructions, delivered through dev-env's global config surface.
- **Plan time is the right moment.** After the gate ships, the cost of discovering a false-positive
  rate is paid in rejected known-good inputs and a walk-back ADR — which is what all three repos
  actually paid. Pass 3 already fires before execution and already has the `N/A` escape that keeps a
  new dimension proportional.
- **Demoting rather than blocking keeps the rule usable.** "Uncalibrated ⇒ diagnostic" gives a plan a
  legal way to ship a suspicious-but-unproven signal: report it, do not act on it, and let a confirmed
  positive promote it later. ADR-142's empty-shell and empty-tree arms are exactly this, and its
  Consequences section records the observation that would promote them — the pattern generalizes.

## Consequences

- Qualifying plans carry one more short audit line; the large majority answer
  `N/A — no gate introduced or tightened`, the same way most answer `N/A — no experiment` for
  dimension 7. Context weight added to the always-loaded `claude/CLAUDE.md` is roughly 150 words.
- **A plan proposing an uncalibrated blocking gate now has to either measure it or demote it.** That is
  the intended friction, and it is where the cost lands.
- ADR-142 becomes the standing worked example in both directions — the rejected 12/48 heuristic for
  why measurement beats plausibility, and the mislabelled 21.3% ceiling for why "calibrated: yes" is
  not the question. Its empty-shell arm is the reference example of a gate correctly held to
  advisory-only.
- **The rule is unenforced by machinery, so it can be silently skipped.** Accepted deliberately (see
  *Enforcement is advisory* above and *Alternatives* below); the honest statement is that this is a
  Claude-facing prose rule whose failure mode is omission, not misfire.
- **The provenance requirement is stated here for gates, and is currently absent from ADR-115 for
  experiment instruments** — where the failure it prevents is equally live, since ADR-142's error
  occurred inside an ADR that cites ADR-115. Deliberately not folded into ADR-115 in this change:
  amending an accepted ADR is a separate decision, and [dev-env#925](https://github.com/brownm09/dev-env/pull/925)
  already has an ADR-115 amendment in flight. Recorded here so the asymmetry is a known gap rather
  than an oversight, and tracked as a follow-up.
- The dimension count in `claude/CLAUDE.md` moves seven → eight by hand. ADR-042 ("six") and ADR-115
  ("seven") are left as historical record rather than retro-edited, which is the convention ADR-115
  established. `README.md`, `docs/REFERENCE.md`, and `stop-experiment-verdict-gate.py`'s docstring all
  refer to Experimental validity as "dimension 7"; appending as #8 keeps every one of those true.
- No new hook, script, skill, or routine, so the dev-env Documentation Maintenance table is not
  triggered and `README.md` needs no entry — the precedent ADR-042 set explicitly for a global
  CLAUDE.md rule. One clause is added to `docs/REFERENCE.md`'s `/experiment-audit` entry, because a
  reader of that entry is precisely the person likely to wonder whether it covers their gate.

## Alternatives considered

- **Extend the existing Experimental-validity dimension instead of adding an eighth.** Rejected: it
  collapses two objects that share one law and almost none of the machinery, and it makes
  `N/A — no experiment` ambiguous for a plan that ships a gate but runs no comparison — the most
  common shape this rule needs to catch. Separating them is what lets the boundary table above be
  written at all.
- **A mechanical hook that blocks a plan or PR introducing an uncalibrated gate.** Rejected on
  ADR-115's reasoning: no reliable signature for "a gate is being introduced," and the resulting
  language-sniffing check would ship uncalibrated — failing its own dimension on the first run.
- **A parity check pinning the "eight dimensions" count against the length of the numbered list.**
  Rejected, and recorded as *declined* rather than omitted, because it is the kind of thing this repo
  reflexively builds: the same retro queue's third action item flags that ~70% of dev-env's merged PRs
  in the window repaired machinery the tooling itself created. ADR-042 and ADR-115 both bumped this
  count by hand without incident, the drift is visible to any reader of the list, and a count-parity
  gate is a check whose entire justification is a failure that has not yet occurred.
- **A mandatory per-project `## Gates` section.** Rejected: the `## Experiments` instrument registry
  already exists to hold known-good/known-bad references and its wording already says
  "each judge/**check**". A fourth mandatory per-project section would force an `N/A` line into every
  project CLAUDE.md — the churn ADR-115 rejected when it made `## Experiments` optional.
- **Requiring a measured false-positive rate for every gate, with no diagnostic escape.** Rejected:
  some signals are worth surfacing before anyone has a confirmed positive to calibrate against, and an
  all-or-nothing rule would either suppress them or get ignored. The diagnostic tier is what makes the
  rule followable.

## References

- [ADR-042](042-plan-risk-dimension-audit-and-observability-section.md) — the Pass-3 audit, the
  `N/A — <reason>` escape, and the defer-to-project-section pattern this extends.
- [ADR-115](115-experimental-rigor-protocol.md) — the calibration law this generalizes from experiments
  to gates, the `/experiment-audit` Step D4 worksheet it points at rather than restates, and the
  precedent for enforcing a design-half rule at plan time with no hook.
- [ADR-089](089-privilege-restricted-test-defaults.md) — precedent for extending the Pass-3 audit with
  a behavioral rule and no mechanical enforcement.
- [ADR-142](142-node-modules-truncation-gate.md) — the two-directional worked example: the 12/48
  rejected receipt heuristic, the 0/38 shipped signal, and the 21.3%-sourced-from-a-known-bad-tree
  provenance error caught in review.
- [ADR-116](116-single-source-worktree-recovery-recipe.md) — dev-env's own vacuously-passing parity
  gate, and why a known-bad reference is also an anti-vacuity proof.
- [ADR-011](011-adr-warrant-check.md) — criterion 3 (a workflow rule other CLAUDE.md files reference),
  which is why this change warrants an ADR.
- career-playbook ADRs [155](https://github.com/brownm09/career-playbook/blob/main/docs/adr/155-philosophy-is-a-property-not-a-slot.md),
  [156](https://github.com/brownm09/career-playbook/blob/main/docs/adr/156-opener-and-close-criteria-not-shape.md),
  [157](https://github.com/brownm09/career-playbook/blob/main/docs/adr/157-dimension-4-quotas-become-properties.md),
  [158](https://github.com/brownm09/career-playbook/blob/main/docs/adr/158-fit-screen-reads-the-record.md),
  [159](https://github.com/brownm09/career-playbook/blob/main/docs/adr/159-fixed-criteria-become-properties.md)
  — the "criteria not shape" family.
- cover-letter-runtime [ADR-0004](https://github.com/brownm09/cover-letter-runtime/blob/main/docs/adr/0004-properties-over-shapes-in-the-letter-plan-port.md)
  — the same conclusion reached independently.
- dev-env#1074 (this item), dev-env#963 (the retro queue that surfaced it), dev-env#970 / #1071
  (ADR-142, the worked example and the preceding link in the same chain).
