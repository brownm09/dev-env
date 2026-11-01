"""Shared logic for auto-resuming desktop sessions cut off mid-turn (dev-env#1182, ADR-152).

Two callers use it:

* ``session-start-auto-resume.py`` is a ``SessionStart`` (``resume``) hook wired with
  ``asyncRewake``. When a session's CLI process starts and its transcript ended mid-turn,
  the hook wakes the model with a "check for errors and continue" message. Exiting 2 is
  what starts the turn.
* ``auto-resume-launcher.py`` runs from a Windows scheduled task. After the Claude app
  starts, it opens each interrupted, opted-in session with the
  ``claude://code/continue?session=`` deep link, so the app starts that session's CLI
  and the hook above fires. The app does not do this on its own. After a restart it
  starts a session's CLI only when the session is opened.

The opt-in lives in ``~/.claude/auto-resume.json``, which is machine-local and not in
this repo. A missing, unreadable or ``"enabled": false`` file means nothing is ever
resumed (fail closed).

The app's own session files (``Claude-Data/*/claude-code-sessions/<account>/<org>/
local_*.json``) are read-only inputs here and are never written. Only ``cliSessionId``,
``cwd``, ``title``, ``isArchived`` and ``isStarred`` (the sidebar pin) are read. An
unreadable file means "no metadata": the session can still match ``all`` or
``session:<cliSessionId>``, but never ``pinned``.
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import fnmatch
import json
import os
import time
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
CONFIG_PATH = CLAUDE_HOME / "auto-resume.json"
PROJECTS = CLAUDE_HOME / "projects"
STATE_DIR = CLAUDE_HOME / "scratch" / "auto-resume"
ATTEMPTS_PATH = STATE_DIR / "attempts.json"

DEFAULTS = {
    "enabled": False,
    # Rules, any of which opts a session in: "all", "pinned", "cwd:<glob>",
    # "session:<local_... or cliSessionId>". `exclude` uses the same syntax and wins.
    "include": [],
    "exclude": [],
    # A turn cut off longer ago than this is left alone.
    "maxAgeHours": 12,
    # The hook waits this long before waking the model, then re-reads the transcript.
    # Any new message in the meantime (the user typed, or a queued message ran) cancels.
    "settleSeconds": 20,
    # At most this many wakes per session within maxAgeHours, so a turn that keeps
    # crashing is not resumed forever.
    "maxAttempts": 2,
}

# A turn whose last step is one of these is waiting on the user, not interrupted.
WAITING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
USER_STOP_MARKER = "[Request interrupted by user"
TAIL_BYTES = 512 * 1024


# --- config -------------------------------------------------------------------------


def load_config(path: Path | None = None) -> dict:
    """Return the config merged over DEFAULTS. Missing or bad file -> disabled."""
    cfg = dict(DEFAULTS)
    try:
        raw = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return cfg
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    if not isinstance(cfg["include"], list) or not isinstance(cfg["exclude"], list):
        cfg["enabled"] = False
    return cfg


def _rule_matches(rule: str, meta: dict) -> bool:
    if rule == "all":
        return True
    if rule == "pinned":
        return meta.get("isStarred") is True
    kind, _, value = rule.partition(":")
    if kind == "cwd" and value and meta.get("cwd"):
        return fnmatch.fnmatch(os.path.normcase(meta["cwd"]), os.path.normcase(value))
    if kind == "session" and value:
        return value in (meta.get("sessionId"), meta.get("cliSessionId"))
    return False


def opted_in(cfg: dict, meta: dict) -> bool:
    """True when the config is enabled, an include rule matches and no exclude rule does."""
    if cfg.get("enabled") is not True or meta.get("isArchived") is True:
        return False
    if any(_rule_matches(str(r), meta) for r in cfg["exclude"]):
        return False
    return any(_rule_matches(str(r), meta) for r in cfg["include"])


# --- transcript ---------------------------------------------------------------------


def read_tail(path: Path, max_bytes: int = TAIL_BYTES) -> list[dict]:
    """Parse the last *max_bytes* of a JSONL transcript. Unparseable lines are skipped."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read()
    except OSError:
        return []
    lines = chunk.decode("utf-8", "replace").splitlines()
    if size > max_bytes:
        lines = lines[1:]  # the first line is probably cut in half
    out = []
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _blocks(entry: dict) -> list:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def _text_of(block: dict) -> str:
    if block.get("type") == "text":
        return str(block.get("text", ""))
    if block.get("type") == "tool_result":
        c = block.get("content")
        if isinstance(c, list):
            return " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
        return str(c or "")
    return ""


