#!/usr/bin/env python3
"""Tests for journal-compose-meta.py -- /journal-compose Step 6.7 (dev-env #52, #892).

Exercises the helper against fixture compose-worktree trees in ``tempfile`` directories. No network,
no ``gh``, no git: the helper is pure filesystem work, so the whole pass -- records in, derived
stubs and manifest shards out, composed journal installed, Step 9 simulated, tree proven clean -- is
replayable offline. ``main()`` *is* covered (as in test_journal_project_repo_map.py) because the
exit contract and the ``KEY=value`` report are what the skill's coordinator reads, and the
observability half is what turns a recurrence into a loud failure.

Cases pinned:

- **The #52 acceptance, as a fixture day.** ``test_issue_52_*`` builds a two-project day with no
  ``sessions/meta/``, feeds trigger records (two valid, one fabricated), and walks the pass to the end:
  derived stubs and schema-valid manifest shards appear, the fabricated record is rejected *by name*,
  a composed journal installs, Step 9 is simulated, ``check-clean`` passes, and the worktree holds
  ``sessions/meta/DATE-<slug>.md`` with no prompt anywhere.
- **The #892 regression.** ``test_issue_892_*``: the old Step 2b's output -- a stray
  ``sessions/meta/DATE_draft.md`` -- fails ``check-clean``, so the orphaning shape cannot ship silently.
- **Every rejection class is named, never silent.** A record is a claim; the report must say which
  claim failed and why (``test_each_rejection_class_is_named``).
- **Replace semantics and idempotence.** Re-running ``stub`` yields byte-identical files; a re-run
  supersedes the earlier derived set; a *failed* re-run leaves the earlier set alone.
- **A derived stub never shadows a real one**, and ``abandon`` never deletes one.
- **``install`` is a gate, not a copy**: a missing heading or an uncited ``Source:`` refuses with exit
  2 and copies nothing; derived stubs with no extractable ``Source:`` are a precondition error, never
  a vacuous pass; no derived stubs prints ``SOURCES_CITED=n/a`` explicitly.
- **Drift gates (ADR-144 "extraction must be non-empty").** The eleven heading regexes and the seven
  trigger slugs in the skill equal the helper's, each asserted non-empty before they are compared;
  the skill no longer prompts or writes ``_draft.md``; Step 6.7 and both Step 10.5 pathspec lists
  name what they must.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout

# ---------------------------------------------------------------------------
# Load the module under test without executing main()
# ---------------------------------------------------------------------------
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _SCRIPTS)

_spec = importlib.util.spec_from_file_location(
    "journal_compose_meta", os.path.join(_SCRIPTS, "journal-compose-meta.py")
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

import _journal_schema  # noqa: E402

_REPO = os.path.abspath(os.path.join(_SCRIPTS, "..", ".."))
_SKILL = os.path.join(_REPO, "claude", "skills", "journal-compose", "SKILL.md")
_ROUTINE = os.path.join(_REPO, "claude", "routines", "daily-journal-compose", "SKILL.md")

DATE = "2026-10-01"
DEV_NAME = f"{DATE}_101530.stub.md"
CP_NAME = f"{DATE}_090000.stub.md"

DEV_STUB = """## Session: 2026-10-01 10:15 — Harden the compose skill

Edited `claude/CLAUDE.md` to record that unattended composes never prompt.
The merged PR brownm09/dev-env#900 added the helper script.
Ordinary prose with no trigger in it.

### Details

More text under a sub-heading.

<!-- tokens: input=1 output=1 cost≈$0 -->
<!-- next-session-context -->
Next steps.
"""

CP_STUB = """## Session: 2026-10-01 09:00 — Interview prep

On Windows, `bash.exe` from WSL shadows Git Bash in Python subprocess calls; the fix is to
call the Git for Windows `bash.exe` by absolute path instead.

