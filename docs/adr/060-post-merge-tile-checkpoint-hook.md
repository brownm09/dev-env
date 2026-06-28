# ADR-060: Post-Merge Tile Checkpoint Hook

**Date:** 2026-06-28
**Status:** Accepted
**Tags:** hooks, post-tool-use, tiles, spawn-task, post-merge, enforcement, ADR-046

---

## Context

[ADR-046](046-post-merge-followup-tiles.md) established the rule: after every `gh pr merge`, spawn
a `spawn_task` tile for each out-of-scope fix, deferred work item, or idea surfaced during the
session. The rule is documented in `claude/CLAUDE.md` under *Git Workflow → Capture post-merge
follow-ups as tiles*.

The rule is **purely behavioral** — no hook enforces it. In practice, the merge event triggers a
dense cleanup sequence: journal stub update, board-item move to Done, manifest shard update,
open-PR shard deletion. By the time that sequence completes and Claude reports the merge done, the
tile checkpoint is no longer salient. The motivating incident is lifting-logbook PR #600
(2026-06-28), where a tile for issue #599 was missed despite `feedback_tile_spawning_checkpoint.md`
recording the same failure from a prior session.

The journal reminder hook (`pr-merge-reminder.py`) provides the proven enforcement model: a
PostToolUse Bash hook that detects a successful merge and exits 2, emitting a blocking stderr
`systemMessage` that Claude must acknowledge before its response continues. The tile checkpoint
needs identical treatment.

One pre-existing gap: `pr-merge-reminder.py` bails early when `exitCode != 0` (line 279), so it
silently misses worktree merges where `gh pr merge` exits non-zero on local-cleanup steps but the
remote merge already succeeded. The new hook uses the `is_successful_merge` pattern (exit 0 **or**
stdout success marker) to handle this correctly.

---

## Decision

Add `claude/scripts/post-merge-tile-checkpoint.py` as a new PostToolUse Bash hook. It fires on
every successful `gh pr merge` and exits 2 with the message:

> [tile-checkpoint] PR merged — spawn follow-up tiles now via spawn_task for any out-of-scope
> fixes, deferred work, or ideas surfaced during this session. Only an explicit 'skip tiles' user
> instruction exempts this checkpoint.

Register it in `claude/settings.json` immediately after `pr-merge-reminder.py` in the PostToolUse
Bash hooks list.

### Detection predicate

```python
def is_successful_merge(command: str, exit_code: int, output: str) -> bool:
    if "gh pr merge" not in command:
        return False
    return exit_code == 0 or output_has_merge_marker(output)
```

This mirrors the three sibling merge hooks (`post-pr-merge-reclaim.py`, `post-pr-merge-pull.py`,
`post-pr-merge-project.py`), all of which use the same simple substring check plus the
`_hookio.output_has_merge_marker` test.

### Why simple substring over `_scan_top_level`

`pr-merge-reminder.py` uses a stack-based parser (`_scan_top_level`) to avoid matching `gh pr
merge` inside heredoc bodies or quoted strings. The three sibling hooks use the simpler `"gh pr
merge" in command` check instead. The output marker check provides a second confirmation that the
merge actually succeeded — a false positive from `echo "gh pr merge"` in a heredoc would not
produce the success marker. Consistency with the three siblings outweighs marginal robustness gain.

### Why exit 2 (blocking) not exit 0 (advisory)

The motivation is that the behavioral instruction gets crowded out by the merge-cleanup sequence.
An advisory (exit 0) would be silently ignored in the same way. Exit 2 forces acknowledgment.

### No `_winsubp`

The hook reads stdin and writes stderr only — no subprocess calls. `_winsubp` is required only
when `subprocess.run`/`Popen` is used ([ADR-007](007-hook-command-invocation.md)).

---

## Consequences

- Every successful `gh pr merge` now produces two blocking reminders: one for the journal stub
  (from `pr-merge-reminder.py`) and one for the tile checkpoint (from this hook). Both must be
  acknowledged before Claude continues.
- Worktree merges that exit non-zero but produce a success marker now trigger the tile checkpoint
  (fixing the gap in `pr-merge-reminder.py` for this specific checkpoint — the journal gap is
  pre-existing and out of scope here).
- The opt-out is an explicit user instruction — "skip tiles" anywhere in the current session. Plan
  approval does not count as such an instruction (per the clarification in the ADR-046 addendum,
  [dev-env#413](https://github.com/brownm09/dev-env/issues/413)).
- Test: `claude/scripts/tests/test_post_merge_tile_checkpoint.py` — four offline cases covering
  the predicate's four code paths.

---

## Alternatives considered

**Extend `pr-merge-reminder.py` to include the tile message.** This would merge two concerns
(journal stub reminder and tile checkpoint) into one script, making the journal hook responsible
for enforcing ADR-046. Rejected: single-responsibility principle; the journal hook is already
complex enough; separate hooks can be independently disabled or tested.

**Advisory exit 0.** Would fire on every merge without blocking, keeping the message visible in
the transcript. Rejected: the problem is precisely that non-blocking reminders disappear into the
merge-cleanup sequence.

**Integrate into `posttooluse-inert-advisory.py` (Stop hook safety net).** The Stop hook fires
at session end, not at merge time; the tile checkpoint is meaningful only immediately after the
merge, while the session context is intact.

---

## References

- [ADR-046](046-post-merge-followup-tiles.md) — Post-Merge Follow-Up Tiles (the rule being enforced)
- [ADR-007](007-hook-command-invocation.md) — Hook command invocation (`pyw -3`, `_winsubp`)
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) — Shared `_hookio` helpers
- [dev-env#415](https://github.com/brownm09/dev-env/issues/415) — Issue tracking this change
