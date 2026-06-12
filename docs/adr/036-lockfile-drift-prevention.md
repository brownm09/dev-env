# ADR-036 — Lockfile-Drift Prevention: Global Pre-Push Hook + Dependency-Edit Rule

**Date:** 2026-06-03
**Status:** Accepted
**Closes:** [dev-env#305](https://github.com/brownm09/dev-env/issues/305)
**Tags:** hooks, pre-push, npm, lockfile, package-lock, ci, global-rule, code-quality
**Related:** [ADR-005](005-global-core-hooks-path.md), [ADR-026](026-suppression-policy.md), [ADR-029](029-test-integrity-policy.md)

---

## Context

In any npm repo, `package-lock.json` records the exact resolved dependency tree implied by the
version ranges in `package.json`. `npm ci` — the install command used in CI — **refuses to run when
the two are out of sync**, by design: it exists to install exactly what the lockfile pins, and an
inconsistent lockfile means the pinned tree no longer satisfies the declared ranges. The refusal
surfaces as a terse `EUSAGE` error that names neither the offending dependency nor the root cause.

When an author edits a dependency range in `package.json` but does not regenerate and commit the
lockfile, the lockfile drifts. The drift is invisible locally (where `npm install`, which *updates*
the lockfile, is the common command) and only bites in CI, where `npm ci` runs against the committed
lockfile. The result is a red `main` and a reactive recovery PR.

This happened on `brownm09/lifting-logbook`: clerk-backend, clerk-shared, and react ranges drifted
from the committed lockfile, `main` CI went red at the `npm ci` step, and it was fixed reactively in
[lifting-logbook#432](https://github.com/brownm09/lifting-logbook/pull/432) /
[#433](https://github.com/brownm09/lifting-logbook/pull/433). Nothing prevented recurrence.

A defense-in-depth response has four layers, two of them repo-local and two global (cross-repo):

| # | Layer | Where | Catches drift… |
|---|-------|-------|----------------|
| 1 | Branch protection requiring CI checks | repo settings | at merge (red checks block non-admin merge) |
| 2 | CI lockfile-sync guard (clear message) | repo `ci.yml` | in CI, before the cryptic `npm ci` error |
| 3 | **Global pre-push hook** | dev-env `claude/hooks/pre-push` | **locally, before the push leaves the machine** |
| 4 | **Global dependency-edit rule** | dev-env `claude/CLAUDE.md` | at authoring time (forcing function for Claude) |

Layers 1 and 2 are repo-local (implemented for lifting-logbook in
[#435](https://github.com/brownm09/lifting-logbook/issues/435)). This ADR covers the two **global**
layers, which apply to every npm repo without per-repo wiring.

---

## Decision

**Layer 3 — extend the existing global `pre-push` hook.** The hook already runs for every repo via
`core.hooksPath` ([ADR-005](005-global-core-hooks-path.md)). Add a block that:

1. While iterating the pushed refs, flags whether the push range (`<remote_sha>..<local_sha>`, or
   `origin/main..<local_sha>` for a new branch) touches any `package.json` (root or workspace, via
   `grep -qE '(^|/)package\.json$'`).
2. After the loop, only if a `package.json` changed **and** `npm` is on `PATH` **and** the repo has
   a root `package.json` + `package-lock.json`, regenerates the lockfile metadata
   (`npm install --package-lock-only --ignore-scripts`) and compares it to the working-tree
   lockfile. On any difference, prints an actionable message and **blocks the push** (`exit 1`).

The check is **non-destructive**: the working-tree lockfile is backed up (to a `mktemp` file
outside the repo) before regeneration and restored on every exit path by a `trap` — drift,
no-drift, npm failure, and SIGINT/SIGTERM — so neither an interrupted run nor a normal one ever
leaves the tree modified or a stray backup in the repo. If `npm` exits non-zero (e.g. offline),
the check is **skipped with a warning, not blocked** — a tooling failure must not wedge an
unrelated push. The block path `exit 1`s before the per-repo hook chain, consistent with the
hook's existing fully-blocking journal case.

It extends the *existing* `pre-push` file rather than adding a second hook because `core.hooksPath`
resolves a single `pre-push` per repo; a second file would silently never run.

**Layer 4 — add a "Dependency and lockfile policy" rule to `claude/CLAUDE.md`** (under Code Quality,
alongside the Suppression and Test Integrity policies):

- **Rule 1:** editing a dependency in any `package.json` requires running `npm install` and
  committing the regenerated `package-lock.json` in the same change.
- **Rule 2:** a pre-PR lockfile-sync check
  (`npm install --package-lock-only --ignore-scripts && git diff --exit-code package-lock.json`)
  when any `package.json` changed.

Layer 4 is the forcing function at authoring time; Layer 3 is the automated backstop if the author
(human or Claude) forgets.

---

## Consequences

- A push that drifts the lockfile is blocked at the source, on the machine, before CI runs — the
  fastest possible feedback and zero red `main`.
- The guard runs only when a pushed `package.json` actually changed, so the overwhelming majority of
  pushes pay nothing. Non-npm repos (no root lockfile) and npm-absent environments skip cleanly.
- The check costs one `npm install --package-lock-only` (no `node_modules` write, no scripts) on the
  pushes that do touch `package.json` — a few seconds, occasionally a dependency-resolution network
  round-trip.
- The hook is non-destructive and chains to any repo-level `.git/hooks/pre-push`, so it composes
  with Husky, git-secrets, and corporate hooks.
- Layer 4 binds Claude behaviorally even in repos that have not yet adopted Layers 1–2.

---

## Alternatives Considered

- **Compare the regenerated lockfile against `HEAD` instead of the working-tree copy.** Rejected:
  it produces false positives when the working tree legitimately has staged lockfile edits, and it
  couples the check to commit state. Comparing the regenerated lockfile to the pre-regeneration
  working-tree copy is a pure "does `package.json` imply a different lockfile than the one on disk?"
  test, independent of what is committed.
- **Block (not skip) when `npm` fails or is offline.** Rejected: a transient resolution failure or
  an offline push is unrelated to lockfile correctness; blocking would wedge legitimate pushes. The
  hook warns and proceeds, leaving CI (Layer 2) as the backstop.
- **A separate dedicated lockfile hook file.** Rejected: `core.hooksPath` runs one `pre-push` per
  repo, so a second file would never execute. Extending the existing hook is the only correct shape.
- **Rely on Layers 1–2 alone.** Rejected as insufficient: they catch drift only after the push
  reaches GitHub and CI runs. The local hook (Layer 3) gives immediate feedback, and the CLAUDE.md
  rule (Layer 4) prevents the drift from being authored in the first place.

---

## References

- [npm-ci](https://docs.npmjs.com/cli/v10/commands/npm-ci) — official docs: `npm ci` requires an
  existing `package-lock.json` that is in sync with `package.json`, and errors out otherwise.
- [npm-install — `--package-lock-only`](https://docs.npmjs.com/cli/v10/commands/npm-install) —
  regenerates `package-lock.json` from `package.json` without writing `node_modules`; the basis of
  the drift check.
- [Git `githooks` — `pre-push`](https://git-scm.com/docs/githooks#_pre_push) — the hook's stdin ref
  format (`<local ref> <local sha> <remote ref> <remote sha>`) and exit-code semantics (non-zero
  aborts the push).
- [Git `core.hooksPath`](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corehooksPath)
  — how a single global hooks directory applies across all repos (see [ADR-005](005-global-core-hooks-path.md)).
- [lifting-logbook#523 / PR #524](https://github.com/brownm09/lifting-logbook/pull/524) — the
  precedent that scoped a Layer-2 CI sync gate to `pull_request` (see Addendum).

---

## Addendum (2026-06-11) — operational float rule + Layer-2 enforcement context

Two refinements emerged from recurring lifting-logbook incidents (#501, #516, #520, #523), captured
here and as **Rule 3** of the `claude/CLAUDE.md` "Dependency and lockfile policy".

**Upstream in-range float — discard vs. regenerate.** A caret dependency, **direct or transitive**
(e.g. `@clerk/backend` and the transitive `@clerk/shared` pinned under its own `^` range), can
publish a new in-range version while a PR is open or after a merge. Because the sync check
(`npm install --package-lock-only`) rebuilds the ideal tree from the *current* registry, the
committed lockfile can be flagged stale **with no dependency edit by any contributor** — the passage
of time alone breaks it. The response depends on context:

- **Uncommitted local float** surfaced by the pre-PR check → **discard** (`git checkout -- package-lock.json`); it does not belong in the PR.
- **CI red on a pushed branch / open PR** → **regenerate and commit** (`npm install`; verify `npm ci` exits 0 locally); the regenerated lockfile is what CI now resolves.

Distinguishing the two is non-obvious — the reflexive "discard the clerk float" is correct for a
local working-tree float but wrong when CI on a pushed branch demands it. Read the *emitting* signal
(per [ADR-034](034-error-message-diligence.md)): a pre-PR working-tree diff ≠ CI's `npm ci`/sync-gate
failure on a pushed ref.

**Layer-2 (CI sync gate) must be PR-scoped, not `main`-scoped.** The Layer-2 CI guard's purpose is to
catch a contributor editing `package.json` without regenerating the lockfile — a *pull-request-time*
mistake. Run on every `push` to `main`, the same step instead recomputes the ideal tree from
live-registry time, so any upstream in-range publish reddens `main` with no contributor action
(the #432/#433/#501/#516/#520 class). The CI step should therefore be gated to `pull_request`
(`if: github.event_name == 'pull_request'`); `npm ci` on `main` still guards install-breaking drift
(the `EUSAGE` class). This brings Layer 2 into line with the **Layer 3** pre-push hook above, which
already runs the drift check only when a pushed `package.json` is in the push range — i.e. only on a
real authored change, never on registry time. Precedent: lifting-logbook
[#523 / PR #524](https://github.com/brownm09/lifting-logbook/pull/524) (`ci.yml`).
