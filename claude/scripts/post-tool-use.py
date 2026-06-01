#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh issue create' or 'gh pr create'
and automatically adds the item to the configured GitHub project.

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
    "tool_response": {"output": "...", "exitCode": 0},
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

CONFIG_FILE = ".claude/hook-config.json"


def load_config(cwd: str) -> dict | None:
    """Load hook-config.json from the project root, or return None."""
    path = os.path.join(cwd, CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
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
    output = data.get("tool_response", {}).get("output", "")
    exit_code = data.get("tool_response", {}).get("exitCode", 0)
    cwd = data.get("cwd", "")

    is_issue_create = bool(re.search(r"\bgh\s+issue\s+create\b", command))
    is_pr_create = bool(re.search(r"\bgh\s+pr\s+create\b", command))

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
        # If a repo filter is configured, a missing URL most likely means the
        # command targeted a different repo — exit silently rather than warning.
        if repo:
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
