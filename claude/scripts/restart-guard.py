#!/usr/bin/env python3
"""Hold off a Windows restart while Claude sessions are working (dev-env#1398, ADR-165).

Purpose: a long-running hidden-task process that asks for confirmation before a restart you start
and defers Windows Update's automatic restart while any Claude session is busy.

Run by the ClaudeRestartGuard scheduled task (register-restart-guard.ps1), at sign-in, non-elevated,
outside the Claude app's process tree. Every 30 s it asks `_busy_sessions.Detector` which sessions are
busy: a turn in progress, or a background task still running. A named mutex keeps it to one copy.

Layer 1, a restart you start. The process owns a small top-level window, visible but placed off-screen
with no taskbar button, because Windows lets only an application with a visible window cancel a
shutdown (https://learn.microsoft.com/en-us/windows/win32/shutdown/shutdown-changes-for-windows-vista).
While sessions are busy it sets a block reason (ShutdownBlockReasonCreate) naming them and answers
WM_QUERYENDSESSION with FALSE, from the state of the last poll so the answer is immediate. Windows
then shows its "apps are preventing restart" screen with that reason, and Restart anyway still works.
It never refuses a critical shutdown (ENDSESSION_CRITICAL) or a Restart Manager request to close
(ENDSESSION_CLOSEAPP), and it exits after the latter; the task starts it again.

Layer 2, Windows Update's automatic restart. Windows Update doesn't restart automatically during
active hours (https://support.microsoft.com/windows/manage-device-restarts-after-updates), which can
span up to 18 hours. While sessions are busy, the guard moves ActiveHoursStart/End under
HKLM\\SOFTWARE\\Microsoft\\WindowsUpdate\\UX\\Settings so the current hour sits inside the window
(start = 2 hours ago, end = 18 hours after start). BUILTIN\\Users has SetValue on that key, so this
needs no elevation. Each write is read back.

Its backup, %USERPROFILE%\\Documents\\LOGS\\ClaudeRestartGuardActiveHoursBackup.json, holds the values
captured when a deferral starts and the window the guard last wrote. Its path is fixed, so moving
Documents can't hide it. The guard puts the captured values back once nothing has been busy for
IDLE_POLLS polls in a row, and when the deferral reaches `maxDeferHours` (default 24). It does that
only while the registry still holds its own window: if anything else changed active hours meanwhile
(you in Settings, or Windows), it leaves that change alone. The next deferral captures the values
afresh, so a change you make between deferrals is the one restored. The backup is never deleted, and
an unusable backup stops any change.

The cap's clock starts at the first deferral and resets only when an idle step falls outside the
captured active hours, when Windows Update could restart, so a short daytime gap can't keep resetting
it. It is kept in ~/.claude/restart-guard-state.json, and runs only while `deferUpdates` is on.

Config ~/.claude/restart-guard.json, created by the register script:
  {"enabled": true, "block": false, "deferUpdates": false, "maxDeferHours": 24}
`block` and `deferUpdates` start false: report-only. The guard then only logs what it would have done,
so the busy check can be calibrated against real sessions before it blocks anything (Pass 3 gate
calibration). Turn each on once its log looks right. Turning `deferUpdates` off ends any deferral.

Fail direction: open. If the busy check fails, nothing counts as busy, so no restart is refused, and
active hours aren't touched for that poll: no new deferral, and no restore. A deferral already in
place still ends at the cap. An unreadable config means report-only.

Log: Documents\\LOGS\\claude-restart-guard_<date>.jsonl, one JSON object per event: start, a change in
the busy set, each WM_QUERYENDSESSION and the answer, each change in what Layer 2 does, and each
distinct error once (then `recovered`). Files older than LOG_RETENTION_DAYS are deleted.

Usage:
  pyw -3 restart-guard.py            run the guard (what the task runs)
  py -3 restart-guard.py --status    print the busy sessions and what the guard would do now
  py -3 restart-guard.py --restore   end the deferral: put the captured active hours back. Refuses
                                     while the guard runs; stop the task first.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Callable

import _busy_sessions

CLAUDE_HOME = Path.home() / ".claude"
CONFIG_PATH = CLAUDE_HOME / "restart-guard.json"
STATE_PATH = CLAUDE_HOME / "restart-guard-state.json"
ANCHOR_NAME = "ClaudeRestartGuardActiveHoursBackup.json"
ANCHOR_PATH = Path.home() / "Documents" / "LOGS" / ANCHOR_NAME
UX_KEY = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
NAMES = ("ActiveHoursStart", "ActiveHoursEnd")
MUTEX_NAME = "Local\\ClaudeRestartGuard"
POLL_SECONDS = 30
IDLE_POLLS = 3
LOG_RETENTION_DAYS = 14
DEFAULTS = {"enabled": True, "block": False, "deferUpdates": False, "maxDeferHours": 24}


def docs_logs() -> Path:
    """Documents\\LOGS, the log home. Resolved through the shell, since Documents may be redirected."""
    if os.name == "nt":
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
            return Path(buf.value) / "LOGS"
    return Path.home() / "Documents" / "LOGS"


# ------------------------------------------------------------------ config and log


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(data, dict):
        return dict(DEFAULTS)
    cfg = {**DEFAULTS, **{k: data[k] for k in DEFAULTS if k in data}}
    if not isinstance(cfg["maxDeferHours"], (int, float)) or not 1 <= cfg["maxDeferHours"] <= 72:
        cfg["maxDeferHours"] = DEFAULTS["maxDeferHours"]
    for key in ("enabled", "block", "deferUpdates"):
        cfg[key] = cfg[key] is True
    return cfg


def log(event: str, logs_dir: Path | None = None, **fields) -> None:
    try:
        d = logs_dir or docs_logs()
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "event": event, **fields})
        with open(d / f"claude-restart-guard_{dt.date.today().isoformat()}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def prune_logs(logs_dir: Path, today: dt.date, keep_days: int = LOG_RETENTION_DAYS) -> int:
    """Delete this guard's daily logs older than keep_days. Returns how many it deleted."""
    removed = 0
    for path in logs_dir.glob("claude-restart-guard_*.jsonl"):
        try:
            day = dt.date.fromisoformat(path.stem.rsplit("_", 1)[1])
        except ValueError:
            continue
        if (today - day).days > keep_days:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# ------------------------------------------------------------------ Layer 2: active hours


