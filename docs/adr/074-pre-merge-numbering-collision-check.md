# ADR 074 — Pre-Merge Numbering-Collision Check for CLAUDE.md Testing Section and ADR INDEX

**Date:** 2026-07-02
**Status:** Accepted
**Tags:** hooks, pre-tool-use, merge, workflow, numbering, concurrency, claude-md, adr-index, global-rule

---

## Context

`CLAUDE.md`'s `## Testing` section and `docs/adr/INDEX.md`'s ADR table are both hand-numbered
sequential lists that many concurrent PRs/worktrees append to. Each PR picks "the next number"
from its own branch's snapshot of the file. When two branches cut around the same time both pick
the same number, git accepts the second PR's insertion as a clean three-way merge — it never
touches the first PR's lines, so there is no textual conflict, only a silent duplicate number.

**Concretely, on 2026-07-02:** PR #506 added Testing item "34" on its branch. While #506 sat in
review, PR #479 and PR #507 both merged first and claimed items 34 and 35 respectively. #506 had
to be rebased and its item manually renumbered to 36 mid-session (dev-env#516).

**The race is at merge time, not authoring time.** #506's branch was internally self-consistent
when written (items 1–34, no dupes, no gaps). The collision was introduced by *other* PRs merging
while #506 sat in review — after PR-open time. A convention of "check origin/main immediately
before `gh pr create`" would not have prevented this incident.

**This is not a one-off.** `git log --oneline --all -i --grep=renumber -- CLAUDE.md docs/adr/`
shows the identical pattern has hit `docs/adr/INDEX.md`'s ADR numbering at least seven times:

- `d5b174f` — renumber ADR-067 to ADR-068 (collision with #448)
- `465716c` — renumber worktree-safety ADR 065 -> 066 (065 claimed by merged #443)
- `15a035c` — renumber ADR from 063 to 064 (063 taken by always-plan-rule, PR #433)
- `b38a850` — renumber sharding ADR 055 -> 056
- `836f843` — renumber ADR-052 -> 053 (052 claimed by concurrent open PR #386)
- `044c98e` — renumber ADR-041 -> ADR-042 (number collision with concurrent PR #326)
- `2e0a5f1` — renumber ADR to 014

Each ADR renumber is *more* expensive than a Testing-item renumber: an ADR number is
cross-referenced in roughly five files per incident (`CLAUDE.md`, `README.md`,
`docs/REFERENCE.md`, the ADR's own filename, `docs/adr/INDEX.md`). A repo-wide grep confirms
Testing-item numbers are never referenced anywhere outside the list itself — a Testing-item
renumber only ever touches `CLAUDE.md`.

Two existing PreToolUse hooks already gate `gh pr merge` for analogous reasons —
`pre-merge-findings-gate.py` (ADR-028/ADR-039) and `pre-merge-message-check.py` (ADR-061) — both
converting a prose rule into a mechanical, fail-open check at the merge checkpoint. `_hookio.py`
already exposes `effective_merge_dir()` (ADR-067) for scoping a hook to the actual merge-target
repo through a `cd <repo> && gh pr merge` chain, which this check needs since hooks are wired
globally in `~/.claude/settings.json` but the two files being checked are dev-env-specific.

## Decision

1. **New `claude/scripts/pre-merge-numbering-check.py`** — a PreToolUse hook mirroring its two
   siblings' structure and philosophy:
   - Detects a top-level `gh pr merge` (same `_GH_PR_MERGE_RE` convention as the two siblings)
     and resolves the merge directory via `effective_merge_dir()`.
   - Scopes to the dev-env repo only, via a new `is_dev_env_repo()` check on the origin remote
     URL — every other repo is a silent no-op.
   - Runs `git fetch origin main`, then reads three snapshots of both `CLAUDE.md` and
     `docs/adr/INDEX.md`: the merge-base, this branch's `HEAD`, and `origin/main`.
   - A number this branch newly introduces (absent at the merge-base) that `origin/main` has
     *also* claimed since the branch point is a genuine collision -> **block the merge** (exit 2),
     naming both colliding lines and the fix (rebase, renumber, re-run). An item present at the
     merge-base whose text was independently edited on both sides is never flagged — that is an
     edit, not a fresh claim.
   - A sequencing gap in the resulting union (no collision, just non-contiguous — e.g. a
     legitimate item deletion) is advisory only via a `systemMessage`, never blocking.
   - Fails open on any git/network/parse error, exactly like its two siblings: this check must
     never wedge a legitimate merge on its own failure.

2. **Checks both known hot spots, not just the one in the triggering incident.** The ADR-INDEX.md
   collision history (seven incidents, listed above) is proof this problem recurs there at least
   as often as in the Testing section, and costs more per incident. One extractor function per
   file format (`extract_testing_numbers` for the numbered-list convention,
   `extract_adr_numbers` for the markdown-table convention), both feeding the same
   `find_new_collisions` / `find_gaps` core.

3. **Pure/impure split, matching every other hook in `claude/scripts/`.** `extract_section`,
   `extract_testing_numbers`, `extract_adr_numbers`, `is_dev_env_repo`, `find_new_collisions`,
   `find_gaps`, `find_new_gaps`, `format_block_message`, and `is_pr_merge_command` are pure
   functions, unit-tested offline with no subprocess/network/disk in
   `tests/test_pre_merge_numbering_check.py`. Unlike `pre-merge-message-check.py` (whose `main()`
   never shells out to git and so has no such layer), this hook's git/subprocess orchestration —
   the actual point of the review-driven revisions below — is exercised by a second, end-to-end
   test layer: the real `main()` driven over stdin via subprocess against throwaway git repos
   (a bare "origin" plus independent clones, mirroring `test_canonical_mutate_guard.py`'s Layer 2
   pattern), proving a genuine cross-branch collision is discovered only because the hook's own
   `git fetch` pulls in a competing branch's already-pushed commit.

4. **`CLAUDE.md`'s own Testing section gets a pointer note** (not a new prose rule) near its
   header, naming this hook and the fix procedure — since the check is now mechanically enforced,
   the note exists for human/agent orientation, not as the enforcement mechanism itself.

## Considered alternatives

- **A pre-PR-create check instead of (or in addition to) a pre-merge check.** Rejected as the
  *sole* mechanism: the #506 incident proves the race is at merge time — #506 was self-consistent
  when its PR was opened, and the collision was created by two *other* PRs merging during its
  review window. A pre-PR-create check cannot see a collision that doesn't exist yet. A pre-merge
  check is the only point that is authoritative, because it is the last chance before the write
  actually lands. (A pre-PR-create early-warning variant remains a reasonable future addition —
  see Consequences — but is not required to fix the actual failure mode.)
- **A prose reminder to "recheck origin/main before merging," with no mechanical enforcement.**
  Rejected: the seven ADR-renumber incidents in git history occurred despite the ADR-warrant
  checkpoint discipline already asking sessions to consult `docs/adr/INDEX.md` before writing an
  ADR. Prose-only reminders have already demonstrably failed to prevent this exact failure mode
  seven times; ADR-039 established the precedent of converting a failing prose rule into a
  mechanical gate for the identical reason.
- **Auto-renumbering instead of blocking.** Rejected: silently rewriting the colliding item's
  number could reorder content the author didn't expect, and (for the ADR case specifically)
  would need to rewrite the ADR's own filename and every cross-reference to it in the same
  automated step — a much larger blast radius than a block-and-instruct message, for a check that
  fires rarely enough that a manual rebase-and-renumber is not a meaningful burden.
  Auto-renumbering could still be considered later as a convenience layered on top of the block.
- **Rendering the numbered lists with a single repeated `1.` prefix so Markdown/HTML
  auto-increments the visible number, eliminating the possibility of a source-level duplicate
  entirely** (a known technique — see
  [markdownlint MD029 `ol-prefix: one`](https://github.com/DavidAnson/markdownlint/blob/main/doc/md029.md)).
  Rejected: `CLAUDE.md` is read as raw text by Claude Code sessions far more often than it is
  viewed as rendered HTML on GitHub, and both files are also read as raw text by ADR
  cross-references (`ADR-052`, `Testing item 12`, etc.) elsewhere in this repo. Collapsing the
  source numbers to a repeated `1.` would make every raw read and every existing cross-reference
  meaningless, trading a rare merge-time collision for a permanent readability regression in the
  primary consumption path. Does not apply to `docs/adr/INDEX.md` at all — ADR numbers are
  load-bearing identifiers baked into filenames, not just list-display order.
- **Restructure to a per-item frontmatter/manifest instead of a single shared numbered list or
  table.** Not pursued: a strictly larger structural change than either file's actual failure
  mode requires, and neither candidate above proved insufficient.

## Consequences

- Every future `gh pr merge` in dev-env pays one `git fetch` and up to six `git show` calls
  (two files × three refs) before merging — comparable in cost to the existing sibling merge
  hooks, one of which already makes a live `gh pr view` network call on every merge.
- A colliding PR must rebase onto `origin/main` and renumber before it can merge — this is the
  same manual fix already applied by hand in all eight precedent incidents (the #506 Testing item
  plus seven ADR renumbers), now surfaced mechanically and immediately instead of discovered by a
  human noticing a duplicate after the fact.
- This hook is dev-env-scoped and is a no-op in every other repo, so it adds no overhead to
  merges in lifting-logbook, career-playbook, or any other project.
- A pre-PR-create early-warning variant of this check (catching *some* collisions earlier, though
  never all of them, per the Considered Alternatives discussion) is a reasonable, small follow-up
  if collisions at PR-open time turn out to be common enough to be worth the extra check; not
  built here since the pre-merge check alone is sufficient to prevent every collision from
  actually landing.

## References

- [Issue #516](https://github.com/brownm09/dev-env/issues/516) — the #506/#479/#507 Testing-item
  collision and the seven-incident ADR-renumber history that motivated this ADR.
- [ADR-028](028-all-findings-merge-gate.md), [ADR-039](039-merge-gate-findings-enforcement.md) —
  `pre-merge-findings-gate.py`, the precedent for converting a prose merge-time rule into a
  mechanical, fail-open gate.
- [ADR-061](061-pre-merge-message-queue.md) — `pre-merge-message-check.py`, this hook's closest
  structural sibling (same stdin shape, same PreToolUse merge-gate role). Its `main()` never
  shells out to git, so it has no equivalent to this hook's end-to-end git-orchestration test
  layer — the two hooks share a shape, not a test-coverage boundary.
- [ADR-067](067-scope-merge-keyed-hooks-to-target-repo.md) — `effective_merge_dir()`, reused here
  to scope this check to the actual merge-target repo through a `cd`-chain.
- [ADR-004](004-pr-review-reads-from-remote.md) — the established "read from remote, not local
  worktree" principle this check's `git fetch origin main` step extends to a pre-merge numbering
  check.
- `claude/scripts/pre-merge-numbering-check.py`, `claude/scripts/tests/test_pre_merge_numbering_check.py`.

## Amendment 1 (2026-11-06): new tests take no number, so they can't conflict

**Trigger.** [dev-env#1309](https://github.com/brownm09/dev-env/issues/1309). This check stops a bad
merge, but it doesn't reduce the conflicts or the renumbering behind them. On 2026-11-06
[PR #1276](https://github.com/brownm09/dev-env/pull/1276) took item 114, then 115, then 116, and
finished as 117, while #1284 and #1304 claimed the same numbers. Each round meant a merge, a
renumber, a change to the test README's counts, a PR body edit and a CI restart.

**Corrections to the context above.** Two PRs appending at the end of the list do conflict; the
clean merge and silent duplicate described above need different insertion points. Testing numbers
are also cited outside the list now, for example `dev-env-sync.py` cites "Testing item 68", so they
can't be renumbered.

**Decision.** The numbered list stops at item 117, and those items keep their numbers. A new test
edits only its own file: a `Required when ...` line and a `Run: ...` line in its first 10 lines, with
what it pins and its scope gaps below them in the same docstring or comment. It adds nothing to
`CLAUDE.md`, `docs/TESTING.md` or `claude/scripts/tests/README.md`, and it's cited by file name.
The README loses its header counts, because every new test changed them.

Two gates hold this, both in CI:

- `test_testing_index_parity.py` (item 76) refuses any item past `LAST_NUMBERED_ITEM`. That constant
  is 117, the last number on `origin/main` when the list was frozen. The check is exact, so there's
  no margin to tune. Known good: `main`. Known bad: an added item 118, as
  [PR #1284](https://github.com/brownm09/dev-env/pull/1284) has.
- `test_readme_index_parity.py` (item 84) requires each file in `claude/scripts/tests/` to have a row
  or its own entry. Known good: `test_testing_index_merge.py`. Known bad: a test with neither, which
  that same test builds and expects to fail.

This pre-merge check stays. It still covers the ADR table, which is unchanged, and it still catches a
Testing number that reaches a merge. Its message now tells a Testing collision not to renumber.

**Alternatives rejected.**

- **A custom merge driver.** A driver is defined in
  [`.git/config`, not in `.gitattributes`](https://git-scm.com/docs/gitattributes#_defining_a_custom_merge_driver),
  so a repository can't carry it to GitHub's merge.
- **`merge=union` on the three files.** This built-in removes the conflict but not the duplicate: both
  PRs still claim 118, so this check still blocks one and it still renumbers. The attribute also
  covers the whole file, so two edits to the same line anywhere in `CLAUDE.md` would keep both sides
  with no conflict, and the
  [Git documentation](https://git-scm.com/docs/gitattributes#_built_in_merge_drivers) warns that the
  result needs checking. Whether GitHub honours it wasn't tested, since these reasons already rule it
  out.
- **Different numbers in the same files**, such as the issue number, or no number. The insertions
  still land at the same point, so they still conflict. Sorting by name only makes that less frequent.
- **One small file per item**, such as `docs/testing/<test>.md`. This also can't conflict, but it adds
  a second file per test and a gate to keep the two matched. The test's docstring already does that
  job.

**GitHub's merge.** GitHub merges with
[`merge-ort` and no working tree](https://github.blog/engineering/infrastructure/scaling-merge-ort-across-github/),
so the choice has to work without local configuration. Branches that each add a different file have
no text to merge, so this doesn't depend on attributes or a driver. This was checked on 2026-11-06
through the [merge-a-branch endpoint](https://docs.github.com/en/rest/branches/branches#merge-a-branch),
on throwaway refs since deleted, cut from this change:

| # | Branches, in merge order | First merge | Second merge |
|---|---|---|---|
| 1 | New test A, then new test B | 201 Created | 201 Created |
| 2 | New test B, then new test A | 201 Created | 201 Created |
| 3 | Numbered item A, then numbered item B | 201 Created | 409 Conflict |

Row 3 is the known-bad reference, showing the endpoint reports a conflict for the old way.
`test_testing_index_merge.py` repeats the check on every CI run with `git merge`, and with
`git merge-tree` in a bare clone.

**Consequences.**

- `CLAUDE.md` loads in every session, and it no longer lists every test. A session finds a newer test
  with `git grep -l <script-name> -- claude/scripts/tests claude/hooks/tests`. The list stops growing
  in the always-loaded file, which is the direction [ADR-114](114-slim-testing-section-index.md) set.
- An open PR that added item 118 fails item 76 once this merges, and the failure says what to change.
- Adding a script still conflicts in `claude/scripts/README.md`, on its counts and rows. That's
  tracked in [dev-env#1311](https://github.com/brownm09/dev-env/issues/1311).
- To reverse this: revert the change, then give each test added since then a number and a row.
