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
        {"t": start, "e": "foreground", "hwnd": hwnd, "cls": TERMINAL, "chain": HOST_CHAIN},
        {"t": start + 0.03, "e": "show", "hwnd": hwnd, "cls": TERMINAL, "chain": HOST_CHAIN,
         "recent": [{"exe": "git.exe", "chain": ["git.exe", "claude.exe"]},
                    {"exe": "cmd.exe", "chain": CMD_CHAIN}]},
    ]
    if pseudo_chain:
        records.append({"t": start + 0.1, "e": "show", "hwnd": hwnd + 1, "cls": "PseudoConsoleWindow",
                        "chain": pseudo_chain})
    if program:
        records.append({"t": start + 0.15, "e": "name", "hwnd": hwnd, "program": program})
    records += [{"t": start + 1.2, "e": "hide", "hwnd": hwnd},
                {"t": start + 1.21, "e": "foreground", "hwnd": 99}]
    return records


def test_program_from_title_keeps_only_the_program():
    assert wfm.program_from_title(r"C:\Windows\System32\cmd.exe") == "cmd.exe"
    assert wfm.program_from_title(r"Administrator: C:\a b\Tool-2.EXE") == "Tool-2.EXE"
    assert wfm.program_from_title("Windows Terminal") is None
    assert wfm.program_from_title("") is None
    return "names the program, and nothing without one"


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


def test_recent_lists_new_processes_newest_first():
    history = _history()
    history.update({10: ("claude.exe", 1), 40: ("git.exe", 10)}, 110.0)
    history.update({10: ("claude.exe", 1), 40: ("git.exe", 10), 50: ("gh.exe", 10)}, 111.0)
    assert [r["exe"] for r in history.recent(111.0, seconds=3)] == ["gh.exe", "git.exe"]
    return "only processes first seen in the window, newest first"


def test_window_record_title_rule():
    history = _history()
    assert wfm.window_record("show", 1.0, 5, 30, "Chrome_WidgetWin_1", SENTINEL, history) is None
    assert wfm.window_record("foreground", 1.0, 5, 30, "Chrome_WidgetWin_1", SENTINEL, history) is None
    console = wfm.window_record("show", 1.0, 5, 30, TERMINAL, f"{SENTINEL} tool.exe", history)
    assert SENTINEL not in json.dumps(console), "a console window outside Claude kept its title"
    assert console["program"] == "tool.exe" and "recent" in console
    mine = wfm.window_record("show", 1.0, 5, 10, "Chrome_WidgetWin_1", SENTINEL, history)
    assert mine["title"] == SENTINEL
    return "other windows unlogged, consoles keep only a program name, Claude's windows keep titles"


def test_terminal_flash_times_focus_and_names_the_source():
    flashes = wfm.find_flashes(_terminal_flash())
    assert len(flashes) == 1
    flash = flashes[0]
    assert flash["seconds"] == 1.17 and flash["focus_lost"] == 1.21
    assert flash["program"] == "cmd.exe" and flash["source"] == CMD_CHAIN and flash["claude"] is True
    return f"seconds={flash['seconds']} focus_lost={flash['focus_lost']}"


def test_pseudo_console_owner_is_preferred_over_a_name_match():
    other = ["cmd.exe", "svchost.exe"]
    flash = wfm.find_flashes(_terminal_flash(pseudo_chain=other))[0]
    assert flash["source"] == other and flash["claude"] is False
    mismatch = wfm.find_flashes(_terminal_flash(pseudo_chain=["other.exe", "svchost.exe"]))[0]
    assert mismatch["source"] == CMD_CHAIN, "used a pseudo console belonging to another program"
    return "the pseudo console's owner wins, unless it names a different program"


def test_classic_console_reports_its_owner():
    records = [{"t": 1.0, "e": "show", "hwnd": 3, "cls": "ConsoleWindowClass", "chain": ["cmd.exe", "svchost.exe"]},
               {"t": 1.5, "e": "hide", "hwnd": 3}]
    flash = wfm.find_flashes(records)[0]
    assert flash["source"] == ["cmd.exe", "svchost.exe"] and flash["focus_lost"] == 0.0
    return "owner chain used, and no foreground event means no focus lost"


def test_unattributed_flash_is_said_to_be_unattributed():
    flash = wfm.find_flashes(_terminal_flash(program=None))[0]
    assert flash["source"] is None and flash["claude"] is False
    report = wfm.render_report("2026-11-05", _terminal_flash(program=None))
    assert "| unattributed |" in report and "1 unattributed" in report
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


def test_report_counts_and_numbers_rows():
    records = _terminal_flash() + _terminal_flash(start=2000.0, hwnd=8, program=None)
    records.append({"t": 3000.0, "e": "show", "hwnd": 9, "cls": "Notepad", "pid": 1,
                    "chain": ["notepad.exe", "claude.exe"], "title": "a|b.txt"})
    report = wfm.render_report("2026-11-05", records)
    assert "Flashes: 2 (1 from Claude, 0 from elsewhere, 1 unattributed)" in report
    rows = [line for line in report.splitlines() if line.startswith("| ") and "---" not in line]
    assert len(rows) == 5, f"expected 2 headers and 3 rows, got {rows}"
    assert rows[1].startswith("| 1 |") and rows[2].startswith("| 2 |")
    assert "Other windows opened under Claude: 1." in report and "a/b.txt" in report
    return "counts, numbered rows, and table cells kept intact"


