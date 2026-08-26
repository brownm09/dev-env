#!/usr/bin/env python3
"""Unit tests for reconcile-project-board.py pure helpers (dev-env #447).

`reconcile-project-board.py` is the backstop for the gap ADR-053 documents: PostToolUse
hooks are inert in background/spawn_task sessions, so issues filed there are never added
to the project board. The script lists open issues + board items, computes the set
difference (orphans), adds them, and reports the issues still missing a required field —
**without ever guessing the field value**. These tests pin that contract offline:

  - the set-difference core (open issues - board issues = orphans),
  - the board-membership and missing-required-field detection (Impact/Why),
  - the canonical-worktree-root resolution that lets a worktree invocation find the
    machine-local hook-config,
  - that render_report emits `gh project item-edit` *commands* but assigns no value
    (no-guessing) and ends in the machine-readable RESULT line the routine reads, and
  - that render_scan_summary (dev-env#462, ADR-070) emits the --scan-dir aggregate
    RESULT line the routine reads as its *final* line in scan-dir mode, and
  - that _validated_config (dev-env#465 review) resolves ok / no-config / bad-config
    against a real tmp-dir hook-config.json — including a valid-but-non-object JSON
    payload, the regression case for a crash that used to abort an entire --scan-dir
    sweep on one malformed file — and that _added_and_failed's added/failed split
    matches what render_report and _reconcile_repo each derive from it.

The gh boundary (fetch_open_issues / fetch_board_items / add_to_project) is not mocked,
matching the repo's fixture-only / no-subprocess-mock convention. _validated_config only
touches the filesystem (a real tmp-dir hook-config.json), so it is tested directly rather
than via --dry-run integration runs.

Usage:
    py -3 claude/scripts/tests/test_reconcile_project_board.py

Exit 0 = all pass.
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "reconcile-project-board.py"

# The script imports _winsubp (a sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("reconcile_project_board", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

canonical_repo_root = mod.canonical_repo_root
field_key = mod.field_key
colliding_required_fields = mod.colliding_required_fields
open_issue_numbers = mod.open_issue_numbers
board_issue_numbers = mod.board_issue_numbers
compute_orphans = mod.compute_orphans
item_missing_fields = mod.item_missing_fields
board_items_missing_fields = mod.board_items_missing_fields
looks_like_scope_error = mod.looks_like_scope_error
is_truncated = mod.is_truncated
render_report = mod.render_report
render_scan_summary = mod.render_scan_summary
_added_and_failed = mod._added_and_failed
_validated_config = mod._validated_config
_backfill_item_cache = mod._backfill_item_cache

# reconcile-project-board.py's own `from _gh_project import ...` already resolved
# claude/scripts/ onto sys.path (above) -- this module is importable directly too,
# to read back what _backfill_item_cache wrote (dev-env#1057, ADR-141).
import _gh_project

REPO = "brownm09/dev-env"

CONFIG = {
    "repo": REPO,
    "project_number": "3",
    "project_owner": "brownm09",
    "project_node_id": "PVT_kwHOAjEKvM4BWKFe",
    "required_fields": [
        {
            "name": "Impact",
            "field_id": "PVTSSF_lAHOAjEKvM4BWKFezhRgkNc",
            "type": "single_select",
            "options": {"High": "08de2558", "Medium": "6320e8a6", "Low": "d8a85c2f"},
        },
        {
            "name": "Why",
            "field_id": "PVTF_lAHOAjEKvM4BWKFezhRgkN0",
            "type": "text",
            "hint": "one sentence",
        },
    ],
}


def _issue(number, title="t"):
    return {"number": number, "url": f"https://github.com/{REPO}/issues/{number}", "title": title}


def _item(number, *, type="Issue", repo=REPO, impact=None, why=None, item_id=None):
    """A `gh project item-list --format json` item. Field values are exposed under the
    field name lowercased (impact / why), absent when unset."""
    content = {"type": type, "number": number, "repository": repo,
               "url": f"https://github.com/{repo}/issues/{number}"}
    item = {"content": content, "id": item_id or f"PVTI_{number}"}
    if impact is not None:
        item["impact"] = impact
    if why is not None:
        item["why"] = why
    return item


def _write_config(repo_root, payload) -> None:
    """Write `payload` (any JSON-serializable value, not necessarily a dict) as
    repo_root's hook-config.json, creating .claude/ as needed -- mirrors what a real
    repo checkout has, no mocking required."""
    config_dir = Path(repo_root) / ".claude"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "hook-config.json").write_text(json.dumps(payload), encoding="utf-8")


# --- canonical worktree root -------------------------------------------------


def test_canonical_repo_root() -> str:
    canonical = "C:/Users/brown/Git/dev-env"
    assert canonical_repo_root(canonical) == canonical, "a canonical checkout is unchanged"
    wt = canonical + "/.claude/worktrees/reconcile-board"
    assert canonical_repo_root(wt) == canonical, "worktree path strips to the canonical root"
    assert canonical_repo_root(wt + "/claude/scripts") == canonical, "deep worktree path strips too"
    # Windows backslash spelling
    assert canonical_repo_root(r"C:\Users\brown\Git\dev-env\.claude\worktrees\x") == r"C:\Users\brown\Git\dev-env"
    assert canonical_repo_root("") == "", "empty -> empty (no crash)"
    return "worktree paths resolve to the canonical checkout (where hook-config lives); others pass through"


# --- field key / membership / set difference ---------------------------------


def test_field_key() -> str:
    assert field_key("Impact") == "impact"
    assert field_key("Why") == "why"
    assert field_key("  Linked Pull Requests ") == "linked pull requests", "interior spaces preserved"
    assert field_key("") == ""
    return "field name -> item-list JSON key (lowercased, trimmed, interior spaces kept)"


def test_colliding_required_fields() -> str:
    assert colliding_required_fields(["Impact", "Why"]) == [], "the real dev-env config has no collision"
    assert colliding_required_fields(["Status", "Impact"]) == ["Status"], "built-in key -> flagged"
    assert colliding_required_fields(["title", "Repository", "Why"]) == ["title", "Repository"], "case-insensitive"
    assert colliding_required_fields([]) == []
    return "required_fields names colliding with gh's built-in item keys are flagged, not silently mis-detected"


def test_open_issue_numbers() -> str:
    issues = [_issue(439), _issue(447), {"url": "x"}]  # one malformed (no number)
    assert open_issue_numbers(issues) == {439, 447}, "non-int / missing numbers dropped"
    return "open_issue_numbers collects valid int issue numbers"


def test_board_issue_numbers_filters() -> str:
    items = [
        _item(30),                                # ours, Issue
        _item(99, type="PullRequest"),            # a PR -> ignored
        _item(7, repo="brownm09/other"),          # cross-repo -> ignored
        {"content": {"type": "Issue"}, "id": "x"},  # no number -> ignored
    ]
    assert board_issue_numbers(items, REPO) == {30}, "only same-repo Issue items counted"
    return "board_issue_numbers ignores PRs, cross-repo items, and number-less items"


def test_compute_orphans_set_difference() -> str:
    issues = [_issue(434), _issue(435), _issue(436), _issue(30)]
    board = {30}  # only #30 is on the board
    orphans = compute_orphans(issues, board)
    assert [o["number"] for o in orphans] == [434, 435, 436], "open - board, sorted by number"
    assert orphans[0]["url"].endswith("/issues/434") and orphans[0]["title"] == "t"
    # Nothing orphaned when every open issue is already tracked.
    assert compute_orphans(issues, {30, 434, 435, 436}) == []
    return "compute_orphans = open issues - board issues (the #434/#435/#436 case), sorted"


# --- item-ID cache backfill (dev-env#1057, ADR-141) ---------------------------


def test_backfill_item_cache_populates_every_item() -> str:
    """_backfill_item_cache caches every fetched item's (repo, number) -> id, keyed
    by each item's OWN content.repository -- not the single `repo` being
    reconciled -- since a shared project board can carry items from other repos
    too (ADR-070, --scan-dir), and content.repository is exactly what
    board_issue_numbers above already uses to tell them apart. Every item type is
    cached (unlike board_issue_numbers's Issue-only filter, which exists for its
    own orphan-detection purpose) -- PR items are equally useful to have cached,
    since get-project-item.sh and post-pr-merge-project.py look up items by number
    regardless of type. An item with no number is silently skipped, never raises.
    The project board identity (owner + number) is part of every written key -- see
    _gh_project.py's _cache_key -- since this repo alone already reconciles more
    than one real board."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "cache.json"
        real_env = os.environ.get("PROJECT_ITEM_CACHE_PATH_OVERRIDE")
        os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = str(cache_path)
        try:
            items = [
                _item(434, item_id="PVTI_434"),
                _item(30, item_id="PVTI_30"),
                _item(7, repo="brownm09/other", item_id="PVTI_other_7"),  # cross-repo
                _item(20, type="PullRequest", item_id="PVTI_pr_20"),  # PR, not just Issue
                {"content": {"type": "Issue"}, "id": "x"},  # no number -> silently skipped
            ]
            _backfill_item_cache(items, "brownm09", "3")
        finally:
            if real_env is None:
                del os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"]
            else:
                os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = real_env
        cache = _gh_project.read_item_cache(cache_path)
        assert cache == {
            f"brownm09/3|{REPO}#434": "PVTI_434",
            f"brownm09/3|{REPO}#30": "PVTI_30",
            "brownm09/3|brownm09/other#7": "PVTI_other_7",
            f"brownm09/3|{REPO}#20": "PVTI_pr_20",
        }, f"unexpected cache contents: {cache}"
    return "_backfill_item_cache caches every item (any type, any repo), keyed by its own content.repository/number under this board's project identity"


