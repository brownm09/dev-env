#!/usr/bin/env python3
"""Unit tests for reconcile-pending-tiles.py (ADR-118, dev-env#869).

`reconcile-pending-tiles.py` is a UserPromptSubmit hook that, once per session, walks the
per-tile shards `sessions/<project>/tiles/<issue-number>.json`, unlinks the ones whose
paired GitHub issue is CLOSED, and surfaces an index of what is still pending so a
`spawn_task` chip lost to an app restart can be re-spawned.

Three properties carry real risk and get the most coverage here:

1. **URL validation gates a destructive path.** The remove branch `unlink`s the shard, so a
   mis-resolved `--repo` that answers CLOSED destroys a payload that cannot be
   reconstructed -- and `gh --repo` accepts a `HOST/OWNER/REPO` form, so an unvalidated URL
   is a redirect primitive. `test_repo_from_issue_url_*` pins the host check, the
   owner/repo character class, and that every rejection routes to skip-and-keep rather than
   to an unlink. This is stricter than the `reconcile-open-prs.py` precedent by design.

2. **The issue number comes from the FILENAME, never the URL** -- so even a URL that passes
   validation cannot redirect the lookup to a different issue. Pinned directly, plus the
   corrupt-shard case where the `issue` field contradicts the filename (skip-and-keep: the
   disagreement is itself evidence the shard cannot drive a delete).

3. **Cost scales with repo count, not shard count.** Shards accumulate un-pruned across the
   whole dormant window before this hook lands, so a per-shard `gh issue view` would fan
   out into N sequential subprocess spawns on the first prompt -- against a `gh` with
   documented quota-exhaustion history (dev-env#769). `test_lookup_states_one_fetch_per_repo`
   asserts the call count equals the number of distinct repos regardless of shard count.

Also pinned: the conservative keep-on-uncertainty contract (only a confirmed CLOSED
removes), that a survivor shard is left byte-identical (the ADR-056 no-clobber guarantee,
inherited), the race-tolerant empty-dir cleanup, and that the emitted message never
truncates silently.

`fetch_repo_issue_states` (the live `gh` boundary) is not tested -- subprocess boundary,
matching `reconcile-open-prs.py`'s `check_pr_state` convention and this repo's fixture-only
rule. The batching, budget, and failure handling *around* it are tested through
`lookup_states`' injectable `fetch`. Structural compliance (heartbeat, safe-exit fail-open,
output-contract channel, settings wiring) rides the shared gates, items 61/62/63/68, which
auto-discover the hook from `claude/settings.json`.

Usage:
    py -3 claude/scripts/tests/test_reconcile_pending_tiles.py

Exit 0 = all pass.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "reconcile-pending-tiles.py"

# The script imports _winsubp / _hookout / _hookutil / _journal_shards (siblings in
# scripts/); make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename -- import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("reconcile_pending_tiles", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

repo_from_issue_url = mod.repo_from_issue_url
should_remove_tile = mod.should_remove_tile
project_dirs = mod.project_dirs
make_tile = mod.make_tile
load_tiles = mod.load_tiles
group_numbers_by_repo = mod.group_numbers_by_repo
lookup_states = mod.lookup_states
reconcile_tiles = mod.reconcile_tiles
prune_empty_tile_dirs = mod.prune_empty_tile_dirs
tile_index_line = mod.tile_index_line
format_message = mod.format_message

REPO = "brownm09/dev-env"
URL = "https://github.com/brownm09/dev-env/issues/{n}"


def _entry(issue, **over):
    e = {
        "issue": issue,
        "url": URL.format(n=issue),
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
    p.write_text(json.dumps(_entry(issue, **over)), encoding="utf-8")
    return p


def _journal(root: Path, project="dev-env") -> Path:
    """Build `<root>/sessions/<project>/` and return it."""
    pd = root / "sessions" / project
    pd.mkdir(parents=True, exist_ok=True)
    return pd


# --- URL validation: the guard in front of a destructive path ----------------


def test_repo_from_issue_url_accepts_github() -> str:
    assert repo_from_issue_url(URL.format(n=869)) == REPO
    assert repo_from_issue_url("https://github.com/o/r") == "o/r", "no trailing path needed"
    assert repo_from_issue_url("https://github.com/o/r/issues/1?x=y#z") == "o/r"
    assert repo_from_issue_url("https://GitHub.COM/o/r/issues/1") == "o/r", \
        "host compare is case-insensitive (RFC 3986 s3.2.2 normalization, not a relaxation)"
    return "well-formed github.com issue URLs resolve to owner/repo"


def test_repo_from_issue_url_rejects_foreign_and_crafted_hosts() -> str:
    # `gh --repo` accepts HOST/OWNER/REPO, so a non-github host is a credential-redirect
    # primitive, not a cosmetic problem.
    for bad in [
        "https://evil.com/o/r/issues/1",
        "https://github.com.evil.com/o/r/issues/1",
        "https://user@github.com/o/r/issues/1",   # userinfo -> netloc != github.com
        "https://github.com:8080/o/r/issues/1",   # port -> netloc != github.com
        "https://raw.githubusercontent.com/o/r",
        "ssh://github.com/o/r",                   # non-http scheme still carries a netloc
    ]:
        assert repo_from_issue_url(bad) is None, f"must reject foreign/crafted host: {bad}"
    return "any netloc that is not exactly github.com is rejected (incl. userinfo, port, subdomain)"


def test_repo_from_issue_url_rejects_bad_owner_repo_chars() -> str:
    for bad in [
        "https://github.com/o r/repo",        # space
        "https://github.com/o;rm -rf/repo",    # shell metacharacters
        "https://github.com/o/re:po",          # colon -> could read as HOST/OWNER/REPO
        "https://github.com/../../etc/passwd",
        "https://github.com/o",                # only one path segment
        "https://github.com/",                 # no segments
    ]:
        assert repo_from_issue_url(bad) is None, f"must reject: {bad}"
    return "owner/repo must match ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$; anything else rejected"


def test_repo_from_issue_url_rejects_non_strings_and_garbage() -> str:
    for bad in [None, "", 42, [], {}, "not a url", "github.com/o/r"]:
        assert repo_from_issue_url(bad) is None, f"must reject: {bad!r}"
    return "None/non-str/empty/scheme-less input returns None rather than raising"


def test_invalid_url_shard_is_skipped_never_unlinked() -> str:
    # The load-bearing consequence of validation: a shard whose URL fails must survive even
    # when the state oracle would say CLOSED for everything.
    with tempfile.TemporaryDirectory() as root:
        proj = _journal(Path(root))
        shard = _write_tile(proj, 5, url="https://evil.com/o/r/issues/5")
        tiles = load_tiles(Path(root))
        pending, removed, skipped = reconcile_tiles(
            tiles, {(REPO, 5): "CLOSED"})
        assert shard.exists(), "a shard with an unvalidated URL must never be unlinked"
        assert (pending, removed) == ([], []), "it is neither pending nor removed"
        assert len(skipped) == 1 and skipped[0]["skip"] == "url failed validation"
    return "url validation failure -> skip-and-keep; the payload survives a CLOSED oracle"


# --- issue number comes from the filename ------------------------------------


def test_issue_number_taken_from_filename_not_url() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = _journal(Path(root))
        # Filename says 42; the URL claims a different issue and the field is absent.
        td = proj / "tiles"
        td.mkdir(parents=True)
        payload = _entry(42)
        payload["url"] = "https://github.com/brownm09/dev-env/issues/999"
        del payload["issue"]
        (td / "42.json").write_text(json.dumps(payload), encoding="utf-8")

        tile = load_tiles(Path(root))[0]
        assert tile["issue"] == 42, f"filename is authoritative, got {tile['issue']}"
        assert tile["repo"] == REPO, "repo still resolves from the URL"
        assert tile["skip"] is None
        assert group_numbers_by_repo([tile]) == {REPO: [42]}, "42 is looked up, not 999"
    return "the issue number is read from the filename; the URL cannot redirect it"


def test_issue_field_disagreeing_with_filename_is_skipped() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = _journal(Path(root))
        shard = _write_tile(proj, 7)
        # Corrupt the field so it contradicts the filename.
        data = json.loads(shard.read_text(encoding="utf-8"))
        data["issue"] = 8
        shard.write_text(json.dumps(data), encoding="utf-8")

        tiles = load_tiles(Path(root))
        pending, removed, skipped = reconcile_tiles(tiles, {(REPO, 7): "CLOSED"})
        assert shard.exists(), "a self-contradictory shard must not be unlinked"
        assert (pending, removed) == ([], [])
        assert len(skipped) == 1 and "disagrees with filename" in skipped[0]["skip"]
    return "an `issue` field contradicting the filename -> skip-and-keep (corrupt, untrusted)"


# --- conservative keep/remove contract ---------------------------------------


def test_should_remove_tile() -> str:
    assert should_remove_tile("CLOSED") is True
    assert should_remove_tile("OPEN") is False
    assert should_remove_tile(None) is False, "gh failure (None) is conservative -- keep"
    assert should_remove_tile("") is False
    assert should_remove_tile("closed") is False, "state vocabulary is uppercase; no case-folding"
    assert should_remove_tile("MERGED") is False, "issues never merge -- unknown state -> keep"
    return "only a confirmed CLOSED removes; OPEN/None/unknown/lowercase -> keep"


def test_removes_only_closed_and_leaves_survivor_byte_identical() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = _journal(Path(root))
        closed = _write_tile(proj, 860)
        survivor = _write_tile(proj, 869)
        survivor_bytes = survivor.read_bytes()

        tiles = load_tiles(Path(root))
        pending, removed, skipped = reconcile_tiles(
            tiles, {(REPO, 860): "CLOSED", (REPO, 869): "OPEN"})

        assert not closed.exists(), "closed-issue shard must be unlinked"
        assert survivor.exists(), "open-issue shard must remain"
        assert survivor.read_bytes() == survivor_bytes, \
            "survivor must be byte-identical (per-file unlink, never a rewrite)"
        assert [t["issue"] for t in pending] == [869]
        assert [t["issue"] for t in removed] == [860]
        assert skipped == []
    return "closed shard unlinked; the survivor file is untouched byte-for-byte (ADR-056 no-clobber)"


def test_unresolved_state_keeps_shard() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = _journal(Path(root))
        shard = _write_tile(proj, 100)
        tiles = load_tiles(Path(root))
        # Empty state map == every lookup unresolved (gh failure / outside window).
        pending, removed, skipped = reconcile_tiles(tiles, {})
        assert shard.exists(), "an unresolved tile must be kept"
        assert [t["issue"] for t in pending] == [100] and removed == [] and skipped == []
    return "a missing/None state (gh failure, budget spent, outside window) keeps the shard"


# --- shard discovery ----------------------------------------------------------


def test_project_dirs() -> str:
    with tempfile.TemporaryDirectory() as root:
        rootp = Path(root)
        (rootp / "sessions" / "dev-env").mkdir(parents=True)
        (rootp / "sessions" / "career-playbook").mkdir(parents=True)
        (rootp / "sessions" / "note.txt").write_text("x", encoding="utf-8")
        got = [p.name for p in project_dirs(rootp)]
        assert got == ["career-playbook", "dev-env"], f"sorted project dirs, got {got}"
    with tempfile.TemporaryDirectory() as root2:
        assert project_dirs(Path(root2)) == [], "no sessions/ -> []"
    return "project_dirs lists sorted sessions/<project>/ dirs; [] when absent"


def test_load_tiles_across_projects_and_tolerates_junk() -> str:
    with tempfile.TemporaryDirectory() as root:
        rootp = Path(root)
        a = _journal(rootp, "dev-env")
        b = _journal(rootp, "career-playbook")
        _write_tile(a, 10)
        _write_tile(a, 2)
        _write_tile(b, 30)
        # Junk the shared reader must filter out, and must never delete.
        (a / "tiles" / "bad.json").write_text("{not json", encoding="utf-8")
        (a / "tiles" / "index.json").write_text("{}", encoding="utf-8")
        (a / "tiles" / "9.json").write_text("[1,2]", encoding="utf-8")  # non-object

        tiles = load_tiles(rootp)
        got = [(t["project"], t["issue"]) for t in tiles]
        assert got == [("career-playbook", 30), ("dev-env", 2), ("dev-env", 10)], \
            f"projects sorted, shards numerically sorted within each, got {got}"
        assert (a / "tiles" / "bad.json").exists(), "unparseable shard left for a human"
        assert (a / "tiles" / "index.json").exists(), "non-numeric name ignored, not deleted"
        assert (a / "tiles" / "9.json").exists(), "non-object shard left in place"
    return "tiles read across all projects, numerically sorted; junk skipped and never deleted"


def test_load_tiles_missing_dirs() -> str:
    with tempfile.TemporaryDirectory() as root:
        assert load_tiles(Path(root)) == [], "no sessions/ -> []"
        _journal(Path(root))  # project dir exists but has no tiles/
        assert load_tiles(Path(root)) == [], "project without tiles/ -> []"
    return "missing sessions/ or missing tiles/ yields [] with no error"


# --- batching: cost scales with repos, not shards ----------------------------


def test_group_numbers_by_repo_dedups_and_excludes_skipped() -> str:
    with tempfile.TemporaryDirectory() as root:
        rootp = Path(root)
        a = _journal(rootp, "dev-env")
        b = _journal(rootp, "career-playbook")
        _write_tile(a, 3)
        _write_tile(a, 1)
        _write_tile(b, 5, url="https://github.com/brownm09/career-playbook/issues/5")
        _write_tile(b, 6, url="https://evil.com/x/y/issues/6")   # skipped
        got = group_numbers_by_repo(load_tiles(rootp))
        assert got == {"brownm09/career-playbook": [5], REPO: [1, 3]}, got
    return "grouped by repo, numbers sorted+deduped, skipped tiles excluded from lookup"


def test_lookup_states_one_fetch_per_repo() -> str:
    # The cold-start guard: N shards across R repos must cost R calls, not N.
    calls = []

    def fake_fetch(repo):
        calls.append(repo)
        return {n: "OPEN" for n in range(1, 100)}

    by_repo = {"o/a": [1, 2, 3, 4, 5, 6], "o/b": [7, 8, 9]}
    states = lookup_states(by_repo, fetch=fake_fetch)
    assert calls == ["o/a", "o/b"], f"one fetch per repo, got {calls}"
    assert len(states) == 9, "every requested (repo, issue) pair resolved"
    assert states[("o/a", 1)] == "OPEN"
    return "9 shards across 2 repos cost exactly 2 gh calls (O(repos), not O(shards))"


def test_lookup_states_fetch_failure_yields_none_for_that_repo_only() -> str:
    def fake_fetch(repo):
        return None if repo == "o/a" else {7: "CLOSED"}

    states = lookup_states({"o/a": [1], "o/b": [7]}, fetch=fake_fetch)
    assert states[("o/a", 1)] is None, "a failed fetch must not poison into a CLOSED"
    assert states[("o/b", 7)] == "CLOSED", "an unrelated repo still resolves"
    return "a per-repo fetch failure yields None for that repo only; others unaffected"


def test_lookup_states_missing_number_is_none_not_closed() -> str:
    # An issue outside the page window (or deleted/transferred) is absent from the result.
    # Absence must never be read as "closed" -- that would unlink a live tile's payload.
    states = lookup_states({"o/a": [1, 2]}, fetch=lambda repo: {1: "OPEN"})
    assert states[("o/a", 1)] == "OPEN"
    assert states[("o/a", 2)] is None, "absent from the page window -> unresolved, not CLOSED"
    return "an issue missing from the fetched page resolves to None (kept), never CLOSED"


def test_lookup_states_non_dict_fetch_result_is_tolerated() -> str:
    states = lookup_states({"o/a": [1]}, fetch=lambda repo: ["unexpected"])
    assert states[("o/a", 1)] is None, "a non-dict fetch result degrades to unresolved"
    return "a malformed fetch return value degrades to unresolved rather than raising"


def test_lookup_states_budget_stops_further_fetches() -> str:
    # A slow/hanging gh must degrade to "unresolved, all kept" rather than let the hook be
    # killed mid-flight (which would lose the whole index, prunes included).
    calls = []
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])

    def fake_clock():
        return next(ticks)

    def fake_fetch(repo):
        calls.append(repo)
        return {1: "CLOSED", 7: "CLOSED"}

    states = lookup_states({"o/a": [1], "o/b": [7]}, fetch=fake_fetch,
                           budget=10.0, clock=fake_clock)
    assert calls == ["o/a"], f"the over-budget repo must not be fetched, got {calls}"
    assert states[("o/a", 1)] == "CLOSED"
    assert states[("o/b", 7)] is None, "the skipped repo's tiles are unresolved -> kept"
    return "the wall-clock budget stops further fetches; skipped repos resolve to None (kept)"


def test_lookup_states_empty_plan_makes_no_calls() -> str:
    calls = []
    states = lookup_states({}, fetch=lambda r: calls.append(r) or {})
    assert states == {} and calls == [], "no tiles -> no gh calls at all"
    return "an empty lookup plan issues zero subprocess calls"


# --- directory cleanup --------------------------------------------------------


def test_prune_removes_emptied_tiles_dir_only() -> str:
    with tempfile.TemporaryDirectory() as root:
        rootp = Path(root)
        a = _journal(rootp, "dev-env")
        b = _journal(rootp, "career-playbook")
        _write_tile(a, 1)
        _write_tile(b, 2)
        _write_tile(b, 3)

        tiles = load_tiles(rootp)
        _, removed, _ = reconcile_tiles(tiles, {
            (REPO, 1): "CLOSED",
            ("brownm09/career-playbook", 2): "CLOSED",
            ("brownm09/career-playbook", 3): "OPEN",
        })
        # career-playbook's URLs default to dev-env, so re-point expectations: only assert
        # the dirs, which is what this test is about.
        prune_empty_tile_dirs(removed)
        assert not (a / "tiles").exists(), "emptied tiles/ dir removed"
        assert (b / "tiles").exists(), "a tiles/ dir with a survivor is kept"
    return "an emptied tiles/ dir is removed; one with a surviving shard is left alone"


def test_prune_leaves_dir_when_a_shard_reappears() -> str:
    # Race tolerance: a concurrent session writing a new shard between the emptiness check
    # and the rmdir must not lose it. Simulated by leaving an unrelated file behind.
    with tempfile.TemporaryDirectory() as root:
        proj = _journal(Path(root))
        shard = _write_tile(proj, 1)
        tiles = load_tiles(Path(root))
        _, removed, _ = reconcile_tiles(tiles, {(REPO, 1): "CLOSED"})
        assert not shard.exists()
        (proj / "tiles" / "99.json").write_text(json.dumps(_entry(99)), encoding="utf-8")
        prune_empty_tile_dirs(removed)
        assert (proj / "tiles").exists(), "a dir that is no longer empty must survive rmdir"
        assert (proj / "tiles" / "99.json").exists(), "the concurrent shard is never lost"
    return "rmdir is race-tolerant -- a shard written after the unlink is never destroyed"


def test_prune_with_no_removals_touches_nothing() -> str:
    with tempfile.TemporaryDirectory() as root:
        proj = _journal(Path(root))
        (proj / "tiles").mkdir()
        prune_empty_tile_dirs([])
        assert (proj / "tiles").exists(), \
            "an empty tiles/ dir this run did not empty (e.g. one a concurrent session just created) is untouched"
    return "pruning is scoped to dirs this run emptied -- an unrelated empty tiles/ survives"


# --- message formatting: no silent truncation --------------------------------


def test_tile_index_line_is_index_not_payload() -> str:
    tile = make_tile("dev-env", Path("x/tiles/869.json"), _entry(869))
    line = tile_index_line(tile)
    for expected in ["dev-env", "#869", "Tile 869", "2026-07-22", "sessions/dev-env/tiles/869.json"]:
        assert expected in line, f"index line must carry {expected!r}: {line}"
    assert "the full self-contained prompt" not in line, "the payload must stay on disk"
    assert "does a thing" not in line, "tldr is payload too -- not in the index"
    return "the index row carries project/issue/title/spawned/path -- never prompt or tldr"


def test_tile_index_line_truncates_long_title() -> str:
    tile = make_tile("dev-env", Path("x/tiles/1.json"), _entry(1, title="T" * 200))
    line = tile_index_line(tile)
    assert "..." in line and len(line) < 200, f"long title must be truncated: {len(line)}"
    return "an over-long title is truncated in the index (the shard keeps the full value)"


def test_format_message_states_total_when_capped() -> str:
    tiles = [make_tile("dev-env", Path(f"x/tiles/{n}.json"), _entry(n)) for n in range(1, 15)]
    msg = format_message(tiles, [], [], unresolved=0, max_shown=10)
    assert "Pending tiles (14)" in msg, "the true total is always stated"
    assert "and 4 more not shown" in msg and "10 of 14 listed" in msg, \
        f"a capped list must say what it withheld: {msg}"
    assert msg.count("sessions/dev-env/tiles/") == 10, "exactly max_shown rows rendered"
    return "a capped index states the true total and the withheld count -- no silent truncation"


def test_format_message_mentions_list_sessions_check() -> str:
    tiles = [make_tile("dev-env", Path("x/tiles/1.json"), _entry(1))]
    msg = format_message(tiles, [], [], unresolved=0)
    assert "list_sessions" in msg, "must tell Claude to dedupe against running sessions"
    return "the advisory directs Claude to check list_sessions before re-spawning (ADR-118 caveat)"


def test_format_message_reports_removals_unresolved_and_skips() -> str:
    pending = [make_tile("dev-env", Path("x/tiles/1.json"), _entry(1))]
    removed = [make_tile("dev-env", Path("x/tiles/2.json"), _entry(2))]
    skipped = [make_tile("dev-env", Path("x/tiles/3.json"), _entry(3, url="https://evil.com/a/b"))]
    msg = format_message(pending, removed, skipped, unresolved=1)
    assert "Pruned 1 tile shard(s)" in msg and "dev-env #2" in msg
    assert "1 pending tile(s) could not be resolved" in msg
    assert "1 tile shard(s) skipped and kept" in msg and "url failed validation" in msg
    return "prunes, unresolved counts, and skipped shards are each reported explicitly"


def test_format_message_empty_when_nothing_to_report() -> str:
    assert format_message([], [], [], unresolved=0) == "", \
        "no tiles, no prunes, nothing skipped -> emit nothing (stay silent)"
    return "an empty reconciliation emits no message at all"


def test_format_message_is_ascii() -> str:
    # The emission rides _hookout's JSON channel (ensure_ascii=True), so this is insurance
    # rather than a live requirement -- but it keeps the text safe if the channel ever moves
    # to the raw exit-2 stderr path, which is cp1252-decoded on Windows.
    tiles = [make_tile("dev-env", Path("x/tiles/1.json"), _entry(1))]
    msg = format_message(tiles, tiles, tiles, unresolved=2)
    assert msg.isascii(), "message must be ASCII-only"
    return "the rendered message is ASCII-only (cp1252-safe on the raw-stream path)"


# --- output contract ----------------------------------------------------------


def test_emits_model_visible_stdout_at_exit_zero() -> str:
    # The ADR-098 bug class, asserted directly: on UserPromptSubmit an exit-0 advisory is
    # model-visible ONLY on stdout. If this ever routes to stderr, or to a systemMessage
    # (the *user* toast), Claude never sees the pending-tile index and the whole feature is
    # silently inert -- which is exactly how dev-env-sync's warnings hid for months.
    import _hookout
    emission = _hookout.plan_emission("UserPromptSubmit", "hello", audience="model")
    assert emission.exit_code == 0, "advisory must not block the prompt"
    assert emission.stderr is None, "stderr is NOT surfaced on UserPromptSubmit at exit 0"
    assert emission.stdout is not None, "the advisory must ride stdout"
    payload = json.loads(emission.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert payload["hookSpecificOutput"]["additionalContext"] == "hello", \
        "must reach the model as additionalContext"
    assert "systemMessage" not in payload, \
        "systemMessage is the user toast -- the tile index is for Claude to act on"
    return "advisory rides exit-0 stdout as model-visible additionalContext, never stderr (ADR-098)"


def test_hook_call_site_uses_model_audience_on_userpromptsubmit() -> str:
    # Pin the actual call site, not just the emitter: an AST scan proves the hook asks for
    # the (UserPromptSubmit, model) channel with a *literal* event, per _hookout's migration
    # note -- a dynamic event would make plan_emission raise into the fail-open guard and
    # vanish, while still passing an end-to-end "emitted nothing" test.
    import ast
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "emit_advisory"):
            event = node.args[0] if node.args else None
            audience = next((k.value for k in node.keywords if k.arg == "audience"), None)
            found.append((
                event.value if isinstance(event, ast.Constant) else None,
                audience.value if isinstance(audience, ast.Constant) else None,
            ))
    assert found == [("UserPromptSubmit", "model")], \
        f"expected exactly one emit_advisory('UserPromptSubmit', ..., audience='model'), got {found}"
    return "the single call site passes a literal 'UserPromptSubmit' with audience='model'"


def main() -> int:
    tests = [
        ("url validation accepts github.com", test_repo_from_issue_url_accepts_github),
        ("url validation rejects foreign/crafted hosts", test_repo_from_issue_url_rejects_foreign_and_crafted_hosts),
        ("url validation rejects bad owner/repo chars", test_repo_from_issue_url_rejects_bad_owner_repo_chars),
        ("url validation rejects non-strings/garbage", test_repo_from_issue_url_rejects_non_strings_and_garbage),
        ("invalid url -> skip-and-keep, never unlink", test_invalid_url_shard_is_skipped_never_unlinked),
        ("issue number from filename, not url", test_issue_number_taken_from_filename_not_url),
        ("issue field disagreeing with filename skipped", test_issue_field_disagreeing_with_filename_is_skipped),
        ("should_remove_tile predicate", test_should_remove_tile),
        ("closed unlinked, survivor byte-identical", test_removes_only_closed_and_leaves_survivor_byte_identical),
        ("unresolved state keeps the shard", test_unresolved_state_keeps_shard),
        ("project_dirs discovery", test_project_dirs),
        ("load_tiles across projects, junk tolerated", test_load_tiles_across_projects_and_tolerates_junk),
        ("load_tiles with missing dirs", test_load_tiles_missing_dirs),
        ("group_numbers_by_repo dedups/excludes skipped", test_group_numbers_by_repo_dedups_and_excludes_skipped),
        ("ONE gh call per repo, not per shard", test_lookup_states_one_fetch_per_repo),
        ("fetch failure scoped to its repo", test_lookup_states_fetch_failure_yields_none_for_that_repo_only),
        ("missing number -> None, never CLOSED", test_lookup_states_missing_number_is_none_not_closed),
        ("non-dict fetch result tolerated", test_lookup_states_non_dict_fetch_result_is_tolerated),
        ("wall-clock budget stops further fetches", test_lookup_states_budget_stops_further_fetches),
        ("empty plan makes no calls", test_lookup_states_empty_plan_makes_no_calls),
        ("emptied tiles/ dir pruned, others kept", test_prune_removes_emptied_tiles_dir_only),
        ("rmdir is race-tolerant", test_prune_leaves_dir_when_a_shard_reappears),
        ("no removals -> nothing pruned", test_prune_with_no_removals_touches_nothing),
        ("index row is index, not payload", test_tile_index_line_is_index_not_payload),
        ("long title truncated in index", test_tile_index_line_truncates_long_title),
        ("capped list states its total", test_format_message_states_total_when_capped),
        ("advisory names the list_sessions check", test_format_message_mentions_list_sessions_check),
        ("removals/unresolved/skips all reported", test_format_message_reports_removals_unresolved_and_skips),
        ("nothing to report -> empty message", test_format_message_empty_when_nothing_to_report),
        ("message is ASCII-only", test_format_message_is_ascii),
        ("exit-0 stdout is the model channel (ADR-098)", test_emits_model_visible_stdout_at_exit_zero),
        ("call site uses literal event + model audience", test_hook_call_site_uses_model_audience_on_userpromptsubmit),
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
