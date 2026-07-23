#!/usr/bin/env python3
"""Unit tests for _journal_shards — the shared numeric-shard/legacy reader (ADR-057, ADR-118).

ADR-056 split open-PR tracking into per-PR shards `sessions/<project>/open-prs/<N>.json`
plus a draining legacy `open-prs.jsonl`. `reconcile-open-prs.py` (needs the file paths to
`unlink`) and `post-compact.py` (needs the parsed entries) had each grown their own copy
of the enumerate+sort+parse+fold-legacy logic, which drifted once already (the shard sort
key was lexical in one and numeric in the other until PR #394's review). `_journal_shards`
is the single source of truth they now both import; these tests pin its behaviour offline
(tmp dirs, no network, no gh).

ADR-118 added tile shards (`sessions/<project>/tiles/<issue-number>.json`) on the identical
numeric layout, so the reader generalised to `iter_numeric_shards` with `iter_pr_shards` /
`iter_tile_shards` as named delegations. `test_all_shard_readers_are_one_implementation`
is the load-bearing pin there: it fails if anyone re-specialises an entry point and
reintroduces the very drift this module was extracted to end.

dev-env#881 hoisted `project_dirs` — the walk over `sessions/<project>/` that both reconcile
hooks run *before* reading either shard kind — into this module after it had been copy-pasted a
third time. Its behaviour is pinned here; each hook's own test keeps a one-line identity pin
that the hook still resolves to this copy rather than a reintroduced local one.

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
    iter_numeric_shards,
    iter_pr_shards,
    iter_tile_shards,
    project_dirs,
    read_legacy_entries,
    shard_number,
    shard_pr_number,
)

URL = "https://github.com/brownm09/dev-env/pull/{n}"
ISSUE_URL = "https://github.com/brownm09/dev-env/issues/{n}"


def _entry(pr):
    return {"pr": pr, "url": URL.format(n=pr), "topic": f"PR {pr}", "stub": "s.stub.md", "opened": "2026-06-22"}


def _tile_entry(issue):
    return {
        "issue": issue,
        "url": ISSUE_URL.format(n=issue),
        "title": f"Tile {issue}",
        "tldr": "A follow-up.",
        "prompt": "Do the thing.",
        "cwd": "C:/Users/brown/Git/dev-env",
        "stub": "s.stub.md",
        "spawned": "2026-07-22",
    }


def _write_shard(shard_dir: Path, pr, entry=None):
    shard_dir.mkdir(parents=True, exist_ok=True)
    p = shard_dir / f"{pr}.json"
    p.write_text(json.dumps(_entry(pr) if entry is None else entry), encoding="utf-8")
    return p


def _write_tile_shard(shard_dir: Path, issue, entry=None):
    shard_dir.mkdir(parents=True, exist_ok=True)
    p = shard_dir / f"{issue}.json"
    p.write_text(json.dumps(_tile_entry(issue) if entry is None else entry), encoding="utf-8")
    return p


# --- project_dirs (the walk one level above the readers) ---------------------


def test_project_dirs() -> str:
    with tempfile.TemporaryDirectory() as root:
        rootp = Path(root)
        (rootp / "sessions" / "dev-env").mkdir(parents=True)
        (rootp / "sessions" / "career-playbook").mkdir(parents=True)
        (rootp / "sessions" / "note.txt").write_text("x", encoding="utf-8")  # not a dir
        got = [p.name for p in project_dirs(rootp)]
        assert got == ["career-playbook", "dev-env"], f"sorted project dirs, got {got}"
    with tempfile.TemporaryDirectory() as root2:
        assert project_dirs(Path(root2)) == [], "no sessions/ -> []"
    return "project_dirs lists sorted sessions/<project>/ dirs; [] when absent"


def test_project_dirs_returns_paths_under_sessions() -> str:
    # The callers join a shard-kind subdirectory onto each returned path
    # (`<project>/open-prs`, `<project>/tiles`), so what is returned must be the project
    # directory itself, not a bare name — a `[p.name for p in ...]` assertion alone would
    # pass for a helper that returned strings and break both hooks.
    with tempfile.TemporaryDirectory() as root:
        rootp = Path(root)
        (rootp / "sessions" / "dev-env" / "tiles").mkdir(parents=True)
        got = project_dirs(rootp)
        assert got == [rootp / "sessions" / "dev-env"], f"full paths under sessions/, got {got}"
        assert (got[0] / "tiles").is_dir(), "returned path joins to a real shard dir"
    return "project_dirs returns full Paths that join to a shard dir, not bare names"


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


def test_iter_skips_non_utf8() -> str:
    # dev-env#804: path.read_text(encoding="utf-8") raises UnicodeDecodeError (a
    # ValueError, not an OSError) on a non-UTF-8 shard, which the pre-fix except
    # tuple let escape uncaught.
    with tempfile.TemporaryDirectory() as root:
        sd = Path(root) / "open-prs"
        _write_shard(sd, 7)
        (sd / "9.json").write_bytes(b"\xff\xfe\x00\x9d")
        prs = [e["pr"] for _p, e in iter_pr_shards(sd)]
        assert prs == [7], f"good shard kept, non-UTF-8 shard skipped, got {prs}"
    return "a non-UTF-8 shard is skipped (UnicodeDecodeError caught, dev-env#804)"


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


# --- tile shards (ADR-118) ---------------------------------------------------


def test_shard_number_generic() -> str:
    # shard_number is the real parse; shard_pr_number is a retained alias for
    # journal-shard-write-advisory.py. Both must agree, or the advisory's
    # filename-vs-field cross-check would disagree with the reader that enumerates it.
    assert shard_number(Path("tiles/868.json")) == 868
    assert shard_number(Path("open-prs/386.json")) == 386
    assert shard_number(Path("index.json")) is None
    for name in ("tiles/868.json", "open-prs/386.json", "index.json", "bad.json", "12.json"):
        assert shard_number(Path(name)) == shard_pr_number(Path(name)), f"alias diverged on {name}"
    return "shard_number parses any <N>.json; shard_pr_number is an exact alias of it"


def test_iter_tile_shards_reads_tiles_dir() -> str:
    with tempfile.TemporaryDirectory() as root:
        td = Path(root) / "tiles"
        p868 = _write_tile_shard(td, 868)
        got = iter_tile_shards(td)
        assert len(got) == 1, f"one tile shard expected, got {len(got)}"
        path, entry = got[0]
        assert path == p868, "returns the real shard path (so reconcile can unlink it)"
        assert entry["issue"] == 868, "returns the parsed tile entry"
        assert entry["prompt"] == "Do the thing.", "the spawn payload survives the round-trip"
    return "iter_tile_shards yields (real_path, parsed_tile_entry) pairs"


def test_iter_tile_shards_numeric_sort() -> str:
    # Tile shards are keyed by paired ISSUE number, which spans the same wide range as PR
    # numbers — so the lexical-sort bug ADR-057 fixed for PRs would bite identically here.
    with tempfile.TemporaryDirectory() as root:
        td = Path(root) / "tiles"
        _write_tile_shard(td, 10)
        _write_tile_shard(td, 2)
        _write_tile_shard(td, 100)
        issues = [e["issue"] for _p, e in iter_tile_shards(td)]
        assert issues == [2, 10, 100], f"numeric order, not lexical, got {issues}"
    return "tile shards sorted by issue number ascending, not lexically"


def test_all_shard_readers_are_one_implementation() -> str:
    # THE anti-drift pin. ADR-057 exists because two copies of "glob, sort, parse" drifted
    # (lexical vs numeric sort). ADR-118 added a second shard kind on the same layout, so the
    # temptation to give tiles their own reader is exactly the mistake to prevent. If someone
    # later "specializes" one entry point, this fails.
    with tempfile.TemporaryDirectory() as root:
        d = Path(root) / "shards"
        _write_shard(d, 10)
        _write_shard(d, 2)
        (d / "index.json").write_text(json.dumps({"x": 1}), encoding="utf-8")  # non-numeric
        (d / "9.json").write_text("{not json", encoding="utf-8")               # unparseable
        (d / "8.json").write_text(json.dumps([1, 2]), encoding="utf-8")        # non-dict
        base = iter_numeric_shards(d)
        assert iter_pr_shards(d) == base, "iter_pr_shards diverged from iter_numeric_shards"
        assert iter_tile_shards(d) == base, "iter_tile_shards diverged from iter_numeric_shards"
        # Assert WHICH shards survived, not just how many. The three equality checks above
        # compare the entry points to each other, so a *uniform* regression (all three
        # returning the malformed shards, or the right ones misordered) is invisible to
        # them — only pinning the identity and order of the survivors catches that.
        assert [e["pr"] for _p, e in base] == [2, 10], f"shared core still filters+sorts, got {base}"
    return "iter_pr_shards / iter_tile_shards / iter_numeric_shards agree exactly (anti-drift)"


def test_iter_tile_shards_missing_dir() -> str:
    # The common case on every session before any tile is ever spawned: no tiles/ dir at all.
    # reconcile-pending-tiles.py calls this unconditionally per project, so it must not raise.
    with tempfile.TemporaryDirectory() as root:
        assert iter_tile_shards(Path(root) / "tiles") == [], "missing tiles dir -> []"
    return "missing tiles/ dir -> [] (callable unconditionally, the pre-first-tile case)"


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


def test_legacy_non_utf8_file() -> str:
    # dev-env#804: same UnicodeDecodeError-escapes-the-except-tuple bug as
    # test_iter_skips_non_utf8, for the legacy single-file reader.
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "open-prs.jsonl"
        f.write_bytes(b"\xff\xfe\x00\x9d")
        assert read_legacy_entries(f) == [], "non-UTF-8 file -> []"
    return "non-UTF-8 legacy file -> [] (UnicodeDecodeError caught, dev-env#804)"


def main() -> int:
    tests = [
        ("project_dirs discovery", test_project_dirs),
        ("project_dirs returns joinable paths", test_project_dirs_returns_paths_under_sessions),
        ("shard_pr_number parsing", test_shard_pr_number),
        ("iter yields (path, entry)", test_iter_returns_path_and_entry),
        ("iter numeric sort", test_iter_numeric_sort),
        ("iter skips non-numeric names", test_iter_skips_nonnumeric_names),
        ("iter skips unparseable JSON", test_iter_skips_unparseable),
        ("iter skips non-UTF-8 shard", test_iter_skips_non_utf8),
        ("iter skips non-dict shards", test_iter_skips_non_dict),
        ("iter returns dict shard without pr (content-agnostic)", test_iter_returns_dict_without_pr),
        ("iter missing/non-dir -> []", test_iter_missing_or_nondir),
        ("shard_number generic + shard_pr_number alias", test_shard_number_generic),
        ("iter_tile_shards reads tiles dir", test_iter_tile_shards_reads_tiles_dir),
        ("iter_tile_shards numeric sort", test_iter_tile_shards_numeric_sort),
        ("all shard readers are one implementation", test_all_shard_readers_are_one_implementation),
        ("iter_tile_shards missing dir -> []", test_iter_tile_shards_missing_dir),
        ("legacy reads objects in order", test_legacy_reads_objects_in_order),
        ("legacy skips blank/malformed/non-dict", test_legacy_skips_blank_malformed_nondict),
        ("legacy missing file -> []", test_legacy_missing_file),
        ("legacy non-UTF-8 file -> []", test_legacy_non_utf8_file),
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
