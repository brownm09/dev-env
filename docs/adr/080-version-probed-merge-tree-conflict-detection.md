# ADR-080 — Version-Probed `merge-tree` Conflict Detection in journal-compose Step 10.5

**Date:** 2026-07-03
**Status:** Accepted
**Tags:** journal, composition, skill, git, merge-tree, conflict-detection, false-negative, windows, correction

---

## Context

Step 10.5 of `claude/skills/journal-compose/SKILL.md` decides whether the day's
`draft/YYYY-MM-DD` branch can merge cleanly into `origin/main` (PR head = the draft branch) or
whether the composed files must be cherry-picked onto a clean `compose/YYYY-MM-DD` branch.
Detection was:

```bash
CONFLICT_LINES=$(git -C ... merge-tree "$MERGE_BASE" HEAD origin/main | grep -c "^<<<<<<<" || true)
```

The installed git (2.37.1.windows.1) predates git 2.38's rewritten `merge-tree`, so this runs
the old-style ("trivial merge") 3-argument mode. That mode renders each merged file as a
*diff-style preview*: content lines carry a leading `+`/`-`/space, so conflict markers appear as
`+<<<<<<< .our`, never at column 0. The `^<<<<<<<` anchor therefore matches nothing —
`CONFLICT_LINES` was 0 unconditionally, and the check was a silent no-op that always selected
the draft branch as the PR head.

Incident (2026-07-03): engineering-journal
[PR #150](https://github.com/brownm09/engineering-journal/pull/150) (head `draft/2026-07-02`)
passed Step 10.5 with 0 conflicts, but the branch genuinely conflicted with the just-merged
PR #149 (`README.md`, `sessions/career-playbook/README.md`). GitHub reported the PR
`CONFLICTING`; the merge failed and recovery required a manual merge of `origin/main` into the
branch. A CONFLICTING PR also suppresses `pull_request` CI entirely (global CLAUDE.md → "CI not
firing — merge conflict silences GitHub Actions"), compounding the confusion.

A false negative here is the *expensive* direction: a false positive merely routes composition
through the existing compose-branch recovery (a valid PR, slightly more work), while a false
negative ships a broken PR discovered only at merge time.

---

## Decision

Replace the single anchored-grep pipeline with a version-probed, two-path check in Step 10.5:

1. **Probe modern merge-tree first.** Run `git merge-tree --write-tree HEAD origin/main` and
   read the exit code: 0 = clean → `CONFLICT_LINES=0`; 1 = conflicts → `CONFLICT_LINES=1`. The
   modern form's exit status is defined as 0 for a clean merge and 1 for conflicts
   ([git-merge-tree documentation](https://git-scm.com/docs/git-merge-tree)). Exit-code
   semantics are immune to content-based false positives/negatives entirely.
2. **Fall back on old git.** Any other exit code (git < 2.38 rejects `--write-tree`; 2.37 dies
   with a usage error, never 0 or 1) falls back to the deprecated 3-argument mode with
   `grep -cE "^\+?<<<<<<<"`.

Why the fallback pattern is `^\+?<<<<<<<` and not:

- **`^<<<<<<<` (the bug):** never matches old-style output — markers there are always
  `+`-prefixed.
- **Unanchored `<<<<<<<`:** also matches journal *content* that merely mentions markers
  mid-line — which the journal now durably contains, since this incident's own entry quotes
  `"+<<<<<<< .our"` — producing avoidable false positives on clean merges. Real conflict
  markers in old-style merge-tree output always sit at line start with exactly one `+`;
  `^\+?` keeps them while ignoring mid-line mentions.

Residual false positive: a fenced code block quoting a marker at column 0 becomes `+<<<<<<<`
in the diff preview and is indistinguishable from a real marker by any text pattern. Accepted:
it fails in the safe direction (compose-branch recovery), and the modern exit-code path
eliminates the whole class once git is upgraded past 2.38 — the probe makes that upgrade
self-activating with no skill edit at upgrade time.

---

## Consequences

**Positive:**
- Real draft-branch conflicts are detected again on the installed git; the incident class
  (CONFLICTING PR at merge time + silenced CI) is closed.
- On any future git >= 2.38, detection switches automatically to precise exit-code semantics.
- `CONFLICT_LINES` keeps its name and its `0` / `> 0` contract, so the step's downstream prose
  (PR_HEAD selection, recovery procedure, multi-project note) is untouched.

**Trade-offs / limits:**
- On the modern path `CONFLICT_LINES` is a 0/1 flag, not a marker count — acceptable because
  the step only ever tests zero vs non-zero.
- `merge-tree --write-tree` writes tree/blob objects to the object database; harmless and
  gc-able.
- The modern path cannot be exercised on this machine today (git 2.37) — verification covers
  only that the probe exits with rc ∉ {0,1} and correctly selects the fallback.
- rc=1 is trusted as "conflicts" without distinguishing exotic gits that might exit 1 for other
  reasons; a misclassification degrades to the safe compose-branch recovery.

---

## Alternatives Considered

**Drop the anchor only (`grep -c "<<<<<<<"`), single code path.** Simplest correct-today fix,
but permanently exposed to mid-line-mention false positives in a repo whose content is *about*
engineering incidents (including this one), and it leaves the check tied to a deprecated mode
git may eventually remove. Rejected for the probe + `^\+?` anchor, which subsumes it.

**Assume modern git (>= 2.38) and use `--write-tree` unconditionally.** Cleanest semantics but
breaks the skill outright on the installed 2.37 — the check would error on every compose run
until git is upgraded. Rejected; the probe reaches the same end state without a flag day.

**Parse old-style merge-tree's section structure ("changed in both" + conflict stages) instead
of grepping markers.** More faithful to the old format but substantially more parsing logic
inside a skill snippet, still tied to a deprecated mode, and unnecessary once the modern path
exists. Rejected as disproportionate.

---

## References

- [git-merge-tree documentation](https://git-scm.com/docs/git-merge-tree) — modern
  `--write-tree` exit-status semantics; deprecated 3-argument mode.
- Incident: [engineering-journal PR #150](https://github.com/brownm09/engineering-journal/pull/150).
- Issue tracking this fix: [dev-env#550](https://github.com/brownm09/dev-env/issues/550).
- Related: [ADR-017](017-journal-compose-today-guard.md) — journal-compose guard lineage;
  global CLAUDE.md → "CI not firing — merge conflict silences GitHub Actions".
- `claude/skills/journal-compose/SKILL.md` → Step 10.5.
