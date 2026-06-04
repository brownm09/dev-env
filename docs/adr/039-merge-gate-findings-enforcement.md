# ADR-039 — Mechanical Enforcement of the All-Findings Merge Gate

**Date:** 2026-06-04
**Status:** Accepted
**Closes:** [dev-env#315](https://github.com/brownm09/dev-env/issues/315)
**Tags:** review, workflow, hooks, pre-tool-use, pr, merge, blocking-rule, enforcement
**Related:** [ADR-028](028-all-findings-merge-gate.md), [ADR-026](026-suppression-policy.md), [ADR-022](022-test-coverage-gate-before-pr.md)

---

## Context

[ADR-028](028-all-findings-merge-gate.md) established the policy that **all** `/review`
findings — blocking and non-blocking — must be addressed before `gh pr merge`, where "addressed"
means *fixed in the PR* or *filed as a tracked issue and linked*. It explicitly rejected leaving
non-blocking findings "as-is".

That policy was enforced only by prose in `claude/CLAUDE.md`. In practice it failed. During the
lockfile-drift work stream, PR #310 was reviewed (two non-blocking findings surfaced) and then
**merged with both left "as-is"** — the exact silent-skip ADR-028 was written to prevent. Two
factors combined:

1. **The `/review` skill softens its own output.** Non-blocking findings are printed as
   `Suggestion: … not worth it`, which reads as permission to skip. Following the skill's tone
   instead of ADR-028's mandate is an easy, invisible slip.
2. **There was no mechanical gate.** Every other merge-time invariant that actually holds —
   suppression policy ([ADR-026](026-suppression-policy.md)), test-coverage gate
   ([ADR-022](022-test-coverage-gate-before-pr.md)), test integrity, lockfile drift
   ([ADR-036](036-lockfile-drift-prevention.md)) — is backed by a grep, a hook, or a CI step. The
   all-findings gate had only a sentence. A rule with no gate depends on perfect literal
   compliance, and that is precisely where it broke.

The tell: in the same work stream, a #307 finding *was* correctly closed by filing a follow-up
issue (#308). The only difference for #310 was a discretionary "low value" judgment — a judgment
ADR-028 removes from scope. The gap is enforcement, not policy.

---

## Decision

Add mechanical enforcement in three coordinated parts.

1. **Merge-gate hook** — `claude/scripts/pre-merge-findings-gate.py`, a `PreToolUse` hook on
   `Bash` (wired in `settings.json` after `pre-pr-create-check.py`). When the command is
   `gh pr merge`, it resolves the target PR (positional ref / `--repo`, else current branch),
   runs `gh pr view --json comments,body`, and reads the **last** comment carrying the marker
   `<!-- review-findings: blocking=N non_blocking=M -->`. If `N+M > 0` and the PR body records no
   disposition (a "Review findings disposition" section, or a `<!-- findings-disposed -->`
   sentinel), it **blocks the merge with exit 2** and prints the fix-or-file instruction.
   It exits 0 (allows) when: no marker is present (PR not reviewed by `/review`), the review is
   clean, a disposition is recorded, or anything fails (fail-open — a transient `gh`/network
   error must never wedge a legitimate merge).

2. **`/review` skill emits the marker.** `claude/skills/review/SKILL.md` Step 8 now always ends
   the posted review with `<!-- review-findings: blocking=<B> non_blocking=<NB> -->` (actual
   counts) and adds a "Findings Disposition" section restating the fix-or-file requirement. The
   Non-Blocking template note explicitly forbids "not worth it" / "leave as-is" softening — a
   finding that does not meet the fix-or-file bar is dropped, not recorded.

3. **Policy text.** `claude/CLAUDE.md` and this ADR make the disposition step explicit; ADR-028
   gains a pointer here.

**Design limitation (accepted):** the gate verifies that a *conscious disposition step happened*
(counts > 0 ⇒ a disposition section must exist), not that each individual finding was genuinely
fixed or filed. It breaks the silent-merge autopilot and forces a deliberate action per reviewed
PR; it is not a proof of closure. Per-finding verification would require fragile parsing of free
text and is out of scope. The reviewer and CLAUDE.md remain responsible for honest dispositions;
the hook removes the *silent* path.

---

## Consequences

- A reviewed PR with open findings cannot be merged on autopilot: `gh pr merge` halts until the
  author records a disposition, which forces them to revisit each finding and fix-or-file it.
- The marker is the contract between skill and hook. Reviews posted before this change (no marker)
  are out of scope and merge freely — the gate is forward-looking.
- Fail-open means the gate never blocks a merge due to its own error; the trade is that a `gh`
  outage disables enforcement for that merge (CLAUDE.md still applies).
- The gate keys off the `/review` marker, not the `reviewed-by-claude` label, so a human-reviewed
  PR with no Claude marker is unaffected.
- New behavior ships with a behavioral self-test (`claude/scripts/tests/test-merge-findings-gate.sh`,
  6 scenarios) using the hook's `MERGE_GATE_TEST_JSON` seam — no cross-platform `gh` stub needed.

---

## Alternatives Considered

- **Wording-only (sharpen ADR-028/CLAUDE.md to ban "as-is").** Rejected: that is essentially the
  rule that already existed and was violated. Prose without a gate does not bind.
- **Per-finding closure verification.** Rejected for v1: requires parsing free-text dispositions
  and matching them to findings — fragile and high-maintenance. The conscious-step gate captures
  most of the value at a fraction of the complexity.
- **Block on missing review entirely (no `reviewed-by-claude` ⇒ block merge).** Rejected as scope
  creep and false-positive risk (human-reviewed PRs, merging others' PRs). Review-existence is a
  separate concern governed by CLAUDE.md and the label.
- **Fail-closed on `gh` error.** Rejected: blocking a legitimate merge because of a transient
  network error is worse UX than briefly losing enforcement; CI and CLAUDE.md remain as backstops.

---

## References

- [ADR-028 — All-Findings Merge Gate](028-all-findings-merge-gate.md) — the policy this ADR enforces mechanically.
- [Claude Code hooks — PreToolUse and exit codes](https://docs.anthropic.com/en/docs/claude-code/hooks) — exit code 2 from a `PreToolUse` hook blocks the tool call; stdout JSON `systemMessage` surfaces advisories.
- [GitHub CLI — `gh pr view`](https://cli.github.com/manual/gh_pr_view) — `--json comments,body` provides the review-comment and PR-body data the gate inspects.
