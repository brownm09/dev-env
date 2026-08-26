#!/usr/bin/env python3
"""Unit tests for hook-liveness-check.py (ADR-106).

Exercises the pure helpers offline (tmp dirs for heartbeat files, hand-built
settings.json fixtures -- no real ~/.claude/scratch and never the live
claude/settings.shared.json, so this suite's pass/fail doesn't drift with production
wiring changes): `hook_name_from_command` (script-basename extraction),
`wired_hook_events` (settings.json hooks-block parsing into hook -> {events}),
`exempt_hooks` (PostCompact/Notification-only exemption), `stale_hooks` (the
missing/stale/fresh decision against injected heartbeat files and a
caller-supplied `now`), `_age_desc`, `format_warning`, and
`format_self_check_failure` (the distinct self-check-failure advisory text --
`/review` finding on PR #752: without this the hook could go silently dark on
a broken settings.json parse while its own heartbeat stayed fresh). The
writer side (`_hookutil.record_heartbeat`) is covered in test_hookutil.py.

Also exercises `wired_hook_events()` against the REAL `claude/settings.shared.json`
for agreement with `tests/_hook_wiring.wired_script_events()` -- a second
`/review` finding on PR #752: these are two independent settings.json parsers
(deliberately not consolidated yet -- see ADR-106's Settings-parsing scope
decision, deferred to Phase E), so nothing else would catch a future
settings.json schema change applied to one and not the other.

A behavioral layer drives the real `main()` over stdin via subprocess, with
HOME/USERPROFILE isolated to a temp dir (so `_hookutil.HEARTBEAT_DIR` and the
once-per-session sentinel never touch the real `~/.claude/scratch/`, mirroring
`test_stop_journal_stub_checkpoint.py`'s pattern) and `SETTINGS_PATH` overridden
via the `HOOK_LIVENESS_SETTINGS_PATH` test seam (so a broken/malformed
settings.json can be exercised without touching the real file): the healthy
no-stale-hooks path is silent; a stale non-exempt hook emits an
`additionalContext` warning; an unreadable/malformed settings.json and a
settings.json missing this hook's own wiring each emit the distinct
self-check-failure advisory; and the once-per-session debounce silences a
second call in the same session while a different session's call still fires.

Usage:
    py -3 claude/scripts/tests/test_hook_liveness_check.py

Exit 0 = all pass.
"""
import json
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

hook_liveness_check = importlib.import_module("hook-liveness-check")


# --- hook_name_from_command ------------------------------------------------

def test_hook_name_from_command_matches_py_script() -> str:
    got = hook_liveness_check.hook_name_from_command(
        "pyw -3 C:/Users/brown/.claude/scripts/foo-bar.py"
    )
    assert got == "foo-bar", got
    return "hook_name_from_command extracts the script basename minus .py"


def test_hook_name_from_command_trailing_whitespace() -> str:
    got = hook_liveness_check.hook_name_from_command("pyw -3 C:/x/y.py   ")
    assert got == "y", got
    return "hook_name_from_command tolerates trailing whitespace"


def test_hook_name_from_command_non_py_returns_none() -> str:
    assert hook_liveness_check.hook_name_from_command("echo hello") is None
    return "hook_name_from_command returns None for a non-.py command"


def test_hook_name_from_command_empty_or_none() -> str:
    assert hook_liveness_check.hook_name_from_command("") is None
    assert hook_liveness_check.hook_name_from_command(None) is None
    return "hook_name_from_command returns None for empty string / None"


# --- wired_hook_events -------------------------------------------------------

