# ADR-159: The Global Instruction File Holds Rules, Not Stories, and Its Size Is Capped

**Date:** 2026-11-06
**Status:** Accepted
**Closes:** [dev-env#1216](https://github.com/brownm09/dev-env/issues/1216)
**Tags:** token-efficiency, cost, claude-md, instructions, gate
**Related:** [ADR-038](038-durable-preferences-documented-in-repo.md), [ADR-154](154-token-attribution-and-shared-pricing.md), [ADR-156](156-compact-early-and-default-subagents-to-sonnet.md), [ADR-144](144-gate-calibration-pass-3-dimension.md)

---

## Context

Re-reading the starting context on every call is about a fifth of spend ([dev-env#1213](https://github.com/brownm09/dev-env/issues/1213)). An earlier estimate treated `claude/CLAUDE.md` as about 42k tokens, assuming 4 bytes per token, and left about 110k tokens of a session's start unexplained.

The transcripts store each request's system blocks, tools and messages, so the start can be measured exactly. Fitting first-call tokens against bytes across 105 sessions gave 0.395 tokens per byte for the instruction block and 0.33 for the rest, with a median error of 417 tokens. At those rates the block is about half of a dev-env session's start, and `claude/CLAUDE.md` alone was about 70k tokens. The unexplained 110k came mostly from the wrong bytes-per-token assumption.

| # | Source, dev-env session on 2026-11-06 | Bytes | Tokens, estimated |
|---|---|---|---|
| 1 | `claude/CLAUDE.md` at 177,620 bytes | 177,620 | 70,000 |
| 2 | Project `CLAUDE.md` and memory index | 48,600 | 19,000 |
| 3 | Tool definitions (54 tools) | 137,000 | 45,000 |
| 4 | Skill listing, deferred tool names, agent listing, hook context, environment | 38,400 | 13,000 |
| 5 | System prompt | 34,400 | 11,000 |

The file also grew about 15k bytes between the issue being filed and this work landing, to 181,551 bytes. Most of its length was the story of each rule: the incident that prompted it, what was ruled out, how it was verified. Those stories matter when someone doubts or overrides a rule, and an agent following the rule rarely needs them.

## Decision

1. **`claude/CLAUDE.md` holds the rule; `docs/REFERENCE.md` → Instruction Detail holds the story.** Long rules were shortened in place, keeping their names, because hooks, skills and other files point at them by name. The old text moved there verbatim. A rule's commands, flags and thresholds stay in the file when an agent needs them at that moment.
2. **Cap the file with a test.** `test_claude_md_size.py` fails above 96,500 bytes, about 5% over the 91,796 bytes left after this trim. A later change that needs more room moves a story out or raises the ceiling with a reason in the PR.
3. **Check its links in the same test,** because rules now depend on them. Every local link must resolve, including anchors into `docs/REFERENCE.md`.
4. **Fix what was already wrong in passing.** The file said the turn warning fires at 50 and 25 and that two hooks hadn't landed. They had.

Gate calibration, per [ADR-144](144-gate-calibration-pass-3-dimension.md): the measured property is the file's byte count with line endings normalized. Known-bad: 166,696 and 181,551 bytes, the file before this trim, both rejected. Known-good: the trimmed file, the worst and only known-good observation, about 5% under the ceiling. It reads the file directly, and a separate assertion rejects an implausibly small read, so it can't pass on nothing.

## Alternatives considered

- **Portal and the shunt plugin, as in the article that started this work.** Rejected in [ADR-156](156-compact-early-and-default-subagents-to-sonnet.md): large-file reads are about 1% of spend here, and it would add a third party.
- **`@` imports.** Rejected: they load eagerly, so they save nothing ([memory documentation](https://code.claude.com/docs/en/memory)).
- **Deleting the stories.** Rejected: they're why a rule can be challenged sensibly later, and [ADR-038](038-durable-preferences-documented-in-repo.md) needs durable reasoning kept visible.
- **A warning instead of a failing test.** Rejected: the file grew through many small additions, each reasonable alone, which is the case a warning doesn't stop.
- **A lower ceiling, nearer the 200 lines the documentation suggests.** Rejected for now. The file holds hundreds of rules, and cutting further would mean dropping or merging them, a different decision from moving their stories.

## Consequences

- **An agent has less context when a rule is unclear.** It has to follow the link. Rules that matter at an exact moment keep their commands so it usually doesn't have to, and a rule that gets misapplied can win its detail back.
- **Each session starts about 35k tokens smaller,** at the fitted rate: 89,755 bytes at 0.395. That's direct arithmetic on the request, not a measured change in spend. Spend per merged PR is too noisy to show a change this size ([ADR-156](156-compact-early-and-default-subagents-to-sonnet.md)), so no saving is claimed beyond it.
- **`docs/REFERENCE.md` is about a third larger.** It doesn't load by default, so this has no per-session cost.
- **Open PRs that edit moved lines will conflict once.** Their rule edits should go in as a short line, with any story in Instruction Detail.
- **The rest of the start is untouched.** Tool definitions and the project `CLAUDE.md` are separate decisions, tracked in follow-up issues.
- **Rollback:** revert the PR. The test and the file go together.
