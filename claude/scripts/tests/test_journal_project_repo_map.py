#!/usr/bin/env python3
"""Tests for journal-project-repo-map.py -- Step 8a Source 3 slug resolution (dev-env #1045).

Exercises the resolver against fixture journal trees in ``tempfile`` directories. No network
and no ``gh``: the ``gh issue list`` call deliberately stays in the skill, so the extracted
unit is pure filesystem parsing and is fully testable offline. ``main()`` *is* covered here
(unlike the pure-helper convention most test_*.py in this directory follow) because the exit
contract and the stdout report are the observability half of the fix -- the half that turns a
recurrence into a loud failure -- so leaving them untested would miss the point of the change.

Cases pinned:

- **The dev-env#1045 regression, both halves.** ``test_regression_1045_*`` assert the two
  independent reasons Source 3 was inert, each with the old pattern applied to the same fixture
  as a control so the test fails against the old logic and passes against the new:
  (a) a ``**Repository:**`` line -- ``Repo:`` is not a substring of ``Repository:``;
  (b) a ``**Repo:**`` line whose bold markers the old pattern did not tolerate.
- **The root README is the primary source**, correlating ``**Repo:**`` with
  ``**Journal:** [sessions/<project>/`` -- keyed on the *Journal* bullet, never the heading,
  because section titles deliberately do not match directory names (``### Job Search`` ->
  ``sessions/job-search/``). ``test_root_readme_section_title_differs_from_directory`` pins that.
- **The wrong-repo trap.** ``sessions/cover-letter-runtime/README.md``'s first github.com link
  points at *career-playbook*. ``test_prose_pr_link_is_not_mistaken_for_a_repo_slug`` pins that a
  deep path (``owner/repo/pull/16``) is rejected rather than truncated to ``owner/repo``, which
  is what any "grab the first github link" heuristic would have done.
- **Precedence**: the per-project README is a fallback, consulted only when the root README has
  no matching section -- it never overrides a root-README answer.
- **Skips are named, never silent** -- absent README, absent section, and malformed target each
  produce a distinct reason string. This is the actual defect: the old code did a bare
  ``continue``, making "mapping broken" indistinguishable from "no labeled issues".
- **``query_order`` dedupes by slug**, deterministically, so two projects sharing one repo
  (``meta`` and ``dev-env`` both map to ``brownm09/dev-env`` live) produce one ``gh`` query.
- **``SOURCE3_MAPPING_EMPTY`` + exit 1** when projects exist and none resolve -- the #1045
  signature itself -- versus exit 0 for an empty tree, where nothing is wrong.
- **A root README that cannot be read is reported, not fatal**: the per-project fallback still
  runs, because a resolvable project should not be lost to an unrelated read failure.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

# ---------------------------------------------------------------------------
# Load the module under test without executing main()
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "journal-project-repo-map.py"
)
_spec = importlib.util.spec_from_file_location("journal_project_repo_map", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

build_map = mod.build_map
format_report = mod.format_report
mapping_empty = mod.mapping_empty
normalize_slug = mod.normalize_slug
parse_root_readme = mod.parse_root_readme
parse_project_readme = mod.parse_project_readme
list_projects = mod.list_projects

# The exact pattern Source 3 used before this fix, as a control. Any test that claims to pin
# the #1045 regression asserts this finds nothing on the same fixture the new code resolves --
# otherwise the test would pass just as happily against the broken implementation.
import re  # noqa: E402

OLD_PATTERN = re.compile(
    r"Repo:\s*\[([^\]]+)\]\(https://github\.com/([^/]+/[^/)]+)\)"
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_journal(root_readme=None, projects=None):
    """Build a throwaway engineering-journal tree; returns its root path.

    ``projects`` maps directory name -> README text, or None for a directory with no README.
    """
    root = tempfile.mkdtemp(prefix="s3map_")
    sessions = os.path.join(root, "sessions")
    os.makedirs(sessions)
    if root_readme is not None:
        with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as handle:
            handle.write(root_readme)
    for name, text in (projects or {}).items():
        project_dir = os.path.join(sessions, name)
        os.makedirs(project_dir)
        if text is not None:
            with open(
                os.path.join(project_dir, "README.md"), "w", encoding="utf-8"
            ) as handle:
                handle.write(text)
    return root


def _root_section(title, project, slug):
    return (
        f"### {title}\n\n"
        "One-line lead sentence.\n\n"
        f"**Repo:** [{slug}](https://github.com/{slug})\n"
        f"**Journal:** [sessions/{project}/](sessions/{project}/README.md)\n\n"
    )


def _skip_reason(result, project):
    for entry in result["skipped"]:
        if entry["project"] == project:
            return entry["reason"]
    raise AssertionError(
        f"{project!r} not in skipped: {[e['project'] for e in result['skipped']]}"
    )


def _run_main(argv_tail):
    """Run main() capturing stdout; returns (exit_code, stdout_text)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = mod.main(["journal-project-repo-map.py"] + argv_tail)
    return code, buffer.getvalue()


