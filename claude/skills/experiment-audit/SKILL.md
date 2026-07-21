---
name: experiment-audit
description: Rigor protocol for process experiments — design mode pre-registers (tier, hypothesis + primary outcome construct, contamination inventory, instrument calibration, frozen win bar) before any results exist; verdict mode gates the conclusion (deviation ledger, threat sweep T1–T10, verdict read off the primary construct, decision legality). Invoke as /experiment-audit design <description> | verdict <results-path, issue #N, or description>.
argument-hint: "design <experiment description> | verdict <results path | issue #N | description>"
allowed-tools: Read Grep Glob Bash AskUserQuestion Write Edit
---

# Experiment Audit

Enforces experimental rigor for any **process experiment** — a comparative claim about a
process change (A/B arms, before/after, challenger vs. incumbent). Two modes:

- **`design`** — run *before generating any results*. Produces a pre-registration that is
  frozen in the tracking issue.
- **`verdict`** — run *before stating any conclusion*. Gates the conclusion through Gates 0–6.

**The one law:** *no conclusion without a design that could have produced the opposite
conclusion.* An unfair or unregistered experiment cannot yield adopt/reject — only a hypothesis
for a proper run. See [ADR-115](../../../docs/adr/115-experimental-rigor-protocol.md) and the
global `## Experimental Rigor` section.

**Vocabulary — keep these two layers separate (collapsing them was the motivating incident):**
- **Verdict** (epistemic): `supported` | `refuted` | `inconclusive — confounded by <X>`. "Failure"
  is banned as a verdict word.
- **Decision** (operational): `adopt` | `reject` | `iterate` | `escalate` | `shelve — untested`.

**Every block you emit in either mode MUST begin with a literal `[experiment-audit]` marker line**
— it is the signal `stop-experiment-verdict-gate.py` reads to confirm the audit ran.

---

## Step 0 — Parse mode and load the project's experiment config

Parse `$ARGUMENTS`: the first token is the mode (`design` or `verdict`); the rest is the
experiment description, a results path (e.g. `calibration/<slug>/`), or an issue ref (`#N`). If the
mode is missing or unrecognized, ask with `AskUserQuestion` (options: *design* / *verdict*).

Load the project's `## Experiments` section from its CLAUDE.md (corpus catalog, instrument registry
with known-good/known-bad calibration references, results home, tier triggers):

```bash
grep -n -A40 '^## Experiments' CLAUDE.md
```

If absent, tell the user the project has no `## Experiments` section, propose one (corpus,
instruments + calibration references, results home), and proceed using answers gathered via
`AskUserQuestion` — do not block. Read the tracking issue when one is referenced
(`gh issue view <N>`).

---

## Step D1 — Determine the tier (design mode)

**Tie-breaker:** if the session is about to write **adopt, reject, failure, or success** about a
process change, it is **Tier 1**. Otherwise:

- **Tier 0 — probe.** An n=1 exploration. Emit the three-line declaration into the tracking issue
  and STOP — no corpus, calibration, or bar required:

  ```
  [experiment-audit] Tier 0 probe — <experiment slug>
  Hypothesis: <one falsifiable sentence>
  Trying: <what will be run, once>
  Legal endings: signal — escalate to Tier 1 | infeasible as specified | shelved — untested
  ```

  A Tier 0 probe may conclude *only* one of those three endings — **never** adopt / reject /
  "failure" / "success."

- **Tier 1 — test.** Required before any adopt/reject of a standing process (a briefing, a workflow
  step, a gate threshold, any rule justified by "we tested it"). Continue to D2.

---

## Step D2 — Draft pre-registration fields 1–5 (the question)

Auto-draft each field; mark any you cannot derive with a `<CONFIRM>` tag for the user to fill.