def test_empty_and_damaged_logs():
    assert "nothing can be said" in wfm.render_report("2026-11-05", [])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "2026-11-05.jsonl"
        path.write_text('{"t": 1.0, "e": "beat"}\n{"t": 2.0, "e":\n', encoding="utf-8")
        records = wfm.load_records(path)
        assert records == [{"t": 1.0, "e": "beat"}]
        report = wfm.render_report("2026-11-05", records)
        assert "Flashes: 0" in report and "Monitor recorded from" in report
    return "an empty log claims nothing, and a cut-off line is skipped"


def test_report_for_a_missing_log_says_nothing_was_seen():
    with tempfile.TemporaryDirectory() as tmp:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wfm.report("2026-11-05", Path(tmp))
    assert code == 1 and "says nothing about flashes" in out.getvalue()
    return "exit 1, rather than reading as zero flashes"


def test_expired_logs_only_touches_old_daily_logs():
    today = dt.date(2026, 11, 20)
    names = ["2026-11-06.jsonl", "2026-11-05.jsonl", "monitor-errors.log", "2026-01-01.txt", "notes.jsonl"]
    assert wfm.expired_logs(names, today) == ["2026-11-05.jsonl"]
    return "day 14 kept, day 15 removed, other names untouched"


def test_monitor_logs_only_the_foreground_change_that_ends_a_flash():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        (log_dir / "2020-01-01.jsonl").write_text("old\n", encoding="utf-8")
        monitor = wfm.Monitor(log_dir)
        monitor.history = _history()
        monitor.on_foreground(1.0, 5, 30, "Chrome_WidgetWin_1", SENTINEL)  # before any console
        monitor.on_show(2.0, 6, 30, TERMINAL, "Windows Terminal")
        monitor.check_watched(2.05, 6, True, "Windows Terminal")  # no program yet
        monitor.check_watched(2.1, 6, True, r"C:\x\cmd.exe")
        monitor.check_watched(2.15, 6, True, r"C:\x\cmd.exe")  # unchanged, so not logged again
        monitor.on_foreground(2.2, 6, 30, TERMINAL, "Windows Terminal")
        monitor.on_foreground(2.3, 5, 30, "Chrome_WidgetWin_1", SENTINEL)  # ends the flash's time in front
        monitor.on_foreground(2.4, 8, 30, "Chrome_WidgetWin_1", SENTINEL)  # ordinary use
        monitor.check_watched(2.5, 6, False, "")
        assert not monitor.watched, "a window that went away is still watched"
        monitor.on_show(3.0, 9, 30, TERMINAL, "Windows Terminal")
        monitor.check_watched(3.0 + wfm.FLASH_MAX_SECONDS + 0.1, 9, True, r"C:\x\late.exe")
        assert not monitor.watched, "a window past the flash limit is still watched"
        log = next(p for p in log_dir.iterdir() if p.name != "2020-01-01.jsonl")
        text = log.read_text(encoding="utf-8")
        events = [(r["t"], r["e"]) for r in map(json.loads, text.splitlines())]
        assert not (log_dir / "2020-01-01.jsonl").exists(), "expired log kept"
    assert SENTINEL not in text, "a non-Claude title reached the log"
    assert events == [(2.0, "show"), (2.1, "name"), (2.2, "foreground"), (2.3, "foreground"), (2.5, "hide"),
                      (3.0, "show")], events
    return "one foreground record ends the flash, later ones aren't logged, and watching stops"


def live() -> int:
    """Run the real monitor against real windows. See the module docstring."""
    parent = Path(sys.executable).name.lower()
    probe = ["cmd.exe", "/c", "ping -n 2 127.0.0.1 >nul"]
    failed = 0

    def check(ok: bool, name: str, detail: str = "") -> None:
        nonlocal failed
        failed += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}: {name}{': ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory() as tmp:
        monitor = subprocess.Popen([sys.executable, str(SCRIPT), "--log-dir", tmp, "--seconds", "16"],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(2)
        bad_at = time.time()
        subprocess.Popen(probe, creationflags=subprocess.CREATE_NEW_CONSOLE).wait()
        time.sleep(1)
        good_at = time.time()
        subprocess.Popen(probe, creationflags=subprocess.CREATE_NO_WINDOW).wait()
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
        monitor.wait()

        logs = list(Path(tmp).glob("*.jsonl"))
        check(bool(logs), "the monitor wrote a log")
        text = "".join(p.read_text(encoding="utf-8") for p in logs)
        records = [json.loads(line) for line in text.splitlines()]
        mine = [f for f in wfm.find_flashes(records) if f["source"] and parent in [e.lower() for e in f["source"]]]
        bad = [f for f in mine if bad_at <= f["t"] < good_at]
        good = [f for f in mine if good_at <= f["t"] < good_end]
        check(len(bad) == 1 and bad[0]["source"][0].lower() == "cmd.exe", "known-bad probe is reported",
              " < ".join(bad[0]["source"][:3]) if bad else "no flash with this test in its chain")
        check(not good, "known-good probe adds no flash", f"{len(good)} flash(es)")
        check(SENTINEL not in text, "a titled window outside Claude's tree stays out of the log")
        errors = Path(tmp) / "monitor-errors.log"
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
