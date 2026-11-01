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
   checks every `.py` token, still refuses to apply hooks while any handler file is missing. The
   dispatcher skips its own path, the launcher and any repeated handler, so a misconfigured command
   cannot recurse or run a handler twice at once; the wiring lint rejects those commands too.
2. **Each handler runs as it did alone.** `hook-dispatch.py` executes each handler's file as
   `__main__`, so its own `if __name__ == "__main__":` block and crash guard run, with the same stdin
   payload and its own globals. Handlers run concurrently in threads, as Claude Code runs an event's
   hooks in parallel. `sys.stdin`/`stdout`/`stderr` are routed per thread to the handler's own buffers
   and stay routed until the process exits, so a handler still running when the result is written can
   never write into it. A child process inherits the real standard handles, as it did when its
   handler was its own hook. (A first cut also pointed file descriptors 1 and 2 at the null device;
   under `pythonw.exe` that leaves the Win32 standard handles stale, so a child redirecting only some
   streams inherits a closed or reused handle. It was removed in review.)
3. **Each handler keeps its time budget.** The timeout it had as its own command
   (`BUDGET_SECONDS`, default 30 s) counts from the dispatch start, and each handler is judged against
   its own deadline once, when the dispatcher stops waiting. A handler that missed it is dropped with a
   warning, as a killed hook got Claude Code's timeout notice, and if one is still running the process
   ends with `os._exit` rather than wait for it. The wiring lint checks each budget is at least that
   handler's floor and that the dispatcher's settings timeout is the largest budget plus 10 s, since
   Claude Code's timeout starts at process spawn.
4. **One combined output.** Blocks (exit 2 with stderr, or an exit-0 JSON `decision: "block"`) only:
   the texts joined, on stderr, exit 2. `systemMessage`s and warnings only: one joined
   `systemMessage`, exit 0. Both: the PostToolUse JSON form
   `{"decision": "block", "reason": ..., "systemMessage": ...}`, exit 0, because exit 2 would drop the
   user's messages; per the Claude Code hooks reference, `decision: "block"` prompts Claude with
   `reason`, as exit-2 stderr does. A handler's exit 1 (an error its own guard missed) and any exit-0
   JSON key the dispatcher cannot carry through become warnings rather than vanishing.
5. **Fail direction.** Stage 1's handlers are all advisory, and so is the dispatcher: a missing
   handler file gets the launcher's own warning with its throttle, and any dispatcher error exits 0.
   Stage 3, which dispatches the fail-closed ADR-083 and ADR-096 gates, must carry each handler's fail
   direction explicitly before it can apply this model to them.
6. **Heartbeats and durations.** Each handler's own `record_heartbeat` still writes its liveness file.
   Its exit-time duration record (ADR-106 Amendment 2) does not arm, because `__main__` in
   `sys.modules` stays the dispatcher; the dispatcher records each kept handler's run time instead.
   That `run_ms` spans compiling and importing the handler as well as its body, and `startup_ms` is
   null, so it is not like-for-like with the old per-hook rows: the dispatcher's own `hook-dispatch`
   row (recorded even on the `os._exit` path) is the comparison.
7. **The gates read every script.** `tests/_hook_wiring.py` gives each entry a `scripts` tuple (the
   dispatcher, then each handler), and `wired_scripts()` covers all of them, so the output-contract,
   safe-exit and heartbeat gates (Testing items 61, 62 and 68) still check every handler, as do
   `test_pyw_stdio.py` (item 2) and `hook-liveness-check.py` (item 67). The gates, the liveness check
   and the missing-script guard all tokenize a command with `_settings_sync.py_tokens`, so they agree,
   quoted paths included.

## Consequences

- **Measured (pre-registered on #851):** wall time per Bash call spent in PostToolUse hooks,
  15 alternating pairs after 2 warm-ups, the same scripts and payload on both sides.
  - On the first cut: median 548 ms before, 382 after; 90th percentile 629 before, 885 after. A
    reviewer's probe found the dispatcher's own run time steady (about 156 ms) in a slow run, so the
    slow outliers came before `main()`, from process start and the antivirus, not from the shared
    interpreter.
  - On the reviewed code, which also defers `usage-snapshot.py`'s `urllib` import (about 85 ms, needed
    only after a confirmed merge): median 342 before, 229 after; 90th percentile 464 before, 391 after.
    The machine was less loaded than in the first run, which moved both sides.
  - Spawns per Bash call for this event: 10 before, 1 after. With n=15 the 90th percentile is the 14th
    value, so one slow run moves it; the production timing report is the follow-up measure.
- **Shared process state.** Handlers now share one interpreter: `_winsubp`'s `Popen` patch, module
  state and `os.environ` reach every handler. The stage 1 survey found no handler that changes the
  working directory or environment, uses threads or `os._exit`, reads `sys.argv` or stream attributes,
  or binds `sys.std*` at import. Temp files named only by process ID could collide between threads;
  `_gh_project`'s cache and `_hookutil`'s heartbeat writes now add the thread ID. A future handler that
  changes process state must not be dispatched, or the dispatcher must isolate it.
- **Budgets bound output, not work.** Some handlers' own subprocess timeouts can exceed their budget
  (`post-pr-merge-pull`, `post-pr-merge-project` on a merge), as they could exceed their hook timeout
  before. A dropped handler keeps running until the process ends, up to the dispatcher's largest
  budget, and its report is lost, as a killed hook's was. A handler stuck in a C call that holds the
  GIL delays every budget until it returns; none is known.
- **Failure scope.** One dropped handler costs only its own output. A dispatcher crash or a dispatcher
  killed at its timeout costs every handler's output for that call, where separate processes failed
  one at a time; the margin over the largest budget and the tests keep that rare. If the canonical
  regresses to before this change, `hook-dispatch.py` itself is the missing file, and all ten handlers
  stop behind one throttled launcher warning.
- **Rollback** is a settings change: restore the per-handler commands in
  `claude/settings.shared.json`. No handler's behavior changed.

## Alternatives Considered

- **Keep the handler list inside the dispatcher** (`hook-dispatch.py post-bash`). Rejected: the wiring
  would no longer show what runs, and the missing-script guard (ADR-148) would stop covering handlers.
- **Run handlers sequentially in one thread.** Rejected: on a merge several handlers wait on network
  calls (GitHub, `git pull`, the usage API), and running them one after another would sum their waits
  against a single timeout, where Claude Code ran them in parallel.
- **`runpy.run_path(..., run_name="__main__")`.** Rejected: it swaps `sys.modules["__main__"]`, which is
  process-global, so concurrent handlers would race, and it makes each handler arm an exit-time duration
  record running to the dispatcher's exit.
- **Point file descriptors 1 and 2 at the null device while handlers run.** Tried and removed: under
  `pythonw.exe` the Win32 standard handles go stale (see Decision 2).
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
