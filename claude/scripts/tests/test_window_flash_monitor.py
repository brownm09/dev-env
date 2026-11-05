#!/usr/bin/env python3
"""Tests for window-flash-monitor.py (dev-env#1225, ADR-155).

The default run is pure: event logs and process snapshots are fixtures, modelled on what the
real monitor recorded on 2026-11-05. It opens no windows, so CI runs it.

``--live`` runs the real monitor and opens real windows, so it is local only, the same as
test_pyw_stdio.py. A flash takes focus for about a second while it runs.

Usage:
    py -3 claude/scripts/tests/test_window_flash_monitor.py
    py -3 claude/scripts/tests/test_window_flash_monitor.py --live

Exit 0 = all pass.
"""

import contextlib
import ctypes
import datetime as dt
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "window-flash-monitor.py"

_spec = importlib.util.spec_from_file_location("window_flash_monitor", SCRIPT)
assert _spec and _spec.loader
wfm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wfm)  # safe: main() is guarded by __main__

SENTINEL = "SENTINEL-PRIVATE-TITLE-41c7"
TERMINAL = "CASCADIA_HOSTING_WINDOW_CLASS"
HOST_CHAIN = ["WindowsTerminal.exe", "svchost.exe", "services.exe"]
CMD_CHAIN = ["cmd.exe", "python.exe", "bash.exe", "claude.exe", "sihost.exe"]


def _history(snapshot=None):
    history = wfm.ProcessHistory()
    history.update(snapshot or {10: ("claude.exe", 1), 20: ("cmd.exe", 10), 30: ("chrome.exe", 1)}, 100.0)
    return history


def _terminal_flash(start=1000.0, hwnd=7, program="cmd.exe", pseudo_chain=None):
    """The order the real monitor logged a Windows Terminal flash in."""
    records = [
        {"t": start, "e": "foreground", "hwnd": hwnd},
        {"t": start + 0.03, "e": "show", "hwnd": hwnd, "cls": TERMINAL, "chain": HOST_CHAIN,
         "recent": [{"exe": "git.exe", "chain": ["git.exe", "claude.exe"]},
                    {"exe": "cmd.exe", "chain": CMD_CHAIN}]},
    ]
    if pseudo_chain:
        records.append({"t": start + 0.1, "e": "show", "hwnd": hwnd + 1000, "cls": "PseudoConsoleWindow",
                        "chain": pseudo_chain})
    if program:
        records.append({"t": start + 0.15, "e": "name", "hwnd": hwnd, "program": program})
    records += [{"t": start + 1.2, "e": "hide", "hwnd": hwnd},
                {"t": start + 1.21, "e": "foreground", "hwnd": 99}]
    return records


def test_program_from_title_keeps_only_a_program():
    assert wfm.program_from_title(r"C:\Windows\System32\cmd.exe") == "cmd.exe"
    assert wfm.program_from_title(r"Administrator: C:\a b\Tool-2.EXE") == "Tool-2.EXE"
    assert wfm.program_from_title(r"C:\Program Files\My App.exe") == "My App.exe"
    assert wfm.program_from_title("cmd.exe") == "cmd.exe"
    for private in ("Windows Terminal", "", r"vim C:\Users\me\acme-merger.exe.txt", "budget-q3.exe-notes - Notepad",
                    r"vim C:\Users\me\secret.exe", "open quarterly plan.exe"):
        assert wfm.program_from_title(private) is None, private
    return "a path or bare name counts, and text that merely contains .exe doesn't"


def test_history_names_a_process_after_it_exits():
    history = _history()
    history.update({10: ("claude.exe", 1)}, 130.0)  # cmd.exe is gone from the snapshot
    assert history.chain(20) == ["cmd.exe", "claude.exe"]
    history.update({10: ("claude.exe", 1)}, 100.0 + wfm.HISTORY_SECONDS + 1)
    assert history.chain(20) == [], "kept past the history window"
    return "a process stays nameable for the history window, then drops out"


def test_history_stops_at_a_reused_parent_pid():
    history = wfm.ProcessHistory()
    history.update({20: ("cmd.exe", 10)}, 100.0)
    history.update({20: ("cmd.exe", 10), 10: ("notepad.exe", 1)}, 105.0)  # pid 10 born after its "child"
    assert history.chain(20) == ["cmd.exe"]
    return "a parent seen after its child is not treated as the parent"