def test_wired_hook_events_basic() -> str:
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "pyw -3 C:/x/foo-hook.py", "timeout": 30},
                ]},
            ],
            "UserPromptSubmit": [
                {"hooks": [
                    {"type": "command", "command": "pyw -3 C:/x/foo-hook.py", "timeout": 10},
                    {"type": "command", "command": "pyw -3 C:/x/bar-hook.py", "timeout": 10},
                ]},
            ],
            "PostCompact": [
                {"hooks": [
                    {"type": "command", "command": "pyw -3 C:/x/baz-hook.py", "timeout": 30},
                ]},
            ],
        }
    }
    got = hook_liveness_check.wired_hook_events(settings)
    assert got == {
        "foo-hook": {"PreToolUse", "UserPromptSubmit"},
        "bar-hook": {"UserPromptSubmit"},
        "baz-hook": {"PostCompact"},
    }, got
    return "wired_hook_events maps each hook name to the union of events it is registered under"


def test_wired_hook_events_multiple_groups_same_event_dedupe_to_one() -> str:
    # journal-shard-write-advisory.py's real shape: PostToolUse under three
    # different matchers (Bash/Write/Edit) -- still just "wired to PostToolUse".
    settings = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [{"command": "pyw -3 C:/x/jswa.py"}]},
                {"matcher": "Write", "hooks": [{"command": "pyw -3 C:/x/jswa.py"}]},
                {"matcher": "Edit", "hooks": [{"command": "pyw -3 C:/x/jswa.py"}]},
            ],
        }
    }
    got = hook_liveness_check.wired_hook_events(settings)
    assert got == {"jswa": {"PostToolUse"}}, got
    return "multiple matcher groups under one event contribute a single event membership"


def test_wired_hook_events_empty_settings() -> str:
    assert hook_liveness_check.wired_hook_events({}) == {}
    return "wired_hook_events({}) -> {}"


