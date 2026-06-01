#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh pr merge' and automatically
moves the linked issue's GitHub Project item to Done.

Project opt-in: add .claude/hook-config.json to the project root with
'status_field_id' and 'done_option_id' fields (in addition to the existing
project fields). Projects without these fields are silently skipped.

hook-config.json schema (fields used by this hook):
  {
    "repo":            "owner/repo-name",
    "project_number":  "3",
    "project_owner":   "brownm09",
    "project_node_id": "PVT_kwHOAjEKvM4BWKFe",
    "status_field_id": "PVTSSF_...",
    "done_option_id":  "98236657"
  }

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"output": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0  — gh pr merge not detected, no config, or no Closes ref; silent
Exit 2  — item moved to Done (success) or move failed (fallback command shown)
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

CONFIG_FILE = ".claude/hook-config.json"

# _scan_top_level and helpers below are duplicated from pr-merge-reminder.py.
_MERGE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b")
_CLOSES_RE = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
_PR_URL_RE = re.compile(r"https://github\.com/\S+/pull/(\d+)")


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


def load_config(cwd: str) -> dict | None:
    path = os.path.join(cwd, CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def extract_pr_number(output: str) -> int | None:
    for line in reversed(output.strip().splitlines()):
        m = _PR_URL_RE.search(line.strip())
        if m:
            return int(m.group(1))
    return None


def get_pr_body(pr_number: int, repo: str) -> str | None:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "body", "--repo", repo],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("body", "")
    except Exception:
        return None


def parse_closes_numbers(body: str) -> list[int]:
    return [int(n) for n in _CLOSES_RE.findall(body)]


def find_project_item(issue_number: int, config: dict) -> str | None:
    try:
        result = subprocess.run(
            [
                "gh", "project", "item-list", config["project_number"],
                "--owner", config["project_owner"],
                "--format", "json",
                "--limit", "1000",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        for item in data.get("items", []):
            if item.get("content", {}).get("number") == issue_number:
                return item["id"]
        return None
    except Exception:
        return None


def move_to_done(item_id: str, config: dict) -> bool:
    try:
        result = subprocess.run(
            [
                "gh", "project", "item-edit",
                "--project-id", config["project_node_id"],
                "--id", item_id,
                "--field-id", config["status_field_id"],
                "--single-select-option-id", config["done_option_id"],
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        return False


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

    output = data.get("tool_response", {}).get("output", "")
    cwd = data.get("cwd", "")

    config = load_config(cwd)
    if config is None:
        sys.exit(0)

    if not config.get("status_field_id") or not config.get("done_option_id"):
        sys.exit(0)

    repo = config.get("repo", "")
    if not repo:
        sys.exit(0)

    pr_number = extract_pr_number(output)
    if pr_number is None:
        sys.exit(0)

    body = get_pr_body(pr_number, repo)
    if not body:
        sys.exit(0)

    issue_numbers = parse_closes_numbers(body)
    if not issue_numbers:
        sys.exit(0)

    messages = []
    for issue_number in issue_numbers:
        item_id = find_project_item(issue_number, config)
        if item_id is None:
            messages.append(
                f"[project-hook] Issue #{issue_number} not found in project "
                f"(project {config['project_number']}, owner {config['project_owner']}).\n"
                f"  Move manually:\n"
                f"    gh project item-edit --project-id {config['project_node_id']} "
                f"--id <item-id> --field-id {config['status_field_id']} "
                f"--single-select-option-id {config['done_option_id']}"
            )
            continue

        if move_to_done(item_id, config):
            messages.append(
                f"[project-hook] Issue #{issue_number} moved to Done "
                f"(item {item_id})."
            )
        else:
            messages.append(
                f"[project-hook] Failed to move Issue #{issue_number} to Done.\n"
                f"  Move manually:\n"
                f"    gh project item-edit --project-id {config['project_node_id']} "
                f"--id {item_id} --field-id {config['status_field_id']} "
                f"--single-select-option-id {config['done_option_id']}"
            )

    if messages:
        print("\n\n".join(messages), file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
