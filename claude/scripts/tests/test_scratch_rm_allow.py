#!/usr/bin/env python3
"""Tests for pre-tool-use-scratch-rm-allow.py (dev-env#1134, ADR-147).

End-to-end: each case spawns the real hook as a subprocess with a PreToolUse
JSON payload on stdin and asserts on its stdout and exit code. The hook never
blocks, so every case must exit 0. An ALLOW case must print exactly the
PreToolUse allow JSON. A FALL-THROUGH case must print nothing, which leaves
the normal permission prompt in charge.

Hermetic: `SCRATCH_RM_ALLOW_DIR_OVERRIDE` points the hook at a temporary
scratch directory, and `HOOK_HEARTBEAT_DIR_OVERRIDE` keeps the heartbeat write
out of the real `~/.claude/scratch/hook-heartbeat/`. The hook only computes
paths (realpath) and never deletes anything, so no case touches a real file.
The tilde/`$HOME` cases run without the scratch override against a fake home:
HOME and USERPROFILE both point at a temp directory, so Python's home,
`_hookutil.SCRATCH` (the hook's default scratch), and bash's `~` all agree on
`<fakehome>/.claude/scratch`, and the approval log lands there too.

HOME resolution (ADR-147 review finding 1): Git Bash expands `~`/`$HOME` from
HOME while Python reads USERPROFILE on Windows. A divergent HOME must make
`~`/`$HOME` unresolved; a matching one (native or MSYS `/c/...` form) or an
unset one must still allow. Pinned in-process against `_home()` with
`_python_home` patched (runs everywhere) and end-to-end on Windows, where the
two variables really can disagree.

Approval log: an allow appends one timestamped line to
`scratch-rm-allow.log` in scratch; a fall-through writes nothing; the log
rotates past its cap; and an unwritable log never changes the decision.

Platform-gated cases raise `Skip` and are reported and counted as SKIPPED in
the summary line, never as a pass (Test Integrity Policy, Rule 2).

Allow cases: a literal path; `S=...; rm -f "$S/x"`; `T="..." && rm -f "$T"`;
`cd <scratch> && rm -f rel` (absolute `cd`, unbroken `&&` chain); an
absolute target after `;`; `rm -rf` on a scratch subdirectory; quoted
backslash and `/c/` drive forms; a glob in the final component; `${NAME}`
inside double quotes; `--` and flags after a target; `~` and `$HOME`.

Fall-through cases: a non-rm segment chained on (`gh`, `true`, `echo`); any
`..` component; `||` (its right side runs only on failure, so assignments
across it can't be tracked); a relative target after `;` following a `cd`
(the `cd` may have failed); a relative `cd` ($CDPATH); `$(...)` and backticks; an unresolved variable; a relative path with
no `cd`; `rm -rf` on the scratch root; recursive with a final-component glob;
a path outside scratch; a glob in a directory component; a pipe, a redirect,
background `&`; a disallowed flag; an assignment prefix on rm; an unquoted
expansion with a space; a single-quoted `'$S'` (literal, so relative); a `cd`
outside scratch; unterminated quotes; assignments/cd with no rm; malformed
stdin; a non-Bash tool.

Usage:
    py -3 claude/scripts/tests/test_scratch_rm_allow.py

Exit 0 = all pass.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "pre-tool-use-scratch-rm-allow.py"
IS_WINDOWS = os.name == "nt"

_TMP = tempfile.mkdtemp(prefix="scratch_rm_allow_")
SCRATCH = os.path.join(_TMP, "scratch")
os.makedirs(os.path.join(SCRATCH, "sub"))
HEARTBEAT = os.path.join(_TMP, "heartbeat")
OUTSIDE = os.path.join(_TMP, "outside")
os.makedirs(OUTSIDE)
# A fake home for the `~`/`$HOME` cases: HOME and USERPROFILE point here, so
# the hook's default scratch (`_hookutil.SCRATCH`) is FAKE_SCRATCH.
FAKE_HOME = os.path.realpath(os.path.join(_TMP, "home"))
FAKE_SCRATCH = os.path.join(FAKE_HOME, ".claude", "scratch")
os.makedirs(FAKE_SCRATCH)

# The forward-slash spelling a Git Bash session would type.
S = SCRATCH.replace("\\", "/")
O = OUTSIDE.replace("\\", "/")


_spec = importlib.util.spec_from_file_location("scratch_rm_allow", HOOK)
sys.path.insert(0, str(HOOK.parent))
MOD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MOD)


class Skip(Exception):
    """Raised by a case that can't run on this platform. The runner reports it
    as SKIPPED and counts it, so a degraded run is never mistaken for a full
    one."""


_UNSET = object()


def _env(scratch=SCRATCH, home=None, env_home=_UNSET):
    """Subprocess environment. *scratch* sets SCRATCH_RM_ALLOW_DIR_OVERRIDE
    (None removes it). *home* sets USERPROFILE and HOME, which is what Python
    (and so `_hookutil.SCRATCH`) resolves as home. *env_home* then overrides
    HOME alone (None removes it), to make bash's HOME diverge."""
    env = dict(os.environ)
    env["HOOK_HEARTBEAT_DIR_OVERRIDE"] = HEARTBEAT
    if scratch is not None:
        env["SCRATCH_RM_ALLOW_DIR_OVERRIDE"] = scratch
    else:
        env.pop("SCRATCH_RM_ALLOW_DIR_OVERRIDE", None)
    if home is not None:
        env["USERPROFILE"] = home
        env["HOME"] = home
    if env_home is None:
        env.pop("HOME", None)
    elif env_home is not _UNSET:
        env["HOME"] = env_home
    return env


