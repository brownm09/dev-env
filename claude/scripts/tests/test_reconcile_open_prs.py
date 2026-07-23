#!/usr/bin/env python3
"""Unit tests for reconcile-open-prs.py shard/legacy reconciliation (ADR-056).

`reconcile-open-prs.py` is a UserPromptSubmit hook that, once per session, removes
open-PR tracking records whose PRs are now MERGED/CLOSED. ADR-056 reshaped the
tracking from a single shared `open-prs.jsonl` into per-PR shards
`sessions/<project>/open-prs/<N>.json`, so the structural guarantee the hook now
relies on is: **removing one PR's record is a per-file `unlink` that never rewrites
another PR's file.** These tests pin that — `reconcile_shard_dir` unlinks only the
merged shard and leaves the surviving shard byte-identical — plus the pure helpers,
and confirm the legacy `open-prs.jsonl` path still drains.

Also exercises `find_dirty_open_pr_paths` (dev-env#578): the pure `git status --porcelain`
line filter that surfaces any currently-uncommitted `sessions/*/open-prs*` change (this
session's own fresh unlinks, or a prior session's never-committed ones) so the hook's
systemMessage can hand Claude a ready-to-use pathspec — restoring ADR-018's "picked up by
the next commit" guarantee in a form compatible with ADR-056's sharded shape, after ADR-082
removed the last thing (`/journal-compose`'s old bulk `git add -u`) still catching these
opportunistically. Pins the porcelain `XY <path>` slicing, the shard/legacy-file shape
filter (unrelated paths ignored), and backslash-path normalization.

The reconcilers take an injectable `state_fn(pr, repo) -> state` so the unlink/keep
logic runs offline; the live `gh pr view` boundary (`check_pr_state`) and the
`git status --porcelain` boundary (`dirty_open_pr_status_lines`) are not tested,
matching the repo's fixture-only / no-subprocess-mock convention.

Usage:
    py -3 claude/scripts/tests/test_reconcile_open_prs.py

Exit 0 = all pass.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "reconcile-open-prs.py"

# The script imports _winsubp (a sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("reconcile_open_prs", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

should_remove = mod.should_remove
repo_from_url = mod.repo_from_url
entry_repo_and_pr = mod.entry_repo_and_pr
project_dirs = mod.project_dirs
reconcile_shard_dir = mod.reconcile_shard_dir
reconcile_file = mod.reconcile_file
classify_dirty_open_pr_paths = mod.classify_dirty_open_pr_paths
parse_open_pr_status_line = mod.parse_open_pr_status_line
shard_pr_number_from_path = mod.shard_pr_number_from_path
classify_deletions = mod.classify_deletions
safe_for_command = mod.safe_for_command
cap = mod.cap

URL_386 = "https://github.com/brownm09/dev-env/pull/386"
URL_387 = "https://github.com/brownm09/dev-env/pull/387"


def _entry(pr, url):
    return {"pr": pr, "url": url, "topic": f"PR {pr}", "stub": "s.stub.md", "opened": "2026-06-22"}


def _write_shard(shard_dir: Path, pr, url):
    shard_dir.mkdir(parents=True, exist_ok=True)
    p = shard_dir / f"{pr}.json"
    p.write_text(json.dumps(_entry(pr, url)), encoding="utf-8")
    return p


# --- pure helpers ------------------------------------------------------------


def test_should_remove() -> str:
    assert should_remove("MERGED") is True
    assert should_remove("CLOSED") is True
    assert should_remove("OPEN") is False
    assert should_remove(None) is False, "gh failure (None) is conservative — keep"
    assert should_remove("") is False
    assert should_remove("DRAFT") is False, "unknown state -> keep"
    return "MERGED/CLOSED -> remove; OPEN/None/unknown -> keep (conservative)"


def test_repo_from_url() -> str:
    assert repo_from_url(URL_386) == "brownm09/dev-env"
    assert repo_from_url("") is None
    assert repo_from_url("not a url") is None
    return "owner/repo extracted from a PR URL; empty/garbage -> None"


def test_entry_repo_and_pr() -> str:
    assert entry_repo_and_pr(_entry(386, URL_386)) == ("brownm09/dev-env", 386)
    assert entry_repo_and_pr({"pr": 5}) == (None, 5), "missing url -> repo None"
    assert entry_repo_and_pr({"url": URL_386}) == ("brownm09/dev-env", None), "missing pr -> None"
    assert entry_repo_and_pr({"url": URL_386, "pr": "x"}) == ("brownm09/dev-env", None), "non-int pr -> None"
    return "entry -> (repo, pr); missing/typo fields resolve to None safely"


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


# --- per-PR shard reconciliation (the ADR-056 structural guarantee) ----------


def test_shard_removes_only_merged_leaves_others_intact() -> str:
    with tempfile.TemporaryDirectory() as root:
        shard_dir = Path(root) / "open-prs"
        _write_shard(shard_dir, 386, URL_386)
        survivor = _write_shard(shard_dir, 387, URL_387)
        survivor_bytes = survivor.read_bytes()

        # 386 merged, 387 still open.
        state_fn = lambda pr, repo: "MERGED" if pr == 386 else "OPEN"
        surviving, removed = reconcile_shard_dir(shard_dir, state_fn=state_fn)

        assert not (shard_dir / "386.json").exists(), "merged shard must be unlinked"
        assert (shard_dir / "387.json").exists(), "open shard must remain"
        assert survivor.read_bytes() == survivor_bytes, "survivor shard must be byte-identical (not rewritten)"
        assert [e["pr"] for e in surviving] == [387]
        assert [(e["pr"], st) for e, st in removed] == [(386, "MERGED")]
    return "merged shard unlinked; the survivor file is untouched byte-for-byte (no clobber)"


def test_shard_dir_removed_when_emptied() -> str:
    with tempfile.TemporaryDirectory() as root:
        shard_dir = Path(root) / "open-prs"
        _write_shard(shard_dir, 386, URL_386)
        surviving, removed = reconcile_shard_dir(shard_dir, state_fn=lambda pr, repo: "CLOSED")
        assert surviving == []
        assert not shard_dir.exists(), "open-prs/ dir removed once its last shard is gone"
    return "last shard removed -> the empty open-prs/ directory is cleaned up"


def test_shard_malformed_and_nonnumeric_left_in_place() -> str:
    with tempfile.TemporaryDirectory() as root:
        shard_dir = Path(root) / "open-prs"
        shard_dir.mkdir(parents=True)
        (shard_dir / "bad.json").write_text("{not json", encoding="utf-8")     # unparseable
        (shard_dir / "index.json").write_text("{}", encoding="utf-8")          # non-numeric name
        (shard_dir / "99.json").write_text(json.dumps({"topic": "x"}), encoding="utf-8")  # no url/pr
        surviving, removed = reconcile_shard_dir(shard_dir, state_fn=lambda pr, repo: "MERGED")
        assert surviving == [] and removed == []
        assert (shard_dir / "bad.json").exists(), "unparseable shard left for a human"
        assert (shard_dir / "index.json").exists(), "non-numeric file ignored, not deleted"
        assert (shard_dir / "99.json").exists(), "malformed (no pr/url) shard left in place"
    return "unparseable / non-numeric / malformed shards are never auto-deleted (conservative)"


def test_shard_missing_dir() -> str:
    with tempfile.TemporaryDirectory() as root:
        surviving, removed = reconcile_shard_dir(Path(root) / "open-prs", state_fn=lambda pr, repo: "MERGED")
        assert surviving == [] and removed == []
    return "missing open-prs/ dir -> ([], []) with no error"


# --- legacy single-file path still drains ------------------------------------


def test_legacy_file_drops_only_merged() -> str:
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "open-prs.jsonl"
        f.write_text(json.dumps(_entry(386, URL_386)) + "\n" + json.dumps(_entry(387, URL_387)) + "\n",
                     encoding="utf-8")
        state_fn = lambda pr, repo: "MERGED" if pr == 386 else "OPEN"
        surviving, removed = reconcile_file(f, state_fn=state_fn)
        assert [e["pr"] for e in surviving] == [387]
        assert [(e["pr"], st) for e, st in removed] == [(386, "MERGED")]
        kept = [json.loads(l)["pr"] for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert kept == [387], f"legacy file rewritten to surviving lines only, got {kept}"
    return "legacy open-prs.jsonl: merged line dropped, open line kept (drains over time)"


def test_legacy_file_deleted_when_empty() -> str:
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "open-prs.jsonl"
        f.write_text(json.dumps(_entry(386, URL_386)) + "\n", encoding="utf-8")
        reconcile_file(f, state_fn=lambda pr, repo: "MERGED")
        assert not f.exists(), "legacy file deleted when its last entry is removed"
    return "legacy open-prs.jsonl deleted once its last entry merges"


def test_legacy_non_object_line_dropped_on_rewrite() -> str:
    # A non-object legacy line (manual corruption) used to crash reconcile_file via
    # entry.get(...), which main() swallowed — so the file was NEVER rewritten and the corrupt
    # line froze all cleanup (stale merged entries survived too). read_legacy_entries now skips
    # the non-object line, so reconciliation proceeds and the line is dropped on the rewrite.
    # Intended, tested behaviour (ADR-057): open-prs.jsonl is system-written and draining; a
    # corrupt line carries no tracking data.
    with tempfile.TemporaryDirectory() as root:
        f = Path(root) / "open-prs.jsonl"
        f.write_text(
            json.dumps(_entry(386, URL_386)) + "\n"      # merged -> removed
            + '["corrupt", 1]\n'                          # non-object -> skipped at read
            + json.dumps(_entry(387, URL_387)) + "\n",    # open -> survives
            encoding="utf-8",
        )
        state_fn = lambda pr, repo: "MERGED" if pr == 386 else "OPEN"
        surviving, removed = reconcile_file(f, state_fn=state_fn)
        assert [e["pr"] for e in surviving] == [387], "only the open PR survives"
        assert [(e["pr"], st) for e, st in removed] == [(386, "MERGED")]
        kept = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(kept) == 1 and json.loads(kept[0])["pr"] == 387, \
            f"corrupt line dropped, merged removed, open kept; got {kept}"
    return "a non-object legacy line no longer freezes cleanup — dropped on the rewrite (ADR-057)"


# --- dirty open-PR path classification (dev-env#578, ADR-119) ----------------


def test_parse_filters_to_open_pr_shape() -> str:
    lines = [
        " D sessions/dev-env/open-prs/567.json",
        " M sessions/lifting-logbook/open-prs.jsonl",
        "?? sessions/dev-env/open-prs/999.json",
        " M sessions/dev-env/2026-07-05_090000.stub.md",
        "M  claude/scripts/reconcile-open-prs.py",
    ]
    got = [parse_open_pr_status_line(x)[1] for x in lines if parse_open_pr_status_line(x)]
    assert got == [
        "sessions/dev-env/open-prs/567.json",
        "sessions/lifting-logbook/open-prs.jsonl",
        "sessions/dev-env/open-prs/999.json",
    ], f"expected only open-prs shard/legacy paths, got {got}"
    return "shard (any status) and legacy-file paths kept; unrelated stub/script paths dropped"


def test_parse_normalizes_backslashes() -> str:
    got = parse_open_pr_status_line(r" D sessions\dev-env\open-prs\567.json")
    assert got == (" D", "sessions/dev-env/open-prs/567.json"), got
    return "backslash-separated porcelain paths normalized to forward slashes"


def test_parse_handles_renames() -> str:
    lines = [
        "R  sessions/dev-env/open-prs/567.json -> sessions/dev-env/open-prs/568.json",
        "R  docs/old-name.md -> sessions/lifting-logbook/open-prs/700.json",
        "R  sessions/dev-env/open-prs/1.json -> docs/unrelated.md",
    ]
    got = [parse_open_pr_status_line(x)[1] for x in lines if parse_open_pr_status_line(x)]
    assert got == [
        "sessions/dev-env/open-prs/568.json",
        "sessions/lifting-logbook/open-prs/700.json",
    ], f"expected only destination paths landing in open-prs shape, got {got}"
    return "rename lines ('old -> new') keep only the destination path, matched against its shape"


def test_parse_empty_and_short_lines() -> str:
    assert parse_open_pr_status_line("") is None
    assert parse_open_pr_status_line("M") is None
    assert parse_open_pr_status_line(" M") is None, "lines shorter than 'XY p' are skipped"
    return "empty and malformed/short porcelain lines handled without error"


def test_only_exact_delete_codes_count_as_deletions() -> str:
    """The load-bearing guarantee of ADR-119: a `D` ANYWHERE in the two-char porcelain field
    is not a deletion. `AD`/`RD` are a concurrent session's STAGED shard — the class the
    explicit-pathspec rule exists to keep out of your commit — and `DD`/`DU`/`UD` are merge
    conflicts, where a recommended `git add` silently resolves the conflict and the
    following partial commit fails outright. Regression-pins the dev-env#873 review fix."""
    base = "sessions/dev-env/open-prs/1.json"
    deleting = [" D", "D "]
    not_deleting = ["AD", "RD", "MD", "CD", "TD", "DD", "DU", "UD", "AU", "UA", "AA", "UU",
                    "??", " M", "M ", "R ", "A "]
    for code in deleting:
        got = classify_dirty_open_pr_paths([f"{code} {base}"])
        assert got["deleted"] == [base] and got["other"] == [], f"{code!r} should be a deletion: {got}"
    for code in not_deleting:
        got = classify_dirty_open_pr_paths([f"{code} {base}"])
        assert got["deleted"] == [] and got["other"] == [base], \
            f"{code!r} must NOT be treated as a deletion (got {got})"
    return "only exact ' D'/'D ' are deletions; AD/RD (staged, concurrent) and DD/DU/UD (conflict) are not"


