# ADR-104 — Diff-and-Replay Conflict Recovery in journal-compose Step 10.5

**Date:** 2026-07-12
**Status:** Accepted
**Tags:** journal, composition, skill, git, merge-tree, conflict-recovery, diff-and-replay, correction, adr-080, adr-082, adr-056

---

## Context

When Step 10.5 of `claude/skills/journal-compose/SKILL.md` detects that the day's draft branch
cannot merge cleanly into `origin/main` (`CONFLICT_LINES > 0`), it recovers by moving the compose
worktree onto a fresh `compose/YYYY-MM-DD` branch cut from `origin/main`, then re-applying just
the day's composed output onto it. Before this change, "re-applying the composed output" was a
hand-maintained allowlist:

```bash
git -C "$WT" checkout "$PREV" -- \
  sessions/<project>/YYYY-MM-DD-<slug>.md \
  sessions/<project>/README.md \
  README.md
[ -f "$WT/sessions/<project>/open-prs.jsonl" ] && git -C "$WT" checkout "$PREV" -- sessions/<project>/open-prs.jsonl
[ -d "$WT/sessions/<project>/open-prs" ] && git -C "$WT" checkout "$PREV" -- sessions/<project>/open-prs
# ...then a hand-derived loop re-removing the exact PR-shard numbers Step 9.5 already reconciled
```

`git checkout <commit> -- <path>` only *adds or updates* files present in `<commit>`; it never
deletes a file that exists in the working tree but is absent from `<commit>`. Since the fresh
`compose/YYYY-MM-DD` branch starts from `origin/main` — which still has the *stale*,
not-yet-reconciled `open-prs/<N>.json` shards Step 9.5 already verified-and-removed on the draft
branch — every deletion the day's sessions made had to be re-derived and re-applied by hand as a
second, parallel bookkeeping step ("2b").

