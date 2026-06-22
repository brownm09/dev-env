#!/usr/bin/env python3
"""PostCompact hook — emit a status line and, for manual compactions with open PRs,
inject a systemMessage so Claude auto-invokes /review without user input."""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
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


def read_open_pr_entries(project_dir: Path) -> list[dict]:
    """Union the per-PR shards `open-prs/<N>.json` (current format, ADR-055) with the
    legacy single `open-prs.jsonl` file, deduped by PR number. Pure filesystem read,
    no network — unit-tested in tests/test_post_compact.py. Reading both formats lets
    the transition need no forced migration; the legacy file drains as its PRs merge."""
    entries: list[dict] = []
    seen: set = set()

    def add(entry: dict) -> None:
        pr = entry.get("pr")
        if pr in seen:
            return
        seen.add(pr)
        entries.append(entry)

    shard_dir = project_dir / "open-prs"
    if shard_dir.is_dir():
        shards = sorted(
            shard_dir.glob("*.json"),
            key=lambda p: int(p.stem) if p.stem.isdigit() else 1 << 30,
        )
        for shard in shards:
            try:
                add(json.loads(shard.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue

    legacy = project_dir / "open-prs.jsonl"
    if legacy.exists():
        for line in legacy.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                add(json.loads(line))
            except json.JSONDecodeError:
                continue

    return entries


def load_open_prs() -> list[dict]:
    project = get_journal_project()
    if not project:
        return []
    return read_open_pr_entries(JOURNAL_REPO / "sessions" / project)


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
