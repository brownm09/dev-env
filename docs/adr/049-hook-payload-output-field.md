# ADR-049 — PostToolUse Bash Hooks Read Output from `stdout`, Not `output`

**Date:** 2026-06-21
**Status:** Accepted
**Tags:** hooks, post-tool-use, tool_response, payload, github-project, automation, reliability

---

## Context

`post-tool-use.py` is documented (dev-env `CLAUDE.md` → GitHub Project; [ADR-023](023-generic-required-fields-issue-hook.md)) to fire after `gh issue create` / `gh pr create`, add the new item to the Dev Env GitHub Project (#3), and exit 2 with the `gh project item-edit` commands for Impact/Why.

It never did. Investigating why issue #369 was not auto-added (dev-env #377) showed the hook **ran but silently `sys.exit(0)`'d** on every invocation:

- The hook read the command output via `data["tool_response"]["output"]`.
- Claude Code's Bash hook payload exposes a command's output under **`stdout`** (and `stderr`), **not** `output`. The observed Bash tool-result shape is `interrupted, isImage, noOutputExpected, stderr, stdout` — no `output` and no `exitCode` key — and it is identical across May and June 2026 transcripts.
- So `output` was always `""`, `extract_github_url("", repo)` returned `None`, and because the dev-env `hook-config.json` sets `repo`, the hook took the *silent* `if repo: sys.exit(0)` branch — emitting nothing.

The silent branch existed for a legitimate reason (a `gh` command run from a cwd whose project targets a *different* repo should not warn), but it also swallowed this genuine failure. Two compounding factors hid the bug for the hook's entire lifetime:

1. **Silent failure** — no error, ever. Confirmed: across all retained transcripts (dev-env + lifting-logbook, months), there is not one real `[project-hook]` fire; every project-add was done by hand via the documented fallback.
2. **A working manual fallback** — the `CLAUDE.md` workflow tells the operator to add the item manually, which papered over the dead automation. [ADR-014](014-auto-move-project-item-done-on-merge.md) even records the belief that this hook pattern "caused no incidents" — it failed invisibly.

The wrong-field read was copied into several sibling hooks (`post-pr-merge-project.py`, `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, `stub-push-archive-reminder.py`), so the same latent failure affects them too.

## Decision

1. **Read command output from `stdout`/`stderr`, not `output`.** `post-tool-use.py` routes output through a pure `read_command_output(data)` helper that joins `tool_response.stdout` and `tool_response.stderr`, falling back to the legacy `output` key for forward/backward compatibility. This is the canonical way for a PostToolUse Bash hook in this repo to read command output; new hooks must use it (or the same field precedence) rather than `tool_response["output"]`.

2. **Do not let a silent branch swallow an unexpected empty.** The no-URL path now stays silent **only** when the output contains *some* GitHub URL (the legitimate different-repo case); a successful create that yields **no** URL at all emits an advisory (exit 2) instead of vanishing. A silent path that doubles as the failure mode of a config/payload bug is the reason this went undetected — diagnostics must survive the very breakage they would diagnose.

3. **Cover it with an offline regression test.** `claude/scripts/tests/test_post_tool_use.py` pins that the real `stdout`-shaped payload yields a non-empty output (the pre-fix `output` read was `""`), that the legacy fallback still works, and that the de-silence predicate distinguishes a different-repo miss from a no-URL miss.

The `exitCode` reads elsewhere default to `0` and are harmless because PostToolUse fires only after a successful tool call; they are left unchanged here.

## Consequences

- `post-tool-use.py` now fires on `gh issue create` / `gh pr create`, adds the item to the project, and prints the Impact/Why commands — eliminating the manual add step it was always meant to remove.
- A real failure (no URL in a successful create's output) is now visible instead of silent.
- The Bash-payload field constraint is recorded once here so the sibling-hook fixes and future hooks do not repeat it.
- The sibling hooks remain affected and are tracked as a follow-up. `post-pr-merge-project.py` additionally needs to derive the PR number from the **command** (the `gh pr merge` output carries no `/pull/N` URL), so its fix is more than a field rename and is deliberately out of scope here.
- General lesson for this repo's hooks: a guard's diagnostic must not depend on the same input path that the guard is meant to validate, or the failure is unobservable (compare the [ADR-034](034-error-message-diligence.md) error-message-diligence theme — believe verified signals, not assumed ones).