<!-- tokens: input=2 output=2 cost≈$0 -->
"""

DEV_EVIDENCE = "Edited `claude/CLAUDE.md` to record that unattended composes never prompt."
# Evidence is copied character for character, markdown included: the stub writes `bash.exe` in backticks.
CP_EVIDENCE = "`bash.exe` from WSL shadows Git Bash"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write(root, relpath, text):
    path = os.path.join(root, *relpath.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def _read(root, relpath):
    with open(os.path.join(root, *relpath.split("/")), "rb") as handle:
        return handle.read().decode("utf-8")


def _exists(root, relpath):
    return os.path.exists(os.path.join(root, *relpath.split("/")))


def _manifest(project, name):
    return json.dumps(
        {
            "stub": f"sessions/{project}/{name}",
            "topic": "fixture",
            "tokens": {"input": 0, "output": 0, "cost": 0},
            "prs_opened": [],
            "prs_closed": [],
        }
    ) + "\n"


@contextmanager
def worktree():
    """A two-project compose worktree with no ``sessions/meta/`` -- the 2026-10-01 shape."""
    root = tempfile.mkdtemp(prefix="jcm-test-")
    try:
        _write(root, f"sessions/dev-env/{DEV_NAME}", DEV_STUB)
        _write(root, f"sessions/dev-env/{DEV_NAME[:-8]}.manifest.jsonl", _manifest("dev-env", DEV_NAME))
        _write(root, f"sessions/career-playbook/{CP_NAME}", CP_STUB)
        _write(
            root,
            f"sessions/career-playbook/{CP_NAME[:-8]}.manifest.jsonl",
            _manifest("career-playbook", CP_NAME),
        )
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def record(**overrides):
    base = {
        "project": "dev-env",
        "type": "claude-md",
        "stub": DEV_NAME,
        "reason": "CLAUDE.md now says unattended composes never prompt.",
        "evidence": DEV_EVIDENCE,
    }
    base.update(overrides)
    return base


def cp_record(**overrides):
    return record(
        **{
            "project": "career-playbook",
            "type": "platform-constraint",
            "stub": CP_NAME,
            "reason": "WSL bash.exe shadows Git Bash.",
            "evidence": CP_EVIDENCE,
            **overrides,
        }
    )


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mod.main(["journal-compose-meta.py", *args])
    return rc, out.getvalue(), err.getvalue()


def stub(root, records, date=DATE):
    path = os.path.join(root, "records.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(records, handle)
    return run("stub", root, date, path)


def kv(out):
    """First value for each ``KEY=value`` line, plus every ``META_STUB`` value as a list."""
    values, stubs = {}, []
    for line in out.splitlines():
        if "=" in line and re.match(r"^[A-Z_]+=", line):
            key, _sep, value = line.partition("=")
            values.setdefault(key, value)
            if key == "META_STUB":
                stubs.append(value)
    values["_stubs"] = stubs
    return values


def composed_journal(sources, extra=""):
    cited = "\n".join(f"- `{source}`" for source in sources)
    return f"""# Session Transcript — {DATE}

**Topic:** Meta-relevant changes detected in other projects.

## Contents