**Incident (dev-env#684, closed 2026-07-09):** exactly this gap resurrected open-PR shards
already deleted on the source branch, fixed only as a one-off manual recovery (engineering-journal
PRs [#159](https://github.com/brownm09/engineering-journal/pull/159)/[#160](https://github.com/brownm09/engineering-journal/pull/160)).
The fix patched that one incident, not the mechanism — the allowlist still knew about exactly two
deletable classes (`open-prs.jsonl`, `open-prs/*.json`) and nothing else. Any other file class a
session might legitimately delete on the draft branch — a stub, a manifest shard, a future
`sessions/<project>/reports/*.md` (per global CLAUDE.md's report/analysis journal trigger), or
anything not yet invented — would silently reappear on `compose/YYYY-MM-DD` the same way, because
nothing in the recovery path *asks what the draft branch actually deleted*. dev-env#742 was filed
to close the systemic gap, not just re-run the #684 fix for a new file class.

---

## Decision

Replace the allowlist with a genuine diff-and-replay: compute what the draft branch actually
added, modified, or deleted relative to the point it diverged from `origin/main`, and replay
exactly that onto the fresh compose branch.

```bash
git -C "$WT" diff --no-renames --name-status "$MERGE_BASE" "$PREV" -- \
    "sessions/<project>/" README.md > "$WT/.compose-diff-plan.txt"
while IFS=$'\t' read -r STATUS FILEPATH; do
  case "$STATUS" in
    A|M) git -C "$WT" checkout "$PREV" -- "$FILEPATH" ;;
    D)   [ -e "$WT/$FILEPATH" ] && git -C "$WT" rm --quiet -- "$FILEPATH" ;;
    *)   echo "WARNING: unhandled diff status '$STATUS' for $FILEPATH — inspect manually" ;;
  esac
done < "$WT/.compose-diff-plan.txt"
rm -f "$WT/.compose-diff-plan.txt"
```

- `$MERGE_BASE` is already computed by the Step 10.5 conflict probe (ADR-080) as
  `git merge-base HEAD origin/main` — the exact point the draft branch diverged.
- `$PREV` is the draft branch's tip (captured before the branch switch), i.e. the cumulative net
  effect of every session's commits that day: stub/manifest adds *and* deletes, shard adds *and*
  Step 9.5's reconciliation deletes, and the just-committed composed output.
- `A`/`M` replay as a `checkout <path-from-PREV>` (same effect as the old allowlist's checkouts).
- `D` replays as a `git rm` — the mechanism the old allowlist had no equivalent for outside its
  two hardcoded open-PR-shard cases. This is what closes the #684 class generally: any file the
  draft branch deleted, of any kind, under the composed project's `sessions/<project>/` tree
  (including a future `reports/` subdirectory) or the top-level `README.md`, is deleted again on
  the compose branch — with zero code that has to know the file's specific role.
- `--no-renames` keeps the status column to plain A/M/D. A rename on the draft branch (not a
  pattern this skill currently produces, but not impossible) surfaces as a D of the old path plus
  an A of the new one — both cases the loop already handles correctly, so no R-specific branch is
  needed.
- The scratch file `.compose-diff-plan.txt` is written inside `$WT` and removed immediately after
  the loop — it is planning state internal to this recovery step, not a work-tree file, and
  is never staged or committed.

The multi-project recovery path (Multi-project mode, Step 10.5) gets the identical treatment,
scoped across every composed project's directory in one diff pathspec instead of per-project
hand-maintained lists.

`RECONCILED_SHARDS` (Step 9.5's list of reconciled PR numbers, still needed for the Step 11 PR
body and the post-merge shard-leak check) is unaffected — those checks describe *what was
reconciled*, independent of *how the recovery path re-applies it*.

---

## Consequences

**Positive:**
- Closes dev-env#742: the recovery path no longer needs a hand-maintained list of "known
  deletable file classes." A new file class (e.g. `sessions/<project>/reports/`) is handled
  automatically the moment a session deletes/moves one — no SKILL.md edit required.
- Removes the "2b" step's own correctness burden: no separately re-deriving "the exact PR numbers
  this run's Step 9.5 removed" a second time; the diff already reflects it.
- The `*` case in the replay loop's `case` statement is a visible safety net: an unexpected git
  diff status (e.g. `C` for copy, which `--no-renames` does not suppress) prints a warning instead
  of being silently dropped or silently mis-replayed.

**Trade-offs / limits:**
- The diff is pathspec-scoped to the composed project's own `sessions/<project>/` tree (or, in
  multi-project mode, every composed project's tree) plus `README.md` — deliberately, so a stray
  unrelated file elsewhere in the repo touched by some other mechanism is never swept in. This
  means a legitimate deletion *outside* those paths (there is currently no such case in this
  skill's own write surface) would not be replayed; this mirrors the old allowlist's implicit
  scoping and is not a regression.
- `git diff --name-status` output is not `-z`-delimited, so a path containing a literal tab or
  newline would break the `IFS=$'\t' read` loop. This skill's own filenames (session stubs,
  manifests, shards, composed journals, READMEs) never contain either character; accepted as a
  pre-existing, unchanged constraint (the old allowlist's own paths were equally exposed if such a
  filename existed).
- No dedicated test exists for this mechanism — it is bash embedded in a markdown skill file, not
  a `claude/scripts/*.py` hook, so it falls outside the ADR-103-era hook test suite
  (`claude/CLAUDE.md` → `## Testing`). Verification is the same as for the rest of Step 10.5:
  manual dry-run reasoning plus observation on the next real conflict-recovery invocation (as
  ADR-080's own "cannot be exercised on this machine today" note already accepts for its own
  modern-git path).

---

## Alternatives Considered

**Keep the allowlist, just add `sessions/<project>/reports/` as a third hardcoded class.** Matches
the #684 fix's own precedent exactly, but reproduces the identical bug for the *next* file class
that gets invented — which is precisely what dev-env#742 was filed to stop. Rejected.

**`git cherry-pick $PREV` onto the compose branch instead of a file-level diff.** Cherry-pick
replays the *commit*, which would attempt to reapply Step 10's commit specifically — but Step
10.5 recovery exists precisely because that commit's parent context (the draft branch's full
history back to `$MERGE_BASE`) doesn't apply cleanly against `origin/main`'s tree; a cherry-pick
of just the last commit would hit unrelated conflict markers in the same files this mechanism
exists to avoid conflicting on. Rejected — a plain diff between two known-good trees
(`$MERGE_BASE` and `$PREV`) sidesteps three-way-merge conflict resolution entirely, replaying a
net change rather than a patch.

**`git diff ... | git apply` (patch-based replay) instead of per-file `checkout`/`rm`.** Would
also work and is arguably more idiomatic for "replay a diff," but `git apply` can itself fail on
context-line mismatches if the target tree already differs slightly (e.g. a concurrent late
edit to `README.md` on `origin/main`) — the same false-negative risk ADR-080 already flagged as
the expensive failure direction for this step. The `checkout <path-from-PREV>` /
`rm` pair used instead is a *reconstruction*, not a patch application: it fully overwrites each
touched path from the known-good `$PREV` tree, so it cannot fail on a stale hunk context — worst
case it takes the entire file from `$PREV`, matching Step 10's already-established assumption
that the composed content in `$PREV` is authoritative for that path. Rejected in favor of the more
robust reconstruction.

---

## References

- Issue: [dev-env#742](https://github.com/brownm09/dev-env/issues/742) — this fix.
- Prior narrow fix: [dev-env#684](https://github.com/brownm09/dev-env/issues/684) (closed
  2026-07-09) — the one-off manual recovery this ADR generalizes past.
- Related prior incident: [dev-env#672](https://github.com/brownm09/dev-env/issues/672).
- [git-diff documentation](https://git-scm.com/docs/git-diff) — `--name-status` output format.
- [ADR-080](080-version-probed-merge-tree-conflict-detection.md) — computes `$MERGE_BASE`, the
  divergence point this diff is anchored to.
- [ADR-082](082-journal-compose-worktree-isolation.md) — the isolated compose worktree this
  recovery path operates inside.
- [ADR-056](056-per-session-sharding-journal-companion-files.md) — the per-session/per-PR shard
  scheme whose deletions (Step 9.5's reconciliation) this mechanism now replays generically.
- `claude/skills/journal-compose/SKILL.md` → Step 9.5, Step 10.5.
