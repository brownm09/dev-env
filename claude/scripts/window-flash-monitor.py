#!/usr/bin/env python3
"""
Record console flashes and the process behind each one (dev-env#1225, ADR-155).

A console window that appears for a moment is gone before anyone can see what caused
it, and its owner process has often exited too. This monitor keeps a short history of
running processes, so it can still name that process, and logs what it sees to
``~/.claude/scratch/window-flash-monitor/YYYY-MM-DD.jsonl``, keeping 14 days.

    pyw -3 window-flash-monitor.py              run until stopped (the scheduled task)
    py -3 window-flash-monitor.py --report      today's report
    py -3 window-flash-monitor.py --report 2026-11-05

The monitor only observes. It keeps titles only for windows descended from claude.exe,
because other titles can expose private content. A console window keeps only the program
name found in its title. Other windows are not logged at all, apart from the one
foreground change that ends a flash's time in front, which is logged without a title.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from pathlib import Path

LOG_DIR = Path.home() / ".claude" / "scratch" / "window-flash-monitor"
RETENTION_DAYS = 14
SNAPSHOT_SECONDS = 0.5  # 0.1 cost 22% of a core; a console's own process is caught when it shows
WATCH_SECONDS = 0.02  # how often a console window that may be a flash is checked
HISTORY_SECONDS = 60
RECENT_SECONDS = 3.0
BEAT_SECONDS = 300
FLASH_MAX_SECONDS = 5.0
FOREGROUND_LEAD_SECONDS = 1.0  # a foreground event can land just before the show event

FLASH_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
CONSOLE_CLASSES = FLASH_CLASSES | {"PseudoConsoleWindow"}
TERMINAL_HOSTS = {"windowsterminal.exe", "conhost.exe", "openconsole.exe"}
PROGRAM_RE = re.compile(r"([\w.-]+\.exe)\b", re.I)
LOG_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")


def program_from_title(title: str) -> str | None:
    """The program a console window names in its title, and nothing else from the title."""
    found = PROGRAM_RE.findall(title or "")
    return found[-1] if found else None


def is_claude(chain: list[str]) -> bool:
    return any(exe.lower() == "claude.exe" for exe in chain)


class ProcessHistory:
    """Recently seen processes, kept for a while after they exit."""

    def __init__(self, keep_seconds: float = HISTORY_SECONDS):
        self.keep_seconds = keep_seconds
        self.entries: dict[int, dict] = {}

    def update(self, snapshot: dict[int, tuple[str, int]], now: float) -> None:
        for pid, (exe, ppid) in snapshot.items():
            entry = self.entries.get(pid)
            if entry is None or entry["exe"] != exe or entry["ppid"] != ppid:  # new, or a reused pid
                self.entries[pid] = {"exe": exe, "ppid": ppid, "first": now, "last": now}
            else:
                entry["last"] = now
        for pid in [p for p, e in self.entries.items() if now - e["last"] > self.keep_seconds]:
            del self.entries[pid]

    def chain(self, pid: int) -> list[str]:
        """Executable names from pid up through its parents, newest first."""
        names: list[str] = []
        entry = self.entries.get(pid)
        while entry is not None and len(names) < 12:
            names.append(entry["exe"])
            parent = self.entries.get(entry["ppid"])
            # A parent seen after its child is a reused pid, not the real parent.
            if parent is None or parent["first"] > entry["first"]:
                break
            entry = parent
        return names

    def recent(self, now: float, seconds: float = RECENT_SECONDS, limit: int = 30) -> list[dict]:
        """Processes first seen in the last few seconds, newest first."""
        pids = [p for p, e in self.entries.items() if now - e["first"] <= seconds]
        pids.sort(key=lambda p: self.entries[p]["first"], reverse=True)
        return [{"exe": self.entries[p]["exe"], "chain": self.chain(p)} for p in pids[:limit]]


def window_record(event: str, now: float, hwnd: int, pid: int, cls: str, title: str,
                  history: ProcessHistory) -> dict | None:
    """The log record for a show or foreground event, or None if the window isn't logged."""
    chain = history.chain(pid)
    claude = is_claude(chain)
    console = cls in CONSOLE_CLASSES
    if not console and not claude:
        return None
    record = {"t": round(now, 3), "e": event, "hwnd": hwnd, "pid": pid, "cls": cls, "chain": chain}
    program = program_from_title(title) if console else None
    if program:
        record["program"] = program
    if claude and title:
        record["title"] = title
    if console and event == "show":
        record["recent"] = history.recent(now)
    return record