# ---------------------------------------------------------------------------
# The dev-env#1045 regression
# ---------------------------------------------------------------------------

def test_regression_1045_repository_spelling_resolves():
    # The live sessions/gas-lifting-logbook/README.md shape: the one project README that
    # carries a repo line at all spells it "Repository:", and "Repo:" is not a substring of
    # it (after "Repo" comes "s", not ":").
    text = (
        "# Gas Lifting Logbook -- Journal\n\n"
        "**This project is archived.**\n\n"
        "**Repository:** [brownm09/gas-lifting-logbook]"
        "(https://github.com/brownm09/gas-lifting-logbook)\n"
    )
    assert OLD_PATTERN.search(text) is None, "control: the old pattern must miss this"

    root = _make_journal(projects={"gas-lifting-logbook": text})
    try:
        result = build_map(root)
        assert result["resolved"] == {
            "gas-lifting-logbook": "brownm09/gas-lifting-logbook"
        }
        assert result["skipped"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_regression_1045_bold_markers_resolve():
    # The second, independent reason the old pattern failed: it did not tolerate the ``**``
    # bold markers that actually precede the colon in both README conventions.
    text = "# p\n\n**Repo:** [brownm09/dev-env](https://github.com/brownm09/dev-env)\n"
    assert OLD_PATTERN.search(text) is None, "control: the old pattern must miss this"

    root = _make_journal(projects={"dev-env": text})
    try:
        assert build_map(root)["resolved"] == {"dev-env": "brownm09/dev-env"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_regression_1045_live_shape_resolves_every_project_from_root_readme():
    # The whole-system regression: ten of the eleven live project READMEs carry no repo line
    # at all, so no per-project pattern can resolve them -- only the root README can. A fix
    # that widened the regex alone would resolve just gas-lifting-logbook (the end-to-end test
    # case), and look correct while leaving the other projects silently skipped.
    root_readme = "# Engineering Journal\n\n## Projects\n\n" + "".join(
        _root_section(name.title(), name, f"brownm09/{name}")
        for name in ("alpha", "beta", "gamma")
    )
    root = _make_journal(
        root_readme=root_readme,
        # No project README carries a repo line -- exactly the live situation.
        projects={name: f"# {name}\n\nProse only.\n" for name in ("alpha", "beta", "gamma")},
    )
    try:
        result = build_map(root)
        assert result["resolved"] == {
            "alpha": "brownm09/alpha",
            "beta": "brownm09/beta",
            "gamma": "brownm09/gamma",
        }
        assert result["skipped"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Root README parsing
# ---------------------------------------------------------------------------

def test_root_readme_section_title_differs_from_directory():
    # "### Job Search" -> sessions/job-search/, and "### Tech Leadership Playbooks" ->
    # sessions/tech-leadership-reference/. Keying on the heading would resolve neither; the
    # **Journal:** bullet is the only thing that names the directory.
    root_readme = (
        "# Engineering Journal\n\n## Projects\n\n"
        + _root_section("Job Search", "job-search", "brownm09/career-playbook")
        + _root_section(
            "Tech Leadership Playbooks",
            "tech-leadership-reference",
            "brownm09/tech-leadership-reference",
        )
    )
    mapping, malformed = parse_root_readme(root_readme)
    assert mapping == {
        "job-search": "brownm09/career-playbook",
        "tech-leadership-reference": "brownm09/tech-leadership-reference",
    }
    assert malformed == []


def test_root_readme_relabelled_journal_link_still_resolves():
    # The directory name is load-bearing, not the link text -- match the path, not the label.
    section = (
        "### Some Project\n\n"
        "**Repo:** [brownm09/x](https://github.com/brownm09/x)\n"
        "**Journal:** [read the journal](sessions/some-project/README.md)\n"
    )
    mapping, _ = parse_root_readme(section)
    assert mapping == {"some-project": "brownm09/x"}


def test_root_readme_section_without_repo_bullet_is_not_mapped():
    section = (
        "### Some Project\n\n"
        "**Next:** do a thing.\n"
        "**Journal:** [sessions/some-project/](sessions/some-project/README.md)\n"
    )
    mapping, malformed = parse_root_readme(section)
    assert mapping == {}
    assert malformed == []


def test_root_readme_first_section_wins_on_duplicate_journal_bullet():
    root_readme = _root_section("First", "dup", "brownm09/first") + _root_section(
        "Second", "dup", "brownm09/second"
    )
    mapping, _ = parse_root_readme(root_readme)
    assert mapping == {"dup": "brownm09/first"}


def test_root_readme_preamble_above_first_heading_is_ignored():
    # The block above the first "### " is the start-here dashboard, which carries plenty of
    # github.com links. It must not be parsed as a section.
    root_readme = (
        "# Engineering Journal\n\n"
        "<!-- start-here:begin -->\n"
        "1. **[brownm09/other#4](https://github.com/brownm09/other/issues/4) -- a thing**\n"
        "<!-- start-here:end -->\n\n"
        + _root_section("Real", "real", "brownm09/real")
    )
    mapping, _ = parse_root_readme(root_readme)
    assert mapping == {"real": "brownm09/real"}


# ---------------------------------------------------------------------------
# Per-project fallback + precedence
# ---------------------------------------------------------------------------

def test_project_readme_is_only_a_fallback_never_an_override():
    root_readme = _root_section("Canonical", "p", "brownm09/from-root")
    root = _make_journal(
        root_readme=root_readme,
        projects={
            "p": "# p\n\n**Repository:** [brownm09/from-project]"
            "(https://github.com/brownm09/from-project)\n"
        },
    )
    try:
        assert build_map(root)["resolved"] == {"p": "brownm09/from-root"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_project_readme_fallback_fires_when_root_has_no_section():
    root = _make_journal(
        root_readme="# Engineering Journal\n\n## Projects\n",
        projects={
            "p": "# p\n\n**Repository:** [brownm09/only-here]"
            "(https://github.com/brownm09/only-here)\n"
        },
    )
    try:
        result = build_map(root)
        assert result["resolved"] == {"p": "brownm09/only-here"}
        assert result["skipped"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_accepted_repo_line_spellings():
    # Both spellings, with and without bold markers, and as a list bullet.
    for line in (
        "**Repo:** [o/r](https://github.com/o/r)",
        "**Repository:** [o/r](https://github.com/o/r)",
        "Repo: [o/r](https://github.com/o/r)",
        "Repository: [o/r](https://github.com/o/r)",
        "- **Repo:** [o/r](https://github.com/o/r)",
    ):
        slug, raw = parse_project_readme(f"# t\n\n{line}\n")
        assert slug == "o/r", f"did not resolve: {line!r}"
        assert raw == "o/r"


def test_repo_link_trailing_git_suffix_and_slash_are_trimmed():
    assert normalize_slug("owner/repo.git") == "owner/repo"
    assert normalize_slug("owner/repo/") == "owner/repo"
    assert normalize_slug("  owner/repo  ") == "owner/repo"


def test_prose_mention_of_repo_mid_sentence_does_not_match():
    # The pattern is line-anchored precisely so narrative text cannot resolve a slug. The
    # control below is what makes this meaningful: the identical link resolves when it starts
    # its own line, so the miss above is the anchor working, not the link being unparseable.
    inline = "# t\n\nSee the Repo: [o/r](https://github.com/o/r) mentioned inline above.\n"
    assert parse_project_readme(inline) == (None, None)

    own_line = "# t\n\nRepo: [o/r](https://github.com/o/r)\n"
    assert parse_project_readme(own_line) == ("o/r", "o/r")


# ---------------------------------------------------------------------------
# Malformed / wrong-repo targets are rejected, not guessed at
# ---------------------------------------------------------------------------

def test_prose_pr_link_is_not_mistaken_for_a_repo_slug():
    # sessions/cover-letter-runtime/README.md's first github.com link points at
    # career-playbook -- a different repo. A deep path must be rejected outright rather than
    # truncated to its first two segments.
    assert normalize_slug("brownm09/career-playbook/issues/1066") is None
    assert normalize_slug("brownm09/brownm09/pull/16") is None


def test_malformed_project_readme_target_is_a_named_skip_not_a_silent_drop():
    root = _make_journal(
        root_readme="# Engineering Journal\n",
        projects={
            "p": "# p\n\n**Repo:** [x](https://github.com/brownm09/x/pull/16)\n"
        },
    )
    try:
        result = build_map(root)
        assert result["resolved"] == {}
        reason = _skip_reason(result, "p")
        assert "not an owner/repo slug" in reason
        assert "brownm09/x/pull/16" in reason
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_malformed_root_readme_target_is_reported_in_the_skip_reason():
    root_readme = (
        "### P\n\n"
        "**Repo:** [x](https://github.com/brownm09/x/tree/main)\n"
        "**Journal:** [sessions/p/](sessions/p/README.md)\n"
    )
    root = _make_journal(root_readme=root_readme, projects={"p": "# p\n"})
    try:
        reason = _skip_reason(build_map(root), "p")
        assert "root README.md section has a **Repo:** link" in reason
        assert "brownm09/x/tree/main" in reason
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Skips are named, never silent
# ---------------------------------------------------------------------------

def test_missing_project_readme_is_skipped_with_a_reason_not_a_crash():
    root = _make_journal(root_readme="# Engineering Journal\n", projects={"p": None})
    try:
        result = build_map(root)
        assert result["resolved"] == {}
        reason = _skip_reason(result, "p")
        assert "no ### section in root README.md" in reason
        assert "sessions/p/README.md unreadable" in reason
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_project_readme_without_any_repo_line_is_skipped_with_a_reason():
    root = _make_journal(
        root_readme="# Engineering Journal\n",
        projects={"p": "# p -- Journal\n\nProse only, no repo line.\n"},
    )
    try:
        reason = _skip_reason(build_map(root), "p")
        assert "has no Repo:/Repository: link line" in reason
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unreadable_root_readme_is_reported_but_fallback_still_resolves():
    root = _make_journal(
        # No root README at all.
        projects={
            "p": "# p\n\n**Repository:** [o/r](https://github.com/o/r)\n",
            "q": "# q\n\nProse only.\n",
        },
    )
    try:
        result = build_map(root)
        assert result["root_readme_error"] is not None
        assert result["resolved"] == {"p": "o/r"}
        assert "root README.md unreadable" in _skip_reason(result, "q")

        lines = format_report(result)
        assert any(line.startswith("SOURCE3_ROOT_README_UNREADABLE=") for line in lines)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bom_prefixed_readme_still_parses():
    root = tempfile.mkdtemp(prefix="s3map_")
    try:
        os.makedirs(os.path.join(root, "sessions", "p"))
        with open(os.path.join(root, "README.md"), "wb") as handle:
            handle.write(b"\xef\xbb\xbf" + _root_section("P", "p", "o/r").encode("utf-8"))
        assert build_map(root)["resolved"] == {"p": "o/r"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# query_order dedup
# ---------------------------------------------------------------------------

def test_query_order_dedupes_projects_sharing_one_slug():
    # Live: meta and dev-env both map to brownm09/dev-env, as do job-search and
    # career-playbook. Deduping here is what keeps the caller to one gh query per repo.
    root_readme = _root_section("Dev Env", "dev-env", "brownm09/dev-env") + _root_section(
        "Meta", "meta", "brownm09/dev-env"
    )
    root = _make_journal(
        root_readme=root_readme, projects={"dev-env": "# d\n", "meta": "# m\n"}
    )
    try:
        result = build_map(root)
        assert len(result["resolved"]) == 2
        assert result["query_order"] == [
            {"slug": "brownm09/dev-env", "project": "dev-env"}
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_query_order_is_deterministic_in_project_name_order():
    root_readme = "".join(
        _root_section(name, name, f"brownm09/{name}") for name in ("zeta", "alpha", "mid")
    )
    root = _make_journal(
        root_readme=root_readme,
        projects={name: f"# {name}\n" for name in ("zeta", "alpha", "mid")},
    )
    try:
        order = [entry["project"] for entry in build_map(root)["query_order"]]
        assert order == ["alpha", "mid", "zeta"], "must follow sorted directory order"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_list_projects_ignores_files_and_sorts():
    root = _make_journal(projects={"b": "# b\n", "a": "# a\n"})
    try:
        sessions = os.path.join(root, "sessions")
        with open(os.path.join(sessions, "stray.md"), "w", encoding="utf-8") as handle:
            handle.write("not a project directory\n")
        assert list_projects(sessions) == ["a", "b"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Report + exit contract
# ---------------------------------------------------------------------------

def test_mapping_empty_signals_the_1045_signature_and_exits_1():
    root = _make_journal(
        root_readme="# Engineering Journal\n\n## Projects\n",
        projects={"p": "# p\n", "q": "# q\n"},
    )
    try:
        result = build_map(root)
        assert mapping_empty(result) is True
        assert result["project_count"] == 2
        lines = format_report(result)
        assert any(line.startswith("SOURCE3_MAPPING_EMPTY") for line in lines)
        assert "SOURCE3_RESOLVED=0" in lines
        assert "SOURCE3_SKIPPED=2" in lines

        code, out = _run_main([root])
        assert code == 1
        assert "SOURCE3_MAPPING_EMPTY" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_sessions_tree_is_not_a_mapping_failure():
    # Nothing to resolve is not the same as failing to resolve everything.
    root = _make_journal(root_readme="# Engineering Journal\n")
    try:
        result = build_map(root)
        assert mapping_empty(result) is False
        assert result["project_count"] == 0
        code, out = _run_main([root])
        assert code == 0
        assert "SOURCE3_MAPPING_EMPTY" not in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_skips_alone_are_information_not_failure():
    root = _make_journal(
        root_readme=_root_section("P", "p", "o/r"),
        projects={"p": "# p\n", "q": "# q\n"},
    )
    try:
        code, out = _run_main([root])
        assert code == 0, "a partial resolution must not fail the compose"
        assert "SOURCE3_RESOLVED=1" in out
        assert "SOURCE3_SKIPPED=1" in out
        assert "SOURCE3_SKIP q --" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_every_skipped_project_is_named_in_the_report():
    # The defect being fixed: the old code's bare ``continue`` made a broken mapping
    # indistinguishable from "no labeled issues". Every skip must surface by name.
    root = _make_journal(
        root_readme=_root_section("P", "p", "o/r"),
        projects={"p": "# p\n", "q": "# q\n", "r": None},
    )
    try:
        out = "\n".join(format_report(build_map(root)))
        for project in ("q", "r"):
            assert f"SOURCE3_SKIP {project} --" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# main(): JSON output + usage errors
# ---------------------------------------------------------------------------

def test_main_writes_the_json_contract():
    root = _make_journal(
        root_readme=_root_section("P", "p", "o/r"), projects={"p": "# p\n", "q": "# q\n"}
    )
    out_path = os.path.join(root, "map.json")
    try:
        code, _ = _run_main([root, "--json", out_path])
        assert code == 0
        with open(out_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        assert set(payload) == {"resolved", "query_order", "skipped"}
        assert payload["resolved"] == {"p": "o/r"}
        assert payload["query_order"] == [{"slug": "o/r", "project": "p"}]
        assert [e["project"] for e in payload["skipped"]] == ["q"]
        # root_readme_error is internal to the report; it is not part of the caller contract.
        assert "root_readme_error" not in payload
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_main_rejects_a_root_without_a_sessions_directory():
    root = tempfile.mkdtemp(prefix="s3map_")
    try:
        assert _run_main([root])[0] == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_main_usage_errors_exit_2():
    root = _make_journal(root_readme="# Engineering Journal\n")
    try:
        assert _run_main([])[0] == 2, "missing root"
        assert _run_main([root, "--json"])[0] == 2, "--json without a path"
        assert _run_main([root, "extra"])[0] == 2, "unexpected positional"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_main_reports_an_unwritable_json_path_rather_than_raising():
    root = _make_journal(root_readme=_root_section("P", "p", "o/r"), projects={"p": "# p\n"})
    try:
        # A directory that does not exist -- the open() must be reported, not propagated.
        bad = os.path.join(root, "no-such-dir", "map.json")
        assert _run_main([root, "--json", bad])[0] == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
    total = passed + failed
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