def test_classify_dirty_preserves_status_order() -> str:
    lines = [
        " D sessions/lifting-logbook/open-prs/853.json",
        "?? sessions/dev-env/open-prs/999.json",
        "D  sessions/lifting-logbook/open-prs/856.json",
        " M sessions/dev-env/open-prs/770.json",
    ]
    got = classify_dirty_open_pr_paths(lines)
    assert got["deleted"] == [
        "sessions/lifting-logbook/open-prs/853.json",
        "sessions/lifting-logbook/open-prs/856.json",
    ], got["deleted"]
    assert got["other"] == [
        "sessions/dev-env/open-prs/999.json",
        "sessions/dev-env/open-prs/770.json",
    ], got["other"]
    return "both buckets preserve git status order; staged and unstaged deletes both counted"


# --- deletion classification (ADR-119, dev-env#866) --------------------------


def test_shard_pr_number_from_path() -> str:
    assert shard_pr_number_from_path("sessions/dev-env/open-prs/853.json") == 853
    assert shard_pr_number_from_path("sessions/x/open-prs.jsonl") is None, "legacy file has no single PR"
    assert shard_pr_number_from_path("sessions/x/open-prs/abc.json") is None, "non-numeric stem"
    assert shard_pr_number_from_path("sessions/x/open-prs/12.txt") is None, "non-json"
    return "PR number parsed via the shared _journal_shards reader; legacy/non-numeric yield None"