def _run_raw(stdin_text, **env_kwargs):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=30,
        env=_env(**env_kwargs),
    )


def _run(command, tool="Bash", **env_kwargs):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command},
        "session_id": "test",
        "cwd": os.getcwd(),
    }
    return _run_raw(json.dumps(payload), **env_kwargs)


def _fake_home_env(**overrides):
    """Env kwargs for a run whose home (and so default scratch) is FAKE_HOME."""
    kwargs = {"scratch": None, "home": FAKE_HOME}
    kwargs.update(overrides)
    return kwargs


def assert_silent(r, command):
    assert r.returncode == 0, f"exit {r.returncode} for {command!r}: {r.stderr}"
    assert r.stdout == "", f"expected no decision for {command!r}, got {r.stdout!r}"
    assert r.stderr == "", f"expected empty stderr for {command!r}, got {r.stderr!r}"


def assert_allow(command, **env_kwargs):
    r = _run(command, **env_kwargs)
    assert r.returncode == 0, f"exit {r.returncode} for {command!r}: {r.stderr}"
    assert r.stdout.strip(), f"expected an allow decision, got no output for {command!r}"
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse", out
    assert out["permissionDecision"] == "allow", out
    assert out["permissionDecisionReason"].startswith("scratch-only rm: "), out
    return out["permissionDecisionReason"]


def assert_fallthrough(command, tool="Bash"):
    r = _run(command, tool=tool)
    assert r.returncode == 0, f"exit {r.returncode} for {command!r}: {r.stderr}"
    assert r.stdout == "", f"expected no decision for {command!r}, got {r.stdout!r}"
    assert r.stderr == "", f"expected empty stderr for {command!r}, got {r.stderr!r}"
    if tool == "Bash":
        # Non-vacuity: the subprocess's silence must come from a deliberate
        # Reject, not from an unexpected exception swallowed by fail-open.
        try:
            MOD.evaluate(command, os.path.realpath(SCRATCH))
        except MOD.Reject:
            pass
        else:
            raise AssertionError(f"evaluate() accepted {command!r}")


# --- Allow ------------------------------------------------------------------

