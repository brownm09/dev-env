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

The monitor only observes, and logs as little as it can, because a record of windows is a
record of what the user was doing. A window descended from claude.exe keeps its title when
it is shown. A console window keeps only the program its title names. Foreground changes are
logged only around a console window young enough to be a flash, without titles. Nothing else
is logged.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as _dt
import json
import re
import sys
import time
import traceback
from pathlib import Path

LOG_DIR = Path.home() / ".claude" / "scratch" / "window-flash-monitor"
ERROR_LOG = "monitor-errors.log"
RETENTION_DAYS = 14
LOG_CAP_BYTES = 200 * 1024 * 1024  # a stream of flashes logs tens of MB a day
SNAPSHOT_SECONDS = 0.5  # 0.1 cost 22% of a core; a console's own process is caught when it shows
WATCH_SECONDS = 0.02  # how often a console window that may be a flash is checked
HISTORY_SECONDS = 60
RECENT_SECONDS = 3.0
RECENT_LIMIT = 12
CHAIN_LIMIT = 12
BEAT_SECONDS = 300
FLASH_MAX_SECONDS = 5.0
FOREGROUND_LEAD_SECONDS = 1.0  # a foreground event can land just before the show event
FOCUS_RETURN_SECONDS = 5.0  # a later foreground change is the user's, not focus returning

FLASH_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
CONSOLE_CLASSES = FLASH_CLASSES | {"PseudoConsoleWindow"}
TERMINAL_HOSTS = {"windowsterminal.exe", "conhost.exe", "openconsole.exe"}
# A chain ending at one of these is complete. Any other ending may be a parent that has exited.
ORIGIN_ROOTS = {"wininit.exe", "winlogon.exe", "services.exe", "svchost.exe", "explorer.exe", "csrss.exe"}
LOG_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def program_from_title(title: str) -> str | None:
    """The program a console window's title names, if the title is just that program.

    A path or a bare name counts. Text that merely contains ``.exe`` doesn't, because a title
    can hold a file name or a command the user typed.
    """
    text = (title or "").strip().removeprefix("Administrator: ")
    if not text.lower().endswith(".exe"):
        return None
    if DRIVE_PATH_RE.match(text) or " " not in text:
        return re.split(r"[\\/]", text)[-1] or None
    return None


def is_claude(chain: list[str]) -> bool:
    return any(exe.lower() == "claude.exe" for exe in chain)


def origin(source: list[str] | None) -> str:
    """claude, elsewhere, or unknown. A chain cut off at an exited parent proves nothing."""
    if not source:
        return "unknown"
    if is_claude(source):
        return "claude"
    return "elsewhere" if source[-1].lower() in ORIGIN_ROOTS else "unknown"


class ProcessHistory:
    """Recently seen processes, kept for a while after they exit."""

    def __init__(self):
        self.entries: dict[int, dict] = {}

    def update(self, snapshot: dict[int, tuple[str, int]], now: float,
               created: dict[int, float] | None = None) -> None:
        """Merge a snapshot. ``created`` gives real start times for the first snapshot.

        Without them every process in it would tie, and the reused-pid check in chain() could
        never fire. A process whose time can't be read belongs to another user or the system.
        It gets the oldest time, so a chain can still walk up through it.
        """
        for pid, (exe, ppid) in snapshot.items():
            entry = self.entries.get(pid)
            if entry is None or entry["exe"] != exe or entry["ppid"] != ppid:  # new, or a reused pid
                first = now if created is None else created.get(pid, 0.0)
                self.entries[pid] = {"exe": exe, "ppid": ppid, "first": first, "last": now}
            else:
                entry["last"] = now
        for pid in [p for p, e in self.entries.items() if now - e["last"] > HISTORY_SECONDS]:
            del self.entries[pid]

    def chain(self, pid: int) -> list[str]:
        """Executable names from pid up through its parents, newest first."""
        names: list[str] = []
        current = pid
        entry = self.entries.get(current)
        while entry is not None and len(names) < CHAIN_LIMIT:
            names.append(entry["exe"])
            parent = self.entries.get(entry["ppid"])
            # A parent that started after its child is a reused pid, not the real parent.
            if entry["ppid"] == current or parent is None or parent["first"] > entry["first"]:
                break
            current, entry = entry["ppid"], parent
        return names

    def recent(self, now: float) -> list[dict]:
        """Processes first seen in the last few seconds, newest first, without console hosts."""
        pids = [p for p, e in self.entries.items() if now - e["first"] <= RECENT_SECONDS
                and e["exe"].lower() not in TERMINAL_HOSTS]
        pids.sort(key=lambda p: self.entries[p]["first"], reverse=True)
        return [{"exe": self.entries[p]["exe"], "chain": self.chain(p)} for p in pids[:RECENT_LIMIT]]