def _ident(mapping):
    """url_fn stub: path -> (url, embedded_pr)."""
    return lambda path: mapping.get(path)


def _fixed_state(mapping):
    return lambda pr, repo: mapping.get(pr)


DE = "https://github.com/brownm09/dev-env/pull/"


def test_classify_deletions_buckets_by_confirmed_state() -> str:
    paths = [
        "sessions/dev-env/open-prs/853.json",
        "sessions/dev-env/open-prs/900.json",
        "sessions/dev-env/open-prs/901.json",
        "sessions/dev-env/open-prs.jsonl",
    ]
    got = classify_deletions(
        paths,
        url_fn=_ident({
            "sessions/dev-env/open-prs/853.json": (DE + "853", 853),
            "sessions/dev-env/open-prs/900.json": (DE + "900", 900),
            "sessions/dev-env/open-prs/901.json": (DE + "901", 901),
        }),
        state_fn=_fixed_state({853: "MERGED", 900: "OPEN", 901: None}),
    )
    assert got["merged"] == ["sessions/dev-env/open-prs/853.json"], got["merged"]
    assert got["open"] == ["sessions/dev-env/open-prs/900.json"], got["open"]
    assert got["unverified"] == [
        "sessions/dev-env/open-prs/901.json",   # gh returned nothing (e.g. rate-limited)
        "sessions/dev-env/open-prs.jsonl",      # legacy file, no single PR to confirm
    ], got["unverified"]
    assert got["skipped"] == []
    return "only a confirmed MERGED/CLOSED deletion is cleared for commit; OPEN and unresolved are not"