- [Opening Brief](#opening-brief)

## Opening Brief

> First session for this project — no prior Next Session Context.

## Key Decisions

### Session 1

- A decision.

## Session 1 — Meta-relevant changes

Sources:
{cited}
{extra}
## Open Items / Next Steps

- [ ] An item.

## Token Usage

Not applicable.

## Token Optimization Suggestions

- A suggestion.

## Next Session Context

Next.

## Reflection

- A reflection.

## Further Reading

*No primary sources.*
"""


def stage(root, text):
    path = os.path.join(root, "staged-journal.md")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def tree(root):
    found = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            found.append(os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/"))
    return sorted(found)


def derived_sources(root):
    """The ``Source:`` paths of every derived stub currently in the worktree."""
    sources = []
    meta = os.path.join(root, "sessions", "meta")
    for name in sorted(os.listdir(meta)):
        if name.endswith(".stub.md"):
            sources += mod._SOURCE_RE.findall(_read(root, f"sessions/meta/{name}"))
    return sources


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_normalize_collapses_whitespace_and_folds_compatibility_forms():
    assert mod.normalize("  a \t b\n c  ") == "a b c"
    assert mod.normalize("The ﬁx") == "The fix"  # U+FB01 ligature -> "fi" under NFKC


def test_valid_date_rejects_the_unsubstituted_placeholder():
    assert mod.valid_date("2026-10-01")
    for bad in ("YYYY-MM-DD", "2026-13-01", "2026-02-30", "26-10-01", "", None):
        assert not mod.valid_date(bad), bad


def test_find_evidence_prefers_single_lines_then_joins_wrapped_lines():
    lines = ["alpha beta", "gamma delta", "epsilon"]
    assert mod.find_evidence(lines, "gamma delta") == 1
    assert mod.find_evidence(lines, "beta gamma") == 0  # wrapped across lines 0 and 1
    assert mod.find_evidence(lines, "alpha epsilon") is None
    assert mod.find_evidence(lines, "") is None


def test_session_heading_is_the_nearest_h2_not_an_h3():
    lines = ["## Session A", "text", "### Detail", "evidence here"]
    assert mod.session_heading(lines, 3) == "Session A"
    assert mod.session_heading(["no heading", "evidence"], 1) is None


def test_excerpt_never_crosses_a_heading_in_either_direction():
    lines = ["## S", "", "line a", "line b", "line c EVIDENCE-PHRASE here", "line d", "### Next", "line e"]
    assert mod.excerpt(lines, 4) == ["line a", "line b", "line c EVIDENCE-PHRASE here", "line d"]
    # Evidence directly under a heading: the heading itself is not part of the excerpt.
    assert mod.excerpt(["## H", "EVIDENCE X", "more"], 1) == ["EVIDENCE X", "more"]


def test_allocate_names_are_stable_and_skip_real_stubs():
    assert mod.allocate_names(DATE, {0, 1}, set()) == {0: f"{DATE}_235900.stub.md", 1: f"{DATE}_235901.stub.md"}
    taken = {f"{DATE}_235900.stub.md"}
    assert mod.allocate_names(DATE, {0, 1}, taken) == {0: f"{DATE}_235901.stub.md", 1: f"{DATE}_235902.stub.md"}


def test_allocate_names_raises_when_the_minute_is_exhausted():
    taken = {f"{DATE}_2359{n:02d}.stub.md" for n in range(60)}
    try:
        mod.allocate_names(DATE, {0}, taken)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_missing_headings_reports_labels_in_schema_order():
    assert mod.missing_headings(composed_journal([])) == []
    text = composed_journal([]).replace("## Reflection", "## Thoughts").replace("## Key Decisions", "## KD")
    assert mod.missing_headings(text) == ["Key Decisions", "Reflection"]


# ---------------------------------------------------------------------------
# stub: derived stubs and manifest shards
# ---------------------------------------------------------------------------

def test_stub_writes_one_derived_stub_per_category():
    with worktree() as root:
        rc, out, _err = stub(root, [record(), cp_record()])
        values = kv(out)
        assert rc == 0, out
        assert values["META_STATUS"] == "derived"
        assert values["META_TRIGGERS_ACCEPTED"] == "2"
        assert values["META_TRIGGERS_REJECTED"] == "0"
        assert values["_stubs"] == [f"sessions/meta/{DATE}_235900.stub.md", f"sessions/meta/{DATE}_235901.stub.md"]
        for name in (f"{DATE}_235900", f"{DATE}_235901"):
            assert _exists(root, f"sessions/meta/{name}.stub.md")
            assert _exists(root, f"sessions/meta/{name}.manifest.jsonl")


def test_derived_stub_quotes_the_source_verbatim_and_carries_no_session_markers():
    with worktree() as root:
        stub(root, [record()])
        text = _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert text.startswith(mod.DERIVED_MARKER)
        assert f"- Source: `sessions/dev-env/{DEV_NAME}`" in text
        assert "- Source session: Session: 2026-10-01 10:15 — Harden the compose skill" in text
        assert "- Trigger: CLAUDE.md modified" in text
        assert f"> {DEV_EVIDENCE}" in text
        # The excerpt is the evidence line +/- 2 lines, quoted so a '##' inside it cannot become a heading.
        assert "> The merged PR brownm09/dev-env#900 added the helper script." in text
        assert "Next steps." not in text and "More text under a sub-heading." not in text
        # A derived stub must not displace a real stub's opening brief or next-session-context.
        assert "<!-- opening-brief" not in text and "<!-- next-session-context" not in text
        assert text.rstrip().endswith("<!-- tokens: input=0 output=0 cost≈$0 -->")
        assert "\r" not in text and not text.startswith("﻿")


def test_derived_manifest_passes_the_journal_schema():
    with worktree() as root:
        stub(root, [record(), cp_record()])
        for name in (f"{DATE}_235900", f"{DATE}_235901"):
            raw = _read(root, f"sessions/meta/{name}.manifest.jsonl")
            assert raw.endswith("\n") and raw.count("\n") == 1 and not raw.startswith("﻿")
            entry = json.loads(raw)
            assert _journal_schema.missing_required_fields(entry) == []
            assert _journal_schema.malformed_manifest_fields(entry) == []
            assert entry["stub"] == f"sessions/meta/{name}.stub.md"
            assert entry["topic"].startswith("Derived meta (")
            assert entry["prs_opened"] == [] and entry["prs_closed"] == []


def test_fabricated_evidence_is_rejected_by_name_and_never_written():
    with worktree() as root:
        fake = record(type="dev-env-pr", evidence="merged PR #4242 rewrote the universe")
        rc, out, _err = stub(root, [record(), fake])
        assert rc == 0
        values = kv(out)
        assert values["META_TRIGGERS_ACCEPTED"] == "1" and values["META_TRIGGERS_REJECTED"] == "1"
        rejected = [line for line in out.splitlines() if line.startswith("META_TRIGGER_REJECTED")]
        assert len(rejected) == 1
        assert "dev-env" in rejected[0] and DEV_NAME in rejected[0] and "dev-env-pr" in rejected[0]
        assert "evidence not found" in rejected[0]
        assert "universe" not in _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert not _exists(root, f"sessions/meta/{DATE}_235904.stub.md")  # no dev-env-pr stub at all


def test_each_rejection_class_is_named():
    cases = [
        (record(project="meta"), "project 'meta' is skipped"),
        (record(project="../escape"), "unsafe project name"),
        (record(project="no-such-project"), "no sessions/no-such-project/ directory"),
        (record(type="bogus"), "unknown type"),
        (record(stub="2026-09-30_101530.stub.md"), "stub name must be"),
        (record(stub=f"{DATE}_999999.stub.md"), "stub not found"),
        (record(stub=f"sessions/other/{DEV_NAME}"), "stub path must be"),
        (record(stub="../" + DEV_NAME), "stub path must be"),
        (record(reason=""), "missing or empty field: reason"),
        (record(evidence="   "), "missing or empty field: evidence"),
        ({"project": "dev-env"}, "missing or empty field"),
        ("not an object", "record is not a JSON object"),
    ]
    for bad, expected in cases:
        with worktree() as root:
            rc, out, _err = stub(root, [bad])
            assert rc == 2, (bad, out)
            assert kv(out)["META_STATUS"] == "none"
            lines = [line for line in out.splitlines() if line.startswith("META_TRIGGER_REJECTED")]
            assert len(lines) == 1 and expected in lines[0], (bad, lines)
            assert not _exists(root, "sessions/meta"), bad


def test_all_records_rejected_exits_2_but_an_empty_list_is_fine():
    with worktree() as root:
        rc, out, _err = stub(root, [record(evidence="nowhere in the stub")])
        assert rc == 2 and kv(out)["META_STATUS"] == "none"
        rc, out, _err = stub(root, [])
        assert rc == 0 and kv(out)["META_STATUS"] == "none" and kv(out)["META_RECORDS_RECEIVED"] == "0"


def test_stub_is_idempotent_byte_for_byte():
    with worktree() as root:
        stub(root, [record(), cp_record()])
        first = {path: _read(root, path) for path in tree(root) if path.startswith("sessions/meta/")}
        stub(root, [record(), cp_record()])
        second = {path: _read(root, path) for path in tree(root) if path.startswith("sessions/meta/")}
        assert first == second and len(first) == 4


def test_a_rerun_replaces_the_earlier_derived_set():
    with worktree() as root:
        stub(root, [record(), cp_record()])
        assert _exists(root, f"sessions/meta/{DATE}_235901.stub.md")
        rc, _out, _err = stub(root, [record()])
        assert rc == 0
        assert _exists(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert not _exists(root, f"sessions/meta/{DATE}_235901.stub.md")
        assert not _exists(root, f"sessions/meta/{DATE}_235901.manifest.jsonl")


def test_a_failed_rerun_keeps_the_earlier_derived_set():
    with worktree() as root:
        stub(root, [record()])
        before = _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        rc, _out, _err = stub(root, [record(evidence="a phrase that is not in the stub")])
        assert rc == 2
        assert _read(root, f"sessions/meta/{DATE}_235900.stub.md") == before


def test_a_real_meta_stub_is_never_shadowed_or_touched():
    with worktree() as root:
        real_text = "## Session: 2026-10-01 23:59 — A real late session\n\nBody.\n"
        _write(root, f"sessions/meta/{DATE}_235900.stub.md", real_text)
        _write(root, f"sessions/meta/{DATE}_235900.manifest.jsonl", _manifest("meta", f"{DATE}_235900.stub.md"))
        rc, out, _err = stub(root, [record(), cp_record()])
        assert rc == 0
        assert kv(out)["_stubs"] == [f"sessions/meta/{DATE}_235901.stub.md", f"sessions/meta/{DATE}_235902.stub.md"]
        assert _read(root, f"sessions/meta/{DATE}_235900.stub.md") == real_text
        # Re-running must overwrite its own derived files, not walk further away from the real one.
        stub(root, [record(), cp_record()])
        assert kv(stub(root, [record(), cp_record()])[1])["_stubs"] == [
            f"sessions/meta/{DATE}_235901.stub.md",
            f"sessions/meta/{DATE}_235902.stub.md",
        ]


def test_duplicate_records_are_deduped_and_counted():
    with worktree() as root:
        rc, out, _err = stub(root, [record(), record(reason="same claim, reworded"), cp_record()])
        values = kv(out)
        assert rc == 0
        assert values["META_TRIGGERS_ACCEPTED"] == "2" and values["META_TRIGGERS_DEDUPED"] == "1"
        assert _read(root, f"sessions/meta/{DATE}_235900.stub.md").count("- Source: `") == 1


def test_two_records_in_one_category_share_one_stub():
    with worktree() as root:
        second = record(evidence="The merged PR brownm09/dev-env#900 added the helper script.", reason="PR 900 merged.")
        rc, out, _err = stub(root, [record(), second])
        assert rc == 0 and kv(out)["_stubs"] == [f"sessions/meta/{DATE}_235900.stub.md"]
        text = _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert text.count("\n### ") == 2 and text.count("## CLAUDE.md modified") == 1


def test_type_aliases_normalize_underscores_and_case():
    with worktree() as root:
        rc, out, _err = stub(root, [record(type="CLAUDE_MD"), cp_record(type=" Platform_Constraint ")])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "2"


def test_evidence_matches_after_nfkc_whitespace_and_across_a_wrapped_line():
    with worktree() as root:
        _write(root, f"sessions/dev-env/{DATE}_111111.stub.md", "## S\n\nThe ﬁx   landed\nin   two lines.\n")
        ligature = record(stub=f"{DATE}_111111.stub.md", evidence="The fix landed")
        wrapped = record(stub=f"{DATE}_111111.stub.md", evidence="landed in two lines.", type="convention")
        rc, out, _err = stub(root, [ligature, wrapped])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "2", out
    with worktree() as root:
        rc, out, _err = stub(
            root, [cp_record(evidence="subprocess calls; the fix is to call the Git for Windows")]
        )
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out


def test_a_sessions_prefixed_stub_path_is_tolerated_for_its_own_project_only():
    with worktree() as root:
        rc, out, _err = stub(root, [record(stub=f"sessions/dev-env/{DEV_NAME}")])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1"


def test_records_file_forms_and_failures():
    with worktree() as root:
        path = os.path.join(root, "records.json")
        # Object form with a matching date, and a UTF-8 BOM, are accepted.
        with open(path, "wb") as handle:
            handle.write(b"\xef\xbb\xbf" + json.dumps({"date": DATE, "records": [record()]}).encode("utf-8"))
        assert run("stub", root, DATE, path)[0] == 0
        # A file written for another date is refused outright (stale scratch file).
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"date": "2026-09-30", "records": [record()]}, handle)
        rc, _out, err = run("stub", root, DATE, path)
        assert rc == 1 and "not 2026-10-01" in err
        for bad in ("{not json", '{"records": "nope"}', "42"):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(bad)
            assert run("stub", root, DATE, path)[0] == 1, bad
        assert run("stub", root, DATE, os.path.join(root, "absent.json"))[0] == 1


# ---------------------------------------------------------------------------
# install: a gate, not a copy
# ---------------------------------------------------------------------------

def test_install_accepts_a_complete_journal_and_reports():
    with worktree() as root:
        stub(root, [record(), cp_record()])
        sources = derived_sources(root)
        assert len(sources) == 2
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal(sources)), "meta-triggers")
        values = kv(out)
        assert rc == 0, out
        assert values["META_JOURNAL"] == f"sessions/meta/{DATE}-meta-triggers.md"
        assert values["STRUCTURE"] == "ok" and values["SOURCES_CITED"] == "2/2"
        assert values["FIDELITY"] == f"{values['LINE_COUNT']}/{values['SOURCE_LINES']}"
        installed = _read(root, f"sessions/meta/{DATE}-meta-triggers.md")
        assert "\r" not in installed and not installed.startswith("﻿")
        assert not [name for name in tree(root) if ".tmp-" in name]


def test_install_refuses_a_missing_heading_and_copies_nothing():
    with worktree() as root:
        stub(root, [record()])
        text = composed_journal(derived_sources(root)).replace("## Next Session Context", "## Next")
        rc, out, _err = run("install", root, DATE, stage(root, text), "meta-triggers")
        assert rc == 2 and "INSTALL_REFUSED" in out
        assert "STRUCTURE=missing:Next Session Context" in out
        assert not _exists(root, f"sessions/meta/{DATE}-meta-triggers.md")


def test_install_refuses_an_uncited_source():
    with worktree() as root:
        stub(root, [record(), cp_record()])
        sources = derived_sources(root)
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal(sources[:1])), "meta-triggers")
        assert rc == 2
        assert f"SOURCES_UNCITED={sources[1]}" in out and "STRUCTURE=missing" not in out
        assert not _exists(root, f"sessions/meta/{DATE}-meta-triggers.md")


def test_install_without_derived_stubs_reports_n_a_explicitly():
    with worktree() as root:
        _write(root, f"sessions/meta/{DATE}_060000.stub.md", "## Session: 06:00 — Routine\n\nBody.\n")
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal([])), "routine")
        assert rc == 0 and kv(out)["SOURCES_CITED"] == "n/a", out


def test_install_with_no_meta_stub_at_all_is_a_precondition_error():
    with worktree() as root:
        rc, _out, err = run("install", root, DATE, stage(root, composed_journal([])), "nothing")
        assert rc == 1 and "nothing for this journal to compose" in err
        assert not _exists(root, "sessions/meta")


def test_install_a_derived_stub_with_no_source_line_is_exit_1_never_a_vacuous_pass():
    with worktree() as root:
        _write(root, f"sessions/meta/{DATE}_235900.stub.md", f"{mod.DERIVED_MARKER} fixture -->\n\n## H\n\nNo source line.\n")
        rc, _out, err = run("install", root, DATE, stage(root, composed_journal([])), "meta-triggers")
        assert rc == 1 and "no extractable 'Source:' line" in err
        assert not _exists(root, f"sessions/meta/{DATE}-meta-triggers.md")


def test_install_allows_one_journal_per_date_and_overwrites_the_same_slug():
    with worktree() as root:
        stub(root, [record()])
        staged = stage(root, composed_journal(derived_sources(root)))
        assert run("install", root, DATE, staged, "first")[0] == 0
        assert run("install", root, DATE, staged, "first")[0] == 0  # same slug: a retry, not a second journal
        rc, _out, err = run("install", root, DATE, staged, "second")
        assert rc == 1 and "already exists" in err
        assert [n for n in tree(root) if re.match(rf"sessions/meta/{DATE}-.*\.md$", n)] == [
            f"sessions/meta/{DATE}-first.md"
        ]


def test_install_rejects_bad_slugs_and_unreadable_or_empty_input():
    with worktree() as root:
        stub(root, [record()])
        staged = stage(root, composed_journal(derived_sources(root)))
        for bad in ("Bad Slug", "../x", "", "-lead", "x" * 81):
            assert run("install", root, DATE, staged, bad)[0] == 1, bad
        assert run("install", root, DATE, os.path.join(root, "absent.md"), "ok")[0] == 1
        assert run("install", root, DATE, stage(root, "  \n"), "ok")[0] == 1


def test_install_accepts_a_crlf_composed_journal_and_writes_lf():
    with worktree() as root:
        stub(root, [record()])
        text = composed_journal(derived_sources(root)).replace("\n", "\r\n")
        path = os.path.join(root, "staged-crlf.md")
        with open(path, "wb") as handle:
            handle.write(text.encode("utf-8"))
        rc, out, _err = run("install", root, DATE, path, "meta-triggers")
        assert rc == 0, out
        with open(os.path.join(root, "sessions", "meta", f"{DATE}-meta-triggers.md"), "rb") as handle:
            assert b"\r" not in handle.read()


# ---------------------------------------------------------------------------
# abandon
# ---------------------------------------------------------------------------

def test_abandon_removes_only_derived_files():
    with worktree() as root:
        _write(root, f"sessions/meta/{DATE}_060000.stub.md", "## Session: real\n\nBody.\n")
        _write(root, f"sessions/meta/{DATE}_060000.manifest.jsonl", _manifest("meta", f"{DATE}_060000.stub.md"))
        stub(root, [record(), cp_record()])
        rc, out, _err = run("abandon", root, DATE)
        assert rc == 0 and out.startswith("META_ABANDONED=") and "none" not in out
        assert len(out.split("=", 1)[1].split(",")) == 4
        assert tree(root) and _exists(root, f"sessions/meta/{DATE}_060000.stub.md")
        assert _exists(root, f"sessions/meta/{DATE}_060000.manifest.jsonl")
        assert not [n for n in tree(root) if "_2359" in n]
        assert run("abandon", root, DATE)[1].strip() == "META_ABANDONED=none"


def test_abandon_removes_an_orphaned_derived_manifest():
    with worktree() as root:
        stub(root, [record()])
        os.remove(os.path.join(root, "sessions", "meta", f"{DATE}_235900.stub.md"))  # a crash between the two writes
        assert _exists(root, f"sessions/meta/{DATE}_235900.manifest.jsonl")
        assert run("abandon", root, DATE)[0] == 0
        assert not _exists(root, f"sessions/meta/{DATE}_235900.manifest.jsonl")


# ---------------------------------------------------------------------------
# check-clean
# ---------------------------------------------------------------------------

def _consume_the_day(root):
    """Simulate Step 9 for every project: delete the date's stubs and manifest shards."""
    for base, _dirs, files in os.walk(os.path.join(root, "sessions")):
        for name in files:
            if name.startswith(f"{DATE}_") and name.endswith((".stub.md", ".manifest.jsonl")):
                os.remove(os.path.join(base, name))


def test_check_clean_fails_on_each_leftover_shape_and_names_it():
    shapes = {
        f"sessions/dev-env/{DEV_NAME}": "an unconsumed stub",
        f"sessions/dev-env/{DATE}_101530.manifest.jsonl": "an unconsumed manifest shard",
        f"sessions/dev-env/{DATE}.manifest.jsonl": "a legacy per-day manifest",
        f"sessions/meta/{DATE}_draft.md": "the old Step 2b output",
        f"sessions/meta/{DATE}_235900.stub.md": "a derived stub",
    }
    for path, why in shapes.items():
        with worktree() as root:
            _consume_the_day(root)
            assert run("check-clean", root, DATE)[0] == 0
            _write(root, path, "x\n")
            rc, out, _err = run("check-clean", root, DATE)
            assert rc == 2, why
            assert "CHECK_CLEAN=leftover" in out and f"LEFTOVER {path}" in out, why


def test_check_clean_ignores_other_dates_composed_journals_and_non_directories():
    with worktree() as root:
        _consume_the_day(root)
        # The date-mismatched shape PR #182 carried through: stubs for the NEXT day sitting on the branch.
        _write(root, "sessions/dev-env/2026-10-02_090000.stub.md", "x\n")
        _write(root, "sessions/dev-env/2026-10-02_090000.manifest.jsonl", "{}\n")
        _write(root, f"sessions/dev-env/{DATE}-scheduled-routines.md", "# composed\n")  # a hyphen, not a stub
        _write(root, "sessions/notes.txt", "stray file at the sessions root\n")
        assert run("check-clean", root, DATE) == (0, "CHECK_CLEAN=ok\n", "")


def test_check_clean_on_a_tree_without_sessions_is_a_precondition_error_not_a_pass():
    root = tempfile.mkdtemp(prefix="jcm-test-")
    try:
        rc, _out, err = run("check-clean", root, DATE)
        assert rc == 1 and "no sessions/ directory" in err
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# End to end: the acceptance fixture day, and the regression it exists for
# ---------------------------------------------------------------------------

def test_issue_52_acceptance_a_fixture_day_ends_with_a_composed_meta_journal_and_no_prompt():
    """Two projects, no meta stubs, triggers reported -> a meta journal in the worktree, nothing left over."""
    with worktree() as root:
        assert not _exists(root, "sessions/meta")
        fabricated = record(type="dev-env-pr", evidence="merged PR #4242 rewrote the universe")
        rc, out, _err = stub(root, [record(), cp_record(), fabricated])
        values = kv(out)
        assert rc == 0 and values["META_STATUS"] == "derived"
        assert values["META_TRIGGERS_ACCEPTED"] == "2" and values["META_TRIGGERS_REJECTED"] == "1"
        assert len(values["_stubs"]) == 2

        # Step 6.7 step 3: the coordinator composes from the derived stubs (here: a compliant fixture).
        sources = derived_sources(root)
        assert sorted(sources) == sorted(
            [f"sessions/dev-env/{DEV_NAME}", f"sessions/career-playbook/{CP_NAME}"]
        )
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal(sources)), "meta-triggers")
        assert rc == 0 and kv(out)["META_JOURNAL"] == f"sessions/meta/{DATE}-meta-triggers.md"

        # Not clean yet: Step 9 has not run, so every consumed input is still there.
        assert run("check-clean", root, DATE)[0] == 2
        _consume_the_day(root)
        assert run("check-clean", root, DATE) == (0, "CHECK_CLEAN=ok\n", "")

        assert _exists(root, f"sessions/meta/{DATE}-meta-triggers.md")
        assert not [path for path in tree(root) if path.endswith("_draft.md")]
        assert not [path for path in tree(root) if ".stub.md" in path or ".manifest.jsonl" in path]


