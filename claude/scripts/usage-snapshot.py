#!/usr/bin/env python3
"""Claude Code PostToolUse hook — fires after 'gh pr merge' to emit a usage
snapshot: weekly/5-hour utilization vs. daily soft targets, plus top-5
costliest exchanges from the current session JSONL.

Global (not project-scoped) — fires for every repo without a hook-config.json
opt-in requirement.

Credentials:   C:/Users/brown/.claude/.credentials.json
Config:        C:/Users/brown/Git/dev-env/claude/usage-config.json
JSONL root:    C:/Users/brown/.claude/projects/

Also fires for the PowerShell tool (dev-env#763): registered under both the
Bash and PowerShell PostToolUse matchers in settings.json, since PowerShell is
an equally sanctioned way to run `gh pr merge` in this environment.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",  # or "PowerShell"
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0  — not a merge command, an unconfirmed merge (no success marker and the
          live `gh pr view` fallback also found nothing — e.g. a queued
          `--auto`, or a genuinely failed merge), or creds file absent; silent
Exit 2  — snapshot emitted via stderr, OR a missing/unparseable token whose
          on-demand refresh didn't produce one (advisory — dev-env#819), OR an
          expired token whose on-demand refresh failed (advisory), OR the usage
          API was unreachable after one retry (advisory — #302). Both a
          missing/unparseable token and an expired one are first refreshed on
          demand via the CLI (keep-token-warm.ps1); a still-valid "expiring"
          token proceeds to fetch. Exception: when a CLI *subprocess* is itself
          unauthenticated (the MSIX desktop-app configuration — OAuth lives in the
          OS keychain, no readable .credentials.json exists, so both the refresh
          and an interactive re-auth are futile), the missing-token branch detects
          it via a `claude auth status` probe and emits an accurate advisory naming
          dev-env#915 *without* the doomed ~35s refresh (ADR-124).

Merge-decision trace (dev-env#474 follow-up): dev-env#489/#496 both established
live that gh's success marker does not reliably survive to `tool_response` on a
worktree-merge exit-1 (a suspected gh-side stdout buffering/flush race, not a
bug in this hook's own regex), which is why the `confirm_merge_via_gh` live
fallback exists. But two later live reproductions (dev-env#474 comment thread,
PR #954 on 2026-08-07 and PR #988 on 2026-08-16) both saw NO snapshot appear,
and neither prior investigation captured which branch of the fallback logic
actually ran at that exact moment — both required a human to be present,
instrumented, at the moment of a real worktree-merge failure. `resolve_merge()`
now makes every branch of that decision (marker matched, REST marker matched,
not a merge shape, `--help`-only, no-confirm-needed, `gh pr view` confirmed,
`gh pr view` unconfirmed) an explicit, single return value that `main()` both
acts on and appends to a small best-effort JSONL trace
(`C:/Users/brown/.claude/scratch/usage-snapshot-merge-trace.log`) for every
merge-shaped command, confirmed or not — so the next occurrence has a permanent
record instead of requiring another live-instrumented reproduction. The trace
write can never affect control flow (wrapped, exceptions swallowed), matching
this hook's existing safe-exit-guard contract.

A third occurrence (dev-env#1028, 2026-08-20, career-playbook PR #1356) hit the
identical "no snapshot" symptom with the trace mechanism above already in
place — and the trace log had ZERO entries for the invocation, not merely an
unhelpful one. That's a narrower failure than anything `resolve_merge()` was
built to diagnose: it means the crash happened *before* `resolve_merge()` was
ever reached. `main()`'s own `command`/`exit_code` extraction, two lines
before that call, used an unguarded inline `.get(x, {}).get(...)` chain that
throws on a present-but-non-dict `tool_input`/`tool_response` — silently
caught by the outermost safe-exit guard, with nothing written anywhere. Fixed
by routing both reads through `_hookio.read_command`/`read_exit_code` (which
extend `read_command_output`'s pre-existing "never raises" contract to these
two fields), plus a defense-in-depth `try/except` around the `resolve_merge()`
call itself with a new `reason: "classify_error"` trace entry — so a
merge-shaped command can no longer vanish from the trace no matter which layer
of this pipeline throws.

`/review` on that fix's own PR then found, by executing it against its own
diagnosed payload, that the crash-prevention above still produced zero trace
entries: a destroyed `command` is trivially `not_merge_shape`, so the ordinary
trace-write guard never fires. `main()` now detects a malformed `tool_input`
directly (`reason: "malformed_payload"`), using any merge marker still surviving
in `output` as an independent confirmation signal since `tool_response` is
intact in that case; `cwd` gained the identical `read_cwd()` hardening (its
pre-fix crash landed *after* a `confirmed: true` trace entry, actively asserting
a merge that produced no snapshot); the `classify_error` entry now records the
exception itself; and a non-dict top-level payload is guarded before it can
reach `data.get("tool_name")`. See ADR-050 Amendment 26 (including its
"Post-review fix" section) for the full detail.
"""
import _winsubp  # noqa: F401  -- patches subprocess to suppress console windows
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