def test_classify_deletions_filename_pr_mismatch_is_unverified() -> str:
    """A shard whose embedded `pr` disagrees with its filename stem must never be trusted:
    journal-shard-write-advisory.py only *advises* on that mismatch, so such shards land on
    disk, and trusting the filename alone would query the wrong PR. Here the file is named
    900.json but carries PR 853's identity; 853 is MERGED and 900 is OPEN, so a naive
    implementation reports an OPEN PR's record as safe to commit."""
    got = classify_deletions(
        ["sessions/dev-env/open-prs/900.json"],
        url_fn=_ident({"sessions/dev-env/open-prs/900.json": (DE + "853", 853)}),
        state_fn=_fixed_state({853: "MERGED", 900: "OPEN"}),
    )
    assert got["merged"] == [], "a filename/pr mismatch must never reach `merged`"
    assert got["unverified"] == ["sessions/dev-env/open-prs/900.json"], got
    return "filename stem vs embedded pr mismatch routes to unverified, not merged"


def test_classify_deletions_unresolvable_identity_is_unverified() -> str:
    got = classify_deletions(
        ["sessions/dev-env/open-prs/853.json"],
        url_fn=_ident({}),                       # git show failed / shard not at HEAD
        state_fn=_fixed_state({853: "MERGED"}),  # never consulted
    )
    assert got["unverified"] == ["sessions/dev-env/open-prs/853.json"], got
    assert got["merged"] == [], "no identity means nothing to confirm against"
    return "a shard whose committed identity cannot be read is unverified, never assumed merged"


