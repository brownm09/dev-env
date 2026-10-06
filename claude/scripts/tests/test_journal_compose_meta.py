#!/usr/bin/env python3
"""Tests for journal-compose-meta.py -- /journal-compose Step 6.7 (dev-env #52, #892).

Exercises the helper against fixture compose-worktree trees in ``tempfile`` directories. No network
and no ``gh``; the git fixtures pin ``core.hooksPath`` and ``commit.gpgsign`` so a developer's global
git setup (dev-env installs hooks globally) cannot reach them. The subprocesses are ``git`` itself --
the ``check-staged`` cases and ``install``'s tracked-versus-untracked rule need a real index -- and,
for the two end-to-end tests, a Git for Windows ``bash`` that runs the skill's own extracted commit
blocks against a bare origin.
``main()`` *is* covered (as in test_journal_project_repo_map.py) because the exit contract and the
``KEY=value`` report are what the skill's coordinator reads, and the observability half is what
turns a recurrence into a loud failure.

Cases pinned:

- **The #52 acceptance, as a fixture day.** ``test_issue_52_*``: a two-project day with no
  ``sessions/meta/``, trigger records (two valid, one fabricated), walked to the end -- derived
  stubs and schema-valid manifest shards appear, the fabricated record is rejected *by name*, a
  composed journal installs, Step 9 is simulated, ``check-clean`` passes, and the worktree holds
  ``sessions/meta/DATE-<slug>.md``. No prompt anywhere.
- **The #892 regression.** ``test_issue_892_*``: the old Step 2b's output -- a stray
  ``sessions/meta/DATE_draft.md`` -- fails ``check-clean``, so the orphaning shape cannot ship.
- **Review findings on PR #1126, each with a known-bad and a known-good case.** A journal that drops
  one trigger category's session must not install even when two categories share one source stub
  (``test_install_*category*``); ``check-clean`` passing while the *index* still holds the stubs and
  the composed journal is untracked must fail ``check-staged``
  (``test_check_staged_*``); a one-word "evidence" must not verify a fabricated record; a derived
  stub must not copy ``<!-- tokens -->`` markers; an empty worktree argument must not mean the cwd.
- **Every rejection class is named, with the record's reason preserved**, and a day on which every
  record was refused reports ``rejected`` -- never ``none`` (which means "no meta triggers").
- **Replace semantics, idempotence, all-or-nothing swaps.** Re-running ``stub`` yields
  byte-identical files; a write that fails while staging leaves the earlier set, one that fails
  while swapping leaves no derived file at all (the swap deletes the earlier set first, so it cannot
  restore it), neither leaves a temp file, and a locked file is reported, never raised; a derived
  stub never shadows a real stub or a real orphan manifest, and ``abandon`` never deletes one.
- **``install`` knows whose journal it is.** An untracked journal is this run's and is replaced (the
  fidelity remedy needs that); a tracked one is the draft branch's and is refused
  (``META_JOURNAL_EXISTS=``); where git cannot say it is ``META_INSTALL_UNVERIFIED=``, a different
  cause with a different key. Each category claims its own session, matched on the title's start.
- **Evidence is checked against the real stub shapes.** Too-short phrases, the opening brief (found
  in the body first, because bodies repeat it), ``### Session:`` stubs with no H2, and ``**PR:**``
  lines above a heading.
- **Drift gates (ADR-144 "extraction must be non-empty").** The eleven heading regexes, the seven
  trigger slugs (Step 2b *and* the Phase 1 template's inline copy), the trigger list in
  ``claude/CLAUDE.md``, the real staging commands in Step 10 and Phase 2, both Step 10.5 pathspec
  lists, and the routine's meta rule are tied to the helper, each asserted non-empty first.
- **End to end.** The skill's own Phase 2 and Step 10 commit blocks, extracted and run in a real
  bash: a failed ``check-staged`` publishes nothing; a clean index commits the meta journal.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
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
_GLOBAL_CLAUDE_MD = os.path.join(_REPO, "claude", "CLAUDE.md")

# Built rather than typed: an invisible literal in source is a trap for any editor that strips it.
BOM = chr(0xFEFF)

DATE = "2026-10-01"
DEV_NAME = f"{DATE}_101530.stub.md"
CP_NAME = f"{DATE}_090000.stub.md"
TAIL_NAME = f"{DATE}_120000.stub.md"

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

# Evidence sits on the line directly above the stub's trailing markers.
TAIL_STUB = """## Session: 2026-10-01 12:00 — Marker adjacency

Some context line before the claim.
The hook now refuses a second concurrent compose of the same date.
<!-- tokens: input=3 output=3 cost≈$0 -->
<!-- next-session-context -->
Carry on from here.
"""

DEV_EVIDENCE = "Edited `claude/CLAUDE.md` to record that unattended composes never prompt."
PR_EVIDENCE = "The merged PR brownm09/dev-env#900 added the helper script."
# Evidence is copied character for character, markdown included: the stub writes `bash.exe` in backticks.
CP_EVIDENCE = "`bash.exe` from WSL shadows Git Bash"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _force_rmtree(path):
    """``rmtree`` that also removes git's read-only object files (``ignore_errors`` leaves them behind)."""
    def make_writable_and_retry(func, target, _error):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=make_writable_and_retry)
    except TypeError:  # Python < 3.12 has onerror, not onexc
        shutil.rmtree(path, onerror=make_writable_and_retry)


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


def _populate(root):
    _write(root, f"sessions/dev-env/{DEV_NAME}", DEV_STUB)
    _write(root, f"sessions/dev-env/{DEV_NAME[:-8]}.manifest.jsonl", _manifest("dev-env", DEV_NAME))
    _write(root, f"sessions/career-playbook/{CP_NAME}", CP_STUB)
    _write(root, f"sessions/career-playbook/{CP_NAME[:-8]}.manifest.jsonl", _manifest("career-playbook", CP_NAME))
    _write(root, f"sessions/dev-env/{TAIL_NAME}", TAIL_STUB)


@contextmanager
def worktree():
    """A two-project compose worktree with no ``sessions/meta/`` -- the 2026-10-01 shape."""
    root = tempfile.mkdtemp(prefix="jcm-test-")
    try:
        _populate(root)
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


def pr_record(**overrides):
    return record(
        **{"type": "dev-env-pr", "reason": "PR 900 merged.", "evidence": PR_EVIDENCE, **overrides}
    )


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


def stub_file(root, text):
    path = os.path.join(root, "records.jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def stub(root, records, date=DATE):
    """Run ``stub`` with ``records`` written as one JSON list."""
    return run("stub", root, date, stub_file(root, json.dumps(records)))


def stub_lines(root, text, date=DATE):
    """Run ``stub`` with ``text`` written verbatim (JSON Lines, or deliberately malformed input)."""
    return run("stub", root, date, stub_file(root, text))


def kv(out):
    """First value for each ``KEY=value`` line, plus every ``META_STUB`` value as a list."""
    values, stubs = {}, []
    for line in out.splitlines():
        if re.match(r"^[A-Z_]+=", line):
            key, _sep, value = line.partition("=")
            values.setdefault(key, value)
            if key == "META_STUB":
                stubs.append(value)
    values["_stubs"] = stubs
    return values


def rejections(out):
    return [line for line in out.splitlines() if line.startswith("META_TRIGGER_REJECTED")]


def derived_info(root):
    """``[(label, [sources])]`` for every derived stub currently in the worktree, in name order."""
    info = []
    meta = os.path.join(root, "sessions", "meta")
    for name in sorted(os.listdir(meta)):
        if name.endswith(".stub.md"):
            text = _read(root, f"sessions/meta/{name}")
            if text.startswith(mod.DERIVED_MARKER):
                label = mod._DERIVED_LABEL_RE.search(text).group(1)
                info.append((label, list(dict.fromkeys(mod._SOURCE_RE.findall(text)))))
    return info


def derived_sources(root):
    return [source for _label, sources in derived_info(root) for source in sources]


def composed_journal(sessions=(), extra=""):
    """A journal with the eleven required headings and one session per ``(label, [sources])``."""
    body = ""
    for number, (label, sources) in enumerate(sessions, 1):
        cited = "\n".join(f"- `{source}`" for source in sources)
        body += f"\n## Session {number} — {label}\n\nSources:\n{cited}\n"
    if not sessions:
        body = "\n## Session 1 — Routine meta work\n\nNothing derived.\n"
    return f"""# Session Transcript — {DATE}

**Topic:** Meta-relevant changes detected in other projects.

## Contents

- [Opening Brief](#opening-brief)

## Opening Brief

> First session for this project — no prior Next Session Context.

## Key Decisions

### Session 1

- A decision.
{body}{extra}
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


def journal_for(root, **kwargs):
    """A compliant journal for whatever derived stubs are on disk: one labelled session each."""
    return composed_journal(derived_info(root), **kwargs)


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


def _consume_the_day(root):
    """Simulate Step 9 for every project: delete the date's stubs and manifest shards."""
    for base, _dirs, files in os.walk(os.path.join(root, "sessions")):
        for name in files:
            if name.startswith(f"{DATE}_") and name.endswith((".stub.md", ".manifest.jsonl")):
                os.remove(os.path.join(base, name))


def _git(cwd, *args):
    proc = subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "core.autocrlf=false", "-C", cwd, *args],
        capture_output=True,
    )
    assert proc.returncode == 0, (args, proc.stderr.decode("utf-8", errors="replace"))
    return proc.stdout.decode("utf-8", errors="replace")


def _isolate_git(repo, base):
    """Pin the git config a developer's global setup would otherwise inject into a fixture repository.

    This machine has dev-env's git hooks installed globally (``core.hooksPath``); one of them calls ``gh``
    when a past ``draft/`` branch is pushed to a remote whose URL contains "engineering-journal" -- which is
    exactly what the end-to-end fixture does whenever the temp path says so -- and a global
    ``commit.gpgsign`` would fail every commit. A linked worktree shares the main repository's config, so
    this also covers the skill's own ``git -C "$WT"`` calls.
    """
    no_hooks = os.path.join(base, "no-hooks")
    os.makedirs(no_hooks, exist_ok=True)
    for key, value in (("core.hooksPath", no_hooks.replace("\\", "/")), ("commit.gpgsign", "false"),
                       ("user.name", "fixture"), ("user.email", "fixture@example.invalid"),
                       ("core.autocrlf", "false")):
        _git(repo, "config", key, value)


