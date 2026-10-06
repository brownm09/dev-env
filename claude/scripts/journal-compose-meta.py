#!/usr/bin/env python3
"""Mechanical half of ``/journal-compose`` Step 6.7: meta triggers are composed, not asked about.

dev-env #52 and #892; ADR-082 Addendum (2026-10-02); ADR-129 Amendment 2.

Step 2b used to ask "open a meta draft block? (y/n)", which nobody can answer in a scheduled run,
and its "yes" branch wrote ``sessions/meta/YYYY-MM-DD_draft.md`` -- a legacy draft that nothing
composes, so the answer was lost whether or not meta was composed that day. The fix composes meta
in the same run from *derived stubs*. This script is the part of that pass that must not be left to
an LLM or to the Write tool:

* The harness refuses the coordinator's Write/Edit into the isolated compose worktree (#1119), and a
  Bash write would have the coordinator author stub and manifest content by hand (the
  ``pre-tool-use-journal-shell-write-guard.py`` hook blocks most such redirects; one quoting form is
  a known gap, #1127). A derived stub is generated from records this script has verified, so a
  Python process launched by Bash with no inline literal is the writer (ADR-129 Amendment 2).
* A Phase 1 subagent's trigger report is a claim. ``stub`` checks each record against the stub it
  cites before anything is written, and names every rejection -- nothing is dropped silently.
* The coordinator's composed journal is a claim too. ``install`` gates it before it is copied into
  the worktree; ``check-clean`` and ``check-staged`` prove that nothing for the date was left behind
  in the working tree and that the commit will contain what the working tree does.

Subcommands (``<WT>`` is the compose worktree root: an absolute path, and never a primary checkout;
``<DATE>`` is YYYY-MM-DD)::

    stub         <WT> <DATE> <records.jsonl>
    install      <WT> <DATE> <staged.md> <slug>
    abandon      <WT> <DATE>
    check-clean  <WT> <DATE>
    check-staged <WT> <DATE>

``stub`` reads records ``{"project", "type", "stub", "reason", "evidence"}`` -- one JSON object per
line (JSON Lines), or a JSON list, or an object with a ``records`` list -- verifies each, and writes
one *derived stub* per trigger category into ``<WT>/sessions/meta/`` as ``DATE_2359NN.stub.md`` plus
a manifest shard. A line that is not valid JSON is a named rejection, never a failed batch. Derived
files are untracked, never pushed, and consumed by Step 9's ordinary deletion globs in the same run.
``install`` copies the staged composed journal to ``<WT>/sessions/meta/DATE-<slug>.md`` once it has
all eleven required headings **and**, for every derived stub, its own ``## Session N -- <that
stub's category label>`` section (one session per category) citing each of the stub's ``Source:``
paths, and each session belongs to the category whose label its title *begins* with. It replaces a
journal this run installed earlier (untracked in the worktree, which is created fresh from the draft
branch), so Step 6.7's "expand the staged file and re-run" remedy works; it never replaces one the
draft branch already carries (tracked) -- it prints ``META_JOURNAL_EXISTS=<path>`` and exits 1 -- and
when git cannot say which it is, it prints ``META_INSTALL_UNVERIFIED=<why>`` (a different cause, a
different key) and exits 1. ``abandon`` removes derived files and only derived
files. ``check-clean`` fails if any stub, manifest, ``_draft.md`` or temp file for the date remains
in any ``sessions/<project>/`` directory. ``check-staged`` fails if ``git status`` shows anything
unstaged or untracked (apart from compose lock files), so the commit Step 10 builds from the index
is the commit the working tree describes.

Exit 0 -- ok. Exit 1 -- usage or precondition error, or a write failure (a failed ``stub`` leaves
either the earlier derived set untouched or no derived files at all -- never a mixture, apart from
a file that stayed locked, which is named). Exit 2 --
verification failure (``stub``: records arrived and none was accepted; ``install``: the journal
was refused; ``check-clean`` / ``check-staged``: something was left behind). Reports go to stdout
as ``KEY=value`` lines, errors to stderr prefixed ``[journal-compose-meta]``.

Calibration (ADR-144). Two checks classify stubs the tests do not enumerate.

* ``MIN_EVIDENCE_WORDS = 3``: evidence is a claim that a phrase is in a stub, and a one- or two-word
  phrase ("e", "PR", "the gap") occurs in almost any stub, so it verifies a fabricated record.
  Known-good: the six real evidence phrases from the 2026-10-01 dry run are 7 to 12 words (the worst
  case is 7, a margin of 4 words). Known-bad: "e", "PR", ".", "-" (1 word) and "the gap" (2 words),
  all rejected.
* The opening-brief block (``opening_brief_span``): evidence quoted from the previous day's context
  is rejected. Measured on all 750 stubs ever committed to engineering-journal (751 when a reviewer
  re-ran it later that day; the same result): 65 carry an opening brief (62 behind the
  ``<!-- opening-brief`` marker, 3 that omit it and begin "Opening brief") and the rule finds 65/65;
  the other 685 (683 with only blank or comment lines above the first heading, 2 with
  ``**PR:**``/``**Issue:**`` metadata lines that a blanket "anything above the first heading" rule
  would have wrongly rejected) get no block, 0/685. The rule searches outside the block first,
  because session bodies repeat the brief's phrases; ``docs/TESTING.md`` item 100 has the per-stub
  measurement.

Everything else keys on a literal token this pass generated or that the skill already requires (the
eleven section headings, the ``Source:`` paths, the category labels); ``FIDELITY`` is reported but
never gates.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import unicodedata

import _winsubp  # noqa: F401  -- suppress console windows on Windows
from _journal_schema import malformed_manifest_fields, missing_required_fields

LOG_PREFIX = "[journal-compose-meta]"

# Step 2b's trigger table, in table order. The slug is what a Phase 1 subagent reports and the
# label is the table's wording. Index N names the derived stub ``DATE_2359NN`` so names are stable
# across re-runs and sort after every real stub.
TRIGGER_TYPES = (
    ("claude-md", "CLAUDE.md modified"),
    ("platform-constraint", "New platform constraint"),
    ("workflow-failure", "Workflow failure remediated"),
    ("convention", "Cross-project convention established"),
    ("dev-env-pr", "dev-env PR merged"),
    ("journal-structure", "Journal structure changed"),
    ("canonical-reference", "New canonical reference identified"),
)
_SLUG_TO_INDEX = {slug: index for index, (slug, _label) in enumerate(TRIGGER_TYPES)}

# First line of every derived stub. Identifies a file this script owns, so ``stub`` can replace its
# own previous output and ``abandon`` can never delete a real stub.
DERIVED_MARKER = "<!-- derived-meta:"
TOKENS_COMMENT = "<!-- tokens: input=0 output=0 cost≈$0 -->"
DERIVED_TOPIC_PREFIX = "Derived meta ("

# U+FEFF, built rather than typed: an editor or pipeline that strips an invisible literal would
# turn the ``lstrip`` below into a silent no-op.
BOM = chr(0xFEFF)

# A phrase shorter than this verifies almost any record (see the calibration note above).
MIN_EVIDENCE_WORDS = 3

# The eleven required headings: the skill's ``chk()`` lines, verbatim (Step 6.5 and the Phase 1
# template's Step 6.6). A test keeps all three copies identical.
REQUIRED_HEADINGS = (
    (r"^# Session Transcript — ", "header"),
    (r"^- \[Opening Brief\]\(#opening-brief\)", "TOC"),
    (r"^## Opening Brief$", "Opening Brief"),
    (r"^## Key Decisions$", "Key Decisions"),
    (r"^## Session [0-9]+ — ", "Session dialogue H2"),
    (r"^## Open Items / Next Steps$", "Open Items / Next Steps"),
    (r"^## Token Usage$", "Token Usage"),
    (r"^## Token Optimization Suggestions$", "Token Optimization Suggestions"),
    (r"^## Next Session Context$", "Next Session Context"),
    (r"^## Reflection$", "Reflection"),
    (r"^## Further Reading$", "Further Reading"),
)

_SOURCE_RE = re.compile(r"^- Source: `([^`]+)`[ \t]*$", re.MULTILINE)
_PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
_BOUNDARY_RE = re.compile(r"^(?:#{1,6}\s|<!--)")
_H2_RE = re.compile(r"^##\s+(.*\S)\s*$")
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_BRIEF_START_RE = re.compile(r"^\s*(?:<!--\s*opening-brief\b|opening brief\b)", re.IGNORECASE)
_BRIEF_END_RE = re.compile(r"^\s*<!--\s*/opening-brief\b", re.IGNORECASE)
_SESSION_TITLE_RE = re.compile(r"^## Session [0-9]+ — (.*)$")
_SESSION_H2_RE = re.compile(r"^## Session [0-9]+ — ")
_DERIVED_LABEL_RE = re.compile(r"^## (.+?) — detected in ", re.MULTILINE)

EXCERPT_CONTEXT = 2          # lines of context either side of the evidence line (formatting only)
TITLE_LIMIT = 120            # H3 title length (formatting only)
SHOWN_LIMIT = 80             # a field echoed in a rejection line (formatting only)
REASON_LIMIT = 100           # a record's reason echoed in a rejection line (formatting only)

_NOT_JSON = object()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def normalize(text):
    """NFKC, then collapse every whitespace run to one space -- how evidence is compared."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def clip(value, limit, tail=False):
    """Collapse whitespace and shorten to ``limit`` characters, keeping the head (or the tail)."""
    flat = " ".join(value.split())
    if len(flat) <= limit:
        return flat
    if tail:
        return "…" + flat[-(limit - 1):]
    return flat[: limit - 1].rstrip() + "…"


