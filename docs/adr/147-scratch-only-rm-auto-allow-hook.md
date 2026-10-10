# ADR-147: Auto-Approve a Scratch-Only `rm` with a Path-Resolving PreToolUse Hook

**Date:** 2026-10-09
**Status:** Accepted
**Tags:** hooks, pre-tool-use, bash, permissions, allow-rules, permission-decision, scratch, autonomous-sessions, unattended, career-playbook, cover-letter-runtime, fail-open, shell-parsing, hookout, gate-calibration, adr-007, adr-103, adr-106, adr-138, adr-139, adr-144

---

## Context

Autonomous Claude Code sessions stall on a permission prompt whenever the model deletes a
throwaway file in the scratch directory `C:/Users/brown/.claude/scratch/`. Unattended runs are
hit hardest, especially the career-playbook cover-letter runs: with nobody watching, the
session waits until a human answers the prompt ([dev-env#1134](https://github.com/brownm09/dev-env/issues/1134)).

The only rule covering this is `Bash(rm -f C:/Users/brown/.claude/scratch/*)` in
`claude/settings.shared.json` ([ADR-139](139-machine-local-settings-with-shared-source-sync.md)).
It matches a command that spells out the literal path. Sessions don't write deletions that way:

- `S=C:/Users/brown/.claude/scratch; rm -f "$S/x.json"`
- `rm "$SCR/cl_row_tmp_20261009.json"`, with `SCR=` assigned earlier in the same command
- `rm -f $S/body_arm-d_try.md`
- `cd "C:/Users/brown/.claude/scratch" && rm -f li.html`
- `T=".../scratch/pr_body.md" && ... && rm -f "$T"`

Shell state does not persist between Bash tool calls, so any variable a deletion uses is always
assigned earlier in the same command. That makes the path recoverable from the command text alone.

A sibling career-playbook change removes the deletions its skills prescribe. This ADR is the
backstop for the deletions models improvise on their own.

## Decision

### 1. A PreToolUse hook resolves the paths and emits `allow`

`claude/scripts/pre-tool-use-scratch-rm-allow.py` runs on every Bash call. When it can prove the
command does nothing but delete files inside scratch, it prints a PreToolUse
`permissionDecision: "allow"` on stdout and exits 0. In every other case it prints nothing and
exits 0, which is no decision, so the normal permission flow runs unchanged.

Per the [hooks reference](https://code.claude.com/docs/en/hooks#pretooluse-decision-control),
`allow` skips the permission prompt, deny and ask rules in settings still apply, and when hooks
disagree the precedence is deny > defer > ask > allow. So a sibling guard's block always wins. The
emitter is a new `_hookout.emit_allow(reason)` (pure core `plan_allow`), next to the existing
advisory and block channels ([ADR-103](103-shared-hookout-emitter.md)). An allow is a decision,
not an advisory, so it sits outside that module's channel table.

### 2. Every segment must qualify, not just the `rm`

The hook approves only when **every** top-level segment (split on `&&`, `;`, and newline) is one of:

- a pure literal assignment `NAME=value`, with no command substitution and every `$VAR` resolved
  from an earlier in-command assignment or `$HOME`;
- an absolute `cd <dir>` resolving inside or to scratch, which sets the base for relative targets;
- `rm` with flags only from `-f`, `-r`, `-R`, `-rf`, `-fr`, `--force`, `--recursive`, `--`, and at
  least one target, where every target resolves strictly inside scratch's realpath.

At least one segment must be an `rm`.

**Why approval must never reach a non-rm segment:** a permission decision covers the whole Bash
call. In `gh pr view 1 && rm -f "$S/x"`, the deletion is harmless, but approving the call also
approves the `gh` half, which the user's rules may deliberately prompt for. If the hook approved
"commands whose `rm` parts are safe," it would let anything ride along on a scratch deletion. So
`rm ... ; echo done` and `rm ... || true` get no decision too. That costs a prompt now and then,
but stops the hook from widening the permission surface.

### 3. A fail-closed lexer that models control flow, not just syntax

The hook has its own small POSIX-shell lexer instead of reusing `_hookio.split_top_level` or
`_shell_write_detect`. Those primitives are built for detection and **fail permissive** by design.
`split_top_level` drops an unterminated trailing segment, and `tokenize_posix` throws away
quoting, so it can't tell `'$S'` (a literal) from `"$S"` (an expansion). For a guard looking for a
hazard, a missed segment only skips a warning. Here, a missed segment would be approved. The
lexer rejects the whole command on any construct it doesn't fully model:

- `|`, a lone `&`, `<`, `>`, `(`, `)`, `{`, `}`, `!`, backticks, `$(`, `${...}` forms other than
  `${NAME}`, special parameters, a word-initial `#`, and control characters;
- **`||`**: its right side runs only when its left side failed, so
  `S=<outside> || S=<scratch>; rm -rf "$S/x"` leaves `S` outside scratch in real bash, while a
  sequential evaluator would think it inside;
- **a relative target after `;` or a newline that follows a `cd`**: that segment runs even if the
  `cd` failed, so it might run in the session's own cwd, which is usually a repo;
- **a relative `cd`**, which consults `$CDPATH`;
- **any `..` component**, because Windows collapses `..` lexically, POSIX resolves it physically,
  and bash `cd` resolves it logically. Rejecting `..` means `realpath` never has to agree with any
  of them.

Paths are rejected for an unresolved variable, an unquoted expansion whose value has whitespace or
glob characters (word splitting and globbing), a relative target with no in-command `cd`, a UNC or
drive-relative form, glob characters in a directory component, a recursive flag on scratch itself
or on a glob final component, or a target that doesn't resolve strictly inside scratch. `C:/`,
quoted `C:\`, and `/c/` drive spellings are normalized. Comparison is `os.path.normcase`
(case-insensitive on Windows) after `realpath` of both sides.

### 4. Fail open, to the status quo

Any unexpected exception becomes exit 0 with no output, which means no decision and therefore the
normal prompt. "Fail open" here means "fail to what happens without the hook," never "fail to
approval." The hook never blocks. It's wired under the PowerShell matcher too, to keep the
Bash/PowerShell mirror invariant in `test_settings_hook_wiring.py`, and makes no decision there.

### 5. Guidance so the hook actually applies

`claude/CLAUDE.md` → Platform & Environment → Scratch directory now says cleanup is optional and,
when done, belongs in its own standalone `rm -f` call. Section 6 measures why that sentence is
load-bearing.

### 6. Gate calibration ([ADR-144](144-gate-calibration-pass-3-dimension.md))

- **Measured property:** segment-kind membership plus resolved-path containment under scratch's
  realpath. There's no threshold or ratio. The one constant, `MAX_COMMAND_LEN = 8000`, is a
  heuristic bound with no calibration behind it. It can only *remove* approvals, so it can't
  cause a false allow.
- **Known-good references:** the 16 allow tests (19 commands) in `tests/test_scratch_rm_allow.py`.
  These are the five shapes above, written standalone, plus the drive-form, glob, `${NAME}`, `--`,
  `~`, and continuation variants. 19/19 allowed.
- **Known-bad references:** the 25 fall-through tests in the same file (50 commands plus 5
  malformed payloads), each tied to a rejection in section 3. 55/55 get no decision. Each Bash
  command is also checked in-process to come from the hook's own `Reject`, not from a crash
  swallowed by fail-open, so the suite can't pass vacuously.
- **Real-traffic replay:** 10,312 unique Bash commands across 1,328 session transcripts under
  `~/.claude/projects/`. 158 contain both an `rm` and "scratch". **The hook approves 0 of them.**
  Rejection reasons: 89 redirect `>`, 22 pipe, 20 `$(`/unsupported `$`, 14 `<`, 5 `cd` outside
  scratch, 5 non-rm segment, 1 `${X:-y}`, 1 brace group, 1 target outside scratch.
- **What the replay means:** the false-approval rate on real traffic is 0/158. The flip side is
  that the strict rule, by design, also approves none of the commands sessions wrote *before* this
  change. In practice they chain the cleanup onto the work that made the file
  (`gh ... > "$T" && node ... && rm -f "$T"`). So the hook resolves the prompt only once a session
  issues the deletion as its own call. The section 5 guidance is what gets sessions to do that, and
  the sibling career-playbook change removes the skill-prescribed chained deletions. Re-run the
  replay after the guidance has been live for a while. A non-zero approval count is the
  success signal, and every approved command should be spot-checked.

## Consequences

- A standalone scratch deletion, however its path is spelled, no longer prompts. Unattended
  sessions that follow the guidance don't stall on cleanup.
- Chained cleanup still prompts. That's deliberate (section 2) and measured (section 6).
- `rm -f "$S"/*` (non-recursive) and `rm -rf "$S/<subdir>"` are approved. Scratch also holds
  rebuildable machine state: `project-item-cache.json`, `hook-heartbeat/`, per-session sentinels,
  and baseline snapshots. Deleting those costs a cache refill or a stale liveness reading, not
  data. Accepted, because scratch is throwaway by definition and the recursive-glob form (the one
  that would sweep everything in one call) is rejected.
- Residual trust boundaries the hook does not defend: a `PATH`-shadowed or exported-function `rm`
  in the session environment, and a symlink or junction inside scratch whose target `realpath`
  resolves at check time but is swapped before the command runs (a check-then-use race). Both need
  prior control of the machine.
- `realpath` behavior on symlinks and junctions isn't covered by a test, because creating a
  symlink on Windows needs Developer Mode or elevation.

## Alternatives Considered

- **Broaden the permission rule** (`Bash(rm *)`, `Bash(rm -f *scratch*)`). Rejected. Permission
  rules match command text and can't resolve variables, so a broad rule approves deletions
  anywhere: `rm -rf "$S"` with `S=C:/Users/brown/Git` matches `Bash(rm *)`, and
  `rm -f C:/x/scratch/../../important` matches `*scratch*`.
- **Defer deletions to a later human-gated cleanup step.** Rejected. It moves the prompt instead
  of removing it, and a human still has to show up before an unattended run can finish.
- **Approve when the `rm` segments are scratch-only, whatever the other segments are.** Rejected
  for the reason in section 2. A hook can't know whether the user's rules would have prompted for
  the other segments, and approving the call approves them all.
- **Reuse `split_top_level` / `tokenize_posix`.** Rejected for the reason in section 3. They fail
  permissive, which is correct for detection and wrong for approval.
- **Never clean up scratch.** Partly adopted. The guidance says cleanup is optional. But sessions
  will keep improvising deletions, and a backstop that makes the safe form prompt-free costs little.

## References

- Claude Code hooks reference, PreToolUse decision control:
  <https://code.claude.com/docs/en/hooks#pretooluse-decision-control>
- Claude Code permissions: <https://code.claude.com/docs/en/permissions>
- Bash Reference Manual, Lists of Commands (`&&`, `||`, `;`) and Shell Expansions:
  <https://www.gnu.org/software/bash/manual/bash.html#Lists>,
  <https://www.gnu.org/software/bash/manual/bash.html#Shell-Expansions>
- `cd` and `CDPATH`: <https://www.gnu.org/software/bash/manual/bash.html#Bourne-Shell-Builtins>
- GNU coreutils `rm` (option permutation, refusal of `.`/`..`):
  <https://www.gnu.org/software/coreutils/manual/html_node/rm-invocation.html>
- [dev-env#1134](https://github.com/brownm09/dev-env/issues/1134);
  [ADR-007](007-hook-command-invocation.md) (hook invocation),
  [ADR-103](103-shared-hookout-emitter.md) (`_hookout`),
  [ADR-106](106-hook-heartbeat-liveness-ledger.md) (heartbeat),
  [ADR-138](138-shell-content-write-guard.md) (sibling PreToolUse Bash guard),
  [ADR-139](139-machine-local-settings-with-shared-source-sync.md) (shared settings source),
  [ADR-144](144-gate-calibration-pass-3-dimension.md) (gate calibration).