def likely_source(show: dict, program: str | None, pseudo: dict | None = None) -> list[str] | None:
    """The parent chain of the process behind a console window, or None if unknown.

    A classic console window and a PseudoConsoleWindow both report the program using them as
    their owner. A Windows Terminal window reports only itself, so the fallback is the program
    named in its title, matched against processes that had just started.
    """
    chain = show.get("chain") or []
    if chain and chain[0].lower() not in TERMINAL_HOSTS:
        return chain
    pseudo_chain = (pseudo or {}).get("chain") or []
    if pseudo_chain and (not program or pseudo_chain[0].lower() == program.lower()):
        return pseudo_chain
    if program:
        for candidate in show.get("recent", []):
            if candidate["exe"].lower() == program.lower():
                return candidate["chain"]
    return None


def find_flashes(records: list[dict], max_seconds: float = FLASH_MAX_SECONDS) -> list[dict]:
    """Console windows shown and gone within max_seconds, with focus loss and source."""
    records = sorted((r for r in records if "t" in r), key=lambda r: r["t"])
    flashes = []
    for i, show in enumerate(records):
        if show.get("e") != "show" or show.get("cls") not in FLASH_CLASSES:
            continue
        hwnd = show["hwnd"]
        gone = next((r for r in records[i + 1:] if r.get("hwnd") == hwnd and r.get("e") == "hide"), None)
        if gone is None or gone["t"] - show["t"] > max_seconds:
            continue
        program = show.get("program") or next(
            (r["program"] for r in records[i + 1:]
             if r.get("hwnd") == hwnd and r.get("e") == "name" and r["t"] <= gone["t"]), None)
        pseudo = next((r for r in records[i + 1:] if r.get("e") == "show"
                       and r.get("cls") == "PseudoConsoleWindow" and r["t"] <= gone["t"]), None)
        source = likely_source(show, program, pseudo)
        flashes.append({
            "t": show["t"], "cls": show["cls"], "seconds": round(gone["t"] - show["t"], 3),
            "focus_lost": _focus_lost(records, hwnd, show["t"], gone["t"]),
            "program": program, "source": source, "claude": bool(source and is_claude(source)),
        })
    return flashes


def _focus_lost(records: list[dict], hwnd: int, shown: float, gone: float) -> float:
    """Seconds this window held the foreground, or 0.0 if it never took it."""
    foreground = [r for r in records if r.get("e") == "foreground"]
    taken = next((r for r in foreground if r["hwnd"] == hwnd
                  and shown - FOREGROUND_LEAD_SECONDS <= r["t"] <= gone), None)
    if taken is None:
        return 0.0
    back = next((r for r in foreground if r["t"] > taken["t"] and r["hwnd"] != hwnd), None)
    return round((back["t"] if back else gone) - taken["t"], 3)


def claude_windows(records: list[dict]) -> list[dict]:
    """Titled windows shown by a process descended from claude.exe."""
    return [r for r in records if r.get("e") == "show" and r.get("title")
            and is_claude(r.get("chain") or [])]


