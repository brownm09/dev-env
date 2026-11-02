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
import re
import subprocess
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
    # At most this many wakes per session per Claude app instance (so per boot too), so
    # a session that crashes again on every reopen is not resumed forever, while a new
    # crash later in the day still gets its own tries (dev-env#1185).
    "maxAttempts": 2,
}
_LIMITS = {"maxAgeHours": (0.1, 168), "settleSeconds": (5, 120), "maxAttempts": (1, 10)}

# States the hook and launcher act on. `waiting_on_background`: the turn finished, but a
# background task it started never reported back. After a restart that task is dead.
RESUMABLE = {"interrupted", "waiting_on_background"}
# A turn whose last step is one of these is waiting on the user, not interrupted.
WAITING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
# A resumed CLI writes one of these task-notifications for each background task the
# previous process left unfinished, and that notification starts a turn on its own.
STOP_NOTICE = "didn't finish before the previous session ended"
USER_STOP_MARKER = "[Request interrupted by user"
# User entries a local slash command (/compact, /model, ...) writes. No reply follows them.
LOCAL_COMMAND_PREFIXES = ("<command-name>", "<command-message>", "<local-command-stdout>",
                          "<local-command-stderr>")
# Transcript lines the app and CLI write that are not turn activity. Any other new line
# during the hook's settle wait counts as activity and cancels the wake.
BOOKKEEPING_TYPES = {"bridge-session", "custom-title", "agent-name", "atis-latch", "mode",
                     "last-prompt", "pr-link", "summary", "tag", "file-history-snapshot",
                     "file-history-delta"}
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


def is_synthetic(e: dict) -> bool:
    """A placeholder reply Claude Code writes itself (model ``<synthetic>``), such as the
    "No response requested." it adds when a resumed transcript ends on a user message.
    API-error messages are synthetic too, but keep their own ``api_error`` state."""
    return e.get("type") == "assistant" and not e.get("isApiErrorMessage") \
        and (e.get("message") or {}).get("model") == "<synthetic>"


def _ignored(e: dict) -> bool:
    if e.get("isSidechain") or e.get("isMeta") or e.get("isCompactSummary") \
            or e.get("isVisibleInTranscriptOnly") or is_synthetic(e):
        return True
    if e.get("type") == "user":
        text = " ".join(_text_of(b) for b in _blocks(e)).lstrip()
        return text.startswith(LOCAL_COMMAND_PREFIXES)
    return False


def is_stop_notice(e: dict) -> bool:
    """A task-notification a resumed CLI wrote for a background task that died with the
    previous process."""
    if e.get("type") != "user":
        return False
    text = " ".join(_text_of(b) for b in _blocks(e))
    return "<task-notification>" in text and STOP_NOTICE in text


def trim_stop_notices(entries: list[dict]) -> tuple[list[dict], bool]:
    """Drop a trailing run of CLI stop notices and the one reply turn they set off.

    Returns ``(entries, trimmed)``. The run is trimmed only when nothing else happened
    after the first notice: no other user entry (a prompt, a queued message, a wake from
    the auto-resume hook itself), no tool call, and at most one finished turn. That turn
    is the model answering the notice, often with just "No response requested." Without
    the trim, that turn would hide the interruption it followed (dev-env#1185).
    """
    first = None
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        if is_stop_notice(e):
            first = i
            continue
        if e.get("isSidechain"):
            continue
        if e.get("type") == "user":
            break
        if e.get("type") == "assistant" and any(b.get("type") == "tool_use" for b in _blocks(e)):
            break
    if first is None:
        return entries, False
    tail = entries[first:]
    turns = sum(1 for e in tail if e.get("type") == "system" and e.get("subtype") == "stop_hook_summary")
    if any(e.get("type") == "user" and not is_stop_notice(e) and not e.get("isSidechain") for e in tail) \
            or turns > 1:
        return entries, False
    return entries[:first], True


_BACKGROUND_KINDS = {"Bash": "Bash", "PowerShell": "PowerShell", "Agent": "agent", "Task": "agent"}


MONITOR_DEFAULT_TIMEOUT_MS = 600_000


def pending_background(entries: list[dict], restarted_at: float | None) -> list[str]:
    """Descriptions of background tasks launched on the main thread that never reported
    back: Bash/PowerShell or Agent with ``run_in_background``, and Monitor.

    A notification is a user entry when it arrives between turns. It is a
    ``queued_command`` attachment (and a ``queue-operation``) when it arrives mid-turn,
    so every non-assistant line is searched. A stop notice still present here also
    counts as done: trailing ones were already set aside (``trim_stop_notices``), so the
    session has dealt with any that remain.

    A Monitor can end on its ``timeout_ms`` without leaving a mark, so it counts only
    when its deadline falls after *restarted_at* (the restart must have killed it). With
    no *restarted_at*, Monitors are not counted.
    """
    launched, done = {}, set()
    for e in entries:
        if e.get("isSidechain"):
            continue
        if e.get("type") == "assistant":
            for b in _blocks(e):
                if b.get("type") != "tool_use" or not b.get("id"):
                    continue
                inp = b.get("input") or {}
                if b.get("name") == "Monitor":
                    start = parse_ts(e.get("timestamp"))
                    timeout = inp.get("timeout_ms") or MONITOR_DEFAULT_TIMEOUT_MS
                    if start is None or restarted_at is None or not isinstance(timeout, (int, float)) \
                            or start + timeout / 1000 <= restarted_at:
                        continue
                    kind = "Monitor"
                else:
                    kind = _BACKGROUND_KINDS.get(b.get("name")) if inp.get("run_in_background") else None
                if kind:
                    desc = str(inp.get("description") or inp.get("command") or "")[:80]
                    launched[b["id"]] = f"background {kind} task" + (f" ({desc})" if desc else "")
        elif launched:
            raw = json.dumps(e)
            if "<task-notification>" in raw:
                done.update(i for i in launched if i in raw)
    return [d for i, d in launched.items() if i not in done]