1. **Hypothesis & goal — name the one primary outcome construct.** One falsifiable sentence, then the
   goal stated *arm-agnostically* as the **single primary construct the change exists to improve** —
   the outcome itself, never a mechanism or proxy of it. For the narrative-flow case that construct is
   *overall letter cohesion (a naturally emergent, JD-relevant theme carrying the whole letter)* —
   **not** "the close resolves the opener's frame," which is one easily-fixed ~20% mechanism of
   cohesion. State it arm-agnostically ("a more cohesive letter"), never as a paradigm preference
   ("generate-then-decide is better"). **The hypothesis verdict is read off this construct** (field
   10); every field-3 criterion traces to it and is classified against it.
2. **Manipulated variable.** Exactly one, named, with a held-constant list (model, briefing version,
   corpus, processing tail, judge). If the challenger inherently bundles changes, name the bundle and
   pre-narrow the conclusion to the bundle.
3. **Success criteria — classify PRIMARY vs. SECONDARY, and trace each to the goal.** Tag every
   criterion **PRIMARY** (it directly operationalizes the field-1 construct — e.g. a blind
   whole-letter cohesion rating) or **SECONDARY / mechanism** (a necessary condition, sub-property, or
   diagnostic — e.g. bookend correspondence, one ~20% easily-fixed slice of cohesion). **The
   hypothesis verdict is read off the PRIMARY criteria only; a SECONDARY criterion may FLAG or
   diagnose, but a FLAG on it alone can NEVER produce a `refuted` / failure verdict on the primary
   construct** — deciding on it would substitute a measurable proxy for the goal (threat **T10**). If
   no criterion validly operationalizes the primary construct, the instrument set is uncalibrated *for
   the goal* — a construct-validity gap; build a primary-construct measure before scoring. Separately,
   mark any criterion that exists *because one arm is known to fail it*: it runs as **DIAGNOSTIC**, not
   verdict-bearing, until calibrated in D4 (threat T3).
4. **Baseline (control arm).** Default: a *fresh* incumbent run on this corpus at the D5 stage. An
   archived artifact needs a one-line representativeness justification; a known-*failure* artifact is
   **not** a neutral baseline.
5. **Corpus.** List inputs *before* any generation; span difficulty; do not compose solely of
   incumbent-failure cases; include ≥1 input where the incumbent is known-good. A probe's origin case
   may be included, flagged "origin case."

---

## Step D3 — Incumbent-influence inventory (field 6)

Trace every briefing, template, default, prompt, or gate the **challenger's generation** will read.
Emit a table:

```
[experiment-audit] Incumbent-influence inventory — <slug>
| Input the challenger reads | Encodes the incumbent's METHOD or the TASK? | Action |
|----------------------------|--------------------------------------------|--------|
| <file/default>             | method | task                              | neutralize (strip/substitute) | log as threat T1 |
```

**Equal-integration clause:** the challenger runs with its *own* natural defaults and comparable
integration effort. A challenger run through a hostile harness tests the harness, not the challenger.
Anything left un-neutralized is logged as threat **T1 (treatment contamination)**.

---

## Step D4 — Instrument-calibration worksheet (field 7)

Every judge/check that will touch results must be validated against ≥1 known-good **AND** ≥1
known-bad reference **before any arm is scored** (references come from the project `## Experiments`
section). Emit:

```
[experiment-audit] Instrument calibration — <slug>
| Instrument | Known-good ref → expected | Known-bad ref → expected | Runs before arm scoring? |
|------------|---------------------------|--------------------------|--------------------------|
| <judge/check> | <ref> → PASS            | <ref> → FLAG             | yes                      |
```

An **uncalibrated** instrument is **quarantined**: it may run, its output is reported as instrument
data, but it **cannot bear on the verdict** (unresolved = threat **T2**). LLM judges state their
ensemble plan (e.g. min-of-3 lenses).

---

## Step D5 — Parity, judging, and the decision rule (fields 8–10)

8. **Processing parity.** Name the pipeline stage at which arms are compared ("post-draft, pre-gate"
   or "fully gated"). Same tail both sides, or neither.
9. **Judging protocol.** Record scorer identity; drafter ≠ scorer; fresh context per scored artifact
   (no judge scores both arms in one context after seeing labels/prior scores); strip labels;
   randomize order; log residual identifiability (a style tell that unblinds an arm) as a threat
   rather than papering over it.
