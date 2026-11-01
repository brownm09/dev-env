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
this repo. A missing, unreadable or invalid file, or ``"enabled": false``, means nothing
is ever resumed (fail closed).

The app's own session files (``%LOCALAPPDATA%\\Claude-Data\\<package>\\
claude-code-sessions\\<account>\\<org>\\local_*.json``, the MSIX install's layout) are
read-only inputs here and are never written. Only ``sessionId``, ``cliSessionId``,
``cwd``, ``title``, ``isArchived`` and ``isStarred`` (the sidebar pin) are read. A
session with no readable file there is not a desktop session as far as this module is
concerned, and is never opted in. That covers terminal and headless resumes, and keeps
exclude rules and the archived check from being skipped while the app is writing the
file.
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
ATTEMPTS_DIR = STATE_DIR / "attempts"

DEFAULTS = {
    "enabled": False,
    # Rules, any of which opts a session in: "all", "pinned", "cwd:<glob>",
    # "session:<local_... or cliSessionId>". `exclude` uses the same syntax and wins.
    "include": [],
    "exclude": [],
    # A turn cut off longer ago than this is left alone.
    "maxAgeHours": 12,
    # The hook waits this long before waking the model, then re-reads the transcript.
    # Any new turn activity in the meantime (the user typed, or a queued message ran)
    # cancels. Capped well below the hook's 180 s timeout in settings.shared.json.
    "settleSeconds": 20,
    # At most this many wakes per session within maxAgeHours, so a turn that keeps
    # crashing is not resumed forever.
    "maxAttempts": 2,
}
_LIMITS = {"maxAgeHours": (0.1, 168), "settleSeconds": (5, 120), "maxAttempts": (1, 10)}

# A turn whose last step is one of these is waiting on the user, not interrupted.
WAITING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
USER_STOP_MARKER = "[Request interrupted by user"
# User entries a local slash command (/compact, /model, ...) writes. No reply follows them.
LOCAL_COMMAND_PREFIXES = ("<command-name>", "<command-message>", "<local-command-stdout>",
                          "<local-command-stderr>")
# Transcript lines the app and CLI write that are not turn activity. Any other new line
# during the hook's settle wait counts as activity and cancels the wake.
BOOKKEEPING_TYPES = {"bridge-session", "custom-title", "agent-name", "atis-latch", "mode",
                     "last-prompt", "pr-link", "summary", "tag", "file-history-snapshot"}
TAIL_SIZES = (512 * 1024, 4 * 1024 * 1024, 32 * 1024 * 1024)


# --- config -------------------------------------------------------------------------


def _valid_rule(rule) -> bool:
    if rule in ("all", "pinned"):
        return True
    kind, _, value = str(rule).partition(":")
    return kind in ("cwd", "session") and bool(value)


def load_config(path: Path | None = None) -> dict:
    """Return the config merged over DEFAULTS.

    Missing file -> disabled. A file that can't be parsed, an unknown rule, or a number
    outside its limits -> disabled, with the reason in ``cfg["invalid"]`` for the caller
    to log.
    """
    cfg = dict(DEFAULTS)
    p = path or CONFIG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as e:
        return {**cfg, "invalid": f"{p} could not be read: {e}"}
    if not isinstance(raw, dict):
        return {**cfg, "invalid": f"{p} is not a JSON object"}
    cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    problems = []
    for key in ("include", "exclude"):
        rules = cfg[key]
        if not isinstance(rules, list) or not all(_valid_rule(r) for r in rules):
            problems.append(f"{key} must be a list of all | pinned | cwd:<glob> | session:<id>")
    for key, (lo, hi) in _LIMITS.items():
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            problems.append(f"{key} must be a number from {lo} to {hi}")
    if problems:
        return {**cfg, "enabled": False, "invalid": f"{p}: " + "; ".join(problems)}
    return cfg


def _rule_matches(rule: str, meta: dict) -> bool:
    if rule == "all":
        return True
    if rule == "pinned":
        return meta.get("isStarred") is True
    kind, _, value = rule.partition(":")
    if kind == "cwd" and meta.get("cwd"):
        return fnmatch.fnmatch(os.path.normcase(meta["cwd"]), os.path.normcase(value))
    if kind == "session":
        return value in (meta.get("sessionId"), meta.get("cliSessionId"))
    return False


def opted_in(cfg: dict, meta: dict) -> bool:
    """True for an enabled config, a desktop session (app metadata present), not archived,
    an include rule matching and no exclude rule matching."""
    if cfg.get("enabled") is not True or not str(meta.get("sessionId") or "").startswith("local_"):
        return False
    if meta.get("isArchived") is not False:
        return False
    if any(_rule_matches(r, meta) for r in cfg["exclude"]):
        return False
    return any(_rule_matches(r, meta) for r in cfg["include"])


# --- transcript ---------------------------------------------------------------------


def _parse_lines(chunk: bytes, cut_first: bool) -> list[dict]:
    lines = chunk.decode("utf-8", "replace").splitlines()
    if cut_first:
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


def read_tail(path: Path) -> list[dict]:
    """Parse the end of a JSONL transcript.

    The window grows until it holds at least one whole message line, so a huge last line
    (a screenshot tool_result, say) can't hide the interruption.
    """
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            for n in TAIL_SIZES:
                f.seek(max(0, size - n))
                entries = _parse_lines(f.read(), cut_first=size > n)
                if size <= n or any(e.get("type") in ("user", "assistant") for e in entries):
                    return entries
            return entries
    except OSError:
        return []


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


