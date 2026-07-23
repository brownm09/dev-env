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

Also exercises `parse_open_pr_status_line` / `classify_dirty_open_pr_paths` (dev-env#578,
dev-env#866): the pure `git status --porcelain` line filter that surfaces any
currently-uncommitted `sessions/*/open-prs*` change (this session's own fresh unlinks, or a
prior session's never-committed ones) so the hook's systemMessage can hand Claude a
ready-to-use pathspec — restoring ADR-018's "picked up by the next commit" guarantee in a
form compatible with ADR-056's sharded shape, after ADR-082 removed the last thing
(`/journal-compose`'s old bulk `git add -u`) still catching these opportunistically. Pins
the porcelain `XY <path>` slicing, the shard/legacy-file shape filter (unrelated paths
ignored), backslash-path normalization, and the exact-delete-code rule — plus
`classify_deletions`' merged/open/unverified/skipped bucketing (ADR-119).

**The REST transport's two silent hazards** (dev-env#888, ADR-018 Amendment 1). The lookup
now reads `GET /repos/{o}/{r}/pulls/{n}` on the `core` bucket — REST-only, replacing
ADR-119's GraphQL-then-REST fallback — which introduces two failure modes a naive test
passes straight through, so both are pinned on `pr_state_from_row`, the pure helper that
exists precisely to make them coverable:

  - **MERGED is not a REST `state`.** GraphQL returned MERGED as a distinct value; REST
    returns `state: "closed"` plus a *separate* merge signal — a `merged` boolean on
    `GET /pulls/{n}`, and only `merged_at` on the `GET /pulls` list shape (both verified
    live). Collapsing merged into closed would not change what gets pruned, since
    `should_remove` accepts both — which is exactly why it would go unnoticed.
  - **State case.** REST answers lowercase `"closed"` where `should_remove` compares
    `"CLOSED"`, so without normalization the hook goes *inert* — fail-safe in direction,
    total in effect, and reported nowhere.

The case pin runs raw REST rows all the way to the `unlink`, because that is the only level
at which dropping normalization actually fails. `_PR_PROJECTION` gets a structural gate: it
cannot be executed offline (gh owns the jq), so without one it would be covered by nothing
but a comment.

Also pinned: `WorkBudget` / `budgeted_state_fn` (dev-env#888) — the hook-wide wall clock
that stops N sequential lookups from exhausting the 30s settings.json timeout and getting
the hook killed before it prints the `Open PRs:` line that is its original ADR-018 job — and
the constant invariant that makes that bound true, as an assertion rather than a comment.

And `counting_state_fn` (/review finding on PR #897): the budget makes keep-on-unresolved
*systematic* rather than sporadic, so every remaining PR can be listed under `Open PRs:`
without GitHub ever confirming it, and a merged PR reads as outstanding work. The count that
qualifies that line is pinned both in isolation and against what it must equal — the number of
survivors never confirmed, with a malformed shard (which takes no lookup) excluded.

The reconcilers take an injectable `state_fn(pr, repo) -> state` so the unlink/keep
logic runs offline; the live REST boundary (`check_pr_state`) and the git boundaries
(`dirty_open_pr_status_lines`, `committed_shard_identity`, `merge_in_progress`,
`current_branch`) are not tested, matching the repo's fixture-only / no-subprocess-mock
convention.

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

import _journal_shards  # noqa: E402  -- for the shared-helper identity pin below

should_remove = mod.should_remove
pr_state_from_row = mod.pr_state_from_row
WorkBudget = mod.WorkBudget
budgeted_state_fn = mod.budgeted_state_fn
counting_state_fn = mod.counting_state_fn
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


def test_project_dirs_is_shared_helper() -> str:
    # Anti-drift pin (ADR-057, dev-env#881). Behaviour is pinned once in
    # tests/test_journal_shards.py; what matters *here* is that this hook resolves to that
    # one implementation and not a reintroduced local copy — the exact shape that already
    # drifted between this file and reconcile-pending-tiles.py once (lexical vs numeric sort).
    assert project_dirs is _journal_shards.project_dirs, \
        "reconcile-open-prs.py re-defined project_dirs locally instead of importing the shared one"
    return "project_dirs is _journal_shards' shared helper, not a local copy"


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


# --- REST transport: state case and merged-vs-closed (dev-env#888) -----------


def _rest_pr(number, state="open", **over):
    """A row shaped as `GET /pulls/{n}` actually returns it — lowercase state, `merged` bool.

    Verified live at authoring time against brownm09/dev-env#886 (merged) and #410 (open):
    `{"merged":true,"merged_at":"2026-07-22T22:33:23Z","number":886,"state":"closed"}`.
    """
    row = {"number": number, "state": state, "merged": state == "closed",
           "merged_at": "2026-07-22T22:33:23Z" if state == "closed" else None}
    row.update(over)
    return row


def test_pr_state_from_row_uppercases_state() -> str:
    assert pr_state_from_row(_rest_pr(410, "open")) == "OPEN"
    assert pr_state_from_row({"number": 1, "state": "closed", "merged": False}) == "CLOSED"
    assert should_remove(pr_state_from_row(_rest_pr(410, "open"))) is False
    assert should_remove(pr_state_from_row({"number": 1, "state": "closed", "merged": False})) is True, \
        "the normalized values must satisfy should_remove's case-sensitive contract"
    return 'REST "open"/"closed" are upper-cased into the vocabulary should_remove expects'


def test_pr_state_from_row_distinguishes_merged_from_closed() -> str:
    # MERGED is not a REST `state`: both arrive as state="closed" and differ only in the
    # merge signal. Collapsing them would NOT change what gets pruned (should_remove accepts
    # both), which is exactly why the regression would go unnoticed — while every "removed
    # stale entries" line mislabelled a merge as a closure, and classify_deletions lost the
    # distinction it buckets on.
    merged = {"number": 886, "state": "closed", "merged": True, "merged_at": "2026-07-22T22:33:23Z"}
    closed = {"number": 887, "state": "closed", "merged": False, "merged_at": None}
    assert pr_state_from_row(merged) == "MERGED", "a merged PR must not read as CLOSED"
    assert pr_state_from_row(closed) == "CLOSED", "a closed-unmerged PR must not read as MERGED"
    assert should_remove("MERGED") is True and should_remove("CLOSED") is True, \
        "both still prune — which is why only this assertion catches the collapse"
    return "state=closed + merged distinguishes MERGED from CLOSED (no REST `state` does)"


def test_pr_state_from_row_detects_merge_from_merged_at_alone() -> str:
    # The `GET /pulls` LIST endpoint (pull-request-simple) omits `merged` ENTIRELY and
    # carries only `merged_at` — verified live. Honouring either signal keeps this helper
    # correct for both shapes, so a future move to a list-based batch cannot quietly start
    # reporting every merged PR as CLOSED.
    list_shape = {"number": 886, "state": "closed", "merged_at": "2026-07-22T22:33:23Z"}
    assert "merged" not in list_shape, "fixture must reproduce the list shape's missing key"
    assert pr_state_from_row(list_shape) == "MERGED"
    assert pr_state_from_row({"number": 887, "state": "closed", "merged_at": None}) == "CLOSED"
    return "merge is detected from `merged_at` alone when the `merged` key is absent (list shape)"


def test_pr_state_from_row_tolerates_junk() -> str:
    for junk in [None, [], "row", 42, {}, {"state": None}, {"state": 7}, {"state": ""}]:
        assert pr_state_from_row(junk) is None, f"must degrade to None (-> keep): {junk!r}"
        assert should_remove(pr_state_from_row(junk)) is False
    assert pr_state_from_row({"number": 1, "state": "draft"}) == "DRAFT", \
        "an unrecognised state passes through upper-cased -> should_remove keeps it"
    assert should_remove("DRAFT") is False
    return "malformed rows degrade to None (kept), never to a spurious CLOSED"


def test_pr_projection_preserves_merge_signals() -> str:
    # `_PR_PROJECTION` decides what SHAPE reaches pr_state_from_row, and it cannot be
    # executed offline (gh owns the jq), so without this gate it is the one piece of the
    # transport covered by nothing but a comment. The dangerous edit is the natural-looking
    # simplification to `{number, state}` — dropping the merge signals as redundant, since
    # `state` is "what we classify on". That makes EVERY merged PR arrive indistinguishable
    # from a closed one, with every other test in this file still green.
    #
    # Structural: pin the properties that make the projection safe, not its exact spelling.
    proj = mod._PR_PROJECTION
    for field in ("state", "merged", "merged_at"):
        assert field in proj, (
            f"the projection must carry `{field}` — without it pr_state_from_row cannot "
            "distinguish a merged PR from a closed one"
        )
    assert "select(" not in proj, \
        ("the projection must not classify: that belongs in pr_state_from_row, where it is "
         "testable, not in an unexecutable jq string")
    return "the jq projection carries state + BOTH merge signals, and classifies nothing itself"


def test_rest_rows_prune_end_to_end() -> str:
    # The inertness caveat, pinned at the only level that catches it. If `.upper()` is ever
    # dropped, every per-function test above still passes while the hook silently stops
    # pruning anything — fail-safe in direction, total in effect, reported nowhere. Only a
    # chain that runs raw REST rows all the way to the `unlink` goes red when that happens.
    # The merged/closed split is asserted here too, on the states the message text renders.
    url = "https://github.com/brownm09/dev-env/pull/{n}".format
    rows = {886: _rest_pr(886, "closed"),                                    # merged
            887: {"number": 887, "state": "closed", "merged": False,
                  "merged_at": None},                                        # closed, unmerged
            888: _rest_pr(888, "open")}                                      # open
    with tempfile.TemporaryDirectory() as root:
        shard_dir = Path(root) / "open-prs"
        _write_shard(shard_dir, 886, url(n=886))
        _write_shard(shard_dir, 887, url(n=887))
        survivor = _write_shard(shard_dir, 888, url(n=888))
        survivor_bytes = survivor.read_bytes()

        surviving, removed = reconcile_shard_dir(
            shard_dir, state_fn=lambda pr, repo: pr_state_from_row(rows[pr]))

        assert not (shard_dir / "886.json").exists(), \
            'a REST lowercase "closed" must actually unlink the shard'
        assert not (shard_dir / "887.json").exists(), "a closed-unmerged PR is pruned too"
        assert survivor.exists(), "the open shard must survive"
        assert survivor.read_bytes() == survivor_bytes, \
            "survivor must be byte-identical (per-file unlink, never a rewrite — ADR-056)"
        assert [e["pr"] for e in surviving] == [888]
        # Built from the shared `_entry` fixture, not hand-written dicts: a future field added
        # to the tracking-shard schema (ADR-119 just added `pr` cross-checking) would otherwise
        # break this test for a reason unrelated to the transport it pins. (/review finding.)
        assert sorted(removed, key=lambda t: t[0]["pr"]) == [
            (_entry(886, url(n=886)), "MERGED"),
            (_entry(887, url(n=887)), "CLOSED"),
        ], "the reported states must stay MERGED vs CLOSED, not collapse to one value"
    return 'raw REST rows -> unlink: lowercase "closed" prunes, and MERGED/CLOSED stay distinct'


# --- hook-wide lookup budget (dev-env#888) -----------------------------------


def test_budgeted_state_fn_passes_through_within_budget() -> str:
    calls = []
    ticks = iter([0.0, 0.0, 1.0, 2.0])
    budget = WorkBudget(budget=10.0, clock=lambda: next(ticks))
    gated = budgeted_state_fn(lambda pr, repo: calls.append(pr) or "MERGED", budget)
    assert gated(1, "o/r") == "MERGED" and gated(2, "o/r") == "MERGED"
    assert calls == [1, 2], f"lookups inside the budget must all run, got {calls}"
    return "within budget, every lookup reaches the wrapped state_fn unchanged"


def test_budgeted_state_fn_short_circuits_once_spent() -> str:
    # A slow/hanging gh must degrade to "unresolved, all kept" rather than let the hook be
    # killed at its settings.json timeout — which would lose the whole systemMessage,
    # including the `Open PRs:` line that is this hook's original ADR-018 job.
    calls = []
    ticks = iter([0.0, 0.0, 99.0, 99.0])
    budget = WorkBudget(budget=10.0, clock=lambda: next(ticks))
    gated = budgeted_state_fn(lambda pr, repo: calls.append(pr) or "MERGED", budget)
    assert gated(1, "o/r") == "MERGED", "the first lookup is inside the budget"
    assert gated(2, "o/r") is None, "past the budget the lookup must not be issued"
    assert calls == [1], f"the over-budget lookup must not spawn a subprocess, got {calls}"
    assert should_remove(None) is False, "and None is conservatively KEPT, never pruned"
    return "a spent budget short-circuits to None -> kept, never a mis-prune"


def test_budget_exhaustion_keeps_every_shard() -> str:
    # The load-bearing consequence, end-to-end: an exhausted budget must leave every shard on
    # disk even when the underlying oracle would say MERGED for all of them.
    with tempfile.TemporaryDirectory() as root:
        shard_dir = Path(root) / "open-prs"
        a = _write_shard(shard_dir, 386, URL_386)
        b = _write_shard(shard_dir, 387, URL_387)
        budget = WorkBudget(budget=0.0, clock=lambda: 0.0)  # spent from the first check
        surviving, removed = reconcile_shard_dir(
            shard_dir, state_fn=budgeted_state_fn(lambda pr, repo: "MERGED", budget))
        assert a.exists() and b.exists(), "no shard may be unlinked once the budget is spent"
        assert removed == [] and [e["pr"] for e in surviving] == [386, 387]
    return "budget exhaustion keeps every shard, even against an all-MERGED oracle"


def test_counting_state_fn_counts_only_unresolved() -> str:
    tally = {"unresolved": 0}
    states = {1: "MERGED", 2: None, 3: "OPEN", 4: None}
    counted = counting_state_fn(lambda pr, repo: states[pr], tally)
    got = [counted(pr, "o/r") for pr in (1, 2, 3, 4)]
    assert got == ["MERGED", None, "OPEN", None], "the state must pass through unchanged"
    assert tally["unresolved"] == 2, f"only the None lookups count, got {tally}"
    return "unresolved lookups are counted; resolved ones pass through untouched"


def test_unresolved_count_equals_unconfirmed_survivors() -> str:
    # The count exists to qualify the `Open PRs:` line, so what it must equal is the number of
    # entries listed there WITHOUT GitHub having confirmed them. A malformed entry takes no
    # lookup and is reported separately, so it must not inflate the figure.
    with tempfile.TemporaryDirectory() as root:
        shard_dir = Path(root) / "open-prs"
        _write_shard(shard_dir, 386, URL_386)          # resolves OPEN -> confirmed
        _write_shard(shard_dir, 387, URL_387)          # resolves None -> unconfirmed survivor
        (shard_dir / "99.json").write_text(json.dumps({"topic": "x"}), encoding="utf-8")  # no lookup

        tally = {"unresolved": 0}
        state_fn = counting_state_fn(
            lambda pr, repo: "OPEN" if pr == 386 else None, tally)
        surviving, removed = reconcile_shard_dir(shard_dir, state_fn=state_fn)

        assert [e["pr"] for e in surviving] == [386, 387], "both survive (None is kept)"
        assert removed == []
        assert tally["unresolved"] == 1, \
            f"exactly the one unconfirmed survivor, not the malformed shard, got {tally}"
    return "the unresolved count equals the survivors GitHub never confirmed (malformed excluded)"


def test_work_budget_cannot_outrun_the_hook_timeout() -> str:
    # `WorkBudget` gates the START of every lookup and nothing caps their sum otherwise, so
    # the hook's true ceiling is WORK_BUDGET_SECONDS + one in-flight call's own timeout.
    # That arithmetic is what makes the bound true however many lookup segments are added
    # later — and until now it would have lived only in a comment. Raising a timeout or the
    # budget (plausible: a slow network makes GH_CALL_TIMEOUT look stingy) would silently
    # reintroduce the kill this exists to prevent. Nothing else goes red: the symptom is a
    # hook that occasionally emits nothing, in a session nobody is timing.
    # (The /review finding on dev-env#886, applied to its sibling.)
    #
    # NONLOOKUP_RESERVE_SECONDS is a TERM here, not the leftover: the hook also does ungated
    # local work (heartbeat, the shared-scratch sentinel sweep, the stdin read, message
    # assembly). Leaving that implicit let the assertion claim more coverage than it had —
    # and a kill is unrecoverable, since mark_done() fires before any of this. (/review on #897.)
    worst_case = (mod.WORK_BUDGET_SECONDS
                  + max(mod.GH_CALL_TIMEOUT, mod.GIT_CALL_TIMEOUT)
                  + mod.NONLOOKUP_RESERVE_SECONDS)
    assert worst_case <= mod.HOOK_TIMEOUT_SECONDS, (
        f"the hook can run {worst_case}s against a {mod.HOOK_TIMEOUT_SECONDS}s settings.json "
        f"timeout — either lower WORK_BUDGET_SECONDS ({mod.WORK_BUDGET_SECONDS}) / "
        f"GH_CALL_TIMEOUT ({mod.GH_CALL_TIMEOUT}) / GIT_CALL_TIMEOUT ({mod.GIT_CALL_TIMEOUT}) / "
        f"NONLOOKUP_RESERVE_SECONDS ({mod.NONLOOKUP_RESERVE_SECONDS}), or raise the declared "
        "timeout in claude/settings.json to match"
    )
    assert mod.NONLOOKUP_RESERVE_SECONDS > 0, \
        "the reserve must be a real allowance, not a zeroed-out term that re-hides the gap"
    return ("WORK_BUDGET_SECONDS + max(per-call timeout) + NONLOOKUP_RESERVE_SECONDS stays "
            "within HOOK_TIMEOUT_SECONDS (invariant, not a comment)")


def main() -> int:
    tests = [
        ("should_remove predicate", test_should_remove),
        ("repo_from_url extraction", test_repo_from_url),
        ("entry_repo_and_pr resolution", test_entry_repo_and_pr),
        ("project_dirs is the shared helper", test_project_dirs_is_shared_helper),
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
        ("REST: state upper-cased", test_pr_state_from_row_uppercases_state),
        ("REST: MERGED distinguished from CLOSED", test_pr_state_from_row_distinguishes_merged_from_closed),
        ("REST: merge detected from merged_at alone (list shape)", test_pr_state_from_row_detects_merge_from_merged_at_alone),
        ("REST: malformed rows -> None, never CLOSED", test_pr_state_from_row_tolerates_junk),
        ("REST: jq projection preserves both merge signals", test_pr_projection_preserves_merge_signals),
        ("REST: raw rows prune end-to-end, MERGED/CLOSED distinct", test_rest_rows_prune_end_to_end),
        ("budget: lookups pass through within budget", test_budgeted_state_fn_passes_through_within_budget),
        ("budget: short-circuits to None once spent", test_budgeted_state_fn_short_circuits_once_spent),
        ("budget: exhaustion keeps every shard", test_budget_exhaustion_keeps_every_shard),
        ("budget: unresolved lookups counted", test_counting_state_fn_counts_only_unresolved),
        ("budget: count equals unconfirmed survivors", test_unresolved_count_equals_unconfirmed_survivors),
        ("budget stays within the hook timeout", test_work_budget_cannot_outrun_the_hook_timeout),
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