def show_record(now: float, hwnd: int, pid: int, cls: str, title: str, history: ProcessHistory) -> dict | None:
    """The log record for a window being shown, or None if the window isn't logged."""
    chain = history.chain(pid)
    claude = is_claude(chain)
    console = cls in CONSOLE_CLASSES
    if not console and not claude:
        return None
    record = {"t": round(now, 3), "e": "show", "hwnd": hwnd, "pid": pid, "cls": cls, "chain": chain}
    program = program_from_title(title) if console else None
    if program:
        record["program"] = program
    if claude and title:
        record["title"] = title
    if console:
        record["recent"] = history.recent(now)
    return record


def likely_source(show: dict, program: str | None, pseudo: dict | None) -> list[str] | None:
    """The parent chain of the process behind a console window, or None if unknown.

    A classic console window and a PseudoConsoleWindow both report the program using them as
    their owner. A Windows Terminal window reports only itself, so the fallback is the program
    named in its title, matched against processes that had just started.
    """
    chain = show.get("chain") or []
    if chain and chain[0].lower() not in TERMINAL_HOSTS:
        return chain
    if pseudo:
        return pseudo["chain"]
    if program:
        for candidate in show.get("recent", []):
            if candidate["exe"].lower() == program.lower():
                return candidate["chain"]
    return None


def _flash_windows(records: list[dict]) -> list[tuple[dict, dict, str | None]]:
    """(show, hide, program) for each flash-class window shown and gone within the limit."""
    windows: dict[int, list[dict]] = collections.defaultdict(list)
    for record in records:
        if "hwnd" in record:
            windows[record["hwnd"]].append(record)
    found = []
    for events in windows.values():
        for i, show in enumerate(events):
            if show.get("e") != "show" or show.get("cls") not in FLASH_CLASSES:
                continue
            program, gone = show.get("program"), None
            for later in events[i + 1:]:
                if later.get("e") == "show":
                    break
                if later.get("e") == "hide":
                    gone = later
                    break
                if later.get("e") == "name" and not program:
                    program = later.get("program")
            if gone and gone["t"] - show["t"] <= FLASH_MAX_SECONDS:
                found.append((show, gone, program))
    found.sort(key=lambda item: item[0]["t"])
    return found


def find_flashes(records: list[dict]) -> list[dict]:
    """Console windows shown and gone within the flash limit, with focus loss and source."""
    records = sorted((r for r in records if "t" in r), key=lambda r: r["t"])
    foreground = [r for r in records if r.get("e") == "foreground"]
    pseudos = [r for r in records if r.get("e") == "show" and r.get("cls") == "PseudoConsoleWindow"
               and r.get("chain")]
    foreground_times = [r["t"] for r in foreground]
    pseudo_times = [r["t"] for r in pseudos]
    used: set[int] = set()
    flashes = []
    for show, gone, program in _flash_windows(records):
        pseudo = None
        if program:  # without a name to match, a pseudo console could be any program's
            for j in range(bisect.bisect_left(pseudo_times, show["t"]), len(pseudos)):
                if pseudos[j]["t"] > gone["t"]:
                    break
                if j not in used and pseudos[j]["chain"][0].lower() == program.lower():
                    used.add(j)
                    pseudo = pseudos[j]
                    break
        source = likely_source(show, program, pseudo)
        lost, returned = _focus_lost(foreground, foreground_times, show["hwnd"], show["t"], gone["t"])
        flashes.append({
            "t": show["t"], "cls": show["cls"], "seconds": round(gone["t"] - show["t"], 3),
            "focus_lost": lost, "focus_returned": returned,
            "program": program, "source": source, "origin": origin(source),
        })
    return flashes


def _focus_lost(foreground: list[dict], times: list[float], hwnd: int, shown: float,
                gone: float) -> tuple[float, bool]:
    """Seconds this window held the foreground, and whether focus was seen going elsewhere."""
    for j in range(bisect.bisect_left(times, shown - FOREGROUND_LEAD_SECONDS), len(foreground)):
        if times[j] > gone:
            break
        if foreground[j]["hwnd"] != hwnd:
            continue
        for k in range(j + 1, len(foreground)):
            if times[k] > gone + FOCUS_RETURN_SECONDS:
                break
            if foreground[k]["hwnd"] != hwnd:
                return round(times[k] - times[j], 3), True
        return round(gone - times[j], 3), False
    return 0.0, True


def claude_windows(records: list[dict]) -> list[dict]:
    """Titled windows shown by a process descended from claude.exe."""
    return [r for r in records if r.get("e") == "show" and r.get("title")
            and is_claude(r.get("chain") or [])]


