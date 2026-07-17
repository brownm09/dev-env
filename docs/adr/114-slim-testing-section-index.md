# ADR-114: Slim the Root `## Testing` Section to an Index; Full Detail in `docs/TESTING.md`

Date: 2026-07-17
Status: Accepted
Tags: claude-md, testing, token-efficiency, context-weight, documentation, ssot, adr-074, adr-103

## Context

The root `CLAUDE.md` loads into every dev-env session's context. At `8a279ab` it was
203,421 bytes (~50.5k tokens), of which the `## Testing` section was 2,276 lines
(~47.2k tokens — 93% of the file): 75 numbered items, each a dense prose paragraph
describing one test file's coverage, scope gaps, and incident history. Combined with the
global `claude/CLAUDE.md` (~23k tokens), every session began ~73.5k tokens deep before
the first user word.

The per-item prose was also heavily duplicated: each test's story already exists in the
test file's own module docstring, the linked ADR(s), and (as a one-line map) in
`claude/scripts/tests/README.md` (dev-env#822) — with a curated subset in
`docs/REFERENCE.md` → Script verification suite. The 2026-07 optimization analysis
(dev-env#840, sub-issue dev-env#841) measured this as the single largest token-cost item
in the repo, with the lowest-risk fix.

Two structural constraints bound the change:

1. `pre-merge-numbering-check.py` (ADR-074) parses the section with
   `^(\d+)\.\s+\*\*` — unindented numbered items must remain, with stable append-only
   numbering, or that merge gate and its tests need coordinated changes.
2. The "Test before PR" rule (global `claude/CLAUDE.md`) defers to this section — the
   per-item "run this command when changing that file" mapping must stay in the
   always-loaded file, or the gate loses its operative content.

## Decision

1. **`CLAUDE.md` → `## Testing` becomes a pure index**: a short preamble (run-all
   command, CI note, numbering/collision rules, pointers) plus one generated line per
   item — `N. **name** — <first sentence: required-when-changing clause>. Run:
   `<command>`` — preserving the exact `N. **` shape ADR-074's parser matches and the
   full 1..75 numbering. Result: 203,421 → ~29k bytes (~43.6k tokens saved per session).
2. **Full per-item prose moves verbatim to `docs/TESTING.md`**, a new on-demand
   reference, under the **same item numbers** (relative links re-rooted for the `docs/`
   location). No information was deleted — the move is mechanical
   (`slim-testing-section.py`, run once; two hand-touched lines: items 2 and 4).
3. **Sync rule**: a new test adds an item to *both* files in the same PR — the index
   line in `CLAUDE.md`, the behavioral detail in `docs/TESTING.md`. The index preamble
   and `claude/scripts/tests/README.md` both state this; the numbering gate continues to
   police the index exactly as before.

## Consequences

- Always-loaded instruction weight drops ~43.6k tokens for every future session in this
  repo (~73.5k → ~30k combined) — the largest single win in the 2026-07 roadmap.
- Per-item behavioral detail is now one hop away instead of preloaded. Mitigation: the
  index line carries the operative facts (when to run, what to run); `docs/TESTING.md`
  is consulted when actually modifying a test/script, which is exactly when its detail
  is needed. The tests README and REFERENCE.md pointers were updated in the same PR.
- Drift risk between the two files is bounded by the shared numbering, the same-PR sync
  rule, and a parity gate added from `/review` on PR #855 (`test_testing_index_parity.py`,
  Testing item 76) asserting identical, contiguous item numbers and titles across both
  files. The merge-time collision check (ADR-074) guards only the index's numbers against
  origin/main — it never reads docs/TESTING.md. A CI instruction-weight gate (dev-env#852)
  will additionally prevent silent regrowth of the index file.
- `docs/TESTING.md` inherits the section's growth curve — acceptable: it is loaded on
  demand, not per session.

## Alternatives considered

- **Move prose into `claude/scripts/tests/README.md`** — rejected: that file's declared
  identity (dev-env#822) is a one-line navigational map; merging 190KB of prose into it
  destroys that.
- **Rely on test docstrings + ADRs alone (delete the prose)** — rejected: the prose
  contains cross-item routing ("run items 61-63 when wiring changes") and consolidated
  incident context not fully present in any single docstring; a verbatim move is
  zero-loss and reviewable, deletion is not.
- **Leave `CLAUDE.md` as-is and gate only future growth** — rejected: the existing 47k
  tokens are the cost; a growth gate without the move saves nothing.

## Follow-ups

- dev-env#852 — instruction-weight phase 2 (global CLAUDE.md narratives → ADRs, journal
  mechanics → REFERENCE) plus the CI weight gate.
- Parent roadmap: dev-env#840.
