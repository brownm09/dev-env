#!/usr/bin/env python3
"""Claude Code Stop hook — reliable-event safety net for inert PostToolUse hooks.

In sessions launched as background tasks / via `spawn_task` (SDK-driven), *all*
PostToolUse settings hooks are silently inert — no project-board add after
`gh issue create`/`gh pr create`, no Done-move / usage snapshot after
`gh pr merge` — while UserPromptSubmit, PreToolUse, and Stop hooks from the same
settings file still fire. This is an upstream Claude Code Desktop limitation that
no PostToolUse hook-code change can fix because the hooks never dispatch
(ADR-053, dev-env #381; anthropics/claude-code#42336, #53494).

This Stop hook converts that *silent* gap into a *visible* one. It scans the
just-ended session's transcript and, when a board-relevant `gh` command ran but
**no** PostToolUse hook fired all session, emits a one-line advisory pointing to
the documented manual fallback. It never blocks (stdout, exit 0).

Detection (no `gh` calls, no `project` scope — purely the transcript the Stop
payload points at):

  inert  ==  a dev-env (project #3) board action is present in the transcript
             AND zero transcript `attachment` records carry
             `attachment.hookEvent == "PostToolUse"`.

The harness writes an `attachment` record whenever a hook produces output; a
PostToolUse hook that ran emits either a `hook_success` (exit 0) or a
`hook_blocking_error` (exit 2, e.g. post-tool-use.py) attachment, and both carry
`hookEvent == "PostToolUse"`. Zero such attachments alongside a board action that
*would* have produced one in a healthy session is the inert signature.

False-positive guards:
  * Only **high-confidence dev-env actions** trigger: a `gh issue/pr create`
    whose output carries a `github.com/brownm09/dev-env/(issues|pull)/N` URL (a
    guaranteed post-tool-use.py exit-2 in a healthy session), or a `gh pr merge`
    for a dev-env PR with no hard-merge-failure in its output.
  * Because **any** PostToolUse attachment all session ⇒ stay silent, the
    legitimate different-repo / no-config silent-skip paths (ADR-049) can never
    trip it: in a healthy session at least one PostToolUse hook leaves a record.
  * gh's "Squashed and merged" success marker is *not* preserved in the
    transcript (it goes to stderr), so merge detection keys off the command +
    dev-env PR scope + absence of a hard-merge-failure, treating the issue-#275
    worktree cleanup-failure tail as a successful merge.

Fires at most once per session; once it has advised or confirmed the session is
healthy (any PostToolUse attachment present), a scratch sentinel short-circuits the
transcript re-scan on later Stops. Advisory only — exit 0 always.

Stdin JSON shape (Stop):
  {"session_id": "uuid", "transcript_path": "/abs/path/to/session.jsonl", ...}

See ADR-053 (the inert-PostToolUse limitation) and ADR-054 (this safety net).
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import sys
import time
from pathlib import Path

SCRATCH = Path.home() / ".claude" / "scratch"
PROJECTS = Path.home() / ".claude" / "projects"
SENTINEL_PREFIX = "posttooluse-inert-resolved-"
MAX_AGE_DAYS = 30

# --- dev-env (project #3) scoping ----------------------------------------------
# A board action only counts when it is unambiguously dev-env's, so the advisory
# can never fire for a create/merge in some other (or unconfigured) repo whose
# PostToolUse silence is legitimate.
_ISSUE_CREATE_RE = re.compile(r"\bgh\s+issue\s+create\b")
_PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")
_MERGE_RE = re.compile(r"\bgh\s+pr\s+merge\b")
_DEVENV_CREATE_URL_RE = re.compile(
    r"https://github\.com/brownm09/dev-env/(issues|pull)/(\d+)"
)
_DEVENV_PR_URL_RE = re.compile(r"https://github\.com/brownm09/dev-env/pull/(\d+)")
DEVENV_REPO = "brownm09/dev-env"
# The argument span of the `gh pr merge` invocation only (up to the next shell
# separator) -- so a `/pull/N` URL in an unrelated flag value or a chained sibling
# command cannot hijack the dev-env scoping or the PR-number extraction. Mirrors
# post-pr-merge-project.py's _MERGE_ARGS_RE.
_MERGE_ARGS_RE = re.compile(r"\bgh\s+pr\s+merge\b([^\n;|&]*)")
# A bare positional PR-number token (`42`) within those args; a digit run inside a
# URL (`/pull/42`) or a flag value (`--foo=12`) is not a standalone token.
_MERGE_POS_NUM_RE = re.compile(r"(?<!\S)(\d+)(?=\s|$)")
# A queued `--auto` only *enables* auto-merge -- it is not a completed merge, and
# even a healthy session would not Done-move it yet (cf. post-pr-merge-project.py).
_AUTO_FLAG_RE = re.compile(r"(?<!\S)--auto(?:=\S+)?(?=\s|$)")
# An explicit `--repo owner/name` on the merge invocation overrides the cwd scope.
_REPO_FLAG_RE = re.compile(r"--repo[=\s]+(\S+)")
# A genuine merge *failure* (not the harmless issue-#275 worktree cleanup tail,
# which means the PR merged but the local branch could not be deleted).
_HARD_MERGE_FAIL_RE = re.compile(
    r"not mergeable|merge conflict|failed to merge|GraphQL:.*mergeable",
    re.IGNORECASE,
)
# cwd inside the dev-env repo (canonical `.../Git/dev-env`, a Claude-managed
# worktree under it, or a sibling worktree `dev-env-<suffix>`).
_DEVENV_CWD_RE = re.compile(r"[/\\]dev-env(?:-[\w.-]+)?(?=[/\\]|$)", re.IGNORECASE)


# --- pure helpers (offline-testable) -------------------------------------------

def posttooluse_attachment_present(records: list[dict]) -> bool:
    """True iff any transcript `attachment` record is from a PostToolUse hook.

    Matches both exit-0 (`hook_success`) and exit-2 (`hook_blocking_error`)
    attachments via `attachment.hookEvent == "PostToolUse"`. Presence of any such
    record proves PostToolUse dispatch worked this session, so the gap (if any) is
    legitimate, not the inert limitation — the advisory must stay silent.
    """
    for rec in records:
        if rec.get("type") != "attachment":
            continue
        att = rec.get("attachment")
        if isinstance(att, dict) and att.get("hookEvent") == "PostToolUse":
            return True
    return False


def _result_text(item: dict, record: dict) -> str:
    """Best-available text of a tool_result: the per-id content the model saw,
    falling back to the record's structured `toolUseResult` (stdout+stderr)."""
    c = item.get("content")
    if isinstance(c, str) and c.strip():
        return c
    if isinstance(c, list):
        joined = "\n".join(
            x.get("text", "")
            for x in c
            if isinstance(x, dict) and x.get("type") == "text"
        )
        if joined.strip():
            return joined
    tur = record.get("toolUseResult")
    if isinstance(tur, dict):
        parts = [p for p in (tur.get("stdout"), tur.get("stderr")) if p]
        if parts:
            return "\n".join(parts)
        out = tur.get("output")
        if out:
            return str(out)
    return ""