def test_startup_times_catch_a_reused_parent_pid():
    snapshot = {30: ("chrome.exe", 40), 40: ("bash.exe", 10), 10: ("claude.exe", 1), 50: ("tool.exe", 60),
                60: ("svchost.exe", 70), 70: ("services.exe", 70)}
    history = wfm.ProcessHistory()
    history.update(snapshot, 1000.0, {30: 100.0, 40: 500.0, 10: 400.0, 50: 200.0})
    assert history.chain(30) == ["chrome.exe"], "walked into Claude's tree through a reused pid"
    assert history.chain(40) == ["bash.exe", "claude.exe"], "a real parent was dropped"
    assert history.chain(50) == ["tool.exe", "svchost.exe", "services.exe"], "stopped at an unreadable parent"
    record = wfm.show_record(1000.0, 5, 30, "Chrome_WidgetWin_1", SENTINEL, history)
    assert record is None, "a title reached the log through a reused pid"
    return "a parent started after its child is cut, and unreadable system parents are walked"


def test_chain_stops_at_a_self_parented_process():
    history = wfm.ProcessHistory()
    history.update({4: ("System", 0), 0: ("[System Process]", 0)}, 100.0)
    assert history.chain(4) == ["System", "[System Process]"]
    return "no repeated entries"


def test_recent_lists_new_processes_newest_first():
    history = _history()
    history.update({10: ("claude.exe", 1), 40: ("git.exe", 10), 41: ("conhost.exe", 40)}, 110.0)
    history.update({10: ("claude.exe", 1), 40: ("git.exe", 10), 50: ("gh.exe", 10)}, 111.0)
    assert [r["exe"] for r in history.recent(111.0)] == ["gh.exe", "git.exe"]
    return "only processes first seen in the window, newest first, without console hosts"


def test_show_record_title_rule():
    history = _history()
    assert wfm.show_record(1.0, 5, 30, "Chrome_WidgetWin_1", SENTINEL, history) is None
    console = wfm.show_record(1.0, 5, 30, TERMINAL, f"{SENTINEL} tool.exe", history)
    assert SENTINEL not in json.dumps(console), "a console window outside Claude kept its title"
    assert "program" not in console and "recent" in console
    mine = wfm.show_record(1.0, 5, 10, "Chrome_WidgetWin_1", SENTINEL, history)
    assert mine["titled"] is True and SENTINEL not in json.dumps(mine), "a window under Claude kept its title"
    helper = wfm.show_record(1.0, 5, 10, "Chrome_WidgetWin_1", "", history)
    assert "titled" not in helper
    return "other windows unlogged, consoles keep only a program name, and no title is kept anywhere"


def test_terminal_flash_times_focus_and_names_the_source():
    flashes = wfm.find_flashes(_terminal_flash())
    assert len(flashes) == 1
    flash = flashes[0]
    assert flash["seconds"] == 1.17 and flash["focus_lost"] == 1.21 and flash["focus_returned"] is True
    assert flash["program"] == "cmd.exe" and flash["source"] == CMD_CHAIN and flash["origin"] == "claude"
    return f"seconds={flash['seconds']} focus_lost={flash['focus_lost']}"


def test_pseudo_console_needs_a_matching_name_and_is_used_once():
    other = ["cmd.exe", "svchost.exe", "services.exe", "wininit.exe"]
    flash = wfm.find_flashes(_terminal_flash(pseudo_chain=other))[0]
    assert flash["source"] == other and flash["origin"] == "elsewhere"
    mismatch = wfm.find_flashes(_terminal_flash(pseudo_chain=["other.exe", "svchost.exe"]))[0]
    assert mismatch["source"] == CMD_CHAIN, "used a pseudo console belonging to another program"
    nameless = wfm.find_flashes(_terminal_flash(program=None, pseudo_chain=["node.exe", "claude.exe"]))[0]
    assert nameless["source"] is None and nameless["origin"] == "unknown", "guessed from overlap alone"
    two = _terminal_flash(pseudo_chain=other) + _terminal_flash(start=1000.02, hwnd=8)
    sources = [f["source"] for f in wfm.find_flashes(two)]
    assert sources == [other, CMD_CHAIN], f"one pseudo console served two flashes: {sources}"
    return "owner used only on a name match, and only once"