def test_issue_892_regression_the_old_draft_file_fails_check_clean():
    """Old Step 2b wrote ``sessions/meta/DATE_draft.md``; nothing composed it. It must not ship silently."""
    with worktree() as root:
        _consume_the_day(root)
        _write(root, f"sessions/meta/{DATE}_draft.md", f"<!-- draft: {DATE} -->\nOpening brief: Meta entries.\n")
        rc, out, _err = run("check-clean", root, DATE)
        assert rc == 2 and f"LEFTOVER sessions/meta/{DATE}_draft.md" in out


def test_issue_892_regression_meta_composes_the_same_whether_or_not_real_meta_stubs_exist():
    """The #892 shape (meta already among the composed projects) and the 2026-10-01 shape converge."""
    with worktree() as root:
        _write(root, f"sessions/meta/{DATE}_060000.stub.md", "## Session: 06:00 — Routine\n\nBody.\n")
        _write(root, f"sessions/meta/{DATE}_060000.manifest.jsonl", _manifest("meta", f"{DATE}_060000.stub.md"))
        assert stub(root, [record()])[0] == 0
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal(derived_sources(root))), "meta-day")
        assert rc == 0 and kv(out)["SOURCES_CITED"] == "1/1"
        _consume_the_day(root)
        assert run("check-clean", root, DATE)[0] == 0
        assert [n for n in tree(root) if re.match(rf"sessions/meta/{DATE}-.*\.md$", n)] == [
            f"sessions/meta/{DATE}-meta-day.md"
        ]