def test_backfill_item_cache_batch_write_leaves_no_tmp_leftovers() -> str:
    """_backfill_item_cache now writes the whole fetched batch in one atomic swap
    (via write_item_cache_entries), not one read-modify-write call per item -- a
    /review finding on this PR: looping the single-entry writer over ~719 items
    widened the documented 'a concurrent write can lose one entry' race into 'a
    concurrent write can lose entries for the sweep's entire duration'."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "cache.json"
        real_env = os.environ.get("PROJECT_ITEM_CACHE_PATH_OVERRIDE")
        os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = str(cache_path)
        try:
            items = [_item(n, item_id=f"PVTI_{n}") for n in range(20)]
            _backfill_item_cache(items, "brownm09", "3")
        finally:
            if real_env is None:
                del os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"]
            else:
                os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = real_env
        leftovers = [p for p in Path(tmp).iterdir() if p.name != "cache.json"]
        assert leftovers == [], f"unexpected leftover files: {leftovers}"
        cache = _gh_project.read_item_cache(cache_path)
        assert len(cache) == 20, f"expected 20 entries, got {len(cache)}"
    return "backfilling 20 items leaves no tmp leftovers and writes all 20 entries (structurally one batch write, not 20 individual ones)"


# --- required-field detection ------------------------------------------------


def test_item_missing_fields() -> str:
    req = ["Impact", "Why"]
    assert item_missing_fields(_item(1, impact="High", why="because"), req) == []
    assert item_missing_fields(_item(1, impact="High"), req) == ["Why"], "absent key -> missing"
    assert item_missing_fields(_item(1, impact="High", why="  "), req) == ["Why"], "whitespace -> missing"
    assert item_missing_fields(_item(1), req) == ["Impact", "Why"], "both absent (a just-added orphan)"
    return "a field is present only with a non-empty, non-whitespace value; else it's missing"


def test_board_items_missing_fields_scope() -> str:
    items = [
        _item(434, impact="High"),                       # open, missing Why -> reported
        _item(30, impact="High", why="x"),               # open, complete -> not reported
        _item(20, type="PullRequest"),                   # PR -> ignored
        _item(11, repo="brownm09/other"),                # cross-repo -> ignored
        _item(999),                                      # CLOSED (not in open set) -> ignored
    ]
    open_nums = {434, 30, 20, 11}  # 999 deliberately omitted (closed)
    got = board_items_missing_fields(items, ["Impact", "Why"], open_nums, REPO)
    assert [g["number"] for g in got] == [434], "only the open, same-repo, incomplete Issue is reported"
    assert got[0]["missing"] == ["Why"] and got[0]["item_id"] == "PVTI_434"
    return "board_items_missing_fields surfaces only OPEN same-repo Issues that lack a field (no Done-item nagging)"


# --- scope-error detection ---------------------------------------------------


def test_looks_like_scope_error() -> str:
    assert looks_like_scope_error("error: your token is missing required scopes [read:project]") is True
    assert looks_like_scope_error("HTTP 404: Not Found") is False
    assert looks_like_scope_error("") is False
    return "the missing-project-scope gh error is recognized so the refresh hint can fire"


# --- gh --limit truncation detection ------------------------------------------


def test_is_truncated() -> str:
    assert is_truncated(1000, 1000) is True, "count == limit -> may be capped"
    assert is_truncated(999, 1000) is False, "under the limit -> safe"
    assert is_truncated(0, 1000) is False
    return "is_truncated flags a result count that hit the gh --limit cap (count >= limit)"


# --- render_report: the no-guessing contract + RESULT line -------------------


def test_render_report_adds_and_lists_commands() -> str:
    orphans = [{"number": 434, "url": f"https://github.com/{REPO}/issues/434", "title": "audit issue", "item_id": "PVTI_434"}]
    report = render_report(orphans, [], CONFIG, dry_run=False)
    assert "Added 1 orphan issue(s) to project 3:" in report
    assert "#434 missing: Impact, Why" in report, "a just-added orphan needs both fields"
    # Emits the edit COMMANDS targeting the real item id...
    assert "--id PVTI_434" in report
    assert "--single-select-option-id <option-id>" in report, "Impact command present"
    assert '--text "<why>"' in report, "Why command present"
    # ...but never assigns a value: the option ids appear only as a hint, never as the
    # chosen `--single-select-option-id 08de2558`.
    assert "--single-select-option-id 08de2558" not in report, "must NOT guess Impact=High"
    assert "options: High=08de2558" in report, "option ids shown only as a hint"
    assert "RESULT: orphans_added=1 add_failed=0 needs_attention=1 dry_run=false" in report
    return "added orphans -> edit commands emitted, value never set (no guessing), RESULT line correct"


def test_render_report_dry_run() -> str:
    orphans = [{"number": 447, "url": f"https://github.com/{REPO}/issues/447", "title": "t"}]  # no item_id
    report = render_report(orphans, [], CONFIG, dry_run=True)
    assert "Would add 1 orphan issue(s)" in report
    assert "(add to the board first, then set fields)" in report, "no item id yet in dry-run"
    assert "RESULT: orphans_added=0 add_failed=0 needs_attention=1 dry_run=true" in report
    return "dry-run reports would-add orphans, sets nothing, RESULT shows orphans_added=0 dry_run=true"


def test_render_report_all_clean() -> str:
    report = render_report([], [], CONFIG, dry_run=False)
    assert "No orphan issues" in report
    assert "All open board issues have their required fields set." in report
    assert "RESULT: orphans_added=0 add_failed=0 needs_attention=0 dry_run=false" in report
    return "no orphans + no gaps -> clean report, needs_attention=0"


def test_render_report_preexisting_gap() -> str:
    # An issue already on the board (e.g. someone added it but forgot Why) is surfaced too.
    preexisting = [{"number": 368, "url": f"https://github.com/{REPO}/issues/368", "item_id": "PVTI_368", "missing": ["Why"]}]
    report = render_report([], preexisting, CONFIG, dry_run=False)
    assert "#368 missing: Why" in report
    assert "--id PVTI_368" in report and '--text "<why>"' in report
    assert "RESULT: orphans_added=0 add_failed=0 needs_attention=1 dry_run=false" in report
    return "pre-existing on-board issues missing a field are surfaced with their edit command"


def test_render_report_partial_add_failure() -> str:
    # One add succeeded (item_id set), one failed (item_id stays None) -- the header and
    # RESULT must both reflect the real outcome, not the attempt count (dev-env #447
    # review: the header previously said "Added 2" here, which was true only of attempts).
    orphans = [
        {"number": 434, "url": f"https://github.com/{REPO}/issues/434", "title": "a", "item_id": "PVTI_434"},
        {"number": 435, "url": f"https://github.com/{REPO}/issues/435", "title": "b", "item_id": None},
    ]
    report = render_report(orphans, [], CONFIG, dry_run=False)
    assert "Added 1/2 orphan issue(s) to project 3 (1 failed):" in report
    assert "#435  b  [ADD FAILED - re-run]" in report
    assert "RESULT: orphans_added=1 add_failed=1 needs_attention=2 dry_run=false" in report
    return "a partial add failure shows in both the header and RESULT.add_failed, not just the per-item flag"


# --- render_scan_summary: the --scan-dir aggregate RESULT line (dev-env#462, ADR-070) ---


def test_render_scan_summary_all_clean() -> str:
    line = render_scan_summary(5, 3, 0, 0, 0, 0, dry_run=False)
    assert line == (
        "RESULT: repos_scanned=5 repos_skipped=3 repos_failed=0 orphans_added=0 "
        "add_failed=0 needs_attention=0 dry_run=false"
    )
    return "an all-clean scan reports zero orphans/failures with the repo counts intact"


def test_render_scan_summary_mixed() -> str:
    line = render_scan_summary(10, 6, 1, 4, 1, 5, dry_run=False)
    assert line == (
        "RESULT: repos_scanned=10 repos_skipped=6 repos_failed=1 orphans_added=4 "
        "add_failed=1 needs_attention=5 dry_run=false"
    )
    return "repos_scanned/skipped/failed and the summed orphan/attention counts all appear"


def test_render_scan_summary_dry_run() -> str:
    line = render_scan_summary(2, 0, 0, 0, 0, 3, dry_run=True)
    assert line.endswith("dry_run=true"), "dry_run flag reflected, matching render_report's own flag"
    assert line.startswith("RESULT: repos_scanned=2 repos_skipped=0 repos_failed=0")
    return "dry_run=true is reflected in the aggregate line, matching render_report's flag"


# --- _added_and_failed: shared by render_report and _reconcile_repo (dev-env#465) ---


def test_added_and_failed_dry_run() -> str:
    orphans = [{"number": 1, "item_id": "PVTI_1"}, {"number": 2, "item_id": None}]
    assert _added_and_failed(orphans, dry_run=True) == (0, 0), "dry-run attempts nothing"
    return "dry-run always reports (0, 0) regardless of orphans' item_id state"


def test_added_and_failed_partial() -> str:
    orphans = [
        {"number": 1, "item_id": "PVTI_1"},
        {"number": 2, "item_id": None},
        {"number": 3, "item_id": "PVTI_3"},
    ]
    assert _added_and_failed(orphans, dry_run=False) == (2, 1), "2 succeeded, 1 failed"
    return "added/failed split matches which orphans ended up with a real item_id"


def test_added_and_failed_all_added() -> str:
    orphans = [{"number": 1, "item_id": "PVTI_1"}, {"number": 2, "item_id": "PVTI_2"}]
    assert _added_and_failed(orphans, dry_run=False) == (2, 0)
    return "every orphan added -> (len(orphans), 0)"


# --- _validated_config: ok / no-config / bad-config (dev-env#465 review) -----


def test_validated_config_ok() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, CONFIG)
        config, status, message = _validated_config(tmp)
        assert status == "ok" and message is None
        assert config == CONFIG
    return "a well-formed hook-config.json validates as ok; config is returned as-is"


def test_validated_config_no_config() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        # no .claude/hook-config.json written at all
        result = _validated_config(tmp)
        assert result == (None, "no-config", None)
    return "a repo with no hook-config.json at all is 'no-config', not an error"


def test_validated_config_missing_required_keys() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, {"repo": REPO})  # no project_number / project_owner
        config, status, message = _validated_config(tmp)
        assert config is None and status == "bad-config"
        assert "missing repo / project_number / project_owner" in message
    return "a config missing repo/project_number/project_owner is 'bad-config' with a specific message"


def test_validated_config_colliding_required_fields() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        bad = dict(CONFIG, required_fields=[{"name": "Status", "field_id": "x", "type": "text"}])
        _write_config(tmp, bad)
        config, status, message = _validated_config(tmp)
        assert config is None and status == "bad-config"
        assert "collide" in message
    return "a required_fields name colliding with a reserved gh item key is 'bad-config'"


def test_validated_config_non_dict_json() -> str:
    # dev-env#465 review: a valid-but-non-object hook-config.json (e.g. a bare JSON
    # array) used to crash _validated_config's config.get(...) with an AttributeError,
    # uncaught anywhere in the --scan-dir loop -- one malformed file anywhere under the
    # scan directory aborted the entire nightly sweep. Must be "bad-config", not a crash.
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, [])  # valid JSON, not a JSON object
        config, status, message = _validated_config(tmp)
        assert config is None and status == "bad-config"
        assert "JSON object" in message and "not a dict" in message
    return "a valid-but-non-object hook-config.json (e.g. a bare array) is 'bad-config', not a crash"


def main() -> int:
    tests = [
        ("canonical_repo_root resolution", test_canonical_repo_root),
        ("field_key derivation", test_field_key),
        ("colliding_required_fields reserved-key guard", test_colliding_required_fields),
        ("open_issue_numbers", test_open_issue_numbers),
        ("board_issue_numbers filtering", test_board_issue_numbers_filters),
        ("compute_orphans set difference", test_compute_orphans_set_difference),
        ("_backfill_item_cache populates every item (dev-env#1057)", test_backfill_item_cache_populates_every_item),
        ("_backfill_item_cache batch write, no tmp leftovers", test_backfill_item_cache_batch_write_leaves_no_tmp_leftovers),
        ("item_missing_fields presence rule", test_item_missing_fields),
        ("board_items_missing_fields scope", test_board_items_missing_fields_scope),
        ("looks_like_scope_error", test_looks_like_scope_error),
        ("is_truncated gh --limit cap detection", test_is_truncated),
        ("render_report adds + emits commands (no guessing)", test_render_report_adds_and_lists_commands),
        ("render_report dry-run", test_render_report_dry_run),
        ("render_report all-clean", test_render_report_all_clean),
        ("render_report pre-existing gap", test_render_report_preexisting_gap),
        ("render_report partial add failure", test_render_report_partial_add_failure),
        ("render_scan_summary all-clean", test_render_scan_summary_all_clean),
        ("render_scan_summary mixed counts", test_render_scan_summary_mixed),
        ("render_scan_summary dry-run", test_render_scan_summary_dry_run),
        ("_added_and_failed dry-run", test_added_and_failed_dry_run),
        ("_added_and_failed partial", test_added_and_failed_partial),
        ("_added_and_failed all added", test_added_and_failed_all_added),
        ("_validated_config ok", test_validated_config_ok),
        ("_validated_config no-config", test_validated_config_no_config),
        ("_validated_config missing required keys", test_validated_config_missing_required_keys),
        ("_validated_config colliding required_fields", test_validated_config_colliding_required_fields),
        ("_validated_config non-dict JSON (dev-env#465 crash regression)", test_validated_config_non_dict_json),
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