def test_classic_console_reports_its_owner():
    records = [{"t": 1.0, "e": "show", "hwnd": 3, "cls": "ConsoleWindowClass", "chain": ["cmd.exe", "svchost.exe"]},
               {"t": 1.5, "e": "hide", "hwnd": 3}]
    flash = wfm.find_flashes(records)[0]
    assert flash["source"] == ["cmd.exe", "svchost.exe"] and flash["focus_lost"] == 0.0
    return "owner chain used, and no foreground event means no focus lost"


def test_origin_needs_evidence():
    assert wfm.origin(["cmd.exe", "bash.exe", "claude.exe"]) == "claude"
    assert wfm.origin(["cmd.exe", "svchost.exe", "services.exe", "wininit.exe"]) == "elsewhere"
    assert wfm.origin(["cmd.exe", "Explorer.EXE"]) == "elsewhere"
    assert wfm.origin(["cmd.exe", "daemon.exe"]) == "unknown", "a cut-off chain counted as not Claude"
    assert wfm.origin(None) == "unknown"
    return "not Claude is said only when the chain reaches a known root"


def test_unattributed_flash_is_said_to_be_unattributed():
    flash = wfm.find_flashes(_terminal_flash(program=None))[0]
    assert flash["source"] is None and flash["origin"] == "unknown"
    report = wfm.render_report("2026-11-05", _terminal_flash(program=None))
    assert "| unattributed |" in report and "1 of unknown origin" in report and "| unknown |" in report
    return "no source is reported as unattributed, not as not-Claude"


def test_only_short_lived_console_windows_are_flashes():
    long_lived = _terminal_flash()
    long_lived[-2]["t"] = long_lived[1]["t"] + wfm.FLASH_MAX_SECONDS + 0.1
    still_open = _terminal_flash()[:-2]
    other = [{"t": 1.0, "e": "show", "hwnd": 3, "cls": "Chrome_WidgetWin_1", "chain": ["claude.exe"]},
             {"t": 1.1, "e": "hide", "hwnd": 3}]
    pseudo_only = [{"t": 1.0, "e": "show", "hwnd": 3, "cls": "PseudoConsoleWindow", "chain": ["cmd.exe"]},
                   {"t": 1.1, "e": "hide", "hwnd": 3}]
    for name, records in (("long", long_lived), ("open", still_open), ("other", other), ("pseudo", pseudo_only)):
        assert wfm.find_flashes(records) == [], f"{name} counted as a flash"
    return "long-lived, still-open, non-console and pseudo-only windows are not flashes"


def test_one_hide_ends_one_flash():
    show = {"e": "show", "hwnd": 3, "cls": "ConsoleWindowClass", "chain": ["cmd.exe"]}
    records = [dict(show, t=1.0), dict(show, t=2.0), {"t": 2.5, "e": "hide", "hwnd": 3}]
    flashes = wfm.find_flashes(records)
    assert [f["seconds"] for f in flashes] == [0.5], flashes
    return "a later show of the same window stops the search"


def test_focus_lost_is_bounded():
    records = _terminal_flash()
    records[-1]["t"] = 1000.0 + 1.2 + wfm.FOCUS_RETURN_SECONDS + 3600  # the user's click, an hour later
    flash = wfm.find_flashes(records)[0]
    assert flash["focus_lost"] == 1.2 and flash["focus_returned"] is False
    assert "| 1.2+ |" in wfm.render_report("2026-11-05", records)
    return "a much later foreground change isn't focus returning, and the time is marked as a minimum"


def test_finding_flashes_is_not_quadratic():
    records = []
    for n in range(4000):
        records += _terminal_flash(start=1000.0 + n * 10, hwnd=n + 1)
    started = time.perf_counter()
    assert len(wfm.find_flashes(records)) == 4000
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"{elapsed:.2f}s for 4000 flashes"
    return f"4000 flashes in {elapsed:.2f}s (the quadratic version took about 8s for 4000)"