def _ignored(e: dict) -> bool:
    if e.get("isSidechain") or e.get("isMeta") or e.get("isCompactSummary") \
            or e.get("isVisibleInTranscriptOnly"):
        return True
    if e.get("type") == "user":
        text = " ".join(_text_of(b) for b in _blocks(e)).lstrip()
        return text.startswith(LOCAL_COMMAND_PREFIXES)
    return False


def classify(entries: list[dict]) -> dict:
    """Classify how the last turn in a transcript ended.

    Returns ``{"state", "uuid", "timestamp", "detail"}``, where state is one of:
    ``interrupted`` (cut off mid-turn), ``clean`` (the reply finished),
    ``waiting_on_user`` (the turn is paused on AskUserQuestion / ExitPlanMode),
    ``user_stopped`` (the user pressed stop), ``api_error`` (the turn ended on an
    API error message) or ``empty``.

    Only main-thread ``user`` / ``assistant`` messages count. These are ignored:
    sidechain (subagent) entries; ``isMeta``, compact-summary and transcript-only
    entries; the user entries a local slash command writes (``/compact``, ...); and app
    bookkeeping lines. A ``system`` ``stop_hook_summary`` or ``compact_boundary`` after
    the last message means the turn finished.
    """
    finished_after = False
    for e in reversed(entries):
        t = e.get("type")
        if t == "system" and e.get("subtype") in ("stop_hook_summary", "compact_boundary"):
            finished_after = True
            continue
        if t not in ("user", "assistant") or _ignored(e):
            continue
        res = {"uuid": e.get("uuid"), "timestamp": e.get("timestamp"), "detail": ""}
        blocks = _blocks(e)
        if finished_after:
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


def activity_marker(entries: list[dict]):
    """Identity of the last line that is not app bookkeeping. If it changes, a turn moved."""
    for e in reversed(entries):
        if e.get("type") not in BOOKKEEPING_TYPES:
            return (e.get("type"), e.get("subtype"), e.get("uuid"), e.get("timestamp"))
    return None


def parse_ts(ts) -> float | None:
    """ISO-8601 transcript timestamp -> epoch seconds, or None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _safe_id(cli_session_id: str) -> bool:
    return bool(cli_session_id) and all(c.isalnum() or c == "-" for c in cli_session_id)


def find_transcript(cli_session_id: str, projects: Path | None = None) -> Path | None:
    if not _safe_id(cli_session_id):
        return None
    return next((projects or PROJECTS).glob(f"*/{cli_session_id}.jsonl"), None)


# --- app session metadata -----------------------------------------------------------


def load_app_sessions() -> list[dict]:
    """Read the app's ``local_*.json`` session files, keeping only the fields used here."""
    keep = ("sessionId", "cliSessionId", "cwd", "title", "isArchived", "isStarred")
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "Claude-Data"
    out = []
    for f in root.glob("*/claude-code-sessions/*/*/local_*.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # the app may be mid-write; that session is skipped this time
        if isinstance(j, dict) and j.get("cliSessionId"):
            out.append({k: j.get(k) for k in keep})
    return out


def meta_for(cli_session_id: str, tries: int = 3, delay: float = 2.0) -> dict:
    """App metadata for a CLI session id, or ``{}`` when no readable file names it.

    Retries briefly, since the app may be rewriting the file just as the session opens.
    """
    for i in range(tries):
        for s in load_app_sessions():
            if s.get("cliSessionId") == cli_session_id:
                return s
        if i + 1 < tries:
            time.sleep(delay)
    return {}


# --- state files --------------------------------------------------------------------


def write_json_atomic(path: Path, data) -> None:
    """Write via a temp file and ``os.replace``, retrying briefly if a reader holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    for i in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.2)


def read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _attempts(cli_session_id: str, cfg: dict, now: float, root: Path) -> list[float]:
    window = float(cfg["maxAgeHours"]) * 3600
    times = read_json(root / f"{cli_session_id}.json").get("wakes", [])
    return [t for t in times if isinstance(t, (int, float)) and now - t < window]


def attempts_left(cli_session_id: str, cfg: dict, now: float | None = None,
                  root: Path | None = None) -> bool:
    """One file per session, so concurrent hooks for different sessions never collide."""
    now = time.time() if now is None else now
    return len(_attempts(cli_session_id, cfg, now, root or ATTEMPTS_DIR)) < int(cfg["maxAttempts"])


def record_attempt(cli_session_id: str, cfg: dict, now: float | None = None,
                   root: Path | None = None) -> None:
    now = time.time() if now is None else now
    root = root or ATTEMPTS_DIR
    if _safe_id(cli_session_id):
        times = _attempts(cli_session_id, cfg, now, root) + [now]
        write_json_atomic(root / f"{cli_session_id}.json", {"wakes": times})


# --- logging ------------------------------------------------------------------------


def documents_dir() -> Path:
    """The real Documents folder (follows OneDrive redirection), via SHGetFolderPathW."""
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
        return Path(buf.value)
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


def boot_time() -> float:
    """Epoch seconds of the last Windows boot."""
    tick = ctypes.windll.kernel32.GetTickCount64
    tick.restype = ctypes.c_uint64  # the default c_int wraps after 24.8 days
    return time.time() - tick() / 1000.0
