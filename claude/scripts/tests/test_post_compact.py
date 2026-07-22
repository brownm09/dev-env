#!/usr/bin/env python3
"""Unit tests for post-compact.py open-PR and pending-tile reading (ADR-056, ADR-118).

`post-compact.py` reads the open-PR tracking on a manual `/compact` to remind
Claude to run `/review`. ADR-056 reshaped that tracking into per-PR shards
`sessions/<project>/open-prs/<N>.json`, with the pre-ADR-056 single
`open-prs.jsonl` still read during the transition. `read_open_pr_entries` unions
both, deduped by PR number — these tests pin that union, the numeric (not lexical)
shard ordering, and the malformed-input tolerance, all offline.

ADR-118 (dev-env#869) added the second read: compaction is the other boundary at
which tile context is lost, so `read_tile_entries` lists the pending tile shards
`sessions/<project>/tiles/<issue-number>.json` alongside the open PRs. Two
properties distinguish it from the session-start reconciler and are pinned here:
it is **read-only** (no shard is ever unlinked on the `/compact` path — a tile
whose issue has since closed is pruned by the next session-start reconcile, not
here), and it falls back to the **filename** when a shard's `issue` field is
missing or non-numeric, since the filename is the authoritative key. Truncation
behaviour is pinned too: `format_pending_tiles` must state the true total even
when it caps the list.

`get_journal_project` (a `git` call) and the systemMessage emission are not tested
— they shell out / read stdin — matching the repo's fixture-only convention.

Usage:
    py -3 claude/scripts/tests/test_post_compact.py

Exit 0 = all pass.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "post-compact.py"

# The script imports _winsubp (a sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("post_compact", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

read_open_pr_entries = mod.read_open_pr_entries
read_tile_entries = mod.read_tile_entries
format_pending_tiles = mod.format_pending_tiles

URL = "https://github.com/brownm09/dev-env/pull/{n}"
ISSUE_URL = "https://github.com/brownm09/dev-env/issues/{n}"


def _entry(pr):
    return {"pr": pr, "url": URL.format(n=pr), "topic": f"PR {pr}", "stub": "s.stub.md", "opened": "2026-06-22"}


def _write_shard(project_dir: Path, pr):
    sd = project_dir / "open-prs"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / f"{pr}.json").write_text(json.dumps(_entry(pr)), encoding="utf-8")


def _write_legacy(project_dir: Path, prs):
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "open-prs.jsonl").write_text(
        "\n".join(json.dumps(_entry(pr)) for pr in prs) + "\n", encoding="utf-8")


def test_shards_only() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        _write_shard(proj, 54)
        _write_shard(proj, 110)
        prs = [e["pr"] for e in read_open_pr_entries(proj)]
        assert prs == [54, 110], f"both shards read, got {prs}"
    return "per-PR shards are read"


def test_legacy_only() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        _write_legacy(proj, [7, 9])
        prs = [e["pr"] for e in read_open_pr_entries(proj)]
        assert sorted(prs) == [7, 9], f"legacy lines read, got {prs}"
    return "legacy open-prs.jsonl is still read during the transition"


def test_union_dedup_shard_wins() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        _write_shard(proj, 54)
        _write_legacy(proj, [54, 88])  # 54 duplicates the shard
        entries = read_open_pr_entries(proj)
        prs = [e["pr"] for e in entries]
        assert prs.count(54) == 1, f"PR 54 deduped, got {prs}"
        assert set(prs) == {54, 88}, f"union of shard + legacy, got {prs}"
        assert prs[0] == 54, "shard is read first, so it wins the dedup"
    return "shards + legacy unioned, deduped by PR (shard wins)"


def test_numeric_shard_ordering() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        _write_shard(proj, 2)
        _write_shard(proj, 10)
        prs = [e["pr"] for e in read_open_pr_entries(proj)]
        assert prs == [2, 10], f"numeric order (not lexical '10' < '2'), got {prs}"
    return "shards sorted numerically by PR number, not lexically"


def test_missing_both() -> str:
    with tempfile.TemporaryDirectory() as root:
        assert read_open_pr_entries(Path(root)) == []
    return "no shards and no legacy file -> []"


def test_malformed_shard_skipped() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        _write_shard(proj, 5)
        (proj / "open-prs" / "bad.json").write_text("{not json", encoding="utf-8")
        prs = [e["pr"] for e in read_open_pr_entries(proj)]
        assert prs == [5], f"good shard kept, bad skipped, got {prs}"
    return "an unparseable shard is skipped; valid shards still returned"


def test_prless_records_skipped() -> str:
    # Records that parse but carry no `pr` must be skipped, not (a) collapsed into one
    # via a None dedup key, nor (b) passed to the consumer where pr['pr'] would KeyError
    # and silently drop the whole /review reminder (review findings A1/A2).
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        _write_shard(proj, 5)
        sd = proj / "open-prs"
        (sd / "noprA.json").write_text(json.dumps({"url": URL.format(n=1), "topic": "no pr a"}), encoding="utf-8")
        (sd / "noprB.json").write_text(json.dumps({"url": URL.format(n=2), "topic": "no pr b"}), encoding="utf-8")
        entries = read_open_pr_entries(proj)
        assert [e["pr"] for e in entries] == [5], "only the record with a pr survives"
        assert all(e.get("pr") is not None for e in entries), "no pr-less record leaks to the consumer"
    return "pr-less records are skipped (no collapse, no downstream KeyError)"


# --- pending tile shards (ADR-118) -------------------------------------------


def _tile(issue, **over):
    e = {
        "issue": issue,
        "url": ISSUE_URL.format(n=issue),
        "title": f"Tile {issue}",
        "tldr": "does a thing",
        "prompt": "the full self-contained prompt",
        "cwd": "C:/Users/brown/Git/dev-env",
        "spawned": "2026-07-22",
    }
    e.update(over)
    return e


def _write_tile(project_dir: Path, issue, **over):
    td = project_dir / "tiles"
    td.mkdir(parents=True, exist_ok=True)
    p = td / f"{issue}.json"
    p.write_text(json.dumps(_tile(issue, **over)), encoding="utf-8")
    return p


def test_tiles_read_in_numeric_order() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        _write_tile(proj, 10)
        _write_tile(proj, 2)
        issues = [e["issue"] for e in read_tile_entries(proj)]
        assert issues == [2, 10], f"numeric order (not lexical '10' < '2'), got {issues}"
    return "tile shards are read in numeric issue order"


def test_tiles_missing_dir() -> str:
    with tempfile.TemporaryDirectory() as root:
        assert read_tile_entries(Path(root)) == [], "no tiles/ dir -> []"
    return "a project with no tiles/ directory yields []"


def test_tiles_malformed_skipped() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        _write_tile(proj, 5)
        (proj / "tiles" / "bad.json").write_text("{not json", encoding="utf-8")
        (proj / "tiles" / "index.json").write_text("{}", encoding="utf-8")
        issues = [e["issue"] for e in read_tile_entries(proj)]
        assert issues == [5], f"good shard kept, junk skipped, got {issues}"
    return "unparseable and non-numeric-named files are skipped; valid shards returned"


def test_tiles_issue_falls_back_to_filename() -> str:
    # The filename is the authoritative key (ADR-118). A shard missing the `issue` field —
    # or carrying a non-numeric one — must still be listed, since this path only reports.
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        td = proj / "tiles"
        td.mkdir(parents=True)
        no_field = _tile(7)
        del no_field["issue"]
        (td / "7.json").write_text(json.dumps(no_field), encoding="utf-8")
        non_numeric = _tile(8)
        non_numeric["issue"] = "not-an-int"
        (td / "8.json").write_text(json.dumps(non_numeric), encoding="utf-8")
        issues = [e["issue"] for e in read_tile_entries(proj)]
        assert issues == [7, 8], f"issue resolved from the filename, got {issues}"
    return "a missing or non-numeric `issue` field falls back to the filename, never dropped"


def test_tiles_read_is_non_destructive() -> str:
    # The /compact path must never unlink: pruning belongs to the session-start reconciler,
    # which is the only place that checks live issue state first.
    with tempfile.TemporaryDirectory() as root:
        proj = Path(root)
        shard = _write_tile(proj, 3)
        before = shard.read_bytes()
        read_tile_entries(proj)
        read_tile_entries(proj)
        assert shard.exists() and shard.read_bytes() == before, \
            "reading tiles on /compact must not modify or delete any shard"
    return "the /compact tile read is strictly read-only (no unlink, no rewrite)"


def test_format_pending_tiles_states_total_when_capped() -> str:
    entries = [_tile(n) for n in range(1, 15)]
    msg = format_pending_tiles(entries, max_shown=10)
    assert "Pending tiles (14)" in msg, "the true total is always stated"
    assert "and 4 more not shown" in msg and "10 of 14 listed" in msg, \
        f"a capped list must say what it withheld: {msg}"
    assert "list_sessions" in msg, "must direct Claude to dedupe before re-spawning"
    assert "the full self-contained prompt" not in msg, "payload stays on disk"
    return "the /compact tile block caps at max_shown but always states the true total"


def test_format_pending_tiles_empty() -> str:
    assert format_pending_tiles([]) == "", "no tiles -> no block at all"
    return "an empty tile list renders nothing"


def main() -> int:
    tests = [
        ("shards only", test_shards_only),
        ("legacy file only", test_legacy_only),
        ("union dedup (shard wins)", test_union_dedup_shard_wins),
        ("numeric shard ordering", test_numeric_shard_ordering),
        ("missing both -> []", test_missing_both),
        ("malformed shard skipped", test_malformed_shard_skipped),
        ("pr-less records skipped (A1/A2)", test_prless_records_skipped),
        ("tile shards read in numeric order", test_tiles_read_in_numeric_order),
        ("no tiles/ dir -> []", test_tiles_missing_dir),
        ("malformed tile shards skipped", test_tiles_malformed_skipped),
        ("tile issue falls back to filename", test_tiles_issue_falls_back_to_filename),
        ("tile read is non-destructive", test_tiles_read_is_non_destructive),
        ("tile block states total when capped", test_format_pending_tiles_states_total_when_capped),
        ("empty tile list renders nothing", test_format_pending_tiles_empty),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
