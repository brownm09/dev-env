# ADR 111 — Shared `_repo_target` Resolver for `gh` Command Repo/PR Target Extraction

**Date:** 2026-07-16
**Status:** Accepted
**Tags:** hooks, post-tool-use, stop, github, repo-resolution, pr-url, shared-module, maintainability, dry, adr-050, adr-067, cross-repo, false-positive, masking

---

## Context

Five hooks each independently reimplemented the same primitive: given a `gh pr merge` /
`gh pr create` (and, for one, `gh issue close`) command, extract the `owner/repo` it
targets — honoring an explicit `--repo`/`-R` flag over cwd, then a
`github.com/<owner>/<repo>/pull/<N>` URL, then a positional number — with quote-aware
masking so a `--subject`/`--body` value cannot hijack the match:

- `post-pr-merge-project.py` — `extract_repo_from_command` / `extract_pr_number_from_command` / `_merge_args`
- `pr-merge-reminder.py` — `_effective_merge_repo` / `_effective_create_repo`
- `posttooluse-inert-advisory.py` — `_devenv_merge_pr`
- `post-pr-merge-pull.py` — `extract_repo`
- `stop-tile-enumeration-gate.py` — `_target_pr` / `_explicit_repo` / `_closed_issue_number` and its `_PR_URL_RE`/`_ISSUE_URL_RE` `.finditer` scans

The five copies had **drifted into three distinct `--repo`/`-R` regex shapes** for what
should be the identical match:

```
(?<!\S)(?:--repo|-R)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)      # project / reminder / pull (space only, strict slug)
(?<!\S)(?:--repo|-R)[=\s]+(\S+)                                # inert-advisory (= or space, loose \S+)
(?<!\S)(?:--repo|-R)(?:=|\s+)(?P<repo>[^\s/]+/[^\s]+)          # tile-gate (= or space, semi-loose named)
```