def _label_key(text):
    return normalize(text.replace("`", "")).casefold()


def _title_key(text):
    """A session title compared the way labels are (NFKC, backticks dropped, whitespace collapsed, case
    folded), with leading emphasis markers (``**``) ignored too.

    The Step 2b table writes two labels with backticks (`` `CLAUDE.md` modified``, `` `dev-env` PR
    merged``), a coordinator may capitalize or bold a title, and ``resolve_type`` already treats all of
    those as the same label - the install gate must not be the one place that does not.
    """
    return _label_key(text).lstrip("*_ ")


_LABEL_TO_SLUG = {_label_key(label): slug for slug, label in TRIGGER_TYPES}


def resolve_type(raw):
    """Map a reported type -- a slug in any case with ``_`` or ``-``, or the table's label -- to a slug."""
    slug = raw.strip().lower().replace("_", "-")
    if slug in _SLUG_TO_INDEX:
        return slug
    return _LABEL_TO_SLUG.get(_label_key(raw))


def read_utf8(path):
    """Return ``(text, error)``; exactly one is None. LF-normalized, BOM stripped. Never raises."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, str(exc)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, f"not valid UTF-8: {exc}"
    return text.replace("\r\n", "\n").replace("\r", "\n"), None


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _stage_text(path, text):
    """Write ``text`` to a temp file beside ``path`` and return the temp path; ``path`` is untouched.

    UTF-8, LF, no BOM. A failed write removes its own temp file before re-raising.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError:
        _remove_quietly(temp)
        raise
    return temp


def write_text_atomic(path, text):
    """``_stage_text`` plus ``os.replace``; no temp file survives a failure."""
    temp = _stage_text(path, text)
    try:
        os.replace(temp, path)
    except OSError:
        _remove_quietly(temp)
        raise