def classify(entries: list[dict]) -> dict:
    """Classify how the last turn in a transcript ended.

    Returns ``{"state", "uuid", "timestamp", "detail"}``, where state is one of:
    ``interrupted`` (cut off mid-turn), ``clean`` (the reply finished),
    ``waiting_on_user`` (the turn is paused on AskUserQuestion / ExitPlanMode),
    ``user_stopped`` (the user pressed stop), ``api_error`` (the turn ended on an
    API error message) or ``empty``.

    Only main-thread ``user`` / ``assistant`` messages count; sidechain (subagent)
    entries, ``isMeta`` user entries and app bookkeeping lines (``bridge-session``,
    ``custom-title``, ``api-request``, ``attachment``, ...) are ignored. A
    ``system/stop_hook_summary`` after the last message means the Stop hooks ran, so
    the turn finished.
    """
    stop_summary_after = False
    for e in reversed(entries):
        t = e.get("type")
        if t == "system" and e.get("subtype") == "stop_hook_summary":
            stop_summary_after = True
            continue
        if t not in ("user", "assistant") or e.get("isSidechain") or e.get("isMeta"):
            continue
        res = {"uuid": e.get("uuid"), "timestamp": e.get("timestamp"), "detail": ""}
        blocks = _blocks(e)
        if stop_summary_after:
            return {**res, "state": "clean"}
        if t == "assistant":
            if e.get("isApiErrorMessage"):
                return {**res, "state": "api_error"}
            tools = [b.get("name", "") for b in blocks if b.get("type") == "tool_use"]
            if tools:
                if tools[-1] in WAITING_TOOLS:
                    return {**res, "state": "waiting_on_user", "detail": tools[-1]}
                return {**res, "state": "interrupted", "detail": f"a {tools[-1]} call that never returned"}
            stop = (e.get("message") or {}).get("stop_reason")
            if stop in ("end_turn", "stop_sequence"):
                return {**res, "state": "clean"}
            return {**res, "state": "interrupted", "detail": "a reply cut off mid-stream"}
        text = " ".join(_text_of(b) for b in blocks)
        if USER_STOP_MARKER in text:
            return {**res, "state": "user_stopped"}
        if any(b.get("type") == "tool_result" for b in blocks):
            return {**res, "state": "interrupted", "detail": "a tool result the model never answered"}
        return {**res, "state": "interrupted", "detail": "a message the model never answered"}
    return {"state": "empty", "uuid": None, "timestamp": None, "detail": ""}


def parse_ts(ts) -> float | None:
    """ISO-8601 transcript timestamp -> epoch seconds, or None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def find_transcript(cli_session_id: str, projects: Path | None = None) -> Path | None:
    if not cli_session_id or not all(c.isalnum() or c == "-" for c in cli_session_id):
        return None
    return next((projects or PROJECTS).glob(f"*/{cli_session_id}.jsonl"), None)


# --- app session metadata -----------------------------------------------------------


def session_dirs() -> list[Path]:
    """Every ``claude-code-sessions/<account>/<org>`` directory the desktop app keeps."""
    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "Claude-Data",
             Path(os.environ.get("APPDATA", "")) / "Claude"]
    found = []
    for root in roots:
        found += [p for p in root.glob("*/claude-code-sessions/*/*") if p.is_dir()]
        found += [p for p in root.glob("claude-code-sessions/*/*") if p.is_dir()]
    return found


def load_app_sessions(dirs: list[Path] | None = None) -> list[dict]:
    """Read the app's ``local_*.json`` session files, keeping only the fields used here."""
    keep = ("sessionId", "cliSessionId", "cwd", "title", "isArchived", "isStarred")
    out = []
    for d in session_dirs() if dirs is None else dirs:
        for f in d.glob("local_*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # the app may be mid-write; skipping is safe
            if isinstance(j, dict) and j.get("cliSessionId"):
                out.append({k: j.get(k) for k in keep})
    return out


def meta_for(cli_session_id: str, sessions: list[dict] | None = None) -> dict:
    for s in load_app_sessions() if sessions is None else sessions:
        if s.get("cliSessionId") == cli_session_id:
            return s
    return {"cliSessionId": cli_session_id}


# --- attempts ledger ----------------------------------------------------------------


def _load_attempts(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def attempts_left(cli_session_id: str, cfg: dict, now: float | None = None,
                  path: Path | None = None) -> bool:
    now = time.time() if now is None else now
    window = float(cfg["maxAgeHours"]) * 3600
    recent = [t for t in _load_attempts(path or ATTEMPTS_PATH).get(cli_session_id, [])
              if isinstance(t, (int, float)) and now - t < window]
    return len(recent) < int(cfg["maxAttempts"])


def record_attempt(cli_session_id: str, cfg: dict, now: float | None = None,
                   path: Path | None = None) -> None:
    path = path or ATTEMPTS_PATH
    now = time.time() if now is None else now
    window = float(cfg["maxAgeHours"]) * 3600
    data = {k: [t for t in v if isinstance(t, (int, float)) and now - t < window]
            for k, v in _load_attempts(path).items() if isinstance(v, list)}
    data.setdefault(cli_session_id, []).append(now)
    data = {k: v for k, v in data.items() if v}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


# --- logging ------------------------------------------------------------------------


def documents_dir() -> Path:
    """The real Documents folder (follows OneDrive redirection), via SHGetFolderPathW."""
    try:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


def log_dir() -> Path:
    """``Documents\\LOGS``, or ``%TEMP%\\LOGS`` when Documents is unreachable."""
    for base in (documents_dir(), Path(os.environ.get("TEMP", str(STATE_DIR)))):
        d = base / "LOGS"
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            continue
    return STATE_DIR


def log(source: str, event: str, **fields) -> None:
    """Append one JSON line to ``LOGS/claude-auto-resume_<date>.jsonl``. Never raises."""
    rec = {"at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "source": source, "event": event, **fields}
    try:
        path = log_dir() / f"claude-auto-resume_{_dt.date.today().isoformat()}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def boot_time() -> float | None:
    """Epoch seconds of the last Windows boot, or None off Windows."""
    try:
        tick = ctypes.windll.kernel32.GetTickCount64
        tick.restype = ctypes.c_uint64  # the default c_int wraps after 24.8 days
        return time.time() - tick() / 1000.0
    except AttributeError:
        return None
