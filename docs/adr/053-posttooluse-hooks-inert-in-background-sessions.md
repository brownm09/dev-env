# ADR-053 — PostToolUse Hooks Are Inert in Background / SDK-Launched Sessions (Upstream Harness Limitation)

**Date:** 2026-06-22
**Status:** Accepted
**Tags:** hooks, post-tool-use, claude-code-harness, background-task, spawn-task, sdk, upstream-limitation, observability, reliability

---

## Context

In some Claude Code sessions, **every** `PostToolUse` hook configured in `~/.claude/settings.json`
silently fails to run — all seven Bash hooks (`pr-merge-reminder.py`, `post-tool-use.py`,
`post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, `stub-push-archive-reminder.py`,
`post-pr-merge-project.py`, `usage-snapshot.py`) and the `memory-write-advisory.py` Write hook —
while the `UserPromptSubmit`, `PreToolUse`, and `Stop` hooks from the **same** settings file run
normally in that session. A peer session on the same machine, same settings, same binary, same
`version` (`2.1.181`), same `userType` (`external`), fires all four event classes.

This is a **third, distinct** failure mode for `post-tool-use.py`, separate from the two already
fixed:

- **Not** [ADR-049](049-hook-payload-output-field.md) / [ADR-050](050-shared-hookio-sibling-hook-fixes.md)
  (the hook *runs* but reads command output from the wrong payload field and exits 0). Here the hook
  **never runs at all**.
- **Not** [#378](https://github.com/brownm09/dev-env/issues/378) (the gitignored `hook-config.json`
  is absent from a worktree, so the hook runs but bails). Here the config is present and the hook
  still never runs.

Discovered while investigating [#378](https://github.com/brownm09/dev-env/issues/378) → filed as
[#381](https://github.com/brownm09/dev-env/issues/381).

### Evidence (transcript forensics)

Comparing the affected session `pensive-taussig-448bc7` (`ea0b2f4e…`) to an interactive peer
`happy-brown-ace7c4` (`6d0bb519…`), reading the `attachment` records the harness writes when a hook
produces output:

| Hook event (attachment = hook produced output) | Affected | Peer |
|---|---|---|
| UserPromptSubmit | 5 | 3 |
| PreToolUse | 1 | 6 |
| **PostToolUse** | **0** | **4** |
| Stop | 6 | 2 |

Four observations make the cause **per-session launch context**, not hook code or configuration:

1. **It is not the [ADR-049](049-hook-payload-output-field.md)/[ADR-050](050-shared-hookio-sibling-hook-fixes.md)
   output-field bug.** The affected session created issues **#378** (before the `stdout` fixes
   `cd8bae7`/#377 and `06c7775`/#380 merged to `main`), **#381**, and **#383** (both after). All
   three `gh issue create` calls produced **no** PostToolUse attachment. The hook scripts run from
   `~/.claude/scripts` (junctioned to the canonical worktree on `main`), so the code *changed*
   between #378 and #383 while the inert behavior did **not** — the failure is fixed at session
   launch, independent of hook-code version. Peer sessions emitted PostToolUse attachments even with
   the *pre*-#377 code, so the output-field bug has a different, non-silent signature.

2. **Launch fingerprint.** The affected session's `stop_hook_summary` system record lists the three
   configured Stop hooks **plus two `{"command":"callback"}` entries** — in-process hooks injected by
   the harness. The interactive peer has **no `system` records at all**. Per the Claude Agent SDK
   hooks reference, `callback` hooks are the in-process hook type registered through `options.hooks`;
   their presence is the signature of a session driven **programmatically** (the desktop app's
   background-task / `spawn_task` runner) rather than interactively.

3. **A second inert integration in the same session.** `mcp__ccd_session__spawn_task` was called but
   its UI "chip" never rendered. That is a *different* desktop integration which also depends on a
   foreground session UI — so the common factor is the background/SDK launch context, not any one
   feature.

4. **Upstream corroboration.** Two upstream reports match the two symptoms exactly:
   [anthropics/claude-code#42336 "[BUG] PostToolUse hooks not triggering in Desktop App"](https://github.com/anthropics/claude-code/issues/42336)
   and [anthropics/claude-code#53494 "Cloud-spawned tasks … invisible in desktop UI"](https://github.com/anthropics/claude-code/issues/53494).

A scan of ~745 retained transcripts (18 days) shows the inert signature is **rare and intermittent
within** the background/SDK-launched class — most background sessions *do* fire PostToolUse — so it
is **not deterministically reproducible from configuration**. Settings-loading explanations (e.g. an
SDK `settingSources` that omits the file) are ruled out: the same file's UserPromptSubmit / PreToolUse
/ Stop hooks load and run; only the PostToolUse dispatch is affected.

> The upstream mechanism (how the background-task runner wires PostToolUse dispatch) is internal to
> Claude Code and not determinable from transcript/filesystem artifacts. The in-repo forensics above
> are empirical; the upstream-issue references are the closest primary sources. The hook *model*
> (in-process `callback` hooks vs. settings-file command hooks, additive per-event merge) is from the
> [Claude Agent SDK hooks reference](https://code.claude.com/docs/en/agent-sdk/hooks).

## Decision

1. **Treat this as an upstream Claude Code Desktop harness limitation, not a dev-env defect.** No
   hook-code change can make an un-invoked hook run. [ADR-049](049-hook-payload-output-field.md) and
   [ADR-050](050-shared-hookio-sibling-hook-fixes.md) (read `stdout`, not `output`) are necessary but
   cannot help when the hook never executes.

2. **Record the detection signature** so a future session recognizes the state instead of
   re-investigating it (as happened #378 → #381). A session is in the inert state when **all** hold:
   - PostToolUse side-effects are silently absent — no project-board add after `gh issue create`; no
     Done-move, journal-stub reminder, or usage snapshot after `gh pr merge`; no memory-write advisory;
   - the session was launched as a **background task / via `spawn_task`** (equivalently, its
     `stop_hook_summary` carries `{"command":"callback"}` hooks), and
   - `spawn_task` chips do not render.

3. **Recovery is the existing manual fallbacks** — the GitHub Project manual add (dev-env `CLAUDE.md`
   → GitHub Project § Fallback), the manual board Done-move, and the manually-written journal stub.
   They remain the system of record for affected sessions and are now explicitly tied to this mode.

4. **Prefer launching workflow-critical sessions interactively.** When work must run as a background
   task, perform the PostToolUse-dependent post-actions (board add/move, stub, usage snapshot) by hand.

5. **Proposed follow-up (out of scope here):** a reliable-event safety net — a `Stop` or
   `UserPromptSubmit` hook (both fire in affected sessions) that detects a `gh issue create` /
   `gh pr merge` whose PostToolUse side-effect did **not** occur and surfaces a one-line advisory —
   would convert the silent gap into a visible one. Deferred because (a) it is a distinct feature with
   its own test burden, (b) reliably detecting "the side-effect didn't happen" needs board state and
   the `project` scope, and (c) a working manual recovery already exists. Tracked as a follow-up
   tile/issue, not built in the documenting PR.

## Consequences

- The failure is documented and recognizable; future sessions stop re-discovering it from scratch.
- The dev-env automations that depend on PostToolUse — #3 board add ([ADR-023](023-generic-required-fields-issue-hook.md)),
  Done-move on merge ([ADR-014](014-auto-move-project-item-done-on-merge.md)), the journal-stub
  reminder ([ADR-021](021-auto-stub-on-pr-push.md)), the post-merge usage snapshot, and the
  memory-write advisory ([ADR-048](048-memory-immortalization-issue-pairing.md)) — are **best-effort,
  not guaranteed**, in background/SDK-launched sessions. The manual fallbacks are authoritative there.
- A true fix can only land upstream; [#381](https://github.com/brownm09/dev-env/issues/381) links the
  upstream reports.
- Observability framing (echoing [ADR-049](049-hook-payload-output-field.md)): a silent automation gap
  is the hazard. Here the gap is upstream and unfixable in-repo, so the in-repo mitigation is this
  documentation plus the proposed reliable-event advisory — both keep the gap from being invisible.
