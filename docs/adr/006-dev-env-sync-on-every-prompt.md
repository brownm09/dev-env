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