def test_report_counts_numbers_rows_and_states_gaps():
    records = _terminal_flash() + _terminal_flash(start=2000.0, hwnd=8, program=None)
    records.append({"t": 3000.0, "e": "show", "hwnd": 9, "cls": "Notepad", "pid": 1,
                    "chain": ["note|pad.exe", "claude.exe"], "titled": True})
    report = wfm.render_report("2026-11-05", records)
    assert "Flashes: 2 (1 from Claude, 0 from elsewhere, 1 of unknown origin)" in report
    rows = [line for line in report.splitlines() if line.startswith("| ") and "---" not in line]
    assert len(rows) == 5, f"expected 2 headers and 3 rows, got {rows}"
    assert rows[1].startswith("| 1 |") and rows[2].startswith("| 2 |")
    assert "Other windows opened under Claude: 1." in report and "note/pad.exe < claude.exe" in report
    assert "Nothing was seen in these gaps" in report, "a gap longer than two beats went unstated"
    assert wfm.recording_gaps([1.0, 1.0 + 2 * wfm.BEAT_SECONDS]) == [], "two beats apart is not a gap"
    assert "gaps" not in wfm.render_report("2026-11-05", _terminal_flash())
    return "counts, numbered rows, intact cells, and gaps stated"


def test_empty_and_damaged_logs():
    assert "nothing can be said" in wfm.render_report("2026-11-05", [])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "2026-11-05.jsonl"
        cut_character = "é".encode("utf-8")[:1]
        path.write_bytes(b'{"t": 1.0, "e": "beat"}\n{"t": 2.0, "e":\n{"t": 3.0, "title": "' + cut_character + b"\n")
        records = wfm.load_records(path)
        assert records == [{"t": 1.0, "e": "beat"}]
        report = wfm.render_report("2026-11-05", records)
        assert "Flashes: 0" in report and "Monitor recorded from" in report
    return "an empty log claims nothing, and a line cut short or mid-character is skipped"


def test_report_for_a_missing_log_says_nothing_was_seen():
    with tempfile.TemporaryDirectory() as tmp:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wfm.report("2026-11-05", Path(tmp))
    assert code == 1 and "says nothing about flashes" in out.getvalue()
    return "exit 1, rather than reading as zero flashes"


