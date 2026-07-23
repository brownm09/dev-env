# ADR-104 — Diff-and-Replay Conflict Recovery in journal-compose Step 10.5

**Date:** 2026-07-12
**Status:** Accepted (amended 2026-07-23 — see Amendment 1)
**Tags:** journal, composition, skill, git, merge-tree, conflict-recovery, diff-and-replay, three-way-merge, both-sides-changed, data-loss, silent-failure, shared-script, testing, crlf, msys, correction, adr-056, adr-080, adr-082, adr-119, adr-120

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
- An `A`/`M` status always takes `$PREV`'s content wholesale via `git checkout "$PREV" --
  <path>` — it does not attempt to reconcile against whatever `origin/main` independently holds
  at that path (most relevant for the shared top-level `README.md`, which more than one
  project's compose could touch on the same day). This is **not** new behavior — the
  pre-existing allowlist did the identical unconditional `checkout "$PREV" -- ... README.md` —
  but is worth stating explicitly here since "diff-and-replay" could otherwise read as reconciling
  concurrent edits, which it does not: for any path both sides touched, `$PREV` simply wins.
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

---

## Amendment 1 (2026-07-23) — partition the replay by whether `origin/main` also changed the path (dev-env#890)

### What the original decision got wrong

The **Trade-offs / limits** section above already named this, and named it precisely:

> An `A`/`M` status always takes `$PREV`'s content wholesale via `git checkout "$PREV" --
> <path>` — it does not attempt to reconcile against whatever `origin/main` independently
> holds at that path (most relevant for the shared top-level `README.md` …) … for any path
> both sides touched, `$PREV` simply wins.

It was recorded as an accepted limit on the grounds that the superseded allowlist did the same
thing. That reasoning holds for *parity* but not for *exposure*: the allowlist replayed three
known paths, while diff-and-replay replays **every** changed path — so generalizing the
mechanism also generalized its one unsafe case, from a corner to the default.

**Incident (dev-env#890, 2026-07-21).** The compose remediating #874 hit `CONFLICT_LINES > 0`,
and the conflict was 6 READMEs: `origin/main` had advanced by the 2026-07-20 compose
([engineering-journal PR #181](https://github.com/brownm09/engineering-journal/pull/181)), which
had edited the same README sections. Applied as written, the replay would have discarded that
day's entry rows and Progress Summary updates across all 6 files — and the result would have
been a *clean-looking* commit with no conflict, no warning, and nothing in the PR body to
suggest a published journal had just been reverted. The paths were excluded by hand and
re-applied onto `origin/main`'s versions instead
([PR #182](https://github.com/brownm09/engineering-journal/pull/182)).

The replay is right for paths only the draft branch touched — composed journals, stubs, shards,
the common case. It is wrong exactly for the paths that caused the conflict in the first place.

### Decision

Classify every path before replaying it, with one predicate:

```bash
main_touched() { [ -n "$(git -C "$WT" diff --name-only "$MERGE_BASE" origin/main -- "$1")" ]; }
```

- **Uncontested** (`main_touched` false) — replay wholesale, exactly as before. Unchanged for
  the overwhelming majority of paths.
- **Contested `M`** — 3-way merge against `$MERGE_BASE`. A clean merge is kept and reported as
  `AUTO_MERGED`; a conflict leaves `origin/main`'s content on disk untouched and reports the
  path as `MANUAL_RECONCILE`.
- **Contested `A`** — an add/add has no common ancestor to merge against, so no merge is
  attempted: `MANUAL_RECONCILE`.
- **Contested `D`** — a delete/modify. The draft branch dropped a file `origin/main` edited;
  blind-deleting it is the same silent loss in another shape. `MANUAL_RECONCILE`.
- **`D` where `origin/main` also deleted it** — a clean no-op, not a conflict.

Any non-empty `MANUAL_RECONCILE` exits **2**, and Step 10.5's prose makes the commit conditional
on exit 0. The mechanism fails closed: the failure it guards against is a commit that looks
clean, so "stop before committing" is the only safe default.

The same predicate fixes a second, latent instance of the identical bug. The shard-integrity
restore (dev-env#787) resurrected any open-PR shard present on `$PREV` but absent from the
recovery branch — including one `origin/main` had *deliberately deleted* because a concurrent
session verified that PR merged. Those are now reported as `SHARD_RESTORE_SKIPPED` instead
([ADR-119](119-day-rollover-draft-branch-and-orphaned-shard-deletions.md) is the same
resurrection failure at branch scale).

### The mechanism moves out of SKILL.md into `claude/scripts/journal-compose-replay.sh`

Step 10.5 carries the replay **twice** — a single-project and a multi-project copy that differ
only in their pathspec list and must be kept in sync by hand. Adding ~40 lines of classification
and 3-way-merge plumbing to both would have tripled the drift surface, and this ADR's own
Trade-offs section closes by conceding that no test exists for the mechanism *because* it is
bash embedded in a markdown skill.

Both copies now call one script; the pathspecs remain the only difference. It follows the
extraction precedent this same skill already set with `journal-compose-force-resolve.py` (Step
0.6), and is covered by `## Testing` item 83 —
`claude/scripts/tests/test-journal-compose-replay.sh`, which drives the real script against
fixture repos with no network and no `gh`.