def test_allow_literal_path():
    reason = assert_allow(f"rm -f {S}/x.json")
    assert "x.json" in reason


def test_allow_semicolon_var_quoted():
    assert_allow(f'S={S}; rm -f "$S/x.json"')


def test_allow_and_chain_quoted_assignment():
    assert_allow(f'T="{S}/pr_body.md" && rm -f "$T"')


def test_allow_var_assigned_earlier_unquoted_use():
    assert_allow(f"SCR={S}\nrm $SCR/cl_row_tmp_20261009.json")


def test_allow_cd_then_relative():
    assert_allow(f'cd "{S}" && rm -f li.html')


def test_allow_cd_absolute_subdir_chain():
    assert_allow(f'cd "{S}" && cd "{S}/sub" && rm -f a.txt')


def test_allow_absolute_target_after_semicolon_following_cd():
    # `;` drops the cd base, but an absolute target needs none.
    assert_allow(f'cd "{S}" && rm -f a.txt; rm -f "{S}/b.txt"')


def test_allow_rm_rf_subdir():
    assert_allow(f'S={S}; rm -rf "$S/sub"')


def test_allow_quoted_backslash_form():
    if not IS_WINDOWS:
        raise Skip("a backslash is a path separator only on Windows")
    assert_allow(f"rm -f '{SCRATCH}\\x.json'")


def test_allow_msys_drive_form():
    if not IS_WINDOWS:
        raise Skip("/c/ drive forms are Git Bash on Windows only")
    drive, rest = S[0].lower(), S[2:]
    assert_allow(f"rm -f /{drive}{rest}/x.json")


def test_allow_glob_final_component():
    assert_allow(f'S={S}; rm -f "$S"/tmp_*.json')


def test_allow_braced_var_in_double_quotes():
    assert_allow(f'S={S}; rm -f "${{S}}/x.json"')


def test_allow_double_dash_and_trailing_flag():
    assert_allow(f'rm -- "{S}/-weird.md"')
    assert_allow(f'rm "{S}/a.md" -f')


def test_allow_multiple_targets_and_reason_lists_all():
    reason = assert_allow(f'S={S}; rm -f "$S/a.md" "$S/b.md"')
    assert "a.md" in reason and "b.md" in reason


def test_allow_tilde_and_home_default_scratch():
    # No scratch override: the default scratch is `_hookutil.SCRATCH`, built
    # from Python's home, which the fake HOME/USERPROFILE point at.
    kw = _fake_home_env()
    reason = assert_allow("rm -f ~/.claude/scratch/x.json", **kw)
    assert os.path.normcase(os.path.join(FAKE_SCRATCH, "x.json")) in os.path.normcase(reason), reason
    assert_allow('rm -f "$HOME/.claude/scratch/x.json"', **kw)
    assert_allow("S=~/.claude/scratch; rm -f $S/x.json", **kw)
    # Literal path to the default scratch (finding 7: _hookutil.SCRATCH).
    assert_allow(f'rm -f "{FAKE_SCRATCH}/x.json"', **kw)


def test_allow_tilde_when_home_unset():
    # Bash falls back to the profile directory, which is Python's home.
    if not IS_WINDOWS:
        raise Skip("off Windows, Python's home IS $HOME, so it can't be unset independently")
    assert_allow("rm -f ~/.claude/scratch/x.json", **_fake_home_env(env_home=None))


def test_allow_tilde_when_home_is_msys_form_of_same_dir():
    if not IS_WINDOWS:
        raise Skip("MSYS /c/ HOME form exists only under Git Bash on Windows")
    msys = "/" + FAKE_HOME[0].lower() + FAKE_HOME[2:].replace("\\", "/")
    assert_allow("rm -f ~/.claude/scratch/x.json", **_fake_home_env(env_home=msys))


