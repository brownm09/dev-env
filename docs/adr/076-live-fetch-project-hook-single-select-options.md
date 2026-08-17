# ADR-076 — Live-Fetch Single-Select Field Options in the Project-Board Add Hook

**Date:** 2026-07-02
**Status:** Accepted
**Tags:** hooks, post-tool-use, github-project, hook-config, graphql, drift, reliability, automation, documentation

---

## Context

`post-tool-use.py` fires after `gh issue create` / `gh pr create`, adds the item to the configured GitHub Project, and exits 2 with `gh project item-edit` commands for the project's `required_fields` — for a `single_select` field (e.g. lifting-logbook's Epic, dev-env's own Impact), the reminder includes an `options` list of `{name: option-id}` pairs so the session can pick one without a separate lookup ([ADR-023](023-generic-required-fields-issue-hook.md)).

That `options` map comes entirely from the cached `.claude/hook-config.json`, with no mechanism to detect or refresh drift. `updateProjectV2Field` with `singleSelectOptions` is a full replacement — every option mutation (add/remove/rename an option) regenerates **all** option IDs for that field (global `claude/CLAUDE.md` → "GitHub Projects — single-select option mutation hazard"). The documented Backup-and-restore procedure updates the project's CLAUDE.md table with the new IDs, but had no step to refresh `hook-config.json` itself, since that file isn't part of the CLAUDE.md PR.

This drifted silently at least three times before detection:

