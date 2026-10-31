# ADR-151: Run a Group of Hooks in One Process Through an In-Process Dispatcher

**Date:** 2026-10-31
**Status:** Accepted

## Context

Claude Code starts one process per wired hook command. On angstrom-b every process start costs
0.5-4.3 s under Defender and Acronis, and a Bash call fires about 25 hooks. The baseline taken before
this change (dev-env#851, 2026-10-30, from `hook-timing-report.py` over about 6 hours of production
fires) put the 10 PostToolUse (Bash) hooks at 336 ms of run time against 4,313 ms of startup, and the
15 PreToolUse hooks at 256 against 7,899 ms (sums of per-hook medians). The hooks' own work is a small
fraction of what each Bash call costs the machine.

dev-env#851 asks for one dispatcher per event group, staged: PostToolUse first (all advisory), then
UserPromptSubmit and Stop, then the PreToolUse gates. This ADR records the model; stage 1
(dev-env#1173) applies it to PostToolUse for the Bash and PowerShell matchers.

## Decision

1. **The wiring names every handler.** A dispatched group is one command:
   `pyw -3 <launcher> <scripts>/hook-dispatch.py <scripts>/a.py <scripts>/b.py ...`. What runs stays
   visible in `claude/settings.shared.json`, and `_settings_sync.hooks_guard` (ADR-148), which already
   checks every `.py` token, still refuses to apply hooks while any handler file is missing.
2. **Each handler runs as it did alone.** `hook-dispatch.py` executes each handler's file as
   `__main__`, so its own `if __name__ == "__main__":` block and crash guard run, with the same stdin
   payload and its own globals. Handlers run concurrently in threads, as Claude Code runs an event's
   hooks in parallel. `sys.stdin`/`stdout`/`stderr` are routed per thread to the handler's own
   buffers, and file descriptors 1 and 2 point at the null device while handlers run, so a child
   process writing to an inherited stdout cannot corrupt the result.
3. **Each handler keeps its time budget.** The timeout it had as its own command
   (`BUDGET_SECONDS`, default 30 s) counts from the dispatch start. A handler still running at its
   budget is dropped, as a killed hook would be, and the process ends with `os._exit` so it does not
   wait on that thread. The dispatcher's own settings timeout must exceed the largest budget; the
   wiring lint enforces it as the largest handler floor.
4. **One combined output.** Exit-2 stderr only: the texts joined, exit 2. `systemMessage` JSON only:
   one joined `systemMessage`, exit 0. Both: the PostToolUse JSON form
   `{"decision": "block", "reason": ..., "systemMessage": ...}`, exit 0, because exit 2 would drop the
   user's messages; per the Claude Code hooks reference, `decision: "block"` prompts Claude with
   `reason`, as exit-2 stderr does. Exit 1, an error the handler's own guard missed, is ignored, as
   Claude Code shows it only in the transcript view.
5. **Fail direction.** Stage 1's handlers are all advisory, and so is the dispatcher: a missing
   handler file is skipped with a warning, as the launcher does, and any dispatcher error exits 0.
   Stage 3, which dispatches the fail-closed ADR-083 and ADR-096 gates, must carry each handler's fail
   direction explicitly before it can apply this model to them.
6. **Heartbeats and durations.** Each handler's own `record_heartbeat` still writes its liveness file.
   Its exit-time duration record (ADR-106 Amendment 2) does not arm, because `__main__` in
   `sys.modules` stays the dispatcher; the dispatcher records each finished handler's run time
   instead. The dispatcher heartbeats as `hook-dispatch`.
7. **The gates read every script.** `tests/_hook_wiring.py` gives each entry a `scripts` tuple (the
   dispatcher, then each handler), and `wired_scripts()` covers all of them, so the output-contract,
   safe-exit and heartbeat gates (Testing items 61, 62 and 68) still check every handler. The wiring
   lint (item 63) accepts the dispatcher form, rejects a dispatcher with no handlers, and compares
   Bash/PowerShell mirroring by handler. `hook-liveness-check.py` watches every handler's heartbeat.

## Consequences

- **Measured (stage 1, pre-registered on #851):** wall time per Bash call spent in PostToolUse hooks,
  15 alternating pairs after 2 warm-ups, the same scripts and payload on both sides: median 548 ms
  before, 382 ms after; 90th percentile 629 before, 885 after. Spawns per Bash call for this event:
  10 before, 1 after. Two of the 15 dispatcher runs were slow (885 and 924 ms): handlers in one
  process share an interpreter and its import lock, where ten processes ran in parallel on separate
  cores. The production timing report is the follow-up measure.
- **Shared process state.** Handlers now share one interpreter: `_winsubp`'s `Popen` patch, module
  caches and `os.environ` reach every handler. The stage 1 survey found no handler that changes the
  working directory or environment, uses threads or `os._exit`, or reads `sys.argv`. A future handler
  that does must not be dispatched, or the dispatcher must isolate it.
- **One dropped handler costs only its own output**, as a killed hook did. A dispatcher crash costs
  every handler's output for that call, where separate processes failed one at a time; the dispatcher
  is small and tested to keep that rare.
- **Rollback** is a settings change: restore the per-handler commands in
  `claude/settings.shared.json`. No handler changed.

## Alternatives Considered

- **Keep the handler list inside the dispatcher** (`hook-dispatch.py post-bash`). Rejected: the wiring
  would no longer show what runs, and the missing-script guard (ADR-148) would stop covering handlers.
- **Run handlers sequentially in one thread.** Rejected: on a merge several handlers wait on network
  calls (GitHub, `git pull`, the usage API), and running them one after another would sum their waits
  against a single timeout, where Claude Code ran them in parallel.
- **`runpy.run_path(..., run_name="__main__")`.** Rejected: it swaps `sys.modules["__main__"]`, which is
  process-global, so concurrent handlers would race, and it makes each handler arm an exit-time duration
  record running to the dispatcher's exit.
- **Merge the handlers into one hook.** Rejected: it would rewrite ten tested hooks, lose their
  separate suites and fail directions, and couple unrelated behavior; #850's post-merge fold is
  subsumed by dispatching them instead.
- **Always exit 2 when any handler blocks.** Rejected: on a merge it drops the post-merge pull and
  reclaim messages meant for the user.

## References

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks): parallel execution of an
  event's hooks, exit-code semantics, and the PostToolUse JSON `decision`/`reason`/`systemMessage` fields.
- dev-env#851 (top-level), dev-env#1173 (stage 1), dev-env#848 (timing ledger, ADR-106 Amendment 2),
  dev-env#849 (hot-path gating), dev-env#850 (merge-path consolidation).
- ADR-007 (hook invocation), ADR-103 (output channels), ADR-106 (heartbeats), ADR-148 (launcher).
