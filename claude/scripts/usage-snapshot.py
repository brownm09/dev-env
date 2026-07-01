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
    "tool_response": {"stdout": "...", "stderr": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0  — not a merge command, an unconfirmed merge (no success marker in the
          output — e.g. a queued `--auto`), or creds file absent; silent
Exit 2  — snapshot emitted via stderr, OR an expired token whose on-demand refresh
          failed (advisory), OR the usage API was unreachable after one retry
          (advisory — #302). An expired token is first refreshed on demand via the
          CLI (keep-token-warm.ps1); a still-valid "expiring" token proceeds to fetch.
"""
import _winsubp  # noqa: F401  -- patches subprocess to suppress console windows
import json
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

from _hookio import output_has_merge_marker, read_command_output

CREDS_PATH = "C:/Users/brown/.claude/.credentials.json"
CONFIG_PATH = "C:/Users/brown/Git/dev-env/claude/usage-config.json"
PROJECTS_ROOT = Path("C:/Users/brown/.claude/projects")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
KEEP_WARM_PS1 = "C:/Users/brown/.claude/scripts/keep-token-warm.ps1"

# --- merge detection (command-shape scan; success confirmed via _hookio's
# output marker -- mirrors post-pr-merge-project.py) ---
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


def merge_confirmed(command: str, output: str) -> bool:
    """Return True iff *command* is a top-level `gh pr merge` whose *output*
    confirms a completed merge via gh's success marker.

    Gated on the marker, not the exit code: a worktree merge exits non-zero on
    local branch cleanup ("'main' is already checked out") even though the
    remote merge succeeded (issue #275) -- the marker prints before that
    cleanup tail runs. Trusting the exit code here (as this hook did before)
    silently dropped the snapshot on every worktree merge, the default flow in
    this repo (dev-env#474; mirrors post-pr-merge-project.py's
    merge_succeeded(), see ADR-049/ADR-050).
    """
    return _scan_top_level(command) and output_has_merge_marker(output)


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


def classify_token(expires_at_ms: int, now_ms: float) -> tuple[str, str]:
    """Classify the stored OAuth token by expiry, returning (state, advisory).

    state is one of:
      "no_expiry" — no expiry recorded (expires_at_ms falsy); proceed silently.
      "ok"        — token valid with > 1h remaining; proceed silently.
      "expiring"  — token valid but expires within 1 hour; advisory, do not proceed.
      "expired"   — token already expired; advisory, do not proceed.

    For "no_expiry"/"ok" the advisory is "". For "expiring"/"expired" the advisory
    is a one-line message intended for stderr. Mirrors the visibility of the
    expiring-within-1h path so an already-expired token no longer fails silently.
    """
    if not expires_at_ms:
        return "no_expiry", ""
    if expires_at_ms <= now_ms:
        days_ago = (now_ms - expires_at_ms) / 86_400_000
        return "expired", (
            f"[usage-snapshot] Skipped: OAuth token in .credentials.json expired "
            f"{days_ago:.1f} days ago. This client isn't refreshing that file — run "
            f"`claude` interactively once to rewrite it."
        )
    if (expires_at_ms - now_ms) < 3_600_000:
        return "expiring", (
            "[usage-snapshot] OAuth token expires within 1 hour — open Claude Code "
            "interactively to refresh before the next check."
        )
    return "ok", ""


def snapshot_action(state: str) -> str:
    """Map a token state to the snapshot action (pure; unit-testable offline).

    "expired"                       -> "refresh"  (try an on-demand refresh, then re-check)
    "ok" / "expiring" / "no_expiry" -> "fetch"    (token is still valid; use it)

    An "expiring" token (<1h left) is still valid, so it now proceeds to the fetch
    instead of being skipped — only a truly-expired token triggers a refresh.
    """
    return "refresh" if state == "expired" else "fetch"


def refresh_token_now(timeout: int = 45) -> bool:
    """Best-effort on-demand OAuth-token refresh via the CLI.

    Delegates to keep-token-warm.ps1 (the same script the ClaudeKeepTokenWarm
    scheduled task runs), which invokes the Claude CLI — the CLI owns the token
    refresh and the credential-file write, so this carries none of the raw-OAuth
    rotation risk that gated dev-env#356. The .ps1 gets a shorter internal timeout
    so it kills its own claude child cleanly; this subprocess timeout is the outer
    backstop, kept under Claude Code's ~60s hook budget.

    Returns True if the refresh ran to completion. The caller confirms a token was
    actually obtained by re-reading and re-classifying the credentials.
    """
    try:
        subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", KEEP_WARM_PS1,
                "-TimeoutSeconds", "35",
            ],
            timeout=timeout,
            capture_output=True,
        )
        return True
    except Exception:
        return False


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

    command = data.get("tool_input", {}).get("command", "")
    output = read_command_output(data)
    if not merge_confirmed(command, output):
        sys.exit(0)

    creds = load_credentials()
    if not creds:
        sys.exit(0)

    token, expires_at_ms = get_access_token(creds)
    if not token:
        # Creds file exists but holds no usable token — surface it rather than
        # failing silently (creds-file-absent is handled silently above).
        print(
            "[usage-snapshot] Skipped: .credentials.json has no usable OAuth token "
            "(missing or unparseable). Run `claude` interactively to rewrite it.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Only a truly-expired token blocks the snapshot. Claude Code refreshes lazily,
    # so a scheduled keep-warm can't fully close the gap; instead, refresh on demand
    # right here. A still-valid "expiring" token (<1h) proceeds to the fetch rather
    # than being discarded.
    state, _ = classify_token(expires_at_ms, time.time() * 1000)
    if snapshot_action(state) == "refresh":
        if refresh_token_now():
            creds = load_credentials() or creds
            token, expires_at_ms = get_access_token(creds)
            state, _ = classify_token(expires_at_ms, time.time() * 1000)
        if state == "expired" or not token:
            print(
                "[usage-snapshot] OAuth token expired and on-demand refresh failed "
                "(the refresh token may be dead) — run `claude` interactively to re-auth.",
                file=sys.stderr,
            )
            sys.exit(2)

    util_data = fetch_usage(token)
    if not util_data:
        # Retry once — transient network blips often self-heal
        time.sleep(1)
        util_data = fetch_usage(token)
    if not util_data:
        print(
            "[usage-snapshot] Skipped: usage API unavailable (network error or "
            "transient 5xx). The snapshot was omitted for this merge.",
            file=sys.stderr,
        )
        sys.exit(2)

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