Three regexes for one concept is precisely the divergence that produces silent-misrouting
bugs — a merge's board update or advisory logic acting on the wrong repo. The three
space-only copies silently miss the `--repo=owner/repo` (`=`) form; the loose captures
differ from the strict slug only on malformed input a real `gh` invocation never produces.
This drift generated a family of ~10 open issues
([dev-env#482](https://github.com/brownm09/dev-env/issues/482),
[#505](https://github.com/brownm09/dev-env/issues/505),
[#524](https://github.com/brownm09/dev-env/issues/524),
[#535](https://github.com/brownm09/dev-env/issues/535),
[#566](https://github.com/brownm09/dev-env/issues/566),
[#569](https://github.com/brownm09/dev-env/issues/569),
[#570](https://github.com/brownm09/dev-env/issues/570),
[#571](https://github.com/brownm09/dev-env/issues/571),
[#667](https://github.com/brownm09/dev-env/issues/667),
[#685](https://github.com/brownm09/dev-env/issues/685)).

Correct `--repo`/`-R` extraction proved inseparable from correct **argument-region
bounding** (a `--subject`/`--body` value must not truncate or hijack the search) — a fact
the ADR-050 amendment history bears out: **Amendments 14, 15, 17, 18, 19, 20, 21** are all,
in whole or part, per-site re-applications of the same masking discipline to this
repo-flag/URL/number family, hand-applied one call site at a time. That per-site amendment
treadmill is the recurring cost this consolidation ends: a single shared implementation
means the masking invariant is encoded **once**, not re-derived (and re-mis-derived) at
each new call site.

This is PR11 of 12 in the hook-reliability initiative
([dev-env#717](https://github.com/brownm09/dev-env/issues/717), Phase E — SSOT
consolidation), tracked by [dev-env#779](https://github.com/brownm09/dev-env/issues/779).
It continues this repo's shared-module line: `_hookio` (ADR-050) → `_worktree_liveness`
(ADR-051) → `_journal_shards` (ADR-057) → `_worktree_topology` (ADR-058) → `_hookutil`
(ADR-064) → `_repo_scan` (ADR-072) → `_worktree_canon`/`_gh_project` (ADR-073) →
`_journal_schema` (ADR-081) → `_hookout` (ADR-103).

## Decision

1. **New `claude/scripts/_repo_target.py`** — a pure module (no `subprocess`, no I/O beyond
   the strings passed in; imports only `re` and the two masking helpers from `_hookio`),
   the single source of truth for command-string target extraction. Public surface:
   - `repo_from_flag(text)` — `owner/repo` of a standalone `--repo`/`-R` flag, **both** the
     `=` and space forms, strict GitHub-legal slug, `(?<!\S)` standalone-token lookbehind.
     Masks `text` with `mask_quoted_spans` **internally** — the one place the "the flag
     search must be masked" invariant lives.
   - `merge_args(command)` / `create_args(command)` — the `gh pr merge` / `gh pr create`
     invocation's own argument region, quote-aware-bounded (masks before bounding, returns
     the real unmasked slice), so a chained sibling command's flag cannot leak in.
   - `repo_from_pr_url` / `pr_number_from_pr_url` / `iter_pr_urls` and
     `issue_number_from_issue_url` / `iter_issue_urls` — URL parsing, scheme-agnostic,
     strict slug. These do **not** mask internally — the correct masking *scope* differs
     per caller (four sites mask `--subject`/`--body` decoy values via
     `mask_prose_flag_values`; the tile gate deliberately keeps a *bare* quoted URL
     matchable), so each caller masks (or not) before calling.
   - `positional_number(text)` — first bare positional integer token, masks with
     `mask_quoted_spans` internally (decoy-safe).

   The **flag vs. URL masking asymmetry** (flag masks internally, URL does not) is the load-
   bearing design choice: every one of the five flag call sites already masked with
   `mask_quoted_spans` and differed only in *scope* (args region / whole command / one
   segment), so masking internally centralizes the invariant with zero loss; the URL sites
   genuinely disagree on masking (some need a bare quoted URL to stay matchable), so
   internal masking there would break a legitimate case.

2. **cd-chain / `-C` redirect resolution is NOT re-implemented** — it already lives in
   `_hookio.effective_merge_dir` (ADR-067) and is imported directly by the consumers that
   need it. `_repo_target` owns only *command-string* target extraction; directory
   resolution stays in `_hookio`.

3. **All five consumers migrate and delegate; local regex copies removed.** Each consumer's
   external behavior is preserved — the migration is a body swap, not a rewrite — except
   where fixing one of the named bugs below requires a change. Each consumer's own test
   suite re-runs with materially unchanged assertions, plus new per-bug regression cases.

4. **Bug fixes folded into the consolidation** (each squarely a repo-target-resolution
   concern):
   - **#482 Gap 2 (`=` form)** — the three space-only copies now accept `--repo=owner/repo`
     via the shared flag regex. (Gap 2's `-R` half was already fixed by dev-env#616.)
   - **#482 Gap 1 (unbounded flag search)** — `post-pr-merge-pull.py`'s `extract_repo`
     flag search, previously over the whole command, is now scoped to `merge_args`, so a
     chained sibling `gh pr create --repo X` can no longer leak its flag into the merge.
   - **#667 (chained create+merge cross-contamination)** — `pr-merge-reminder.py`'s
     `_effective_merge_repo` / `_effective_create_repo` previously searched the whole masked
     command, so whichever `--repo` appeared textually first won for **both**. Each now
     scopes to its own invocation's args (`merge_args` / `create_args`), so the two resolve
     independently. This also subsumes #482 Gap 1 for the reminder.
   - **#569 (config not scoped to merge target)** — `post-pr-merge-project.py`'s `main()`
     now resolves `load_config` (and the live-confirm cwd) via
     `effective_merge_dir(command, cwd)`, so a `cd <other-repo> && gh pr merge` loads the
     *merged* repo's `.claude/hook-config.json`, the cd-chain analog of the #559 URL-case
     guard — matching its two sibling merge-triggered hooks.
   - **#685 (tile-gate URL fallbacks unmasked)** — `_target_pr` / `_explicit_repo` /
     `_closed_issue_number`'s `_PR_URL_RE`/`_ISSUE_URL_RE` checks were the last members of
     this family still searched entirely unmasked. Because the URL check runs *first* (ahead
     of the positional fallback), a decoy `/pull/N` URL in a `--subject` value won even when
     a real positional number was present — a strictly more severe shape than the
     bare-number decoy (#650). Routing them through `_repo_target` lets each call site mask
     `--subject`/`--body` decoys with `mask_prose_flag_values`, closing the decoy while
     leaving a bare quoted URL matchable.

5. **New `test_repo_target.py`** exhaustively pins the primitive (flag `=`/space/`-R` forms,
   strict slug, mid-word rejection, quoted decoy masking; args-region statement scoping and
   the #660 quoted-separator boundary; URL/issue parsing and the no-internal-masking
   contract; positional-number decoy safety). Each consumer suite gains its own per-bug
   regression cases (#482/#569/#667/#685) and otherwise re-runs unchanged.

## Issue triage

| Issue | Disposition |
|---|---|
| #482 | **Fixed here.** Gap 2 (`=` form) via the shared flag regex; Gap 1 (unbounded flag) via `merge_args`/`create_args` scoping in pull + reminder. The `-R` half of Gap 2 was already merged (dev-env#616). |
| #667 | **Fixed here.** Statement-scoped flag resolution ends the chained create+merge cross-contamination in `pr-merge-reminder.py`. |
| #569 | **Fixed here.** `load_config` scoped via `effective_merge_dir`. |
| #685 | **Fixed here.** Tile-gate URL fallbacks masked with `mask_prose_flag_values`. |
| #566 | **Addressed.** The corruption risk (wrong repo's board) is closed by the #559 skip-guard (already merged) plus #569's cd-chain scoping (here); the "resolve instead of skip" enhancement is #571. |
| #570 | **Flag part already fixed** by dev-env#646 (`_effective_create_repo` resolves `--repo`/`-R`); #667's statement-scoping here tightens it against cross-contamination. The residual cd-chain-for-`gh pr create` display resolution is deferred (low-impact advisory display; real create commands carry `--repo`, so the flag path covers them). |
| #571 | **Re-scoped, kept open.** A genuine *enhancement* (sibling-config lookup, ADR-077 style) — recover the auto-move for the cross-repo case instead of skipping — not a repo-target-extraction concern. Meaningfully more code + its own test coverage; out of this PR's scope. |
| #535 | **Re-scoped, kept open.** The PR-number mis-extraction paths it could stem from are now hardened (Amendments 19/20/21, preserved by `_repo_target`); the residual (`parse_closes_numbers` matching an incidental `Fixes #N` in PR-body prose) is a distinct issue-linking-keyword-precision concern, not repo-target resolution. |
| #505 | **Re-scoped, kept open.** A different file and concern — `post-tool-use.py`'s issue/PR **create** detection (mis-classifying `gh issue comment`/`close` as a create), already resolved by the `scan_top_level` create-scoping merged earlier. Not repo-target resolution. |
| #524 | **Re-scoped, kept open.** A different concern (`scan_top_level` pipe-awareness) in three *different* hooks (the `pre-merge-*` gates). A piped `gh pr merge` is not a realistic invocation (it consumes no stdin); the issue itself invites a won't-fix. Not repo-target resolution. |

## Considered alternatives

- **Leave the three regex shapes, re-sync on drift.** Rejected — the drift already spans
  five files and produced ~10 issues; the ADR-050 amendment history is the evidence that
  per-site re-syncing does not converge.
- **Mask internally in every function (flag *and* URL).** Rejected — the URL sites
  legitimately disagree on masking (a bare quoted URL must stay matchable in the tile gate),
  so a blanket internal mask would break a real case. The asymmetry (flag masks, URL
  doesn't) is deliberate.
- **Also consolidate the positional-number and URL *output*-parsing (gh's own trusted
  output).** Partially done — the shared functions are used for both command and output
  parsing where the semantics match; `pr-merge-reminder.py`'s `_create_shard_step` (which
  needs the *whole* URL string from gh's own create output) stays local, as it is not a
  target-resolution-from-untrusted-command concern.
- **Fix #570's cd-chain-for-create and #571's sibling-config lookup here.** Rejected —
  #570's residual is a low-impact advisory display (real create commands carry `--repo`) and
  a clean fix would mean generalizing `_hookio.effective_merge_dir` (scope creep into a
  heavily-tested module); #571 is a genuine enhancement with its own test surface. Both
  deferred per the scope guard.

## Consequences

- One module becomes the source of truth for `--repo`/`-R`/PR-URL/issue-URL/positional/
  args-region extraction across five hooks; the "add an ADR-050 amendment per new call site"
  treadmill for this concern ends. A future sixth consumer imports and delegates.
- Net LOC: the five consumers shed their local regexes and hand-applied masking; the module
  plus its dedicated test file are the added surface (11 files changed, ~900 insertions /
  ~300 deletions, most of it the new module + test).
- Behavior changes are limited to the four named bug fixes (#482/#569/#667/#685); all other
  external behavior is preserved, verified by re-running each consumer's suite unchanged.
- `README.md` / `docs/REFERENCE.md` gain a `_repo_target.py` shared-module paragraph
  (matching `_hookio`/`_journal_schema`/`_hookout`), and `post-pr-merge-project.py`'s
  REFERENCE row drops its now-stale "does not yet cover dev-env#569" caveat.

## Amendment 1 — 2026-07-17: sixth consumer (`post-tool-use.py`), host-prefixed/URL `--repo` normalization, `issue_create_args`

[dev-env#838](https://github.com/brownm09/dev-env/issues/838) migrates a sixth member of the
`--repo`-extraction family — `post-tool-use.py`'s `extract_repo_flag` (feeding the cross-repo
sibling-config lookup, dev-env#542/#544) — onto this resolver: the "future sixth consumer
imports and delegates" the Consequences section anticipated. It was the one repo-flag
extractor never consolidated, running its own `_REPO_FLAG_RE.search` over each
`split_top_level` segment, so it lacked both continuation-safety (a `--repo` on a
backslash-continued line landed in a later segment and was silently missed) and quoted-decoy
masking — the exact two protections every other consumer already had.

Two additions to the shared module:

1. **`issue_create_args(command)`** — the `gh issue create` counterpart of `create_args`.
   `post-tool-use.py` fires for both `gh issue create` and `gh pr create`, so its extraction
   needs both invocation regions; the module previously had only `create_args`
   (`gh pr create`). `extract_repo_flag` now checks `issue_create_args` then `create_args`,
   inheriting the continuation-stripping (`strip_line_continuations`, dev-env#831) and
   quote-aware statement-bounding both region helpers already provide.

2. **`repo_from_flag` normalizes a full-URL / host-prefixed `--repo` value.** The strict-slug
   capture is now preceded by an optional, non-capturing
   `(?:https?://)?(?:www\.)?(?:github\.com/)?` prefix, so `--repo https://github.com/owner/repo`
   and `--repo github.com/owner/repo` (both valid per
   [gh's `--repo` docs](https://cli.github.com/manual/gh#--repo-string)) return the bare
   `owner/repo`. This **folds in** `post-tool-use.py`'s former private `_REPO_HOST_PREFIX_RE`
   (dev-env#544) so that consumer migrates with **no behavior loss** — its two dev-env#544
   URL/host-prefix tests pass unchanged through the shared path.

   This narrows the Context's claim that "the loose captures differ from the strict slug only
   on malformed input a real `gh` invocation never produces": the host-prefixed / URL forms
   are **valid, if uncommon, `gh` input**, and the strict slug silently mis-captured
   `github.com/owner` (the first `owner/repo`-shaped run) for the host-prefixed form and
   returned `None` for the `https://` form. Widening `repo_from_flag` therefore also **fixes
   that same latent mis-capture in the original five consumers** — none of which had a test
   exercising a URL-form `--repo` (verified before the change), so the fix is a strict
   improvement there (a `--repo github.com/o/r` that previously resolved to the nonexistent
   `github.com/o` and failed downstream now resolves correctly).

   *Alternatives rejected* (both fully analyzed): keeping post-tool-use's richer extraction
   local (leaving `_REPO_FLAG_RE` in that hook, merely hardened with shared masking) would
   deliver the two protections but leave the "sixth extractor never consolidated" un-retired,
   the opposite of this ADR's thesis; migrating to the strict `repo_from_flag` as-is and
   dropping the URL/host-prefix support would be a (graceful, silent-skip) behavior
   regression, not the behavior-preserving consolidation intended.

`test_repo_target.py` gains the host-prefixed / full-URL / `www.` / `=`-URL `repo_from_flag`
cases and the `issue_create_args` cases (basic, chained-sibling-not-leaked, continuation);
`test_post_tool_use.py` gains sabotage-verified continuation and quoted-`--repo`/URL-decoy
cases for `extract_repo_flag` (each confirmed to fail against the pre-fix logic).
`post-tool-use.py` now joins the original five in `README.md` / `docs/REFERENCE.md`.

## References

- [Issue #779](https://github.com/brownm09/dev-env/issues/779) (this PR),
  [#717](https://github.com/brownm09/dev-env/issues/717) (the initiative).
- The 10-issue family: #482, #505, #524, #535, #566, #569, #570, #571, #667, #685.
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) — the amendment history this
  consolidation supersedes for the repo-flag/URL/number concern (esp. Amendments 14/15/17/18/19/20/21).
- [ADR-067](067-scope-merge-keyed-hooks-to-target-repo.md) — `effective_merge_dir`,
  reused (not re-implemented) for cd-chain resolution.
- [ADR-073](073-shared-worktree-canon-gh-project-modules.md) — the closest shared-module
  precedent (extract a duplicated resolver, pin the reconciliation with a dedicated test).
- `claude/scripts/_repo_target.py`, `claude/scripts/tests/test_repo_target.py`.
