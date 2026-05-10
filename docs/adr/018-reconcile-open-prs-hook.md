# ADR 018 — Auto-Reconcile open-prs.jsonl Against GitHub State

**Date:** 2026-05-10
**Status:** Accepted
**Tags:** hooks, open-prs, UserPromptSubmit, github, token-efficiency

---

## Context

`open-prs.jsonl` in `brownm09/engineering-journal` tracks PRs whose full lifecycle (open →
review → merge) spans multiple sessions. It is updated manually by Claude: append on
`gh pr create`, remove via a Node.js one-liner on `gh pr merge`.

In practice the file drifts. Two failure modes recur:

1. **Session-end aborts.** A session that merges a PR and then loses context (e.g., via
   `/compact` before stub writing, or an unexpected stop) may skip the removal step.
   The merged PR stays in the file indefinitely.

2. **Missing additions.** A session that opens a PR in a complex multi-file context
   occasionally omits the `open-prs.jsonl` append step.

The consequences compound: `post-compact.py` reads the file to emit review reminders, and
Claude reads it at session start to establish context. Stale or missing entries cause Claude
to surface wrong reminders, miss real open PRs, or spend turns reconciling discrepancies —
each wasted turn costs tokens.

Confirmed instance at ADR authoring time: dev-env #170 (ADR 012) was open but had no entry;
a session ended without appending it.

---

## Decision

Add a `UserPromptSubmit` hook (`reconcile-open-prs.py`) that self-heals the file at session
start by querying GitHub directly.

**Behaviour:**

1. Runs once per session via a per-session sentinel file in `scratch/` (same pattern as
   `turn-count-hook.py`).
2. Discovers all `sessions/*/open-prs.jsonl` files in the engineering-journal repo.
3. For each entry, calls `gh pr view <N> --repo <owner>/<repo> --json state`.
4. Removes entries whose state is `MERGED` or `CLOSED` (rewrites the file in-place; deletes
   the file when empty).
5. On `gh` failure (network, auth, timeout), leaves the entry untouched — conservative.
6. Emits a `systemMessage` to stdout listing surviving open PRs and any removals, so Claude
   has correct context from turn 1 without a manual file read.
7. Does **not** commit. Modified files are left dirty and picked up by the next stub commit
   via the existing `git add ... open-prs.jsonl` step in CLAUDE.md.
8. Always exits 0 — never blocks.

**Why a hook instead of a manual utility:**
Staleness is invisible until it causes a problem. A hook that silently self-heals on every
session start eliminates the failure mode without requiring user or Claude action. A manual
script would only help after the user notices a problem, which is too late to prevent the
token waste.

**Why fix silently rather than emit a warning:**
A warning prompts Claude to act, which costs at least one additional turn. The hook can fix
the file cheaper than the warning can be processed. The `systemMessage` still surfaces
removals so there is no silent data loss.

**Why once-per-session rather than every prompt:**
`gh pr view` makes one API call per tracked PR. Running on every prompt would add latency and
API load for no additional value — PR state changes slowly, and a single reconciliation at
session start is sufficient coverage.

---

## Alternatives Considered

**Warning-only (no file modification from hook):**
Emitting a message and letting Claude fix the file would preserve the "hooks never modify
state" property, but costs one Claude turn per stale entry. Token waste is the problem being
solved; adding turns defeats the purpose.

**Detect and add missing PRs (not just remove stale ones):**
Could scan recent stubs/manifests to find PRs that should be in the file but aren't. Deferred:
requires parsing stub markdown, is fragile (stubs may not follow the exact format), and the
missing-addition failure mode is less frequent than the missed-removal mode.

**Run as a daily routine rather than a session hook:**
Would batch the `gh` calls but introduce a lag window where stale data affects sessions.
Session-start execution gives the strongest freshness guarantee.

---

## Consequences

- `open-prs.jsonl` is correct from the first turn of every session, eliminating the stale-data
  class of wasted turns.
- Sessions with no open PRs see no output (hook exits silently).
- Sessions with open PRs receive a `systemMessage` that replaces the manual file read Claude
  would otherwise perform.
- One `gh pr view` call per tracked PR per session — negligible cost given the typical file
  has 0–4 entries.
- Stale sentinel files cleaned up after 30 days (same maintenance window as `turn-count-hook.py`).