@contextmanager
def git_worktree():
    """A *linked* git worktree holding the committed draft-branch tree: real stubs and manifests."""
    base = tempfile.mkdtemp(prefix="jcm-git-")
    main_repo = os.path.join(base, "main")
    wt = os.path.join(base, f"compose-{DATE}")
    try:
        os.makedirs(main_repo)
        _git(main_repo, "init", "-q")
        _isolate_git(main_repo, base)
        _write(main_repo, "README.md", "# top\n")
        _git(main_repo, "add", "README.md")
        _git(main_repo, "commit", "-q", "-m", "init")
        _git(main_repo, "worktree", "add", "-q", "--detach", wt)
        _populate(wt)
        _write(wt, f"sessions/meta/{DATE}_060000.stub.md", "## Session: 06:00 — A real meta session\n\nBody.\n")
        _write(wt, f"sessions/meta/{DATE}_060000.manifest.jsonl", _manifest("meta", f"{DATE}_060000.stub.md"))
        _write(wt, "sessions/meta/README.md", "# meta\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "-m", "draft branch tip: the day's stubs")
        yield wt
    finally:
        _force_rmtree(base)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_normalize_collapses_whitespace_and_folds_compatibility_forms():
    assert mod.normalize("  a \t b\n c  ") == "a b c"
    assert mod.normalize("The ﬁx") == "The fix"  # U+FB01 ligature -> "fi" under NFKC


def test_clip_keeps_the_head_or_the_tail_and_flattens_whitespace():
    assert mod.clip("one  two\nthree", 50) == "one two three"
    assert mod.clip("abcdefghij", 6) == "abcde…"
    assert mod.clip("abcdefghij", 6, tail=True) == "…fghij"


def test_valid_date_rejects_the_unsubstituted_placeholder():
    assert mod.valid_date("2026-10-01")
    for bad in ("YYYY-MM-DD", "2026-13-01", "2026-02-30", "26-10-01", "", None):
        assert not mod.valid_date(bad), bad


def test_resolve_type_accepts_slugs_in_any_case_and_the_tables_labels():
    assert mod.resolve_type("claude-md") == "claude-md"
    assert mod.resolve_type(" Platform_Constraint ") == "platform-constraint"
    assert mod.resolve_type("CLAUDE.md modified") == "claude-md"
    assert mod.resolve_type("`dev-env` PR merged") == "dev-env-pr"          # copied from the table, backticks and all
    assert mod.resolve_type("workflow FAILURE   remediated") == "workflow-failure"
    assert mod.resolve_type("not a type") is None


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
    # A stub with no H2 (the scheduled routines write `### Session: ...`) falls back to its nearest heading.
    assert mod.session_heading(["### Session: x", "body", "evidence"], 2) == "Session: x"
    assert mod.first_heading_index(["opening brief", "", "## S", "body"]) == 2
    assert mod.first_heading_index(["no heading at all"]) is None


def test_excerpt_never_crosses_a_heading_in_either_direction():
    lines = ["## S", "", "line a", "line b", "line c EVIDENCE-PHRASE here", "line d", "### Next", "line e"]
    assert mod.excerpt(lines, 4) == ["line a", "line b", "line c EVIDENCE-PHRASE here", "line d"]
    # Evidence directly under a heading: the heading itself is not part of the excerpt.
    assert mod.excerpt(["## H", "EVIDENCE X", "more"], 1) == ["EVIDENCE X", "more"]


def test_excerpt_stops_at_html_comment_markers_so_none_are_copied():
    lines = ["## S", "claim line one two", "<!-- tokens: input=1 -->", "<!-- next-session-context -->", "paragraph"]
    assert mod.excerpt(lines, 1) == ["claim line one two"]
    above = ["<!-- opening-brief -->", "Opening brief: text", "claim line one two", "tail"]
    assert mod.excerpt(above, 2) == ["Opening brief: text", "claim line one two", "tail"]


def test_session_sections_run_to_the_next_h2_and_ignore_other_h2s():
    text = "# T\n\n## Intro\nx\n## Session 1 — A\nbody a\n### sub\nmore\n## Session 2 — B\nbody b\n## Reflection\nz\n"
    sections = mod.session_sections(text)
    assert [heading for heading, _body in sections] == ["## Session 1 — A", "## Session 2 — B"]
    assert "more" in sections[0][1] and "body b" not in sections[0][1] and "z" not in sections[1][1]


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
    assert mod.missing_headings(composed_journal()) == []
    text = composed_journal().replace("## Reflection", "## Thoughts").replace("## Key Decisions", "## KD")
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
        assert "\r" not in text and not text.startswith(BOM)


def test_evidence_directly_above_the_stubs_trailing_markers_copies_none_of_them():
    """PR #1126 review: the excerpt used to carry <!-- tokens --> and <!-- next-session-context -->."""
    with worktree() as root:
        evidence = "The hook now refuses a second concurrent compose of the same date."
        rc, out, _err = stub(root, [record(stub=TAIL_NAME, type="workflow-failure", evidence=evidence)])
        assert rc == 0, out
        text = _read(root, f"sessions/meta/{DATE}_235902.stub.md")
        assert f"> {evidence}" in text
        assert text.count("<!-- tokens:") == 1, "only the derived stub's own trailing placeholder"
        for marker in ("<!-- next-session-context", "<!-- opening-brief", "Carry on from here."):
            assert marker not in text, marker


def test_derived_manifest_passes_the_journal_schema():
    with worktree() as root:
        stub(root, [record(), cp_record()])
        for name in (f"{DATE}_235900", f"{DATE}_235901"):
            raw = _read(root, f"sessions/meta/{name}.manifest.jsonl")
            assert raw.endswith("\n") and raw.count("\n") == 1 and not raw.startswith(BOM)
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
        lines = rejections(out)
        assert len(lines) == 1
        assert "dev-env" in lines[0] and DEV_NAME in lines[0] and "dev-env-pr" in lines[0]
        assert "evidence not found" in lines[0]
        assert "universe" not in _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert not _exists(root, f"sessions/meta/{DATE}_235904.stub.md")  # no dev-env-pr stub at all


def test_each_rejection_class_is_named():
    cases = [
        (record(project="meta"), "project 'meta' is skipped"),
        (record(project="META"), "project 'meta' is skipped"),
        (record(project="meta."), "project 'meta' is skipped"),
        (record(project="../escape"), "unsafe project name"),
        (record(project="no-such-project"), "no sessions/no-such-project/ directory"),
        (record(project="Dev-Env"), "names are case-sensitive"),
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
            assert kv(out)["META_STATUS"] == "rejected", (bad, out)
            lines = rejections(out)
            assert len(lines) == 1 and expected in lines[0], (bad, lines)
            assert not _exists(root, "sessions/meta"), bad


def test_evidence_too_short_to_discriminate_is_rejected_by_name():
    """PR #1126 review: "e" and "PR" verified fabricated records. Known-bad (1-2 words) vs known-good (>= 3)."""
    for phrase in ("e", "PR", ".", "-", "the", "The merged", "the gap"):
        with worktree() as root:
            rc, out, _err = stub(root, [record(evidence=phrase)])
            assert rc == 2, phrase
            assert "evidence too short" in rejections(out)[0], phrase
    with worktree() as root:
        rc, out, _err = stub(root, [record(evidence="The merged PR brownm09")])  # 4 words: a phrase
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out
        assert mod.MIN_EVIDENCE_WORDS == 3


def test_rejection_lines_keep_the_records_reason_and_clip_long_fields_from_the_left():
    with worktree() as root:
        long_stub = "x" * 200 + DEV_NAME
        rc, out, _err = stub(root, [record(stub=long_stub, reason="Why this matters   to the journal.")])
        assert rc == 2
        line = rejections(out)[0]
        assert "| reason: Why this matters to the journal." in line
        assert "…" in line and line.count(DEV_NAME) >= 1, "clipped from the left, so the filename survives"


def test_all_records_rejected_exits_2_with_status_rejected_but_an_empty_list_is_none():
    with worktree() as root:
        rc, out, _err = stub(root, [record(evidence="nowhere in the stub at all")])
        assert rc == 2 and kv(out)["META_STATUS"] == "rejected"
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


def test_a_real_orphan_manifest_keeps_its_name_and_survives_stub_and_abandon():
    """PR #1126 review: allocation avoided real stub names but not real manifest names."""
    with worktree() as root:
        orphan = _manifest("meta", f"{DATE}_235900.stub.md")
        _write(root, f"sessions/meta/{DATE}_235900.manifest.jsonl", orphan)
        rc, out, _err = stub(root, [record()])
        assert rc == 0 and kv(out)["_stubs"] == [f"sessions/meta/{DATE}_235901.stub.md"]
        assert _read(root, f"sessions/meta/{DATE}_235900.manifest.jsonl") == orphan
        run("abandon", root, DATE)
        assert _read(root, f"sessions/meta/{DATE}_235900.manifest.jsonl") == orphan


def test_records_pointing_at_one_evidence_line_dedupe_and_the_latest_attempt_wins():
    """PR #1126 review: a retry that quotes the same change differently must not double the entry."""
    with worktree() as root:
        first = record(reason="first attempt", evidence="Edited `claude/CLAUDE.md` to record that unattended")
        second = record(reason="the retry's wording", evidence="record that unattended composes never prompt.")
        rc, out, _err = stub(root, [first, second, cp_record()])
        values = kv(out)
        assert rc == 0 and values["META_TRIGGERS_ACCEPTED"] == "2" and values["META_TRIGGERS_DEDUPED"] == "1"
        text = _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert text.count("- Source: `") == 1
        assert "the retry's wording" in text and "first attempt" not in text


def test_distinct_lines_of_one_stub_and_category_stay_separate_records():
    with worktree() as root:
        other = record(reason="another change", evidence="Ordinary prose with no trigger in it.")
        rc, out, _err = stub(root, [record(), other])
        assert rc == 0 and kv(out)["_stubs"] == [f"sessions/meta/{DATE}_235900.stub.md"]
        text = _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert text.count("\n### ") == 2 and text.count("## CLAUDE.md modified") == 1


def test_type_aliases_normalize_slugs_underscores_case_and_the_tables_labels():
    with worktree() as root:
        records = [record(type="CLAUDE_MD"), cp_record(type=" Platform_Constraint ")]
        rc, out, _err = stub(root, records)
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "2"
    with worktree() as root:
        rc, out, _err = stub(root, [pr_record(type="`dev-env` PR merged")])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out


def test_evidence_matches_after_nfkc_whitespace_and_across_a_wrapped_line():
    with worktree() as root:
        _write(root, f"sessions/dev-env/{DATE}_111111.stub.md", "## S\n\nThe ﬁx   landed\nin   two lines.\n")
        ligature = record(stub=f"{DATE}_111111.stub.md", evidence="The fix landed")
        wrapped = record(stub=f"{DATE}_111111.stub.md", evidence="landed in two lines.", type="convention")
        rc, out, _err = stub(root, [ligature, wrapped])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "2", out
    with worktree() as root:
        rc, out, _err = stub(root, [cp_record(evidence="subprocess calls; the fix is to call the Git for Windows")])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out


def test_stub_paths_may_be_bare_sessions_prefixed_or_absolute_inside_this_worktree():
    with worktree() as root:
        bare = record()
        prefixed = record(stub=f"sessions/dev-env/{DEV_NAME}", type="convention")
        absolute = record(stub=os.path.join(root, "sessions", "dev-env", DEV_NAME), type="journal-structure")
        rc, out, _err = stub(root, [bare, prefixed, absolute])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "3", out
    with worktree() as root:
        outside = os.path.join(root, "records.jsonl")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write(DEV_STUB)
        rc, out, _err = stub(root, [record(stub=outside)])
        assert rc == 2 and "absolute stub path is not a file under sessions/dev-env/" in rejections(out)[0]


def test_records_file_forms_json_lines_list_object_and_failures():
    with worktree() as root:
        path = os.path.join(root, "records.jsonl")
        # Object form with a matching date, and a UTF-8 BOM, are accepted.
        with open(path, "wb") as handle:
            handle.write(BOM.encode("utf-8") + json.dumps({"date": DATE, "records": [record()]}).encode("utf-8"))
        assert run("stub", root, DATE, path)[0] == 0
        # JSON Lines: one record per line, blank lines ignored.
        lines = json.dumps(record()) + "\n\n" + json.dumps(cp_record()) + "\n"
        rc, out, _err = stub_lines(root, lines)
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "2", out
        # A single record on one line is a record, not a malformed batch.
        rc, out, _err = stub_lines(root, json.dumps(record()))
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out
        # A file written for another date is refused outright (stale scratch file).
        rc, _out, err = stub_lines(root, json.dumps({"date": "2026-09-30", "records": [record()]}))
        assert rc == 1 and "not 2026-10-01" in err
        for bad in ('{"records": "nope"}', "42"):
            assert stub_lines(root, bad)[0] == 1, bad
        assert run("stub", root, DATE, os.path.join(root, "absent.jsonl"))[0] == 1


def test_one_malformed_json_line_is_a_named_rejection_not_a_failed_batch():
    """PR #1126 review: one stray \\U (an unescaped Windows path) used to fail every valid record."""
    with worktree() as root:
        good = json.dumps(record())
        bad = ('{"project": "dev-env", "type": "claude-md", "stub": "' + DEV_NAME
               + '", "reason": "r", "evidence": "C:\\Users\\x is a path"}')
        rc, out, _err = stub_lines(root, good + "\n" + bad + "\n")
        values = kv(out)
        assert rc == 0, out
        assert values["META_TRIGGERS_ACCEPTED"] == "1" and values["META_TRIGGERS_REJECTED"] == "1"
        assert values["META_RECORDS_RECEIVED"] == "2"
        assert "line 2 is not valid JSON" in rejections(out)[0]
    with worktree() as root:
        rc, out, _err = stub_lines(root, "{not json\nalso not json\n")
        assert rc == 2 and kv(out)["META_STATUS"] == "rejected" and len(rejections(out)) == 2


def test_a_correctly_escaped_windows_path_in_evidence_is_accepted():
    with worktree() as root:
        _write(root, f"sessions/dev-env/{DATE}_130000.stub.md", "## S\n\nPATH starts with C:\\tools\\bin\\bash.exe on this box.\n")
        rec = record(stub=f"{DATE}_130000.stub.md", evidence="PATH starts with C:\\tools\\bin\\bash.exe on this box.")
        rc, out, _err = stub_lines(root, json.dumps(rec) + "\n")
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out


def test_a_failed_swap_leaves_no_derived_files_and_a_failed_staging_keeps_the_earlier_set():
    """PR #1126 reviews: a mid-write OSError used to leave a half-written set and a stray *.tmp-<pid>;
    the first fix then claimed a rollback to the earlier set that the swap cannot do (it deletes the
    earlier set first), so a failed swap now clears every derived file instead -- all or nothing."""
    with worktree() as root:
        stub(root, [record()])
        assert [path for path in tree(root) if "_2359" in path], "the earlier derived set is in place"
        real_replace = mod.os.replace
        mod.os.replace = lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("locked by the indexer"))
        try:
            rc, _out, err = stub(root, [record(), cp_record()])
        finally:
            mod.os.replace = real_replace
        assert rc == 1 and "no derived files remain" in err
        assert not [path for path in tree(root) if ".tmp-" in path], "no stray temp file"
        assert not [path for path in tree(root) if "_2359" in path], "neither a mixture nor the earlier set: none"
    with worktree() as root:
        stub(root, [record()])
        before = {path: _read(root, path) for path in tree(root) if path.startswith("sessions/meta/")}
        real_stage = mod._stage_text
        calls = []

        def flaky(path, text):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("disk full")
            return real_stage(path, text)

        mod._stage_text = flaky
        try:
            rc, _out, err = stub(root, [record(), cp_record()])
        finally:
            mod._stage_text = real_stage
        assert rc == 1 and "nothing was changed" in err
        after = {path: _read(root, path) for path in tree(root) if path.startswith("sessions/meta/")}
        assert after == before, "the earlier derived set is intact"
        assert not [path for path in tree(root) if ".tmp-" in path]


def test_a_locked_derived_file_is_reported_by_abandon_and_stub_not_raised_as_a_traceback():
    """PR #1126 second review: remove_derived let a PermissionError escape both subcommands."""
    with worktree() as root:
        stub(root, [record()])
        locked = os.path.join(root, "sessions", "meta", f"{DATE}_235900.stub.md")
        real_remove = mod.os.remove

        def guarded(path, *args, **kwargs):
            if os.path.abspath(path) == os.path.abspath(locked):
                raise PermissionError("locked by the indexer")
            return real_remove(path, *args, **kwargs)

        mod.os.remove = guarded
        try:
            rc, out, err = run("abandon", root, DATE)
            assert rc == 1, (out, err)
            assert f"META_ABANDON_FAILED=sessions/meta/{DATE}_235900.stub.md" in out
            assert "locked by the indexer" in err
            assert not _exists(root, f"sessions/meta/{DATE}_235900.manifest.jsonl"), "everything else was still removed"
            # stub cannot remove the earlier set either: exit 1, its staged temp files are cleaned up.
            rc, out, err = stub(root, [record(), cp_record()])
            assert rc == 1 and "could not remove" in err and "run 'abandon'" in err, (out, err)
            assert not [path for path in tree(root) if ".tmp-" in path], "no stray temp file"
        finally:
            mod.os.remove = real_remove
        assert run("abandon", root, DATE)[0] == 0, "once the lock is released, abandon finishes the job"
        assert not [path for path in tree(root) if "_2359" in path]


def test_evidence_in_the_opening_brief_is_rejected_by_name():
    """PR #1126 second review: the opening brief is the previous day's context, not this session's work."""
    with worktree() as root:
        name = f"{DATE}_140000.stub.md"
        _write(root, f"sessions/dev-env/{name}",
               "<!-- opening-brief (first stub of the day only) -->\n"
               "Opening brief: yesterday we fixed the compose skill.\n\n"
               "## Session: 2026-10-01 14:00 — Fresh work\n\n"
               "Today's claim sits in the session body of this stub.\n")
        rc, out, _err = stub(root, [record(stub=name, evidence="yesterday we fixed the compose skill.")])
        assert rc == 2 and "only in the stub's opening brief" in rejections(out)[0], out
        assert not _exists(root, "sessions/meta"), "a rejected record writes nothing"
        # Known-good counterpart: a phrase from the session body of the same stub.
        rc, out, _err = stub(root, [record(stub=name, evidence="claim sits in the session body")])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out
        assert "- Source session: Session: 2026-10-01 14:00 — Fresh work" in _read(root, f"sessions/meta/{DATE}_235900.stub.md")
    with worktree() as root:  # three real stubs omit the marker: their brief begins "Opening brief"
        name = f"{DATE}_140001.stub.md"
        _write(root, f"sessions/dev-env/{name}",
               "<!-- stub: 2026-10-01 140001 -->\nOpening brief: yesterday we fixed the compose skill.\n"
               "<!-- session: fresh-work -->\n## Session: 14:00 — Fresh work\n\nBody text of the session.\n")
        rc, out, _err = stub(root, [record(stub=name, evidence="yesterday we fixed the compose skill.")])
        assert rc == 2 and "only in the stub's opening brief" in rejections(out)[0], out


def test_a_close_marker_ends_the_opening_brief_so_prose_after_it_stays_citable():
    """Fourth review, style note: three real meta stubs close the block with `<!-- /opening-brief -->`. Prose
    between that marker and the first heading is not the carried-forward context, so it must be citable."""
    lines = ["<!-- opening-brief -->", "Opening brief: carried context.", "<!-- /opening-brief -->",
             "**Task:** the real claim sits after the close marker.", "", "## Session: x", "body"]
    assert mod.opening_brief_span(lines) == (0, 3)
    assert mod.opening_brief_span(lines[:2] + lines[3:]) == (0, 4), "without a close marker it runs to the heading"
    assert mod.opening_brief_span(["## Session: x", "Opening brief mentioned in a body line."]) is None
    with worktree() as root:
        name = f"{DATE}_140005.stub.md"
        _write(root, f"sessions/dev-env/{name}", "\n".join(lines) + "\n")
        rc, out, _err = stub(root, [record(stub=name, evidence="the real claim sits after the close marker")])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out
        rc, out, _err = stub(root, [record(stub=name, evidence="Opening brief: carried context.")])
        assert rc == 2 and "only in the stub's opening brief" in rejections(out)[0], out


def test_evidence_the_session_body_repeats_from_the_opening_brief_is_found_in_the_body():
    """Third review, non-blocking 1: session bodies routinely restate the brief that carried their work
    forward (10 of the 65 briefed stubs in the real corpus). The first version took the FIRST occurrence,
    inside the brief, and rejected a quote the subagent had correctly copied from the body."""
    with worktree() as root:
        name = f"{DATE}_140002.stub.md"
        _write(root, f"sessions/dev-env/{name}",
               "<!-- opening-brief (first stub of the day only) -->\n"
               "Opening brief: Next, merge PR #1126 once the third review is clean.\n\n"
               "## Session: 2026-10-01 14:00 — Merge day\n\n"
               "Merged brownm09/dev-env#1126 once the third review is clean and the gate passed.\n")
        phrase = "once the third review is clean"
        rc, out, _err = stub(root, [record(stub=name, evidence=phrase)])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out
        text = _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert "Merged brownm09/dev-env#1126" in text and "Next, merge PR #1126" not in text, (
            "the excerpt must be the body line, not the brief's"
        )
        assert "- Source session: Session: 2026-10-01 14:00 — Merge day" in text


def test_lines_above_the_heading_that_are_not_an_opening_brief_stay_citable():
    """Two real lifting-logbook stubs carry `**PR:**` / `**Issue:**` metadata above their heading. A blanket
    'anything above the first heading is context' rule rejected exactly the lines a dev-env-pr trigger quotes."""
    with worktree() as root:
        name = f"{DATE}_140003.stub.md"
        _write(root, f"sessions/dev-env/{name}",
               "**PR:** [#610](https://github.com/brownm09/dev-env/pull/610)\n"
               "**Issue:** [#609](https://github.com/brownm09/dev-env/issues/609)\n\n"
               "## Session: 2026-10-01 14:00 — Fix\n\nBody.\n")
        # Two adjacent metadata lines, so the quote is long enough to be evidence at all (>= 3 words).
        both = ("**PR:** [#610](https://github.com/brownm09/dev-env/pull/610) "
                "**Issue:** [#609](https://github.com/brownm09/dev-env/issues/609)")
        rc, out, _err = stub(root, [pr_record(stub=name, evidence=both)])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out
    with worktree() as root:  # a body line that mentions "the opening brief" must not open a block of its own
        name = f"{DATE}_140004.stub.md"
        _write(root, f"sessions/dev-env/{name}",
               "## Session: 2026-10-01 14:00 — Docs\n\nOpening brief wording was rewritten in the skill today.\n")
        rc, out, _err = stub(root, [record(stub=name, evidence="Opening brief wording was rewritten in the skill")])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out


def test_a_scheduled_routine_stub_with_only_an_h3_session_heading_stays_citable():
    """The real 2026-10-01 retro-chain-backstop stub opens with `### Session: ...` and has no H2 at all.
    The first fix for the opening-brief case keyed on 'an H2 exists above' and rejected its body --
    found by re-running the dry run on the real day, not by any fixture."""
    with worktree() as root:
        name = f"{DATE}_150000.stub.md"
        _write(root, f"sessions/dev-env/{name}",
               "### Session: retro-chain-backstop (scheduled routine, 2026-10-01)\n\n"
               "**Task:** daily self-healing check of the backlog chain mechanism across all six repos.\n")
        rc, out, _err = stub(root, [record(stub=name, evidence="self-healing check of the backlog chain mechanism")])
        assert rc == 0 and kv(out)["META_TRIGGERS_ACCEPTED"] == "1", out
        text = _read(root, f"sessions/meta/{DATE}_235900.stub.md")
        assert "- Source session: Session: retro-chain-backstop (scheduled routine, 2026-10-01)" in text
    with worktree() as root:  # a stub with no heading at all is malformed but not unciteable
        name = f"{DATE}_150001.stub.md"
        _write(root, f"sessions/dev-env/{name}", "Plain text with the claim somewhere inside it.\n")
        rc, out, _err = stub(root, [record(stub=name, evidence="with the claim somewhere inside")])
        assert rc == 0, out
        assert "(no session heading found)" in _read(root, f"sessions/meta/{DATE}_235900.stub.md")


def test_stub_never_writes_outside_sessions_meta():
    with worktree() as root:
        before = set(tree(root))
        stub(root, [record(), cp_record()])
        added = set(tree(root)) - before - {"records.jsonl"}
        assert added and all(path.startswith("sessions/meta/") for path in added), added


# ---------------------------------------------------------------------------
# install: a gate, not a copy
# ---------------------------------------------------------------------------

def test_install_accepts_a_complete_journal_and_reports():
    with worktree() as root:
        stub(root, [record(), cp_record()])
        assert len(derived_sources(root)) == 2
        rc, out, _err = run("install", root, DATE, stage(root, journal_for(root)), "meta-triggers")
        values = kv(out)
        assert rc == 0, out
        assert values["META_JOURNAL"] == f"sessions/meta/{DATE}-meta-triggers.md"
        assert values["STRUCTURE"] == "ok" and values["SOURCES_CITED"] == "2/2"
        assert values["FIDELITY"] == f"{values['LINE_COUNT']}/{values['SOURCE_LINES']}"
        assert values["REAL_SOURCE_LINES"] == "0" and values["REAL_FIDELITY"] == "n/a"
        installed = _read(root, f"sessions/meta/{DATE}-meta-triggers.md")
        assert "\r" not in installed and not installed.startswith(BOM)
        assert not [name for name in tree(root) if ".tmp-" in name]


def test_install_refuses_a_missing_heading_and_copies_nothing():
    with worktree() as root:
        stub(root, [record()])
        text = journal_for(root).replace("## Next Session Context", "## Next")
        rc, out, _err = run("install", root, DATE, stage(root, text), "meta-triggers")
        assert rc == 2 and "INSTALL_REFUSED" in out
        assert "STRUCTURE=missing:Next Session Context" in out
        assert not _exists(root, f"sessions/meta/{DATE}-meta-triggers.md")


def test_install_refuses_a_journal_that_drops_a_category_even_when_two_share_one_source_stub():
    """PR #1126 blocking finding 1: the old whole-document check passed this with SOURCES_CITED=1/1."""
    with worktree() as root:
        rc, out, _err = stub(root, [record(), pr_record()])  # claude-md + dev-env-pr from ONE dev-env stub
        assert rc == 0 and len(kv(out)["_stubs"]) == 2
        info = derived_info(root)
        assert [label for label, _sources in info] == ["CLAUDE.md modified", "dev-env PR merged"]
        assert info[0][1] == info[1][1] == [f"sessions/dev-env/{DEV_NAME}"], "one shared Source path"
        dropped = composed_journal(info[:1])  # the dev-env-pr category's whole session is missing
        rc, out, _err = run("install", root, DATE, stage(root, dropped), "meta-triggers")
        assert rc == 2, out
        assert "SESSIONS_MISSING=dev-env PR merged" in out and "STRUCTURE=missing" not in out
        assert not _exists(root, f"sessions/meta/{DATE}-meta-triggers.md")
        # Known-good counterpart: both categories present, the shared path cited in each section.
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal(info)), "meta-triggers")
        assert rc == 0 and kv(out)["SOURCES_CITED"] == "2/2", out


