#!/usr/bin/env python3
"""Unit tests for _journal_shards — the shared open-PR shard/legacy reader (ADR-057).

ADR-056 split open-PR tracking into per-PR shards `sessions/<project>/open-prs/<N>.json`
plus a draining legacy `open-prs.jsonl`. `reconcile-open-prs.py` (needs the file paths to
`unlink`) and `post-compact.py` (needs the parsed entries) had each grown their own copy
of the enumerate+sort+parse+fold-legacy logic, which drifted once already (the shard sort
key was lexical in one and numeric in the other until PR #394's review). `_journal_shards`
is the single source of truth they now both import; these tests pin its behaviour offline
(tmp dirs, no network, no gh).

Usage:
    py -3 claude/scripts/tests/test_journal_shards.py

Exit 0 = all pass.
"""

import json
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _journal_shards import (  # noqa: E402
    iter_pr_shards,
    read_legacy_entries,
    shard_pr_number,
)

URL = "https://github.com/brownm09/dev-env/pull/{n}"


def _entry(pr):
    return {"pr": pr, "url": URL.format(n=pr), "topic": f"PR {pr}", "stub": "s.stub.md", "opened": "2026-06-22"}


def _write_shard(shard_dir: Path, pr, entry=None):
    shard_dir.mkdir(parents=True, exist_ok=True)
    p = shard_dir / f"{pr}.json"
    p.write_text(json.dumps(_entry(pr) if entry is None else entry), encoding="utf-8")
    return p


# --- shard_pr_number ---------------------------------------------------------


def test_shard_pr_number() -> str:
    assert shard_pr_number(Path("open-prs/386.json")) == 386
    assert shard_pr_number(Path("54.json")) == 54
    assert shard_pr_number(Path("index.json")) is None, "non-numeric stem -> ignored"
    assert shard_pr_number(Path("bad.json")) is None
    return "numeric stems parse to PR numbers; non-numeric stems -> None"


# --- iter_pr_shards ----------------------------------------------------------


def test_iter_returns_path_and_entry() -> str:
    with tempfile.TemporaryDirectory() as root:
        sd = Path(root) / "open-prs"
        p386 = _write_shard(sd, 386)
        got = iter_pr_shards(sd)
        assert len(got) == 1, f"one shard expected, got {len(got)}"
        path, entry = got[0]
        assert path == p386, "returns the real shard path (so reconcile can unlink it)"
        assert path.exists(), "the returned path is a real, existing file"
        assert entry["pr"] == 386, "returns the parsed entry alongside the path"
    return "iter yields (real_path, parsed_entry) pairs"


def test_iter_numeric_sort() -> str:
    with tempfile.TemporaryDirectory() as root:
        sd = Path(root) / "open-prs"
        _write_shard(sd, 10)
        _write_shard(sd, 2)
        _write_shard(sd, 100)
        prs = [e["pr"] for _p, e in iter_pr_shards(sd)]
        assert prs == [2, 10, 100], f"numeric order, not lexical '10'<'100'<'2', got {prs}"
    return "shards sorted by PR number ascending, not lexically"


def test_iter_skips_nonnumeric_names() -> str:
    with tempfile.TemporaryDirectory() as root:
        sd = Path(root) / "open-prs"
        _write_shard(sd, 5)
        # valid JSON object, but a non-numeric filename -> not a PR shard
        (sd / "index.json").write_text(json.dumps(_entry(1)), encoding="utf-8")
        prs = [e["pr"] for _p, e in iter_pr_shards(sd)]
        assert prs == [5], f"non-numeric-named file ignored even when valid, got {prs}"
    return "a non-numeric filename is not a PR shard, even when it parses"


def test_iter_skips_unparseable() -> str:
    with tempfile.TemporaryDirectory() as root:
        sd = Path(root) / "open-prs"
        _write_shard(sd, 7)
        (sd / "9.json").write_text("{not json", encoding="utf-8")
        prs = [e["pr"] for _p, e in iter_pr_shards(sd)]
        assert prs == [7], f"good shard kept, unparseable skipped, got {prs}"
    return "unparseable JSON shards are skipped (left for a human)"


