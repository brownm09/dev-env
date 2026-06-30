# ADR-065 — Scope the git-push Journal Reminder to the Push-Target Repo

**Date:** 2026-06-30
**Status:** Accepted
**Refines:** [ADR-021](021-auto-stub-on-pr-push.md)
**Tags:** hooks, post-tool-use, git-push, journal, cross-repo, correction

---

## Context

[ADR-021](021-auto-stub-on-pr-push.md) extended `pr-merge-reminder.py` to emit a
journal-stub reminder on `git push` when the pushed branch has an open PR. The detection
calls `_open_pr_for_cwd(cwd)`, which runs `git branch --show-current` + `gh pr list` in the
session **cwd** and fires if that repo has an open PR for its checked-out branch.

ADR-021 implicitly assumed the session cwd *is* the repo being pushed. That assumption breaks
for cross-repo pushes, which are routine here:

- A single session frequently pushes to more than one repo — e.g. a feature push to the
  project repo plus several journal-stub pushes to `engineering-journal`, commonly run as
  `cd <other-repo> && git push`.
- When cwd is a repo with an open PR (e.g. `lifting-logbook` #622), **every** such push —
  regardless of which repo it actually targeted — fired `lifting-logbook`'s reminder. In one
  observed session it fired ~6 times, including on the journal-stub pushes themselves. The
  `_EJ_REPO_FRAGMENT` skip (meant to silence `engineering-journal` pushes) also checks `cwd`,
  not the push target, so it missed entirely.

The result was pure noise: a reminder naming a PR unrelated to what was pushed, repeated on
every push of the session. The defect was **misattribution** — the wrong repo's reminder
firing on an unrelated push — *not* the per-push cadence itself. ADR-021's own "reminder only,
no subprocess lookup" alternative was rejected for being "too noisy"; the cwd-keyed lookup
reintroduced noise by a different route once cross-repo pushes are in play.

---

## Decision

Two changes to the `is_push` branch of `pr-merge-reminder.py`, plus a hardening wrap:

**1. Scope the open-PR lookup to the repo the push actually targets.**
`_effective_push_dir(command, cwd)` parses a `cd <path> &&` (or `;`) prefix that chains into
the `git push` and returns `<path>` (resolved against `cwd` when relative); a bare `git push`
returns `cwd`. The hook then calls `_open_pr_for_cwd(push_dir)` instead of
`_open_pr_for_cwd(cwd)`. A `cd /other/repo && git push` is now evaluated against `/other/repo`,
and a journal push (`cd <engineering-journal> && …`) routes correctly into the existing
`_EJ_REPO_FRAGMENT` skip. The parser is deliberately conservative: any shape it cannot parse
confidently falls back to `cwd` (the pre-this-ADR behavior), and a mis-resolved directory
simply yields no open PR downstream — a silent no-op, never a wrong-repo positive.

**2. Keep firing on every qualifying push — no per-session dedup.**
Once attribution is correct, the reminder fires on every push to a branch that has an open PR
**in the repo actually pushed**. This is intentional and matches ADR-021's update-trigger
framing: each push in a review cycle (initial → after review-fix-1 → after review-fix-2)
carries *new* journalable content, so re-nudging on each push is the wanted behavior. Scoping
(change 1) — not deduplication — is what removes the #442 cross-repo noise: an
`engineering-journal` push no longer fires `lifting-logbook`'s reminder, and a cross-repo
`dev-env` push fires `dev-env`'s reminder (correct) or none. The fires that remain are all
correct, repo-appropriate nudges. A once-per-PR-per-session sentinel was considered and
**rejected** (see Alternatives) because it would suppress exactly those wanted later-push
nudges.

**3. Non-blocking hardening.**
The `__main__` entry is wrapped in `try / except Exception: sys.exit(0)` so any internal error
(parsing, I/O) exits 0 and never crashes the user's push flow. `sys.exit(2)` (the intentional
reminder path) raises `SystemExit`, a `BaseException`, so it still propagates and the exit-2
contract is preserved.

---

## Consequences

**Positive:**
- Cross-repo pushes no longer fire a misattributed reminder — it names the repo actually
  pushed, or stays silent.
- `engineering-journal` pushes from a non-EJ cwd now correctly hit the EJ skip, so the
  journal-stub pushes that dominated the observed ~6×/session noise stop firing entirely.
- Genuine pushes to an open-PR branch still nudge on every push (per ADR-021), now attributed
  to the correct repo — preserving the per-push journal-update prompt the workflow relies on.
- The hook is strictly more crash-safe than before (it previously had no top-level guard).

**Trade-off / limits:**
- `_effective_push_dir` is a best-effort string parse, not a full shell evaluation. A push
  whose governing `cd` is hidden behind quoting or an unusual construct falls back to `cwd`
  (i.e. ADR-021 behavior) — it under-corrects rather than mis-fires. Accepted: the dominant
  real shape is a simple `cd <repo> && … && git push` chain, which is handled and unit-tested.
- A *relative* `cd` in a multi-`cd` chain resolves against `cwd`, not against an earlier
  absolute `cd` in the same chain — `cd /a && cd b && git push` is read as `cwd/b`, not
  `/a/b`. This too under-corrects (a non-existent dir yields no open PR downstream — a silent
  no-op, never a wrong-repo positive), and the shape is rare enough that threading the running
  directory through each `cd` is not worth the complexity.
- `git -C <path> push` is not matched by the push detector at all (pre-existing; `_PUSH_RE`
  requires `git push` adjacency) and so never fires — unchanged by this ADR. (This is why
  pushing this very PR via `git -C <worktree> push` does not trip the reminder.)
- The reminder still fires once per qualifying push, so a multi-push session to the same open
  PR gets one nudge per push. That is the intended cadence (each push carries new content), not
  a regression — it is deliberately *not* deduplicated.

---

## Alternatives considered

**Add a once-per-PR-per-session sentinel (dedup the reminder).** A per-PR, per-session flag
(`~/.claude/scratch/pr-merge-reminder-<pr>-<session>.flag`, via the shared `_hookutil`
helpers from [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md)) would suppress
repeat fires for the same PR in one session, collapsing any residual repetition to ≤ 1×.
**Rejected:** once scoping fixes attribution, the remaining fires are all correct, and each
later push in a review cycle carries new journalable content the reminder is meant to prompt.
Dedup would suppress precisely those wanted later-push nudges to buy a noise reduction that
scoping already delivers. (An earlier draft of this ADR adopted the sentinel; it was dropped
before merge in favor of every-push firing.)

**Reminder only, no subprocess lookup.** ADR-021 already rejected this for being too noisy
(fires on every push regardless of PR state). Scoping keeps the subprocess lookup but points it
at the right repo, which is the precise fix.

**Rewrite ADR-021's cwd lookup to parse the push refspec.** ADR-021 already noted the
`git branch --show-current` vs. refspec gap. Parsing `origin other:target` refspecs is
orthogonal to the cross-repo *directory* problem and far rarer in practice; out of scope here.

---

## References

- [ADR-021](021-auto-stub-on-pr-push.md) — the original auto-stub-on-push decision this refines.
- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — the shared `_hookutil`
  per-session sentinel helpers, evaluated for the rejected dedup alternative above.
- Git, *git-push Documentation* — https://git-scm.com/docs/git-push (a push resolves its remote
  from the repository it runs in; the working directory determines the target repo).
- Issue [#442](https://github.com/brownm09/dev-env/issues/442) — tracked this fix.
- `claude/scripts/pr-merge-reminder.py`; `claude/scripts/tests/test_pr_merge_reminder.py`.
