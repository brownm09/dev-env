#!/usr/bin/env python3
"""
reconcile-late-stubs.py <draft/YYYY-MM-DD>

Moves stub files pushed to an already-merged draft branch to the earliest
unmerged draft branch (or today's branch if none exists), then deletes the
stale source branch.

Usage:
  py -3 reconcile-late-stubs.py draft/2026-05-06
  py -3 reconcile-late-stubs.py 2026-05-06   # draft/ prefix optional
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SCRATCH = Path.home() / ".claude" / "scratch"
TODAY = date.today().strftime("%Y-%m-%d")


def run(cmd, cwd=None, check=True, capture=True):
    result = subprocess.run(
        cmd, cwd=cwd or JOURNAL_REPO, capture_output=capture, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(str(c) for c in cmd)}\n{result.stderr}")
    return result


def get_merged_pr_info(branch: str) -> Optional[dict]:
    result = subprocess.run(
        ["gh", "pr", "list", "--repo", "brownm09/engineering-journal",
         "--state", "merged", "--head", branch,
         "--json", "number,mergedAt,url"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    prs = json.loads(result.stdout.strip() or "[]")
    return prs[0] if prs else None


def get_commits_after(branch: str, after_iso: str) -> list[str]:
    result = run(
        ["git", "log", f"origin/{branch}", f"--after={after_iso}", "--format=%H"]
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_stub_files_in_commits(commits: list[str]) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for commit in commits:
        result = run(["git", "show", commit, "--name-status", "--format="])
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status, path = parts
            if status.startswith("D"):
                continue
            # Per-session manifest shards (YYYY-MM-DD_HHMMSS.manifest.jsonl, ADR-055)
            # match the same suffix as the legacy per-day manifest, so both move.
            # Open-PR records (legacy open-prs.jsonl and open-prs/<N>.json shards) are
            # excluded — the target branch's copy is authoritative.
            if (path.endswith(".stub.md")
                    or path.endswith(".manifest.jsonl")):
                if path not in seen:
                    files.append(path)
                    seen.add(path)
    return files


def find_target_branch(source_date: str) -> str:
    result = run(["git", "ls-remote", "--heads", "origin", "refs/heads/draft/*"])
    remote_dates: list[str] = []
    for line in result.stdout.splitlines():
        if "\t" in line:
            ref = line.split("\t", 1)[1].strip()
            date_str = ref.replace("refs/heads/draft/", "")
            if re.match(r"\d{4}-\d{2}-\d{2}$", date_str) and date_str > source_date:
                remote_dates.append(date_str)

    main_tree = run(["git", "ls-tree", "-r", "--name-only", "origin/main", "sessions/"])
    composed: set[str] = set()
    for line in main_tree.stdout.splitlines():
        fname = line.split("/")[-1]
        if not fname.endswith(".stub.md") and fname.endswith(".md"):
            if len(fname) >= 10 and fname[4] == "-" and fname[7] == "-":
                composed.add(fname[:10])

    for date_str in sorted(remote_dates):
        if date_str not in composed:
            return f"draft/{date_str}"

    # Fallback: today's branch. There should essentially always be an active
    # day branch, and ensure_remote_branch_exists will create it if absent.
    return f"draft/{TODAY}"


def ensure_remote_branch_exists(branch: str) -> None:
    result = run(["git", "ls-remote", "--heads", "origin", branch])
    if not result.stdout.strip():
        print(f"  Creating remote branch {branch} from origin/main...")
        run(["git", "push", "origin", f"origin/main:refs/heads/{branch}"])


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: reconcile-late-stubs.py <draft/YYYY-MM-DD>", file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[1].strip()
    source_branch = arg if arg.startswith("draft/") else f"draft/{arg}"
    source_date = source_branch.removeprefix("draft/")

    print(f"Reconciling late stubs from {source_branch}...")

    run(["git", "fetch", "origin", source_branch, "--quiet"])

    pr = get_merged_pr_info(source_branch)
    if not pr:
        print(f"Error: No merged PR found for {source_branch}. Aborting.", file=sys.stderr)
        sys.exit(1)

    merged_at = pr["mergedAt"]
    print(f"Found merged PR #{pr['number']} ({pr['url']}) merged at {merged_at}")

    commits = get_commits_after(source_branch, merged_at)
    if not commits:
        print("No commits found after merge. Deleting stale branch...")
        run(["git", "push", "origin", "--delete", source_branch])
        print(f"Deleted {source_branch}. Done.")
        return

    print(f"Found {len(commits)} commit(s) after merge:")
    for c in commits:
        msg = run(["git", "log", "-1", "--format=%s", c]).stdout.strip()
        print(f"  {c[:8]} {msg}")

    stub_files = get_stub_files_in_commits(commits)
    if not stub_files:
        print("No stub/manifest files in new commits. Deleting stale branch...")
        run(["git", "push", "origin", "--delete", source_branch])
        print(f"Deleted {source_branch}. Done.")
        return

    print(f"Found {len(stub_files)} file(s) to move:")
    for f in stub_files:
        print(f"  {f}")

    run(["git", "fetch", "origin", "--quiet"])
    target_branch = find_target_branch(source_date)
    print(f"Target branch: {target_branch}")

    ensure_remote_branch_exists(target_branch)
    run(["git", "fetch", "origin", target_branch, "--quiet"])

    temp_dir = SCRATCH / f"reconcile_tmp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # --detach avoids "branch already checked out" errors from active worktrees
        run(["git", "worktree", "add", "--detach", str(temp_dir),
             f"origin/{target_branch}"])

        copied: list[str] = []
        for rel_path in stub_files:
            content_result = run(
                ["git", "show", f"origin/{source_branch}:{rel_path}"],
                check=False,
            )
            if content_result.returncode != 0:
                print(f"  Skipping {rel_path} (not found on source branch)")
                continue
            dest = temp_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content_result.stdout, encoding="utf-8")
            print(f"  Copied {rel_path}")
            copied.append(rel_path)

        if not copied:
            print("No files could be copied. Deleting stale branch...")
            run(["git", "push", "origin", "--delete", source_branch])
            return

        run(["git", "add"] + copied, cwd=temp_dir)
        run(
            ["git", "commit", "-m",
             f"reconcile: move late stubs from {source_branch}\n\n"
             f"PR #{pr['number']} was already merged but {len(commits)} commit(s) were pushed "
             f"after the merge. Moving {len(copied)} stub file(s) to {target_branch}."],
            cwd=temp_dir,
        )
        run(["git", "push", "origin", f"HEAD:refs/heads/{target_branch}"], cwd=temp_dir)
        print(f"Pushed {len(copied)} file(s) to {target_branch}")

    finally:
        run(["git", "worktree", "remove", "--force", str(temp_dir)], check=False)
        shutil.rmtree(temp_dir, ignore_errors=True)

    run(["git", "push", "origin", "--delete", source_branch])
    print(f"Deleted stale branch {source_branch}")
    print("Reconciliation complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