def test_report_shows_that_days_errors():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        (log_dir / "2026-11-05.jsonl").write_text('{"t": 1.0, "e": "beat"}\n', encoding="utf-8")
        (log_dir / wfm.ERROR_LOG).write_text("2026-11-04T01:00:00 OldError: no\n2026-11-05T01:00:00 BadError: yes\n",
                                             encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert wfm.report("2026-11-05", log_dir) == 0
    assert "BadError" in out.getvalue() and "OldError" not in out.getvalue()
    return "a monitor failing on every event doesn't read as zero flashes"


def test_expired_logs_only_touches_old_daily_logs():
    today = dt.date(2026, 11, 20)
    names = ["2026-11-06.jsonl", "2026-11-05.jsonl", "monitor-errors.log", "2026-01-01.txt", "notes.jsonl",
             "2026-13-45.jsonl"]
    assert wfm.expired_logs(dict.fromkeys(names, 1), today) == ["2026-11-05.jsonl"]
    return "day 14 kept, day 15 removed, other names and invalid dates untouched"


def test_size_cap_removes_oldest_but_never_today():
    today = dt.date(2026, 11, 20)
    big = wfm.LOG_CAP_BYTES // 2 + 1
    sizes = {"2026-11-20.jsonl": big, "2026-11-19.jsonl": big, "2026-11-18.jsonl": big, "2026-11-17.jsonl": 1}
    assert sorted(wfm.expired_logs(sizes, today)) == ["2026-11-17.jsonl", "2026-11-18.jsonl", "2026-11-19.jsonl"]
    assert wfm.expired_logs({"2026-11-20.jsonl": wfm.LOG_CAP_BYTES * 2}, today) == []
    return "newest logs fit under the cap, and today's stays even when it alone is over"


def test_monitor_logs_foreground_only_around_a_flash():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        (log_dir / "2020-01-01.jsonl").write_text("old\n", encoding="utf-8")
        monitor = wfm.Monitor(log_dir)
        monitor.history = _history()
        monitor.on_foreground(1.0, 5, "Chrome_WidgetWin_1")  # before any console
        monitor.on_foreground(1.1, 4, TERMINAL)  # an ordinary terminal: pending, never shown
        monitor.on_foreground(1.2, 5, "Chrome_WidgetWin_1")  # leaving it isn't logged either
        monitor.on_foreground(1.9, 6, TERMINAL)  # the flash takes focus just before it shows
        monitor.on_show(2.0, 6, 30, TERMINAL, "Windows Terminal")
        monitor.check_watched(2.05, 6, True, "Windows Terminal")  # no program yet
        monitor.check_watched(2.1, 6, True, r"C:\x\cmd.exe")
        monitor.check_watched(2.15, 6, True, r"C:\x\cmd.exe")  # unchanged, so not logged again
        monitor.check_watched(2.2, 6, False, "")
        monitor.on_foreground(2.3, 5, "Chrome_WidgetWin_1")  # ends the flash's time in front
        monitor.on_foreground(2.4, 8, "Chrome_WidgetWin_1")  # ordinary use
        assert not monitor.watched, "a window that went away is still watched"
        monitor.on_show(3.0, 9, 30, TERMINAL, "Windows Terminal")
        monitor.check_watched(3.0 + wfm.FLASH_MAX_SECONDS + 0.1, 9, True, r"C:\x\late.exe")
        assert not monitor.watched, "a window past the flash limit is still watched"
        log = next(p for p in log_dir.iterdir() if p.name != "2020-01-01.jsonl")
        text = log.read_text(encoding="utf-8")
        events = [(r["t"], r["e"]) for r in map(json.loads, text.splitlines())]
        assert not (log_dir / "2020-01-01.jsonl").exists(), "expired log kept"
    assert SENTINEL not in text, "a non-Claude title reached the log"
    assert events == [(1.9, "foreground"), (2.0, "show"), (2.1, "name"), (2.2, "hide"), (2.3, "foreground"),
                      (3.0, "show")], events
    return "an ordinary terminal leaves no record, and a flash keeps its two foreground changes"


def test_write_failures_do_not_lose_or_stop_anything():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp) / "logs"
        monitor = wfm.Monitor(log_dir)
        monitor.write({"t": 1.0, "e": "beat"})
        assert (log_dir / f"{dt.date.today().isoformat()}.jsonl").exists(), "directory not created on write"
        shutil.rmtree(log_dir)
        monitor.write({"t": 2.0, "e": "beat"})
        assert log_dir.exists(), "a deleted log directory was not recreated"

        monitor.history = _history()
        title_with_half_a_pair = "C:\\x\\tool\ud83d.exe"
        monitor.on_show(3.0, 5, 10, "ConsoleWindowClass", title_with_half_a_pair)
        assert 5 in monitor.watched and monitor.watched[5]["program"] == "tool\ud83d.exe"

        blocked = Path(tmp) / "file-not-directory"
        blocked.write_text("x", encoding="utf-8")
        wfm.append_error(blocked, "must not raise")  # mkdir fails: nothing left to report to
        stuck = wfm.Monitor(blocked)
        stuck.log_error(OSError("disk full"))
        stuck.log_error(OSError("disk full"))
        assert len(stuck.errors) == 1

        monitor.log_error(ValueError("line one\nline two"))
        text = (log_dir / wfm.ERROR_LOG).read_text(encoding="utf-8")
        assert "ValueError: line one | line two" in text
    return "directory recreated, half a surrogate pair written, and a failed error write doesn't raise"


def test_a_watched_window_keeps_writing_to_the_day_it_showed():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        monitor = wfm.Monitor(log_dir)
        monitor.history = _history()
        monitor.on_show(1.0, 6, 30, TERMINAL, "Windows Terminal")
        monitor.watched[6]["date"] = "2020-01-02"  # as if it was shown before midnight
        monitor.check_watched(1.5, 6, False, "")
        hide = json.loads((log_dir / "2020-01-02.jsonl").read_text(encoding="utf-8"))
    assert hide["e"] == "hide"
    return "a flash across midnight stays in one file"