def test_install_refuses_a_source_cited_only_in_the_wrong_sessions_section():
    with worktree() as root:
        stub(root, [record(), cp_record()])
        (label_a, sources_a), (label_b, sources_b) = derived_info(root)
        swapped = composed_journal([(label_a, sources_b), (label_b, sources_a)])
        rc, out, _err = run("install", root, DATE, stage(root, swapped), "meta-triggers")
        assert rc == 2
        assert f"SOURCES_UNCITED {label_a} -- {sources_a[0]}" in out
        assert f"SOURCES_UNCITED {label_b} -- {sources_b[0]}" in out


def test_install_without_derived_stubs_reports_n_a_and_the_real_stub_share():
    with worktree() as root:
        _write(root, f"sessions/meta/{DATE}_060000.stub.md", "## Session: 06:00 — Routine\n\nBody.\nMore.\n")
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal()), "routine")
        values = kv(out)
        assert rc == 0 and values["SOURCES_CITED"] == "n/a", out
        assert values["REAL_SOURCE_LINES"] == "4" and values["REAL_FIDELITY"].endswith("/4")


def test_install_with_no_meta_stub_at_all_is_a_precondition_error():
    with worktree() as root:
        rc, _out, err = run("install", root, DATE, stage(root, composed_journal()), "nothing")
        assert rc == 1 and "nothing for this journal to compose" in err
        assert not _exists(root, "sessions/meta")