def classify(entries: list[dict], restarted_at: float | None = None) -> dict:
    """Classify how the last turn in a transcript ended.

    *restarted_at* is when the CLI that owns the transcript was (re)started: the hook's
    own start, or the app instance's start for the launcher. Only Monitor detection
    needs it; see ``pending_background``.

    Returns ``{"state", "uuid", "timestamp", "detail", "stopNotice"}``, where state is
    one of: ``interrupted`` (cut off mid-turn), ``waiting_on_background`` (the turn
    finished but a background task it started never reported back), ``clean`` (the
    reply finished), ``waiting_on_user`` (the turn is paused on AskUserQuestion /
    ExitPlanMode), ``user_stopped`` (the user pressed stop), ``api_error`` (the turn
    ended on an API error message) or ``empty``. ``stopNotice`` is True when a trailing
    CLI stop notice (and its reply) was set aside first (``trim_stop_notices``).

    Only main-thread ``user`` / ``assistant`` messages count. These are ignored:
    sidechain (subagent) entries; ``isMeta``, compact-summary and transcript-only
    entries; the user entries a local slash command writes (``/compact``, ...); and app
    bookkeeping lines. A ``system`` ``stop_hook_summary`` or ``compact_boundary`` after
    the last message means the turn finished.
    """
    entries, trimmed = trim_stop_notices(entries)
    v = _classify(entries)
    if v["state"] == "clean":
        pending = pending_background(entries, restarted_at)
        if pending:
            v = {**v, "state": "waiting_on_background", "detail": "; ".join(pending)}
    return {**v, "stopNotice": trimmed}


def _classify(entries: list[dict]) -> dict:
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


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_since(path: Path, offset: int) -> list[dict]:
    """Parse the lines appended to a transcript after byte *offset*."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return _parse_lines(f.read(), cut_first=False)
    except OSError:
        return []


def turn_started(new: list[dict], stop_notice: bool) -> bool:
    """Did a turn start in the lines written during the hook's settle wait?

    A user entry that isn't a CLI stop notice always counts: a prompt, a queued or
    cross-session message. Otherwise, any line that isn't app bookkeeping counts, except
    after a stop notice. That turn is the CLI's own, answering the notice, and it is
    exactly the turn that would otherwise hide the interruption.
    """
    stop_notice = stop_notice or any(is_stop_notice(e) for e in new)
    for e in new:
        if e.get("type") == "user" and not is_stop_notice(e) and not e.get("isSidechain"):
            return True
        if not stop_notice and e.get("type") not in BOOKKEEPING_TYPES and not is_synthetic(e):
            return True
    return False


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


# --- processes ----------------------------------------------------------------------

_RESUME_ARG = re.compile(r"--(?:resume|session-id)[= ]\"?([0-9a-fA-F-]{36})")
_APP_EXE = re.compile(r"\\windowsapps\\claude_[^\\]+\\app\\claude\.exe$", re.IGNORECASE)
_PS = (
    "Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\" -ErrorAction Stop | "
    "ForEach-Object { [pscustomobject]@{ path = $_.ExecutablePath; cmd = $_.CommandLine; "
    "created = $_.CreationDate.ToUniversalTime().ToString('o') } } | ConvertTo-Json -Compress"
)


def list_processes() -> list[dict]:
    """Every process named claude.exe (case-insensitive: the app and its CLI children).

    Raises when the query itself fails, so a failure is never read as "not running".
    """
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS],
        capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if r.returncode != 0:
        raise RuntimeError("process query failed: " + r.stderr.decode("utf-8", "replace").strip()[:300])
    out = r.stdout.decode("utf-8", "replace").strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def app_start(procs: list[dict]) -> float | None:
    """Start time of the app's main process, identified by its MSIX install path."""
    starts = [parse_ts(p.get("created")) for p in procs
              if _APP_EXE.search(p.get("path") or "") and "--type=" not in (p.get("cmd") or "")]
    starts = [s for s in starts if s is not None]
    return min(starts) if starts else None


def running_cli_ids(procs: list[dict]) -> set[str]:
    """Session ids any claude.exe is running, from its ``--resume`` / ``--session-id``."""
    ids = set()
    for p in procs:
        ids.update(m.lower() for m in _RESUME_ARG.findall(p.get("cmd") or ""))
    return ids


def instance_start() -> float:
    """Start of the current app instance, or the last boot if the app can't be found."""
    try:
        started = app_start(list_processes())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        started = None
    return started if started is not None else boot_time()


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


def attempts_left(cli_session_id: str, cfg: dict, since: float, now: float | None = None,
                  root: Path | None = None) -> bool:
    """Fewer than ``maxAttempts`` wakes since *since*, the current app instance's start.

    One file per session, so concurrent hooks for different sessions never collide.
    """
    now = time.time() if now is None else now
    recent = [t for t in _attempts(cli_session_id, cfg, now, root or ATTEMPTS_DIR) if t >= since]
    return len(recent) < int(cfg["maxAttempts"])


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