10. **Decision rule.** Win bar; aggregation function (median / win-rate) fixed; **n (inputs) and k
    (generations per input per arm) fixed — no optional stopping**; discard criteria (technical
    failures only, all logged); the outcome map (which results → supported / refuted / inconclusive);
    one sentence on why this n/k makes a fluke unlikely to clear the bar; a provenance plan (model IDs
    + prompt/briefing SHAs per arm); the results home; and the tier + rider status.

**Adoption rider** (attach when the decision would *replace* a standing process, not merely inform
iteration): ≥1 held-out input never seen during challenger iteration, scored once at the end; a named
post-adoption tripwire (a golden-set entry or dated baseline snapshot); and the rollback path.

Resolve every `<CONFIRM>` marker with the user before freezing.

---

## Step D6 — Freeze

Emit the complete 10-field pre-registration as one `[experiment-audit] Pre-registration — <slug>
(Tier 1)` block, then **post it to the tracking issue before any generation** (with the user's
confirmation, per the messaging rules) — GitHub's timestamp + edit history is the tamper-evidence
Gate 0 checks later. Optionally also write `<results-home>/preregistration.md`. Design mode ends
here; generation happens *after* the freeze.

---

## Step V1 — Locate the pre-registration (verdict mode)

Find the frozen pre-registration (the tracking issue, or `<results-home>/preregistration.md`) and the
results (artifacts, scores, timestamps — `git log --format=%ci -- <path>` dates generation vs. the
freeze). **If none exists**, the verdict is capped at `inconclusive — unregistered`; the only product
is a *salvage design* (run `design` mode to produce a Tier-1 pre-registration for a proper re-run).
Do not manufacture a retroactive pre-registration.

---

## Step V2 — Gates 0–1

- **Gate 0 — Registration & freeze.** The pre-registration exists AND its freeze timestamp precedes
  *every* generation timestamp. Missing or post-hoc ⇒ cap at `inconclusive — unregistered` (→ salvage).
- **Gate 1 — Deviation ledger.** Diff conduct against each of the 10 fields. Author-listed deviations:
  assess impact per deviation. An **auditor-discovered** deviation the author did not disclose ⇒ the
  verdict is **void** (re-audit only after full disclosure).

---

## Step V3 — Gates 2–3

- **Gate 2 — Instrument validity.** Every verdict-bearing instrument's calibration ran, *before* arm
  scoring, and passed (known-good → PASS, known-bad → FLAG). Instruments failing this are quarantined
  retroactively; if the headline result depended on one, verdict = `inconclusive — confounded by T2`.
- **Gate 3 — Achieved power & complete reporting.** Report achieved n and k vs. registered; a
  per-input × per-generation table with **no silent discards** (each discard cites its pre-registered
  criterion); the **worst-case row beside the aggregate**; and a heterogeneity note (win/loss
  direction per input — an aggregate may not hide a split).

---

## Step V4 — Gate 4: threat sweep T1–T10

Emit one row per threat, each `PASS` / `FLAG` / `N/A` with one line of evidence. Any `FLAG` on a
load-bearing threat forces `inconclusive — confounded by <Tn>`.

```
[experiment-audit] Threat sweep — <slug>
| # | Threat                                              | Verdict | Evidence |
|----|----------------------------------------------------|---------|----------|
| T1  | Treatment contamination (incumbent leaks in)      | PASS/FLAG | ... |
| T2  | Uncalibrated instrument                           | ... | ... |
| T3  | Circular criteria (gate from one arm's known fail)| ... | ... |
| T4  | Baseline non-neutrality                           | ... | ... |
| T5  | Processing inequality (stage mismatch)            | ... | ... |
| T6  | Corpus bias / regression to the mean              | ... | ... |
| T7  | Power / cherry-picking / optional stopping        | ... | ... |
| T8  | Peeking / HARKing (bar moved after results)       | ... | ... |
| T9  | Judge contamination / dependence                  | ... | ... |
| T10 | Criterion substitution / surrogate endpoint       | ... | ... |
```

