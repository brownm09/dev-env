#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh issue create' or 'gh pr create'
and automatically adds the item to the configured GitHub project.

Detection matches only a top-level CLI invocation (via _hookio.scan_top_level),
not the string appearing inside commit messages, heredocs, grep patterns, or
other quoted arguments (dev-env#499).

Project opt-in: add .claude/hook-config.json to the project root.
Projects without that file are silently skipped.

hook-config.json schema:
  {
    "project_number":  "2",
    "project_owner":   "brownm09",
    "project_node_id": "PVT_kwHOAjEKvM4BTuEF",
    "epic_field_id":   "PVTSSF_...",
    "epic_options": {
      "<name>": "<option-id>",
      ...
    },
    "milestones": ["v0.1 — Foundation", ...]
  }

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "..."},  # NOT "output" — ADR-049
    "session_id": "...",
    "cwd": "..."
  }

Exit 0  — not a relevant command, no config, or gh command itself failed; silent
Exit 2  — item added (or failed to add); structured reminder emitted via stderr
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

from _hookio import read_command_output, scan_top_level

CONFIG_FILE = ".claude/hook-config.json"

# Matches the start of a statement token against `gh issue create` or
# `gh pr create` (dev-env#499). The check functions below anchor via
# .match(), and scan_top_level only ever calls them on top-level statements —
# never a substring buried in a heredoc body, a quoted commit message, a grep
# pattern, or a --text field value (the false-positive class fixed here; the
# previous unanchored re.search(r"\bgh\s+issue\s+create\b", command) /
# re.search(r"\bgh\s+pr\s+create\b", command) matched the pattern ANYWHERE in
# the raw command string).
_ISSUE_CREATE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+issue\s+create\b")
_PR_CREATE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+create\b")


def _check_issue_create_stmt(token: str) -> bool:
    return bool(_ISSUE_CREATE_RE.match(token.lstrip()))


def _check_pr_create_stmt(token: str) -> bool:
    return bool(_PR_CREATE_RE.match(token.lstrip()))


def is_issue_create_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh issue create`."""
    return scan_top_level(command, _check_issue_create_stmt)


def is_pr_create_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh pr create`."""
    return scan_top_level(command, _check_pr_create_stmt)


# Matches `<root>/.claude/worktrees/<name>` at the start of a path, capturing the
# canonical repo root (everything before `/.claude/`). Mirrors the proven prefix
# regex in pre-tool-use-worktree-path-check.py; tolerates `/` and `\` separators.
_WORKTREE_RE = re.compile(
    r"^(.+?)[/\\]\.claude[/\\]worktrees[/\\][^/\\]+",
    re.IGNORECASE,
)


def canonical_root_from_worktree(cwd: str) -> str | None:
    """Canonical repo root for a Claude-managed worktree cwd
    (`<root>/.claude/worktrees/<name>/...`), else None. Pure — no I/O, so it is
    exercised offline by the unit tests."""
    m = _WORKTREE_RE.match(cwd or "")
    return m.group(1) if m else None


def _canonical_root_from_common_dir(cwd: str, common: str) -> str | None:
    """Resolve the canonical repo root from a `git rev-parse --git-common-dir`
    result. `common` may be absolute or relative to `cwd`; it is the canonical
    checkout's `.git` dir, so its parent is the root. Returns None when the output
    is empty or does not name a `.git` dir. Pure — no I/O, offline-testable."""
    common = (common or "").strip()
    if not common:
        return None
    common_abs = common if os.path.isabs(common) else os.path.join(cwd, common)
    common_norm = os.path.normpath(common_abs)
    if os.path.basename(common_norm).lower() != ".git":
        return None
    return os.path.dirname(common_norm)


def canonical_root_via_git(cwd: str) -> str | None:
    """Canonical repo root for a *sibling* worktree (e.g. `dev-env-188`, which the
    path regex above cannot derive) via `git rev-parse --git-common-dir`. Returns
    None on any git failure so load_config degrades to a silent skip rather than
    raising. Only the `subprocess.run` call is untested (repo convention,
    cf. add_to_project); the pure resolution is `_canonical_root_from_common_dir`."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return _canonical_root_from_common_dir(cwd, result.stdout)