def test_wired_hook_events_non_py_command_contributes_nothing() -> str:
    settings = {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": "echo hi"}]}]}}
    assert hook_liveness_check.wired_hook_events(settings) == {}
    return "a command that isn't a .py invocation contributes no hook"


def test_wired_hook_events_malformed_structure_degrades_gracefully() -> str:
    settings = {
        "hooks": {
            "PreToolUse": "not-a-list",  # skipped: groups must be a list
            "UserPromptSubmit": [
                "not-a-dict",             # skipped: group must be a dict
                {"hooks": "not-a-list"},  # iterated as chars, none are dicts -> skipped
                {"hooks": [123, None, "x"]},  # non-dict hook entries skipped
                {"hooks": [{"command": "pyw -3 C:/x/real-hook.py"}]},  # the only real one
            ],
        }
    }
    got = hook_liveness_check.wired_hook_events(settings)
    assert got == {"real-hook": {"UserPromptSubmit"}}, got
    return "malformed structure at any level is skipped, not raised"


def test_wired_hook_events_non_dict_hooks_key() -> str:
    assert hook_liveness_check.wired_hook_events({"hooks": "not-a-dict"}) == {}
    return "a non-dict top-level 'hooks' value yields {} rather than raising"


# --- exempt_hooks -------------------------------------------------------------

def test_exempt_hooks_postcompact_only() -> str:
    events = {"post-compact": {"PostCompact"}}
    assert hook_liveness_check.exempt_hooks(events) == {"post-compact"}
    return "a hook wired only to PostCompact is exempt"


def test_exempt_hooks_notification_only() -> str:
    events = {"notif-only-hook": {"Notification"}}
    assert hook_liveness_check.exempt_hooks(events) == {"notif-only-hook"}
    return "a hook wired only to Notification is exempt"


def test_exempt_hooks_both_rare_events() -> str:
    events = {"both-rare": {"PostCompact", "Notification"}}
    assert hook_liveness_check.exempt_hooks(events) == {"both-rare"}
    return "a hook wired only to PostCompact + Notification (both rare) is exempt"


def test_exempt_hooks_not_exempt_when_also_wired_elsewhere() -> str:
    # awake-blocker.py's real shape: Notification + UserPromptSubmit + Stop --
    # NOT exempt, since it's also wired to a normal-cadence event.
    events = {"awake-blocker": {"Notification", "UserPromptSubmit", "Stop"}}
    assert hook_liveness_check.exempt_hooks(events) == set()
    return "a hook also wired to a non-rare event is NOT exempt, even if Notification is among its events"


def test_exempt_hooks_empty_event_set_not_exempt() -> str:
    # Defensive: wired_hook_events() never actually produces an empty set (a
    # name only appears once an event added it), but exempt_hooks must not
    # vacuously treat a hand-built empty set as "all events are exempt events".
    assert hook_liveness_check.exempt_hooks({"weird": set()}) == set()
    return "a hook with an empty event set is not exempt (guards the vacuous-subset case)"


# --- stale_hooks ----------------------------------------------------------------

def test_stale_hooks_all_fresh_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb = Path(root)
        now = 1_000_000.0
        (hb / "foo.ts").write_text(str(now - 10), encoding="utf-8")
        got = hook_liveness_check.stale_hooks({"foo": {"UserPromptSubmit"}}, hb, now)
        assert got == [], got
    return "a fresh heartbeat -> not stale"


def test_stale_hooks_missing_file_is_stale_with_none() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb = Path(root)
        now = 1_000_000.0
        got = hook_liveness_check.stale_hooks({"foo": {"UserPromptSubmit"}}, hb, now)
        assert got == [{"hook": "foo", "last_seen": None}], got
    return "a missing heartbeat file -> stale with last_seen=None"


def test_stale_hooks_old_timestamp_is_stale() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb = Path(root)
        now = 1_000_000.0
        cadence = 7
        old = now - (cadence + 1) * 86400
        (hb / "foo.ts").write_text(str(old), encoding="utf-8")
        got = hook_liveness_check.stale_hooks({"foo": {"UserPromptSubmit"}}, hb, now, cadence_days=cadence)
        assert got == [{"hook": "foo", "last_seen": old}], got
    return "a heartbeat older than cadence_days -> stale with its last_seen value"


def test_stale_hooks_boundary_exactly_at_cutoff_not_stale() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb = Path(root)
        now = 1_000_000.0
        cadence = 7
        cutoff = now - cadence * 86400
        (hb / "foo.ts").write_text(str(cutoff), encoding="utf-8")
        got = hook_liveness_check.stale_hooks({"foo": {"UserPromptSubmit"}}, hb, now, cadence_days=cadence)
        assert got == [], got
    return "a heartbeat exactly cadence_days old is NOT stale (boundary is on the healthy side)"


def test_stale_hooks_boundary_just_past_cutoff_is_stale() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb = Path(root)
        now = 1_000_000.0
        cadence = 7
        cutoff = now - cadence * 86400
        just_past = cutoff - 1
        (hb / "foo.ts").write_text(str(just_past), encoding="utf-8")
        got = hook_liveness_check.stale_hooks({"foo": {"UserPromptSubmit"}}, hb, now, cadence_days=cadence)
        assert got == [{"hook": "foo", "last_seen": just_past}], got
    return "one second past the cutoff -> stale"


def test_stale_hooks_malformed_content_treated_as_never() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb = Path(root)
        now = 1_000_000.0
        (hb / "foo.ts").write_text("not-a-number", encoding="utf-8")
        got = hook_liveness_check.stale_hooks({"foo": {"UserPromptSubmit"}}, hb, now)
        assert got == [{"hook": "foo", "last_seen": None}], got
    return "unparseable heartbeat content is treated the same as a missing file (last_seen=None)"


def test_stale_hooks_exempt_hook_never_flagged() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb = Path(root)
        now = 1_000_000.0
        # No heartbeat file at all for post-compact -- would be "stale" if it
        # weren't exempt.
        got = hook_liveness_check.stale_hooks({"post-compact": {"PostCompact"}}, hb, now)
        assert got == [], got
    return "an exempt hook (PostCompact/Notification-only) is never flagged, even with no heartbeat at all"


def test_stale_hooks_sorted_by_name() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb = Path(root)
        now = 1_000_000.0
        events = {"zeta": {"UserPromptSubmit"}, "alpha": {"UserPromptSubmit"}}
        got = hook_liveness_check.stale_hooks(events, hb, now)
        names = [e["hook"] for e in got]
        assert names == ["alpha", "zeta"], names
    return "stale_hooks output is sorted by hook name for deterministic messages"


# --- _age_desc / format_warning --------------------------------------------------

def test_age_desc_none_is_never_recorded() -> str:
    assert hook_liveness_check._age_desc(None, 1_000_000.0) == "never recorded"
    return "_age_desc(None, ...) -> 'never recorded'"


def test_age_desc_formats_days() -> str:
    now = 1_000_000.0
    last_seen = now - 3.5 * 86400
    assert hook_liveness_check._age_desc(last_seen, now) == "last seen 3.5d ago"
    return "_age_desc formats elapsed days to one decimal place"


def test_format_warning_is_ascii() -> str:
    stale = [{"hook": "foo", "last_seen": None}, {"hook": "bar", "last_seen": 900000.0}]
    msg = hook_liveness_check.format_warning(stale, 1_000_000.0, 7)
    assert msg.isascii(), f"message must be pure ASCII: {msg!r}"
    return "format_warning's output is pure ASCII"


def test_format_warning_contains_hook_names_and_never() -> str:
    stale = [{"hook": "foo", "last_seen": None}]
    msg = hook_liveness_check.format_warning(stale, 1_000_000.0, 7)
    assert "foo" in msg, msg
    assert "never recorded" in msg, msg
    return "format_warning names the stale hook and shows 'never recorded' for last_seen=None"


def test_format_warning_contains_day_count_for_stale_timestamp() -> str:
    now = 1_000_000.0
    last_seen = now - 10 * 86400
    stale = [{"hook": "bar", "last_seen": last_seen}]
    msg = hook_liveness_check.format_warning(stale, now, 7)
    assert "bar" in msg, msg
    assert "10.0d ago" in msg, msg
    return "format_warning shows the elapsed-day count for a real stale timestamp"


def test_format_warning_cadence_whole_number_formatting() -> str:
    msg = hook_liveness_check.format_warning([], 1_000_000.0, 7)
    assert "7 days" in msg, msg
    assert "7.0 days" not in msg, msg
    return "cadence_days formats as '7 days', not '7.0 days', for a whole-number cadence"


# --- format_self_check_failure (review finding: silent self-check gap) ----------

def test_format_self_check_failure_is_ascii() -> str:
    msg = hook_liveness_check.format_self_check_failure("something broke", 3)
    assert msg.isascii(), f"message must be pure ASCII: {msg!r}"
    return "format_self_check_failure's output is pure ASCII"


def test_format_self_check_failure_names_reason_and_count() -> str:
    msg = hook_liveness_check.format_self_check_failure("could not read/parse settings.json (boom)", 0)
    assert "could not read/parse settings.json (boom)" in msg, msg
    assert "parsed 0 wired hook(s)" in msg, msg
    return "format_self_check_failure includes the specific reason and the parsed hook count"


def test_format_self_check_failure_distinct_from_format_warning() -> str:
    # The whole point of this separate message is that the two failure classes
    # ("some OTHER hook is stale" vs. "this check's own machinery may be broken")
    # are never conflated in the advisory Claude sees.
    self_check_msg = hook_liveness_check.format_self_check_failure("reason", 5)
    warning_msg = hook_liveness_check.format_warning([], 1_000_000.0, 7)
    assert "self-check failed" in self_check_msg
    assert "self-check failed" not in warning_msg
    return "format_self_check_failure's text is distinct from format_warning's"


# --- cross-check vs. tests/_hook_wiring.py (review finding: duplicate parsers) --

def test_wired_hook_events_agrees_with_hook_wiring_module() -> str:
    # Regression guard for the two near-duplicate settings.json parsers this repo
    # deliberately keeps separate until Phase E (ADR-106's Settings-parsing scope
    # decision) -- runs against the REAL claude/settings.shared.json so a future schema
    # change applied to one parser and not the other is caught here, not just
    # against a static fixture both were written against.
    hook_wiring = importlib.import_module("_hook_wiring")
    settings = hook_wiring.load_settings()
    ours = hook_liveness_check.wired_hook_events(settings)
    theirs = hook_wiring.wired_script_events(settings)
    theirs_normalized = {
        (name[:-3] if name.endswith(".py") else name): events for name, events in theirs.items()
    }
    assert ours == theirs_normalized, (
        f"wired_hook_events() and _hook_wiring.wired_script_events() disagree against the "
        f"real claude/settings.shared.json -- only in ours: {set(ours) - set(theirs_normalized)}; "
        f"only in theirs: {set(theirs_normalized) - set(ours)}; "
        f"differing events: {[(k, ours[k], theirs_normalized[k]) for k in ours if k in theirs_normalized and ours[k] != theirs_normalized[k]]}"
    )
    return "wired_hook_events() agrees with tests/_hook_wiring.wired_script_events() against the real claude/settings.shared.json"


# --- behavioral layer: real main() over stdin via subprocess --------------------
# HOME/USERPROFILE isolated to a temp dir (mirrors test_stop_journal_stub_checkpoint.py)
# so _hookutil.HEARTBEAT_DIR and the once-per-session sentinel never touch the real
# ~/.claude/scratch/. SETTINGS_PATH overridden via the HOOK_LIVENESS_SETTINGS_PATH
# test seam so a broken/malformed settings.json can be exercised safely.

SCRIPT = REPO_ROOT / "claude" / "scripts" / "hook-liveness-check.py"


def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


def _run_hook(settings_path, home, *, session_id="sess-test"):
    """Drive the real hook once. Returns (exit_code, stdout, stderr)."""
    payload = json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "cwd": str(home),
    })
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Path.home() honors USERPROFILE on Windows
    env["HOOK_LIVENESS_SETTINGS_PATH"] = str(settings_path)
    proc = subprocess.run(
        _py_cmd() + [str(SCRIPT)], input=payload,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _write_settings(path, wired_names):
    """A minimal settings.json wiring each name in *wired_names* under UserPromptSubmit."""
    hooks = [{"command": f"pyw -3 C:/x/{name}.py"} for name in wired_names]
    path.write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": hooks}]}}), encoding="utf-8"
    )