def test_classify_deletions_reports_rather_than_drops_beyond_cap() -> str:
    paths = [f"sessions/dev-env/open-prs/{n}.json" for n in (1, 2, 3)]
    got = classify_deletions(
        paths,
        url_fn=_ident({p: (DE + str(i + 1), i + 1) for i, p in enumerate(paths)}),
        state_fn=_fixed_state({1: "MERGED", 2: "MERGED", 3: "MERGED"}),
        max_probes=2,
    )
    assert got["merged"] == paths[:2], got["merged"]
    assert got["skipped"] == paths[2:], "over-cap entries are surfaced, not silently dropped"
    return "the probe cap surfaces what it skipped, so a capped run never reads as full coverage"


def test_classify_deletions_deadline_stops_probing() -> str:
    """The count cap alone cannot bound latency (N probes x two 15s timeouts >> the hook's
    30s budget), so a wall-clock deadline is the real guard. Pinned via an injected
    deadline_fn that expires after the first probe."""
    paths = [f"sessions/dev-env/open-prs/{n}.json" for n in (1, 2, 3)]
    calls = {"n": 0}

    def expiring():
        calls["n"] += 1
        return calls["n"] > 1  # fresh for the first check, expired thereafter

    got = classify_deletions(
        paths,
        url_fn=_ident({p: (DE + str(i + 1), i + 1) for i, p in enumerate(paths)}),
        state_fn=_fixed_state({1: "MERGED", 2: "MERGED", 3: "MERGED"}),
        deadline_fn=expiring,
    )
    assert got["merged"] == paths[:1], got["merged"]
    assert got["skipped"] == paths[1:], "past the deadline, the remainder is reported as skipped"
    return "an expired wall-clock deadline stops probing and reports the remainder as skipped"


def test_classify_deletions_legacy_path_costs_no_probe() -> str:
    paths = ["sessions/dev-env/open-prs.jsonl", "sessions/dev-env/open-prs/853.json"]
    got = classify_deletions(
        paths,
        url_fn=_ident({"sessions/dev-env/open-prs/853.json": (DE + "853", 853)}),
        state_fn=_fixed_state({853: "MERGED"}),
        max_probes=1,
    )
    assert got["merged"] == ["sessions/dev-env/open-prs/853.json"], got
    assert got["skipped"] == [], "the legacy path consumed no probe, so the shard still got one"
    return "a no-PR-number path does not consume the probe budget"


