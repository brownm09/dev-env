# ADR-136: The `/review` Skill's Doc-Reconciliation Check Classifies by File Status, Not Path Glob

**Date:** 2026-08-16
**Status:** Accepted
**Tags:** skills, review, documentation, doc-reconciliation, false-positive, changetype, gh-pr-view, classification, correction, claude-md, adr-004, adr-011, adr-120

---

## Context

Dev-env's root `CLAUDE.md` "Documentation Maintenance" table requires a `README.md`/
`docs/REFERENCE.md` update for exactly **seven** conditions: add/remove/rename a skill, a hook
script, a utility script, or a routine; change `hook-config.json`'s schema; change a skill's
invocation syntax; or rename/move a file linked from either doc. A PR that only edits an
*existing* skill's internal prose or step logic — no add/remove/rename, no invocation-syntax
change — matches none of the seven rows, and the table requires nothing of it.

The `/review` skill's Step 2b (Documentation Reconciliation Check) enforced a coarser rule than
that table: if any changed file's *path* matched `claude/skills/**`, `claude/hooks/**`,
`claude/scripts/**`, or `claude/routines/**`, and neither `README.md` nor `docs/REFERENCE.md`
appeared among the changed files, it recorded a **blocking** Documentation finding — regardless
of whether the file was added, removed, renamed, or merely edited in place. The check tested
path against glob and never looked at how the file changed.