def live() -> int:
    """Run the real monitor against real windows. See the module docstring."""
    parent = Path(sys.executable).name.lower()
    failed = 0

    def check(ok: bool, name: str, detail: str = "") -> None:
        nonlocal failed
        failed += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}: {name}{': ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp) / "logs"
        # Uniquely named copies, so another session's flash can't be mistaken for a probe's.
        ping = Path(os.environ["SystemRoot"]) / "System32" / "PING.EXE"
        bad_name, good_name = "flashprobe-bad.exe", "flashprobe-good.exe"
        for name in (bad_name, good_name):
            shutil.copy2(ping, Path(tmp) / name)

        monitor = subprocess.Popen([sys.executable, str(SCRIPT), "--log-dir", str(log_dir), "--seconds", "25"],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.time() + 15
        while time.time() < deadline and not list(log_dir.glob("*.jsonl")):  # the first beat follows the hooks
            time.sleep(0.1)
        check(bool(list(log_dir.glob("*.jsonl"))), "the monitor started and wrote its first beat")

        def probe(name: str, flag: int) -> None:
            subprocess.Popen([str(Path(tmp) / name), "-n", "2", "127.0.0.1"], creationflags=flag,
                             stdout=None if flag == subprocess.CREATE_NEW_CONSOLE else subprocess.DEVNULL).wait()

        probe(bad_name, subprocess.CREATE_NEW_CONSOLE)
        time.sleep(1)
        good_at = time.time()
        probe(good_name, subprocess.CREATE_NO_WINDOW)
        time.sleep(1)
        good_end = time.time()

        # Started through WMI so its parent is not this test, and so not under claude.exe.
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        window = (f"import ctypes,time;ctypes.windll.user32.CreateWindowExW(0x08000000,'STATIC','{SENTINEL}',"
                  "0x10000000,0,0,300,80,0,0,0,0);time.sleep(3)")
        env = dict(os.environ, FLASH_PROBE_COMMAND=f'"{pythonw}" -c "{window}"')
        created = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
             "-Arguments @{CommandLine=$env:FLASH_PROBE_COMMAND}).ReturnValue"],
            env=env, capture_output=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        check(created.stdout.strip() == "0", "privacy window started", created.stdout.strip() or created.stderr)
        time.sleep(1.5)
        check(bool(ctypes.windll.user32.FindWindowW(None, SENTINEL)), "privacy window is on screen")
        check(monitor.poll() is None, "the monitor was still running while it was on screen")
        check(monitor.wait() == 0, "the monitor exited cleanly")

        text = "".join(p.read_text(encoding="utf-8") for p in log_dir.glob("*.jsonl"))
        records = [json.loads(line) for line in text.splitlines()]
        flashes = wfm.find_flashes(records)
        bad = [f for f in flashes if f["program"] == bad_name]
        good = [f for f in flashes if f["program"] == good_name or (f["source"] and f["source"][0] == good_name)]
        check(len(bad) == 1 and bool(bad[0]["source"]) and bad[0]["source"][0] == bad_name
              and parent in [e.lower() for e in bad[0]["source"]],
              "known-bad probe is reported with this test in its chain",
              " < ".join(bad[0]["source"][:3]) if bad and bad[0]["source"] else f"{len(bad)} flash(es)")
        check(not good, "known-good probe adds no flash", f"{len(good)} flash(es)")
        others = [f for f in flashes if not f["source"] and good_at <= f["t"] < good_end]
        if others:
            print(f"NOTE: {len(others)} unattributed flash(es) during the known-good probe, probably another session's")
        check(SENTINEL not in text, "a titled window outside Claude's tree stays out of the log")
        errors = log_dir / wfm.ERROR_LOG
        check(not errors.exists(), "the monitor logged no errors", errors.read_text() if errors.exists() else "")
    print(f"\nLive checks: {failed} failed")
    return 1 if failed else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "--live" in sys.argv[1:]:
        return live()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    assert tests, "no tests collected"
    failed = 0
    for fn in tests:
        try:
            detail = fn()
            print(f"PASS: {fn.__name__}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {fn.__name__}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