def valid_date(value):
    """True only for a real YYYY-MM-DD date -- so an unsubstituted placeholder fails loudly."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def rel(wt, path):
    return os.path.relpath(path, wt).replace(os.sep, "/")


def _inside(root, path):
    try:
        base = os.path.realpath(root)
        return os.path.commonpath([base, os.path.realpath(path)]) == base
    except ValueError:
        return False


def _meta_dir(wt):
    return os.path.join(wt, "sessions", "meta")


def _fail(message):
    sys.stderr.write(f"{LOG_PREFIX} {message}\n")
    return 1


def _emit(lines):
    for line in lines:
        print(line)


def _utf8_stdio():
    """Keep ``print`` byte-stable: UTF-8 and bare LF, whatever the console codepage is."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace", newline="\n")
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# Derived-stub bookkeeping
# ---------------------------------------------------------------------------

def is_derived_stub(path):
    """True when the file's first line is the derived marker."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(256)
    except OSError:
        return False
    return head.decode("utf-8", errors="replace").lstrip(BOM).startswith(DERIVED_MARKER)


def list_meta_stubs(wt, date):
    """``(real, derived)``: sorted full paths of ``sessions/meta/DATE_*.stub.md``, split by marker."""
    real, derived = [], []
    meta_dir = _meta_dir(wt)
    try:
        names = sorted(os.listdir(meta_dir))
    except OSError:
        return real, derived
    for name in names:
        if name.startswith(f"{date}_") and name.endswith(".stub.md"):
            path = os.path.join(meta_dir, name)
            (derived if is_derived_stub(path) else real).append(path)
    return real, derived


def _is_derived_manifest(path):
    text, _error = read_utf8(path)
    if text is None:
        return False
    try:
        entry = json.loads(text.strip().splitlines()[0])
    except (ValueError, IndexError):
        return False
    topic = entry.get("topic") if isinstance(entry, dict) else None
    return isinstance(topic, str) and topic.startswith(DERIVED_TOPIC_PREFIX)


def real_manifest_stub_names(wt, date):
    """Stub names implied by *real* manifest shards for ``date`` -- orphans included.

    A real manifest with no stub beside it still owns its name: ``stub`` must not overwrite it.
    """
    names = set()
    meta_dir = _meta_dir(wt)
    try:
        listing = sorted(os.listdir(meta_dir))
    except OSError:
        return names
    for name in listing:
        if name.startswith(f"{date}_") and name.endswith(".manifest.jsonl"):
            if not _is_derived_manifest(os.path.join(meta_dir, name)):
                names.add(name[: -len(".manifest.jsonl")] + ".stub.md")
    return names


def remove_derived(wt, date, include_temps=True):
    """Delete derived stubs, their manifest shards, and orphaned derived manifests for ``date``.

    Marker-verified: a real stub or manifest is never touched. ``include_temps`` also removes the
    ``*.tmp-<pid>`` files a failed write can leave (``stub`` passes False: it stages new temp files
    before it removes the old set). Never raises: returns ``(removed, failed)`` -- the relative
    paths deleted, and ``(path, reason)`` for each file that could not be (a lock held by an
    antivirus scanner or the indexer), so the caller reports it instead of dying with a traceback.
    """
    removed, failed = [], []

    def drop(path):
        try:
            os.remove(path)
        except OSError as exc:
            failed.append((rel(wt, path), str(exc)))
        else:
            removed.append(rel(wt, path))

    _real, derived = list_meta_stubs(wt, date)
    for stub in derived:
        pair = stub[: -len(".stub.md")] + ".manifest.jsonl"
        for path in (stub, pair):
            if os.path.isfile(path):
                drop(path)
    meta_dir = _meta_dir(wt)
    try:
        names = sorted(os.listdir(meta_dir))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(meta_dir, name)
        if name.startswith(f"{date}_") and name.endswith(".manifest.jsonl") and _is_derived_manifest(path):
            drop(path)
        elif include_temps and name.startswith((f"{date}_", f"{date}-")) and ".tmp-" in name:
            drop(path)
    return removed, failed


def allocate_names(date, type_indexes, taken):
    """Map each type index to a free ``DATE_2359NN.stub.md`` name.

    NN starts at the type's table index and is bumped past ``taken`` (names owned by real stubs
    and real manifests) and past names already handed out, so nothing real is ever shadowed.
    Raises ValueError when the minute is exhausted.
    """
    used = set()
    names = {}
    for index in sorted(type_indexes):
        seconds = index
        while seconds <= 59 and (f"{date}_2359{seconds:02d}.stub.md" in taken or seconds in used):
            seconds += 1
        if seconds > 59:
            raise ValueError(f"no free derived stub name left for type index {index}")
        used.add(seconds)
        names[index] = f"{date}_2359{seconds:02d}.stub.md"
    return names


# ---------------------------------------------------------------------------
# Record verification
# ---------------------------------------------------------------------------

def find_evidence(lines, needle, skip=None):
    """Index of the first line whose normalized text contains ``needle`` (or, failing that, whose
    join with the next line does -- evidence copied across a wrapped line). None if absent.

    ``skip`` is a half-open ``(start, end)`` range of lines that may not hold or begin a match.
    """
    if not needle:
        return None
    normalized = [normalize(line) for line in lines]

    def skipped(position):
        return skip is not None and skip[0] <= position < skip[1]

    for index, line in enumerate(normalized):
        if not skipped(index) and needle in line:
            return index
    for index in range(len(normalized) - 1):
        if skipped(index) or skipped(index + 1):
            continue
        if needle in f"{normalized[index]} {normalized[index + 1]}":
            return index
    return None


def opening_brief_span(lines):
    """Half-open ``(start, end)`` of the stub's opening-brief block, or None.

    The block starts at the ``<!-- opening-brief`` marker (or, in the three stubs that omit the
    marker, at a line that begins "Opening brief") and runs to the stub's first Markdown heading, or
    to a ``<!-- /opening-brief -->`` close marker when the stub has one. It carries the previous
    day's Next Session Context, not this session's work, and Step 2b scans session blocks only. Only
    a start line *above* the first heading counts: a session body that mentions "the opening brief"
    must not open a block of its own, and a stub with no brief (the scheduled routines' `### Session:`
    stubs, or the `**PR:**` metadata lines two lifting-logbook stubs carry above their heading) has
    no span at all.
    """
    first = first_heading_index(lines)
    limit = len(lines) if first is None else first
    for position in range(limit):
        if _BRIEF_START_RE.match(lines[position]):
            end = limit
            for later in range(position + 1, limit):
                if _BRIEF_END_RE.match(lines[later]):
                    end = later + 1
                    break
            return position, end
    return None


def locate_evidence(lines, needle):
    """``(status, index)``: ``("ok", i)`` for the first match outside the opening brief,
    ``("brief", None)`` when the phrase occurs only inside it, ``("absent", None)`` otherwise.

    Outside-first matters: a session body routinely repeats a phrase from the brief that carried
    its work forward, and a subagent that copied the phrase from the session body, as instructed,
    must not be rejected because the brief says it first.
    """
    span = opening_brief_span(lines)
    index = find_evidence(lines, needle, skip=span)
    if index is not None:
        return "ok", index
    if span is not None and find_evidence(lines, needle) is not None:
        return "brief", None
    return "absent", None


def session_heading(lines, index):
    """Text of the session heading at or above ``index``, or None.

    The nearest H2 wins (a ``### Details`` sub-heading of a ``## Session`` block must not replace
    it). A stub with no H2 at all -- the scheduled routines write ``### Session: ...`` -- falls back
    to the nearest heading of any other level.
    """
    fallback = None
    for position in range(index, -1, -1):
        match = _H2_RE.match(lines[position])
        if match:
            return match.group(1)
        if fallback is None:
            other = _ANY_HEADING_RE.match(lines[position])
            if other:
                fallback = other.group(1)
    return fallback


def first_heading_index(lines):
    """Index of the first Markdown heading of any level, or None."""
    for position, line in enumerate(lines):
        if _ANY_HEADING_RE.match(line):
            return position
    return None


def excerpt(lines, index):
    """The evidence line plus up to ``EXCERPT_CONTEXT`` lines either side.

    Never crosses a Markdown heading or an HTML comment line: the trailing ``<!-- tokens -->``,
    ``<!-- next-session-context -->`` and ``<!-- opening-brief -->`` markers must not be copied
    into a derived stub, which carries none of them by design.
    """
    start = index
    while start > max(index - EXCERPT_CONTEXT, 0) and not _BOUNDARY_RE.match(lines[start - 1]):
        start -= 1
    end = index
    last = min(index + EXCERPT_CONTEXT, len(lines) - 1)
    while end < last and not _BOUNDARY_RE.match(lines[end + 1]):
        end += 1
    chunk = list(lines[start : end + 1])
    while chunk and not chunk[0].strip():
        chunk.pop(0)
    while chunk and not chunk[-1].strip():
        chunk.pop()
    return chunk


def _shown(record, key):
    value = record.get(key)
    if isinstance(value, str) and value.strip():
        return clip(value, SHOWN_LIMIT, tail=True)
    return "?"


def _stub_basename(value, project, date, wt):
    """``(name, problem)``: the bare stub filename for a bare name, a ``sessions/<project>/`` path,
    or an absolute path that is the very file inside this worktree (Step 1's ``ls`` prints those)."""
    text = value.strip()
    path = text.replace("\\", "/")
    if os.path.isabs(text) or re.match(r"^[A-Za-z]:/", path):
        name = path.rsplit("/", 1)[-1]
        target = os.path.join(wt, "sessions", project, name)
        try:
            same = os.path.samefile(text, target)
        except OSError:
            same = False
        if not same:
            return None, f"absolute stub path is not a file under sessions/{project}/ of this worktree"
    elif "/" in path:
        prefix, _sep, name = path.rpartition("/")
        if prefix != f"sessions/{project}":
            return None, f"stub path must be the bare filename or sessions/{project}/<filename>"
    else:
        name = path
    if not re.fullmatch(re.escape(date) + r"_\d{6}\.stub\.md", name):
        return None, f"stub name must be {date}_######.stub.md for the compose date"
    return name, None


def validate_record(wt, date, record, listing):
    """Check one trigger record against the stub it cites.

    ``listing`` is ``os.listdir(<WT>/sessions)``: project names are matched against it exactly,
    because the filesystem is case-insensitive here and ``Dev-Env`` would otherwise be accepted.
    Returns ``(accepted, None)`` or ``(None, (project, stub, type, why, reason))``; the rejection
    carries whatever the record had so the report can name it and preserve its reason.
    """
    if not isinstance(record, dict):
        return None, ("?", "?", "?", "record is not a JSON object", "")
    shown = (_shown(record, "project"), _shown(record, "stub"), _shown(record, "type"))
    raw_reason = record.get("reason")
    reason_shown = clip(raw_reason, REASON_LIMIT) if isinstance(raw_reason, str) else ""

    def reject(why):
        return None, shown + (why, reason_shown)

    fields = {}
    for key in ("project", "type", "stub", "reason", "evidence"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            return reject(f"missing or empty field: {key}")
        fields[key] = value.strip()

    project = fields["project"]
    if project.rstrip(". ").casefold() == "meta":
        return reject("project 'meta' is skipped: meta triggers are never reported for meta")
    if not _PROJECT_RE.fullmatch(project) or project.endswith((".", " ")):
        return reject(f"unsafe project name {project!r}")
    if project not in listing:
        return reject(f"no sessions/{project}/ directory in the compose worktree (names are case-sensitive)")

    slug = resolve_type(fields["type"])
    if slug is None:
        known = ", ".join(name for name, _label in TRIGGER_TYPES)
        return reject(f"unknown type {fields['type']!r} (expected one of: {known})")

    stub_name, problem = _stub_basename(fields["stub"], project, date, wt)
    if problem:
        return reject(problem)
    stub_path = os.path.join(wt, "sessions", project, stub_name)
    if not _inside(wt, stub_path):
        return reject("stub path escapes the compose worktree")
    if not os.path.isfile(stub_path):
        return reject(f"stub not found: sessions/{project}/{stub_name}")
    text, error = read_utf8(stub_path)
    if text is None:
        return reject(f"stub unreadable: {error}")

    needle = normalize(fields["evidence"])
    if len(needle.split()) < MIN_EVIDENCE_WORDS:
        return reject(
            f"evidence too short: copy a phrase of at least {MIN_EVIDENCE_WORDS} words "
            "from ONE line of the stub"
        )
    lines = text.split("\n")
    status, index = locate_evidence(lines, needle)
    if status == "absent":
        return reject(
            "evidence not found in the cited stub (copy a short phrase from ONE line, "
            "character for character)"
        )
    if status == "brief":
        return reject(
            "evidence appears only in the stub's opening brief (it carries the previous day's "
            "context, not this session's work): copy a phrase from a session block"
        )
    return {
        "project": project,
        "type_index": _SLUG_TO_INDEX[slug],
        "stub": stub_name,
        "reason": " ".join(fields["reason"].split()),
        "heading": session_heading(lines, index),
        "excerpt": excerpt(lines, index),
        "line_index": index,
    }, None


# ---------------------------------------------------------------------------
# Derived stub construction
# ---------------------------------------------------------------------------

def build_derived_stub(date, label, items):
    """Markdown for one derived stub: one H2, an H3 per record, evidence quoted with ``> ``."""
    projects = sorted({item["project"] for item in items})
    lines = [
        f"{DERIVED_MARKER} generated by journal-compose for {date} from trigger records; "
        "compose-internal, not a session. Quoted lines are copied verbatim from the cited source "
        "stubs. -->",
        "",
        f"## {label} — detected in {', '.join(projects)} ({date})",
        "",
        f"{len(items)} trigger(s) reported by the per-project composers; each evidence phrase "
        "was found in the stub it cites.",
        "",
    ]
    for item in items:
        heading = item["heading"] or "(no session heading found)"
        title = f"{item['project']} — {heading}"
        if len(title) > TITLE_LIMIT:
            title = title[: TITLE_LIMIT - 1].rstrip() + "…"
        lines += [
            f"### {title}",
            "",
            f"- Source: `sessions/{item['project']}/{item['stub']}`",
            f"- Source session: {heading}",
            f"- Why meta-relevant: {item['reason']}",
            f"- Trigger: {label}",
            "",
        ]
        lines += [f"> {line.rstrip()}" if line.strip() else ">" for line in item["excerpt"]]
        lines.append("")
    lines.append(TOKENS_COMMENT)
    return "\n".join(lines) + "\n"


def missing_headings(text):
    """Labels of the required headings absent from ``text`` (LF-normalized)."""
    return [label for pattern, label in REQUIRED_HEADINGS if not re.search(pattern, text, re.MULTILINE)]


def session_title(heading):
    """The title after ``## Session N — `` in a heading line (empty when it is not a session heading)."""
    match = _SESSION_TITLE_RE.match(heading)
    return match.group(1).strip() if match else ""


def session_sections(text):
    """``[(heading_line, section_text)]`` for each ``## Session N — …`` H2, running to the next H2."""
    sections = []
    heading, body = None, []
    for line in text.split("\n"):
        if line.startswith("## "):
            if heading is not None:
                sections.append((heading, "\n".join(body)))
            heading = line if _SESSION_H2_RE.match(line) else None
            body = [line]
        elif heading is not None:
            body.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(body)))
    return sections


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _load_records(path, date):
    """``(records, rejections, error)``: parsed records plus per-line rejections, or an error.

    The file is parsed whole first (a list, or an object with a ``records`` list, or a single
    record). If that fails it is read as JSON Lines, where a malformed line becomes a named
    rejection instead of failing the batch -- one stray ``\\U`` in a copied Windows path must not
    cost the valid records beside it.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, [], str(exc)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, [], f"not valid UTF-8: {exc}"
    try:
        data = json.loads(text)
    except ValueError:
        data = _NOT_JSON
    if data is _NOT_JSON:
        records, rejections = [], []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError as exc:
                rejections.append(("?", "?", "?", f"line {number} is not valid JSON ({exc})", ""))
        return records, rejections, None
    if isinstance(data, dict):
        if "records" in data:
            if data.get("date") not in (None, date):
                return None, [], f"records file is for {data.get('date')!r}, not {date}"
            data = data["records"]
        else:
            data = [data]
    if not isinstance(data, list):
        return None, [], "expected JSON Lines, a JSON list, or an object with a 'records' list"
    return data, [], None


def cmd_stub(wt, date, records_path):
    records, load_rejections, error = _load_records(records_path, date)
    if records is None:
        return _fail(f"cannot read records {records_path!r}: {error}")
    rejected = list(load_rejections)

    listing = os.listdir(os.path.join(wt, "sessions"))
    items, deduped = {}, 0
    for record in records:
        item, rejection = validate_record(wt, date, record, listing)
        if rejection is not None:
            rejected.append(rejection)
            continue
        # One trigger per (project, stub, category, evidence line): a retry that quotes the same
        # change differently is a duplicate, and the latest attempt wins.
        key = (item["project"], item["stub"], item["type_index"], item["line_index"])
        if key in items:
            deduped += 1
        items[key] = item
    accepted = list(items.values())
    received = len(records) + len(load_rejections)

    report = [
        f"META_RECORDS_RECEIVED={received}",
        f"META_TRIGGERS_ACCEPTED={len(accepted)}",
        f"META_TRIGGERS_REJECTED={len(rejected)}",
        f"META_TRIGGERS_DEDUPED={deduped}",
    ]
    for proj, stub, kind, why, reason in rejected:
        line = f"META_TRIGGER_REJECTED {proj} {stub} {kind} -- {why}"
        report.append(f"{line} | reason: {reason}" if reason else line)
    if not accepted:
        # "rejected" is deliberately not "none": the PR body uses none for "no meta triggers",
        # and a day on which every record was refused must not be relayed as having had none.
        report.append("META_STATUS=rejected" if received else "META_STATUS=none")
        _emit(report)
        return 2 if received else 0

    real, _derived_old = list_meta_stubs(wt, date)
    by_type = {}
    for item in accepted:
        by_type.setdefault(item["type_index"], []).append(item)
    taken = {os.path.basename(path) for path in real} | real_manifest_stub_names(wt, date)
    try:
        names = allocate_names(date, by_type, taken)
    except ValueError as exc:
        return _fail(str(exc))

    planned = []
    for index in sorted(by_type):
        group = sorted(by_type[index], key=lambda entry: (entry["project"], entry["stub"], entry["line_index"]))
        label = TRIGGER_TYPES[index][1]
        projects = sorted({entry["project"] for entry in group})
        manifest = {
            "stub": f"sessions/meta/{names[index]}",
            "topic": f"{DERIVED_TOPIC_PREFIX}{label}): {', '.join(projects)}",
            "tokens": {"input": 0, "output": 0, "cost": 0},
            "prs_opened": [],
            "prs_closed": [],
        }
        problems = missing_required_fields(manifest) + malformed_manifest_fields(manifest)
        if problems:
            return _fail("derived manifest would be invalid: " + "; ".join(problems))
        planned.append((names[index], build_derived_stub(date, label, group), json.dumps(manifest) + "\n"))

    # Stage every new file under a temp name first, so a failed write (an antivirus lock, a full
    # drive) leaves the earlier derived set untouched; only then swap the sets. A failure while
    # swapping cannot restore the earlier set (the swap deletes it first), so it clears every
    # derived file instead: the caller always ends with the whole new set or with none of it, never
    # a mixture that Step 9 would half-consume.
    meta_dir = _meta_dir(wt)
    staged = []
    try:
        for stub_name, stub_text, manifest_text in planned:
            final_stub = os.path.join(meta_dir, stub_name)
            final_manifest = os.path.join(meta_dir, stub_name[: -len(".stub.md")] + ".manifest.jsonl")
            staged.append((_stage_text(final_stub, stub_text), final_stub))
            staged.append((_stage_text(final_manifest, manifest_text), final_manifest))
    except OSError as exc:
        for temp, _final in staged:
            _remove_quietly(temp)
        return _fail(f"could not write the derived stubs ({exc}); nothing was changed")
    try:
        _removed, stuck = remove_derived(wt, date, include_temps=False)
        if stuck:
            raise OSError(f"could not remove the earlier {stuck[0][0]}: {stuck[0][1]}")
        for temp, final in staged:
            os.replace(temp, final)
    except OSError as exc:
        for temp, _final in staged:
            _remove_quietly(temp)
        _removed, stuck = remove_derived(wt, date)
        if stuck:
            names = ", ".join(path for path, _reason in stuck)
            return _fail(f"could not place the derived stubs ({exc}); could not remove {names} -- run 'abandon'")
        return _fail(f"could not place the derived stubs ({exc}); no derived files remain")

    total = 0
    for stub_name, stub_text, _manifest_text in planned:
        report.append(f"META_STUB=sessions/meta/{stub_name}")
        total += len(stub_text.splitlines())
    report += [f"META_DERIVED_LINES={total}", "META_STATUS=derived"]
    _emit(report)
    return 0


def _derived_expectations(wt, derived):
    """``([(label, [sources])], error)`` for the derived stubs; an error refuses a vacuous check."""
    expectations = []
    for path in derived:
        stub_text, error = read_utf8(path)
        if stub_text is None:
            return None, f"derived stub {rel(wt, path)} unreadable: {error}"
        label_match = _DERIVED_LABEL_RE.search(stub_text)
        if not label_match:
            return None, f"derived stub {rel(wt, path)} has no recognizable category heading"
        sources = list(dict.fromkeys(_SOURCE_RE.findall(stub_text)))
        if not sources:
            return None, (
                f"derived stub {rel(wt, path)} has no extractable 'Source:' line -- refusing a "
                "citation check that would pass vacuously"
            )
        expectations.append((label_match.group(1), sources))
    return expectations, None


def _tracked_meta_files(wt):
    """``(names, problem)``: the file names git tracks directly in ``<WT>/sessions/meta``.

    ``names`` is None when git cannot say -- ``wt`` has no ``.git`` file (it is not a linked
    worktree, so an enclosing repository must not answer for it), git is missing, or ``ls-files``
    fails. Callers treat that as "not provably written by this run".
    """
    if not os.path.isfile(os.path.join(wt, ".git")):
        return None, "not a linked git worktree"
    try:
        proc = subprocess.run(
            ["git", "-C", wt, "ls-files", "-z", "--", "sessions/meta"], capture_output=True, check=False
        )
    except OSError as exc:
        return None, f"cannot run git: {exc}"
    if proc.returncode != 0:
        return None, "git ls-files failed: " + proc.stderr.decode("utf-8", errors="replace").strip()
    prefix = "sessions/meta/"
    names = set()
    for token in proc.stdout.decode("utf-8", errors="replace").split("\0"):
        if token.startswith(prefix) and "/" not in token[len(prefix):]:
            names.add(token[len(prefix):])
    return names, None


def cmd_install(wt, date, staged, slug):
    if not _SLUG_RE.fullmatch(slug):
        return _fail(f"slug {slug!r} must match [a-z0-9][a-z0-9-]* (at most 80 characters)")
    text, error = read_utf8(staged)
    if text is None:
        return _fail(f"staged journal {staged!r} unreadable: {error}")
    if not text.strip():
        return _fail(f"staged journal {staged!r} is empty")

    real, derived = list_meta_stubs(wt, date)
    if not real and not derived:
        return _fail(f"no sessions/meta/{date}_*.stub.md in the worktree: nothing for this journal to compose")
    expectations, problem = _derived_expectations(wt, derived)
    if expectations is None:
        return _fail(problem)

    missing = missing_headings(text)
    sections = session_sections(text)
    missing_sessions, uncited = [], []
    pairs_total = pairs_cited = 0
    claimed = set()
    for label, sources in expectations:
        pairs_total += len(sources)
        # One session per derived stub, and a session belongs to the category whose label its title
        # BEGINS with (Step 6.7's contract: `## Session N — <label>`, a subtitle may follow). Matching
        # the label anywhere in the title let a subtitle that merely names another category ("dev-env
        # PR merged: the PR that left CLAUDE.md modified") capture that category's session and send the
        # coordinator to fix the wrong one. A real meta session whose title also begins with the label
        # can still precede the derived one, so each category claims the first unclaimed session that
        # carries its label AND cites all of its sources; failing that, the one that cites the most is
        # blamed, so the report names what is actually missing.
        label_key = _label_key(label)
        candidates = [
            i for i, (heading, _body) in enumerate(sections)
            if _title_key(session_title(heading)).startswith(label_key) and i not in claimed
        ]
        if not candidates:
            missing_sessions.append(label)
            continue
        cited_in = {i: [source for source in sources if source in sections[i][1]] for i in candidates}
        complete = [i for i in candidates if len(cited_in[i]) == len(sources)]
        chosen = complete[0] if complete else max(candidates, key=lambda i: len(cited_in[i]))
        claimed.add(chosen)
        pairs_cited += len(cited_in[chosen])
        uncited += [(label, source) for source in sources if source not in cited_in[chosen]]
    if missing or missing_sessions or uncited:
        report = ["INSTALL_REFUSED"]
        if missing:
            report.append("STRUCTURE=missing:" + ",".join(missing))
        if missing_sessions:
            report.append("SESSIONS_MISSING=" + ",".join(missing_sessions))
        for label, source in uncited:
            report.append(f"SOURCES_UNCITED {label} -- {source}")
        _emit(report)
        return 2

    meta_dir = _meta_dir(wt)
    target_name = f"{date}-{slug}.md"
    target = os.path.join(meta_dir, target_name)
    final_text = text if text.endswith("\n") else text + "\n"
    try:
        existing = sorted(
            name for name in os.listdir(meta_dir) if name.startswith(f"{date}-") and name.endswith(".md")
        )
    except OSError:
        existing = []
    # A journal the draft branch already carries (tracked), or one git cannot vouch for, was not
    # written by this run and is never replaced. An untracked one can only be this run's earlier
    # install -- the compose worktree is created fresh from the draft branch -- so it is replaced:
    # Step 6.7's fidelity remedy ("expand the staged file and re-run install") needs exactly that.
    foreign, own, unverified = [], [], None
    if existing:
        tracked, why = _tracked_meta_files(wt)
        if tracked is None:
            foreign, unverified = list(existing), clip(why or "git could not be consulted", 160)
        else:
            for name in existing:
                (foreign if name in tracked else own).append(name)
    if foreign:
        previous, _error = read_utf8(target) if existing == [target_name] else (None, None)
        if previous != final_text:
            if unverified is not None:
                # Not "already exists in the draft branch": git could not say, and reporting a guess as
                # that would make Step 6.7's failure policy stop the compose under a false cause.
                _emit([f"META_INSTALL_UNVERIFIED={unverified}"])
                return _fail(
                    f"cannot tell whether sessions/meta/{foreign[0]} was written by this run ({unverified}); "
                    "not replacing it -- fix git in the worktree and re-run install, or report it"
                )
            _emit([f"META_JOURNAL_EXISTS=sessions/meta/{foreign[0]}"])
            return _fail(
                f"a composed meta journal for {date} already exists in the draft branch "
                f"(sessions/meta/{foreign[0]}): one per date. Do not overwrite or remove it -- report "
                "it (stubs added to an already-composed day are the reconcile-late-stubs.py case)"
            )
    else:
        try:
            for name in own:
                if name != target_name:  # an earlier install of this run, under another slug
                    os.remove(os.path.join(meta_dir, name))
            write_text_atomic(target, final_text)
        except OSError as exc:
            return _fail(f"could not install sessions/meta/{target_name} ({exc}); re-run install")

    def line_count(path):
        body, _err = read_utf8(path)
        return len(body.splitlines()) if body else 0

    real_lines = sum(line_count(path) for path in real)
    source_lines = real_lines + sum(line_count(path) for path in derived)
    journal_lines = len(text.splitlines())
    _emit(
        [
            f"META_JOURNAL=sessions/meta/{target_name}",
            "STRUCTURE=ok",
            f"SOURCES_CITED={pairs_cited}/{pairs_total}" if expectations else "SOURCES_CITED=n/a",
            f"LINE_COUNT={journal_lines}",
            f"SOURCE_LINES={source_lines}",
            f"FIDELITY={journal_lines}/{source_lines}",
            f"REAL_SOURCE_LINES={real_lines}",
            f"REAL_FIDELITY={journal_lines}/{real_lines}" if real_lines else "REAL_FIDELITY=n/a",
        ]
    )
    return 0


def cmd_abandon(wt, date):
    removed, failed = remove_derived(wt, date)
    _emit(["META_ABANDONED=" + (",".join(removed) if removed else "none")])
    if failed:
        _emit(["META_ABANDON_FAILED=" + ",".join(path for path, _reason in failed)])
        return _fail("could not remove " + "; ".join(f"{path} ({reason})" for path, reason in failed))
    return 0


def cmd_check_clean(wt, date):
    sessions = os.path.join(wt, "sessions")
    leftovers = []
    for project in sorted(os.listdir(sessions)):
        project_dir = os.path.join(sessions, project)
        if not os.path.isdir(project_dir):
            continue
        for name in sorted(os.listdir(project_dir)):
            is_stub = name.startswith(f"{date}_") and name.endswith((".stub.md", ".manifest.jsonl"))
            is_temp = name.startswith((f"{date}_", f"{date}-")) and ".tmp-" in name
            if is_stub or is_temp or name in (f"{date}.manifest.jsonl", f"{date}_draft.md"):
                leftovers.append(f"sessions/{project}/{name}")
    if not leftovers:
        _emit(["CHECK_CLEAN=ok"])
        return 0
    _emit(["CHECK_CLEAN=leftover"] + [f"LEFTOVER {path}" for path in leftovers])
    return 2


# Untracked files that are expected to sit in a compose worktree and are never committed.
_EPHEMERAL = (".draft-compose.lock", ".compose-creating")


def cmd_check_staged(wt, _date):
    """Fail on any unstaged change or untracked file: the index must equal the working tree.

    ``check-clean`` reads the working tree, but the commit is built from the index. A stub deleted
    on disk but still in the index, or a composed journal that was never ``git add``-ed, passes
    ``check-clean`` and still ships the #892 shape -- real meta stubs reaching ``main`` uncomposed.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", wt, "status", "--porcelain=v1", "-z", "-uall"], capture_output=True, check=False
        )
    except OSError as exc:
        return _fail(f"cannot run git: {exc}")
    if proc.returncode != 0:
        return _fail("git status failed: " + proc.stderr.decode("utf-8", errors="replace").strip())
    tokens = proc.stdout.decode("utf-8", errors="replace").split("\0")
    unstaged = []
    position = 0
    while position < len(tokens):
        token = tokens[position]
        position += 1
        if len(token) < 4:
            continue
        status, path = token[:2], token[3:]
        if status[0] in "RC":
            position += 1  # a rename or copy entry carries its source path as the next token
        if status == "??":
            if os.path.basename(path) in _EPHEMERAL:
                continue
            unstaged.append(f"UNSTAGED ?? {path}")
        elif status[1] != " ":
            unstaged.append(f"UNSTAGED {status} {path}")
    if not unstaged:
        _emit(["CHECK_STAGED=ok"])
        return 0
    _emit(["CHECK_STAGED=unstaged"] + unstaged)
    return 2


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