def test_fallthrough_tilde_when_home_diverges():
    # Bash would expand `~` to OUTSIDE; Python's home is FAKE_HOME. Neither is
    # trusted, so `~`/`$HOME` get no decision -- but a literal path still works.
    if not IS_WINDOWS:
        raise Skip("off Windows, Python's home is read from $HOME, so the two can't diverge")
    kw = _fake_home_env(env_home=OUTSIDE)
    for command in ("rm -f ~/.claude/scratch/x.json",
                    'rm -f "$HOME/.claude/scratch/x.json"',
                    "S=~/.claude/scratch; rm -f $S/x.json"):
        assert_silent(_run(command, **kw), command)
    assert_allow(f'rm -f "{FAKE_SCRATCH}/x.json"', **kw)


def _with_home(env_home, python_home):
    """Call MOD._home() with HOME=*env_home* (None = unset) and Python's home
    patched to *python_home*. Returns the home or the Reject raised."""
    saved_env = os.environ.get("HOME")
    saved_fn = MOD._python_home
    try:
        if env_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = env_home
        MOD._python_home = lambda: python_home
        try:
            return MOD._home()
        except MOD.Reject as exc:
            return exc
    finally:
        MOD._python_home = saved_fn
        if saved_env is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_env


def test_home_resolution_in_process():
    # Platform-independent: Python's home is patched, so divergence is real
    # even where expanduser would read HOME.
    assert _with_home(FAKE_HOME, FAKE_HOME) == FAKE_HOME
    assert _with_home(FAKE_HOME + os.sep, FAKE_HOME) == FAKE_HOME
    assert _with_home(None, FAKE_HOME) == FAKE_HOME
    for bad in (OUTSIDE, "", "relative/home"):
        got = _with_home(bad, FAKE_HOME)
        assert isinstance(got, MOD.Reject), f"HOME={bad!r} must be unresolved, got {got!r}"
    # The Reject propagates out of evaluate(), so `~` yields no decision.
    saved = os.environ.get("HOME")
    saved_fn = MOD._python_home
    try:
        os.environ["HOME"] = OUTSIDE
        MOD._python_home = lambda: FAKE_HOME
        try:
            MOD.evaluate("rm -f ~/.claude/scratch/x.json", FAKE_SCRATCH)
        except MOD.Reject:
            pass
        else:
            raise AssertionError("evaluate() resolved ~ under a divergent HOME")
    finally:
        MOD._python_home = saved_fn
        if saved is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved


def test_home_msys_root_form_is_unresolved_in_process():
    if not IS_WINDOWS:
        raise Skip("an MSYS-root path is only unrepresentable natively on Windows")
    got = _with_home("/home/someone", FAKE_HOME)
    assert isinstance(got, MOD.Reject), got


def test_allow_line_continuation():
    assert_allow(f"rm -f \\\n  {S}/x.json")


# --- Fall through -----------------------------------------------------------

def test_fallthrough_non_rm_segment_chained():
    assert_fallthrough(f'S={S}; gh pr view 1 && rm -f "$S/x"')
    assert_fallthrough(f'rm -f "{S}/x" || true')
    assert_fallthrough(f'rm -f "{S}/x"; echo done')


def test_fallthrough_dotdot_escape():
    assert_fallthrough(f'rm -f "{S}/../outside/x"')
    assert_fallthrough(f'cd "{S}" && rm -f ../x')


def test_fallthrough_command_substitution():
    assert_fallthrough(f'rm -f "{S}/$(whoami)"')
    assert_fallthrough(f"rm -f {S}/`whoami`")
    assert_fallthrough(f'S=$(echo {S}); rm -f "$S/x"')


def test_fallthrough_unresolved_variable():
    assert_fallthrough('rm -f "$UNSET_SCRATCH_VAR/x.json"')


def test_fallthrough_relative_without_cd():
    assert_fallthrough("rm -f x.json")