def test_e2e_healthy_session_no_stale_is_silent() -> str:
    with tempfile.TemporaryDirectory() as home:
        settings = Path(home) / "settings.json"
        _write_settings(settings, ["hook-liveness-check"])
        rc, out, err = _run_hook(settings, home)
        assert rc == 0, (rc, out, err)
        assert out.strip() == "", f"expected silent exit, got stdout: {out!r}"
        assert err.strip() == "", f"expected silent exit, got stderr: {err!r}"
    return "healthy session (own heartbeat just recorded, no other wired hooks) exits silently"


def test_e2e_stale_hook_emits_additionalcontext() -> str:
    with tempfile.TemporaryDirectory() as home:
        settings = Path(home) / "settings.json"
        _write_settings(settings, ["hook-liveness-check", "some-other-hook"])
        rc, out, err = _run_hook(settings, home)
        assert rc == 0, (rc, out, err)
        assert err.strip() == "", f"advisory must ride stdout, not stderr: {err!r}"
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "some-other-hook" in ctx, ctx
        assert "never recorded" in ctx, ctx
    return "a stale non-exempt hook (no heartbeat file at all) emits a model-visible additionalContext warning"


def test_e2e_unreadable_settings_emits_self_check_failure() -> str:
    with tempfile.TemporaryDirectory() as home:
        settings = Path(home) / "settings.json"
        settings.write_text("not valid json", encoding="utf-8")
        rc, out, err = _run_hook(settings, home)
        assert rc == 0, (rc, out, err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "self-check failed" in ctx, ctx
        assert "could not read/parse" in ctx, ctx
    return "an unparseable settings.json emits the self-check-failure advisory, not a silent exit"


def test_e2e_own_wiring_missing_emits_self_check_failure() -> str:
    with tempfile.TemporaryDirectory() as home:
        settings = Path(home) / "settings.json"
        _write_settings(settings, ["some-other-hook"])  # hook-liveness-check itself absent
        rc, out, err = _run_hook(settings, home)
        assert rc == 0, (rc, out, err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "self-check failed" in ctx, ctx
        assert "did not find this hook's own wiring" in ctx, ctx
    return "a settings.json missing this hook's own wiring emits the self-check-failure advisory (the review's core finding)"


def test_e2e_debounce_second_call_same_session_silent() -> str:
    with tempfile.TemporaryDirectory() as home:
        settings = Path(home) / "settings.json"
        _write_settings(settings, ["hook-liveness-check", "some-other-hook"])
        rc1, out1, _ = _run_hook(settings, home, session_id="sess-debounce")
        assert rc1 == 0 and "some-other-hook" in out1, (rc1, out1)
        rc2, out2, err2 = _run_hook(settings, home, session_id="sess-debounce")
        assert rc2 == 0, (rc2, out2, err2)
        assert out2.strip() == "", f"second call in the same session must be debounced silently, got: {out2!r}"
    return "a second call in the same session is silenced by the once-per-session debounce"


def test_e2e_different_session_not_debounced() -> str:
    with tempfile.TemporaryDirectory() as home:
        settings = Path(home) / "settings.json"
        _write_settings(settings, ["hook-liveness-check", "some-other-hook"])
        rc1, out1, _ = _run_hook(settings, home, session_id="sess-a")
        assert rc1 == 0 and "some-other-hook" in out1, (rc1, out1)
        rc2, out2, _ = _run_hook(settings, home, session_id="sess-b")
        assert rc2 == 0 and "some-other-hook" in out2, (rc2, out2)
    return "the once-per-session debounce is keyed by session_id -- a different session still fires"


def test_e2e_heartbeat_tmp_orphan_swept() -> str:
    # dev-env#802: record_heartbeat writes <hook>.ts.<pid>.tmp then os.replace()s it onto <hook>.ts;
    # a rare os.replace failure orphans the .tmp in HEARTBEAT_DIR. hook-liveness-check now reaps stale
    # ones. Confirm a >30-day-old orphan is swept while a fresh (in-flight) .tmp and the live .ts
    # ledger are spared. Mirrors test_dev_env_sync.py::test_tmp_orphan_swept_by_tmp_cleanup.
    with tempfile.TemporaryDirectory() as home:
        hb_dir = Path(home) / ".claude" / "scratch" / "hook-heartbeat"
        hb_dir.mkdir(parents=True)
        stale_tmp = hb_dir / "some-hook.ts.99999.tmp"
        fresh_tmp = hb_dir / "some-hook.ts.88888.tmp"
        ledger = hb_dir / "some-hook.ts"
        for p in (stale_tmp, fresh_tmp, ledger):
            p.write_text("x", encoding="utf-8")
        old = time.time() - 40 * 86400
        os.utime(stale_tmp, (old, old))

        settings = Path(home) / "settings.json"
        _write_settings(settings, ["hook-liveness-check"])  # only itself wired -> healthy/silent
        rc, out, err = _run_hook(settings, home)
        assert rc == 0, (rc, out, err)
        assert not stale_tmp.exists(), "a >30-day-old heartbeat .tmp orphan must be swept"
        assert fresh_tmp.exists(), "a fresh (in-flight) heartbeat .tmp must NOT be swept"
        assert ledger.exists(), "the live .ts ledger must never be swept by the *.tmp glob"
    return "hook-liveness-check reaps a stale heartbeat .tmp orphan, sparing the fresh .tmp and the .ts ledger"


def main() -> int:
    tests = [
        ("hook_name_from_command: matches .py script", test_hook_name_from_command_matches_py_script),
        ("hook_name_from_command: trailing whitespace", test_hook_name_from_command_trailing_whitespace),
        ("hook_name_from_command: non-.py -> None", test_hook_name_from_command_non_py_returns_none),
        ("hook_name_from_command: empty/None -> None", test_hook_name_from_command_empty_or_none),
        ("wired_hook_events: basic multi-event mapping", test_wired_hook_events_basic),
        ("wired_hook_events: multiple groups, same event dedupe", test_wired_hook_events_multiple_groups_same_event_dedupe_to_one),
        ("wired_hook_events: empty settings -> {}", test_wired_hook_events_empty_settings),
        ("wired_hook_events: non-.py command contributes nothing", test_wired_hook_events_non_py_command_contributes_nothing),
        ("wired_hook_events: malformed structure degrades gracefully", test_wired_hook_events_malformed_structure_degrades_gracefully),
        ("wired_hook_events: non-dict 'hooks' key -> {}", test_wired_hook_events_non_dict_hooks_key),
        ("exempt_hooks: PostCompact-only", test_exempt_hooks_postcompact_only),
        ("exempt_hooks: Notification-only", test_exempt_hooks_notification_only),
        ("exempt_hooks: both rare events", test_exempt_hooks_both_rare_events),
        ("exempt_hooks: not exempt when also wired elsewhere", test_exempt_hooks_not_exempt_when_also_wired_elsewhere),
        ("exempt_hooks: empty event set not exempt", test_exempt_hooks_empty_event_set_not_exempt),
        ("stale_hooks: all fresh -> []", test_stale_hooks_all_fresh_returns_empty),
        ("stale_hooks: missing file -> stale, last_seen=None", test_stale_hooks_missing_file_is_stale_with_none),
        ("stale_hooks: old timestamp -> stale", test_stale_hooks_old_timestamp_is_stale),
        ("stale_hooks: exactly at cutoff -> not stale", test_stale_hooks_boundary_exactly_at_cutoff_not_stale),
        ("stale_hooks: just past cutoff -> stale", test_stale_hooks_boundary_just_past_cutoff_is_stale),
        ("stale_hooks: malformed content -> None", test_stale_hooks_malformed_content_treated_as_never),
        ("stale_hooks: exempt hook never flagged", test_stale_hooks_exempt_hook_never_flagged),
        ("stale_hooks: sorted by name", test_stale_hooks_sorted_by_name),
        ("_age_desc: None -> 'never recorded'", test_age_desc_none_is_never_recorded),
        ("_age_desc: formats days", test_age_desc_formats_days),
        ("format_warning: pure ASCII", test_format_warning_is_ascii),
        ("format_warning: names hook + 'never recorded'", test_format_warning_contains_hook_names_and_never),
        ("format_warning: day count for stale timestamp", test_format_warning_contains_day_count_for_stale_timestamp),
        ("format_warning: whole-number cadence formatting", test_format_warning_cadence_whole_number_formatting),
        ("format_self_check_failure: pure ASCII", test_format_self_check_failure_is_ascii),
        ("format_self_check_failure: names reason + count", test_format_self_check_failure_names_reason_and_count),
        ("format_self_check_failure: distinct from format_warning", test_format_self_check_failure_distinct_from_format_warning),
        ("wired_hook_events: agrees with _hook_wiring against real settings.json", test_wired_hook_events_agrees_with_hook_wiring_module),
        ("e2e: healthy session, no stale -> silent", test_e2e_healthy_session_no_stale_is_silent),
        ("e2e: stale hook -> additionalContext warning", test_e2e_stale_hook_emits_additionalcontext),
        ("e2e: unreadable settings.json -> self-check failure", test_e2e_unreadable_settings_emits_self_check_failure),
        ("e2e: own wiring missing -> self-check failure", test_e2e_own_wiring_missing_emits_self_check_failure),
        ("e2e: debounce silences second call, same session", test_e2e_debounce_second_call_same_session_silent),
        ("e2e: different session not debounced", test_e2e_different_session_not_debounced),
        ("e2e: heartbeat .tmp orphan swept, ledger spared", test_e2e_heartbeat_tmp_orphan_swept),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
