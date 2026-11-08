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
   launch, independent of hook-code version. The output-field bug is also a *different, non-silent*
   signature: it suppresses `post-tool-use.py`'s URL *extraction*, not PostToolUse *dispatch* — with
   the [ADR-049](049-hook-payload-output-field.md) de-silencing it exits 2 with a "no URL" advisory
   (an attachment), rather than producing no PostToolUse attachment at all as seen here.

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

## Amendment (2026-11-07) — `start_session` sessions aren't inert, and zero attachments doesn't mean inert

[dev-env#1320](https://github.com/brownm09/dev-env/issues/1320). Tiles now launch with
`start_session`, and the launch language above had no measurement behind it for that path.

**Answer: sessions launched with `start_session` are not in the inert state.** All 162 transcripts
the app links to a `start_session` call show their PostToolUse hooks running, including the 20 with
no PostToolUse attachment. The inert state hasn't been seen in any launch path since 2026-10-14.
This doesn't show it can't recur, and the 2026-06 case above stands as recorded.

### How it was measured

A read-only scan on 2026-11-07 of the 356 top-level transcripts then on this machine. Sessions still
running at that time count as they stood then, so a session with no attachments may gain one later.

- **Which sessions.** The app's session metadata carries `spawnedFrom`, and `cliSessionId` names the
  transcript. The spawning tool comes from the parent transcript, whose tool result names the same
  task ID. This replaces matching by prompt text.
- **Attachments.** Records with `attachment.hookEvent == "PostToolUse"`, the signal
  `posttooluse-inert-advisory.py` uses.
- **A signal that doesn't depend on hook output.** `post-tool-use-cwd-track.py` runs after every
  successful Bash or PowerShell call, prints nothing, and is the only writer of
  `~/.claude/scratch/bash_state_<session-id>.json`. The file existing means PostToolUse dispatched in
  that session, and its modified time says when it last did.
- **Per call.** `post-tool-use-cwd-track.dur.jsonl` records each hook run, but not which session ran
  it. A line was credited to a call only when no other transcript, subagents included, had a shell
  result within 5 seconds. The same window 30 seconds later, where nobody had a shell result, is the
  control.

### Results

| # | Launch path | Transcripts | With attachments | Without | Without, and `bash_state` file exists |
|---|---|---|---|---|---|
| 1 | `start_session` | 162 | 142 | 20 | 20 |
| 2 | `spawn_task` | 51 | 45 | 6 | 6 |
| 3 | Linked, spawning tool not found | 11 | 10 | 1 | 1 |
| 4 | Not linked (top-level) | 132 | 98 | 34 | 17 |

In row 4, 9 of the 17 without a file never ran a shell call, and the other 8 predate 2026-10-14.

- **Why attachments mislead.** A hook leaves an attachment only when it prints something, so only
  1,387 of 26,305 successful shell calls carry one. A session that creates and merges nothing can
  run its hooks all day and leave none. PR creation is the closest thing to a reliable trigger:
  131 of 133 in `start_session` sessions left one. Pushes and merges often don't (265 of 899 and
  82 of 168).
- **The file's calibration.** Since the last missing file, on 2026-10-14, 275 of 275 transcripts with
  a successful shell call have the file, 43 of them with no attachments. None of the 9 transcripts
  with no shell call has one. For 19 of the 20 `start_session` transcripts without attachments, the
  file was written within 60 seconds of the last successful shell call. The other was written 6,263
  seconds after it, which still shows dispatch.
- **Per call.** In `start_session` sessions without attachments, 45 of 45 attributable shell results
  have a ledger line within 5 seconds, against 0 in the control windows. Sessions with attachments,
  as the known-good reference, scored 640 of 641, with 1 in the control windows. The ledger only
  reaches back to 2026-11-06, so this covers 11 of the 20 sessions.
- **The limit of the file.** It has no known-bad reference in its window, because no inert session
  has occurred since it became reliable. Its evidence is that only that hook writes it. Absence
  before 2026-10-14 proves nothing: the oldest surviving file is from 2026-10-10, and 21 sessions
  with attachments from that period have none.

### The 11 transcripts with no attachments in the earlier scan

The earlier scan found 147 of 158 by prompt text. Rebuilding it from records written before its
timestamp reproduces exactly 158, 147 and 11. They are neither short sessions as a class nor inert
sessions:

- **1 never ran a tool.** It has one user record and no assistant turn.
- **10 ran their hooks silently.** All 10 have the file. For 9 it was written within 60 seconds of
  the last successful shell call, and for the other it was written later. Their tool-call counts at
  scan time ranged from 10 to 171. One of them later produced 3 attachments.

### What still can't be settled

Three top-level sessions from 2026-10-08, on version 2.1.286, created a PR with no attachment and
left none all session. These are the only sessions where PR creation stayed silent and the whole
session did too. All three predate `start_session`, whose first transcript is from 2026-10-16. Their
files are gone, and absence proves nothing from that period, so they remain undetermined. That week
also had silent PR creates in sessions that did have attachments, which weakens the case further.

### Corrections to the Decision above

- **The detection signature in Decision 2 no longer identifies this state.**
  - `{"command":"callback"}` in `stop_hook_summary` doesn't discriminate: 292 transcripts have it and
    have attachments.
  - `promptSource: "sdk"` appears on nearly every desktop session.
  - The `spawn_task` chip condition has no equivalent now that tiles are `start_session` cards.
- **Use this check instead.** Find `bash_state_<session-id>.json` and compare its modified time with
  the session's last successful shell call. If it's current, hooks ran, so look for a reason the
  specific hook stayed silent. If it's missing despite successful shell calls in a recent session,
  that is the inert signature.
- **A failed tool call runs no PostToolUse hook.** 0 of 892 errored shell calls carry an attachment,
  which matches the separate
  [`PostToolUseFailure` event](https://code.claude.com/docs/en/hooks). A chain whose last command
  exits non-zero therefore skips the board add even when its `gh pr create` succeeded.
- **Decision 4 no longer applies as written.** Launch path isn't a reason to prefer an interactive
  session. The manual fallbacks in Decision 3 stay correct whenever a side effect is really missing.

### The advisory

`posttooluse-inert-advisory.py` fired for real in 5 transcripts, and its message blamed a
"background/SDK-launched" session each time:

| # | Transcripts | What it was |
|---|---|---|
| 1 | 2 (`start_session`) | Hooks were fine: PostToolUse attachments appeared later in the same session |
| 2 | 1 (`start_session`) | Hooks were fine: 6 of 6 attributable calls ran. The create was in a chain that exited non-zero, so its board add really was skipped, and the merge printed nothing |
| 3 | 1 (linked) | Hooks were fine: the `gh pr merge` had failed on a rate limit, so nothing was missing |
| 4 | 1 (top-level, 2026-10-08) | Undetermined, one of the three above |

So the message now states what was observed, says this doesn't prove hooks didn't run, gives the
check above, and no longer names a launch path. Its detection is unchanged. It still points at a real
gap in row 2, and stopping it from firing is a separate decision with its own calibration:
[dev-env#1331](https://github.com/brownm09/dev-env/issues/1331).