def load_records(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue  # a line cut short by a crash or power loss
    return records


def _clock(t: float) -> str:
    return _dt.datetime.fromtimestamp(t).strftime("%H:%M:%S")


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def render_report(date: str, records: list[dict]) -> str:
    times = [r["t"] for r in records if "t" in r]
    if not times:
        return f"# Console flashes on {date}\n\nThe log has no records, so nothing can be said about this day.\n"
    flashes = find_flashes(records)
    known = sum(1 for f in flashes if f["source"])
    from_claude = sum(1 for f in flashes if f["claude"])
    lines = [
        f"# Console flashes on {date}", "",
        f"Monitor recorded from {_clock(min(times))} to {_clock(max(times))}. "
        "Anything outside those times was not seen.", "",
        f"Flashes: {len(flashes)} ({from_claude} from Claude, {known - from_claude} from elsewhere, "
        f"{len(flashes) - known} unattributed).", "",
    ]
    if flashes:
        lines += ["| # | Time | Seconds shown | Focus lost (s) | Program | Source chain | From Claude |",
                  "|---|------|---------------|----------------|---------|--------------|-------------|"]
        for n, f in enumerate(flashes, 1):
            source = " < ".join(f["source"]) if f["source"] else "unattributed"
            lines.append(f"| {n} | {_clock(f['t'])} | {f['seconds']} | {f['focus_lost']} | "
                         f"{f['program'] or '-'} | {_cell(source)} | {'yes' if f['claude'] else 'no'} |")
        lines.append("")
    windows = claude_windows(records)
    lines.append(f"Other windows opened under Claude: {len(windows)}.")
    if windows:
        lines += ["", "| # | Time | Process | Title |", "|---|------|---------|-------|"]
        for n, r in enumerate(windows, 1):
            lines.append(f"| {n} | {_clock(r['t'])} | {r['chain'][0]} | {_cell(r['title'])} |")
    return "\n".join(lines) + "\n"


def expired_logs(names: list[str], today: _dt.date, days: int = RETENTION_DAYS) -> list[str]:
    """Daily log names older than the retention window. Other names are never returned."""
    old = []
    for name in names:
        match = LOG_NAME_RE.match(name)
        if match and (today - _dt.date.fromisoformat(match.group(1))).days > days:
            old.append(name)
    return old


class Monitor:
    """Event handling, kept apart from the Windows calls so it can be read on its own."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.history = ProcessHistory()
        self.watched: dict[int, dict] = {}  # console windows young enough to still be a flash
        self.log_next_foreground = False
        self.log_date = ""

    def write(self, record: dict) -> None:
        date = _dt.date.today().isoformat()
        if date != self.log_date:
            self.log_date = date
            self.prune()
        with open(self.log_dir / f"{date}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def prune(self) -> None:
        names = [p.name for p in self.log_dir.iterdir()]
        for name in expired_logs(names, _dt.date.today()):
            (self.log_dir / name).unlink(missing_ok=True)

    def on_show(self, now: float, hwnd: int, pid: int, cls: str, title: str) -> None:
        record = window_record("show", now, hwnd, pid, cls, title, self.history)
        if record:
            self.write(record)
            if cls in CONSOLE_CLASSES:
                self.watched[hwnd] = {"shown": now, "program": record.get("program")}

    def on_foreground(self, now: float, hwnd: int, pid: int, cls: str, title: str) -> None:
        record = window_record("foreground", now, hwnd, pid, cls, title, self.history)
        if record is None and self.log_next_foreground:
            record = {"t": round(now, 3), "e": "foreground", "hwnd": hwnd}  # just ends the flash's time in front
        if record:
            self.write(record)
        self.log_next_foreground = cls in CONSOLE_CLASSES

    def check_watched(self, now: float, hwnd: int, visible: bool, title: str) -> None:
        """Log a watched window's program name and its end. Past the flash limit, stop watching."""
        state = self.watched[hwnd]
        if not visible:
            del self.watched[hwnd]
            self.write({"t": round(now, 3), "e": "hide", "hwnd": hwnd})
        elif now - state["shown"] > FLASH_MAX_SECONDS:
            del self.watched[hwnd]
        else:
            program = program_from_title(title)
            if program and program != state["program"]:
                state["program"] = program
                self.write({"t": round(now, 3), "e": "name", "hwnd": hwnd, "program": program})


def run_monitor(log_dir: Path, seconds: float | None) -> int:
    """Hook window events and snapshot processes until stopped. Windows only."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    log_dir.mkdir(parents=True, exist_ok=True)
    if log_dir == LOG_DIR:  # a run with its own log directory, such as a test, may overlap the task
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        mutex = kernel32.CreateMutexW(None, True, "Local\\ClaudeWindowFlashMonitor")
        if ctypes.get_last_error() == 183 or not mutex:  # ERROR_ALREADY_EXISTS
            return 0

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def snapshot() -> dict[int, tuple[str, int]]:
        found: dict[int, tuple[str, int]] = {}
        handle = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if handle in (None, wintypes.HANDLE(-1).value):
            return found
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            more = kernel32.Process32FirstW(handle, ctypes.byref(entry))
            while more:
                found[entry.th32ProcessID] = (entry.szExeFile, entry.th32ParentProcessID)
                more = kernel32.Process32NextW(handle, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(handle)
        return found

    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)

    def describe(hwnd) -> tuple[int, str, str]:
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        return pid.value, cls.value, title.value

    monitor = Monitor(log_dir)
    monitor.history.update(snapshot(), time.time())
    errors: set[str] = set()

    def log_error(error: Exception) -> None:
        text = f"{type(error).__name__}: {error}"
        if text not in errors:  # once per distinct error: this runs on every window event
            errors.add(text)
            with open(log_dir / "monitor-errors.log", "a", encoding="utf-8") as fh:
                fh.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} {text}\n")

    EVENT_FOREGROUND, EVENT_SHOW = 0x3, 0x8002

    def on_event(_hook, event, hwnd, id_object, id_child, _thread, _time) -> None:
        if not hwnd or id_object != 0 or id_child != 0:  # OBJID_WINDOW, CHILDID_SELF
            return
        try:
            if user32.GetAncestor(hwnd, 2) != hwnd:  # GA_ROOT
                return
            now = time.time()
            pid, cls, title = describe(hwnd)
            if cls in CONSOLE_CLASSES or title:  # catches a process born since the last snapshot
                monitor.history.update(snapshot(), now)
            (monitor.on_show if event == EVENT_SHOW else monitor.on_foreground)(now, hwnd, pid, cls, title)
        except Exception as error:  # an exception can't cross the callback, and pyw has no console
            log_error(error)

    proc_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                   wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
    callback = proc_type(on_event)  # must outlive the hooks
    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.SetWinEventHook.argtypes = (wintypes.UINT, wintypes.UINT, wintypes.HMODULE, proc_type,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.UINT)
    user32.UnhookWinEvent.argtypes = (wintypes.HANDLE,)
    flags = 0x2  # WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
    # Only these two: hide, destroy and name-change events fire far too often to take in Python,
    # so a window that may be a flash is checked directly in the loop below.
    hooks = [user32.SetWinEventHook(e, e, None, callback, 0, 0, flags) for e in (EVENT_FOREGROUND, EVENT_SHOW)]
    if not all(hooks):
        log_error(OSError("SetWinEventHook failed"))
        return 1

    message = wintypes.MSG()
    user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                    wintypes.UINT, wintypes.UINT)
    user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.MsgWaitForMultipleObjects.argtypes = (wintypes.DWORD, wintypes.LPVOID, wintypes.BOOL,
                                                 wintypes.DWORD, wintypes.DWORD)
    started = last_snapshot = time.time()
    last_beat = 0.0
    try:
        while seconds is None or time.time() - started < seconds:
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):  # PM_REMOVE
                user32.DispatchMessageW(ctypes.byref(message))
            now = time.time()
            try:
                for hwnd in list(monitor.watched):
                    monitor.check_watched(now, hwnd, bool(user32.IsWindowVisible(hwnd)), describe(hwnd)[2])
                if now - last_snapshot >= SNAPSHOT_SECONDS:
                    monitor.history.update(snapshot(), now)
                    last_snapshot = now
                if now - last_beat >= BEAT_SECONDS:
                    monitor.write({"t": round(now, 3), "e": "beat"})
                    last_beat = now
            except OSError as error:  # a full disk should not end the monitor
                log_error(error)
            wait = WATCH_SECONDS if monitor.watched else SNAPSHOT_SECONDS
            user32.MsgWaitForMultipleObjects(0, None, False, int(wait * 1000), 0x04FF)  # QS_ALLINPUT
    finally:
        for hook in hooks:
            user32.UnhookWinEvent(hook)
    return 0


def report(date: str | None, log_dir: Path) -> int:
    day = date or _dt.date.today().isoformat()
    path = log_dir / f"{day}.jsonl"
    if not path.exists():
        print(f"No log for {day} at {path}. The monitor was not running, so this says nothing about flashes.")
        return 1
    print(render_report(day, load_records(path)), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    ap.add_argument("--report", nargs="?", const="", metavar="YYYY-MM-DD", help="print a day's report (default today)")
    ap.add_argument("--log-dir", type=Path, default=LOG_DIR, help="log directory (for tests)")
    ap.add_argument("--seconds", type=float, help="stop after this long (for tests)")
    args = ap.parse_args(argv)
    if args.report is not None:
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        return report(args.report or None, args.log_dir)
    return run_monitor(args.log_dir, args.seconds)


if __name__ == "__main__":
    sys.exit(main())