- lifting-logbook's 2026-05-08 Epic-field mutation (CLAUDE.md: "an Observability epic addition wiped assignments on all 89 project items") left `hook-config.json` serving pre-mutation IDs and a truncated option list from that point forward, discovered only when `gh issue create` for lifting-logbook#637 printed 6 stale Epic options — missing 4 real ones entirely — with IDs matching none of the live GraphQL field state ([dev-env#527](https://github.com/brownm09/dev-env/issues/527)).
- [lifting-logbook#628](https://github.com/merickvaughn/lifting-logbook/pull/628) independently rediscovered and fixed the local symptom (corrected `hook-config.json`, patched lifting-logbook's own CLAUDE.md procedure to name the cache) before dev-env#527's own remediation comment found the prior art.
- At least one further session hit the same drift before either fix landed (dev-env#527's problem statement).

Every recurrence was silent: the hook has no way to know its cached `options` map might be wrong, so a session either got a GraphQL error from a stale ID or — the sharper risk — a stale ID that happened to still validate against some other field/option, mis-tagging an item with no error at all.

Two things this ADR does **not** fix, deliberately out of scope: `.claude/propose.json`'s `epics` array (used by `/propose`, no live-fetch equivalent — still covered by the documented procedure only) and `milestone`-type `required_fields` entries (not single-select fields; GitHub milestones drift via a different trigger, per lifting-logbook's CLAUDE.md).

## Decision

`post-tool-use.py` live-fetches a `single_select` field's current options via `gh api graphql` at reminder time, using the cached `hook-config.json` value only as a fallback when the live call fails — and labels the reminder so the difference is visible instead of silent.

1. **`fetch_live_field_options(field_id)`** queries `node(id: $id) { ... on ProjectV2SingleSelectField { options { id name } } }` ([GitHub GraphQL API reference](https://docs.github.com/en/graphql/reference/objects#projectv2singleselectfield)) via `gh api graphql` ([`gh api` manual](https://cli.github.com/manual/gh_api)), with `field_id` passed as a `-f` variable (never string-interpolated into the query, so it cannot inject query structure). Returns `None` on any failure — missing field_id, no `gh` binary, timeout, non-zero exit, or a malformed response — never raises. `timeout=10`, matching the tighter end of this hook's existing live-call budget (`add_to_project` already runs synchronously in this same hook at `timeout=20`, so this is one more bounded call in an already-network-touching hook, not a new latency category).
2. **`_parse_live_options(raw)`** is the pure response parser, split out so it's unit-tested without a subprocess: `{name: id}` on a well-formed response (including a genuinely empty `options: []`, which must **not** collapse to the same `None` that means "the fetch itself failed" — those are different states with different fallback behavior), `None` on anything malformed.
3. **`_resolve_required_fields(config)`** extracts the existing `required_fields` / legacy `epic_field_id`+`milestones` normalization (previously inlined in `format_reminder`, untested) into its own pure function, so `format_reminder` (rendering) and the new `fetch_live_required_field_options` (live-fetch target discovery) share exactly one backward-compat rule instead of two that could silently diverge.
4. **`fetch_live_required_field_options(required_fields, *, fetch_fn=fetch_live_field_options)`** attempts a live fetch for every `single_select` field that has a `field_id`, independently per field — one field's failure never affects another's. `fetch_fn` is injectable so the field-selection logic (which fields get attempted, keyed correctly by `field_id`) is unit-tested without a real subprocess call.
5. **`format_reminder`** gains an optional `live_options: dict[field_id, {name:id} | None] | None = None` keyword. Per `single_select` field: if `live_options` is `None` (no fetch attempted, e.g. any caller other than `main()`'s success path) or the field's `field_id` isn't a key in it, render exactly as before this change — cached `options`, no label. If it's a key with a dict value, use that **live** data, labeled `(live)`. If it's a key with `None` (fetch was attempted and failed), fall back to the cached `options`, labeled `(cached — live fetch failed; may be stale)`. The default parameter keeps every pre-existing behavior byte-identical; only `main()`'s already-successful-add path computes and passes `live_options`.
6. **`main()`** computes `live_options` only after `add_to_project` already returned a real `item_id` — a failed create never pays for the extra call.

**Why fallback-on-failure rather than blocking on a failed live fetch:** this hook's established convention (safe-exit guard, `add_to_project`/`canonical_root_via_git` degrading to `None`) is that a hook interruption from a flaky network must never block the session or lose the reminder entirely — a slightly-possibly-stale reminder with a visible warning is strictly better than no reminder. This also means the fix does not change the hook's risk profile: every new failure mode degrades to the pre-existing cached behavior, just now labeled.

**Why replace rather than only warn:** an earlier design considered detecting drift (comparing live vs. cached and warning without changing the printed IDs) rather than replacing them. Given a successful live fetch is definitionally current data, showing it directly is strictly more useful than telling the session to go run its own `gh api graphql` query — and the field IDs used in the `gh project item-edit --field-id ...` command itself are unaffected (they're stable GraphQL node IDs for the *field*, not its options; `updateProjectV2Field`'s option-ID churn is scoped to `options`, confirmed by the mutation call itself requiring the field ID as an argument).

**Why not also live-fetch milestones or `/propose`'s `epics`:** out of scope for this fix — see Context. The documented CLAUDE.md procedure (updated in this same PR) remains the only safeguard for those.

## Consequences

- The specific failure mode from dev-env#527 — a session trusting a silently-stale `options` list — can no longer happen without at least a visible `(cached — live fetch failed; may be stale)` label; a healthy live fetch shows current data unconditionally.
- `format_reminder`, previously untested, now has coverage for its `single_select` rendering branches (live, cached-after-failure, and the unlabeled backward-compat default) via the new `live_options` parameter.
- `_resolve_required_fields`'s legacy-config normalization is now unit-tested for the first time, and shared instead of duplicated.
- One additional `gh api graphql` call per `single_select` `required_fields` entry (typically one per onboarded project today: lifting-logbook's Epic, dev-env's own Impact), only on the already-successful add path. Verified end-to-end by hand against dev-env's live Impact field during development (`fetch_live_field_options('PVTSSF_lAHOAjEKvM4BWKFezhRgkNc')` returned the exact live `{High, Medium, Low}` IDs matching the cached `CLAUDE.md` table — i.e., no drift currently present — and an invalid field ID degraded to `None` as designed).
- The global `claude/CLAUDE.md` Backup-and-restore procedure (same PR) now names `hook-config.json` / `.claude/propose.json` explicitly as caches to refresh after an option mutation — this remains necessary defense-in-depth for the parts this ADR doesn't cover (milestones, `/propose`, and this fix's own fallback path).
- The "gitignored and machine-local" claim about `hook-config.json` in `post-tool-use.py`'s docstring, ADR-023, `_worktree_canon.py`, `reconcile-project-board.py`, `test_post_tool_use.py`, `docs/REFERENCE.md`, and this ADR's sibling [ADR-052](052-worktree-config-canonical-fallback.md) is corrected (same PR) to reflect that this is dev-env's own convention, not a universal one — lifting-logbook deliberately tracks the file in git.

**Family:** [ADR-023](023-generic-required-fields-issue-hook.md) (the `required_fields` schema this extends), [ADR-052](052-worktree-config-canonical-fallback.md) (the sibling fix to *where* the config is found; this ADR fixes *how fresh* its `single_select` data is), [ADR-073](073-shared-worktree-canon-gh-project-modules.md) (the shared `_gh_project`/`_worktree_canon` modules this hook also uses).

## References

- [GitHub GraphQL API reference — Objects](https://docs.github.com/en/graphql/reference/objects#projectv2singleselectfield) — `ProjectV2SingleSelectField` and its `options` field.
- [`gh api` manual](https://cli.github.com/manual/gh_api) — `-f` variable syntax for `gh api graphql`.
- [dev-env#527](https://github.com/brownm09/dev-env/issues/527) — the motivating issue and full incident diagnosis.
- [lifting-logbook#628](https://github.com/merickvaughn/lifting-logbook/pull/628) — the prior, project-local fix this ADR generalizes and structurally supersedes for the `single_select` case.
