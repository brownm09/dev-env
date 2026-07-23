# ADR-122: Gate the Per-Directory Index READMEs Against the Directories They Index

**Date:** 2026-07-23
**Status:** Accepted
**Tags:** testing, documentation, readme, index, parity-gate, drift, silent-failure, ssot, claude-scripts, adr-074, adr-114
**Issue:** [dev-env#901](https://github.com/brownm09/dev-env/issues/901)

---

## Context

Two directories in this repo carry a hand-maintained, one-row-per-file navigational index:
`claude/scripts/tests/README.md` (created in [dev-env#822](https://github.com/brownm09/dev-env/issues/822))
and `claude/scripts/README.md` ([dev-env#830](https://github.com/brownm09/dev-env/issues/830)). Both
exist because their directories are large enough (80+ and 82 files) that a reader needs a map.

Neither directory's tooling reads its README. `run-hook-tests.py` discovers tests by **glob**
(`test_*.py` / `*.sh`), and Claude Code loads scripts by their `claude/settings.json` **wiring**. So a
file with no README row still runs, the suite stays green, and the index rots with nothing mechanical
to report it. Each README states the contract in its own words — "a new test file is picked up
automatically by the runner. **This README is not.** Add a row below in the same PR that adds a test
file." — and nothing enforced it.

The contract had already been broken. By the time this ADR was written the tests README was missing
rows for `check-remote-read-hygiene.sh` and `test_stop_experiment_verdict_gate.py`, and its line-20
cross-reference claimed "covers 77" of 83 files. [dev-env#895](https://github.com/brownm09/dev-env/pull/895)
had corrected three stale *header* counts while adding an unrelated row, and deliberately left the
missing rows as out-of-scope drift — which is what dev-env#901 tracked. The sibling scripts README was
worse: missing a row for `stop-experiment-verdict-gate.py`, mislabeling its Engineering-journal section
`(18)` when it held 20 rows, and stating both "**79 files**" and "**76 files**" for the same 82-file
set seven lines apart. A number restated in four places (two headers plus two cross-references in
`docs/REFERENCE.md` and the scripts README) had drifted in three of them.

This is the same class [ADR-114](114-slim-testing-section-index.md) addressed for the two-file
`## Testing` split (`CLAUDE.md` ↔ `docs/TESTING.md`, gated by `test_testing_index_parity.py`, Testing
item 76): where a fact must live in two places, a cheap offline test asserts they agree. The gap was
that the *directory ↔ its own README* relationship — the more common shape — had no such gate.

## Decision

Add `claude/scripts/tests/test_readme_index_parity.py` (Testing item 84): a pure offline file parse,
auto-discovered by `run-hook-tests.py`, that checks each indexed directory against its README. It is
table-driven over a per-directory `DirSpec` (directory, README, first-column exemptions, header count
sentences), so the same code covers both READMEs and any future one is one list entry.

Six checks per directory:

1. **Row coverage** — every indexable file (a top-level `.py`/`.sh`/`.ps1`, excluding `README.md`,
   subdirectories, and the exemption set) appears in some table's **first** column. First column, not
   "mentioned anywhere": a prose cross-reference is not an index entry. A first cell may name two files
   (a shared module's paired tests) — both count.
2. **No orphan rows** — every first-column filename exists on disk (a rename or delete that updated the
   file but not its row).
3. **Exemptions are real and indexed** — each exempt file both exists and appears *somewhere* in the
   README, so an exemption can never silently hide a deletion.
4. **Header counts** — each header count sentence occurs **exactly once** and equals the live directory
   count. Exactly-once, not first-match: a stray second copy of a count phrase would otherwise let drift
   hide in the duplicate.
5. **Section counts** — every `### Title (N)` heading's N equals that section's first-column entry
   count, **and** the number of numbered headings matches a per-directory expectation, so a heading
   reworded past the `(N)` form cannot silently drop out of gating.
6. **Component completeness** — where a header breaks its total into components (the tests README's
   "N `test_*.py`, N bash gates, one module"), every indexable file matches a declared component or is
   an exemption, so a file of a new kind cannot make the components silently fail to sum to the gated
   total.

The gate is **fail-closed** throughout: a count sentence reworded past its regex fails check 4 (zero
matches), a duplicated count fails it too (more than one), and a numbered heading that loses its `(N)`
fails check 5's heading-count assertion — none of them pass on a count the gate can no longer see, the
correct direction for a gate whose whole job is catching silent drift.

**The instances are fixed in the same PR** (three missing rows added, `docs/REFERENCE.md` and the
scripts-README cross-references de-numbered, the two headers reworded, the mislabeled section corrected)
so the gate lands green over a clean tree.

### Judgment calls

**One gated count per README; cross-references go numberless.** The user chose (of "gate all count
sites" / "one gated count, cross-refs numberless" / "remove every count") the middle option. Each
README keeps one authoritative, gated count in its header; the `docs/REFERENCE.md` and cross-README
mentions become "every file in `claude/scripts/tests/`" rather than restating a number. A number that
does not exist cannot rot, and gating a count in prose the gate does not own would couple it to wording
in a file it does not check.

**Restrict indexable to `.py`/`.sh`/`.ps1`.** This matches each README's own declared scope and keeps a
stray non-script artifact from being demanded a row; both directories currently contain nothing else.

**Drop the scripts-README's `44 wired / 15 shared / 20 utility` breakdown to prose.** Those categories
are not derivable from the filesystem and do not map onto the numbered `### (N)` sections, so the gate
cannot defend them; keeping the numbers would have re-created the drift this ADR removes. The category
*names* stay; only the numbers go.

**`_hook_wiring.py` is exempt from row-coverage, not from indexing.** It is deliberately indexed in the
tests README's **second** column (with a `—` first cell) because it is test-support infrastructure, not
a test. Check 3 still requires it to appear, so the exemption cannot mask its deletion.

**A separate ADR, not an ADR-114 amendment.** ADR-114 explicitly treats `claude/scripts/tests/README.md`
as a *separate* artifact with its own declared identity ("a one-line navigational map") and rejected
merging the two Testing files into it. This gate governs a different relationship (directory ↔ README,
not file ↔ file) across two READMEs ADR-114 never mentioned, so it earns its own record.

**Own item number, appended and collision-checked.** Testing item 84 is added to **both** `CLAUDE.md`
and `docs/TESTING.md` in this PR (the ADR-114 two-file rule, itself gated by item 76);
`pre-merge-numbering-check.py` ([ADR-074](074-pre-merge-numbering-collision-check.md)) re-checks the
number against `origin/main` at merge.

## Consequences

- A file added to either directory without a README row now fails CI on the PR that adds it, closing the
  class dev-env#822 and dev-env#830 opened but could not enforce. The failure names the offending files
  and the corrected counts, so the fix is mechanical.
- The gate is **not** a proof of global consistency: it checks first-column coverage, orphan rows,
  header counts, `(N)` section counts, and component completeness — not the accuracy of any row's prose
  description, and not the `## Shared support modules` section which carries no `(N)`. It also does not
  strip fenced ` ``` ` code blocks: a future README that placed a `| `-prefixed, backticked-filename
  line inside a fence could be mis-parsed as a table row. Both READMEs contain zero code fences today,
  so this is a latent-only gap, recorded rather than pre-solved — the same call ADR-116 and ADR-121 made
  for their own markdown scanners until fence-blindness actually bit them.
- Adding a genuinely new indexed directory means one `DirSpec` entry plus its header count regex; the
  header must be worded so its numbers parse without depending on an em-dash (the reason both headers
  were reworded here).
- Declared fail direction: like every other test in the suite it simply asserts and exits non-zero on
  failure; it is not a wired hook, so the hook fail-open/closed convention does not apply.

## References

- [dev-env#901](https://github.com/brownm09/dev-env/issues/901) — this issue
- [dev-env#822](https://github.com/brownm09/dev-env/issues/822) — `claude/scripts/tests/README.md`'s origin and declared identity
- [dev-env#830](https://github.com/brownm09/dev-env/issues/830) — `claude/scripts/README.md`'s origin
- [dev-env#895](https://github.com/brownm09/dev-env/pull/895) — fixed the tests-README header counts but left the missing rows
- [ADR-114](114-slim-testing-section-index.md) — the two-file parity-gate precedent (`test_testing_index_parity.py`, Testing item 76)
- [ADR-074](074-pre-merge-numbering-collision-check.md) — merge-time Testing-item / ADR numbering-collision check
