# ADR-149: Scope of a Change — Simplicity-First and Surgical-Diff Rules, Adapted from andrej-karpathy-skills

**Date:** 2026-10-11
**Status:** Accepted
**Closes:** [dev-env#1149](https://github.com/brownm09/dev-env/issues/1149)
**Tags:** code-quality, scope-discipline, simplicity, surgical-diffs, review-skill, plan-then-optimize, claude-md, global-rule, claude-facing, third-party-guidelines, attribution, adr-028, adr-038, adr-144, adr-145
**Related:** [ADR-028](028-all-findings-merge-gate.md), [ADR-038](038-durable-preferences-documented-in-repo.md), [ADR-144](144-gate-calibration-pass-3-dimension.md), [ADR-145](145-scope-discipline-self-referential-churn.md)

---

## Source and attribution

The rules in this ADR are **adapted, not copied**, from
[andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills/tree/2c606141936f1eeef17fa3043a72095b4765b9c2)
(pinned at commit `2c60614`). Its author is Jiayuan Zhang ([@forrestchang](https://github.com/forrestchang)),
with a few outside contributors. That repo in turn distills
[Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on the mistakes
LLMs make when writing code. It ships one short `CLAUDE.md`, also packaged as a Claude Code skill and
plugin, built around four principles: **Think Before Coding**, **Simplicity First**,
**Surgical Changes**, and **Goal-Driven Execution**.

Licensing: `plugin.json` and the skill's frontmatter declare MIT, but the repo has no `LICENSE` file.
dev-env therefore restates the ideas in its own words, links the source, and reproduces no
upstream text.

## Context

The global `claude/CLAUDE.md` covers *process* scope thoroughly (plan first, issue before change,
test before PR), and [ADR-145](145-scope-discipline-self-referential-churn.md) covers dev-env's own
self-referential churn. Nothing governed the **scope and size of the code Claude writes**:

| # | Upstream principle | Already in dev-env? |
|---|---|---|
| 1 | Think Before Coding | Mostly: plan-first, Plan-then-optimize, Error Message Diligence. Missing: naming the interpretations of an ambiguous request, and pushing back when a simpler approach exists. |
| 2 | Simplicity First | No, not for code Claude writes. |
| 3 | Surgical Changes | No, and it is in tension with *Code Quality → Fix errors on encounter*. |
| 4 | Goal-Driven Execution | Mostly: Test before PR and the coverage gate. Missing: a verify check on each plan step, and a reproducing test before a bug fix. |

Two upstream lines contradict standing dev-env rules if taken literally:

- "If uncertain, ask" conflicts with *Fix everything, always — never ask which findings to address*,
  and with dev-env's "when you have enough information to act, act".
- "Mention unrelated dead code, don't delete it" conflicts with a broad reading of
  *Fix errors on encounter*.

## Decision

1. **New `### Scope of a change` subsection** under `## Code Quality`, placed right after
   *Fix errors on encounter*. It has two rules, *Simplicity first* and *Surgical diffs*, plus a
   reconciliation clause. Surgical scope governs **this PR's diff**. *Fix errors on encounter* still
   applies, and routes an unrelated fix to its own issue and PR, as its existing "separate PR" bullet
   already requires. Pre-existing dead code is not an error the session ran into: it gets mentioned or
   tiled, not deleted in passing.
2. **Plan-then-optimize gains principles 1 and 4's missing pieces.** Each numbered plan step carries a
   `→ verify: <check>`. Pass 2 gains one bullet: name the readings of an ambiguous request and say which
   one the plan follows, asking **only when the choice changes the result**. That condition is how
   "if uncertain, ask" is reconciled with *never ask which findings to address*. The bullet also says to
   mention a simpler approach when one exists, and to start a bug fix with a reproducing test.
3. **`/review` checks it.** Step 4 and the Step 5 Subagent B prompt flag out-of-scope hunks and
   speculative code as **non-blocking** `maintainability` findings. A hunk the PR body justifies is
   exempt. The check is non-blocking because "does this hunk trace to the issue" is a judgment call,
   not an objective fact. Treating it as one would make it an uncalibrated gate, which
   [ADR-144](144-gate-calibration-pass-3-dimension.md) says may report but not block.

## Alternatives Considered

- **Install the upstream plugin** (`extraKnownMarketplaces` plus `enabledPlugins` in
  `claude/settings.shared.json`). Rejected for four reasons:
  - Its content can change upstream with no history in dev-env.
  - Its skill loads only when the skill description matches the task, so it isn't always active.
  - It leaves both conflicts above unresolved.
  - It adds a third-party dependency to a global config loaded by every project.
- **Copy the skill into `claude/skills/karpathy-guidelines/`.** Rejected. It pins the content, but it
  duplicates principles 1 and 4, which dev-env already covers, and still contradicts two rules. The
  unclear license is a further reason to restate the ideas rather than copy the text.
- **Paste the upstream `CLAUDE.md` into the global file.** Rejected. It would add about 2.5 KB of
  partly duplicate, partly contradictory text to a 158 KB file that loads every session.
- **Make the `/review` scope check blocking.** Rejected. It is uncalibrated judgment (see Decision 3).

## Consequences

- Global instructions grow by about 2 KB. Every project picks up the rules automatically through the
  `~/.claude/CLAUDE.md` symlink.
- `/review` reports one more class of non-blocking finding. Under the all-findings merge gate
  ([ADR-028](028-all-findings-merge-gate.md)) each one still needs a disposition, which is the point:
  an out-of-scope hunk gets reverted, split out, or justified, and is never silently kept.
- **Unproven benefit.** Upstream's own success test is fewer unneeded changes in diffs. Per
  `## Experimental Rigor`, dev-env makes no improvement claim here. A Tier 0 probe is declared in
  [dev-env#1149](https://github.com/brownm09/dev-env/issues/1149): count the scope-of-change findings
  across the next ~10 dev-env PRs. Its only legal endings are escalate, infeasible, or shelved.
