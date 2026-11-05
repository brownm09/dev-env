#!/usr/bin/env python3
"""
reconcile-late-stubs.py <draft/YYYY-MM-DD>

Moves stub files pushed to an already-merged draft branch to the earliest
unmerged draft branch (or today's branch if none exists), then deletes the
stale source branch. Each moved stub and manifest is renamed to the target
branch's date, because /journal-compose finds them by <DATE>_*.stub.md.

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
LATE_FILE_NAME = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{6})\.(stub\.md|manifest\.jsonl)")
CARRIED_FILE = re.compile(r"/(reports/[^/]+\.md|tiles/[^/]+\.json)$")


def run(cmd, cwd=None, check=True, capture=True):
    result = subprocess.run(
        cmd, cwd=cwd or JOURNAL_REPO, capture_output=capture, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(str(c) for c in cmd)}\n{result.stderr}")
    return result


def get_merged_pr_info(source_date: str) -> Optional[dict]:
    """The day's latest merged PR. A day merges from draft/<D>, or from compose/<D>
    when the draft conflicted with main, and some days have merged from both. Only
    commits after the last merge are late. A failed lookup raises: treating it as
    "no PR" could pick an earlier merge and move stubs already composed."""
    prs: list[dict] = []
    for head in (f"draft/{source_date}", f"compose/{source_date}"):
        result = run(
            ["gh", "pr", "list", "--repo", "brownm09/engineering-journal",
             "--state", "merged", "--head", head,
             "--json", "number,mergedAt,url"]
        )
        prs += json.loads(result.stdout.strip() or "[]")
    return max(prs, key=lambda pr: pr["mergedAt"], default=None)


def get_commits_after(branch: str, after_iso: str) -> list[str]:
    result = run(
        ["git", "log", f"origin/{branch}", f"--after={after_iso}", "--format=%H"]
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_late_files_in_commits(commits: list[str]) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for commit in commits:
        # --no-renames: a rename line has two tab-separated paths, which would read
        # as one path that does not exist on the branch, losing the stub.
        result = run(["git", "show", commit, "--name-status", "--no-renames", "--format="])
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status, path = parts
            if status.startswith("D"):
                continue
            # Per-session manifest shards (YYYY-MM-DD_HHMMSS.manifest.jsonl, ADR-056)
            # match the same suffix as the legacy per-day manifest. main() refuses a
            # legacy one, since it has no single stub to rename with.
            # Open-PR records (legacy open-prs.jsonl and open-prs/<N>.json shards) are
            # excluded — the target branch's copy is authoritative. Reports and tile
            # shards exist nowhere else once the source branch is deleted, so they move too.
            if (path.endswith(".stub.md")
                    or path.endswith(".manifest.jsonl")
                    or CARRIED_FILE.search(path)):
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
        # Composed journals sit directly in sessions/<project>/. A report, retro or
        # audit in a subdirectory can carry a date whose journal is not composed yet.
        if line.count("/") != 2:
            continue
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


def retarget_manifest(text: str, new_stem: str) -> str:
    """Point a manifest shard's `stub` field at new_stem, keeping its form: both a
    bare filename and a sessions/<project>/ path occur in real shards."""
    entry = json.loads(text)
    prefix = entry["stub"].rpartition("/")[0]
    entry["stub"] = f"{prefix}/{new_stem}.stub.md" if prefix else f"{new_stem}.stub.md"
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"


def show_on_branch(branch: str, rel_path: str) -> Optional[str]:
    result = run(["git", "show", f"origin/{branch}:{rel_path}"], check=False)
    return result.stdout if result.returncode == 0 else None


def read_late_files(source_branch: str, late_files: list[str]
                    ) -> tuple[dict[tuple[str, str], dict[str, str]], dict[str, str]]:
    """Read and check everything before the remote is touched. Returns the sessions to
    rename, keyed by (parent, stem), and the reports and tile shards to copy as they are.

    A late stub brings its manifest along, so compose keeps the session's topic and PR
    numbers. A late manifest with no late stub is bookkeeping for a session already
    composed: moving it would feed that topic into another day, so it is skipped.
    """
    sessions: dict[tuple[str, str], dict[str, str]] = {}
    carried: dict[str, str] = {}
    keep = f"Nothing was moved and {source_branch} is kept; move it by hand."
    for rel_path in late_files:
        if CARRIED_FILE.search(rel_path):
            content = show_on_branch(source_branch, rel_path)
            if content is not None:
                carried[rel_path] = content
            continue
        parent, _, name = rel_path.rpartition("/")
        match = LATE_FILE_NAME.fullmatch(name)
        if not match:
            raise RuntimeError(
                f"Cannot rename {rel_path}: not a YYYY-MM-DD_HHMMSS stub or manifest. {keep}"
            )
        stem = match[1]
        if (parent, stem) in sessions:
            continue
        stub_path = f"{parent}/{stem}.stub.md"
        if stub_path not in late_files:
            print(f"  Skipping {rel_path}: no late stub, so that session is already composed")
            continue
        stub = show_on_branch(source_branch, stub_path)
        if stub is None:
            print(f"  Skipping {rel_path}: {stub_path} is not on {source_branch}")
            continue
        contents = {"stub.md": stub}
        manifest_path = f"{parent}/{stem}.manifest.jsonl"
        manifest = show_on_branch(source_branch, manifest_path)
        if manifest is not None:
            try:
                retarget_manifest(manifest, stem)
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                raise RuntimeError(f"Cannot read {manifest_path}: {e!r}. {keep}") from e
            contents["manifest.jsonl"] = manifest
        sessions[(parent, stem)] = contents
    return sessions, carried


def place_on_target(worktree: Path, parent: str, stem: str,
                    contents: dict[str, str], target_date: str) -> dict[str, str]:
    """Map one session's stub and manifest to their paths on the target branch.

    Keeps HHMMSS, stepping it forward past a different session's file so nothing is
    overwritten. Files already there with the same content stay put, so an immediate
    re-run does not add the session again.
    """
    hhmmss = int(stem[11:])
    while True:
        new_stem = f"{target_date}_{hhmmss:06d}"
        placed = {
            f"{parent}/{new_stem}.{kind}":
                retarget_manifest(content, new_stem) if kind == "manifest.jsonl" else content
            for kind, content in contents.items()
        }
        clash = False
        for kind in ("stub.md", "manifest.jsonl"):
            rel_path = f"{parent}/{new_stem}.{kind}"
            existing = worktree / rel_path
            if existing.exists() and existing.read_text(encoding="utf-8") != placed.get(rel_path):
                clash = True
        if not clash:
            return placed
        hhmmss += 1


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: reconcile-late-stubs.py <draft/YYYY-MM-DD>", file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[1].strip()
    source_branch = arg if arg.startswith("draft/") else f"draft/{arg}"
    source_date = source_branch.removeprefix("draft/")

    print(f"Reconciling late stubs from {source_branch}...")

    run(["git", "fetch", "origin", source_branch, "--quiet"])

    pr = get_merged_pr_info(source_date)
    if not pr:
        print(f"Error: No merged PR found for draft/{source_date} or compose/{source_date}. "
              "Aborting.", file=sys.stderr)
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

    late_files = get_late_files_in_commits(commits)
    print(f"Found {len(late_files)} late file(s):")
    for f in late_files:
        print(f"  {f}")

    sessions, carried = read_late_files(source_branch, late_files)
    if not sessions and not carried:
        print("No files to move in new commits. Deleting stale branch...")
        run(["git", "push", "origin", "--delete", source_branch])
        print(f"Deleted {source_branch}. Done.")
        return

    run(["git", "fetch", "origin", "--quiet"])
    target_branch = find_target_branch(source_date)
    print(f"Target branch: {target_branch}")
    if target_branch == source_branch:
        # The fallback target is today's branch. Moving onto it and then deleting the
        # source would delete the only branch that holds the stubs.
        print(f"Error: {source_branch} is today's branch, so there is no later branch to "
              "move to. Nothing was moved and it is kept.", file=sys.stderr)
        sys.exit(1)

    ensure_remote_branch_exists(target_branch)
    run(["git", "fetch", "origin", target_branch, "--quiet"])
    target_date = target_branch.removeprefix("draft/")

    temp_dir = SCRATCH / f"reconcile_tmp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # --detach avoids "branch already checked out" errors from active worktrees
        run(["git", "worktree", "add", "--detach", str(temp_dir),
             f"origin/{target_branch}"])

        to_write = dict(carried)
        for (parent, stem), contents in sessions.items():
            to_write.update(place_on_target(temp_dir, parent, stem, contents, target_date))

        copied = list(to_write)
        for rel_path, content in to_write.items():
            dest = temp_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8", newline="\n")
            print(f"  Copied to {rel_path}")

        run(["git", "add"] + copied, cwd=temp_dir)
        if run(["git", "diff", "--cached", "--quiet"], cwd=temp_dir, check=False).returncode == 0:
            print(f"Already on {target_branch}; nothing to push")
        else:
            run(
                ["git", "commit", "-m",
                 f"reconcile: move late stubs from {source_branch}\n\n"
                 f"PR #{pr['number']} was already merged but {len(commits)} commit(s) were pushed "
                 f"after the merge. Moving {len(copied)} stub file(s) to {target_branch}, "
                 f"renamed to its date."],
                cwd=temp_dir,
            )
            run(["git", "push", "origin", f"HEAD:refs/heads/{target_branch}"], cwd=temp_dir)
            print(f"Pushed {len(copied)} file(s) to {target_branch}")

    finally:
        run(["git", "worktree", "remove", "--force", str(temp_dir)], check=False)
        shutil.rmtree(temp_dir, ignore_errors=True)

    # A re-run is only safe while the target is uncomposed: after that the script picks
    # a later branch and would move the same session again.
    if run(["git", "push", "origin", "--delete", source_branch], check=False).returncode != 0:
        print(f"Error: moved the files to {target_branch} but could not delete {source_branch}. "
              f"Delete it by hand: git push origin --delete {source_branch}. Do not re-run.",
              file=sys.stderr)
        sys.exit(1)
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
