#!/usr/bin/env python3
"""Tests for journal-shard-write-advisory.py — the write-time PostToolUse hook that
validates engineering-journal shards touched by Write/Edit/Bash (dev-env #556, ADR-081).

Exercises every pure helper offline (no stdin plumbing, no real `~/Git/engineering-journal`
checkout — `isfile` is injected wherever the helper touches disk, except `collect_problems`,
which is exercised against real `tempfile.TemporaryDirectory()` fixtures per the repo's
established convention for hooks whose impure surface is filesystem-only, e.g.
`test_hookutil.py` / `test_prune_merged_worktrees.py`). `main()`'s stdin plumbing is not
covered (pure-helper convention, matches every other test_*.py in this directory).

Cases pinned — see the module docstring of journal-shard-write-advisory.py for why the
Bash token harvest is a raw regex scan rather than `_hookio.scan_top_level`-anchored (it
validates on-disk data, not command intent, so over-matching a heredoc/quoted/subshell
mention is harmless — this also means `test_no_crude_command_substring_checks.py`'s AST
gate does not apply to this file's regex-based approach).
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import importlib.util
from pathlib import Path

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "journal-shard-write-advisory.py")
_spec = importlib.util.spec_from_file_location("journal_shard_write_advisory", _SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {_SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

classify_shard_path = mod.classify_shard_path
extract_candidate_tokens = mod.extract_candidate_tokens
extract_base_dirs = mod.extract_base_dirs
resolve_candidates = mod.resolve_candidates
candidate_paths = mod.candidate_paths
validate_shard_bytes = mod.validate_shard_bytes
collect_problems = mod.collect_problems
format_advisory = mod.format_advisory
normalize_path = mod.normalize_path
JOURNAL_FALLBACK = mod.JOURNAL_FALLBACK
MAX_SHARD_BYTES = mod.MAX_SHARD_BYTES


# ---------------------------------------------------------------------------
# classify_shard_path
# ---------------------------------------------------------------------------

def test_classify_canonical_manifest_path():
    p = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/2026-07-03_170000.manifest.jsonl"
    assert classify_shard_path(p) == "manifest"

def test_classify_backslash_form():
    p = r"C:\Users\brown\Git\engineering-journal\sessions\dev-env\2026-07-03_170000.manifest.jsonl"
    assert classify_shard_path(p) == "manifest"

def test_classify_worktree_nested_journal_path():
    p = ("C:/Users/brown/Git/engineering-journal/.claude/worktrees/funny-agnesi-9a3219/"
         "sessions/meta/open-prs/147.json")
    assert classify_shard_path(p) == "open-pr"

def test_classify_underscore_spelling():
    p = "C:/Users/brown/Git/engineering_journal/sessions/dev-env/2026-07-03_170000.manifest.jsonl"
    assert classify_shard_path(p) == "manifest"

def test_classify_open_pr_shard():
    p = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/open-prs/556.json"
    assert classify_shard_path(p) == "open-pr"

def test_classify_legacy_open_prs_jsonl_not_matched():
    p = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/open-prs.jsonl"
    assert classify_shard_path(p) is None

def test_classify_non_journal_repo_with_manifest_suffix():
    p = "C:/Users/brown/Git/some-other-repo/sessions/dev-env/x.manifest.jsonl"
    assert classify_shard_path(p) is None

def test_classify_journal_path_missing_sessions_component():
    p = "C:/Users/brown/Git/engineering-journal/README.manifest.jsonl"
    assert classify_shard_path(p) is None

def test_classify_stub_file_not_matched():
    p = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/2026-07-03_170000.stub.md"
    assert classify_shard_path(p) is None

def test_classify_reports_json_not_open_pr():
    p = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/reports/2026-07-03-x.json"
    assert classify_shard_path(p) is None

def test_classify_git_bash_absolute_form():
    p = "/c/Users/brown/Git/engineering-journal/sessions/dev-env/open-prs/1.json"
    assert classify_shard_path(p) == "open-pr"

def test_classify_empty_string():
    assert classify_shard_path("") is None


# ---------------------------------------------------------------------------
# extract_candidate_tokens
# ---------------------------------------------------------------------------

def test_extract_redirect_target():
    cmd = ('echo \'{"pr":54}\' > '
           '"C:/Users/brown/Git/engineering-journal/sessions/dev-env/open-prs/54.json"')
    tokens = extract_candidate_tokens(cmd)
    assert "C:/Users/brown/Git/engineering-journal/sessions/dev-env/open-prs/54.json" in tokens

def test_extract_heredoc_target():
    cmd = ("cat > C:/Users/brown/Git/engineering-journal/sessions/dev-env/"
           "2026-07-03_170000.manifest.jsonl <<'EOF'\n{\"a\":1}\nEOF")
    tokens = extract_candidate_tokens(cmd)
    assert ("C:/Users/brown/Git/engineering-journal/sessions/dev-env/"
            "2026-07-03_170000.manifest.jsonl") in tokens

def test_extract_node_e_quoted_js_path():
    # The exact prs_closed-update recipe from docs/REFERENCE.md.
    cmd = (
        "node -e \"\n"
        "  const fs = require('fs');\n"
        "  const path = 'C:/Users/brown/Git/engineering-journal/sessions/dev-env/"
        "2026-07-03_170000.manifest.jsonl';\n"
        "  const o = JSON.parse(fs.readFileSync(path,'utf8'));\n"
        "  o.prs_closed = [556];\n"
        "  fs.writeFileSync(path, JSON.stringify(o) + '\\n');\n"
        "\""
    )
    tokens = extract_candidate_tokens(cmd)
    assert ("C:/Users/brown/Git/engineering-journal/sessions/dev-env/"
            "2026-07-03_170000.manifest.jsonl") in tokens

def test_extract_multi_path_git_add():
    cmd = "git add sessions/meta/x.manifest.jsonl sessions/meta/open-prs/147.json"
    tokens = extract_candidate_tokens(cmd)
    assert "sessions/meta/x.manifest.jsonl" in tokens
    assert "sessions/meta/open-prs/147.json" in tokens

def test_extract_rm_f():
    cmd = "rm -f sessions/meta/open-prs/54.json"
    assert extract_candidate_tokens(cmd) == ["sessions/meta/open-prs/54.json"]

def test_extract_legacy_open_prs_jsonl_not_matched():
    assert extract_candidate_tokens("cat sessions/meta/open-prs.jsonl") == []

def test_extract_no_tokens():
    assert extract_candidate_tokens("git status") == []

def test_extract_cap_at_20():
    cmd = " ".join(f"sessions/p/f{i}.manifest.jsonl" for i in range(30))
    assert len(extract_candidate_tokens(cmd)) == 20

def test_extract_skips_oversized_command():
    # dev-env#556 /review finding: the token regexes lead with an unbounded greedy
    # class followed by a required literal suffix -- O(n^2) via re.findall's
    # per-start-position retries when the suffix never appears (verified: ~10s for
    # one regex against a 40,000-char run of plain word characters). A command over
    # MAX_COMMAND_CHARS must be skipped entirely, before either regex runs.
    oversized = "a" * (mod.MAX_COMMAND_CHARS + 1)
    assert extract_candidate_tokens(oversized) == []

def test_extract_pathological_input_completes_quickly():
    # Regression proof for the fix above: a much larger pathological blob (no path
    # suffix anywhere) must still return promptly rather than hang for seconds.
    import time
    blob = "a" * 200_000
    start = time.time()
    result = extract_candidate_tokens(blob)
    elapsed = time.time() - start
    assert result == []
    assert elapsed < 1.0, f"took {elapsed:.2f}s -- cap did not bound the regex scan"


# ---------------------------------------------------------------------------
# extract_base_dirs / resolve_candidates
# ---------------------------------------------------------------------------

def test_base_dirs_cwd_first():
    bases = extract_base_dirs("git status", "C:/Users/brown/Git/dev-env")
    assert bases[0] == "C:/Users/brown/Git/dev-env"

def test_base_dirs_includes_git_dash_c_dir():
    cmd = "git -C C:/Users/brown/Git/engineering-journal add sessions/x.manifest.jsonl"
    bases = extract_base_dirs(cmd, "C:/Users/brown/Git/dev-env")
    assert "C:/Users/brown/Git/engineering-journal" in bases

def test_base_dirs_includes_journal_fallback_last():
    bases = extract_base_dirs("git status", "")
    assert bases[-1] == normalize_path(str(JOURNAL_FALLBACK))

def test_resolve_absolute_hit():
    target = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/x.manifest.jsonl"
    result = resolve_candidates([target], [], isfile=lambda p: p == target)
    assert result == [target]

def test_resolve_absolute_miss():
    result = resolve_candidates(["C:/nope/x.manifest.jsonl"], [], isfile=lambda p: False)
    assert result == []

def test_resolve_relative_via_cwd_base():
    target = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/x.manifest.jsonl"
    result = resolve_candidates(
        ["sessions/dev-env/x.manifest.jsonl"],
        ["C:/Users/brown/Git/engineering-journal"],
        isfile=lambda p: p == target,
    )
    assert result == [target]

def test_resolve_relative_via_git_dash_c_base_when_cwd_misses():
    target = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/x.manifest.jsonl"
    result = resolve_candidates(
        ["sessions/dev-env/x.manifest.jsonl"],
        ["C:/Users/brown/Git/dev-env", "C:/Users/brown/Git/engineering-journal"],
        isfile=lambda p: p == target,
    )
    assert result == [target]

def test_resolve_relative_via_journal_fallback():
    target = normalize_path(str(JOURNAL_FALLBACK)) + "/sessions/dev-env/x.manifest.jsonl"
    result = resolve_candidates(
        ["sessions/dev-env/x.manifest.jsonl"],
        ["C:/Users/brown/Git/dev-env", normalize_path(str(JOURNAL_FALLBACK))],
        isfile=lambda p: p == target,
    )
    assert result == [target]

def test_resolve_drops_when_no_base_matches():
    result = resolve_candidates(
        ["sessions/dev-env/x.manifest.jsonl"], ["C:/Users/brown/Git/dev-env"], isfile=lambda p: False
    )
    assert result == []


# ---------------------------------------------------------------------------
# validate_shard_bytes
# ---------------------------------------------------------------------------

def _valid_manifest_entry(**overrides):
    base = {
        "stub": "sessions/dev-env/2026-07-03_170000.stub.md",
        "topic": "t",
        "tokens": {"input": 1, "output": 1, "cost": 0.0},
        "prs_opened": [],
        "prs_closed": [],
    }
    base.update(overrides)
    return base

def _valid_open_pr_entry(**overrides):
    base = {
        "pr": 147,
        "url": "https://github.com/brownm09/dev-env/pull/147",
        "topic": "t",
        "stub": "y.stub.md",
        "opened": "2026-07-02",
    }
    base.update(overrides)
    return base

def test_validate_healthy_manifest():
    raw = json.dumps(_valid_manifest_entry()).encode("utf-8")
    assert validate_shard_bytes(raw, "manifest", "2026-07-03_170000") == []

def test_validate_manifest_missing_topic_tokens():
    entry = _valid_manifest_entry()
    del entry["topic"]
    del entry["tokens"]
    raw = json.dumps(entry).encode("utf-8")
    assert validate_shard_bytes(raw, "manifest", "s") == ["missing topic, tokens"]

def test_validate_manifest_bom_and_missing_fields_both_reported():
    entry = _valid_manifest_entry()
    del entry["topic"]
    del entry["tokens"]
    raw = b"\xef\xbb\xbf" + json.dumps(entry).encode("utf-8")
    problems = validate_shard_bytes(raw, "manifest", "s")
    assert problems == ["UTF-8 BOM", "missing topic, tokens"]

def test_validate_open_pr_utf8_bom():
    raw = b"\xef\xbb\xbf" + json.dumps(_valid_open_pr_entry()).encode("utf-8")
    problems = validate_shard_bytes(raw, "open-pr", "147", num_from_name=147)
    assert problems == ["UTF-8 BOM"]

def test_validate_open_pr_summary_instead_of_topic():
    entry = _valid_open_pr_entry()
    del entry["topic"]
    entry["summary"] = "session text"
    raw = json.dumps(entry).encode("utf-8")
    problems = validate_shard_bytes(raw, "open-pr", "147", num_from_name=147)
    assert problems == ["missing topic"]

def test_validate_manifest_unparseable_line_with_lineno():
    problems = validate_shard_bytes(b"not json at all", "manifest", "x")
    assert problems == ["line 1: not a JSON object"]

def test_validate_manifest_legacy_multientry_one_bad_line():
    good = json.dumps(_valid_manifest_entry(stub="s1.stub.md"))
    raw = (good + "\nnot json\n").encode("utf-8")
    assert validate_shard_bytes(raw, "manifest", "x") == ["line 2: not a JSON object"]

def test_validate_empty_file_manifest():
    assert validate_shard_bytes(b"", "manifest", "x") == ["empty shard (no JSON object)"]

def test_validate_empty_file_open_pr():
    # Numeric stem/num_from_name isolates this from the non-numeric-filename path
    # (covered separately by test_validate_open_pr_non_numeric_stem_reported_even_when_empty).
    assert validate_shard_bytes(b"   \n  ", "open-pr", "1", num_from_name=1) == ["empty shard (no JSON object)"]

def test_validate_non_dict_json():
    raw = b'["not", "a", "dict"]'
    assert validate_shard_bytes(raw, "open-pr", "1", num_from_name=1) == ["not a JSON object"]

def test_validate_open_pr_non_numeric_stem():
    raw = json.dumps(_valid_open_pr_entry(pr=1)).encode("utf-8")
    problems = validate_shard_bytes(raw, "open-pr", "index", num_from_name=None)
    assert len(problems) == 1
    assert "non-numeric filename 'index.json'" in problems[0]
    assert "reconcile/post-compact/compose" in problems[0]

def test_validate_open_pr_stem_pr_mismatch():
    raw = json.dumps(_valid_open_pr_entry(pr=148)).encode("utf-8")
    problems = validate_shard_bytes(raw, "open-pr", "147", num_from_name=147)
    assert problems == ["filename stem '147' does not match embedded pr=148"]

def test_validate_open_pr_matching_stem_pr_silent():
    raw = json.dumps(_valid_open_pr_entry(pr=147)).encode("utf-8")
    assert validate_shard_bytes(raw, "open-pr", "147", num_from_name=147) == []

def test_validate_open_pr_non_numeric_stem_reported_even_when_empty():
    # dev-env#556 /review finding: the non-numeric-filename check must fire even when
    # the shard is ALSO empty/malformed -- previously the empty-shard early return
    # meant the (arguably more important) filename diagnosis never surfaced.
    problems = validate_shard_bytes(b"", "open-pr", "index", num_from_name=None)
    assert problems[0] == (
        "non-numeric filename 'index.json' - invisible to every open-PR reader "
        "(reconcile/post-compact/compose)"
    )
    assert "empty shard (no JSON object)" in problems

def test_validate_open_pr_non_numeric_stem_reported_even_when_not_json():
    problems = validate_shard_bytes(b"not json", "open-pr", "index", num_from_name=None)
    assert problems[0].startswith("non-numeric filename 'index.json'")
    assert "not a JSON object" in problems


# ---------------------------------------------------------------------------
# format_advisory
# ---------------------------------------------------------------------------

def test_format_advisory_multi_file_aggregation():
    problems = [
        ("C:/a/x.manifest.jsonl", ["missing topic, tokens"]),
        ("C:/a/open-prs/1.json", ["UTF-8 BOM"]),
    ]
    text = format_advisory(problems)
    assert "C:/a/x.manifest.jsonl: missing topic, tokens" in text
    assert "C:/a/open-prs/1.json: UTF-8 BOM" in text

def test_format_advisory_contains_schema_templates():
    text = format_advisory([("C:/a/x.manifest.jsonl", ["missing topic"])])
    assert '"stub":"sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md"' in text
    assert '"pr":N,"url":"https://github.com/<owner>/<repo>/pull/N"' in text

def test_format_advisory_full_field_set_sentence_present():
    text = format_advisory([("C:/a/x.manifest.jsonl", ["missing topic"])])
    assert "FULL field set" in text

def test_format_advisory_is_ascii_and_cp1252_safe():
    problems = [("C:/a/x.manifest.jsonl", ["missing topic, tokens", "UTF-8 BOM"])]
    text = format_advisory(problems)
    assert text.isascii()
    text.encode("cp1252")  # must not raise

def test_format_advisory_caps_files_shown():
    problems = [(f"C:/a/f{i}.manifest.jsonl", ["missing topic"]) for i in range(15)]
    text = format_advisory(problems)
    assert "... and 5 more files with problems (not shown)" in text


# ---------------------------------------------------------------------------
# candidate_paths
# ---------------------------------------------------------------------------

def test_candidate_paths_write_passthrough():
    result = candidate_paths("Write", {"file_path": "C:/a/x.manifest.jsonl"}, "C:/cwd")
    assert result == ["C:/a/x.manifest.jsonl"]

def test_candidate_paths_edit_passthrough():
    result = candidate_paths("Edit", {"file_path": "C:/a/x.manifest.jsonl"}, "C:/cwd")
    assert result == ["C:/a/x.manifest.jsonl"]

def test_candidate_paths_bash_harvest():
    target = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/x.manifest.jsonl"
    result = candidate_paths(
        "Bash",
        {"command": "git add sessions/dev-env/x.manifest.jsonl"},
        "C:/Users/brown/Git/engineering-journal",
        isfile=lambda p: p == target,
    )
    assert result == [target]

def test_candidate_paths_powershell_harvest():
    # dev-env#763 review: main()'s outer gate widened to accept "PowerShell", but
    # this function's own internal dispatch originally still checked
    # `tool_name == "Bash"` literally, so a PowerShell-run command silently
    # harvested nothing even after the outer gate accepted it. Mirrors
    # test_candidate_paths_bash_harvest with tool_name="PowerShell".
    target = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/x.manifest.jsonl"
    result = candidate_paths(
        "PowerShell",
        {"command": "git add sessions/dev-env/x.manifest.jsonl"},
        "C:/Users/brown/Git/engineering-journal",
        isfile=lambda p: p == target,
    )
    assert result == [target]

def test_candidate_paths_other_tool_returns_empty():
    assert candidate_paths("Read", {"file_path": "x"}, "cwd") == []

def test_candidate_paths_missing_tool_input_returns_empty():
    assert candidate_paths("Write", {}, "cwd") == []
    assert candidate_paths("Bash", {}, "cwd") == []


# ---------------------------------------------------------------------------
# collect_problems (real tempfile fixtures — this hook's impure surface is
# filesystem-only, matching test_hookutil.py / test_prune_merged_worktrees.py)
# ---------------------------------------------------------------------------

def test_collect_problems_healthy_shard_reports_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "engineering-journal", "sessions", "dev-env")
        os.makedirs(d)
        path = os.path.join(d, "2026-07-03_170000.manifest.jsonl").replace("\\", "/")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(_valid_manifest_entry()))
        assert collect_problems([path]) == []

def test_collect_problems_broken_shard_reported():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "engineering-journal", "sessions", "dev-env")
        os.makedirs(d)
        path = os.path.join(d, "2026-07-03_170000.manifest.jsonl").replace("\\", "/")
        entry = _valid_manifest_entry()
        del entry["topic"]
        del entry["tokens"]
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(entry))
        result = collect_problems([path])
        assert result == [(path, ["missing topic, tokens"])]

def test_collect_problems_non_journal_path_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "x.manifest.jsonl").replace("\\", "/")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not even json")
        assert collect_problems([path]) == []

def test_collect_problems_nonexistent_path_skipped():
    path = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/does-not-exist.manifest.jsonl"
    assert collect_problems([path]) == []

def test_collect_problems_oversized_shard_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "engineering-journal", "sessions", "dev-env")
        os.makedirs(d)
        path = os.path.join(d, "2026-07-03_170000.manifest.jsonl").replace("\\", "/")
        with open(path, "wb") as f:
            f.write(b"x" * (MAX_SHARD_BYTES + 1))
        assert collect_problems([path]) == []


# ---------------------------------------------------------------------------
# Tile shards (ADR-118 enforcement, dev-env#870)
# ---------------------------------------------------------------------------

_TILE_OK = {
    "issue": 870,
    "url": "https://github.com/brownm09/dev-env/issues/870",
    "title": "Tile persistence PR3",
    "tldr": "enforce the shard write",
    "prompt": "the full self-contained spawn_task prompt",
    "cwd": "C:/Users/brown/Git/dev-env",
    "spawned": "2026-07-22",
}


def _tile_bytes(**over):
    entry = dict(_TILE_OK)
    entry.update(over)
    return json.dumps(entry).encode("utf-8")


def test_classify_tile_shard():
    p = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/tiles/870.json"
    assert classify_shard_path(p) == "tile"


def test_classify_tile_shard_backslash_form():
    p = r"C:\Users\brown\Git\engineering-journal\sessions\dev-env\tiles\870.json"
    assert classify_shard_path(p) == "tile"


def test_classify_tile_shard_in_journal_worktree():
    p = ("C:/Users/brown/Git/engineering-journal/.claude/worktrees/funny-agnesi-9a3219/"
         "sessions/meta/tiles/12.json")
    assert classify_shard_path(p) == "tile"


def test_classify_tile_outside_journal_is_none():
    # A `tiles/<N>.json` under any non-journal repo must not be validated as a shard --
    # the journal-component requirement is what keeps this hook off unrelated files.
    assert classify_shard_path("C:/Users/brown/Git/dev-env/sessions/x/tiles/1.json") is None


def test_validate_tile_healthy():
    assert validate_shard_bytes(_tile_bytes(), "tile", "870", num_from_name=870) == []


def test_validate_tile_missing_fields():
    raw = json.dumps({"issue": 870, "url": _TILE_OK["url"]}).encode("utf-8")
    problems = validate_shard_bytes(raw, "tile", "870", num_from_name=870)
    assert problems == ["missing title, tldr, prompt, cwd, spawned"], problems


def test_validate_tile_missing_prompt_only():
    # `prompt` is the field whose loss is total: the issue survives without it, but the
    # exact re-spawn -- the entire point of the shard -- does not.
    entry = dict(_TILE_OK)
    del entry["prompt"]
    problems = validate_shard_bytes(json.dumps(entry).encode("utf-8"), "tile", "870",
                                    num_from_name=870)
    assert problems == ["missing prompt"], problems


def test_validate_tile_issue_field_disagrees_with_filename():
    # reconcile-pending-tiles.py skips a disagreeing shard as corrupt, so it would never
    # be pruned -- flagging it at write time is the only cheap moment to fix it.
    problems = validate_shard_bytes(_tile_bytes(issue=999), "tile", "870", num_from_name=870)
    assert len(problems) == 1, problems
    assert "does not match embedded issue=999" in problems[0]
    assert "skips this shard as corrupt" in problems[0]


def test_validate_tile_non_numeric_filename():
    problems = validate_shard_bytes(_tile_bytes(), "tile", "index", num_from_name=None)
    assert any("non-numeric filename 'index.json'" in p for p in problems), problems
    assert any("the filename IS the issue key" in p for p in problems), problems


def test_validate_tile_stub_is_optional():
    # `stub` is deliberately NOT required (ADR-118): the tiling rule fires the moment a
    # follow-up is identified, while stub triggers are PR-open/PR-merge/report -- so a
    # session that tiles something in passing legitimately writes no stub.
    assert validate_shard_bytes(_tile_bytes(), "tile", "870", num_from_name=870) == []
    assert validate_shard_bytes(
        _tile_bytes(stub="sessions/dev-env/2026-07-22_143749.stub.md"),
        "tile", "870", num_from_name=870) == []


def test_validate_tile_utf8_bom_and_missing_field_both_reported():
    entry = dict(_TILE_OK)
    del entry["cwd"]
    raw = b"\xef\xbb\xbf" + json.dumps(entry).encode("utf-8")
    problems = validate_shard_bytes(raw, "tile", "870", num_from_name=870)
    assert len(problems) == 2, problems
    assert any("missing cwd" in p for p in problems), problems


def test_validate_tile_not_a_json_object():
    assert validate_shard_bytes(b"[1,2]", "tile", "870", num_from_name=870) == ["not a JSON object"]


def test_extract_candidate_tokens_finds_tile_paths():
    cmd = ('git add sessions/dev-env/tiles/870.json '
           'sessions/dev-env/2026-07-22_143749.manifest.jsonl '
           'sessions/dev-env/open-prs/884.json')
    tokens = extract_candidate_tokens(cmd)
    assert "sessions/dev-env/tiles/870.json" in tokens, tokens
    assert "sessions/dev-env/open-prs/884.json" in tokens, tokens
    assert any(t.endswith(".manifest.jsonl") for t in tokens), tokens


def test_collect_problems_flags_a_real_tile_shard_on_disk():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "engineering-journal", "sessions", "dev-env", "tiles")
        os.makedirs(d)
        path = os.path.join(d, "870.json").replace("\\", "/")
        with open(path, "wb") as f:
            f.write(_tile_bytes(issue=999))
        results = collect_problems([path])
        assert len(results) == 1, results
        assert "does not match embedded issue=999" in results[0][1][0]


def test_format_advisory_documents_the_tile_schema():
    text = format_advisory([("x/tiles/1.json", ["missing prompt"])])
    assert "tile schema:" in text
    assert '"prompt"' in text and '"spawned"' in text
    # The serializer warning is the one that prevents a silently-corrupt payload.
    assert "never echo" in text
    assert text.isascii(), "advisory rides exit-2 stderr, which is cp1252-decoded on Windows"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