def test_install_a_derived_stub_without_a_source_line_or_label_is_exit_1_never_a_vacuous_pass():
    with worktree() as root:
        _write(root, f"sessions/meta/{DATE}_235900.stub.md",
               f"{mod.DERIVED_MARKER} fixture -->\n\n## CLAUDE.md modified — detected in dev-env ({DATE})\n\nNo source line.\n")
        rc, _out, err = run("install", root, DATE, stage(root, composed_journal()), "meta-triggers")
        assert rc == 1 and "no extractable 'Source:' line" in err
    with worktree() as root:
        _write(root, f"sessions/meta/{DATE}_235900.stub.md",
               f"{mod.DERIVED_MARKER} fixture -->\n\n## Not the expected heading shape\n\n- Source: `sessions/dev-env/x`\n")
        rc, _out, err = run("install", root, DATE, stage(root, composed_journal()), "meta-triggers")
        assert rc == 1 and "no recognizable category heading" in err
        assert not _exists(root, f"sessions/meta/{DATE}-meta-triggers.md")


def test_install_replaces_the_journal_it_installed_earlier_so_the_fidelity_remedy_works():
    """PR #1126 second review, blocking 2: Step 6.7 says 'expand the staged file and re-run install',
    but install refused any differing journal -- including the one it had written moments earlier."""
    with git_worktree() as wt:
        short = composed_journal()
        rc, out, _err = run("install", wt, DATE, stage(wt, short), "real-day")
        assert rc == 0 and kv(out)["REAL_FIDELITY"].endswith("/3"), out
        longer = composed_journal(extra="\n## Session 2 — More real work\n\nDetail.\n")
        rc, out, _err = run("install", wt, DATE, stage(wt, longer), "real-day")
        assert rc == 0, out
        assert _read(wt, f"sessions/meta/{DATE}-real-day.md") == longer, "the expanded journal replaced it"
        # A different slug on the re-run replaces it too: one journal per date, never two.
        rc, out, _err = run("install", wt, DATE, stage(wt, longer), "renamed")
        assert rc == 0, out
        assert [n for n in tree(wt) if re.match(rf"sessions/meta/{DATE}-.*\.md$", n)] == [
            f"sessions/meta/{DATE}-renamed.md"
        ]


