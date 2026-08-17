# ADR-135: `journal-compose` Step 9 Cleanup-Completeness Gate

**Date:** 2026-08-16
**Status:** Accepted
**Tags:** journal, composition, skill, validation, gate, manifest, stub, cleanup, silent-failure, advisory, multi-project, post-merge, adr-056, adr-119, adr-121

---

## Context

`journal-compose` Step 9 deletes the source `.stub.md` and `.manifest.jsonl` files for every
composed session once their content is folded into the composed journal doc. [dev-env#1005]
documented that `.manifest.jsonl` deletion leaks silently while `.stub.md` deletion never does —
confirmed across two different compose code paths:

- **A normal same-day multi-project compose** (engineering-journal PR #198, 2026-08-01): every
  `.stub.md` for the date was correctly deleted across 4 projects; 6 `.manifest.jsonl` shards
  were not. Open-vs-closed PR status in the manifest didn't predict which survived, ruling out
  "kept because its PR is still open" as the mechanism.
- **Recovery-pass composes** (dev-env#876's backlog remediation): the same shape, on a different
  code path — composing a stub/manifest pair discovered sitting directly on `main` rather than a
  fresh draft branch. 3 more instances, including one from the same-day engineering-journal
  PR #224, whose own composed doc explicitly noted the gap as already-visible-but-unfixed at
  compose time.

The leaked manifests' content was correctly present in their composed docs in every case — this
is not a data-loss bug. It is a cleanup-completeness bug: stale shard files accumulate in the
repo indefinitely because nothing ever notices they were supposed to go and didn't.

**Root cause.** `journal-compose` has no interpreter executing Step 9 — the entire compose flow
(single-project, and the multi-project Phase 1/Phase 2 split) is prose in `SKILL.md` that an LLM
agent reads and translates into Bash tool calls, once per project per compose run. Step 9's own
heading read "**Delete stub files and release lock**", and the multi-project Phase 2 per-project
checklist read "**Step 9** — Delete stubs and release lock for this project" — neither named
manifests, even though the step's body deleted them too. An agent reconstructing what "Step 9"
entails from its own heading or the per-project checklist shorthand — plausible across a
multi-project loop, or a different-day recovery pass, rather than re-reading the full code block
fresh every time — reproduces exactly what the title says: stub deletion and lock release. The
manifest deletion, never name-checked in either place, is exactly the detail that goes missing.
This matches the evidence precisely: the file kind named in the title (stub) was 100% reliable;
the file kind mentioned only in the body (manifest) leaked.

Two structural gaps compounded this:

1. **No completion check.** Step 6.5 (fidelity self-check), Step 6.6 (structural assertion), and
   Step 8b (stray-terminal-output scan, [ADR-121]) all verify their own step actually succeeded
   before letting the compose proceed. Step 9 had no equivalent — an incomplete deletion sailed
   straight through Step 10's commit undetected. Step 10's own "verify what's staged" check only
   guards against staging something *unexpected*; it has no way to notice an expected deletion
   that silently didn't happen, since a missing `rm` just makes the staged diff smaller, and
   nothing flags "smaller than expected."
2. **No post-merge backstop for this file kind.** A "Post-merge shard-leak check" already existed
   for Step 9.5's open-PR shard reconciliation — confirming, after merge, that no reconciled
   `open-prs/<N>.json` shard reappeared on `origin/main`. No equivalent existed for Step 9's
   output (stub/manifest files).

Both evidence populations route through the same Step 9 prose: recovery composes land stub/
manifest pairs onto a `draft/<date>` branch and then run the standard `/journal-compose` flow
(dev-env#876's own approach: "recover onto a `draft/<date>-recovery` branch ... compose, merge").
`reconcile-late-stubs.py` (moves late-pushed stub/manifest pairs between branches) and
`journal-compose-replay.sh` (conflict-recovery diff/replay) were checked and ruled out — neither
touches deletion logic.

**dev-env#993 cross-check.** dev-env#993's 2026-08-15 recovery (a different incident — a Phase 1
subagent ran the full solo compose+merge flow instead of stopping at Step 6.6) landed as
[engineering-journal PR #216], merged 2026-08-16. Its file diff shows paired `DELETED` status for
both `.stub.md` and `.manifest.jsonl` across all 5 recovered sessions, and `origin/main` carries
no leftover stub/manifest files for 2026-08-15 as of this writing. That recovery did not exhibit
this leak.

## Decision

Three changes to `claude/skills/journal-compose/SKILL.md`, all prose/bash — consistent with the
existing Step 6.5/6.6 self-check idiom and the existing post-merge leak-check idiom, both of
which are themselves prose/bash rather than externalized scripts:

**1. Name every deleted artifact kind in the step heading and its cross-reference.** Step 9's
heading becomes "Delete stub, manifest, and lock files"; the Phase 2 per-project checklist bullet
becomes "Delete stubs, manifests, and release lock for this project". This directly targets the
title-omission mechanism identified above.

**2. Add a completion-verification gate immediately after Step 9's deletion commands**, modeled
on Step 6.6's `chk()`-style self-check already established in this file: re-glob for any
remaining `.stub.md` or `.manifest.jsonl` matching the composed date under
`$WT/sessions/<project>/`, and require the agent to re-run the deletion and re-check before
proceeding to Step 9.5 or Step 10 if anything remains. Scoped per-project in multi-project mode,
so a leftover in one project isn't masked by a clean result in another. This is the load-bearing
fix: it makes reaching the commit with an incomplete Step 9 structurally impossible, independent
of whether fix (1) alone would have been sufficient in every future case.

**3. Extend the existing post-merge leak check** (renamed "Post-merge cleanup-leak check" to
reflect the broadened scope) with a second check, run alongside the open-PR-shard one, scanning
`origin/main` after merge for the exact stub/manifest paths this run's Step 9 deleted — mirroring
the open-PR-shard check's own "exact list this run reconciled" scoping rather than a tree-wide
glob for the composed date. A tree-wide date glob was the first-drafted shape and was corrected
during review: `journal-compose`'s Step 1 discovers stubs across *all* projects for the target
date up front, but the post-merge check runs after commit and merge, at the end — a concurrent
session pushing a new, legitimate stub for the same date in a project Step 1 never saw (because
the push happened after discovery) would be caught by a tree-wide glob and misreported as a leak,
with the check's own suggested remediation ("remove in a follow-up commit") pointing at deleting
someone else's in-progress work. Defense in depth for a leak that reaches the commit anyway — e.g.
a differently-shaped recovery flow that doesn't follow Step 9's prose as literally as the standard
flow does.

## Consequences

- A future compose that skips manifest deletion now fails the Step 9 gate immediately, in the
  same run, before the commit — instead of shipping silently and being discovered, if at all,
  only by chance days or weeks later (as both #1005 evidence populations were).
- A leak that somehow still reaches `origin/main` is caught immediately post-merge by check (3),
  narrowing the detection window from "next incidental audit" to "the same session."
- No new script, no new test file. The check is a glob-and-nonempty-test, matching Step 6.6's
  existing complexity class, not Step 8b's (`validate-composed-output.py`'s signature/fence/span
  detection is genuine algorithmic logic that warrants a script and a test suite; this doesn't).
- Rollback is a revert of the `SKILL.md` prose edits — the gate owns no state and no schema.
- Retroactive cleanup of the files already leaked in #1005's evidence is explicitly out of scope
  here — that is tracked and already in progress via engineering-journal#222's cleanup PR, in a
  different repo. This ADR is about preventing recurrence, not remediating history.

## Alternatives considered

**Restructure deletion into a single stub-derived loop** (derive each manifest's filename from
the stub filenames already found, rather than an independent glob) — the shape #1005's own
"suggested next step" gestured at ("deleting both file kinds from one shared source-of-truth
list"). Rejected: `claude/CLAUDE.md`'s stub workflow documents a "late PR-merge bookkeeping
re-creates the shard" scenario, where a manifest can legitimately be re-created after its stub
was already composed away, carrying a `stub` field that may reference a filename no longer
physically present. Deriving manifest names strictly from currently-present stub files risks
silently missing that legitimate case — trading today's bug for a quieter one. The verification
gate (decision 2) reaches the same end state (nothing incomplete reaches the commit) without
depending on getting that edge case's mechanics exactly right; it also directly increments the
same evidence the issue asked for, since it fails on *any* leftover regardless of which mechanism
produced it.

**A dedicated Python validation script**, following [ADR-121]'s `validate-composed-output.py`
precedent exactly. Rejected as disproportionate: ADR-121's script exists because *detecting* a
stray-terminal-output leak requires real logic (a signature list, fence-awareness, code-span
matching with backreferenced ticks) that benefits from unit tests and precise, checkable
behavior. This check is "does a glob for a known filename pattern return anything" — the same
complexity class Step 6.6's inline `chk()` function already handles without a script, and adding
one here would be process for its own sake.

## References

- dev-env [#1005](https://github.com/brownm09/dev-env/issues/1005) — this issue
- dev-env [#993](https://github.com/brownm09/dev-env/issues/993) — the unrelated Phase 1
  subagent-scope incident whose recovery PR was checked and found clean of this leak shape
- dev-env [#876](https://github.com/brownm09/dev-env/issues/876) — the recovery-pass backlog
  remediation that produced evidence population B
- engineering-journal [#222](https://github.com/brownm09/engineering-journal/issues/222) — the
  original single-incident report and the retroactive cleanup of already-leaked files (separate
  from this ADR's scope)
- [engineering-journal PR #216](https://github.com/brownm09/engineering-journal/pull/216) — the
  dev-env#993 recovery PR checked as part of this investigation
- [ADR-121](121-composed-output-stray-terminal-scan.md) — the closest precedent: a validation
  gate added to `journal-compose` after a recurring silent-failure incident, and the template
  this ADR follows
- [ADR-056](056-per-session-sharding-journal-companion-files.md) — introduces the per-session
  manifest shards this gate protects
- [ADR-119](119-day-rollover-draft-branch-and-orphaned-shard-deletions.md) — the day-rollover and
  orphaned-shard-deletion discipline this complements (that ADR covers open-PR shards; this one
  covers stub/manifest files)

[dev-env#1005]: https://github.com/brownm09/dev-env/issues/1005
[engineering-journal PR #216]: https://github.com/brownm09/engineering-journal/pull/216
[ADR-121]: 121-composed-output-stray-terminal-scan.md
