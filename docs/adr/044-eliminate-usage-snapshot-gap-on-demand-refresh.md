# ADR-044 — Eliminate the Usage-Snapshot Gap via On-Demand CLI Refresh

**Date:** 2026-06-16
**Status:** Accepted
**Closes:** [dev-env#361](https://github.com/brownm09/dev-env/issues/361)
**Tags:** hooks, oauth, token-refresh, usage-snapshot, post-tool-use, lazy-refresh
**Related:** [ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md), [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md)

---

## Context

After [#357](https://github.com/brownm09/dev-env/pull/357) (made a stale token visible) and
[ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md) (the `ClaudeKeepTokenWarm` scheduled
task, verified refreshing the token — two `REFRESHED` log lines on 2026-06-15/16), the post-merge
usage snapshot still had a residual window where it showed the advisory instead of a snapshot. Two
causes:

1. **Lazy refresh (structural).** Claude Code refreshes the OAuth token only at/near expiry, not
   proactively. The keep-warm logs prove it: no refresh at 213 min remaining; refresh at −27 min and
   +0.1 min. So the on-disk token is *briefly expired every ~8h cycle*, and a **scheduled** task can
   only bound that window (≤ its cadence) — it can never eliminate it. A merge that lands in the gap
   sees an expired token.
2. **Self-inflicted.** `usage-snapshot.py` blocked with an advisory whenever the token was "expiring"
   (< 1h left), even though such a token is **still valid** and the ~5 s fetch would succeed —
   discarding snapshots it could have produced.

## Decision

Refresh the token **on demand, inside the snapshot path, at the moment it reads the token** — the only
point at which a valid token can be guaranteed given lazy refresh — and stop discarding still-valid
tokens.

In `usage-snapshot.py` `main()`:

- **Only a truly-expired token blocks.** `expiring` / `ok` / `no_expiry` all proceed to `fetch_usage`
  (a new pure helper `snapshot_action(state)` maps `expired → "refresh"`, everything else `→ "fetch"`).
- **On `expired`, refresh on demand, then re-read and re-classify.** `refresh_token_now()` shells to
  the existing `keep-token-warm.ps1` (the same script the scheduled task runs) with a short internal
  timeout (`-TimeoutSeconds 35`) wrapped in a ~45 s subprocess backstop — both under Claude Code's
  ~60 s hook budget. Only if the token is *still* expired afterward (refresh token dead) does the hook
  emit the [#357](https://github.com/brownm09/dev-env/pull/357) advisory and exit 2.

The refresh is performed **by the CLI**, which owns the OAuth client and the credential-file write —
exactly as the keep-warm task and a manual `claude` run do. This is **not** the rejected
[#356](https://github.com/brownm09/dev-env/issues/356) (which reimplemented a raw OAuth refresh client
and carried refresh-token-rotation / write-race risk); our code never performs an OAuth POST or writes
`.credentials.json`.

## Rationale

**Why on-demand and not a tighter schedule.** Lazy refresh means the token is *designed* to expire
briefly each cycle; no background cadence closes that by construction (a 1 h cadence still leaves a
sub-cycle expired window). Only a refresh triggered by the snapshot itself guarantees a valid token at
read time. On-demand also covers the machine-was-asleep case (the Interactive-logon scheduled task
doesn't run while logged off).

**Why keep `ClaudeKeepTokenWarm` (ADR-043).** Its role is refined from "bound the gap" to **latency
optimizer**: it keeps the token usually-fresh, so the on-demand refresh — which adds ~5–25 s to a merge
— rarely has to fire. Most merges hit a valid token and pay zero added latency.

**Why reuse `keep-token-warm.ps1`.** One canonical "refresh the token now" implementation, shared by
the scheduled task and the on-demand path (resolve `claude.exe`, run it, log before/after expiry,
clean child-process kill on timeout). No duplicated refresh logic.

**Why the latency is acceptable.** The added time only occurs on a merge that lands on an expired token
(rare, given keep-warm). The feasibility spike measured the full python→powershell→`claude` chain at
28.2 s — within the 45 s subprocess budget and the 60 s hook budget with room for the subsequent fetch.

## Alternatives considered

- **Tighten the keep-warm cadence (1–2 h).** Only shrinks the window; lazy refresh leaves the token
  briefly expired each cycle. Does not eliminate.
- **Force a proactive refresh by writing a past `expiresAt` into `.credentials.json` before running
  `claude`.** Would eliminate the gap from the background task with no merge latency, but reintroduces
  the credential-file write ADR-043 deliberately avoided (concurrent-write/corruption, schema
  coupling). Rejected.
- **Expiry-aware self-rescheduling task.** Fragile — claude's lazy-refresh threshold is unknown,
  firing *before* expiry wouldn't trigger a refresh, and it still misses the machine-asleep case.
- **Self-healing raw OAuth refresh in the hook ([#356]).** Already rejected (raw client + rotation
  risk). On-demand via the CLI achieves the same end safely.

## Consequences

**Positive:**

- The snapshot succeeds whenever the token is valid **or** refreshable — the gap is eliminated except
  for the irreducible dead-refresh-token case (which correctly degrades to the #357 advisory).
- Still-valid "expiring" tokens now produce snapshots instead of being discarded.
- No credential write-back from our code; the CLI owns the token lifecycle (the #356 risk stays out).

**Negative / residual:**

- A merge that lands on an expired token pays ~5–25 s of added hook latency while the refresh runs
  (bounded under the 60 s hook timeout; falls back to the advisory on timeout).
- The on-demand refresh firing **from inside a live merge hook** is not exercised by this PR's own
  merge (the token is fresh at merge → `fetch`, not `refresh`); it is verified by the existing
  `REFRESHED` keep-warm logs plus the standalone chain spike, with full confirmation deferred to the
  next merge that naturally lands on an expired token.
- Windows-coupled: the hook shells to `powershell`/`keep-token-warm.ps1` (consistent with the rest of
  this Windows-only tooling).

## References

- [dev-env#361](https://github.com/brownm09/dev-env/issues/361) — issue this ADR closes.
- [ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md) — the keep-warm scheduled task (role refined here to latency optimizer).
- [dev-env#356](https://github.com/brownm09/dev-env/issues/356) — rejected raw-OAuth self-healing (the CLI-owned refresh here is distinct).
- [PR #357](https://github.com/brownm09/dev-env/pull/357) — the advisory fallback this preserves.
- OAuth 2.0 refresh-token grant + rotation — [RFC 6749 §6](https://datatracker.ietf.org/doc/html/rfc6749#section-6).