def test_iter_skips_non_dict() -> str:
    # A numeric-named shard that parses to a JSON list/scalar must not reach a consumer:
    # entry.get(...) would raise, and the hooks only catch that in an outer guard that would
    # discard ALL of a project's open-PR context (the ADR-057 hardening).
    with tempfile.TemporaryDirectory() as root:
        sd = Path(root) / "open-prs"
        _write_shard(sd, 7)
        (sd / "8.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")   # JSON list
        (sd / "9.json").write_text(json.dumps("nope"), encoding="utf-8")      # JSON scalar
        prs = [e["pr"] for _p, e in iter_pr_shards(sd)]
        assert prs == [7], f"non-dict shards skipped, got {prs}"
    return "numeric shards that parse to a non-object are skipped defensively"


def test_iter_returns_dict_without_pr() -> str:
    # The primitive does NOT filter on entry contents — a numeric shard with no pr/url is
    # still returned; deciding what to do with it is the consumer's job (reconcile leaves it
    # in place, post-compact's dedup skips it). Pin that the reader stays content-agnostic.
    with tempfile.TemporaryDirectory() as root:
        sd = Path(root) / "open-prs"
        _write_shard(sd, 9, entry={"topic": "no pr/url"})
        got = iter_pr_shards(sd)
        assert len(got) == 1 and got[0][1] == {"topic": "no pr/url"}, f"got {got}"
    return "a parseable numeric dict shard is returned even with no pr/url (consumer decides)"


def test_iter_missing_or_nondir() -> str:
    with tempfile.TemporaryDirectory() as root:
        assert iter_pr_shards(Path(root) / "open-prs") == [], "missing dir -> []"
        f = Path(root) / "afile"
        f.write_text("x", encoding="utf-8")
        assert iter_pr_shards(f) == [], "a path that is a file, not a dir -> []"
    return "missing / non-directory shard dir -> [] (callable unconditionally)"


# --- read_legacy_entries -----------------------------------------------------


def test_legacy_reads_objects_in_order() -> str:
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "open-prs.jsonl"
        f.write_text(json.dumps(_entry(7)) + "\n" + json.dumps(_entry(9)) + "\n", encoding="utf-8")
        prs = [e["pr"] for e in read_legacy_entries(f)]
        assert prs == [7, 9], f"objects read in file order, got {prs}"
    return "legacy file: one object per line, in order"


def test_legacy_skips_blank_malformed_nondict() -> str:
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "open-prs.jsonl"
        f.write_text(
            "\n"                                   # blank line
            + json.dumps(_entry(7)) + "\n"
            + "   \n"                               # whitespace-only line
            + "{not json\n"                         # unparseable line
            + json.dumps([1, 2]) + "\n"            # non-object (list) line
            + json.dumps(_entry(9)) + "\n",
            encoding="utf-8",
        )
        prs = [e["pr"] for e in read_legacy_entries(f)]
        assert prs == [7, 9], f"blank/malformed/non-dict lines skipped, got {prs}"
    return "legacy reader skips blank, unparseable, and non-object lines"


def test_legacy_missing_file() -> str:
    with tempfile.TemporaryDirectory() as root:
        assert read_legacy_entries(Path(root) / "open-prs.jsonl") == [], "missing file -> []"
    return "missing legacy file -> [] (no path.exists() guard needed at call site)"


def main() -> int:
    tests = [
        ("shard_pr_number parsing", test_shard_pr_number),
        ("iter yields (path, entry)", test_iter_returns_path_and_entry),
        ("iter numeric sort", test_iter_numeric_sort),
        ("iter skips non-numeric names", test_iter_skips_nonnumeric_names),
        ("iter skips unparseable JSON", test_iter_skips_unparseable),
        ("iter skips non-dict shards", test_iter_skips_non_dict),
        ("iter returns dict shard without pr (content-agnostic)", test_iter_returns_dict_without_pr),
        ("iter missing/non-dir -> []", test_iter_missing_or_nondir),
        ("legacy reads objects in order", test_legacy_reads_objects_in_order),
        ("legacy skips blank/malformed/non-dict", test_legacy_skips_blank_malformed_nondict),
        ("legacy missing file -> []", test_legacy_missing_file),
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
