#!/usr/bin/env python3
"""Claude Code PostToolUse hook — fires after 'gh pr merge' to emit a usage
snapshot: weekly/5-hour utilization vs. daily soft targets, plus top-5
costliest exchanges from the current session JSONL.

Global (not project-scoped) — fires for every repo without a hook-config.json
opt-in requirement.

Credentials:   C:/Users/brown/.claude/.credentials.json
Config:        C:/Users/brown/Git/dev-env/claude/usage-config.json
JSONL root:    C:/Users/brown/.claude/projects/

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"output": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0  — not a merge command, token missing/unparseable, or API error; silent
Exit 2  — snapshot emitted via stderr, OR token expires within 1 hour (advisory warning)
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

CREDS_PATH = "C:/Users/brown/.claude/.credentials.json"
CONFIG_PATH = "C:/Users/brown/Git/dev-env/claude/usage-config.json"
PROJECTS_ROOT = Path("C:/Users/brown/.claude/projects")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"

# --- merge detection (mirrors post-pr-merge-project.py) ---
_MERGE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b")


def _check_merge_stmt(token: str) -> bool:
    return bool(_MERGE_RE.match(token.lstrip()))


def _find_heredoc_end(cmd: str, start: int) -> int:
    n = len(cmd)
    i = start + 2
    strip_tabs = False
    if i < n and cmd[i] == "-":
        strip_tabs = True
        i += 1
    quote: str | None = None
    if i < n and cmd[i] in ("'", '"'):
        quote = cmd[i]
        i += 1
    stop_chars = "\n\r" + (quote or "")
    delim_start = i
    while i < n and cmd[i] not in stop_chars:
        i += 1
    delimiter = cmd[delim_start:i]
    if quote and i < n and cmd[i] == quote:
        i += 1
    while i < n and cmd[i] not in ("\n", "\r"):
        i += 1
    if i < n:
        i += 1
    while i < n:
        line_start = i
        if strip_tabs:
            while i < n and cmd[i] == "\t":
                i += 1
            line_start = i
        while i < n and cmd[i] not in ("\n", "\r"):
            i += 1
        if cmd[line_start:i] == delimiter:
            if i < n:
                i += 1
            return i
        if i < n:
            i += 1
    return i


def _scan_top_level(command: str) -> bool:
    """Return True when command contains a top-level `gh pr merge` statement."""
    n = len(command)
    i = 0
    stmt_start = 0
    stack = ["top"]
    while i < n:
        c = command[i]
        state = stack[-1]
        if state == "single":
            if c == "'":
                stack.pop()
        elif state == "double":
            if c == "\\" and i + 1 < n:
                i += 1
            elif c == '"':
                stack.pop()
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1
        elif state == "subshell":
            if c == ")":
                stack.pop()
            elif c == "'":
                stack.append("single")
            elif c == '"':
                stack.append("double")
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1
            elif c == "(":
                stack.append("subshell")
            elif c == "<" and i + 1 < n and command[i + 1] == "<":
                i = _find_heredoc_end(command, i)
                continue
        else:  # top
            if c == "'":
                stack.append("single")
            elif c == '"':
                stack.append("double")
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1
            elif c == "<" and i + 1 < n and command[i + 1] == "<":
                i = _find_heredoc_end(command, i)
                continue
            elif c in (";", "\n"):
                if _check_merge_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 1
            elif c == "&" and i + 1 < n and command[i + 1] == "&":
                if _check_merge_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 2
                i += 1
            elif c == "|" and i + 1 < n and command[i + 1] == "|":
                if _check_merge_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 2
                i += 1
        i += 1
    if stack == ["top"]:
        return _check_merge_stmt(command[stmt_start:])
    return False


# --- credentials ---

def load_credentials() -> dict | None:
    try:
        return json.loads(Path(CREDS_PATH).read_text(encoding="utf-8"))
    except Exception:
        return None


def get_access_token(creds: dict) -> tuple[str | None, int]:
    """Return (access_token, expires_at_ms) or (None, 0) on failure."""
    try:
        oauth = creds["claudeAiOauth"]
        return oauth["accessToken"], int(oauth.get("expiresAt", 0))
    except (KeyError, TypeError, ValueError):
        return None, 0


# --- usage API ---

def fetch_usage(token: str) -> dict | None:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA_HEADER,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError):
        return None


# --- threshold math ---

def load_config() -> dict:
    try:
        return json.loads(Path(CONFIG_PATH).read_text(encoding="utf-8"))
    except Exception:
        return {"weekday_pct": 12, "weekend_pct": 20, "alert_approaching_margin": 5}


def compute_cumulative_target(resets_at_str: str, config: dict) -> tuple[int, int, int]:
    """Return (cumulative_target_pct, day_number_1indexed, days_in_window).

    cumulative_target_pct: expected utilisation % by end of today.
    day_number: 1-based day index in the window (1 = first day).
    """
    try:
        resets_at = datetime.fromisoformat(resets_at_str.replace("Z", "+00:00"))
        window_start = resets_at - timedelta(days=7)
        now = datetime.now(timezone.utc)
        days_elapsed = max(0, (now - window_start).days)  # 0 = first day

        weekday_pct = config.get("weekday_pct", 12)
        weekend_pct = config.get("weekend_pct", 20)

        cumulative = 0
        for d in range(days_elapsed + 1):
            day_date = window_start + timedelta(days=d)
            cumulative += weekend_pct if day_date.weekday() >= 5 else weekday_pct

        return cumulative, days_elapsed + 1, 7
    except Exception:
        return 0, 0, 7


def status_emoji(util: float, target: int, margin: int) -> str:
    if target == 0:
        return ""
    if util >= target:
        return "🔴 over cap"
    if util >= target - margin:
        return "⚠️ approaching cap"
    return "✅ under cap"


# --- JSONL parsing ---

def encode_cwd(cwd: str) -> str:
    """Encode a project path the way Claude Code encodes it for the projects/ dir."""
    encoded = cwd.replace("\\", "/").replace(":", "").replace("/", "--")
    return encoded.lstrip("-")


def find_session_jsonl(cwd: str, session_id: str) -> Path | None:
    """Find the session JSONL by session_id.

    Strategy:
    1. Try the session-specific file in the encoded-cwd project dir.
    2. If cwd is a worktree path (contains /.claude/worktrees/), strip that
       suffix and retry with the canonical repo path.
    3. Fall back to searching all project dirs for <session_id>.jsonl.
    """
    def _find_in_dir(project_dir: Path) -> Path | None:
        if not project_dir.exists():
            return None
        candidate = project_dir / f"{session_id}.jsonl"
        return candidate if candidate.exists() else None

    # 1. Try direct encoded path
    encoded = encode_cwd(cwd)
    result = _find_in_dir(PROJECTS_ROOT / encoded)
    if result:
        return result

    # 2. Strip worktree suffix if present
    worktree_marker = "/.claude/worktrees/"
    norm = cwd.replace("\\", "/")
    if worktree_marker in norm:
        canonical = norm.split(worktree_marker)[0]
        encoded_canonical = encode_cwd(canonical)
        result = _find_in_dir(PROJECTS_ROOT / encoded_canonical)
        if result:
            return result

    # 3. Search all project dirs for the session-specific file
    if session_id:
        for project_dir in PROJECTS_ROOT.iterdir():
            if project_dir.is_dir():
                candidate = project_dir / f"{session_id}.jsonl"
                if candidate.exists():
                    return candidate

    return None


def describe_content(content: list) -> str:
    """Return a short description of what the assistant turn did."""
    if not content:
        return "response"
    first = content[0] if isinstance(content[0], dict) else {}
    ctype = first.get("type", "")
    if ctype == "tool_use":
        return f"tool:{first.get('name', '?')}"
    if ctype == "text":
        text = first.get("text", "")
        return text[:40].replace("\n", " ").strip() or "text"
    if ctype == "thinking":
        # Try second element for the actual action
        if len(content) > 1 and isinstance(content[1], dict):
            ctype2 = content[1].get("type", "")
            if ctype2 == "tool_use":
                return f"tool:{content[1].get('name', '?')}"
    return ctype or "response"


def top_exchanges(jsonl_path: Path, n: int = 5) -> list[dict]:
    """Parse JSONL and return top-N assistant turns by total token count."""
    seen_request_ids: set[str] = set()
    exchanges: list[dict] = []

    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") != "assistant":
                    continue

                request_id = obj.get("requestId", "")
                if request_id and request_id in seen_request_ids:
                    continue
                if request_id:
                    seen_request_ids.add(request_id)

                msg = obj.get("message", {})
                usage = msg.get("usage")
                if not usage:
                    continue

                total = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + usage.get("output_tokens", 0)
                )
                content = msg.get("content", [])
                exchanges.append(
                    {
                        "total": total,
                        "input": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
                        "cache_write": usage.get("cache_creation_input_tokens", 0),
                        "output": usage.get("output_tokens", 0),
                        "action": describe_content(content),
                    }
                )
    except Exception:
        pass

    exchanges.sort(key=lambda x: x["total"], reverse=True)
    return exchanges[:n]


# --- formatting ---

def format_snapshot(util_data: dict, config: dict, exchanges: list[dict]) -> str:
    seven_day = util_data.get("seven_day") or {}
    five_hour = util_data.get("five_hour") or {}
    extra = util_data.get("extra_usage") or {}

    util_7d = seven_day.get("utilization", 0)
    util_5h = five_hour.get("utilization", 0)
    resets_at_str = seven_day.get("resets_at", "")
    margin = config.get("alert_approaching_margin", 5)

    target, day_num, window_days = compute_cumulative_target(resets_at_str, config)
    emoji = status_emoji(util_7d, target, margin)

    resets_display = ""
    if resets_at_str:
        try:
            resets_dt = datetime.fromisoformat(resets_at_str.replace("Z", "+00:00"))
            resets_display = resets_dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            resets_display = resets_at_str

    lines = ["### Usage Snapshot (post-merge)"]
    lines.append(
        f"- **Weekly:** {util_7d:.0f}% used "
        f"(day {day_num}/{window_days} — target ≤{target}% — {emoji})"
    )
    lines.append(f"- **5-hour window:** {util_5h:.0f}%")

    if resets_display:
        lines.append(f"- **Weekly window resets:** {resets_display}")

    if extra.get("is_enabled"):
        used = extra.get("used_credits", 0)
        limit = extra.get("monthly_limit", 0)
        util_extra = extra.get("utilization", 0)
        lines.append(
            f"- **Extra usage:** ${used:.2f} / ${limit:.2f} ({util_extra:.1f}%)"
        )

    if exchanges:
        lines.append("")
        lines.append("**Top exchanges this session (by token count):**")
        lines.append("| Action | Input+Cache_R | Cache_W | Output | Total |")
        lines.append("|---|---|---|---|---|")
        for ex in exchanges:
            lines.append(
                f"| {ex['action']} | {ex['input']:,} | {ex['cache_write']:,} | {ex['output']:,} | {ex['total']:,} |"
            )
    else:
        lines.append("")
        lines.append("*(No session JSONL found — exchange breakdown unavailable)*")

    return "\n".join(lines)


# --- main ---

def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    if data.get("tool_response", {}).get("exitCode", 0) != 0:
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not _scan_top_level(command):
        sys.exit(0)

    creds = load_credentials()
    if not creds:
        sys.exit(0)

    token, expires_at_ms = get_access_token(creds)
    if not token:
        sys.exit(0)

    # Skip silently if token is already expired; warn if it expires within 1 hour
    now_ms = time.time() * 1000
    if expires_at_ms and expires_at_ms <= now_ms:
        sys.exit(0)
    if expires_at_ms and (expires_at_ms - now_ms) < 3_600_000:
        print(
            "[usage-snapshot] OAuth token expires within 1 hour — open Claude Code "
            "interactively to refresh before the next check.",
            file=sys.stderr,
        )
        sys.exit(2)

    util_data = fetch_usage(token)
    if not util_data:
        # Silently skip — don't block session on API failure
        sys.exit(0)

    config = load_config()
    session_id = data.get("session_id", "")
    cwd = data.get("cwd", "")

    jsonl_path = find_session_jsonl(cwd, session_id)
    exchanges = top_exchanges(jsonl_path) if jsonl_path else []

    snapshot = format_snapshot(util_data, config, exchanges)
    print(snapshot, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
