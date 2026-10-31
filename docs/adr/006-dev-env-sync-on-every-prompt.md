# ADR 006 — UserPromptSubmit Dev-Env Sync on Every Prompt

**Date:** 2026-04-19  
**Status:** Accepted

---

## Context

Claude Code reads `CLAUDE.md` once at session start and caches it. Changes committed to `dev-env` during a running session (e.g., a new rule added via PR in a parallel session) are invisible to the current session until it is restarted.

In practice, dev-env changes happen frequently — especially during active workflow development — and restarting sessions to pick them up creates significant friction. The risk of running stale config is non-trivial: a hook removed in one session may still fire in another; a new rule documented in CLAUDE.md won't be followed until the session reads it.

---

## Decision

`dev-env-sync.py` runs on every `UserPromptSubmit` hook. It auto-pulls the `dev-env` repo at `C:/Users/brown/Git/dev-env`, which causes `~/.claude/` symlink targets to reflect the latest committed state immediately — within the current session, without restart.

**Failure mode handling:** The hook must fail open. A network failure, merge conflict, or git error must not block the session. `dev-env-sync.py` logs warnings on failure but exits 0 so the prompt is not blocked.

---

## Consequences

- Commits to `dev-env` take effect in the current session on the next prompt — no session restart required.
- Every prompt incurs a small network round-trip (a `git pull`). On a fast connection this is imperceptible; on a slow connection it adds latency.
- The hook must not be removed to "improve performance" without understanding this tradeoff — stale config is a silent failure mode, not a visible one.
- The script only runs when `main` is checked out and only attempts a `--ff-only` pull. Because CLAUDE.md prohibits direct commits to `main`, local uncommitted changes on `main` are not an expected state; the pull failing due to a dirty tree would be caught by the existing "fast-forward pull failed" warning path.

---

## References

- Engineering journal: `sessions/dev-env/2026-04-19-hook-infrastructure.md`
- `dev-env` commit `317639a` — `feat: auto-sync dev-env repo on every prompt to prevent stale CLAUDE.md`
- `claude/scripts/dev-env-sync.py` — hook implementation
- `claude/settings.json` — `UserPromptSubmit` hook registration

## Amendment 1 (2026-10-30) — the network fetch runs at most once per 15 minutes (dev-env#849)

**What changed.** The hook still runs on every prompt, but its `git fetch origin main` runs only when
`.git/FETCH_HEAD` in the canonical is more than `FETCH_MIN_INTERVAL_SECONDS` (15 minutes) old. Every
fetch of the checkout rewrites that file, whoever runs it (this hook, `session-start-sync.py`, a merge
hook, a manual fetch), so the gate needs no state file of its own. A missing, unreadable or
future-dated `FETCH_HEAD` fetches. The branch check now reads `.git/HEAD` directly
(`_bash_state.read_branch`, ADR-085 Amendment 1), and the two `rev-parse` calls are one.

**Why.** The fetch dominated the hook: 2.5 s at the median and 6.3 s at the 90th percentile per prompt
on this machine (dev-env#849 baseline on #851), against a 30 s budget it sometimes ran out of, ending
in the "sync check failed unexpectedly" fallback. Measured after the change, five fires each: 3,561 ms
before, 911 ms after, at the median.

**What still runs every prompt.** The settings sync, the off-main check and its escalation (ADR-110
Amendment 1), the comparison of local `main` to the last-fetched `origin/main`, and the pull when
behind. So a canonical behind the last fetch is still pulled at once, and a failing pull still counts
toward the persistent-failure escalation on every prompt (ADR-110, dev-env#697/#795): the gate only
delays noticing a new remote commit, never a stuck checkout.

**Cost.** A merge made on another machine, or one this machine's merge hooks did not pull, reaches the
canonical up to 15 minutes later. A merge from this machine is pulled at once by
`post-pr-merge-pull.py`.

**Alternatives considered.** A per-session or repo-level timestamp file (rejected: `FETCH_HEAD`
already records every fetch, including other hooks'); gating the whole hook (rejected: the off-main
and failed-pull checks are local, cheap and are what catch the stale-canonical incidents); a shorter
interval (the issue asked for at least 15 minutes, conservative because of #697/#795, and the checks
that matter for those stay per-prompt anyway).