def test_fallthrough_recursive_on_root():
    assert_fallthrough(f'rm -rf "{S}"')
    assert_fallthrough(f'rm -rf "{S}/"')
    assert_fallthrough(f'cd "{S}" && rm -rf .')
    assert_fallthrough(f'rm -rf "{S}/sub/.."')


def test_fallthrough_recursive_with_final_glob():
    assert_fallthrough(f'rm -rf "{S}"/tmp_*')
    assert_fallthrough(f'rm -r -f "{S}"/*')


def test_fallthrough_path_outside_scratch():
    assert_fallthrough(f"rm -f {O}/x.json")
    assert_fallthrough(f'rm -f "{S}/x.json" "{O}/y.json"')


def test_fallthrough_glob_in_directory_component():
    assert_fallthrough(f'rm -f "{S}"/s*/x.json')


def test_fallthrough_pipe_redirect_background():
    assert_fallthrough(f'rm -f "{S}/x" | cat')
    assert_fallthrough(f'rm -f "{S}/x" 2>/dev/null')
    assert_fallthrough(f'rm -f "{S}/x" &')
    assert_fallthrough(f'rm -f "{S}/x" < /dev/null')


def test_fallthrough_disallowed_flag_or_verb():
    assert_fallthrough(f'rm -i "{S}/x"')
    assert_fallthrough(f'rm -v "{S}/x"')
    assert_fallthrough(f'sudo rm -f "{S}/x"')
    assert_fallthrough(f'echo "{S}/x" | xargs rm -f')
    assert_fallthrough(f'/bin/rm -f "{S}/x"')


def test_fallthrough_assignment_prefix_on_rm():
    assert_fallthrough(f'S={S} rm -f "$S/x"')


def test_fallthrough_unquoted_expansion_with_space():
    assert_fallthrough(f'S="{S}/a b"; rm -f $S')


def test_fallthrough_single_quoted_var_is_literal():
    # '$S/x' is the literal text $S/x -- a relative path -- not an expansion.
    assert_fallthrough(f"S={S}; rm -f '$S/x'")


def test_fallthrough_cd_outside_scratch():
    assert_fallthrough(f'cd "{O}" && rm -f x')


def test_fallthrough_or_operator():
    # bash never runs the second assignment, so S stays outside scratch.
    assert_fallthrough(f'S={O} || S={S}; rm -rf "$S/sub"')
    assert_fallthrough(f'rm -f "{S}/a" || rm -f "{S}/b"')


def test_fallthrough_relative_after_semicolon_following_cd():
    # If the cd fails, `rm -f x` still runs -- in the session's repo cwd.
    assert_fallthrough(f'cd "{S}/missing"; rm -f x')
    assert_fallthrough(f'cd "{S}"\nrm -f x')
    assert_fallthrough(f'cd "{S}" && rm -f a; rm -f b')


def test_fallthrough_relative_cd():
    # A relative cd consults $CDPATH.
    assert_fallthrough(f'cd "{S}" && cd sub && rm -f a.txt')


def test_fallthrough_dotdot_anywhere():
    assert_fallthrough(f'rm -f "{S}/sub/../x"')
    assert_fallthrough(f'cd "{S}/sub/.." && rm -f x')


def test_fallthrough_unterminated_quote():
    assert_fallthrough(f'rm -f "{S}/x')
    assert_fallthrough(f"rm -f '{S}/x")


def test_fallthrough_no_rm_segment():
    assert_fallthrough(f"S={S}")
    assert_fallthrough(f'cd "{S}"')


def test_fallthrough_rm_without_target():
    assert_fallthrough("rm -f")


def test_fallthrough_brace_and_subshell_forms():
    assert_fallthrough(f'rm -f "{S}"/{{a,b}}.json')
    assert_fallthrough(f'( rm -f "{S}/x" )')
    assert_fallthrough(f'S={S}; rm -f "${{S:-/}}x"')