def iter_bash_calls(records: list[dict]) -> list[tuple[str, str, str]]:
    """Pair each Bash tool_use with its tool_result by `tool_use_id`.

    Returns (command, output, cwd) tuples. Pairing by id (not adjacency) keeps
    parallel tool calls from mismatching. `cwd` is taken from the assistant
    record that issued the command.
    """
    commands: dict[str, tuple[str, str]] = {}
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        cwd = rec.get("cwd", "") or ""
        msg = rec.get("message") or {}
        for item in msg.get("content") or []:
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "Bash"
            ):
                tid = item.get("id")
                if tid:
                    commands[tid] = ((item.get("input") or {}).get("command", ""), cwd)

    calls: list[tuple[str, str, str]] = []
    for rec in records:
        if rec.get("type") != "user":
            continue
        msg = rec.get("message") or {}
        for item in msg.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                tid = item.get("tool_use_id")
                if tid in commands:
                    command, cwd = commands[tid]
                    calls.append((command, _result_text(item, rec), cwd))
    return calls


def _is_devenv_cwd(cwd: str) -> bool:
    return bool(_DEVENV_CWD_RE.search(cwd or ""))


def _devenv_merge_pr(command: str, cwd: str) -> str | None:
    """Return the dev-env PR number a `gh pr merge` invocation targets, else None.

    Scoped to the merge invocation's own argument span (`_MERGE_ARGS_RE`) so a
    `/pull/N` URL in an unrelated flag value or a chained sibling command can't
    hijack it. Returns None for a queued `--auto` (not a completed merge) or an
    explicit non-dev-env `--repo`. Repo identity: an explicit `--repo` wins, else a
    dev-env `/pull/N` URL self-identifies, else a bare positional number is dev-env
    only from a dev-env cwd. PR number: positional token preferred, then the URL.
    """
    am = _MERGE_ARGS_RE.search(command)
    if not am:
        return None
    args = am.group(1)
    if _AUTO_FLAG_RE.search(args):
        return None

    repo_m = _REPO_FLAG_RE.search(args)
    url_m = _DEVENV_PR_URL_RE.search(args)
    if repo_m:
        is_devenv = repo_m.group(1) == DEVENV_REPO
    elif url_m:
        is_devenv = True  # a dev-env /pull/N URL names the repo itself
    else:
        is_devenv = _is_devenv_cwd(cwd)
    if not is_devenv:
        return None

    num_m = _MERGE_POS_NUM_RE.search(args)
    if num_m:
        return num_m.group(1)
    return url_m.group(1) if url_m else None