def _read_config(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_config(cwd: str) -> dict | None:
    """Load hook-config.json for the project, or None.

    The config is gitignored and machine-local, so it lives only in the canonical
    checkout — `git worktree add` never checks it out and the harness copies it into
    Claude-managed worktrees only inconsistently (dev-env #378). Read the cwd-local
    copy first; when it is absent in a worktree, fall back to the canonical
    checkout's copy so worktree sessions behave like main-checkout sessions.
    """
    cfg = _read_config(os.path.join(cwd, CONFIG_FILE))
    if cfg is not None:
        return cfg
    # Claude-managed worktree (`<root>/.claude/worktrees/<name>`): pure, no subprocess.
    root = canonical_root_from_worktree(cwd)
    if root:
        cfg = _read_config(os.path.join(root, CONFIG_FILE))
        if cfg is not None:
            return cfg
    # Sibling worktree (`<root>-<suffix>`): resolve the canonical root via git.
    root = canonical_root_via_git(cwd)
    if root and os.path.normpath(root) != os.path.normpath(cwd):
        return _read_config(os.path.join(root, CONFIG_FILE))
    return None


def extract_github_url(output: str, repo: str | None = None) -> str | None:
    """Return the last GitHub URL found in command output, or None.

    If repo is provided (e.g. 'owner/name'), only return a URL that contains
    that repo path — prevents cross-repo false positives when cwd belongs to
    a different project than the one being created.
    """
    pattern = (
        rf"https://github\.com/{re.escape(repo)}/" if repo
        else r"https://github\.com/"
    )
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if re.search(pattern, line):
            # Extract just the URL in case the line has surrounding text
            match = re.search(r"https://github\.com/\S+", line)
            if match:
                return match.group(0).rstrip(".")
    return None


def add_to_project(url: str, config: dict) -> str | None:
    """Add item to the configured project and return the item ID, or None."""
    try:
        result = subprocess.run(
            [
                "gh", "project", "item-add", config["project_number"],
                "--owner", config["project_owner"],
                "--url", url,
                "--format", "json",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("id")
    except Exception:
        return None


def format_reminder(item_type: str, url: str, item_id: str, config: dict) -> str:
    lines = [
        f"[project-hook] {item_type} added to project.",
        f"  URL:     {url}",
        f"  Item ID: {item_id}",
    ]

    required_fields = list(config.get("required_fields", []))

    # Backward compat: convert old epic_field_id / milestones shape
    if not required_fields:
        if config.get("epic_field_id"):
            opts = config.get("epic_options", {})
            required_fields.append({
                "name": "Epic",
                "field_id": config["epic_field_id"],
                "type": "single_select",
                "options": opts,
            })
        if config.get("milestones"):
            required_fields.append({
                "name": "Milestone",
                "type": "milestone",
                "options_list": config["milestones"],
            })

    for field in required_fields:
        name = field.get("name", "Field")
        field_id = field.get("field_id", "")
        ftype = field.get("type", "text")
        hint = field.get("hint", "")
        hint_str = f" ({hint})" if hint else ""

        lines.append("")
        lines.append(f"  Set {name}{hint_str}:")

        project_node_id = config.get("project_node_id", "<project-node-id>")
        if ftype == "single_select":
            lines += [
                f"    gh project item-edit \\",
                f"      --project-id {project_node_id} \\",
                f"      --id {item_id} \\",
                f"      --field-id {field_id} \\",
                f"      --single-select-option-id <option-id>",
            ]
            opts = field.get("options", {})
            if opts:
                lines.append(f"  {name} options:")
                for opt_name, opt_id in opts.items():
                    lines.append(f"      {opt_name}: {opt_id}")
        elif ftype == "text":
            lines += [
                f"    gh project item-edit \\",
                f"      --project-id {project_node_id} \\",
                f"      --id {item_id} \\",
                f"      --field-id {field_id} \\",
                f"      --text \"<{name.lower()}>\"",
            ]
        elif ftype == "milestone":
            opts_list = field.get("options_list", [])
            opts_str = (
                ", ".join(f'"{m}"' for m in opts_list) if opts_list else "<milestone>"
            )
            lines.append(f"    gh issue edit <N> --milestone {opts_str}")

    return "\n".join(lines)


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
    exit_code = data.get("tool_response", {}).get("exitCode", 0)
    cwd = data.get("cwd", "")

    is_issue_create = is_issue_create_command(command)
    is_pr_create = is_pr_create_command(command)

    if not (is_issue_create or is_pr_create):
        sys.exit(0)

    # Don't process if the gh command itself failed
    if exit_code != 0:
        sys.exit(0)

    # Load project config — skip silently if not present
    config = load_config(cwd)
    if config is None:
        sys.exit(0)

    item_type = "Issue" if is_issue_create else "PR"
    repo = config.get("repo")  # e.g. "owner/repo-name"

    url = extract_github_url(output, repo)
    if not url:
        # A configured repo filter can legitimately miss: the command may have
        # created the item in a *different* repo than this cwd's project. Stay
        # silent only in that case — some GitHub URL is present, just not ours.
        # If a successful create produced no GitHub URL at all, that is the
        # symptom of a real failure (e.g. reading the wrong payload field — the
        # bug behind #377) and must surface rather than be swallowed silently.
        if repo and extract_github_url(output, None):
            sys.exit(0)
        print(
            f"[project-hook] {item_type} created but no GitHub URL found in output.\n"
            f"  Add to project manually:\n"
            f"    gh project item-add {config['project_number']} "
            f"--owner {config['project_owner']} --url <url>",
            file=sys.stderr,
        )
        sys.exit(2)

    item_id = add_to_project(url, config)

    if item_id:
        print(format_reminder(item_type, url, item_id, config), file=sys.stderr)
    else:
        print(
            f"[project-hook] {item_type} created but auto-add to project failed.\n"
            f"  URL: {url}\n"
            f"  Add manually:\n"
            f"    gh project item-add {config['project_number']} "
            f"--owner {config['project_owner']} --url {url}",
            file=sys.stderr,
        )

    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