import _hookout
from _hookio import (
    confirm_merge_via_gh,
    effective_merge_dir,
    is_merge_help_only,
    is_rest_merge_command,
    output_has_merge_marker,
    output_has_rest_merge_marker,
    read_command,
    read_command_output,
    read_cwd,
    read_exit_code,
    scan_top_level,
    should_confirm_via_gh,
)
import _hookutil
from _worktree_canon import canonical_root_from_worktree

CREDS_PATH = "C:/Users/brown/.claude/.credentials.json"
CONFIG_PATH = "C:/Users/brown/Git/dev-env/claude/usage-config.json"
PROJECTS_ROOT = Path("C:/Users/brown/.claude/projects")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
KEEP_WARM_PS1 = "C:/Users/brown/.claude/scripts/keep-token-warm.ps1"
MERGE_TRACE_PATH = "C:/Users/brown/.claude/scratch/usage-snapshot-merge-trace.log"
MERGE_TRACE_MAX_LINES = 500

# --- merge detection (command-shape scan; success confirmed via _hookio's
# output marker — mirrors post-pr-merge-project.py) ---
_MERGE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b")


def _check_merge_stmt(token: str) -> bool:
    return bool(_MERGE_RE.match(token.lstrip()))


def merge_confirmed(command: str, output: str) -> bool:
    """Return True iff *command* is a top-level `gh pr merge` whose *output*
    confirms a completed merge via gh's success marker.

    Gated on the marker, not the exit code: a worktree merge exits non-zero on
    local branch cleanup ("'main' is already checked out") even though the
    remote merge succeeded (issue #275) — the marker prints before that
    cleanup tail runs. Trusting the exit code here (as this hook did before)
    silently dropped the snapshot on every worktree merge, the default flow in
    this repo (dev-env#474; mirrors post-pr-merge-project.py's
    merge_succeeded(), see ADR-049/ADR-050).

    gh's already-printed marker does not always survive to this hook's
    captured output when gh exits abruptly right after that same
    local-cleanup failure (dev-env#489) — main() falls back to a live
    `gh pr view` confirmation when this predicate returns False but the
    command was still a `gh pr merge` invocation (dev-env#504).

    Also recognizes the two-step REST merge fallback (`gh api -X PUT
    .../pulls/<N>/merge`, dev-env#986): a `gh pr merge` outage (e.g. a GitHub
    GraphQL rate-limit exhaustion) has a documented REST-only merge path that
    bypasses `gh pr merge` entirely, so it never prints gh's own success
    marker and needs its own success signal (the REST response body's
    `"merged":true`).
    """
    if scan_top_level(command, _check_merge_stmt) and output_has_merge_marker(output):
        return True
    return is_rest_merge_command(command) and output_has_rest_merge_marker(output)