# --- command-safety and message bounding (dev-env#873 review) ----------------


def test_safe_for_command_rejects_shell_metacharacters() -> str:
    """`git status --porcelain` does not quote a path containing `;`, and such a path still
    satisfies the reporting shape check — so the ready-to-run command must be gated on a
    stricter allowlist or the emitted text carries a second command."""
    assert safe_for_command(["sessions/dev-env/open-prs/1.json"]) is True
    for bad in [
        "sessions/dev-env;id/open-prs/1.json",
        "sessions/dev-env/open-prs/1.json; id",
        "sessions/dev env/open-prs/1.json",
        "sessions/$(id)/open-prs/1.json",
        "../sessions/dev-env/open-prs/1.json",
    ]:
        assert safe_for_command([bad]) is False, f"{bad!r} must not be command-safe"
    assert safe_for_command(["sessions/a/open-prs/1.json", "sessions/b;x/open-prs/2.json"]) is False, \
        "one unsafe path taints the whole batch"
    return "only plain sessions/<project>/open-prs/<N>.json is interpolated into a shell command"


def test_cap_bounds_message_lists() -> str:
    paths = [f"sessions/p/open-prs/{n}.json" for n in range(1, 9)]
    got = cap(paths)
    assert got.count(",") == 4 and got.endswith("(+3 more)"), got
    assert cap(paths[:2]) == "sessions/p/open-prs/1.json, sessions/p/open-prs/2.json"
    assert cap([]) == "", "empty list renders empty, not '(+-5 more)'"
    return "path lists in the systemMessage are bounded with an honest (+N more) count"


def main() -> int:
    tests = [
        ("should_remove predicate", test_should_remove),
        ("repo_from_url extraction", test_repo_from_url),
        ("entry_repo_and_pr resolution", test_entry_repo_and_pr),
        ("project_dirs discovery", test_project_dirs),
        ("shard removal leaves others byte-identical (ADR-056 guarantee)", test_shard_removes_only_merged_leaves_others_intact),
        ("empty open-prs/ dir cleaned up", test_shard_dir_removed_when_emptied),
        ("malformed/non-numeric shards left in place", test_shard_malformed_and_nonnumeric_left_in_place),
        ("missing shard dir -> no error", test_shard_missing_dir),
        ("legacy file drops only merged", test_legacy_file_drops_only_merged),
        ("legacy file deleted when empty", test_legacy_file_deleted_when_empty),
        ("legacy non-object line dropped on rewrite (ADR-057)", test_legacy_non_object_line_dropped_on_rewrite),
        ("open-PR paths filtered from git status (dev-env#578)", test_parse_filters_to_open_pr_shape),
        ("porcelain paths normalize backslashes", test_parse_normalizes_backslashes),
        ("rename lines keep the destination (review finding, PR #581)", test_parse_handles_renames),
        ("empty/short porcelain lines handled", test_parse_empty_and_short_lines),
        ("ONLY exact ' D'/'D ' count as deletions (ADR-119)", test_only_exact_delete_codes_count_as_deletions),
        ("both buckets preserve git-status order", test_classify_dirty_preserves_status_order),
        ("PR number parsed via the shared reader", test_shard_pr_number_from_path),
        ("deletions bucketed by confirmed PR state", test_classify_deletions_buckets_by_confirmed_state),
        ("filename/embedded-pr mismatch -> unverified", test_classify_deletions_filename_pr_mismatch_is_unverified),
        ("unresolvable identity -> unverified, never assumed merged", test_classify_deletions_unresolvable_identity_is_unverified),
        ("probe cap surfaces what it skipped", test_classify_deletions_reports_rather_than_drops_beyond_cap),
        ("wall-clock deadline stops probing", test_classify_deletions_deadline_stops_probing),
        ("legacy open-prs.jsonl path costs no probe", test_classify_deletions_legacy_path_costs_no_probe),
        ("ready-to-run command rejects shell metacharacters", test_safe_for_command_rejects_shell_metacharacters),
        ("message path lists are bounded", test_cap_bounds_message_lists),
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