def test_install_never_replaces_a_journal_the_draft_branch_already_carries():
    """The other half of blocking 2: untracked means this run's; tracked means the draft branch's."""
    with git_worktree() as wt:
        _write(wt, f"sessions/meta/{DATE}-prior.md", composed_journal())
        _git(wt, "add", f"sessions/meta/{DATE}-prior.md")
        _git(wt, "commit", "-q", "-m", "a prior compose of the day")
        edited = composed_journal(extra="\n## Session 2 — Other\n\nx\n")
        rc, out, err = run("install", wt, DATE, stage(wt, edited), "prior")
        assert rc == 1 and f"META_JOURNAL_EXISTS=sessions/meta/{DATE}-prior.md" in out, (out, err)
        assert "Do not overwrite or remove it" in err
        assert _read(wt, f"sessions/meta/{DATE}-prior.md") == composed_journal(), "left untouched"
        rc, out, err = run("install", wt, DATE, stage(wt, composed_journal()), "other-slug")
        assert rc == 1 and "META_JOURNAL_EXISTS=" in out and not _exists(wt, f"sessions/meta/{DATE}-other-slug.md")
        # Byte-identical to what the branch carries: nothing to do, and that is success.
        rc, out, _err = run("install", wt, DATE, stage(wt, composed_journal()), "prior")
        assert rc == 0 and kv(out)["META_JOURNAL"] == f"sessions/meta/{DATE}-prior.md", out


def test_install_matches_each_category_to_its_own_session_not_the_first_one_carrying_the_label():
    """PR #1126 second review, non-blocking 2: next(... if label in heading) judged only the FIRST
    session whose title mentions the label, so an earlier real session on the same topic hid it."""
    with worktree() as root:
        stub(root, [pr_record()])
        ((label, sources),) = derived_info(root)
        shadow = ("dev-env PR merged: #1121 abbreviations rule", [])  # a real session; it cites nothing
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal([shadow, (label, sources)])), "meta-triggers")
        assert rc == 0 and kv(out)["SOURCES_CITED"] == "1/1", out
    with worktree() as root:  # known-bad: only the label-bearing real session exists
        stub(root, [pr_record()])
        ((label, sources),) = derived_info(root)
        rc, out, _err = run("install", root, DATE, stage(root, composed_journal([shadow])), "meta-triggers")
        assert rc == 2 and f"SOURCES_UNCITED {label} -- {sources[0]}" in out, out
    with worktree() as root:  # known-bad: one session titled with two labels may stand for only one category
        stub(root, [record(), pr_record()])  # claude-md + dev-env-pr from ONE dev-env stub
        info = derived_info(root)
        both = composed_journal([("CLAUDE.md modified and dev-env PR merged", info[0][1])])
        rc, out, _err = run("install", root, DATE, stage(root, both), "meta-triggers")
        assert rc == 2 and "SESSIONS_MISSING=dev-env PR merged" in out, out


def test_install_never_replaces_a_journal_git_cannot_vouch_for_and_names_a_different_cause():
    """PR #1126 review: same-slug installs silently replaced an already-committed journal. A directory
    git cannot vouch for (no linked worktree) keeps the conservative rule -- and, third review, non-blocking
    4, reports it as META_INSTALL_UNVERIFIED, not as 'already exists in the draft branch': Step 6.7's
    failure policy reads META_JOURNAL_EXISTS as a reason to stop, and a git problem is not that."""
    with worktree() as root:
        stub(root, [record()])
        staged = stage(root, journal_for(root))
        assert run("install", root, DATE, staged, "first")[0] == 0
        assert run("install", root, DATE, staged, "first")[0] == 0, "byte-identical re-run is a no-op success"
        rc, out, err = run("install", root, DATE, stage(root, journal_for(root, extra="\nEdited.\n")), "first")
        assert rc == 1 and "META_INSTALL_UNVERIFIED=not a linked git worktree" in out, (out, err)
        assert "META_JOURNAL_EXISTS" not in out and "cannot tell whether" in err and "not replacing it" in err
        rc, out, err = run("install", root, DATE, staged, "second")
        assert rc == 1 and "META_INSTALL_UNVERIFIED=" in out, (out, err)
        assert [n for n in tree(root) if re.match(rf"sessions/meta/{DATE}-.*\.md$", n)] == [
            f"sessions/meta/{DATE}-first.md"
        ]


