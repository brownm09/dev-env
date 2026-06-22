#!/usr/bin/env python3
"""Unit tests for post-compact.py open-PR reading (ADR-056).

`post-compact.py` reads the open-PR tracking on a manual `/compact` to remind
Claude to run `/review`. ADR-056 reshaped that tracking into per-PR shards
`sessions/<project>/open-prs/<N>.json`, with the pre-ADR-056 single
`open-prs.jsonl` still read during the transition. `read_open_pr_entries` unions
both, deduped by PR number — these tests pin that union, the numeric (not lexical)
shard ordering, and the malformed-input tolerance, all offline.

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

URL = "https://github.com/brownm09/dev-env/pull/{n}"


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


def main() -> int:
    tests = [
        ("shards only", test_shards_only),
        ("legacy file only", test_legacy_only),
        ("union dedup (shard wins)", test_union_dedup_shard_wins),
        ("numeric shard ordering", test_numeric_shard_ordering),
        ("missing both -> []", test_missing_both),
        ("malformed shard skipped", test_malformed_shard_skipped),
        ("pr-less records skipped (A1/A2)", test_prless_records_skipped),
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
