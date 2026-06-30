# ADR-065 — Scope the git-push Journal Reminder to the Push-Target Repo, Once Per PR Per Session

**Date:** 2026-06-30
**Status:** Accepted
**Refines:** [ADR-021](021-auto-stub-on-pr-push.md)
**Tags:** hooks, post-tool-use, git-push, journal, sentinel, cross-repo, token-efficiency, correction

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
every push of the session. ADR-021's own "reminder only, no subprocess lookup" alternative was
rejected for being "too noisy"; the cwd-keyed lookup reintroduced noise by a different route
once cross-repo pushes are in play.

---

## Decision

Three changes to the `is_push` branch of `pr-merge-reminder.py`:

**1. Scope the open-PR lookup to the repo the push actually targets.**
`_effective_push_dir(command, cwd)` parses a `cd <path> &&` (or `;`) prefix that chains into
the `git push` and returns `<path>` (resolved against `cwd` when relative); a bare `git push`
returns `cwd`. The hook then calls `_open_pr_for_cwd(push_dir)` instead of
`_open_pr_for_cwd(cwd)`. A `cd /other/repo && git push` is now evaluated against `/other/repo`,
and a journal push (`cd <engineering-journal> && …`) routes correctly into the existing
`_EJ_REPO_FRAGMENT` skip. The parser is deliberately conservative: any shape it cannot parse
confidently falls back to `cwd` (the pre-this-ADR behavior), and a mis-resolved directory
simply yields no open PR downstream — a silent no-op, never a wrong-repo positive.

**2. Fire at most once per open PR per session.**
The reminder is idempotent guidance ("keep this PR's stub current"), so repeating it on every
push is waste. A per-PR, per-session sentinel
(`~/.claude/scratch/pr-merge-reminder-<pr>-<session_id>.flag`, via the shared `_hookutil`
helpers from [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md)) suppresses repeat
fires for the same PR in the same session. `cleanup_stale_sentinels(SENTINEL_PREFIX)` reaps
flags older than 30 days and runs **lazily** — only when an open PR is actually found, not on
every Bash command (so the common no-PR push pays no extra cost).

**3. Non-blocking hardening.**
The `__main__` entry is wrapped in `try / except Exception: sys.exit(0)` so any internal error
(sentinel I/O, parsing) exits 0 and never crashes the user's push flow. `sys.exit(2)` (the
intentional reminder path) raises `SystemExit`, a `BaseException`, so it still propagates and
the exit-2 contract is preserved.

---

## Consequences

**Positive:**
- Cross-repo pushes no longer fire a misattributed reminder — it names the repo actually
  pushed, or stays silent.
- A given PR's push reminder fires once per session instead of on every push: the ~6×/session
  noise collapses to ≤ 1×.
- `engineering-journal` pushes from a non-EJ cwd now correctly hit the EJ skip.
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
- The sentinel is keyed on `session_id`; a payload missing it degrades to a stable
  `"unknown-session"` key (still dedupes within that session) rather than disabling dedup.

---

## Alternatives considered

**Per-session sentinel only (no push-target scoping).** Collapses the 6× to 1×, but the single
surviving fire is still misattributed to the cwd repo and can burn the cwd PR's
once-per-session budget on an unrelated cross-repo push. Rejected alone; adopted *with* scoping
so the two compose.

**Push-target scoping only (no sentinel).** Fixes attribution but still fires on every push to
a genuinely-open-PR branch in the target repo. Rejected alone; the reminder is idempotent, so
once per session suffices.

**Rewrite ADR-021's cwd lookup to parse the push refspec.** ADR-021 already noted the
`git branch --show-current` vs. refspec gap. Parsing `origin other:target` refspecs is
orthogonal to the cross-repo *directory* problem and far rarer in practice; out of scope here.

---

## References

- [ADR-021](021-auto-stub-on-pr-push.md) — the original auto-stub-on-push decision this refines.
- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — the shared `_hookutil`
  per-session sentinel helpers reused here.
- Git, *git-push Documentation* — https://git-scm.com/docs/git-push (a push resolves its remote
  from the repository it runs in; the working directory determines the target repo).
- Issue [#442](https://github.com/brownm09/dev-env/issues/442) — tracked this fix.
- `claude/scripts/pr-merge-reminder.py`; `claude/scripts/tests/test_pr_merge_reminder.py`.