The script derives `MERGE_BASE` from `$PREV` itself rather than inheriting it. That also closes
a latent gap: SKILL.md's push-failure rule routes a pre-push merged-draft-branch rejection
straight to the recovery block and says to *skip the merge-tree probe*, which is the only place
the skill computes `$MERGE_BASE` — so on that route it was unset.

### Two implementation traps the extraction surfaced

Both were caught by the new test on its first run, and neither is visible in review of a
markdown snippet — which is itself part of the argument for extracting the mechanism.

1. **Never merge a blob against a work-tree file.** `git show` emits stored content (LF); the
   checked-out file carries whatever the smudge filter produced, which under the `core.autocrlf`
   this machine runs is CRLF. Mixing the two makes *every* line differ, so a trivially disjoint
   merge conflicts. All three sides are read as blobs — `origin/main`'s included, which is also
   the authoritative content, since the recovery branch was just cut from it and nothing has
   touched the path yet. The test sets `core.autocrlf true` on every fixture, on every platform,
   so this stays pinned rather than being a Windows-only accident.
2. **`MSYS_NO_PATHCONV=1` is all-or-nothing per command.** It is required to protect the
   `<ref>:<path>` argument ([ADR-120](120-review-skill-absence-checks-over-api.md),
   dev-env#602/#877) — but applied to `git -C "$WT" show …` it equally stops `-C`'s own path
   from being translated, and git then cannot find the repo at all (`fatal: cannot change to
   '/tmp/…'`). The guard is scoped inside a subshell that `cd`s instead — `cd` is a bash builtin
   and needs no translation — so the ref argument is protected without changing how git locates
   the repo. stderr is deliberately not suppressed, per the same ADR.

### Consequences

**Positive:**
- The failure this step could produce that was worst *because* it was silent — a clean commit
  reverting a published journal — now cannot happen without an explicit human decision.
- One implementation instead of two hand-synced copies, and the first real test coverage of a
  path that only executes during conflict recovery (i.e. rarely, and under time pressure).
- `BOTH_CHANGED` lands in the Step 11 PR body beside `RECONCILED_SHARDS`, so a both-sides
  reconciliation is visible on the PR even when it merged cleanly.

**Trade-offs / limits:**
- A clean auto-merge is still a machine decision, not a reviewed one. It is reported rather than
  silent, which is the mitigation; the alternative (stop on *every* contested path) would make
  hand-reconciliation of the shared `README.md` mandatory on nearly every recovery run.
- Exit 2 means the recovery is no longer fully mechanical: someone must reconcile before the
  commit. That is the point — but a conflict-recovery compose can now stop midway, where
  previously it always ran to completion (incorrectly).
- The caller-side half of the contract — that Step 10.5 actually honors exit 2 before
  committing — is prose in the skill, not code, and stays unenforced. Same limit this ADR
  already accepted for bash embedded in a markdown skill.
- `git diff --name-status` output is still not `-z`-delimited: a path containing a tab or
  newline would break the parse. Unchanged, pre-existing, and impossible for this skill's own
  filenames.

**Superseded:** the note above about `.compose-diff-plan.txt` living inside `$WT` ("planning
state internal to this recovery step … never staged or committed"). The plan is now read into a
bash array, so no scratch file is written into the compose worktree at all; the per-path temps
the 3-way merge needs hold file *content* rather than a path list, and live in the scratch
directory per the global CLAUDE.md convention. Where that fixed path does not exist — every CI
run, which executes as a different user — the script makes its own `mktemp -d` and the `EXIT`
trap `rmdir`s it, so the fallback cannot leak a directory per invocation on the one path that
always takes it. `JOURNAL_COMPOSE_REPLAY_SCRATCH` overrides the scratch root; it exists solely so
that fallback branch is reachable from the test suite on a machine where the fixed path *does*
exist, and the real invocation never sets it.

### References

- Issue: [dev-env#890](https://github.com/brownm09/dev-env/issues/890) — this amendment.
- Incident artifacts: engineering-journal
  [PR #181](https://github.com/brownm09/engineering-journal/pull/181) (the 2026-07-20 compose
  whose work would have been reverted) and
  [PR #182](https://github.com/brownm09/engineering-journal/pull/182) (the hand-reconciled result).
- [git-merge-file documentation](https://git-scm.com/docs/git-merge-file) — `-p`, and the
  conflict-count exit status.
- [ADR-119](119-day-rollover-draft-branch-and-orphaned-shard-deletions.md) — shard resurrection,
  the same class the `SHARD_RESTORE_SKIPPED` guard closes at file scope.
- [ADR-120](120-review-skill-absence-checks-over-api.md) — the MSYS `<ref>:<path>` mangle, and
  why a suppressed `fatal:` reads as an empty file.
- `claude/scripts/journal-compose-replay.sh`,
  `claude/scripts/tests/test-journal-compose-replay.sh` (`## Testing` item 83),
  `claude/skills/journal-compose/SKILL.md` → Step 10.5, Step 11.