def test_install_in_a_linked_worktree_whose_git_fails_is_unverified_not_already_existing():
    with git_worktree() as wt:
        _write(wt, f"sessions/meta/{DATE}-mine.md", composed_journal())  # untracked: this run's
        assert run("install", wt, DATE, stage(wt, composed_journal(extra="\n## Session 2 — More\n\nx\n")), "mine")[0] == 0
        link = os.path.join(wt, ".git")
        os.chmod(link, stat.S_IWRITE)
        os.remove(link)  # git hides `.git` on Windows, and a hidden file cannot be reopened for writing
        with open(link, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("gitdir: C:/no/such/place/.git/worktrees/gone\n")  # the pointer git needs is broken
        rc, out, err = run("install", wt, DATE, stage(wt, composed_journal(extra="\n## Session 2 — Other\n\ny\n")), "mine")
        assert rc == 1 and "META_INSTALL_UNVERIFIED=" in out and "META_JOURNAL_EXISTS" not in out, (out, err)
        assert "cannot tell whether" in err


def test_install_does_not_let_a_subtitle_naming_another_category_capture_its_session():
    """Third review, non-blocking 3: a label matched ANYWHERE in a title let 'dev-env PR merged: the PR that
    left CLAUDE.md modified' capture the CLAUDE.md category, so the gate blamed the wrong session and obeying
    its report produced a journal it accepted with the real citation still missing."""
    with worktree() as root:
        stub(root, [record(), pr_record()])  # claude-md + dev-env-pr from ONE dev-env stub
        (label_a, sources_a), (label_b, sources_b) = derived_info(root)
        assert (label_a, label_b) == ("CLAUDE.md modified", "dev-env PR merged")
        trap = composed_journal([
            (label_a, []),                                                                  # citation forgotten
            (f"{label_b}: the PR that left {label_a}", sources_b),                          # cites, subtitle names A
        ])
        rc, out, _err = run("install", root, DATE, stage(root, trap), "meta-triggers")
        assert rc == 2 and f"SOURCES_UNCITED {label_a} -- {sources_a[0]}" in out, out
        assert "SESSIONS_MISSING" not in out, "the gate must blame the session that really lacks the citation"
        fixed = composed_journal([(label_a, sources_a), (f"{label_b}: the PR that left {label_a}", sources_b)])
        rc, out, _err = run("install", root, DATE, stage(root, fixed), "meta-triggers")
        assert rc == 0 and kv(out)["SOURCES_CITED"] == "2/2", out


def test_install_matches_a_title_to_its_label_ignoring_backticks_case_and_spacing():
    """Fourth review, non-blocking 1: the Step 2b table itself writes two labels with backticks
    (`` `CLAUDE.md` modified``, `` `dev-env` PR merged``) and a coordinator may capitalize or bold a title.
    resolve_type already treated those as the same label; the install gate must not be the one place that
    did not, because a second refusal sends the triggers to the manual recovery runbook."""
    variants = (
        ("`CLAUDE.md` modified", "`dev-env` PR merged"),                  # the Step 2b table's own spelling
        ("CLAUDE.md Modified", "Dev-env PR merged: a subtitle"),
        ("CLAUDE.md  modified", "dev-env  PR  merged"),                   # doubled spaces
        ("**CLAUDE.md modified**", "**dev-env PR merged**"),              # bold
        ("CLAUDE.md modified.", "dev-env PR merged — and more"),          # a trailing period, a dash subtitle
    )
    for title_a, title_b in variants:
        with worktree() as root:
            stub(root, [record(), pr_record()])
            (_label_a, sources_a), (_label_b, sources_b) = derived_info(root)
            rc, out, _err = run("install", root, DATE, stage(root, composed_journal([(title_a, sources_a), (title_b, sources_b)])), "x")
            assert rc == 0 and kv(out)["SOURCES_CITED"] == "2/2", (title_a, title_b, out)
    with worktree() as root:  # known-bad: the title merely CONTAINS the label; it does not begin with it
        stub(root, [record(), pr_record()])
        (label_a, sources_a), (label_b, sources_b) = derived_info(root)
        journal = composed_journal([(f"Notes on {label_a}", sources_a), (label_b, sources_b)])
        rc, out, _err = run("install", root, DATE, stage(root, journal), "x")
        assert rc == 2 and f"SESSIONS_MISSING={label_a}" in out, out


def test_install_rejects_bad_slugs_and_unreadable_or_empty_input():
    with worktree() as root:
        stub(root, [record()])
        staged = stage(root, journal_for(root))
        for bad in ("Bad Slug", "../x", "", "-lead", "x" * 81):
            assert run("install", root, DATE, staged, bad)[0] == 1, bad
        assert run("install", root, DATE, os.path.join(root, "absent.md"), "ok")[0] == 1
        assert run("install", root, DATE, stage(root, "  \n"), "ok")[0] == 1


def test_install_accepts_a_crlf_composed_journal_and_writes_lf():
    with worktree() as root:
        stub(root, [record()])
        text = journal_for(root).replace("\n", "\r\n")
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

def test_abandon_removes_only_derived_files_and_stray_temp_files():
    with worktree() as root:
        _write(root, f"sessions/meta/{DATE}_060000.stub.md", "## Session: real\n\nBody.\n")
        _write(root, f"sessions/meta/{DATE}_060000.manifest.jsonl", _manifest("meta", f"{DATE}_060000.stub.md"))
        stub(root, [record(), cp_record()])
        _write(root, f"sessions/meta/{DATE}_235900.stub.md.tmp-123", "half written\n")
        rc, out, _err = run("abandon", root, DATE)
        assert rc == 0 and out.startswith("META_ABANDONED=") and "none" not in out
        assert len(out.split("=", 1)[1].split(",")) == 5
        assert _exists(root, f"sessions/meta/{DATE}_060000.stub.md")
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

def test_check_clean_fails_on_each_leftover_shape_and_names_it():
    shapes = {
        f"sessions/dev-env/{DEV_NAME}": "an unconsumed stub",
        f"sessions/dev-env/{DATE}_101530.manifest.jsonl": "an unconsumed manifest shard",
        f"sessions/dev-env/{DATE}.manifest.jsonl": "a legacy per-day manifest",
        f"sessions/meta/{DATE}_draft.md": "the old Step 2b output",
        f"sessions/meta/{DATE}_235900.stub.md": "a derived stub",
        f"sessions/meta/{DATE}_235900.stub.md.tmp-4242": "a temp file from a failed write",
        f"sessions/meta/{DATE}-meta-day.md.tmp-4242": "a temp file from a failed install",
    }
    for path, why in shapes.items():
        with worktree() as root:
            _consume_the_day(root)
            assert run("check-clean", root, DATE)[0] == 0
            _write(root, path, "x\n")
            rc, out, _err = run("check-clean", root, DATE)
            assert rc == 2, why
            assert "CHECK_CLEAN=leftover" in out and f"LEFTOVER {path}" in out, why


def test_check_clean_scans_one_level_deep_and_ignores_other_dates_and_composed_journals():
    with worktree() as root:
        _consume_the_day(root)
        # The date-mismatched shape PR #182 carried through: stubs for the NEXT day sitting on the branch.
        _write(root, "sessions/dev-env/2026-10-02_090000.stub.md", "x\n")
        _write(root, "sessions/dev-env/2026-10-02_090000.manifest.jsonl", "{}\n")
        _write(root, f"sessions/dev-env/{DATE}-scheduled-routines.md", "# composed\n")  # a hyphen, not a stub
        _write(root, "sessions/notes.txt", "stray file at the sessions root\n")
        # Documented limit: only sessions/<project>/ itself is scanned (Step 1's glob is the same depth).
        _write(root, f"sessions/meta/archive/{DATE}_draft.md", "nested\n")
        _write(root, f"sessions/{DATE}_090000.stub.md", "root-level\n")
        assert run("check-clean", root, DATE) == (0, "CHECK_CLEAN=ok\n", "")


def test_check_clean_on_a_tree_without_sessions_is_a_precondition_error_not_a_pass():
    root = tempfile.mkdtemp(prefix="jcm-test-")
    try:
        rc, _out, err = run("check-clean", root, DATE)
        assert rc == 1 and "no sessions/ directory" in err
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# check-staged: the commit is built from the index, not the working tree
# ---------------------------------------------------------------------------

def test_check_staged_catches_what_check_clean_cannot_see():
    """PR #1126 blocking finding 3: check-clean was green while the index still held real meta stubs
    and the composed meta journal was untracked -- the #892 shape, shipped with a green check."""
    with git_worktree() as wt:
        # The coordinator composed meta, ran Step 9's rm, edited the README -- and staged nothing.
        _consume_the_day(wt)
        _write(wt, f"sessions/meta/{DATE}-meta-day.md", "# composed meta journal\n")
        _write(wt, "sessions/meta/README.md", "# meta\nA new row.\n")
        assert run("check-clean", wt, DATE) == (0, "CHECK_CLEAN=ok\n", ""), "the blind spot: the working tree is clean"
        rc, out, _err = run("check-staged", wt, DATE)
        assert rc == 2 and "CHECK_STAGED=unstaged" in out
        assert f"UNSTAGED ?? sessions/meta/{DATE}-meta-day.md" in out, "the composed journal is untracked"
        assert f"UNSTAGED  D sessions/meta/{DATE}_060000.stub.md" in out, "the real meta stub's deletion is unstaged"
        assert "UNSTAGED  M sessions/meta/README.md" in out
        # Known-good counterpart: the skill's own staging commands.
        _git(wt, "add", f"sessions/meta/{DATE}-meta-day.md", "sessions/meta/README.md")
        _git(wt, "add", "-u", "sessions/")
        assert run("check-staged", wt, DATE) == (0, "CHECK_STAGED=ok\n", "")


def test_check_staged_ignores_compose_lock_files_and_reports_a_git_failure():
    with git_worktree() as wt:
        _consume_the_day(wt)
        _git(wt, "add", "-u", "sessions/")
        _write(wt, "sessions/dev-env/.draft-compose.lock", "2026-10-02T00:00:00Z\n")
        _write(wt, ".compose-creating", "2026-10-02T00:00:00Z\n")
        assert run("check-staged", wt, DATE) == (0, "CHECK_STAGED=ok\n", "")
    with worktree() as root:  # a directory that is not a git repository at all
        rc, _out, err = run("check-staged", root, DATE)
        assert rc == 1 and "git status failed" in err


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

        # Step 6.7 step 4: the coordinator composes from the derived stubs (here: a compliant fixture).
        assert sorted(derived_sources(root)) == sorted(
            [f"sessions/dev-env/{DEV_NAME}", f"sessions/career-playbook/{CP_NAME}"]
        )
        rc, out, _err = run("install", root, DATE, stage(root, journal_for(root)), "meta-triggers")
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
        rc, out, _err = run("install", root, DATE, stage(root, journal_for(root)), "meta-day")
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
        assert run("check-staged", root)[0] == 1                                # too few arguments
        rc, _out, err = run("check-clean", root, "YYYY-MM-DD")
        assert rc == 1 and "is not a YYYY-MM-DD date" in err                    # unsubstituted placeholder
        assert run("check-clean", root, "2026-13-45")[0] == 1
        assert run("check-clean", os.path.join(root, "no-such-dir"), DATE)[0] == 1


def test_an_empty_or_relative_worktree_argument_never_means_the_current_directory():
    """PR #1126 review: normpath('') is '.', so an unsubstituted $WT used to act on the cwd."""
    with worktree() as root:
        here = os.getcwd()
        os.chdir(root)  # a cwd that does have sessions/ -- the dangerous case
        try:
            for bad in ("", "   ", ".", "sessions/..", "relative/dir"):
                rc, _out, err = run("check-clean", bad, DATE)
                assert rc == 1 and "must be an absolute path" in err, bad
            rc, _out, _err = run("stub", "", DATE, stub_file(root, json.dumps([record()])))
            assert rc == 1 and not _exists(root, "sessions/meta")
        finally:
            os.chdir(here)


def test_a_primary_checkout_is_refused_but_a_linked_worktree_is_not():
    base = tempfile.mkdtemp(prefix="jcm-primary-")
    try:
        _git(base, "init", "-q")
        _write(base, f"sessions/dev-env/{DEV_NAME}", DEV_STUB)
        rc, _out, err = run("check-clean", base, DATE)
        assert rc == 1 and "primary checkout" in err
    finally:
        shutil.rmtree(base, ignore_errors=True)
    with git_worktree() as wt:
        assert run("check-clean", wt, DATE)[0] == 2, "reaches the check (and finds the day's stubs)"


# ---------------------------------------------------------------------------
# Drift gates: the skill, routine and global CLAUDE.md must agree with the helper
# ---------------------------------------------------------------------------

def _read_doc(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read().replace("\r\n", "\n")


def _section(text, heading_regex):
    """From the first heading matching ``heading_regex`` to the next heading of equal-or-higher rank."""
    match = re.search(heading_regex, text, re.MULTILINE)
    assert match, f"section not found: {heading_regex}"
    level = len(re.match(r"#+", match.group(0)).group(0))
    stop = re.compile(rf"^#{{1,{level}}} ")
    kept, in_fence = [], False
    for line in text[match.end():].split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and stop.match(line):  # a '# comment' inside a bash fence is not a heading
            break
        kept.append(line)
    return match.group(0) + "\n".join(kept)


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


def test_step_2b_table_labels_equal_the_helpers_labels():
    """The category label is also the session-title token the install gate looks for."""
    skill = _read_doc(_SKILL)
    step_2b = _section(skill, r"^## Step 2b — .*$")
    rows = re.findall(r"^\| ([^|]+?) \| `([a-z-]+)` \|", step_2b, re.MULTILINE)
    assert len(rows) == len(mod.TRIGGER_TYPES), f"expected {len(mod.TRIGGER_TYPES)} table rows, found {len(rows)}"
    for (label, slug), (want_slug, want_label) in zip(rows, mod.TRIGGER_TYPES):
        assert slug == want_slug
        assert label.replace("`", "") == want_label, (label, want_label)


def test_phase_1_template_inlines_the_same_slugs_because_a_subagent_follows_its_own_copy():
    """ADR-082's 2026-07-23 addendum: a subagent acts on the copy in its template, not a pointer."""
    lines = _read_doc(_SKILL).split("\n")
    marker = next((i for i, line in enumerate(lines) if "exactly one of these seven" in line), None)
    assert marker is not None, "the Phase 1 template's Step 2b must inline the trigger slugs"
    slugs, started = [], False
    for line in lines[marker + 1: marker + 6]:  # the sentence may wrap once before the list starts
        if re.fullmatch(r"\s+[a-z-]+(?:,\s*[a-z-]+)*,?\s*", line):
            started = True
            slugs += re.findall(r"[a-z-]+", line)
        elif started:
            break
    assert slugs, "no slugs extracted from the template's inline list"
    assert slugs == [slug for slug, _label in mod.TRIGGER_TYPES]
    template = "\n".join(lines[marker - 8: marker + 12])
    assert '"project":"<project>"' in template, "the template must make the subagent emit its own project"


def test_global_claude_md_trigger_list_matches_the_helpers_seven_categories():
    """Step 2b names claude/CLAUDE.md as the source of the trigger criteria; tie the two together."""
    text = _read_doc(_GLOBAL_CLAUDE_MD)
    start = text.index("**Meta journal** (`sessions/meta/`):")  # a bold line, not a '#' heading
    section = text[start: text.index("**Full journal conventions:**", start)]
    assert len(section.splitlines()) > 5, "Meta journal section extraction came back empty"
    bullets = [line for line in section.split("\n") if line.startswith("- When ")]
    assert len(bullets) == len(mod.TRIGGER_TYPES), f"expected 7 'When ...' bullets, found {len(bullets)}"
    keyword = {
        "claude-md": r"`CLAUDE\.md` is modified",
        "platform-constraint": r"platform constraint",
        "workflow-failure": r"workflow failure",
        "convention": r"cross-project convention",
        "journal-structure": r"journal structure",
        "canonical-reference": r"canonical reference",
        "dev-env-pr": r"dev-env` PR is merged",
    }
    assert set(keyword) == {slug for slug, _label in mod.TRIGGER_TYPES}
    for slug, pattern in keyword.items():
        hits = [bullet for bullet in bullets if re.search(pattern, bullet)]
        assert len(hits) == 1, f"{slug}: expected exactly one bullet matching {pattern!r}, found {len(hits)}"


def test_skill_no_longer_prompts_or_writes_a_draft_file():
    skill = _read_doc(_SKILL)
    step_2b = _section(skill, r"^## Step 2b — .*$")
    assert len(step_2b.splitlines()) > 5, "Step 2b section extraction came back empty"
    flat = re.sub(r"\s+", " ", skill)  # robust to a reflowed paragraph: ban the prompt's shape, not its line breaks
    for gone in (
        "Should I open a meta draft block",
        "y (append meta block",
        "present the findings to the user before continuing",
        "create it with `<!-- draft",
    ):
        assert gone not in flat, f"the old interactive Step 2b text is back: {gone!r}"
    assert not re.search(r"git .*\badd\b.*sessions/meta/YYYY-MM-DD_draft\.md", skill), (
        "the skill must not stage a sessions/meta/YYYY-MM-DD_draft.md (dev-env#892)"
    )
    assert "never prompts" in step_2b.lower()


def test_skill_wires_step_6_7_to_every_subcommand_and_step_10_to_both_checks():
    skill = _read_doc(_SKILL)
    step_67 = _section(skill, r"^## Step 6\.7 — .*$")
    assert len(step_67.splitlines()) > 10, "Step 6.7 section extraction came back empty"
    assert "journal-compose-meta.py" in step_67
    for name in ("stub", "install", "abandon"):
        assert re.search(rf"journal-compose-meta\.py {name} ", step_67), f"Step 6.7 must show the {name} invocation"
    assert re.search(r"Steps 7, 8, 8a, 8b, 9 and 9\.5 then run for meta", step_67), (
        "Step 6.7's closing step list must name 8a and 8b, or meta skips the stray-output scan"
    )
    step_10 = _section(skill, r"^## Step 10 — .*$")
    for name in ("check-clean", "check-staged"):
        assert re.search(rf"journal-compose-meta\.py {name} ", step_10), f"Step 10 must run {name}"
    phase_2 = _section(skill, r"^### Phase 2 — .*$")
    # An executable invocation inside a fenced block -- a prose mention of the word does not run anything.
    assert re.search(r"^\s*py -3 \S*journal-compose-meta\.py check-staged ", phase_2, re.MULTILINE), (
        "Phase 2 must invoke check-staged, not merely mention it"
    )


def _fenced_blocks(section):
    blocks, current = [], None
    for line in section.split("\n"):
        if line.lstrip().startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append(current)
                current = None
        elif current is not None:
            current.append(line)
    return blocks


def test_every_commit_block_re_runs_check_staged_first_and_stops_on_its_failure():
    """PR #1126 second review, blocking 1: Phase 2's block ran check-staged and then committed and
    pushed unconditionally, so the check gated nothing. Pin the invocation AND its position."""
    skill = _read_doc(_SKILL)
    for name, heading in (("Phase 2", r"^### Phase 2 — .*$"), ("Step 10", r"^## Step 10 — .*$")):
        section = _section(skill, heading)
        commit_blocks = [b for b in _fenced_blocks(section) if any(re.match(r'\s*git -C "\$WT" commit\b', l) for l in b)]
        assert commit_blocks, f"{name}: no fenced block containing the commit was extracted"
        for block in commit_blocks:
            commit_at = next(i for i, l in enumerate(block) if re.match(r'\s*git -C "\$WT" commit\b', l))
            guards = [i for i, l in enumerate(block)
                      if re.match(r"\s*py -3 \S*journal-compose-meta\.py check-staged ", l)]
            assert guards and guards[0] < commit_at, f"{name}: check-staged must run before the commit, in the same block"
            guard = block[guards[0]]
            # Anchored: the stop must be a brace group on the same line. `|| ( ...; exit 1 )` exits only a
            # subshell, `|| echo "...exit..."` and `|| true  # exit 1` stop nothing -- each looked like a
            # guard to the earlier substring check and each lets the commit through (third review, NB 2).
            assert re.fullmatch(
                r'\s*py -3 \S*journal-compose-meta\.py check-staged "\$WT" YYYY-MM-DD \|\| \{ echo "[^"]*"; exit 1; \}\s*',
                guard,
            ), f"{name}: a failed check-staged must stop the block with a brace-group exit: {guard!r}"
            # The push target is the resolved branch, never a literal: on the `-recovery` path a literal
            # `draft/YYYY-MM-DD` pushes the compose commit to the wrong ref (fourth review, non-blocking 3).
            assert 'git -C "$WT" push origin "HEAD:refs/heads/$SOURCE_BRANCH"' in block, (
                f"{name}: the commit block must push to $SOURCE_BRANCH"
            )
        # Staging is keyed on META_JOURNAL, never on the PR-body status: a FAILED pass can still have
        # installed a real-stub journal (second review, non-blocking 1). Any mention of META_STATUS in
        # these sections -- however it is worded -- is the old keying coming back.
        assert "META_STATUS" not in section, f"{name}: staging must key on META_JOURNAL, not META_STATUS"
        assert "META_JOURNAL" in section, f"{name}: the meta staging must be conditioned on META_JOURNAL"
    phase_2 = _section(skill, r"^### Phase 2 — .*$")
    staging = [b for b in _fenced_blocks(phase_2) if any("add -u sessions/" in l for l in b)]
    assert staging and any(
        re.match(r"\s*py -3 \S*journal-compose-meta\.py check-staged ", l) for l in staging[0]
    ), "Phase 2's staging block must end with the check, so its result is read before any commit"
    # The push target is a loud placeholder, not a literal draft/YYYY-MM-DD: on the `-recovery` path a
    # coordinator that substitutes only the date would otherwise push to the wrong branch silently.
    commit_block = next(b for b in _fenced_blocks(phase_2) if any(re.match(r'\s*git -C "\$WT" commit\b', l) for l in b))
    assert any(re.match(r"SOURCE_BRANCH=<[^>]+>", l) for l in commit_block), (
        "Phase 2's commit block must define SOURCE_BRANCH as an explicit <placeholder> to be substituted"
    )
    step_67 = _section(skill, r"^## Step 6\.7 — .*$")
    for definition in ("- `META_JOURNAL` — the", "- `META_STATUS` — what the PR body says"):
        assert definition in step_67, f"Step 6.7 must define {definition!r}"


# ---------------------------------------------------------------------------
# End to end: the skill's OWN commit blocks, extracted and run in a real bash against a real git repo
# ---------------------------------------------------------------------------

def _git_bash():
    """A Git for Windows bash. Fails loudly rather than skipping: a skipped end-to-end gate is no gate.

    ``shutil.which("bash")`` can be the WSL launcher in System32, which cannot run these blocks, so the
    interpreter is also looked for beside git itself.
    """
    candidates = []
    found = shutil.which("bash")
    if found and not any(part in found.lower() for part in ("system32", "windowsapps")):
        candidates.append(found)
    git = shutil.which("git")
    if git:
        here = os.path.dirname(os.path.realpath(git))
        for hops in ("", "..", os.path.join("..", ".."), os.path.join("..", "..", "..")):
            candidates.append(os.path.normpath(os.path.join(here, hops, "bin", "bash.exe")))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise AssertionError(f"no Git for Windows bash found (tried {candidates})")


@contextmanager
def compose_day(projects):
    """A bare origin, a draft branch, and a linked compose worktree left as Phase 1, Step 6.7 (a real-stub-only
    meta journal), Steps 7-9 leave it: journals and READMEs written, every stub deleted -- nothing staged."""
    base = tempfile.mkdtemp(prefix="jcm-e2e-")
    origin = os.path.join(base, "origin.git")
    main_repo = os.path.join(base, "ej")
    wt = os.path.join(base, f"compose-{DATE}")
    # The `-recovery` branch (Step 0.6 documents it): a skill block that pushed a LITERAL
    # `draft/YYYY-MM-DD` would land on a different ref, and the tip assertions below would fail.
    branch = f"draft/{DATE}-recovery"
    try:
        os.makedirs(main_repo)
        subprocess.run(["git", "init", "-q", "--bare", origin], check=True, capture_output=True)
        _git(main_repo, "init", "-q")
        _isolate_git(main_repo, base)
        _git(main_repo, "remote", "add", "origin", origin.replace("\\", "/"))
        _write(main_repo, "README.md", "# top\n")
        for project in (*projects, "meta"):
            _write(main_repo, f"sessions/{project}/README.md", f"# {project}\n")
            _write(main_repo, f"sessions/{project}/{DATE}_090000.stub.md", f"## Session: {project}\n\nbody\n")
        _git(main_repo, "add", "-A")
        _git(main_repo, "commit", "-q", "-m", "draft tip: the day's stubs")
        _git(main_repo, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        _git(main_repo, "worktree", "add", "-q", "--detach", wt, "HEAD")
        tip = _git(wt, "rev-parse", "HEAD").strip()
        for project in projects:
            _write(wt, f"sessions/{project}/{DATE}-slug-a.md", "# journal\n")
            _write(wt, f"sessions/{project}/README.md", f"# {project}\na row\n")
        _write(wt, f"sessions/meta/{DATE}-real-only.md", "# meta journal from the real stubs\n")
        _write(wt, "sessions/meta/README.md", "# meta\na row linking the meta journal\n")
        _write(wt, "README.md", "# top\nupdated\n")
        for project in (*projects, "meta"):
            os.remove(os.path.join(wt, "sessions", project, f"{DATE}_090000.stub.md"))
        yield {"base": base, "origin": origin, "wt": wt, "branch": branch, "tip": tip}
    finally:
        _force_rmtree(base)


def _fill(block, day, projects):
    """Turn a skill block into a runnable script: the helper under test, this fixture's paths."""
    wt = day["wt"].replace("\\", "/")
    text = block.replace("C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-YYYY-MM-DD", wt)
    text = text.replace("C:/Users/brown/.claude/scripts/journal-compose-meta.py", mod_posix_path())
    text = re.sub(r"^\s*\.\.\. \\\n", "", text, flags=re.MULTILINE)  # the "..." placeholder continuation line
    for placeholder, value in (("<slug-a>", "slug-a"), ("<slug-b>", "slug-a"), ("<slug>", "slug-a"),
                               ("<meta-slug>", "real-only"), ("<project>", projects[0])):
        text = text.replace(placeholder, value)
    text = re.sub(r"SOURCE_BRANCH=<[^>\n]*>", f"SOURCE_BRANCH={day['branch']}", text)
    text = text.replace("YYYY-MM-DD", DATE)
    if not re.search(r"^WT=", text, re.MULTILINE):  # Step 10's blocks assume WT and SOURCE_BRANCH are set
        text = f"WT={wt}\nSOURCE_BRANCH={day['branch']}\n" + text
    # Every substitution must have taken effect. If the skill rewords its `WT=` line or the helper path,
    # the block would otherwise run against the REAL engineering-journal checkout or the INSTALLED helper
    # and still look like a pass (fourth review, non-blocking 2).
    assert "C:/Users/brown/Git/engineering-journal" not in text, "a WT= path was not substituted"
    assert "C:/Users/brown/.claude/scripts" not in text, "a helper path was not substituted"
    if "journal-compose-meta.py" in block:
        assert mod_posix_path() in text, "the block must run the helper under test"
    assert f"WT={wt}" in text, "WT is not the fixture's worktree"
    if "SOURCE_BRANCH" in block or '"$SOURCE_BRANCH"' in block:
        assert f"SOURCE_BRANCH={day['branch']}" in text, "SOURCE_BRANCH is not the fixture's branch"
    for leftover in ("<slug", "<meta-slug>", "<project>", "SOURCE_BRANCH=<", "YYYY-MM-DD"):
        assert leftover not in text, f"placeholder {leftover!r} survived substitution"
    return text


def mod_posix_path():
    return os.path.join(_SCRIPTS, "journal-compose-meta.py").replace("\\", "/")


def _run_block(text, day):
    script = os.path.join(day["base"], "block.sh")
    with open(script, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    proc = subprocess.run([_git_bash(), script.replace("\\", "/")], capture_output=True, cwd=day["base"])
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def _remote_tip(day):
    proc = subprocess.run(["git", "--git-dir", day["origin"], "rev-parse", f"refs/heads/{day['branch']}"],
                          capture_output=True)
    return proc.stdout.decode().strip()


def _pushed_changes(day):
    out = subprocess.run(["git", "--git-dir", day["origin"], "show", "--name-status", "--format=", _remote_tip(day)],
                         capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return [line.replace("\t", " ") for line in out.splitlines() if line.strip()]


def test_phase_2_blocks_end_to_end_a_failed_check_publishes_nothing_and_a_clean_index_commits_meta():
    """PR #1126 second review, blocking 1, as a regression test instead of a manual run: the skill's own two
    Phase 2 blocks, extracted and executed. A structural test of the text cannot tell a guard that stops from
    one that merely looks like it (a subshell `exit` stops nothing); only running it can."""
    skill = _read_doc(_SKILL)
    blocks = ["\n".join(b) for b in _fenced_blocks(_section(skill, r"^### Phase 2 — .*$"))]
    staging = next(b for b in blocks if "add -u sessions/" in b and "check-staged" in b)
    commit = next(b for b in blocks if re.search(r'git -C "\$WT" commit', b))
    projects = ("project-a", "project-b")
    with compose_day(projects) as day:
        # A: the failure policy's real-stub-only path, with the meta `git add` skipped.
        omitted = re.sub(r'^git -C "\$WT" add sessions/meta/[^\n]*\n', "", staging, flags=re.MULTILINE)
        assert omitted != staging, "the fixture must actually omit the meta add"
        rc, out, _err = _run_block(_fill(omitted, day, projects), day)
        assert rc == 2 and "CHECK_STAGED=unstaged" in out and f"?? sessions/meta/{DATE}-real-only.md" in out, (rc, out)
        rc, out, err = _run_block(_fill(commit, day, projects), day)
        assert rc != 0 and "CHECK_STAGED=unstaged" in out, f"the commit block must refuse: rc={rc} out={out!r} err={err!r}"
        assert _remote_tip(day) == day["tip"], "nothing may be pushed past a failed check-staged"
        # B: the same day with the meta add executed (git add is idempotent, so the same worktree continues).
        rc, out, _err = _run_block(_fill(staging, day, projects), day)
        assert rc == 0 and "CHECK_STAGED=ok" in out, (rc, out)
        rc, out, err = _run_block(_fill(commit, day, projects), day)
        assert rc == 0, (out, err)
        assert _remote_tip(day) != day["tip"], "a clean index must be committed and pushed"
        changes = _pushed_changes(day)
        assert f"A sessions/meta/{DATE}-real-only.md" in changes, changes
        assert f"D sessions/meta/{DATE}_090000.stub.md" in changes and "M sessions/meta/README.md" in changes, changes


def test_step_10_blocks_end_to_end_a_failed_check_publishes_nothing_and_a_clean_index_commits_meta():
    """The same run for Step 10's blocks (the single-project flow): project staging, the META_JOURNAL block,
    and the commit block, with the meta block skipped and then run."""
    skill = _read_doc(_SKILL)
    blocks = ["\n".join(b) for b in _fenced_blocks(_section(skill, r"^## Step 10 — .*$"))]
    project_stage = next(b for b in blocks if "add -u sessions/<project>/" in b)
    meta_stage = next(b for b in blocks if "add -u sessions/meta/" in b)
    commit = next(b for b in blocks if re.search(r'git -C "\$WT" commit', b))
    projects = ("project-a",)
    with compose_day(projects) as day:
        assert _run_block(_fill(project_stage, day, projects), day)[0] == 0
        rc, out, err = _run_block(_fill(commit, day, projects), day)  # meta block skipped
        assert rc != 0 and "CHECK_STAGED=unstaged" in out, (rc, out, err)
        assert _remote_tip(day) == day["tip"], "nothing may be pushed past a failed check-staged"
        assert _run_block(_fill(meta_stage, day, projects), day)[0] == 0
        rc, out, err = _run_block(_fill(commit, day, projects), day)
        assert rc == 0, (out, err)
        assert _remote_tip(day) != day["tip"]
        changes = _pushed_changes(day)
        assert f"A sessions/meta/{DATE}-real-only.md" in changes and f"D sessions/meta/{DATE}_090000.stub.md" in changes, changes


def test_meta_staging_lines_are_real_commands_not_comments():
    """PR #1126 blocking finding 3: the staging of the meta journal existed only as commented lines."""
    skill = _read_doc(_SKILL)
    command = r'^\s*git -C "\$WT" add[^\n]*sessions/meta/'
    step_10 = _section(skill, r"^## Step 10 — .*$")
    assert re.search(command + r"YYYY-MM-DD-<meta-slug>\.md", step_10, re.MULTILINE), (
        "Step 10 must contain an executable `git add` of the meta journal"
    )
    assert re.search(r'^\s*git -C "\$WT" add -u[^\n]*sessions/meta/', step_10, re.MULTILINE), (
        "Step 10 must stage the deletions of meta's real stubs"
    )
    phase_2 = _section(skill, r"^### Phase 2 — .*$")
    assert re.search(command + r"YYYY-MM-DD-<meta-slug>\.md", phase_2, re.MULTILINE), (
        "Phase 2's combined commit must contain an executable `git add` of the meta journal"
    )


def test_both_step_10_5_replay_pathspec_lists_name_sessions_meta():
    skill = _read_doc(_SKILL)
    # A call is its first line plus every backslash-continued line after it.
    calls = re.findall(r"journal-compose-replay\.sh \"\$WT\" \"\$PREV\"(?:[^\n]*\\\n)*[^\n]*", skill)
    assert len(calls) == 2, f"expected the single-project and multi-project replay calls, found {len(calls)}"
    for call in calls:
        assert call.count("\n") >= 1, f"the pathspec continuation line was not captured:\n{call}"
        assert "sessions/meta/" in call, f"a Step 10.5 pathspec list omits sessions/meta/:\n{call}"


def test_routine_states_the_unattended_meta_rule_in_its_own_words():
    """PR #1126 review: the old gate still passed with the whole rule deleted -- 'Step 6.7' and
    'Meta journal:' also appear in steps 5-6, and 'Never prompt the user.' pre-dates the change."""
    routine = _read_doc(_ROUTINE)
    constraints = routine.split("**Constraints:**", 1)[-1]
    assert len(constraints.splitlines()) > 5, "Constraints extraction came back empty"
    for unique in (
        "Meta triggers are never asked about, declined, or deferred",
        "do not create a `YYYY-MM-DD_draft.md`",
        "Late meta entry recovery",
    ):
        assert unique in re.sub(r"\s+", " ", constraints), f"the routine's constraint bullet lost: {unique!r}"
    # Steps 5 and 6 each carry their own instruction; the constraint bullet also says "Meta journal:",
    # so a whole-file substring check cannot tell whether either step still does (second review, NB 4).
    steps = routine.split("**Constraints:**", 1)[0]
    step_5 = re.search(r"^5\. .*?(?=^6\. )", steps, re.MULTILINE | re.DOTALL)
    step_6 = re.search(r"^6\. .*", steps, re.MULTILINE | re.DOTALL)
    assert step_5 and step_6, "routine steps 5 and 6 were not extracted"
    assert "Step 6.7" in re.sub(r"\s+", " ", step_5.group(0)), "step 5 must say the skill composes meta (its Step 6.7)"
    assert "`Meta journal:` status" in re.sub(r"\s+", " ", step_6.group(0)), (
        "step 6 must tell the routine to report the PR body's Meta journal: status line"
    )


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