USAGE = (
    "usage: py -3 journal-compose-meta.py stub <worktree> <YYYY-MM-DD> <records.jsonl>\n"
    "       py -3 journal-compose-meta.py install <worktree> <YYYY-MM-DD> <staged.md> <slug>\n"
    "       py -3 journal-compose-meta.py abandon <worktree> <YYYY-MM-DD>\n"
    "       py -3 journal-compose-meta.py check-clean <worktree> <YYYY-MM-DD>\n"
    "       py -3 journal-compose-meta.py check-staged <worktree> <YYYY-MM-DD>\n"
)

# Positional arguments each subcommand takes after its own name.
ARITY = {"stub": 3, "install": 4, "abandon": 2, "check-clean": 2, "check-staged": 2}


def main(argv):
    _utf8_stdio()
    args = argv[1:]
    if not args or args[0] not in ARITY or len(args) - 1 != ARITY[args[0]]:
        sys.stderr.write(f"{LOG_PREFIX} {USAGE}")
        return 1
    name, params = args[0], args[1:]
    raw_wt, date = params[0], params[1]
    # An empty or relative worktree would silently mean the current directory (normpath('') is '.'),
    # which could be the shared canonical checkout; Step 0.6 warns that $WT does not persist.
    if not raw_wt.strip() or not os.path.isabs(raw_wt):
        return _fail(f"worktree {raw_wt!r} must be an absolute path -- was $WT left unsubstituted?")
    wt = os.path.normpath(raw_wt)
    if not valid_date(date):
        return _fail(f"{date!r} is not a YYYY-MM-DD date")
    if os.path.isdir(os.path.join(wt, ".git")):
        return _fail(
            f"{wt!r} is a primary checkout (it has a .git directory), not a linked compose "
            "worktree -- refusing to touch it"
        )
    if not os.path.isdir(os.path.join(wt, "sessions")):
        return _fail(f"no sessions/ directory under {wt!r} -- is that a compose worktree?")
    if name == "stub":
        return cmd_stub(wt, date, params[2])
    if name == "install":
        return cmd_install(wt, date, params[2], params[3])
    if name == "abandon":
        return cmd_abandon(wt, date)
    if name == "check-staged":
        return cmd_check_staged(wt, date)
    return cmd_check_clean(wt, date)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
