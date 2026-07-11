#!/usr/bin/env python3
"""PostCompact hook — on a manual /compact, emit a {"systemMessage"} status toast
(compaction done + context size) and, when open PRs exist, append the /review
directive so Claude auto-invokes /review. Auto-compaction stays silent.

Routes its one exit-0 output through _hookout.emit_advisory(audience="user") — the
systemMessage channel PostCompact delivers to the user on exit 0. The prior
status/summary/nudge lines went to exit-0 stderr, which PostCompact surfaces to no
one (ADR-103, dev-env#727)."""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import subprocess
import sys
from pathlib import Path

import _hookout
from _journal_shards import iter_pr_shards, read_legacy_entries

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
    """Union the per-PR shards `open-prs/<N>.json` (current format, ADR-056) with the
    legacy single `open-prs.jsonl` file, deduped by PR number. Pure filesystem read,
    no network — unit-tested in tests/test_post_compact.py. Enumeration/parse of both
    formats is delegated to the shared `_journal_shards` reader (ADR-057), the single
    source of truth `reconcile-open-prs.py` imports too. Reading both formats lets the
    transition need no forced migration; the legacy file drains as its PRs merge. Shards
    are read first, so a shard wins the dedup over a stale legacy line for the same PR."""
    entries: list[dict] = []
    seen: set = set()

    def add(entry: dict) -> None:
        pr = entry.get("pr")
        if pr is None:
            return  # a record with no PR number can't drive a /review reminder — skip
            # (also keeps the consumer's pr['pr'] access safe and stops two distinct
            #  pr-less records collapsing to one via a None dedup key)
        if pr in seen:
            return
        seen.add(pr)
        entries.append(entry)

    for _shard, entry in iter_pr_shards(project_dir / "open-prs"):
        add(entry)
    for entry in read_legacy_entries(project_dir / "open-prs.jsonl"):
        add(entry)

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

    # Only a manual /compact surfaces anything. On PostCompact, exit-0 stderr
    # reaches no one (ADR-103) — the prior status/summary/nudge stderr writes were
    # invisible — while exit-0 stdout is delivered to the USER as a
    # {"systemMessage": ...} toast. A manual /compact is user-initiated, so a
    # confirmation is expected; auto-compaction stays silent (routing its status
    # to a systemMessage would newly toast on every auto-compaction — noise, not
    # signal, and a behavior change this cleanup does not intend).
    if trigger != "manual":
        return

    if tokens is not None:
        lines = [f"[compact] done -- context now {tokens:,} tokens"]
    else:
        lines = ["[compact] done"]
    if summary:
        first_line = summary.splitlines()[0].strip()[:120]
        lines.append(f"summary: {first_line}")

    prs = load_open_prs()
    if prs:
        if len(prs) == 1:
            pr = prs[0]
            pr_ref = f"#{pr['pr']} — {pr['url']}" if pr.get("url") else f"#{pr['pr']}"
            review = (
                f"Open PR: {pr_ref}\n"
                f"Per CLAUDE.md workflow: invoke /review {pr.get('url', '')} --post-comment now."
            )
        else:
            pr_list = ", ".join(f"#{p['pr']} {p.get('url', '')}" for p in prs)
            review = (
                f"Open PRs: {pr_list}\n"
                "Per CLAUDE.md workflow: invoke /review on the relevant PR --post-comment now."
            )
        lines.append("")       # blank line separates the status from the /review directive
        lines.append(review)

    # audience="user" -> {"systemMessage": ...} on exit 0, the one channel
    # PostCompact delivers to the user. emit_advisory json.dumps(ensure_ascii=True)
    # escapes the em dash in pr_ref on the wire, so no raw non-ASCII reaches stdout.
    _hookout.emit_advisory("PostCompact", "\n".join(lines), audience="user")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