# ---------------------------------------------------------------------------
# main(): the exit contract
# ---------------------------------------------------------------------------

def test_main_usage_and_precondition_errors_exit_1():
    with worktree() as root:
        assert run()[0] == 1
        assert run("bogus", root, DATE)[0] == 1
        assert run("stub", root, DATE)[0] == 1                                  # missing records argument
        assert run("check-clean", root, DATE, "extra")[0] == 1                  # too many arguments
        rc, _out, err = run("check-clean", root, "YYYY-MM-DD")
        assert rc == 1 and "is not a YYYY-MM-DD date" in err                    # unsubstituted placeholder
        assert run("check-clean", root, "2026-13-45")[0] == 1
        assert run("check-clean", os.path.join(root, "no-such-dir"), DATE)[0] == 1


def test_stub_never_writes_outside_sessions_meta():
    with worktree() as root:
        before = set(tree(root))
        stub(root, [record(), cp_record()])
        added = set(tree(root)) - before - {"records.json"}
        assert added and all(path.startswith("sessions/meta/") for path in added), added


# ---------------------------------------------------------------------------
# Drift gates: the skill and routine must agree with the helper
# ---------------------------------------------------------------------------

def _read_doc(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read().replace("\r\n", "\n")


def _section(text, heading_regex):
    """From the first heading matching ``heading_regex`` to the next heading of equal-or-higher rank."""
    match = re.search(heading_regex, text, re.MULTILINE)
    assert match, f"section not found: {heading_regex}"
    level = len(re.match(r"#+", match.group(0)).group(0))
    rest = text[match.end():]
    end = re.search(rf"^#{{1,{level}}} ", rest, re.MULTILINE)
    return match.group(0) + (rest[: end.start()] if end else rest)


def test_skill_chk_blocks_equal_the_helpers_heading_regexes():
    skill = _read_doc(_SKILL)
    found = re.findall(r"^\s*chk '([^']*)'\s+'([^']*)'\s*$", skill, re.MULTILINE)
    assert len(found) == 2 * len(mod.REQUIRED_HEADINGS), f"expected two copies of the chk() block, found {len(found)} lines"
    size = len(mod.REQUIRED_HEADINGS)
    assert tuple(found[:size]) == mod.REQUIRED_HEADINGS
    assert tuple(found[size:]) == mod.REQUIRED_HEADINGS


def test_skill_lists_exactly_the_helpers_trigger_slugs():
    skill = _read_doc(_SKILL)
    line = re.search(r"^\*\*Trigger slugs.*$", skill, re.MULTILINE)
    assert line, "Step 2b must carry a '**Trigger slugs ...**' line"
    slugs = re.findall(r"`([a-z-]+)`", line.group(0))
    assert slugs, "no slugs extracted from the Trigger slugs line"
    assert slugs == [slug for slug, _label in mod.TRIGGER_TYPES]


def test_phase_1_template_inlines_the_same_slugs_because_a_subagent_follows_its_own_copy():
    """ADR-082's 2026-07-23 addendum: a subagent acts on the copy in its template, not a pointer."""
    lines = _read_doc(_SKILL).split("\n")
    marker = next((i for i, line in enumerate(lines) if "exactly one of these seven" in line), None)
    assert marker is not None, "the Phase 1 template's Step 2b must inline the trigger slugs"
    slugs = []
    for line in lines[marker + 1:]:
        if not re.fullmatch(r"\s+[a-z-]+(?:,\s*[a-z-]+)*,?\s*", line):
            break
        slugs += re.findall(r"[a-z-]+", line)
    assert slugs, "no slugs extracted from the template's inline list"
    assert slugs == [slug for slug, _label in mod.TRIGGER_TYPES]


def test_skill_no_longer_prompts_or_writes_a_draft_file():
    skill = _read_doc(_SKILL)
    step_2b = _section(skill, r"^## Step 2b — .*$")
    assert len(step_2b.splitlines()) > 5, "Step 2b section extraction came back empty"
    for gone in (
        "Should I open a meta draft block",
        "open a meta draft block",
        "y (append meta block",
        "create it with `<!-- draft",
    ):
        assert gone not in skill, f"the old interactive Step 2b text is back: {gone!r}"
    assert not re.search(r"git .*\badd\b.*sessions/meta/YYYY-MM-DD_draft\.md", skill), (
        "the skill must not stage a sessions/meta/YYYY-MM-DD_draft.md (dev-env#892)"
    )
    assert "no prompt" in step_2b.lower() or "never prompt" in step_2b.lower() or "does not prompt" in step_2b.lower()


def test_skill_wires_step_6_7_to_every_subcommand_and_check_clean_to_step_10():
    skill = _read_doc(_SKILL)
    step_67 = _section(skill, r"^## Step 6\.7 — .*$")
    assert len(step_67.splitlines()) > 10, "Step 6.7 section extraction came back empty"
    assert "journal-compose-meta.py" in step_67
    for name in ("stub", "install", "abandon"):
        assert re.search(rf"journal-compose-meta\.py {name} ", step_67), f"Step 6.7 must show the {name} invocation"
    step_10 = _section(skill, r"^## Step 10 — .*$")
    assert re.search(r"journal-compose-meta\.py check-clean ", step_10), "Step 10 must run check-clean before staging"


def test_both_step_10_5_replay_pathspec_lists_name_sessions_meta():
    skill = _read_doc(_SKILL)
    # A call is its first line plus every backslash-continued line after it.
    calls = re.findall(r"journal-compose-replay\.sh \"\$WT\" \"\$PREV\"(?:[^\n]*\\\n)*[^\n]*", skill)
    assert len(calls) == 2, f"expected the single-project and multi-project replay calls, found {len(calls)}"
    for call in calls:
        assert call.count("\n") >= 1, f"the pathspec continuation line was not captured:\n{call}"
        assert "sessions/meta/" in call, f"a Step 10.5 pathspec list omits sessions/meta/:\n{call}"


def test_routine_states_the_unattended_meta_rule():
    routine = _read_doc(_ROUTINE)
    assert "Step 6.7" in routine and "Meta journal:" in routine, (
        "daily-journal-compose/SKILL.md must say meta is composed automatically and the status is reported"
    )
    assert re.search(r"never (?:ask|prompt)", routine, re.IGNORECASE)


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
        except Exception as e:  # noqa: BLE001 -- report every failure, keep running
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