class AnchorError(Exception):
    """The backup or the live values can't be trusted, so nothing may change."""


def check_values(values: dict) -> dict:
    """Each value must be absent (None) or an hour, 0 to 23. Raises AnchorError otherwise."""
    if set(values) != set(NAMES) or not all(
            v is None or (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 23) for v in values.values()):
        raise AnchorError(f"active hours {values} aren't hours (0 to 23, or unset)")
    return values


def as_values(window: tuple[int, int] | None) -> dict | None:
    return None if window is None else dict(zip(NAMES, window))


class ActiveHours:
    """Reads and writes the two DWORDs, in the 64-bit registry view. A seam for the tests, which pass
    a dict-backed fake."""

    def _key(self, access: int):
        import winreg

        return winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UX_KEY, 0, access | winreg.KEY_WOW64_64KEY)

    def read(self) -> dict[str, int | None]:
        """The two values, None for an absent one. Raises AnchorError for a value that isn't a DWORD."""
        import winreg

        out: dict[str, int | None] = {}
        with self._key(winreg.KEY_READ) as key:
            for name in NAMES:
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    out[name] = None
                    continue
                if kind != winreg.REG_DWORD:
                    raise AnchorError(f"{name} has registry type {kind}, not REG_DWORD")
                out[name] = value
        return out

    def set(self, name: str, value: int) -> None:
        import winreg

        with self._key(winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)

    def delete(self, name: str) -> None:
        import winreg

        with self._key(winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


def window_for(now: dt.datetime) -> tuple[int, int]:
    """An 18-hour window starting 2 hours before the current hour."""
    start = (now.hour - 2) % 24
    return start, (start + 18) % 24


def outside_active_hours(hour: int, values: dict) -> bool:
    """Whether Windows Update may restart at this hour under these values. Windows uses 8 to 17
    when they are unset."""
    start = values.get("ActiveHoursStart")
    end = values.get("ActiveHoursEnd")
    start, end = (8 if start is None else start), (17 if end is None else end)
    inside = start <= hour < end if start < end else (hour >= start or hour < end)
    return not inside


def read_anchor(path: Path) -> dict | None:
    """{"values": captured values, "window": the window the guard last wrote, or None when no deferral
    is outstanding}. None when there is no backup. Raises AnchorError when it is unusable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        values = check_values({k: data[k] for k in NAMES})
        window = data.get("guardWindow")
    except AnchorError as exc:
        raise AnchorError(f"{path}: {exc}. Refusing to change active hours.") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AnchorError(f"{path} is unusable ({type(exc).__name__}). Refusing to change active hours.") from exc
    if window is not None:
        if not (isinstance(window, list) and len(window) == 2):
            raise AnchorError(f"{path} has an unusable guardWindow. Refusing to change active hours.")
        try:
            check_values(as_values(tuple(window)))
        except AnchorError as exc:
            raise AnchorError(f"{path} has an unusable guardWindow. Refusing to change active hours.") from exc
        window = tuple(window)
    return {"values": values, "window": window}


def write_anchor(path: Path, values: dict, window: tuple[int, int] | None) -> None:
    """Write the backup through a temp file and read it back. Raises AnchorError on any failure."""
    check_values(values)
    data = {**values, "guardWindow": list(window) if window else None,
            "capturedAt": dt.datetime.now().isoformat(timespec="seconds")}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise AnchorError(f"could not write {path} ({exc}); refusing to change active hours") from exc
    back = read_anchor(path)
    if back != {"values": values, "window": window}:
        raise AnchorError(f"{path} read back {back}, expected {values} / {window}")


def _apply(hours: ActiveHours, values: dict) -> None:
    for name, value in values.items():
        if value is None:
            hours.delete(name)
        else:
            hours.set(name, value)
    after = hours.read()
    if after != values:
        raise AnchorError(f"wrote {values}, read back {after}")


def finish_deferral(hours: ActiveHours, anchor: dict, anchor_path: Path, logf: Callable[..., None]) -> str:
    """End an outstanding deferral. Returns 'restored' when the registry still held the guard's window
    and the captured values went back, or 'released' when something else had changed active hours,
    which are then left as they are. Raises AnchorError."""
    current = hours.read()
    if current == as_values(anchor["window"]):
        _apply(hours, anchor["values"])
        logf("active_hours_restored", values=anchor["values"])
        result = "restored"
    else:
        logf("active_hours_changed_elsewhere", found=current, guardWindow=list(anchor["window"]),
             detail="left as they are")
        result = "released"
    write_anchor(anchor_path, anchor["values"], None)
    return result


def defer(hours: ActiveHours, anchor: dict | None, anchor_path: Path, now: dt.datetime,
          logf: Callable[..., None]) -> str:
    """Move active hours to cover now. Returns 'deferred' or 'unchanged'. Raises AnchorError."""
    window = window_for(now)
    target = as_values(window)
    current = check_values(hours.read())
    outstanding = anchor is not None and anchor["window"] is not None
    if outstanding and current == as_values(anchor["window"]):
        captured = anchor["values"]  # still the guard's own window: the same deferral goes on
    else:
        if outstanding:  # someone else changed them during the deferral: theirs are the values to keep
            logf("active_hours_changed_elsewhere", found=current, guardWindow=list(anchor["window"]),
                 detail="captured as the values to restore")
        captured = current  # a new deferral captures the values as they are now
    if anchor is None or anchor["values"] != captured or anchor["window"] != window:
        write_anchor(anchor_path, captured, window)  # before any registry write
    if current == target:
        return "unchanged"
    _apply(hours, target)
    logf("active_hours_deferred", start=window[0], end=window[1], previous=current)
    return "deferred"


def read_since(path: Path = STATE_PATH) -> float | None:
    """When the current run of deferrals began, for the cap. None when there is none."""
    try:
        since = json.loads(path.read_text(encoding="utf-8"))["since"]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return float(since) if isinstance(since, (int, float)) else None


def write_since(since: float | None, path: Path = STATE_PATH) -> None:
    try:
        if since is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"since": since}), encoding="utf-8")
    except OSError:
        pass


def update_active_hours(
    busy: bool | None,
    cfg: dict,
    hours: ActiveHours,
    anchor_path: Path,
    now: dt.datetime,
    logf: Callable[..., None],
    state_path: Path = STATE_PATH,
) -> str:
    """One Layer 2 step. `busy` is True, False, or None when it isn't known (the busy check failed,
    or nothing has been busy for fewer than IDLE_POLLS polls): None makes no new deferral and ends
    none, except at the cap. Returns 'report', 'deferred', 'unchanged', 'hold', 'restored',
    'released', 'skip', 'cap' or 'error'. Never raises."""
    try:
        anchor = read_anchor(anchor_path)
        outstanding = anchor is not None and anchor["window"] is not None
        since = read_since(state_path) if cfg["deferUpdates"] else None
        if not cfg["deferUpdates"]:
            write_since(None, state_path)
        elif busy is False and since is not None:
            baseline = anchor["values"] if anchor else check_values(hours.read())
            if outside_active_hours(now.hour, baseline):
                write_since(None, state_path)
                since = None
        capped = since is not None and now.timestamp() - since >= cfg["maxDeferHours"] * 3600
        if busy and cfg["deferUpdates"] and not capped:
            if since is None:
                write_since(now.timestamp(), state_path)
            return defer(hours, anchor, anchor_path, now, logf)
        if busy is None and not capped:
            return "hold"
        result = finish_deferral(hours, anchor, anchor_path, logf) if outstanding else "skip"
        if capped:
            if outstanding:  # once, when the deferral ends, not every poll while the cap holds
                logf("active_hours_cap", maxDeferHours=cfg["maxDeferHours"], since=since)
            return "cap"
        if busy and result == "skip":
            return "report"  # busy, with deferUpdates off: report-only
        return result
    except Exception as exc:  # noqa: BLE001 -- the guard must keep running
        logf("active_hours_error", detail=f"{type(exc).__name__}: {exc}")
        return "error"


# ------------------------------------------------------------------ Layer 1: the window

WM_DESTROY, WM_QUERYENDSESSION, WM_ENDSESSION, WM_TIMER = 0x0002, 0x0011, 0x0016, 0x0113
ENDSESSION_CLOSEAPP, ENDSESSION_CRITICAL = 0x00000001, 0x40000000
ERROR_ALREADY_EXISTS = 183


def query_end_session_answer(busy: list, cfg: dict, lparam: int) -> bool:
    """True lets the session end. A critical shutdown and a Restart Manager request to close are
    never refused, and report-only never refuses anything."""
    if lparam & (ENDSESSION_CRITICAL | ENDSESSION_CLOSEAPP):
        return True
    return not (cfg["enabled"] and cfg["block"] and busy)


def guard_running() -> bool:
    """Whether a guard holds the mutex in this session."""
    if os.name != "nt":
        return False
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = wintypes.HANDLE
    k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenMutexW(0x00100000, False, MUTEX_NAME)  # SYNCHRONIZE
    if handle:
        k32.CloseHandle(handle)
        return True
    return False


class Guard:
    def __init__(
        self,
        detector: _busy_sessions.Detector | None = None,
        config_path: Path = CONFIG_PATH,
        state_path: Path = STATE_PATH,
        anchor_path: Path = ANCHOR_PATH,
        logs_dir: Path | None = None,
    ) -> None:
        self.detector = detector or _busy_sessions.Detector()
        self.logs_dir = logs_dir
        self.config_path = config_path
        self.state_path = state_path
        self.anchor = anchor_path
        self.busy: list = []
        self.cfg = load_config(config_path)
        self.hours = ActiveHours()
        self.idle_polls = 0
        self.layer2 = ""
        self.hwnd = None
        self.user32 = None
        self.reason_set = ""
        self._errors: dict[str, str] = {}
        self._log_day: dt.date | None = None

    def log(self, event: str, **fields) -> None:
        """Each distinct error is logged once; `recovered` is logged when it clears."""
        if event.endswith("_error"):
            detail = str(fields.get("detail"))
            if self._errors.get(event) == detail:
                return
            self._errors[event] = detail
        log(event, **fields)

    def _recovered(self, event: str) -> None:
        if self._errors.pop(event, None) is not None:
            log("recovered", error=event)

    def poll(self) -> None:
        today = dt.date.today()
        if today != self._log_day:
            self._log_day = today
            removed = prune_logs(self.logs_dir or docs_logs(), today)
            if removed:
                log("logs_pruned", removed=removed, keepDays=LOG_RETENTION_DAYS)
        self.cfg = load_config(self.config_path)
        try:
            busy = self.detector.busy() if self.cfg["enabled"] else []
        except Exception as exc:  # noqa: BLE001 -- fail open: nothing counts as busy
            self.log("busy_check_error", detail=f"{type(exc).__name__}: {exc}")
            busy, known = [], False
        else:
            self._recovered("busy_check_error")
            known = True
        if [b.session_id for b in busy] != [b.session_id for b in self.busy]:
            log("busy_changed", count=len(busy), sessions=[{"name": b.name, "why": b.why} for b in busy],
                block=self.cfg["block"], deferUpdates=self.cfg["deferUpdates"])
        self.busy = busy
        if not known:
            layer2_busy = None
        elif busy:
            self.idle_polls, layer2_busy = 0, True
        else:
            self.idle_polls += 1
            layer2_busy = False if self.idle_polls >= IDLE_POLLS else None
        result = update_active_hours(layer2_busy, self.cfg, self.hours, self.anchor, dt.datetime.now(),
                                     self.log, self.state_path)
        if result != "error":
            self._recovered("active_hours_error")
        if result != self.layer2:
            log("active_hours", result=result)
            self.layer2 = result
        self._set_reason()

    def _set_reason(self) -> None:
        if self.hwnd is None or self.user32 is None:
            return
        want = _busy_sessions.reason_text(self.busy) if (self.cfg["block"] and self.busy) else ""
        if want == self.reason_set:
            return
        if want:
            ok = self.user32.ShutdownBlockReasonCreate(self.hwnd, want)
        else:
            ok = self.user32.ShutdownBlockReasonDestroy(self.hwnd)
        if not ok:
            self.log("block_reason_error", detail=f"error {ctypes.get_last_error()} setting {want!r}")
            return  # tried again at the next poll
        self._recovered("block_reason_error")
        self.reason_set = want

    def on_query_end_session(self, lparam: int) -> bool:
        """Answers from the last poll, so Windows gets the answer at once."""
        allow = query_end_session_answer(self.busy, self.cfg, lparam)
        log("query_end_session", lparam=hex(lparam), busy=len(self.busy), allowed=allow,
            wouldBlock=bool(self.busy) and not lparam & (ENDSESSION_CRITICAL | ENDSESSION_CLOSEAPP),
            block=self.cfg["block"])
        return allow

    def run(self) -> int:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        ctypes.set_last_error(0)
        self._mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)  # held for the life of the process
        if not self._mutex or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            log("already_running", pid=os.getpid())
            return 0

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.ShutdownBlockReasonCreate.restype = wintypes.BOOL
        user32.ShutdownBlockReasonCreate.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.ShutdownBlockReasonDestroy.restype = wintypes.BOOL
        user32.ShutdownBlockReasonDestroy.argtypes = [wintypes.HWND]
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        self.user32 = user32

        def wndproc(hwnd, msg, wparam, lparam):
            try:
                if msg == WM_QUERYENDSESSION:
                    return 1 if self.on_query_end_session(int(lparam) & 0xFFFFFFFF) else 0
                if msg == WM_TIMER:
                    self.poll()
                    return 0
                if msg == WM_ENDSESSION:
                    closeapp = bool(int(lparam) & ENDSESSION_CLOSEAPP)
                    log("end_session", ending=bool(wparam), closeApp=closeapp)
                    if wparam and closeapp:  # Restart Manager wants the files the guard holds
                        user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as exc:  # noqa: BLE001 -- fail open on any error inside the window proc
                log("wndproc_error", detail=f"{type(exc).__name__}: {exc}")
                return 1 if msg == WM_QUERYENDSESSION else 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # keep a reference for the life of the window
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=hinst, lpszClassName="ClaudeRestartGuard")
        if not user32.RegisterClassW(ctypes.byref(wc)):
            log("start_error", detail=f"RegisterClassW failed: {ctypes.get_last_error()}")
            return 1
        WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
        WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
        # Visible, so Windows lets it cancel a shutdown; off-screen, 1x1 and a tool window, so it
        # has no taskbar button and is never seen. The title is what the blocking screen names.
        self.hwnd = user32.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, "ClaudeRestartGuard",
                                           "Claude sessions", WS_POPUP | WS_VISIBLE,
                                           -32000, -32000, 1, 1, None, None, hinst, None)
        if not self.hwnd:
            log("start_error", detail=f"CreateWindowExW failed: {ctypes.get_last_error()}")
            return 1
        log("start", pid=os.getpid(), config=self.cfg)
        self.poll()
        user32.SetTimer(self.hwnd, 1, POLL_SECONDS * 1000, None)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        log("exit", pid=os.getpid())
        return 0


# ------------------------------------------------------------------ CLI


def status() -> int:
    cfg = load_config()
    busy = _busy_sessions.Detector().busy()
    print(f"config: {cfg}")
    print(f"guard running: {'yes' if guard_running() else 'no'}")
    print(f"busy sessions: {len(busy)}")
    for b in busy:
        print(f"  {b.name or b.session_id}: {b.why}")
    print(f"a restart you start now: {'needs confirmation' if not query_end_session_answer(busy, cfg, 0) else 'goes ahead'}"
          f"{' (report-only: would need confirmation)' if busy and not cfg['block'] else ''}")
    try:
        print(f"active hours now: {ActiveHours().read()}; while busy the guard sets {window_for(dt.datetime.now())}"
              f"{'' if cfg['deferUpdates'] else ' (report-only: not written)'}")
    except (OSError, AnchorError) as exc:
        print(f"active hours: unreadable ({exc})")
    try:
        anchor = read_anchor(ANCHOR_PATH)
    except AnchorError as exc:
        print(f"backup: {exc}")
    else:
        if anchor and anchor["window"]:
            print(f"deferral outstanding: the guard set {anchor['window']}; it restores {anchor['values']}")
    since = read_since() if cfg["deferUpdates"] else None
    if since:
        print(f"deferring since {dt.datetime.fromtimestamp(since).isoformat(timespec='minutes')}"
              f" (cap {cfg['maxDeferHours']} h)")
    return 0


def restore_command(running: Callable[[], bool] = guard_running, hours: ActiveHours | None = None,
                    anchor_path: Path = ANCHOR_PATH, state_path: Path = STATE_PATH) -> int:
    if running():
        print("ERROR: the guard is running and would defer again within 30 s. Stop it first: "
              "Stop-ScheduledTask ClaudeRestartGuard (register-restart-guard.ps1 -Unregister does this).",
              file=sys.stderr)
        return 1
    try:
        anchor = read_anchor(anchor_path)
        if anchor is None or anchor["window"] is None:
            print("Skip: no deferral outstanding" + ("" if anchor else " (no backup: the guard never changed active hours)")
                  + ".")
            write_since(None, state_path)
            return 0
        result = finish_deferral(hours or ActiveHours(), anchor, anchor_path, log)
    except (AnchorError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    write_since(None, state_path)
    print({"restored": "Active hours restored.",
           "released": "Active hours had been changed since the guard set them; left as they are."}[result])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hold off restarts while Claude sessions are working.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="print what the guard sees and would do")
    group.add_argument("--restore", action="store_true", help="end the deferral: put the captured active hours back")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("restart-guard is Windows-only.")
        return 0
    if args.status:
        return status()
    if args.restore:
        return restore_command()
    return Guard().run()


if __name__ == "__main__":
    sys.exit(main())
