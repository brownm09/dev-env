# ADR-145: Scope Discipline for Self-Referential Churn — ADR Granularity, Tile-Persistence ROI, and a Retroactive Vacuity Audit

**Date:** 2026-08-28
**Status:** Accepted
**Closes:** [dev-env#1079](https://github.com/brownm09/dev-env/issues/1079)
**Tags:** workflow, scope-discipline, churn, adr-granularity, amendment, tiles, tile-shards, roi, gates, vacuous-gate, known-bad-reference, calibration, retroactive-audit, measurement, decision-record, claude-md, global-rule, claude-facing, adr-011, adr-094, adr-116, adr-118, adr-144
**Related:** [ADR-011](011-adr-warrant-check.md), [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-116](116-single-source-worktree-recovery-recipe.md), [ADR-118](118-tile-persistence-shards.md), [ADR-144](144-gate-calibration-pass-3-dimension.md)

---

## Context

The biweekly retro of 2026-08-08 ([dev-env#963](https://github.com/brownm09/dev-env/issues/963))
observed that most of dev-env's merged work in that window repaired machinery the tooling itself
had created, rather than serving the projects the tooling exists for. It asked for an explicit
decision on three questions and gave them no owning issue.
[dev-env#1079](https://github.com/brownm09/dev-env/issues/1079) is that issue; this ADR is its output.

This is a **decision record, not an implementation**. The pull to start fixing things mid-audit is
the failure mode under review, so every repair this review surfaced is filed as its own scoped
issue and none is performed here.

### What the numbers actually say

The retro's framing needs one correction before it can carry a decision. "70% of merged PRs
repaired machinery the tooling itself created" is, for a *tooling repo*, close to tautological:
dev-env's product **is** hooks, skills, and workflow rules, so ~100% of its PRs are about tooling
by construction. That ratio measures the repo's purpose, not its discipline.

The discipline question is the second derivative — **how much of that work repairs machinery this
repo introduced recently**, as opposed to maintaining machinery that has earned its place. Measured
over the retro window (2026-07-11 … 2026-08-08, 92 commits on `main`, a squash-merge repo so
≈92 merged PRs):

| Measure | Value |
|---|---|
| Conventional-commit mix | 38 `fix` · 23 `docs` · 21 `feat` · 4 `refactor` · 6 other |
| `fix` PRs as a share of the window | **41%** |
| File-touches in `fix` PRs repairing a `claude/scripts/` file **≤7 days old** | 18 of 75 (**24%**) |
| …**≤30 days old** | 49 of 75 (**65%**) |
| ADRs added in the window | 28, i.e. one for every ~3.3 PRs |
| ADRs added on 2026-07-22 alone | **5** |

Two clarifications the review owes its own evidence. First, the retro's "5 ADRs on 2026-07-22"
is correct **by merge date**; counted by each ADR's own `Date:` field the same day shows 2. ADR
dates and merge dates diverge routinely, and any future count should say which it means. Second,
65% of script repairs landing within a month of the script's creation is the real signal: the repo
is not maintaining mature machinery, it is **debugging new machinery in production**.

### The evidence cuts both ways, and the decision must respect that

**For the concern.** [#1076](https://github.com/brownm09/dev-env/pull/1076) (ADR-144) deliberately
declined to build a count-parity gate pinning the phrase "eight dimensions" to the length of the
list beneath it, citing this retro item. That is the discipline working — but it worked because a
human-authored retro item existed to point at, not because any mechanism enforced restraint.

**Against a naive "build less" reading.** The same session hit a real coordination failure the
existing machinery does *not* cover: two concurrent sessions each claimed ADR-143, both having
correctly run the documented ref-scoping check, because a **pushed branch with no PR** is invisible
to both `origin/main` and the open-PR set. It surfaced only because one session clobbered the
other's same-named scratch file ([dev-env#1080](https://github.com/brownm09/dev-env/issues/1080)).

So the question is **which** machinery earns its keep, not whether to build less of it.

---

## Decision

### 1. ADR granularity — the unit of an ADR is a *decision*, not a *change*

ADR-011's four criteria are **trigger conditions** — they answer "does this change need a written
rationale?" They were never a **granularity** rule, and nothing answered "*where* does that rationale
go?" Absent that second question, every qualifying change defaulted to a new file, which is how one
day produced five ADRs.

Adopted, and recorded as an amendment to ADR-011 so the next session applying the warrant check
gets the new grain:

> Once a change is warranted, choose the smallest home that preserves the reasoning:
>
> - **A new ADR** — the change makes a decision that could later be *reversed on its own*: a new
>   invariant, a mechanism whose alternatives were weighed, a rule other CLAUDE.md files will cite.
> - **An amendment to an existing ADR** — the change refines, corrects, extends, or bounds a decision
>   **already recorded**. If the honest opening sentence is "as ADR-N says, except…", it is an
>   amendment. A follow-up fix to machinery an ADR introduced is *always* an amendment, never a new
>   ADR; so is a newly discovered failure mode of a decision already taken.
> - **A CLAUDE.md line with no ADR** — the reasoning is fully contained in the rule as written and
>   `git log` plus the issue recover everything a reader needs. A one-line rule with no rejected
>   alternative and no invariant to protect does not need a file.
>
> Tie-break: if you cannot name at least one *alternative you rejected*, it is not a new ADR.

The tie-break is the operative half. It is checkable in seconds, it is the thing ADR-011's own
structure already demands (every ADR carries an *Alternatives Considered* section), and applied
retroactively it is exactly what separates the substantial ADRs of the window from the thin ones.

**Not adopted: a numeric cap** ("at most N ADRs per day/week"). A cap would be a threshold applied
to a population with no calibration behind it — the very defect §3 audits for — and it punishes a
genuinely dense day of real decisions identically to a day of over-splitting.

### 2. Tile-persistence (ADR-118) — **keep the shards, cap the digest**

Measured cost, live at the time of writing:

| Measure | Value |
|---|---|
| ADR-118 commits since 2026-07-22 | 8, of which 4 landed on the **same day** |
| Amendments | 6 |
| Tile shards on disk | **253** across 7 projects (career-playbook 158, dev-env 49) |
| Median shard age | 17.7 days · none older than 60 days |
| Shards the session-start reconciler could **not** resolve against GitHub in one session | **108 of 251 (43%)** — lookup budget exhausted |
| Shards permanently unreconcilable (`url failed validation`) | 2 — never resolved, never deleted |

The retro's hypothesis was that the shard layer is redundant against ADR-094's guarantee that every
genuine tile already has a GitHub issue. The evidence **does not support retiring it**, for a reason
the hypothesis did not anticipate: a 20-shard sample of dev-env's store found **18 of 20 issues still
open**. The store is not rotting — it is an accurate reflection of a genuinely large open backlog,
and the payload it holds (`prompt`, `cwd`, `tldr`) is the part an issue does *not* carry and cannot
reconstruct. Retiring the shards would trade a real capability for a saving the evidence says is not
there.

What the evidence *does* indict is the **digest**, not the store. Every session pays for a
251-line pending-tiles block of which 43% is unverified, and which the reconciler cannot finish
within its lookup budget. A digest that reports "pending" for work that may be finished, and says so
in its own footer, is a diagnostic being read as a gate.

**Decision:** keep the shard store and its write rule unchanged. Cap and rank the session-start
digest so its cost is bounded and everything it prints is verified — the unverified remainder becomes
a single count, not 108 unverified rows. Filed separately as
[dev-env#1082](https://github.com/brownm09/dev-env/issues/1082); **not implemented here.**

**The transferable lesson is about the amendments, not the shards.** Four commits on one day and six
amendments is what §1's grain rule now prevents from recurring: those were refinements of one
decision, and under the new rule they were always amendments — which is, to ADR-118's credit,
exactly how five of the six were in fact recorded.

### 3. Vacuous gates — audited retroactively, and the class is **narrow, not systemic**

ADR-144's Pass-3 dimension 8 requires every *new* gate to name a known-bad reference it fires on.
That is precisely an anti-vacuity proof, and nothing had applied it backwards. This review ran it
backwards over the existing inventory.

**Method.** The population is checks that classify inputs they do not enumerate *and* block, fail, or
act destructively — ADR-144's definition, which excludes fixture-pinned unit tests. Two signals:
(a) does the gate's declared test file assert it **fires** on a bad input, and (b) can the gate's
input-extraction silently yield **empty**, making its comparison trivially true (the ADR-116 class)?

**Result — runtime gates: healthy.** Of **29** blocking scripts under `claude/scripts/`, **27** map to
a test file in the `## Testing` index and every one of those carries known-bad assertions. Two are
unmapped: `pre-tool-use-nested-agent-background-guard.py` (wired, blocking, *has* a test file on
disk but no index entry) and `reconcile-late-stubs.py` (not wired, no test). Five wired hooks are
absent from the index; four of those five have no test file at all
(`awake-blocker.py`, `multi-worktree-alert.py`, `post-tool-use-cwd-track.py`, `turn-count-hook.py`),
all non-blocking advisories.

**Result — structural gates: one real, high-stakes vacuity, confirmed with a constructed known-bad.**
The three settings-wiring gates and the heartbeat guard all read `claude/settings.shared.json`
through the shared `_hook_wiring.hook_entries()`, which returns `[]` on any structural surprise
(`if not isinstance(hooks, dict): return []`) rather than raising, with **no non-empty assertion
anywhere in the parser or its consumers**. Feeding three realistic malformations (a renamed `hooks`
key, `hooks` as a list, groups as dicts), each driving the parser to 0 entries where the real file
yields 83:

| Gate (`## Testing` item) | Fires on the known-bad? |
|---|---|
| `test_hook_safe_exit_guard.py` (62) | ✅ fires |
| `test_settings_hook_wiring.py` (63) | ✅ fires |
| `test_hook_output_contract.py` (**61**) | ❌ **passes vacuously** — reports "39 passed" against zero hooks |
| `test_hook_heartbeat_guard.py` (**68**) | ❌ **passes vacuously** — reports "11 passed" against zero hooks |

Two of four green-lighting a settings file that wires nothing, on the surface that wires all 50
hooks. Filed as [dev-env#1081](https://github.com/brownm09/dev-env/issues/1081); **not fixed here.**

**Verdict: the class is narrow.** 27 of 29 runtime gates calibrated and 2 of ~13 structural gates
vacuous does not justify a remediation programme, a standing anti-vacuity gate, or a sweep of the
whole inventory. It justifies **two scoped fixes and a named pattern** — which is itself an
application of this ADR's own thesis.

**The pattern already exists in-repo, and is now named.** ADR-116's own gate is the worked example:
alongside its parity assertion it carries two dedicated tests whose only job is to prove the
comparison is not against nothing —

```python
def test_runbook_section_exists(self): ...      # the extracted section is non-empty
def test_runbook_has_runnable_lines(self): ...  # "a vacuous pass, not a clean one"
```

Any gate whose input is *extracted* (a section scan, a regex, a glob, a parse that degrades to `[]`)
owes an assertion that the extraction is non-empty, separate from the comparison itself. Added to
dimension 8 in `claude/CLAUDE.md` as a sentence, not a mechanism.

### 4. The standing rule this review adds

One line, in `claude/CLAUDE.md`'s ADR-warrant bullet, because it is the moment the question arises:

> **Prefer the smallest durable home.** Amendment over new ADR; a CLAUDE.md line over either. If you
> cannot name an alternative you rejected, it is not a new ADR.

No hook, no gate, no checklist item. A mechanism to enforce restraint about mechanisms would be
self-refuting, and §3's audit is the evidence that dev-env's gates are mostly fine without another
one watching them.

---

## Instrument calibration (dimension 8, applied to this review's own instruments)

This ADR introduces no gate — the audit scripts are one-off readers in
`C:/Users/brown/.claude/scratch/`, deliberately not committed, and they report rather than block. But
the audit's conclusions rest on them, so their provenance is stated to the same standard.

- **Vacuity probe** (`vacuity_probe_1079.py`). *Measured property:* whether a gate's process exit
  status changes when `hook_entries()` is driven to 0 entries. *Known-good:* the real
  `settings.shared.json` — 83 entries, all four gates green. *Known-bad:* three constructed
  malformations, each independently verified to drive the parser to 0. *Non-vacuity of the probe
  itself:* it **fires on 2 of 4** gates; a probe that reported all-clear would be indistinguishable
  from a broken probe, and this one is not.
- **Calibration scan** (`gate_audit_1079_v2.py`). *Measured property:* presence of failing-outcome
  assertions in the test file the `## Testing` index declares for each gate. *Known-bad:* its own v1,
  which resolved gate→test by glob fallback (`test_*guard*.py`), silently scored six gates against
  another gate's assertions, and reported a confident "calibrated" for all of them. v2 resolves from
  the index and reports `UNMAPPED` rather than guessing; the two genuine unmapped gates are the ones
  v1's fallback had hidden. **The audit reproduced, in its own first draft, the exact defect it was
  auditing for** — an extractor quietly matching the wrong input and a comparison succeeding against
  it. That is recorded rather than tidied away, because it is the strongest available evidence that
  the ADR-116 class is easy to reintroduce and worth a named pattern.
- **Stated limitation.** The calibration scan detects "no known-bad assertion *at all*." It does
  **not** detect "the known-bad assertion compares empty to empty" — it would not have caught
  pre-fix ADR-116, and did not find the two vacuous gates above. Those came from the probe. Any
  future reuse of these readings must not treat the scan's clean result as an anti-vacuity proof.

---

## Consequences

**Positive.**

- Three questions that had been raised but never decided now have written answers a future session
  will encounter at the moment each applies: granularity in ADR-011 and the warrant bullet, the
  anti-vacuity pattern in dimension 8.
- The ADR-granularity tie-break is cheap enough to actually apply ("name a rejected alternative") and
  is checkable against a structure ADRs already have.
- The gate audit converts a suspicion ("gates may be vacuous") into a measured claim (27/29 runtime
  gates calibrated; 2 of 4 settings-wiring gates vacuous, with a reproducible known-bad), and the two
  real defects are filed rather than left as an impression.
- Every repair surfaced left this review as a scoped issue, so the decision record stayed a decision
  record.

**Negative.**

- The granularity rule is a judgment call with no mechanical enforcement, so it will degrade the way
  judgment rules do. Accepted deliberately: §4 explains why a gate here would be self-refuting, and
  the biweekly retro is the existing backstop that surfaced this in the first place.
- "Name a rejected alternative" is gameable — an author who wants a new file can always invent a
  thin alternative. It raises the floor rather than closing the door.
- The audit's population is dev-env's own gates. Other repos' gates are unaudited, and the
  27/29 result should not be generalized to them.
- Keeping ADR-118's shard store means keeping a 253-file store and its reconciler. The decision
  bounds the *digest* cost, not the store's carrying cost.

---

## Alternatives Considered

**Retire the tile-shard layer entirely** (the retro's implied hypothesis). Rejected on evidence: 18
of 20 sampled shards track still-open issues, and the shard payload (`prompt`, `cwd`) is precisely
what the GitHub issue does not carry. The measured cost is concentrated in the session-start digest,
which is fixable without discarding the store.

**A standing anti-vacuity gate** — a test asserting every gate's extracted population is non-empty.
Rejected: it is machinery watching machinery, the exact pattern under review, and §3's measurement
says the class is 2 gates wide. A named pattern in dimension 8 plus two scoped fixes is proportionate;
if the class recurs after those land, that is new evidence and the decision can be revisited.

**A numeric ADR cap.** Rejected — see §1: an uncalibrated threshold on a population, which
dimension 8 would itself reject.

**Fix the two vacuous gates in this PR.** Rejected: [#1079](https://github.com/brownm09/dev-env/issues/1079)
scopes this as a decision review and names mid-audit repair as the failure mode. The fixes are real
and small, which is exactly what makes the temptation worth resisting on the record.

**Do nothing — treat the retro observation as noise.** Rejected: the repair-latency measurement (65%
of script fixes landing within 30 days of the script's creation) is a real signal, and two genuinely
vacuous gates were found on the highest-stakes surface in the repo.

---

## Verification

- `claude/CLAUDE.md`'s ADR-warrant bullet carries the smallest-durable-home rule; Pass-3 dimension 8
  carries the extraction-non-empty sentence.
- [ADR-011](011-adr-warrant-check.md) carries a dated amendment with the three-way granularity rule
  and the tie-break.
- Follow-ups filed, not implemented: [dev-env#1081](https://github.com/brownm09/dev-env/issues/1081)
  (two vacuous settings-wiring gates), [dev-env#1082](https://github.com/brownm09/dev-env/issues/1082)
  (bound the pending-tiles digest), [dev-env#1083](https://github.com/brownm09/dev-env/issues/1083)
  (`pre-tool-use-nested-agent-background-guard.py` absent from the `## Testing` index).
- [dev-env#1080](https://github.com/brownm09/dev-env/issues/1080) (ref-scoping blind spot) deferred to
  this review's conclusion and is **unblocked**: it is a documentation fix to an existing checklist
  backed by an observed incident, which §1 classifies as a CLAUDE.md line with no ADR — the cheapest
  home, and precisely what this ADR endorses.
- Tracked in [dev-env#1079](https://github.com/brownm09/dev-env/issues/1079); action item 3 of
  [dev-env#963](https://github.com/brownm09/dev-env/issues/963).
