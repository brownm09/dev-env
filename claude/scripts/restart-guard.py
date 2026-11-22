#!/usr/bin/env python3
"""Hold off a Windows restart while Claude sessions are working (dev-env#1398, ADR-165).

Purpose: a long-running hidden-task process that asks for confirmation before a restart you start
and defers Windows Update's automatic restart while any Claude session is busy.

Run by the ClaudeRestartGuard scheduled task (register-restart-guard.ps1), at sign-in, non-elevated,
outside the Claude app's process tree. Every 30 s it asks `_busy_sessions.Detector` which sessions are
busy: a turn in progress, or a background task still running.

Layer 1, a restart you start. The process owns a small top-level window, visible but placed off-screen
with no taskbar button, because Windows lets only an application with a visible window cancel a
shutdown (https://learn.microsoft.com/en-us/windows/win32/shutdown/shutdown-changes-for-windows-vista).
While sessions are busy it sets a block reason (ShutdownBlockReasonCreate) naming them and answers
WM_QUERYENDSESSION with FALSE. Windows then shows its "apps are preventing restart" screen with that
reason, and Restart anyway still works. A critical shutdown (ENDSESSION_CRITICAL) is never refused.

Layer 2, Windows Update's automatic restart. Windows Update doesn't restart automatically during
active hours (https://support.microsoft.com/windows/manage-device-restarts-after-updates), which can
span up to 18 hours. While sessions are busy, the guard moves ActiveHoursStart/End under
HKLM\\SOFTWARE\\Microsoft\\WindowsUpdate\\UX\\Settings so the current hour sits inside the window
(start = 2 hours ago, end = 18 hours after start). It puts back the captured values when nothing is
busy, and after `maxDeferHours` (default 24) of continuous deferral. BUILTIN\\Users has SetValue on that
key, so this needs no elevation. Before its first write it captures the values to
Documents\\LOGS\\ClaudeRestartGuardActiveHoursBackup.json (written if absent, never deleted, ADR-079),
and it reads back each write.

Config ~/.claude/restart-guard.json, created by the register script:
  {"enabled": true, "block": false, "deferUpdates": false, "maxDeferHours": 24}
`block` and `deferUpdates` start false: report-only. The guard then only logs what it would have done,
so the busy check can be calibrated against real sessions before it blocks anything (Pass 3 gate
calibration). Turn each on once its log looks right.

Fail direction: open. If the busy check fails, nothing counts as busy, so no restart is refused and
active hours go back. An unreadable config means report-only.

Log: Documents\\LOGS\\claude-restart-guard_<date>.jsonl, one JSON object per event: start, a change in
the busy set, each WM_QUERYENDSESSION and the answer, each active-hours write or restore.

Usage:
  pyw -3 restart-guard.py            run the guard (what the task runs)
  py -3 restart-guard.py --status    print the busy sessions and what the guard would do now
  py -3 restart-guard.py --restore   put the captured active hours back and clear the deferral
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

import _winsubp  # noqa: F401  -- UTF-8 decoding + no console flash for child processes (ADR-007)
import _busy_sessions

CLAUDE_HOME = Path.home() / ".claude"
CONFIG_PATH = CLAUDE_HOME / "restart-guard.json"
STATE_DIR = CLAUDE_HOME / "scratch" / "restart-guard"
DEFERRAL_PATH = STATE_DIR / "deferral.json"
UX_KEY = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
POLL_SECONDS = 30
DEFAULTS = {"enabled": True, "block": False, "deferUpdates": False, "maxDeferHours": 24}


def docs_logs() -> Path:
    """Documents\\LOGS, the backup-anchor and log home (ADR-079). Resolved through the shell, since
    Documents may be redirected."""
    if os.name == "nt":
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
            return Path(buf.value) / "LOGS"
    return Path.home() / "Documents" / "LOGS"


ANCHOR_NAME = "ClaudeRestartGuardActiveHoursBackup.json"


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


# ------------------------------------------------------------------ Layer 2: active hours


class ActiveHours:
    """Reads and writes the two DWORDs. A seam for the tests, which pass a dict-backed fake."""

    def read(self) -> dict[str, int | None]:
        import winreg

        out: dict[str, int | None] = {}
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UX_KEY, 0, winreg.KEY_READ) as key:
            for name in ("ActiveHoursStart", "ActiveHoursEnd"):
                try:
                    out[name] = int(winreg.QueryValueEx(key, name)[0])
                except FileNotFoundError:
                    out[name] = None
        return out

    def set(self, name: str, value: int) -> None:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UX_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)

    def delete(self, name: str) -> None:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UX_KEY, 0, winreg.KEY_SET_VALUE) as key:
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


class AnchorError(Exception):
    pass


def read_anchor(path: Path) -> dict | None:
    """The captured values, None when there is no anchor. Raises AnchorError when it is unusable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        values = {k: data[k] for k in ("ActiveHoursStart", "ActiveHoursEnd")}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AnchorError(f"{path} is unusable ({type(exc).__name__}). Refusing to restore.") from exc
    if not all(v is None or (isinstance(v, int) and 0 <= v <= 23) for v in values.values()):
        raise AnchorError(f"{path} holds out-of-range values. Refusing to restore.")
    return values