def recording_gaps(times: list[float]) -> list[tuple[float, float]]:
    """Spans with no records long enough that the monitor can't have been running."""
    ordered = sorted(times)
    return [(a, b) for a, b in zip(ordered, ordered[1:]) if b - a > 2 * BEAT_SECONDS]


def load_records(path: Path) -> list[dict]:
    records = []
    # errors="replace": a crash can cut a line in the middle of a character.
    with open(path, encoding="utf-8", errors="replace") as fh:
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
    counts = collections.Counter(f["origin"] for f in flashes)
    lines = [f"# Console flashes on {date}", "",
             f"Monitor recorded from {_clock(min(times))} to {_clock(max(times))}. "
             "Anything outside those times was not seen."]
    gaps = recording_gaps(times)
    if gaps:
        spans = ", ".join(f"{_clock(a)} to {_clock(b)}" for a, b in gaps)
        lines.append(f"Nothing was seen in these gaps either, because the monitor was off or the machine asleep: {spans}.")
    lines += ["", f"Flashes: {len(flashes)} ({counts['claude']} from Claude, {counts['elsewhere']} from elsewhere, "
                  f"{counts['unknown']} of unknown origin).", ""]
    if flashes:
        lines += ["| # | Time | Seconds shown | Focus lost (s) | Program | Source chain | From Claude |",
                  "|---|------|---------------|----------------|---------|--------------|-------------|"]
        answer = {"claude": "yes", "elsewhere": "no", "unknown": "unknown"}
        for n, f in enumerate(flashes, 1):
            source = " < ".join(f["source"]) if f["source"] else "unattributed"
            lost = f"{f['focus_lost']}{'' if f['focus_returned'] else '+'}"
            lines.append(f"| {n} | {_clock(f['t'])} | {f['seconds']} | {lost} | "
                         f"{f['program'] or '-'} | {_cell(source)} | {answer[f['origin']]} |")
        lines.append("")
        if not all(f["focus_returned"] for f in flashes):
            lines += ["A + means focus was not seen going elsewhere, so the time is a minimum.", ""]
    windows = claude_windows(records)
    lines.append(f"Other windows opened under Claude: {len(windows)}.")
    if windows:
        lines += ["", "| # | Time | Process | Title |", "|---|------|---------|-------|"]
        for n, r in enumerate(windows, 1):
            lines.append(f"| {n} | {_clock(r['t'])} | {r['chain'][0]} | {_cell(r['title'])} |")
    return "\n".join(lines) + "\n"


def expired_logs(sizes: dict[str, int], today: _dt.date) -> list[str]:
    """Daily logs past retention, then the oldest past the size cap. Today's log always stays.

    Other names are never returned, including one shaped like a date that isn't one.
    """
    old, kept = [], []
    for name, size in sizes.items():
        match = LOG_NAME_RE.match(name)
        if not match:
            continue
        try:
            day = _dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if (today - day).days > RETENTION_DAYS:
            old.append(name)
        else:
            kept.append((day, name, size))
    total = 0
    for day, name, size in sorted(kept, reverse=True):
        total += size
        if total > LOG_CAP_BYTES and day != today:
            old.append(name)
    return old


