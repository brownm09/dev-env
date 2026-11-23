#!/usr/bin/env python3
"""Which Claude Code sessions on this machine are working right now (dev-env#1398).

Purpose: lists the busy Claude sessions (a turn in progress, or a background task still
running) for restart-guard.py, from each CLI's ~/.claude/sessions/<pid>.json and its transcript.

Each running CLI keeps `~/.claude/sessions/<pid>.json` with its `sessionId`, `cwd`, `name`,
`procStart` (the process creation time as a Windows FILETIME) and `status`: "busy" while a turn
runs, "idle" between turns, "waiting" while it waits on the user. None of this is documented,
so every reading here fails toward "not busy" and the guard then blocks nothing.

A session is busy when:
- its process is alive, matched by pid AND creation time, because files from dead processes stay
  behind and Windows reuses pids; and
- its status is "busy", or it is "idle" but its transcript says the last turn left a background
  task with no completion notice (`_auto_resume.classify` -> `waiting_on_background`): a restart
  would kill that task.

"waiting" isn't busy. A session waiting on you loses nothing to a restart that auto-resume
(ADR-152) can't bring back.

The transcript is read only for an idle session, and the result is cached by the file's size and
mtime, so a 30 s poll stays cheap.

Known blind spot: `_auto_resume.read_tail` reads the last 512 KB, so a background task launched
earlier than that in a long turn isn't seen, and the session reads as not busy. That fails open. The
report-only period (ADR-165) is where it would show: a restart that killed a background task with
no `busy_changed` line before it.
"""
from __future__ import annotations

import ctypes
import json
import os
import time
from pathlib import Path
from typing import Callable, NamedTuple

import _auto_resume
from _worktree_liveness import encode_project_slug

CLAUDE_HOME = Path.home() / ".claude"
SESSIONS_DIR = CLAUDE_HOME / "sessions"
PROJECTS = CLAUDE_HOME / "projects"


class Busy(NamedTuple):
    session_id: str
    name: str
    why: str  # "working" or "waiting on a background task"


_K32 = None
STILL_ACTIVE = 259
FIND_RETRY_SECONDS = 600


def _kernel32():
    """kernel32 with the prototypes process_created needs, set up once."""
    global _K32
    if _K32 is None:
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        _K32 = k32
    return _K32


def process_created(pid: int) -> int | None:
    """The creation time of a running process as a FILETIME integer, or None when it isn't running
    or can't be opened. A process that has exited still opens while another process holds a handle
    to it, so its exit code must still be STILL_ACTIVE."""
    if os.name != "nt":
        return None
    from ctypes import wintypes

    k32 = _kernel32()
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        k32.CloseHandle(handle)


def read_registry(sessions_dir: Path = SESSIONS_DIR) -> list[dict]:
    """Every readable session file. An unreadable one is skipped: it may be mid-write."""
    out = []
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def is_alive(entry: dict, created: Callable[[int], int | None] = process_created) -> bool:
    pid, start = entry.get("pid"), entry.get("procStart")
    if not isinstance(pid, int) or not isinstance(start, str) or not start.isdigit():
        return False
    return created(pid) == int(start)


def direct_transcript(entry: dict, projects: Path = PROJECTS) -> Path | None:
    sid, cwd = entry.get("sessionId"), entry.get("cwd")
    if isinstance(sid, str) and isinstance(cwd, str):
        direct = projects / encode_project_slug(cwd) / f"{sid}.jsonl"
        if direct.is_file():
            return direct
    return None


class Detector:
    """Polls the session files. Holds two caches between polls, each pruned to the sessions seen in
    the latest poll: the transcript verdict, keyed by the file's size and mtime, and where a
    transcript not at its direct path was found (a miss is retried every FIND_RETRY_SECONDS, since
    the search walks every project folder)."""

    def __init__(
        self,
        sessions_dir: Path = SESSIONS_DIR,
        projects: Path = PROJECTS,
        created: Callable[[int], int | None] = process_created,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sessions_dir = sessions_dir
        self.projects = projects
        self.created = created
        self.clock = clock
        self._cache: dict[str, tuple[tuple[int, float], bool]] = {}
        self._found: dict[str, tuple[float, Path | None]] = {}
        self._seen: set[str] = set()

    def _transcript(self, entry: dict) -> Path | None:
        direct = direct_transcript(entry, self.projects)
        sid = entry.get("sessionId")
        if direct is not None or not isinstance(sid, str):
            return direct
        cached = self._found.get(sid)
        if cached and (cached[1] is not None or self.clock() - cached[0] < FIND_RETRY_SECONDS):
            return cached[1]
        path = _auto_resume.find_transcript(sid, self.projects)
        self._found[sid] = (self.clock(), path)
        return path

    def _waiting_on_background(self, entry: dict) -> bool:
        path = self._transcript(entry)
        if path is None:
            return False
        try:
            st = path.stat()
        except OSError:
            return False
        key, sig = str(path), (st.st_size, st.st_mtime)
        self._seen.add(key)
        cached = self._cache.get(key)
        if cached and cached[0] == sig:
            return cached[1]
        # restarted_at=None: a pending Monitor doesn't count. It only watches, so a restart
        # loses no work there, and auto-resume restarts it.
        verdict = _auto_resume.classify(_auto_resume.read_tail(path))["state"] == "waiting_on_background"
        self._cache[key] = (sig, verdict)
        return verdict

    def busy(self) -> list[Busy]:
        out = []
        entries = read_registry(self.sessions_dir)
        self._seen = set()
        live_ids = {e.get("sessionId") for e in entries}
        self._found = {k: v for k, v in self._found.items() if k in live_ids}
        for entry in entries:
            if not is_alive(entry, self.created):
                continue
            status = entry.get("status")
            name = entry.get("name") if isinstance(entry.get("name"), str) else ""
            sid = str(entry.get("sessionId"))
            if status == "busy":
                out.append(Busy(sid, name, "working"))
            elif status == "idle" and self._waiting_on_background(entry):
                out.append(Busy(sid, name, "waiting on a background task"))
        self._cache = {k: v for k, v in self._cache.items() if k in self._seen}
        return sorted(out)


def reason_text(busy: list[Busy], limit: int = 200) -> str:
    """The short reason Windows shows on its "apps are preventing restart" screen. Session
    names only, never transcript content."""
    if not busy:
        return ""
    count = f"{len(busy)} Claude session{'s are' if len(busy) != 1 else ' is'} still working"
    names = ", ".join(b.name or b.session_id[:8] for b in busy)
    text = f"{count}: {names}"
    return text if len(text) <= limit else text[: limit - 3] + "..."