def save_anchor(path: Path, current: dict) -> bool:
    """Write the anchor only if absent. False when it can't be written, or one exists that
    restore couldn't use: the caller then makes no change."""
    if path.exists():
        try:
            return read_anchor(path) is not None
        except AnchorError:
            return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**current, "capturedAt": dt.datetime.now().isoformat(timespec="seconds")}, indent=2),
                        encoding="utf-8")
        return True
    except OSError:
        return False


def restore(hours: ActiveHours, anchor_path: Path, logf: Callable[..., None]) -> str:
    """Put the captured values back. Returns 'restored', 'skip' or 'no-anchor'. Raises AnchorError."""
    captured = read_anchor(anchor_path)
    if captured is None:
        return "no-anchor"
    current = hours.read()
    if current == captured:
        return "skip"
    for name, value in captured.items():
        if value is None:
            hours.delete(name)
        else:
            hours.set(name, value)
    after = hours.read()
    if after != captured:
        logf("active_hours_error", detail=f"restore read back {after}, expected {captured}")
        raise AnchorError(f"restore read back {after}, expected {captured}")
    logf("active_hours_restored", values=captured)
    return "restored"


def read_deferral(path: Path = DEFERRAL_PATH) -> float | None:
    try:
        return float(json.loads(path.read_text(encoding="utf-8"))["since"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_deferral(since: float | None, path: Path = DEFERRAL_PATH) -> None:
    try:
        if since is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"since": since}), encoding="utf-8")
    except OSError:
        pass


def update_active_hours(
    busy: bool,
    cfg: dict,
    hours: ActiveHours,
    anchor_path: Path,
    now: dt.datetime,
    logf: Callable[..., None],
    deferral_path: Path = DEFERRAL_PATH,
) -> str:
    """One Layer 2 step. Returns what it did: 'report', 'deferred', 'skip', 'restored', 'cap',
    'idle', 'no-anchor' or 'error'. Never raises.

    The cap counts from the first deferral, and its clock resets only on an idle step outside the
    captured active hours, when Windows Update could restart. An idle moment during the day doesn't
    reset it, or a short gap each day could hold off an update forever."""
    try:
        since = read_deferral(deferral_path)
        if busy and since is None:
            since = now.timestamp()
            write_deferral(since, deferral_path)
        if not busy and since is not None:
            captured = read_anchor(anchor_path) or {}
            if outside_active_hours(now.hour, captured):
                write_deferral(None, deferral_path)
                since = None
        capped = since is not None and now.timestamp() - since >= cfg["maxDeferHours"] * 3600
        if busy and not capped:
            start, end = window_for(now)
            if not cfg["deferUpdates"]:
                return "report"
            current = hours.read()
            if current == {"ActiveHoursStart": start, "ActiveHoursEnd": end}:
                return "skip"
            if not save_anchor(anchor_path, current):
                logf("active_hours_error", detail=f"could not write {anchor_path}; refusing to change active hours")
                return "error"
            hours.set("ActiveHoursStart", start)
            hours.set("ActiveHoursEnd", end)
            after = hours.read()
            if after != {"ActiveHoursStart": start, "ActiveHoursEnd": end}:
                logf("active_hours_error", detail=f"wrote {start}-{end}, read back {after}")
                return "error"
            logf("active_hours_deferred", start=start, end=end, previous=current)
            return "deferred"
        result = restore(hours, anchor_path, logf)
        if capped and result == "restored":  # once, not every poll while the cap holds
            logf("active_hours_cap", maxDeferHours=cfg["maxDeferHours"], since=since)
        return "cap" if capped else ("restored" if result == "restored" else ("idle" if result == "skip" else result))
    except Exception as exc:  # noqa: BLE001 -- the guard must keep running
        logf("active_hours_error", detail=f"{type(exc).__name__}: {exc}")
        return "error"


# ------------------------------------------------------------------ Layer 1: the window

WM_DESTROY, WM_QUERYENDSESSION, WM_ENDSESSION, WM_TIMER = 0x0002, 0x0011, 0x0016, 0x0113
ENDSESSION_CRITICAL = 0x40000000