def append_error(log_dir: Path, text: str) -> None:
    """Record a monitor error. pyw has no console, so this file is the only place for it."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / ERROR_LOG, "a", encoding="utf-8", errors="backslashreplace") as fh:
            stamp = _dt.datetime.now().isoformat(timespec="seconds")
            fh.write(f"{stamp} {text.strip().replace(chr(10), ' | ')}\n")
    except OSError:
        pass  # a full disk or a missing directory: nowhere left to say so, and it shouldn't stop the monitor


class Monitor:
    """Event handling, kept apart from the Windows calls so it can be read on its own."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.history = ProcessHistory()
        self.watched: dict[int, dict] = {}  # console windows young enough to still be a flash
        self.foreground = 0
        self.pending_foreground: tuple[float, int] | None = None  # may precede its window's show
        self.just_gone = 0
        self.log_date = ""
        self.errors: set[str] = set()

    def log_error(self, error: Exception) -> None:
        text = f"{type(error).__name__}: {error}"
        if text not in self.errors:  # once per distinct error: this can run on every window event
            self.errors.add(text)
            append_error(self.log_dir, text)

    def write(self, record: dict, date: str | None = None) -> None:
        today = _dt.date.today().isoformat()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if today != self.log_date:
            self.log_date = today
            self.prune()
        with open(self.log_dir / f"{date or today}.jsonl", "a", encoding="utf-8", errors="backslashreplace") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def prune(self) -> None:
        try:
            sizes = {p.name: p.stat().st_size for p in self.log_dir.iterdir()}
            for name in expired_logs(sizes, _dt.date.today()):
                (self.log_dir / name).unlink(missing_ok=True)
        except OSError as error:  # a log that can't be removed shouldn't lose the record being written
            self.log_error(error)

    def on_show(self, now: float, hwnd: int, pid: int, cls: str, title: str) -> None:
        record = show_record(now, hwnd, pid, cls, title, self.history)
        if record is None:
            return
        if cls in CONSOLE_CLASSES:  # first, so a failed write can't also lose the window's end
            # Its later records go in this day's log, so a flash across midnight stays whole.
            self.watched[hwnd] = {"shown": now, "program": record.get("program"),
                                  "date": _dt.date.today().isoformat()}
        pending = self.pending_foreground
        if pending and pending[1] == hwnd and now - pending[0] <= FOREGROUND_LEAD_SECONDS:
            self.write({"t": pending[0], "e": "foreground", "hwnd": hwnd})
        self.pending_foreground = None
        self.write(record)

    def on_foreground(self, now: float, hwnd: int, cls: str) -> None:
        previous, self.foreground = self.foreground, hwnd
        t = round(now, 3)
        ended_flash = self.just_gone != 0 and previous == self.just_gone
        if hwnd in self.watched or previous in self.watched or ended_flash:
            if ended_flash:
                self.just_gone = 0
            self.write({"t": t, "e": "foreground", "hwnd": hwnd})
        elif cls in FLASH_CLASSES:
            self.pending_foreground = (t, hwnd)  # an ordinary terminal never gets a show, so it's never logged

    def check_watched(self, now: float, hwnd: int, visible: bool, title: str) -> None:
        """Log a watched window's program name and its end. Past the flash limit, stop watching."""
        state = self.watched[hwnd]
        if not visible:
            del self.watched[hwnd]
            self.just_gone = hwnd
            self.write({"t": round(now, 3), "e": "hide", "hwnd": hwnd}, state["date"])
        elif now - state["shown"] > FLASH_MAX_SECONDS:
            del self.watched[hwnd]
        else:
            program = program_from_title(title)
            if program and program != state["program"]:
                state["program"] = program
                self.write({"t": round(now, 3), "e": "name", "hwnd": hwnd, "program": program}, state["date"])


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
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4

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

    def created_at(pid: int) -> float | None:
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                return None
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return ticks / 1e7 - 11644473600  # 100 ns units since 1601, to seconds since 1970
        finally:
            kernel32.CloseHandle(handle)

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
    first_snapshot = snapshot()
    created = {pid: t for pid in first_snapshot if (t := created_at(pid)) is not None}
    monitor.history.update(first_snapshot, time.time(), created)

    EVENT_FOREGROUND, EVENT_SHOW = 0x3, 0x8002

    def on_event(_hook, event, hwnd, id_object, id_child, _thread, _time) -> None:
        if not hwnd or id_object != 0 or id_child != 0:  # OBJID_WINDOW, CHILDID_SELF
            return
        try:
            if user32.GetAncestor(hwnd, 2) != hwnd:  # GA_ROOT
                return
            now = time.time()
            pid, cls, title = describe(hwnd)
            if event == EVENT_SHOW:
                if cls in CONSOLE_CLASSES or title:  # catches a process born since the last snapshot
                    monitor.history.update(snapshot(), now)
                monitor.on_show(now, hwnd, pid, cls, title)
            else:
                monitor.on_foreground(now, hwnd, cls)
        except Exception as error:  # an exception can't cross the callback, and pyw has no console
            monitor.log_error(error)

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
        monitor.log_error(OSError("SetWinEventHook failed"))
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
                    last_beat = now
                    monitor.write({"t": round(now, 3), "e": "beat"})
            except Exception as error:  # a full disk or a bad record should not end the monitor
                monitor.log_error(error)
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
    errors = log_dir / ERROR_LOG
    if errors.exists():  # a bug raising on every event would otherwise read as zero flashes
        lines = [line for line in errors.read_text(encoding="utf-8", errors="replace").splitlines()
                 if line.startswith(day)]
        if lines:
            print("\nThe monitor logged errors this day, so the counts above may be low:")
            print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    ap.add_argument("--report", nargs="?", const="", metavar="YYYY-MM-DD", help="print a day's report (default today)")
    ap.add_argument("--log-dir", type=Path, default=LOG_DIR, help="log directory (for tests)")
    ap.add_argument("--seconds", type=float, help="stop after this long (for tests)")
    args = ap.parse_args(argv)
    if args.report is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        return report(args.report or None, args.log_dir)
    try:
        return run_monitor(args.log_dir, args.seconds)
    except Exception:  # pyw has no console, so an unlogged crash would leave no trace
        append_error(args.log_dir, traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
