#!/usr/bin/env python3
"""PostCompact hook — on a manual /compact, emit a {"systemMessage"} status toast
(compaction done + context size), append the /review directive when open PRs exist
so Claude auto-invokes /review, and list any pending tile shards (ADR-118).
Auto-compaction stays silent.

Compaction is the second boundary at which tile context is lost — the first is a
session start / app restart, handled by reconcile-pending-tiles.py. Both read the
same `sessions/<project>/tiles/<issue-number>.json` shards through the same shared
`_journal_shards` reader, exactly as the two hooks already share the open-PR shard
read. This path is read-only: it never reconciles against GitHub and never unlinks
a shard (see `read_tile_entries`).

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
import _hookutil
from _journal_shards import iter_pr_shards, iter_tile_shards, read_legacy_entries

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"

# Pending tiles listed after a /compact before truncating. Matches
# reconcile-pending-tiles.py's cap; the true total is always stated alongside.
MAX_TILES_SHOWN = 10


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


def read_tile_entries(project_dir: Path) -> list[dict]:
    """The pending tile shards `tiles/<issue-number>.json` for one project (ADR-118).

    Compaction is the other boundary where tile context is lost: the pending-tile index
    that `reconcile-pending-tiles.py` surfaces at session start is exactly the kind of
    turn-1 context a `/compact` drops, and the workflow prompts `/compact` right after a PR
    is opened — the moment follow-up tiles have just been spawned. So the same shards are
    re-read here.

    Pure filesystem read, no network — deliberately unlike the UserPromptSubmit reader,
    which reconciles against live GitHub state. This hook has never made a network call and
    a `/compact` should stay fast, so shards are listed as-is; a tile whose issue has since
    closed is pruned by the next session-start reconcile, not here. Nothing is unlinked on
    this path.

    Enumeration/parse is delegated to the shared `_journal_shards` reader (ADR-057), so the
    numeric-filename filtering, numeric sort, and malformed-shard tolerance match the
    reconciler exactly. Unit-tested in tests/test_post_compact.py.
    """
    entries: list[dict] = []
    for shard, entry in iter_tile_shards(project_dir / "tiles"):
        issue = entry.get("issue")
        if not isinstance(issue, int) or isinstance(issue, bool):
            # The filename is the authoritative key (ADR-118); fall back to it so a shard
            # with a missing or non-numeric `issue` field is still listed rather than
            # dropped — this path only reports, so a best-effort read is the right call.
            try:
                issue = int(shard.stem)
            except ValueError:
                continue
        entries.append({**entry, "issue": issue})
    return entries


def format_pending_tiles(entries: list[dict], max_shown: int = MAX_TILES_SHOWN) -> str:
    """Render the pending-tile block, or "" when there are none.

    States the true total even when the list is capped — the same no-silent-truncation
    rule the session-start reader follows.
    """
    if not entries:
        return ""
    lines = [
        f"Pending tiles ({len(entries)}) -- spawn_task chips do not survive an app restart; "
        "these shards are the durable payload (ADR-118). Check list_sessions for a matching "
        "title/branch before re-spawning any of them."
    ]
    for entry in entries[:max_shown]:
        title = str(entry.get("title") or "(no title)")
        lines.append(f"  #{entry['issue']} \"{title}\"")
    if len(entries) > max_shown:
        lines.append(f"  ... and {len(entries) - max_shown} more not shown "
                     f"({max_shown} of {len(entries)} listed).")
    return "\n".join(lines)


def load_open_prs() -> list[dict]:
    project = get_journal_project()
    if not project:
        return []
    return read_open_pr_entries(JOURNAL_REPO / "sessions" / project)


def load_pending_tiles() -> list[dict]:
    project = get_journal_project()
    if not project:
        return []
    return read_tile_entries(JOURNAL_REPO / "sessions" / project)


def main():
    _hookutil.record_heartbeat("post-compact")
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

    tiles = format_pending_tiles(load_pending_tiles())
    if tiles:
        lines.append("")       # blank line separates the tile index from what precedes it
        lines.append(tiles)

    # audience="user" -> {"systemMessage": ...} on exit 0, the one channel
    # PostCompact delivers to the user. emit_advisory json.dumps(ensure_ascii=True)
    # escapes the em dash in pr_ref on the wire, so no raw non-ASCII reaches stdout.
    _hookout.emit_advisory("PostCompact", "\n".join(lines), audience="user")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
