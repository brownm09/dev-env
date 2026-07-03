# ADR-077 — Cross-Repo `hook-config.json` Resolution for `gh issue/pr create --repo`

**Date:** 2026-07-03
**Status:** Accepted
**Refines:** [ADR-052](052-worktree-config-canonical-fallback.md)
**Tags:** hooks, post-tool-use, github-project, hook-config, cross-repo, correction

---

## Context

`post-tool-use.py` auto-adds a newly-created issue/PR to its project board. `load_config(cwd)`
resolves `.claude/hook-config.json` from the invoking session's own cwd, with two fallback
branches (`canonical_root_from_worktree`, `canonical_root_via_git`) that stay within the
*same* repo's worktree lineage. None of the three branches ever inspects the `gh` command
itself — so a `gh issue create --repo owner/name` naming a repo *different* from the session's
own cwd always silently misses, regardless of whether that other repo has a valid, onboarded
`hook-config.json` of its own.

This is not a hypothetical: [#532](https://github.com/brownm09/dev-env/issues/532) and
[#537](https://github.com/brownm09/dev-env/issues/537) were both filed from career-playbook
sessions via `gh issue create --repo brownm09/dev-env ...` and both silently no-oped, requiring
the documented manual fallback each time. [#542](https://github.com/brownm09/dev-env/issues/542)
— the issue tracking this fix — reproduced the same failure live during its own filing.

An initial investigation pass mis-attributed the cause to the `gh issue/pr create` detection
regex having been unanchored prior to commit `65a5f84` (closing #499/#508, merged
`2026-07-02 07:12:34 UTC`). This does not hold up: both #532 (`2026-07-03T01:07:39Z`) and #537
(`2026-07-03T01:54:50Z`) were created ~18 hours *after* that commit, using the anchored
detection. The #532 session's own journal stub confirms the actual mechanism directly: *"My
session's primary directory is career-playbook, not dev-env... the auto-project-add PostToolUse
hook didn't fire."* `load_config` never changed in `65a5f84`'s diff.

The impact is bounded, not catastrophic: `reconcile-project-board.py --scan-dir
C:/Users/brown/Git` runs nightly (ADR-068, ADR-070) and catches orphaned issues across every
repo with a `hook-config.json` within ~24h. But the live hook still misses the synchronous
"run these commands now, before any file edits" workflow the `CLAUDE.md` → `## GitHub Project`
section documents, forcing the same manual recovery on every occurrence.

This is the same failure shape ADR-065 (push reminder) and ADR-067 (merge-keyed hooks) already
fixed for their own hooks: an operation whose real target repo can differ from the session's
own cwd, with the hook naively assuming the two are the same.

---

## Decision

Extend `load_config` with one more resolution attempt, tried only after the existing cwd /
worktree / sibling-worktree branches all miss, and only when a `command` string is supplied:

**1. Parse the `--repo`/`-R` flag off the matched top-level statement.**
`extract_repo_flag(command)` reuses `_hookio.split_top_level` and the existing
`_check_issue_create_stmt` / `_check_pr_create_stmt` predicates to find the *specific* top-level
segment that is a genuine `gh issue create` / `gh pr create` invocation (never a heredoc body, a
quoted string, or an unrelated earlier statement — the same dev-env#499 discipline), then
regex-extracts the flag's value from within that segment only.

**2. Look for the named repo as a sibling checkout.**
`_sibling_repo_config(own_root, repo_flag)` derives `os.path.dirname(own_root)` — the parent
directory this whole fleet already treats as the canonical scan root (`reconcile-project-board.py
--scan-dir C:/Users/brown/Git`, `_repo_scan.find_git_repos`) — and looks for `<parent>/<name>`,
where `name` is the last path segment of `owner/name`.

**3. Never trust a directory-name match alone.**
The sibling is used only when its own `hook-config.json` self-reports a `repo` field matching
the parsed `owner/name` exactly (case-insensitively). A same-named-but-unrelated directory, a
sibling with no config, or no sibling checkout at all all degrade to `None` — byte-identical to
pre-this-ADR behavior, never a wrong-project add. This mirrors `effective_merge_dir`'s own
documented trade-off in `_hookio.py`: conservative by design, under-corrects rather than
mis-fires.

`load_config`'s signature becomes `load_config(cwd, command=None)` — additive and backward
compatible; `command` defaults to `None`, and every other `load_config` in this repo
(`reconcile-project-board.py`, `post-pr-merge-project.py`, `usage-snapshot.py`) is an
independent same-named function in its own module, not a shared import, so nothing else is
affected.

---

## Consequences

**Positive:**
- `gh issue create --repo brownm09/dev-env` (or `gh pr create`) filed from any session whose
  cwd is a sibling checkout under `C:/Users/brown/Git` now fires the live hook immediately,
  instead of relying on the nightly reconcile backstop.
- No behavior change for the common case (no `--repo` flag; `gh` infers the repo from cwd
  itself) — `extract_repo_flag` returns `None` and the new branch is skipped entirely.
- No behavior change when cross-repo resolution can't independently confirm a match — same
  silent skip as before, still caught by the nightly backstop.
- Reuses established, already-shipped conventions (`--scan-dir C:/Users/brown/Git`,
  `_repo_scan.find_git_repos`) rather than inventing a new one.

**Trade-off / limits:**
- `extract_repo_flag` is a best-effort regex over the matched segment's text, not a full
  shell-argument tokenizer. An unusual construction where a quoted `--title`/`--body` value
  itself contains literal `--repo `/`-R ` text ahead of a real flag (or in a flagless command)
  could extract the wrong string. Bounded: `_sibling_repo_config`'s independent verification
  means a misparse degrades to `None` (today's behavior), never a wrong-project add — the same
  bound that makes step 3 safe in the first place.
- Only covers repos checked out as flat siblings under one parent directory (the convention this
  whole fleet already uses). A repo cloned elsewhere is unaffected — falls through to the
  existing silent skip, unchanged from before this ADR.
- Does not change `reconcile-project-board.py` or the nightly routine; the backstop remains the
  safety net for whatever this resolution still misses.

---

## Alternatives Considered

**Hardcode `C:/Users/brown/Git` as a scan root, like the `reconcile-project-board` routine's own
`--scan-dir` invocation does.** Would match existing precedent (that exact path is already a
literal in `claude/routines/reconcile-project-board/SKILL.md`) but is strictly less general than
deriving the parent from the session's own resolved canonical root, and `post-tool-use.py`
already computes that root for the pre-existing worktree-fallback branch — reusing it costs
nothing extra. Rejected in favor of derivation.

**Add a visible stderr note when cross-repo resolution can't confirm a match.** Considered so a
non-fire is never silent. Rejected: the nightly `reconcile-project-board --scan-dir` backstop
(ADR-068, ADR-070) already reports `needs_attention` for exactly this residual case, and a new
notification risks noise for the common, benign case of filing an issue against an external repo
Mike doesn't track (e.g. `--repo torvalds/linux`) that happens to have no local sibling at all.

**Full shell tokenization (`shlex`) instead of a regex search.** Would close the narrow
quoted-value false-extraction edge case noted above. Rejected as disproportionate: the
downstream verification in `_sibling_repo_config` already bounds a misparse to "no worse than
today," so the added complexity buys correctness in a case that degrades safely without it.

---

## References

- [ADR-052](052-worktree-config-canonical-fallback.md) — the worktree-fallback resolution this
  ADR extends with one more branch.
- [ADR-065](065-scope-push-reminder-to-target-repo.md), [ADR-067](067-scope-merge-keyed-hooks-to-target-repo.md)
  — prior fixes for the same failure shape (operation target ≠ session cwd) in sibling hooks.
- [ADR-068](068-reconcile-project-board-orphan-issues.md), [ADR-070](070-reconcile-project-board-scan-dir.md)
  — the nightly backstop that bounds this bug's real-world impact and remains the safety net for
  whatever this fix still misses.
- Issues [#532](https://github.com/brownm09/dev-env/issues/532),
  [#537](https://github.com/brownm09/dev-env/issues/537),
  [#542](https://github.com/brownm09/dev-env/issues/542) — the two observed incidents and the
  issue tracking this fix.
- `claude/scripts/post-tool-use.py`; `claude/scripts/tests/test_post_tool_use.py`.
