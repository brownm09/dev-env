# ADR-044 — Eliminate the Usage-Snapshot Gap via On-Demand CLI Refresh

**Date:** 2026-06-16
**Status:** Accepted; Decision reversed by the 2026-11-06 amendment
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

## Amendment (2026-07-17) — Extend on-demand refresh to the missing/unparseable-token branch (dev-env#819)

This ADR's on-demand refresh only fired on `classify_token()` returning `"expired"` — a token
*present* in a well-formed `.credentials.json` but past its `expiresAt`. A second, separate branch in
`main()` (`if not token:`) handles creds that parse as valid JSON but whose oauth substructure yields
no usable `accessToken` (a missing key, a malformed `expiresAt`, or similar local corruption — distinct
from the file being entirely absent or invalid JSON, which `if not creds:` already discards silently,
untouched by this amendment). That branch skipped straight to the advisory with no refresh attempt,
asymmetric with the adjacent `"expired"` case this ADR already covers.

**Fix.** `if not token:` now attempts the same on-demand refresh before falling back to the advisory,
via a new shared helper, `attempt_token_refresh(creds, token, expires_at_ms, refresh_fn=..., load_fn=...,
get_fn=...)`, extracted because both branches now perform an identical retry-and-recheck sequence (call
`refresh_token_now()`, reload creds, re-extract the token). The three I/O dependencies are
dependency-injected so the sequence is unit-testable offline — this repo's established pattern for
testing a decision that wraps live I/O (e.g. `post-tool-use.py`'s `fetch_live_required_field_options()`).
No new OAuth mechanism: this is the same CLI-owned refresh path this ADR already accepted, now reached
from a second call site.

**Empirical verification — confounded, documented honestly rather than assumed.** The issue asked
whether `refresh_token_now()` (headless `claude -p ok --model haiku` via `keep-token-warm.ps1`) can
actually recover this branch's precondition, since regenerating a broken *token* might need more than
refreshing an otherwise-valid one. Live testing hit a pre-existing, unrelated confound: the test
machine's OAuth session was already dead at the time of testing — `claude auth status --json` reported
`loggedIn: false`, and the `ClaudeKeepTokenWarm` scheduled task had failed (`claude_exit=1`) on every run
that day. A direct `claude -p ok --model haiku` against the **unmodified** credentials file failed
immediately (`OAuth session expired and could not be refreshed`), and two further non-interactive
launch attempts (a redirected-pipe `ProcessStartInfo` launch mirroring `keep-token-warm.ps1` itself, and
a Git Bash `timeout`-wrapped bare launch) both failed identically — Claude Code treats any non-TTY
stdout as `--print` mode and refuses without explicit input, confirming no subprocess-based approach
available to automation can reach whatever startup path a genuine interactive terminal uses to
self-heal. None of the three attempts altered the credentials file (verified via SHA256 before/after
each). This dead-session finding is tracked separately as
[dev-env#825](https://github.com/brownm09/dev-env/issues/825) — it is orthogonal to whether the mirrored
mechanism helps in the common case (a healthy refresh token, corrupted local field), which this ADR's
own Consequences section already accepted only partial/deferred verification for on the sibling
`"expired"` branch ("full confirmation deferred to the next merge that naturally lands on an expired
token"). The same standard applies here: the fix degrades no worse than the pre-fix behavior even when
the refresh token is genuinely dead (same advisory, just after a bounded ~5-25s wasted attempt), and it
helps in exactly the cases the `"expired"` branch already helps in.

See [dev-env#819](https://github.com/brownm09/dev-env/issues/819) and
[dev-env#825](https://github.com/brownm09/dev-env/issues/825).

## Amendment (2026-11-06) — On-demand refresh removed; a status readout makes no model call (dev-env#1299)

This reverses this ADR's Decision. The on-demand refresh meant a merge on an expired token could run
`claude -p ok --model haiku`, a model call whose only purpose was to show a status readout. That cost
appeared in no plan and no approval. This ADR weighed the refresh's latency but never its usage. The
investigation in [dev-env#1294](https://github.com/brownm09/dev-env/issues/1294) found the path had
become reachable on 2026-11-06, before it had fired.

**Decision.** `refresh_token_now()`, `attempt_token_refresh()` and `snapshot_action()` are deleted. A
missing or expired token now skips the snapshot with an advisory saying what fixes it. This ADR's other
fix stands: a still-valid "expiring" token is used, not discarded.

**Cost accepted.** A merge in a stale window shows the advisory instead of the snapshot. The next merge
after anything rewrites the token, such as running `claude` in a terminal, shows it again. The merge
itself is unaffected.

**Alternative rejected: keep the refresh and check the token source first.** Confirming a run bills the
subscription rather than the API would satisfy the
[token-source rule](https://github.com/brownm09/dev-env/issues/1280), but the call would still spend
plan limit on a readout. The check answers which budget pays, and the objection is to paying at all.

**Measured, and not settled.** #1299 left open whether `claude auth status --json` refreshes an expired
token. Two runs on 2026-11-07 disagree. At 06:30:36, 17 seconds after expiry, the file was rewritten
during the command (`expiresAt` 1794051019496 to 1794079838328, mtime 06:30:38). At 14:30:44, 6 seconds
after the next expiry, the command exited 0 with `loggedIn` true and left the file unchanged. So the
hook skips, and [dev-env#1327](https://github.com/brownm09/dev-env/issues/1327) tracks the question.

**The path this amendment removes fired that day.** The file was next rewritten at 14:33:19, 6 seconds
after the old hook, still live on `main`, saw a confirmed merge of win11-init-tools PR 115 on an expired
token. The old hook's only refresh was `claude -p ok --model haiku`, so that was very likely the first
such call, though the call itself wasn't observed.

**Left as is.** The hook's 90-second budget in `hook-dispatch.py` was sized for the refresh. The fetch's
retry and the merge confirmation now take far less, but a larger budget costs nothing unless the hook
hangs, so it isn't changed here.

## Amendment (2026-11-09) — `claude auth status` is not a reliable refresh route (dev-env#1327)

[dev-env#1327](https://github.com/brownm09/dev-env/issues/1327) asked whether `claude auth status --json`,
which makes no model call, refreshes an expired token reliably enough for the hook to run it. The rule
was set before any run: every run in the hook's environment, at least 3, must rewrite the expired token.

**Outcome: it doesn't, so the hook is unchanged.** Six runs from 2026-11-07 to 2026-11-09, in a desktop
session's environment with CLI 2.1.317, each 5 seconds after expiry unless noted. Runs 1 to 4 rewrote
`~/.claude/.credentials.json` 1 to 2 seconds after the command started. Run 5 (2026-11-09 06:33:50)
exited 0 with `loggedIn` true and left the expired token in place. Run 6, 60 seconds after expiry,
rewrote it. With the two #1299 runs above, the command refreshed in 6 of 8. The per-run readings are
[on the issue](https://github.com/brownm09/dev-env/issues/1327#issuecomment-6739077328).

**Why it's intermittent.** The `auth status` handler makes no refresh call itself. The CLI's
`--debug-file` log records no refresh even in the runs that rewrote the file, which landed 1 to 2
seconds after the command started. The likely explanation is background work that sometimes finishes
before the command exits; nothing observed the refresh directly. Run 5 was the fastest run (0.8
seconds against 1.5 to 2.5), which fits, but that's a hypothesis, not a measurement.

**Alternative not adopted: run the command again when the first run misses.** Run 6 rewrote the file
after run 5 missed, but that's one retry after one miss. The hook already fails safely by skipping with
an advisory, so a retry isn't worth adding on that evidence.