def query_end_session_answer(busy: list, cfg: dict, lparam: int) -> bool:
    """True lets the session end. A critical shutdown is never refused, and report-only never
    refuses anything."""
    if lparam & ENDSESSION_CRITICAL:
        return True
    return not (cfg["enabled"] and cfg["block"] and busy)


class Guard:
    def __init__(
        self,
        detector: _busy_sessions.Detector | None = None,
        config_path: Path = CONFIG_PATH,
        deferral_path: Path = DEFERRAL_PATH,
    ) -> None:
        self.detector = detector or _busy_sessions.Detector()
        self.config_path = config_path
        self.deferral_path = deferral_path
        self.busy: list = []
        self.cfg = load_config(config_path)
        self.hwnd = None
        self.reason_set = ""
        self.anchor = docs_logs() / ANCHOR_NAME
        self.hours = ActiveHours()

    def poll(self) -> None:
        self.cfg = load_config(self.config_path)
        try:
            busy = self.detector.busy() if self.cfg["enabled"] else []
        except Exception as exc:  # noqa: BLE001 -- fail open: nothing counts as busy
            log("busy_check_error", detail=f"{type(exc).__name__}: {exc}")
            busy = []
        if [b.session_id for b in busy] != [b.session_id for b in self.busy]:
            log("busy_changed", count=len(busy), sessions=[{"name": b.name, "why": b.why} for b in busy],
                block=self.cfg["block"], deferUpdates=self.cfg["deferUpdates"])
        self.busy = busy
        self._set_reason()
        update_active_hours(bool(busy), self.cfg, self.hours, self.anchor, dt.datetime.now(), log, self.deferral_path)

    def _set_reason(self) -> None:
        if self.hwnd is None:
            return
        user32 = ctypes.windll.user32
        want = _busy_sessions.reason_text(self.busy) if (self.cfg["block"] and self.busy) else ""
        if want == self.reason_set:
            return
        if want:
            user32.ShutdownBlockReasonCreate(self.hwnd, ctypes.c_wchar_p(want))
        else:
            user32.ShutdownBlockReasonDestroy(self.hwnd)
        self.reason_set = want

    def on_query_end_session(self, lparam: int) -> bool:
        self.poll()  # fresh: the cached answer may be up to 30 s old
        allow = query_end_session_answer(self.busy, self.cfg, lparam)
        log("query_end_session", lparam=hex(lparam), busy=len(self.busy), allowed=allow,
            wouldBlock=bool(self.busy) and not lparam & ENDSESSION_CRITICAL, block=self.cfg["block"])
        return allow

    def run(self) -> int:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.ShutdownBlockReasonCreate.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.ShutdownBlockReasonDestroy.argtypes = [wintypes.HWND]
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        def wndproc(hwnd, msg, wparam, lparam):
            try:
                if msg == WM_QUERYENDSESSION:
                    return 1 if self.on_query_end_session(int(lparam) & 0xFFFFFFFF) else 0
                if msg == WM_TIMER:
                    self.poll()
                    return 0
                if msg == WM_ENDSESSION:
                    log("end_session", ending=bool(wparam))
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
        return 0


# ------------------------------------------------------------------ CLI


def status() -> int:
    cfg = load_config()
    busy = _busy_sessions.Detector().busy()
    print(f"config: {cfg}")
    print(f"busy sessions: {len(busy)}")
    for b in busy:
        print(f"  {b.name or b.session_id}: {b.why}")
    print(f"a restart you start now: {'needs confirmation' if not query_end_session_answer(busy, cfg, 0) else 'goes ahead'}"
          f"{' (report-only: would need confirmation)' if busy and not cfg['block'] else ''}")
    try:
        print(f"active hours now: {ActiveHours().read()}; while busy the guard sets {window_for(dt.datetime.now())}"
              f"{'' if cfg['deferUpdates'] else ' (report-only: not written)'}")
    except OSError as exc:
        print(f"active hours: unreadable ({exc})")
    since = read_deferral()
    if since:
        print(f"deferring since {dt.datetime.fromtimestamp(since).isoformat(timespec='minutes')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hold off restarts while Claude sessions are working.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="print what the guard sees and would do")
    group.add_argument("--restore", action="store_true", help="put the captured active hours back")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("restart-guard is Windows-only.")
        return 0
    if args.status:
        return status()
    if args.restore:
        write_deferral(None)
        try:
            result = restore(ActiveHours(), docs_logs() / ANCHOR_NAME, log)
        except AnchorError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print({"restored": "Active hours restored.", "skip": "Active hours already match the backup.",
               "no-anchor": "No backup anchor: the guard never changed active hours."}[result])
        return 0
    return Guard().run()


if __name__ == "__main__":
    sys.exit(main())