**T10 (criterion substitution)** is the "wrong-yardstick" threat and is distinct from T3: a criterion
can be perfectly fair and independent yet still be the wrong *construct* — the verdict decided on a
measurable proxy or a SECONDARY / mechanism criterion instead of the field-1 primary construct.
FLAG it whenever the headline verdict rests on anything other than the primary criteria (field 3).
The motivating incident: a "failure" verdict driven by bookend correspondence (a ~20%, easily-fixed
mechanism) while the actual goal — overall letter cohesion — went unmeasured.

---

## Step V5 — Gate 5: verdict, scope, decision legality

State the **verdict** from the pre-registered outcome map *only* — `supported` | `refuted` |
`inconclusive — confounded by <X>`. **The verdict must be read off the field-1 PRIMARY construct via
its PRIMARY criteria** (field 3): a FLAG confined to SECONDARY / mechanism criteria (e.g. bookend
correspondence) can drive `iterate` or, if it undermines the primary measure, `inconclusive` — but
**never `refuted`** of the primary hypothesis; concluding failure on a proxy is threat T10, and Gate 4
must already have FLAGged it. Add a **scope statement**: "holds for `<corpus>` under `<model IDs /
prompt-briefing SHAs / processing stage>`" — no unscoped generalization. Then read the **decision**
off the legality matrix:

| | supported | refuted | inconclusive |
|---|---|---|---|
| **Tier 0** | escalate only | infeasible-as-specified only | shelve |
| **Tier 1** | adopt (rider satisfied) / iterate | reject *scoped to tested conditions* / iterate | proper re-run or shelve — never adopt/reject |

`shelve` is always legal, recorded as `shelved — untested` (abandoning for cost is legitimate;
mislabeling it refuted is not).

---

## Step V6 — Gate 6: record

Write the full audit — with the **same depth for a negative result as a positive one** — to the
engineering-journal report path `sessions/<project>/reports/YYYY-MM-DD-<slug>.md` (per the global
journal "Report / analysis generated" trigger; link it from the session stub), and to the project's
results home (e.g. career-playbook's `calibration/<slug>/notes.md` + `scores.md`). Comment the
verdict + scope on the tracking issue (and close it if the decision resolves it).

---

## Notes

- **Probe data designs the test; it never scores in it.** The act of noticing signal selected the
  probe's output (threat T7), so counting it in a Tier-1 verdict is cherry-picking by construction.
  Probe artifacts may legally inform the design and serve as calibration / known-bad references only.
- **Quarantine is mechanical, not a judgment call.** A FLAG from an uncalibrated instrument is
  excluded from the verdict — do not argue it back in. That exclusion was the fix for the motivating
  incident's Part-4 problem.
- **A circular gate is a hypothesis, not evidence.** A check purpose-built from one arm's known
  failure tests that failure mode; it is verdict-bearing against the *other* arm only after it passes
  known-good calibration (then it graduates).
- **Measure the goal, not a proxy for it (T10).** Name the one primary outcome construct the change
  exists to improve, and decide the verdict on *that*. A secondary/mechanism criterion — even a fair,
  independent, goal-connected one — can diagnose or trigger `iterate`, but never declare failure of
  the primary hypothesis. The incident: a spike judged a "failure" on bookend correspondence (a
  ~20%, easily-fixed mechanism) while its real target, overall cohesion, was never measured. This is
  distinct from T3 (a proxy can be perfectly fair and still be the wrong construct).
- **The audit is a table, not a re-run.** Verdict mode reasons over recorded results + the frozen
  pre-registration; it does not regenerate arms.
- **Project cross-references** (career-playbook): its calibration harness already encodes
  *no-single-model-emulation* (drafter ≠ scorer) and *scorer-identity-recorded* — incorporate those
  by reference in fields 7/9 rather than duplicating them.
- Residual limitations (small-sample calibration is discrimination-in-principle not a measured
  false-positive rate; the owner remains an unblinded final judge over time; rubric outcomes are not
  market outcomes) are documented in [ADR-115](../../../docs/adr/115-experimental-rigor-protocol.md)
  — name them in the scope statement rather than pretending they are closed.
