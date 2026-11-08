# ADR-162: Deny Five Rarely Used Tools So Every Session Starts Smaller

**Date:** 2026-11-07
**Status:** Accepted
**Closes:** [dev-env#1296](https://github.com/brownm09/dev-env/issues/1296)
**Tags:** token-efficiency, cost, settings, permissions, tools
**Related:** [ADR-139](139-machine-local-settings-with-shared-source-sync.md), [ADR-156](156-compact-early-and-default-subagents-to-sonnet.md), [ADR-159](159-instruction-files-hold-rules-not-stories.md)

---

## Context

A desktop session's first request carries about 135k bytes of tool definitions, re-read on every call. Over 14 days to 2026-11-06 (317 sessions, all projects), several saw little or no use, and `Artifact` alone was 36,538 bytes with one use. A deny rule naming a tool "removes the tool from Claude's context entirely" ([permissions](https://code.claude.com/docs/en/permissions)).

## Decision

The user chose, tool by tool, to add these to `permissions.deny` in `claude/settings.shared.json`:

| # | Tool | Bytes (14-day table) | Sessions using it | What goes with it |
|---|---|---|---|---|
| 1 | `Artifact` | 36,538 | 1 | Publishing hosted pages from a session |
| 2 | `PowerShell` | 9,747 | 68 | The dedicated tool. Bash can still run `powershell.exe`, and the Bash guards see those commands |
| 3 | `Workflow` | 5,462 | 0 | Multi-agent workflow scripts |
| 4 | `SuggestPluginInstall` | 2,510 | 0 | The plugin install card |
| 5 | `SuggestSkills` | 1,587 | 0 | The skill suggestion card |

`permissions` is an owned key, so the sync from [ADR-139](139-machine-local-settings-with-shared-source-sync.md) replaces the live value and the rule reaches every project on the machine. A sync test pins the list, so a change to it is deliberate.

`PowerShell`'s hook matchers stay in place. They do nothing while the tool is denied, and they are what makes removing the rule a one-line revert.

### Measured

Two headless sessions in this repo, on the subscription (`apiKeySource: none`), one with the deny rules through `--settings`. They used about $0.31 and $0.35 at list price, which estimates plan-limit use and isn't a charge.

| # | Measure | Without | With | Change |
|---|---|---|---|---|
| 1 | Tool definition bytes in `api-request-shape` | 80,490 | 28,695 | -51,795 |
| 2 | First-call tokens | 105,075 | 87,114 | -17,961 |
| 3 | Tokens per removed byte | | | 0.347 |

A headless session never offers `SuggestPluginInstall` or `SuggestSkills`, so the table covers the other three. Both appear in desktop sessions, whose saving should be about 55,800 bytes, or about 18,400 tokens per call at the 0.33 tokens per byte fitted across 105 sessions. A one-pair measurement of a size can't speak to whether sessions cost less overall, because a removed tool's work may move elsewhere.

### The two listings

Left as they are, since each option removes something and that is the user's call. Measured the same way, against a first call of 105,075 tokens and a 22,511-byte skill listing:

| # | Setting | Skill listing bytes | First-call tokens |
|---|---|---|---|
| 1 | [`skillListingMaxDescChars`](https://code.claude.com/docs/en/settings-reference#skilllistingmaxdescchars): 250 | 11,711 | 101,511 |
| 2 | [`skillListingBudgetFraction`](https://code.claude.com/docs/en/settings-reference#skilllistingbudgetfraction): 0.005 | 16,597 | 103,166 |
| 3 | [`disableBundledSkills`](https://code.claude.com/docs/en/settings-reference#disablebundledskills) plus [`skillOverrides`](https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings) `name-only` on 10 skills | 11,111 | 108,562 |

Row 3 grew because `disableBundledSkills` enlarged the `Workflow` definition from 5,412 to 22,540 bytes. That growth doesn't apply once `Workflow` is denied, but the combination wasn't measured.

For deferred tool names, no MCP setting reaches the desktop app's connectors, because the app delivers them in-process, so the documented route is disconnecting them at [claude.ai/customize/connectors](https://claude.ai/customize/connectors) ([MCP](https://code.claude.com/docs/en/mcp#how-connectors-reach-claude-code)). A headless session doesn't load those connectors, so a deny rule on their names couldn't be measured here.

## Alternatives considered

- **Deny the other five rows too.** Not chosen: the user left `ScheduleWakeup` and `SearchPlugins` unselected and stated no preference on `mcp__visualize__show_widget`, `mcp__visualize__read_me` and `ReportFindings`.
- **Deny per project.** Rejected: the table covers every project, and the saving would then need a rule in each repo.
- **Deny only the unused tools, keeping `PowerShell` and `Artifact`.** Not what the user chose, and those two are about 83% of the bytes removed.

## Consequences

- **A project can't turn a tool back on.** "If a tool is denied at any level, no other level can allow it" ([permissions](https://code.claude.com/docs/en/permissions)), so a project that needs `Artifact` needs this list changed.
- **A session already running keeps a definition it has seen,** while tool search is active, though it can't call the tool ([prompt caching](https://code.claude.com/docs/en/prompt-caching#denying-an-entire-tool)). The saving starts with new sessions.
- **Prompts that say "use PowerShell" now run it through Bash.** The two PC health scheduled tasks did, and their prompts (machine-local, under `~/.claude/scheduled-tasks/`) now say to run `powershell -NoProfile -Command` with the commands in single quotes, or a `.ps1` file for anything longer. A single-quoted command keeps `$_` and `$(...)` intact. Originals are backed up in `~/.claude/backups/scheduled-tasks-2026-11-07/`.
- **Rollback:** remove the entries in a PR. The next sync replaces the live value.