def test_fallthrough_malformed_stdin():
    for raw in ("", "not json", "[]", "null", json.dumps({"tool_name": "Bash", "tool_input": None})):
        r = _run_raw(raw)
        assert r.returncode == 0 and r.stdout == "", (raw, r.stdout, r.stderr)


def test_fallthrough_non_bash_tool():
    assert_fallthrough(f"rm -f {S}/x.json", tool="PowerShell")
    assert_fallthrough(f"rm -f {S}/x.json", tool="Write")


def test_heartbeat_recorded():
    _run(f"rm -f {S}/x.json")
    assert os.path.exists(os.path.join(HEARTBEAT, "pre-tool-use-scratch-rm-allow.ts"))


_LOG_LINE_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ scratch-only rm: .+$")


def _fresh_scratch(name):
    path = os.path.join(_TMP, name)
    os.makedirs(path)
    return path


def test_approval_log_appends_one_line_per_allow():
    scratch = _fresh_scratch("log_append")
    log = os.path.join(scratch, MOD.APPROVAL_LOG_NAME)
    sp = scratch.replace("\\", "/")
    r1 = assert_allow(f'rm -f "{sp}/a.json"', scratch=scratch)
    r2 = assert_allow(f'rm -f "{sp}/b.json"', scratch=scratch)
    with open(log, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert len(lines) == 2, lines
    for line, reason in zip(lines, (r1, r2)):
        assert _LOG_LINE_RE.match(line), line
        assert line.endswith(reason), (line, reason)


def test_approval_log_untouched_on_fallthrough():
    scratch = _fresh_scratch("log_none")
    sp = scratch.replace("\\", "/")
    for command in (f'rm -f "{sp}/a" || true', "git status", f'rm -rf "{sp}"'):
        assert_silent(_run(command, scratch=scratch), command)
    assert not os.path.exists(os.path.join(scratch, MOD.APPROVAL_LOG_NAME))


def test_approval_log_rotates_past_cap():
    scratch = _fresh_scratch("log_rotate")
    log = os.path.join(scratch, MOD.APPROVAL_LOG_NAME)
    with open(log, "w", encoding="utf-8") as handle:
        handle.write("x" * (MOD.APPROVAL_LOG_MAX_BYTES + 1))
    sp = scratch.replace("\\", "/")
    assert_allow(f'rm -f "{sp}/a.json"', scratch=scratch)
    assert os.path.getsize(log + ".1") == MOD.APPROVAL_LOG_MAX_BYTES + 1
    with open(log, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert len(lines) == 1 and _LOG_LINE_RE.match(lines[0]), lines


def test_approval_log_failure_never_changes_decision():
    # The log path is a directory, so opening it for append fails. The hook
    # must still allow, exit 0, and print nothing on stderr.
    scratch = _fresh_scratch("log_broken")
    os.makedirs(os.path.join(scratch, MOD.APPROVAL_LOG_NAME))
    sp = scratch.replace("\\", "/")
    r = _run(f'rm -f "{sp}/a.json"', scratch=scratch)
    assert r.returncode == 0 and r.stderr == "", (r.returncode, r.stderr)
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "allow", out
    # In-process: log_approval itself never raises, even for a missing root.
    MOD.log_approval("scratch-only rm: x", root=os.path.join(_TMP, "does", "not", "exist"))


# --- Runner -----------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = skipped = failed = 0
    start = time.time()
    try:
        for t in tests:
            try:
                t()
                print(f"  PASS  {t.__name__}")
                passed += 1
            except Skip as e:
                # Not "SKIP:" -- run-hook-tests.py reads a leading "SKIP:" line
                # as a whole-file skip.
                print(f"  SKIP  {t.__name__} -- {e}")
                skipped += 1
            except Exception as e:  # noqa: BLE001
                print(f"  FAIL  {t.__name__}: {e}")
                failed += 1
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\nTests: {passed} passed, {skipped} skipped, {failed} failed ({time.time() - start:.1f}s)")
    sys.exit(1 if failed else 0)
