# ADR-156: Compact at 400k and Default Subagents to Sonnet, and Leave the Main-Session Model to a Registered Test

**Date:** 2026-11-06
**Status:** Accepted
**Closes:** [dev-env#1215](https://github.com/brownm09/dev-env/issues/1215)
**Tags:** token-efficiency, cost, settings, auto-compact, subagents, model-selection, experiment
**Related:** [ADR-154](154-token-attribution-and-shared-pricing.md), [ADR-139](139-machine-local-settings-with-shared-source-sync.md), [ADR-115](115-experimental-rigor-protocol.md)

---

## Context

The spend report from [ADR-154](154-token-attribution-and-shared-pricing.md) priced 2026-10-22 to 2026-11-05 at $3,025.67 at published API prices. Main-session calls over 400k of context were 33.1% of that, subagents 25.6%, and main-session Opus 5.5 about 74%.

Sessions on models with a native 1M window compact at about 967K unless a lower window is set ([model configuration](https://code.claude.com/docs/en/model-config#default-auto-compact-thresholds)), and nothing here set one.

This work started from [an article on Portal and the shunt plugin](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90), which saves the expensive model for reasoning by redirecting large file reads to a cheap worker. Full reads of files over 350 lines were about 1% of spend here, so that target is small on this workload, while the principle still applies to the larger costs above.

## Decision

1. **Seed `autoCompactWindow: 400000`.** A replay of the recorded sessions modelled the net saving, after the cost of the compactions themselves, at 9.0% for 300k, 9.8% for 400k, 7.6% for 500k and 5.5% for 600k. The replay can't see work repeated because a compaction lost detail, so it ranks the window choices and doesn't state a saving. `/autocompact` overrides the seed for a model, as its value takes precedence in the same file ([settings reference](https://code.claude.com/docs/en/settings-reference#modelsettings)).
2. **Seed `env.CLAUDE_CODE_SUBAGENT_MODEL: sonnet`.** It is only a default: a model named by the call or the agent definition wins ([subagents](https://code.claude.com/docs/en/sub-agents#choose-a-model)). General-purpose subagents on Opus 5.5 were about 15% of spend, so at half the price this can save at most about 7%, and less where a call names its model.
3. **Leave the main-session model alone, and test it.** The desktop app's Code tab picker remembers its own choice. 255 of the last 287 sessions started on Opus 5.5 while `model` said Sonnet 4.6, so no setting in this repo controls it. [dev-env#1266](https://github.com/brownm09/dev-env/issues/1266) decides it with a registered replay.
4. **Correct the two stale seeds.** `model` moves to the `sonnet` alias, which can't go stale the way a dated model name did and which matches `## Model Selection`. It only affects terminal sessions, and only on a machine without the key. `effortLevel` moves from `max`, which neither settings key accepts ([model configuration](https://code.claude.com/docs/en/model-config#adjust-effort-level)), to `xhigh`, the nearest accepted value.
5. **Run the first two as a Tier 0 probe.** Spend per merged PR over the last three 2-week windows was about $11, $49 and $18, so comparing periods of normal work can't detect a change of this size. The settings stay on because they are cheap to reverse, and no saving is claimed from them. The registered probe is on [dev-env#1215](https://github.com/brownm09/dev-env/issues/1215#issuecomment-6689405470).

## Alternatives considered

- **Portal and the shunt plugin, as the article describes.** Rejected: they target about 1% of spend here and would add a third party that sees file contents.
- **`opusplan` as the default main-session model.** Rejected for now. Sonnet 5.5's window is 1M, so the context limit isn't the obstacle. Only 24 switches out of plan mode happened across 293 sessions, so most sessions would run entirely on Sonnet with no test of whether that holds up, and it isn't confirmed to be selectable in the Code tab. [dev-env#1266](https://github.com/brownm09/dev-env/issues/1266) can still select it.
- **`CLAUDE_CODE_SUBAGENT_MODEL_FORCE`.** Rejected: it would also move Explore and Plan, but it ignores the `model` field in agent definitions and stops a call naming a model, which would override the Opus pin on `/review` ([subagents](https://code.claude.com/docs/en/sub-agents#run-every-subagent-on-one-model)). Explore is only about 0.5% of spend.
- **`CLAUDE_CODE_DISABLE_1M_CONTEXT`.** Rejected: it compacts at 200K, and a typical session starts at about 167k, which leaves about 33k of working room.
- **`env` as an owned key.** Rejected: owned keys replace the live value on every sync, which would delete any variable a machine set for itself. As a seed key, `env` keeps a machine's existing block whole, at the cost that such a machine gets no new shared variables.
- **A registered before and after test of these two settings.** Rejected: at this noise it would almost certainly end inconclusive, and a replay long enough to reach 400k would be expensive.

## Consequences

- **Compaction can lose working detail mid-task.** This is the main risk. The probe reads compactions per session after 14 days and writes down any repeated work it finds.
- **Subagents doing hard reasoning may do worse on Sonnet.** A call can name a model when the work needs it.
- **Rollback is cheap, but order matters.** While the seed is in the shared file, the sync writes a removed key back on the next prompt, so removing a key by hand or restoring an older backup doesn't stick. To turn a setting off without a revert, keep the key present: run `/autocompact auto`, and set `env` to `{}`. To undo it for good, revert the seed first, then remove both keys from `~/.claude/settings.json`. The sync never removes them itself.
- **Sessions already over 400k may compact when the setting lands.** Claude Code reloads most settings edits in running sessions ([settings](https://code.claude.com/docs/en/settings#when-edits-take-effect)), so a session mid-task may compact on its next turn. The probe's 14 days start after that first day.
- **A seed reaches an existing machine only if the key is absent.** The sync writes `autoCompactWindow` and `env` to this machine with its normal backup, but not a changed `model`, which needs a one-time edit.