def resolve_merge(
    command: str,
    output: str,
    exit_code: int,
    cwd: str,
    confirm_fn=confirm_merge_via_gh,
) -> dict:
    """Resolve whether *command* is a confirmed merge, and by which decision path.

    Returns a dict with:
      "is_merge_shaped": bool -- command matches a `gh pr merge` or REST-merge
                                  shape at all (worth tracing even when unconfirmed)
      "confirmed": bool       -- True iff main() should proceed to emit a snapshot
      "reason": str           -- one of "marker", "rest_marker", "not_merge_shape",
                                  "help_only", "no_confirm_needed", "gh_view_confirmed",
                                  "gh_view_unconfirmed"

    "marker" vs "rest_marker" is a best-effort re-classification for the trace, not a
    strict decomposition of merge_confirmed()'s own `or` -- in the practically-never-seen
    case where a single command's output satisfies BOTH the `gh pr merge` marker and the
    REST "merged":true marker at once, this reports "rest_marker" even if
    merge_confirmed()'s short-circuit actually matched via the `gh pr merge` branch first.
    Not worth branching on (two distinct merge mechanisms succeeding in one Bash call is
    unrealistic), but the trace's `reason` field should be read as "which shape look
    confirmed", not "which internal branch fired", in that edge case.

    Mirrors main()'s pre-existing branch order exactly (marker check, then
    `gh pr merge`-shape check, then `--help`-only guard, then the
    `should_confirm_via_gh` cost gate, then the live `gh pr view` fallback) so
    this is a pure refactor of that control flow, not a behavior change -- see
    test_resolve_merge_* below and test_merge_confirmed_* above (unchanged) for
    the shared fixtures. `confirm_fn` is dependency-injected (mirroring
    `attempt_token_refresh`'s I/O-fake pattern) so the live-network branch is
    testable offline.

    Exists so `main()` can trace every branch of this decision as one explicit
    value instead of the decision being implicit in which of several
    `sys.exit(0)` call sites fired -- see this module's own docstring for why
    (dev-env#474's still-open forensic question).
    """
    if merge_confirmed(command, output):
        is_rest = is_rest_merge_command(command) and output_has_rest_merge_marker(output)
        return {"is_merge_shaped": True, "confirmed": True, "reason": "rest_marker" if is_rest else "marker"}

    if not scan_top_level(command, _check_merge_stmt):
        # Not a `gh pr merge` shape at all -- still worth tracing when it IS an
        # (unconfirmed) REST-merge shape, since that's a distinct, informative
        # outcome ("REST call ran but printed no merged:true").
        return {
            "is_merge_shaped": is_rest_merge_command(command),
            "confirmed": False,
            "reason": "not_merge_shape",
        }

    # `gh pr merge --help` (or any other non-mutating gh pr merge invocation
    # that prints no marker) can categorically never attempt a real merge —
    # treat it exactly like "not a merge command at all" rather than paying
    # a live gh pr view confirmation that resolves against cwd's current
    # branch and can misattribute an unrelated already-merged PR (dev-env#557).
    if is_merge_help_only(command):
        return {"is_merge_shaped": True, "confirmed": False, "reason": "help_only"}

    if not should_confirm_via_gh(exit_code, output):
        return {"is_merge_shaped": True, "confirmed": False, "reason": "no_confirm_needed"}

    # No PR number to extract here: merge_confirmed() already ruled out "not a
    # merge command" above, so its False result means the marker itself is
    # missing from `output` — and merge_pr_number_from_output() scans that same
    # `output` for the identical marker regex, so it would always return None
    # too. `gh pr view` with no number infers the PR from cwd's checked-out
    # branch instead (matching the other five hooks' identical fallback call).
    confirmed_pr = confirm_fn(None, "", effective_merge_dir(command, cwd))
    reason = "gh_view_confirmed" if confirmed_pr is not None else "gh_view_unconfirmed"
    return {"is_merge_shaped": True, "confirmed": confirmed_pr is not None, "reason": reason}