**Motivating instance:** [dev-env#1011](https://github.com/brownm09/dev-env/pull/1011) modified
`claude/skills/journal-compose/SKILL.md` (`changeType: MODIFIED` — logic changes to Step 9 only,
no invocation-syntax change) plus two ADR files. Reviewing it required manually overriding Step
2b's literal instructions with judgment to avoid a false-positive blocking finding — exactly the
kind of silent, undocumented deviation the skill exists to prevent other reviewers from having
to make. Filed as [dev-env#1017](https://github.com/brownm09/dev-env/issues/1017).

### Why this is a category error, not a tuning problem

Path membership and change *type* are independent axes. A file living under `claude/skills/`
does not by itself mean it was added, removed, or renamed there — it may only have been edited.
The old check conflated "this path is in a sensitive directory" with "this path just underwent a
structural change," which are the same thing only for `ADDED`/`DELETED`/`RENAMED` files, not for
`MODIFIED` ones. Every in-place edit to any file under the four directories — the overwhelming
majority of PRs that touch them — tripped the blocking finding.

### What `gh pr view --json files` actually provides

Confirmed via GraphQL introspection against `PullRequestChangedFile` (the type backing
`gh pr view --json files`, already used elsewhere in Step 2): each entry carries `path`,
`additions`, `deletions`, and `changeType` (`PatchStatus`: `ADDED` | `DELETED` | `RENAMED` |
`COPIED` | `MODIFIED` | `CHANGED`) — sufficient to separate rows 1–4 (structural) from rows 5–6
(content) mechanically. It does **not** carry a previous-path/previous-filename field for
renames; only the REST `pulls/{n}/files` endpoint's `previous_filename` does.

## Decision

**1. Step 2 fetches per-file `changeType`.** The existing `gh pr view --json` call gains `files`,
stored as **CHANGED_FILE_LIST** — `{path, changeType, additions, deletions}` per file. No new API
call: `files` rides the same GraphQL request Step 2 already makes for `title`/`body`/etc.

**2. Step 2b classifies against the seven rows in three groups, not one glob:**

- **Rows 1–4 (structural — add/remove/rename a skill, hook script, utility script, routine):**
  mechanical. `changeType` is `ADDED`, `DELETED`, or `RENAMED` **and** path matches one of the
  four directories, **excluding any path with a `tests/` segment** (`claude/scripts/tests/**`,
  `claude/hooks/tests/**` — both exist today). A test file's own structural changes are governed
  by the separate, already-mechanized README index-parity gate
  (`test_readme_index_parity.py`, dev-env Testing item 84), not this table's row 1–4 requirement
  — matching it here would point the author at the wrong remedy. Caught during this PR's own
  `/review` pass, before merge (the same self-referential catch ADR-120 records for its own
  404-classification bug). `MODIFIED`/`CHANGED` never qualify here regardless of directory.
- **Rows 5–6 (content — `hook-config.json` schema change; a skill's invocation-syntax change):**
  `changeType` cannot decide these — a `MODIFIED` `SKILL.md` might be a pure logic edit or a
  frontmatter/invocation change, and only the diff content distinguishes them. For each
  `MODIFIED` `hook-config.json` or `claude/skills/**/SKILL.md`, Step 2b now inspects the diff
  hunks: did it touch a top-level config key, or the YAML frontmatter / "Invoke as" line?
- **Row 7 (renamed file linked from README.md/docs/REFERENCE.md):** deliberately **not**
  reimplemented in Step 2b. Step 2c's existing "cross-tree impact" judgment check — "a skill,
  hook, or workflow change referenced in a parent README or in a related doc outside the
  immediate directory tree" — already covers exactly this scenario for every renamed file, since
  README.md/docs/REFERENCE.md are within Step 2c's ancestor/cross-tree scan. Duplicating it in
  Step 2b would mean building a second, REST-backed (`previous_filename`) code path purely to
  answer a question Step 2c already answers, with the added risk of the two steps reaching
  different conclusions about the same file.

**3. Findings name the specific qualifying condition** ("new skill added", "hook script
removed", "hook-config.json schema changed", etc.) instead of a generic "paths were changed"
message, so the author knows which row applied without re-deriving it.

## Consequences

- Fixes the false-positive class: a PR that only modifies an existing skill/hook/script/routine
  in place, with no structural or invocation-syntax change, no longer trips a blocking finding.
- **Symmetric risk this decision owns:** naively restricting Step 2b to
  `ADDED`/`DELETED`/`RENAMED` alone would silently stop catching rows 5–6, which the old coarse
  glob accidentally caught as a side effect of matching every modification. The new content-based
  judgment check for rows 5–6 exists specifically to close that regression gap — this is not a
  narrower check than before, it is a *correctly-shaped* one.
- No hook or settings changes. The diff is `claude/skills/review/SKILL.md` only — a docs/prompt
  content change, not independently unit-testable, consistent with ADR-120's note that skill
  body content is untestable in the automated suite.
- `gh pr view`'s response payload grows by one field (`files`); negligible, and Step 2c already
  implies per-file iteration for its own ancestor-README checks.
- Step 2b and Step 2c now have a clean division of labor for structural changes: 2b owns
  directory-membership rows (1–4) and content rows (5–6) against the *fixed* Documentation
  Maintenance table; 2c owns judgment-based coverage (the 4 Ds, ancestor READMEs, cross-tree
  impact, including row 7) against *any* directory. Neither re-derives the other's answer.

## Alternatives considered

- **Keep the path glob; layer an exclusion list for "safe" modification patterns.** Rejected —
  still matches by path first, now with exceptions on exceptions; brittle as new patterns are
  discovered, and does not fix the underlying category error (path membership ≠ change type).
- **Derive the seven conditions dynamically by parsing the fetched `CLAUDE.md` content instead of
  hardcoding paths/rows in the skill prose.** Rejected as out of scope. The existing Step 2b/2c
  already hardcode dev-env's specific paths rather than programmatically parsing the table; a
  generic table-parser is a materially larger change, warranted only if a second adopting repo
  needs a differently-shaped table — no such repo exists yet.
- **Mechanically implement row 7 in Step 2b via the REST `pulls/{n}/files` endpoint's
  `previous_filename` field.** Rejected for this change: it would add a second API surface (REST
  alongside the GraphQL `gh pr view --json` calls Step 2b/2c already standardize on) to answer a
  question Step 2c's existing cross-tree-impact judgment already answers for the same file,
  risking two steps disagreeing about one rename instead of one step deciding it once.
- **Leave the `tests/`-segment carve-out for a follow-up, since it's a narrower residual gap than
  the bug this PR fixes.** Rejected — it is the same category error (over-broad path matching
  standing in for a real classification), just on a smaller surface, and the fix is a few lines
  directly in code this PR already rewrote; deferring it would ship a known false-positive class
  in the same PR that exists specifically to remove one.

## References

- [dev-env#1017](https://github.com/brownm09/dev-env/issues/1017) — the false-positive report and
  motivating instance (PR #1011).
- [dev-env#1011](https://github.com/brownm09/dev-env/pull/1011) — the PR whose review required a
  manual override of Step 2b's literal instructions, prompting this fix.
- [ADR-120](120-review-skill-absence-checks-over-api.md) — the prior Step 2b/2c correction (the
  404-classification false-absent trap); same steps, a different category of bug.
- [ADR-004](004-pr-review-reads-from-remote.md) — read PR state from the remote; unaffected here.
- [ADR-011](011-adr-warrant-check.md) — the warrant criterion satisfied (touches a skill
  documented under `claude/`; rationale not recoverable from `git log` alone).
- [GitHub GraphQL API — PatchStatus](https://docs.github.com/en/graphql/reference/enums#patchstatus)
  — the `changeType` enum backing `gh pr view --json files`.
- [GitHub REST API — List pull request files](https://docs.github.com/en/rest/pulls/pulls#list-pull-request-files)
  — the `previous_filename` field, present on REST but not on the GraphQL type used here.
