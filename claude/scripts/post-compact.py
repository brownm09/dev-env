#!/usr/bin/env python3
"""PostCompact hook — emit a status line and, for manual compactions with open PRs,
inject a systemMessage so Claude auto-invokes /review without user input."""
import json
import subprocess
import sys
from pathlib import Path

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"


def get_journal_project() -> str | None:
    """Infer journal project name from the current git repo (worktree-safe)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve().parent.name
    except Exception:
        pass
    return None


def load_open_prs() -> list[dict]:
    project = get_journal_project()
    if not project:
        return []
    path = JOURNAL_REPO / "sessions" / project / "open-prs.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        return
    data = json.loads(raw)
    trigger = data.get("trigger", "unknown")   # "manual" | "auto"
    summary = data.get("summary", "")
    tokens = data.get("context_tokens", None)

    if trigger == "manual":
        label = "[compact]"
    elif trigger == "auto":
        label = "[auto-compact]"
    else:
        label = f"[compact/{trigger}]"
    if tokens is not None:
        print(f"{label} done -- context now {tokens:,} tokens", file=sys.stderr)
    else:
        print(f"{label} done", file=sys.stderr)

    if summary:
        first_line = summary.splitlines()[0].strip()[:120]
        print(f"  summary: {first_line}", file=sys.stderr)

    if trigger == "manual":
        prs = load_open_prs()
        if prs:
            if len(prs) == 1:
                pr = prs[0]
                pr_ref = f"#{pr['pr']} — {pr['url']}" if pr.get("url") else f"#{pr['pr']}"
                msg = (
                    f"PostCompact complete. Open PR: {pr_ref}\n"
                    f"Per CLAUDE.md workflow: invoke /review {pr.get('url', '')} --post-comment now."
                )
            else:
                pr_list = ", ".join(
                    f"#{p['pr']} {p.get('url', '')}" for p in prs
                )
                msg = (
                    f"PostCompact complete. Open PRs: {pr_list}\n"
                    "Per CLAUDE.md workflow: invoke /review on the relevant PR --post-comment now."
                )
            print(json.dumps({"systemMessage": msg}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
