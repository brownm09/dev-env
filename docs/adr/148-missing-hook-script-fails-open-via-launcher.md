# ADR-148: A Missing Hook Script Fails Open — Run Every Hook Through a Launcher Outside the Junction

**Date:** 2026-10-10
**Status:** Accepted
**Tags:** hooks, settings, settings-sync, hook-launcher, fail-open, fail-closed, junction, canonical-checkout, stale-canonical, lockout, pyw, exit-codes, machine-local, windows, adr-007, adr-079, adr-083, adr-096, adr-103, adr-106, adr-110, adr-139, adr-144

---

## Context

Every hook in `claude/settings.shared.json` ran as

    pyw -3 C:/Users/brown/.claude/scripts/<name>.py

When that file does not exist, Python itself exits **2** (`can't open file`). Claude Code reads
exit 2 from a hook as a **blocking** verdict[^1]. So one missing script blocks every tool call or
prompt that hook matches, in every session on the machine
([dev-env#1146](https://github.com/brownm09/dev-env/issues/1146)).

[ADR-139](139-machine-local-settings-with-shared-source-sync.md) made that a standing risk rather
than a theoretical one. The hook *wiring* now lives in the machine-local `~/.claude/settings.json`,
which keeps the newest wiring it has been given. The hook *scripts* come from the canonical
checkout's working tree through the `~/.claude/scripts` junction. Whenever the two disagree, the
machine locks up:

- **Forward case.** A sync runs before the scripts it wires exist. For example, `_settings_sync.py`
  is run from a worktree (its `SHARED_PATH` is the worktree's file) while the canonical has not
  pulled yet.
- **Regress case.** The canonical moves to an older tree, such as a drift onto an old branch (the
  [dev-env#1140](https://github.com/brownm09/dev-env/issues/1140) scenario). The live file keeps
  wiring the newer hooks. The old tree's own copy of every guard runs, and none of them knows
  about the new hooks.

**Seen live on 2026-10-09** during the canonical recovery (engineering-journal
`sessions/dev-env/2026-10-09_202426.stub.md`). The settings had been migrated to main's wiring
while the canonical working tree was still the June tree. Bash, PowerShell and Write were blocked
in every session for several minutes. The only way out was `mcp__terminal__run_in_terminal`,
which no hook covers.

A missing script is never a gate's deliberate verdict. It means the tooling is broken.
[ADR-103](103-shared-hookout-emitter.md) authoring rule 5 already declares a fail direction for
every hook (advisory hooks fail open), and a missing file bypassed it.
[ADR-110](110-escalate-persistent-dev-env-sync-ff-failures.md) Amendment 1 named this exact gap
as an accepted residual risk.

## Decision

Both defenses from the issue's option list are implemented (option c).

### 1. Every hook runs through a launcher that lives outside the junction

The command form becomes

    pyw -3 C:/Users/brown/.claude/hook-launch.py C:/Users/brown/.claude/scripts/<name>.py

`claude/scripts/_hook_launch.py` is the source. `_settings_sync.ensure_launcher` copies it to
`~/.claude/hook-launch.py`, a **real file outside every junction**. That placement is what fixes
the regress case: when the canonical regresses, everything under `~/.claude/scripts` regresses
with it, but the installed launcher does not. No guard *inside* the junctioned tree can protect
the regress case, because once the old tree is checked out, the guard is old code too.

- **Script present.** The launcher runs it **in-process** as `__main__`: it compiles the file and
  executes it in a fresh `__main__` module, the way the interpreter runs a script. It sets
  `sys.argv[0]` to the script, `sys.path[0]` to the script's own directory (so sibling
  `import _hookio` still resolves), and `__file__` (absolute, as Python 3.9+ gives a directly-run
  script). stdin, stdout, stderr, and the exit code pass
  through untouched. There is no try/except around the call, so `SystemExit(0/2)` and a crash's
  traceback with exit 1 behave exactly as before, and every hook keeps its own declared fail
  direction.
- **Script missing** (or not a file, or no argument). The launcher prints one ASCII
  `{"systemMessage": ...}` line, which reaches the user on every hook event (see the
  [`_hookout`](103-shared-hookout-emitter.md) channel table), naming the script and the recovery
  commands, and exits **0**. The recovery commands use absolute, forward-slash paths and `;`,
  never `~` or `&&`, because the hook-free terminal a locked-out user reaches for (the app's
  Terminal panel) runs Windows PowerShell 5.1, which parses neither. The repo named is the one
  the `~/.claude/scripts` junction resolves to.
- **Warnings are throttled.** A stale tree can leave a dozen or more hooks missing, each firing
  on every tool call. So a non-gate script is announced at most once per 10 minutes, keyed by a
  best-effort timestamp file under `~/.claude/scratch/hook-launch/`. Any filesystem error means
  "announce". A missing gate is announced every time, so its NOT-enforcing warning is never
  buried under the rest.
- **Stdlib only.** At module level the launcher imports only `builtins`, `os` and `sys`, all
  already loaded before any script starts. `json` and `time` are imported on the missing path
  alone. It has to work precisely when the dev-env tree is stale or broken, and it runs in front
  of every hook on every tool call. A `runpy`-based draft ran about 44 ms per hook over a direct
  run, wall-clock; it pulls in `pkgutil`, and the draft imported `json` eagerly. About 15 ms of
  that showed as import time under `-X importtime`. The compile-and-exec form measured about
  2 ms. `test_hook_launch.py` enforces this structurally rather than by timing, which is too noisy
  on CI to gate on: it forbids `subprocess`/`runpy`/`importlib`/`os.exec*`/`os.spawn*` and any
  module-level import beyond those three, and checks itself against known-bad snippets.

**Compatibility.** The command form stays `pyw -3` ([ADR-007](007-hook-command-invocation.md)),
and the hook script stays the **last token**. So `tests/_hook_wiring.py`,
`hook-liveness-check.hook_name_from_command`, and the heartbeat ledger
([ADR-106](106-hook-heartbeat-liveness-ledger.md)) are unchanged; they all read a hook's identity
from that token. `test_pyw_stdio.py` (Testing item 2) moved from `parts[2]` to the last token and
now also asserts the launcher sits in position 3. `test_settings_hook_wiring.py` (item 63) gates
the exact launcher form, using the old direct form as its known-bad reference.

### 2. The two fail-closed gates fail open on a missing script too

ADR-103 rule 5 names two fail-closed gates: `pre-auto-merge-checkpoint-gate.py`
([ADR-083](083-auto-merge-checkpoint-gate.md)) and `pre-tool-use-journal-compose-force-guard.py`
([ADR-096](096-journal-compose-mechanical-force-guard.md)). The issue proposed that the launcher
block when one of these is missing. **The user decided against that on 2026-10-10**, for two
reasons:

- **A block would hit all Bash.** A gate fails closed only after its own code has matched the
  commands it gates. A missing gate has no code, so nothing can scope the block. Both gates are
  wired on every Bash call, so failing closed would block *all* Bash, which is the lockout this
  ADR exists to end.
- **It would have repeated the incident.** On 2026-10-09 the canonical was the June tree, which
  predates ADR-083 (2026-07-05). A fail-closed launcher would have reproduced that lockout for
  Bash.

The launcher fails open for every hook. For the two gates, its message adds that the gate "is NOT
enforcing until restored". The launcher's `GATE_SCRIPTS` set is pinned equal to
`test_hook_safe_exit_guard.FAIL_CLOSED`.

### 3. The sync never wires a hook whose files are missing

Before it applies the owned `hooks` key, in both the update path and the fresh-machine path,
`_settings_sync.guard_plan` runs `hooks_guard`. The guard extracts every `.py` token from every
`type: "command"` entry (the launcher and the script) and checks each with `Path.is_file()`,
using the path **exactly as the command spells it**, because that is the path pyw will open.
Commands are tokenized with `shlex.split(posix=False)` and surrounding quotes are stripped. A
quoted path containing a space, such as a home directory that
[dev-env#1113](https://github.com/brownm09/dev-env/issues/1113)'s planned author-home prefix
rewrite could produce, is checked as one path. It does not read as "no `.py` found", which would
otherwise freeze `hooks` indefinitely. The guard withholds `hooks` when:

- any of those paths is missing;
- any command names no `.py` at all, since an extracted input must be shown non-empty
  ([ADR-144](144-gate-calibration-pass-3-dimension.md)) rather than pass as "nothing missing";
- or `hooks` is not an object.

Withholding means the **live** `hooks` value stays as it is, while the other owned and seed keys
still apply. The sync prints an ASCII warning naming the files and the recovery commands:
`git -C <canonical> checkout main; git -C <canonical> pull; py -3 <home>/.claude/scripts/_settings_sync.py`,
with absolute paths, for the PowerShell reason given above. It does this on every sync until
the files exist, then applies `hooks` on its own. Withholding is whole-key, not per entry,
deliberately: a partially applied `hooks` would be a wiring nobody wrote or tested.

**Ordering.** `ensure_launcher` runs first, on every sync, so the launcher exists before any
`hooks` naming it is written. If the launcher cannot be installed (no source and none installed),
its path is missing and the guard withholds `hooks`, and both conditions are reported. A
launcher-only update is reported as a change.

**A launcher that does not compile is never installed.** Every hook, `dev-env-sync.py`
included, runs through the installed copy. A broken one would make every hook exit 1, and exit
1 does not block, so every hook would quietly stop running and nothing would heal it.
`ensure_launcher` therefore compiles the source before writing, and keeps the previous copy if
the source fails. When the source is not the canonical checkout's (a sync run from a worktree),
the note says so, because that branch's launcher now runs machine-wide until a sync from the
canonical replaces it.

**No backup for the launcher write.** [ADR-079](079-backup-restore-convention.md) governs state
someone else could have changed, such as user config or system settings. The launcher is code
dev-env owns outright, and re-running the sync restores it, so a backup would capture nothing
recoverable. The write is still atomic (temp file plus `os.replace`) and verified by read-back. On
Windows, `os.replace` can fail while another hook process has the file open. In that case the
previous launcher stays in place, the failure is reported, and the next sync retries.

### Gate calibration (ADR-144)

The sync-time guard classifies inputs it does not enumerate, so it owes a calibration.

- **Measured property:** whether each path a command names is a file.
- **Known-good:** the real shipped `hooks`, with their paths remapped into this repo. All 84
  commands resolve.
- **Known-bad:** the same set with `dev-env-sync.py` relocated. The guard catches it.
- **Non-empty extraction:** asserted (n=84 > 50).
- **Margin:** not applicable, since the property is binary with no threshold.

The guard withholds a write and never deletes anything, so failing safe means keeping the
working wiring. The launcher's missing-or-present check is fixture-pinned in its own suite, which
includes the known-bad reference that a bare `python <missing>.py` exits 2.

## Consequences

- **The 2026-10-09 lockout cannot recur from a tree at or after this change.** In the regress
  case the user sees warnings, not blocks. In the forward case the old wiring stays in place and
  the sync says why. The exception is a tree older than this change; see the next residual risk.
- **Residual risk: a regress to a tree made after ADR-139 but before this ADR.** That tree's own
  `_settings_sync.py` runs through the launcher, but it rewrites the live `hooks` back to the
  direct form and has no guard. When `dev-env-sync.py` later returns the canonical to `main`, it
  pulls *after* it syncs. So for the rest of that turn, and the next prompt's other hooks,
  direct-form wiring runs against main's scripts. A script the older tree wired that main has
  since removed exits 2. This lasts a turn and then heals itself. A re-sync right after a
  successful pull would close it, but it needs the freshly pulled module, not the in-memory old
  one. That is tracked as a follow-up, [dev-env#1148](https://github.com/brownm09/dev-env/issues/1148).
- **Rollout: the launcher was pre-installed on this machine before merge.** `dev-env-sync.py` on
  `main` imports `_settings_sync` at process start but reads `settings.shared.json` only later.
  If a concurrent session's pull lands between the two, the old code, which has no
  `ensure_launcher` and no guard, would wire launcher-form hooks before the launcher exists.
  That is a lockout that cannot heal itself (the review of PR #1147 reproduced it). Copying
  `claude/scripts/_hook_launch.py` to `~/.claude/hook-launch.py` before merging closes the window,
  the same precaution ADR-139 took for its own migration. A machine that has never run the old
  code (a fresh `setup.sh`) seeds through the new `_settings_sync.py` and installs the launcher
  first.
- **Every hook pays a small extra cost:** one more small file compiled per invocation, about 2 ms
  measured. `test_hook_launch.py` forbids the imports that would raise it (see Decision §1); a
  timing case reports the numbers but does not gate.
- **While a gate script is missing, that gate does not enforce.** The remaining window is a
  canonical regressed onto a pre-July tree, during which `gh pr merge --auto` is ungated. Two
  things partly cover it: GitHub's per-repo `allow_auto_merge` (false on most `brownm09/*`
  repos; see ADR-083's addenda), and dev-env-sync's STALE CANONICAL escalation (ADR-110
  Amendment 1), which tells the user the canonical is off `main`.
- **Residual risk: the launcher itself.** If `~/.claude/hook-launch.py` is deleted, every hook
  exits 2 again. That includes `dev-env-sync.py`, the hook that would reinstall it, so it cannot
  heal itself. Recovery is `py -3 C:/Users/brown/.claude/scripts/_settings_sync.py` (an absolute
  path, so it runs in PowerShell as well) from a hook-free terminal, as documented in
  `docs/REFERENCE.md` and the dev-env `CLAUDE.md` architecture section. A launcher that installs
  but crashes would have the same effect without blocking, which is why `ensure_launcher` refuses
  any source that does not compile. This was
  accepted over special-casing `dev-env-sync.py` into the direct form. That would trade a
  near-impossible risk (the launcher deleted) for a different one: a missing `dev-env-sync.py`
  would block every *prompt*, which is worse than blocking Bash. It would also break the uniform
  form the wiring lint checks.
- **Rollback** means reverting the PR. The shared file and `_settings_sync.py` both return to
  their earlier versions. The reverted sync, which has no guard, applies the direct form, whose
  scripts all exist, and the installed launcher is left behind, inert.

## Alternatives Considered

- **The sync-time guard alone (option b).** Rejected as insufficient. It cannot help the regress
  case, because the guard is part of the tree that regressed. It is kept as the forward-case
  half.
- **Rewrite the commands at sync time** (keep the direct form in the tracked file, and have
  `_settings_sync` insert the launcher when it writes the live file). Rejected. The tracked file
  would no longer be what runs, the wiring lint would check a form that never executes, and the
  live-versus-shared comparison would need the same transform on both sides. #1113's prefix
  rewrite is a different case: it changes a machine-specific path, not the meaning of the command.
- **A subprocess launcher** (check existence, then spawn `pyw -3 <script>`). Rejected. It doubles
  interpreter start-up on every hook, and it adds stdin forwarding and exit-code relaying, both
  new ways to fail.
- **`runpy.run_path`.** Rejected on cost (about 44 ms per hook, measured). The compile-and-exec
  form does the same `__main__` setup in a few lines.
- **Fail closed for the two gates** (the issue's literal option 1), or for the auto-merge gate
  only, matching its import-failure direction in ADR-103 rule 5. Rejected by the user; see
  Decision §2.
- **Wrap the command in a shell existence test** (`test -f x && pyw -3 x`). Rejected, because
  ADR-007 forbids `bash -c` wrappers in hook commands: `bash.exe` is not on the Windows system
  PATH that hook commands run with.

## References

- [dev-env#1146](https://github.com/brownm09/dev-env/issues/1146): the issue; options (a)/(b)/(c).
- [dev-env#1140](https://github.com/brownm09/dev-env/issues/1140) / PR #1145: the STALE CANONICAL
  escalation, and ADR-110 Amendment 1's residual-risk section, which this ADR closes.
- [dev-env#1113](https://github.com/brownm09/dev-env/issues/1113): path portability. The
  launcher path sits under the same `C:/Users/brown/.claude/` prefix that #1113 plans to
  relocate at sync time.
- `claude/scripts/_hook_launch.py`, `claude/scripts/_settings_sync.py` (`ensure_launcher`,
  `hooks_guard`, `guard_plan`).
- Tests: `claude/scripts/tests/test_hook_launch.py` (Testing item 103),
  `test_settings_sync.py` group 8 (item 98), `test_settings_hook_wiring.py` (item 63),
  `test_pyw_stdio.py` (item 2).

---

[^1]: Claude Code hooks reference, *Exit code 2 behavior*: https://code.claude.com/docs/en/hooks