def detect_board_actions(calls: list[tuple[str, str, str]]) -> list[dict]:
    """Detect high-confidence dev-env board actions among paired Bash calls.

    A *create* counts when the command is `gh issue/pr create` and its output
    carries a dev-env issue/PR URL (a successful create → board add expected).
    A *merge* counts when the command is a completed `gh pr merge` naming a dev-env
    PR (see `_devenv_merge_pr`: not a queued `--auto`, not another `--repo`) and the
    output shows no hard merge failure (Done-move + usage snapshot expected).
    """
    actions: list[dict] = []
    for command, output, cwd in calls:
        output = output or ""
        if _ISSUE_CREATE_RE.search(command) or _PR_CREATE_RE.search(command):
            m = _DEVENV_CREATE_URL_RE.search(output)
            if m:
                kind = "issue" if m.group(1) == "issues" else "PR"
                actions.append({"action": "create", "label": f"{kind} {m.group(0)}"})
        if _MERGE_RE.search(command):
            pr = _devenv_merge_pr(command, cwd)
            if pr and not _HARD_MERGE_FAIL_RE.search(output):
                actions.append({"action": "merge", "label": f"PR #{pr}"})
    return actions


def should_emit(records: list[dict]) -> list[dict] | None:
    """Return detected dev-env board actions when PostToolUse was inert this
    session, else None. The presence of *any* PostToolUse attachment short-circuits
    to None (dispatch worked → no advisory)."""
    if posttooluse_attachment_present(records):
        return None
    actions = detect_board_actions(iter_bash_calls(records))
    return actions or None


def format_advisory(actions: list[dict]) -> str:
    # ASCII-only: Claude Code pipes hook stdout as cp1252, so a char outside it
    # (e.g. an arrow or em-dash) would raise UnicodeEncodeError and the whole
    # advisory would vanish through main()'s exit-0 guard — the exact silent
    # failure this hook exists to surface.
    labels = "\n".join(f"    - {a['action']}: {a['label']}" for a in actions)
    return (
        "[posttooluse-inert] PostToolUse hooks did not fire this session "
        "(background/SDK-launched - ADR-053); GitHub Project #3 automation was "
        "silently skipped.\n"
        "  Board-relevant gh command(s) ran with no PostToolUse side-effect:\n"
        f"{labels}\n"
        "  Likely missing: board add (create) / Done-move + usage snapshot (merge).\n"
        "  Apply the manual fallback: dev-env CLAUDE.md -> 'GitHub Project' section "
        "-> Fallback (gh project item-add / item-edit). Advisory only; nothing was blocked."
    )


# --- I/O (thin, untested per the pure-helper convention) -----------------------

def cleanup_stale_sentinels() -> None:
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        for f in SCRATCH.glob(f"{SENTINEL_PREFIX}*.flag"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


def sentinel_path(session_id: str) -> Path:
    return SCRATCH / f"{SENTINEL_PREFIX}{session_id}.flag"


def mark_resolved(session_id: str) -> None:
    """Record that this session needs no further checking on later Stops."""
    if not session_id:
        return
    try:
        SCRATCH.mkdir(exist_ok=True)
        sentinel_path(session_id).write_text("")
    except Exception:
        pass


def find_transcript(session_id: str) -> Path | None:
    matches = list(PROJECTS.glob(f"**/{session_id}.jsonl"))
    return matches[0] if matches else None


def load_records(transcript_path: Path) -> list[dict]:
    records: list[dict] = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def main() -> None:
    # Defensive: degrade an unencodable char to a replacement instead of letting
    # print() raise (and the advisory vanish through the exit-0 guard). The text
    # is ASCII by construction; this protects future edits.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    cleanup_stale_sentinels()

    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    session_id = data.get("session_id") or ""

    # Resolved once per session — skip the transcript re-scan on later Stops. (Stop
    # fires at every turn-end, many times per session.)
    if session_id and sentinel_path(session_id).exists():
        sys.exit(0)

    tpath_str = data.get("transcript_path") or ""
    tpath = Path(tpath_str) if tpath_str else None
    if (tpath is None or not tpath.exists()) and session_id:
        tpath = find_transcript(session_id)
    if tpath is None or not tpath.exists():
        sys.exit(0)

    try:
        records = load_records(tpath)
    except Exception:
        sys.exit(0)

    actions = should_emit(records)
    if actions:
        mark_resolved(session_id)
        print(format_advisory(actions))
    elif posttooluse_attachment_present(records):
        # PostToolUse dispatch works this session (a session-level property; ADR-053),
        # so it can never be inert — resolve it so later Stops skip the full re-scan.
        # A session with no board action *and* no PostToolUse attachment stays
        # unresolved, so a board action later in the session is still caught.
        mark_resolved(session_id)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