def _log_merge_trace(entry: dict, path: str = MERGE_TRACE_PATH, max_lines: int = MERGE_TRACE_MAX_LINES) -> None:
    """Best-effort append of one merge-decision trace line, capped to the most
    recent *max_lines* entries. Never raises.

    Merges are infrequent, so an uncapped append (mirroring session-mode-prompt.py's
    own uncapped `_log`) would take years to matter in practice -- but this trace
    exists specifically so a future occurrence of dev-env#474's question can be
    answered from history, so a deliberate cap (rather than accepting unbounded
    growth) keeps that history bounded without ever needing a separate cleanup pass.
    500 lines is generous for a low-frequency event while keeping the read-modify-write
    below cheap. An observability aid must never become a new way to break the hook --
    any I/O failure here (including an unreadable pre-existing file) is swallowed.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        lines.append(json.dumps(entry, sort_keys=True))
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


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


# --- CLI auth probe (dev-env#915) ---
#
# Under the MSIX Claude desktop app the bundled CLI, run as a *subprocess* (this
# hook's and keep-token-warm.ps1's context), is unauthenticated: OAuth lives in the
# OS keychain and is injected in-process to child sessions, and no readable
# .credentials.json is ever written. So a blank/missing token there can be neither
# read nor refreshed (keep-token-warm.ps1 invokes that same unauthenticated CLI),
# and "run claude interactively" cannot help. `claude auth status --json` reporting
# loggedIn:false is the precise signature; on it we skip the ~35s refresh and emit
# an accurate advisory instead. On any other install (npm CLI with a real token
# file) the packaged .exe is absent, the probe returns None, and the legacy
# refresh-then-advise path runs unchanged.

# MSIX package family for the Claude desktop app; mirrors keep-token-warm.ps1's
# Resolve-ClaudeExe so both resolve the same bundled binary.
_MSIX_CLAUDE_CODE_REL = "Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/claude-code"


def resolve_claude_exe() -> str | None:
    """Path to the newest packaged claude.exe, or None if the MSIX layout is absent.

    Returns the real .exe (not the ~/bin PATH shim) so subprocess can exec it
    directly without the .cmd/PATHEXT indirection. Deliberately no PATH fallback:
    this probe exists only to detect the desktop-app dead-end (dev-env#915); on an
    npm-CLI install the packaged .exe is absent and None routes the caller to the
    legacy refresh path.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    base = Path(local) / _MSIX_CLAUDE_CODE_REL
    try:
        exes = list(base.glob("*/claude.exe"))
    except OSError:
        return None
    if not exes:
        return None

    def _ver(p: Path) -> tuple:
        try:
            return tuple(int(x) for x in p.parent.name.split("."))
        except ValueError:
            return (0,)

    return str(max(exes, key=_ver))


def parse_auth_status(stdout: str) -> str | None:
    """Classify `claude auth status --json` output (pure; offline-testable).

    "in"   -> loggedIn is exactly True,
    "out"  -> loggedIn is exactly False (the desktop-app dead-end signature),
    None   -> unparseable, not an object, or loggedIn missing / non-boolean -- never
              treat malformed output as a dead-end (that would wrongly skip a
              snapshot the legacy refresh path might still recover).
    """
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    logged_in = data.get("loggedIn")
    if logged_in is True:
        return "in"
    if logged_in is False:
        return "out"
    return None


def cli_auth_status(timeout: int = 8, exe_fn=resolve_claude_exe, run_fn=None) -> str | None:
    """Whether a CLI *subprocess* here can authenticate (dev-env#915).

    "out" is the MSIX desktop-app signature (loggedIn:false as a subprocess) — the
    file read and keep-token-warm.ps1's refresh are both permanently futile. "in"
    means a refresh can plausibly help (npm-CLI world). None means unknown -> the
    caller keeps its legacy behavior. exe_fn/run_fn are dependency-injected for
    offline testing, mirroring attempt_token_refresh's fake-injection pattern.
    """
    exe = exe_fn()
    if not exe:
        return None
    runner = run_fn or subprocess.run
    try:
        proc = runner(
            [exe, "auth", "status", "--json"],
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return parse_auth_status(getattr(proc, "stdout", "") or "")


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


def attempt_token_refresh(
    creds: dict,
    token: str | None,
    expires_at_ms: int,
    refresh_fn=refresh_token_now,
    load_fn=load_credentials,
    get_fn=get_access_token,
) -> tuple[str | None, int, dict]:
    """Best-effort on-demand refresh + re-read, shared by both token-recovery
    branches in main() (a missing/unparseable token, and an expired one).

    Returns the possibly-updated (token, expires_at_ms, creds). If refresh_fn()
    itself reports failure, the inputs are returned unchanged — there is nothing
    to re-read. If it reports success but a subsequent load_fn() can't produce a
    creds dict (e.g. the file is briefly unreadable mid-write), creds falls back
    to the caller's original value and the token is re-extracted from that
    (effectively a no-op, matching prior behavior).

    refresh_fn/load_fn/get_fn are dependency-injected (defaulting to the real
    subprocess/file-I/O functions above) so the retry sequence itself is
    testable offline — mirrors this repo's existing pattern for testing an
    I/O-wrapping decision via an injected fake (e.g. post-tool-use.py's
    fetch_live_required_field_options()).
    """
    if refresh_fn():
        creds = load_fn() or creds
        token, expires_at_ms = get_fn(creds)
    return token, expires_at_ms, creds


def status_label(util: float, target: int, margin: int) -> str:
    # ASCII tokens (not emoji) so the snapshot stays cp1252-encodable on the raw
    # stderr channel: emoji are outside cp1252 and crashed the print, flipping
    # exit 2 -> 0 and silently dropping the whole snapshot (PR5 of dev-env#717).
    if target == 0:
        return ""
    if util >= target:
        return "OVER cap"
    if util >= target - margin:
        return "NEAR cap"
    return "OK under cap"


# --- JSONL parsing ---

def encode_cwd(cwd: str) -> str:
    """Encode a project path the way Claude Code encodes it for the projects/ dir."""
    encoded = cwd.replace("\\", "/").replace(":", "").replace("/", "--")
    return encoded.lstrip("-")


def find_session_jsonl(cwd: str, session_id: str) -> Path | None:
    """Find the session JSONL by session_id.

    Strategy:
    1. Try the session-specific file in the encoded-cwd project dir.
    2. If cwd is a worktree path (either the nested `.claude/worktrees/<name>`
       convention or the sibling `<repo>-worktrees/<name>` convention,
       dev-env#760), resolve the canonical repo root via the shared
       _worktree_canon resolver (the same one post-tool-use.py uses) and
       retry there.
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

    # 2. Resolve a worktree cwd to its canonical repo root and retry
    canonical = canonical_root_from_worktree(cwd)
    if canonical:
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
    label = status_label(util_7d, target, margin)

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
        f"(day {day_num}/{window_days} - target <={target}% - {label})"
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
        lines.append("*(No session JSONL found - exchange breakdown unavailable)*")

    return "\n".join(lines)


# --- main ---


_PLAUSIBLE_MERGE_RE = re.compile(r"\bgh\b.*\bpr\b.*\bmerge\b|/pulls/\d+/merge\b", re.IGNORECASE)


def _plausibly_merge_shaped(command: str) -> bool:
    """Cheap, bounded-substring merge-shape check for the `classify_error`
    fallback below (dev-env#1028) -- not the real (and more fragile)
    `scan_top_level`/`_MERGE_RE` machinery, since that may be exactly what
    just raised. Word-bounded (dev-env#1028 post-review finding): an
    unbounded substring test would match "gh"/"pr" inside an unrelated
    command's ordinary words (e.g. `git merge origin/print-highlights`
    contains both "gh" and "pr" as substrings, though neither is a real
    invocation) -- and unlike the harmless false positives this function's
    permissiveness is meant to tolerate, `_log_merge_trace` is a 500-line
    ring buffer this hook writes to on every Bash/PowerShell call, so an
    unbounded flood can evict a genuine merge entry the log exists to
    preserve, in exactly the failure-correlated scenario (resolve_merge()
    throwing on a common command shape) this fallback exists for. A false
    negative here (an unusual REST-merge textual variant this bounded regex
    doesn't recognize) just means that one classify_error case goes
    untraced -- the accepted cost of this being a last-resort diagnostic
    aid, not the primary detection path (`is_rest_merge_command` already
    handles those variants correctly there). Relies on `read_command`'s
    "always str" contract -- no defensive try/except beyond that, matching
    every other caller of this module's `read_*` helpers.
    """
    return bool(_PLAUSIBLE_MERGE_RE.search(command))


def main() -> None:
    _hookutil.record_heartbeat("usage-snapshot")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        # A valid-JSON-but-non-dict top-level payload (a list, string,
        # number, or null) would otherwise crash the very next line
        # (dev-env#1028 post-review finding). Nothing to trace: without a
        # dict there is no tool_name to even confirm this was a
        # Bash/PowerShell PostToolUse event in the first place.
        sys.exit(0)

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    command = read_command(data)
    output = read_command_output(data)
    cwd = read_cwd(data)
    exit_code = read_exit_code(data, default=-1)

    # dev-env#1028 post-review finding (independently confirmed, by execution,
    # by both review passes): read_command()'s crash-prevention alone still
    # produced ZERO trace entries for a present-but-non-dict tool_input --
    # the exact symptom this whole fix exists to close. A destroyed `command`
    # is "" (never merge-shaped: scan_top_level("") is always False), so
    # resolve_merge("") always classifies not_merge_shape/is_merge_shaped=
    # False, and the trace-write guard below never fires. Detect the
    # malformed-input condition directly, before ever calling resolve_merge(),
    # and trace it under its own reason -- using the merge marker in `output`
    # (still reliable: tool_response, unlike tool_input, is intact here) as
    # the only surviving independent signal of whether a merge happened.
    raw_tool_input = data.get("tool_input")
    if "tool_input" in data and not isinstance(raw_tool_input, dict):
        confirmed = output_has_merge_marker(output) or output_has_rest_merge_marker(output)
        _log_merge_trace(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "cwd": cwd,
                "exit_code": exit_code,
                "is_merge_shaped": True,
                "confirmed": confirmed,
                "reason": "malformed_payload",
            }
        )
        if not confirmed:
            sys.exit(0)
        # else: `output` carries independent merge confirmation despite the
        # lost command text -- fall through to the snapshot logic below
        # exactly like any other confirmed merge. resolve_merge() is skipped
        # entirely: `command` no longer carries usable information for it.
    else:
        try:
            resolution = resolve_merge(command, output, exit_code, cwd)
        except Exception as exc:
            # Defense-in-depth (dev-env#1028): a genuine gh-pr-merge-shaped
            # command must never vanish from the trace just because something
            # deeper in the classification pipeline (e.g. split_top_level's
            # heredoc/here-string parser, or the live gh pr view confirm
            # call) throws on a shape the test suite hasn't seen yet.
            # `classify_error` is synthesized here, in main() -- it is never
            # a value resolve_merge() itself returns, so it is not part of
            # that function's own reason enum (see its docstring). The
            # exception itself is recorded (dev-env#1028 post-review
            # finding) so a bare "classify_error" doesn't force yet another
            # live-instrumented reproduction to learn which layer threw.
            if _plausibly_merge_shaped(command):
                _log_merge_trace(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cwd": cwd,
                        "exit_code": exit_code,
                        "is_merge_shaped": True,
                        "confirmed": False,
                        "reason": "classify_error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            sys.exit(0)

        if resolution["is_merge_shaped"]:
            _log_merge_trace(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "cwd": cwd,
                    "exit_code": exit_code,
                    **resolution,
                }
            )
        if not resolution["confirmed"]:
            sys.exit(0)

    creds = load_credentials()
    if not creds:
        sys.exit(0)

    token, expires_at_ms = get_access_token(creds)
    if not token:
        # Creds file exists but holds no usable token (missing/malformed oauth
        # substructure) — surface it rather than failing silently (creds-file-absent
        # is handled silently above).
        #
        # Before paying for a ~35s keep-warm refresh (and before advising an
        # interactive re-auth), check whether a CLI *subprocess* can even
        # authenticate here. Under the MSIX Claude desktop app it cannot: OAuth is in
        # the OS keychain, no readable .credentials.json is ever written, so neither
        # this read nor keep-token-warm.ps1's refresh can produce one and setup-token
        # is 403 at the usage endpoint (dev-env#915, ADR-043/044/124). `claude auth
        # status` reporting loggedIn:false is that exact signature — skip straight to
        # an accurate advisory rather than a doomed refresh. emit_block() exits(2).
        if cli_auth_status() == "out":
            _hookout.emit_block(
                "[usage-snapshot] Skipped: the Claude desktop app keeps OAuth in the "
                "OS keychain, so no readable token file exists and a CLI refresh "
                "cannot create one (dev-env#915). Post-merge usage snapshots are "
                "unavailable in this configuration."
            )
        # Otherwise (npm-CLI world, or an unknown probe result): attempt an on-demand
        # refresh, mirroring the expired-token branch below — the CLI's refresh path
        # can repair a locally corrupted/missing token field, not just refresh an
        # already-valid one (dev-env#819).
        token, expires_at_ms, creds = attempt_token_refresh(creds, token, expires_at_ms)
        if not token:
            _hookout.emit_block(
                "[usage-snapshot] Skipped: .credentials.json has no usable OAuth "
                "token (missing or unparseable), and an on-demand refresh did not "
                "produce one. Run `claude` interactively to rewrite it."
            )

    # Only a truly-expired token blocks the snapshot. Claude Code refreshes lazily,
    # so a scheduled keep-warm can't fully close the gap; instead, refresh on demand
    # right here. A still-valid "expiring" token (<1h) proceeds to the fetch rather
    # than being discarded.
    #
    # This can be the *second* attempt_token_refresh() call in this invocation (the
    # branch above already tried once for a missing/malformed token) if that refresh
    # left a token that's present but still expired -- refresh_token_now() reporting
    # success only means the subprocess didn't throw, not that the CLI actually
    # refreshed anything (confirmed live: dev-env#825). Deliberately not deduplicated:
    # the trigger is narrow (a token that flips from unusable to present-but-expired
    # between the two checks), each attempt is bounded by refresh_token_now()'s own
    # ~45s subprocess timeout, and the hook's configured 90s settings.json timeout
    # (dev-env CLAUDE.md Testing item 63) is an acceptable backstop for this rare case
    # rather than adding cross-branch state to force a single attempt.
    state, _ = classify_token(expires_at_ms, time.time() * 1000)
    if snapshot_action(state) == "refresh":
        token, expires_at_ms, creds = attempt_token_refresh(creds, token, expires_at_ms)
        state, _ = classify_token(expires_at_ms, time.time() * 1000)
        if state == "expired" or not token:
            _hookout.emit_block(
                "[usage-snapshot] OAuth token expired and on-demand refresh failed "
                "(the refresh token may be dead) - run `claude` interactively to re-auth."
            )

    util_data = fetch_usage(token)
    if not util_data:
        # Retry once — transient network blips often self-heal
        time.sleep(1)
        util_data = fetch_usage(token)
    if not util_data:
        _hookout.emit_block(
            "[usage-snapshot] Skipped: usage API unavailable (network error or "
            "transient 5xx). The snapshot was omitted for this merge."
        )

    config = load_config()
    session_id = data.get("session_id", "")

    jsonl_path = find_session_jsonl(cwd, session_id)
    exchanges = top_exchanges(jsonl_path) if jsonl_path else []

    snapshot = format_snapshot(util_data, config, exchanges)
    _hookout.emit_block(snapshot)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
