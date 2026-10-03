#!/usr/bin/env python3
"""Mechanical half of ``/journal-compose`` Step 6.7: meta triggers are composed, not asked about.

dev-env #52 and #892; ADR-082 Addendum (2026-10-02); ADR-129 Amendment 2.

Step 2b used to ask "open a meta draft block? (y/n)", which nobody can answer in a scheduled run,
and its "yes" branch wrote ``sessions/meta/YYYY-MM-DD_draft.md`` -- a legacy draft that nothing
composes, so the answer was lost whether or not meta was composed that day. The fix composes meta
in the same run from *derived stubs*. This script is the part of that pass that must not be left to
an LLM or to the Write tool:

* The harness refuses the coordinator's Write/Edit into the isolated compose worktree (#1119), and
  ``pre-tool-use-journal-shell-write-guard.py`` blocks Bash redirects to stub and manifest paths, so
  a Python process launched by Bash is the only compliant writer (ADR-129 Amendment 2).
* A Phase 1 subagent's trigger report is a claim. ``stub`` checks each record against the stub it
  cites before anything is written, and names every rejection -- nothing is dropped silently.
* The coordinator's composed journal is a claim too. ``install`` gates it before it is copied into
  the worktree, and ``check-clean`` proves nothing for the date was left behind.

Subcommands (``<WT>`` is the compose worktree root, ``<DATE>`` is YYYY-MM-DD)::

    stub        <WT> <DATE> <records.json>
    install     <WT> <DATE> <staged.md> <slug>
    abandon     <WT> <DATE>
    check-clean <WT> <DATE>

``stub`` reads records ``{"project", "type", "stub", "reason", "evidence"}`` (a JSON list, or an
object with a ``records`` list), verifies each, and writes one *derived stub* per trigger category
into ``<WT>/sessions/meta/`` as ``DATE_2359NN.stub.md`` plus a manifest shard. Derived files are
untracked, never pushed, and consumed by Step 9's ordinary deletion globs in the same run.
``install`` copies the staged composed journal to ``<WT>/sessions/meta/DATE-<slug>.md`` once it
has all eleven required headings and cites every derived ``Source:`` path. ``abandon`` removes
derived files and only derived files. ``check-clean`` fails if any stub, manifest or ``_draft.md``
for the date remains anywhere under ``sessions/``.

Exit 0 -- ok. Exit 1 -- usage or precondition error, nothing changed. Exit 2 -- verification
failure (``stub``: records arrived and none was accepted; ``install``: the journal was refused;
``check-clean``: something was left behind). Reports go to stdout as ``KEY=value`` lines, errors to
stderr prefixed ``[journal-compose-meta]``.

There are no numeric thresholds anywhere. Every check keys on a literal token this pass generated
or that the skill already requires (the eleven section headings, the ``Source:`` paths), and
``FIDELITY`` is reported but never gates: the skill's 80% / 50% figures are rough heuristics that
have not been calibrated against derived input (ADR-144).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import unicodedata

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
_HEADING_RE = re.compile(r"^#{1,6}\s")
_H2_RE = re.compile(r"^##\s+(.*\S)\s*$")

EXCERPT_CONTEXT = 2          # lines of context either side of the evidence line (formatting only)
TITLE_LIMIT = 120            # H3 title length (formatting only)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def normalize(text):
    """NFKC, then collapse every whitespace run to one space -- how evidence is compared."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


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


def write_text_atomic(path, text):
    """UTF-8, LF, no BOM, via a temp file in the same directory and ``os.replace``."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.tmp-{os.getpid()}"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temp, path)


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
    return head.decode("utf-8", errors="replace").lstrip("﻿").startswith(DERIVED_MARKER)


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


def remove_derived(wt, date):
    """Delete derived stubs, their manifest shards, and orphaned derived manifests for ``date``.

    Marker-verified: a real stub or manifest is never touched. Returns the removed relative paths.
    """
    removed = []
    _real, derived = list_meta_stubs(wt, date)
    for stub in derived:
        pair = stub[: -len(".stub.md")] + ".manifest.jsonl"
        for path in (stub, pair):
            if os.path.isfile(path):
                os.remove(path)
                removed.append(rel(wt, path))
    meta_dir = _meta_dir(wt)
    try:
        names = sorted(os.listdir(meta_dir))
    except OSError:
        names = []
    for name in names:
        if name.startswith(f"{date}_") and name.endswith(".manifest.jsonl"):
            path = os.path.join(meta_dir, name)
            if _is_derived_manifest(path):
                os.remove(path)
                removed.append(rel(wt, path))
    return removed


def allocate_names(date, type_indexes, taken):
    """Map each type index to a free ``DATE_2359NN.stub.md`` name.

    NN starts at the type's table index and is bumped past ``taken`` (the real stubs' names) and
    past names already handed out, so a real stub is never shadowed. Raises ValueError when the
    minute is exhausted.
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

def find_evidence(lines, needle):
    """Index of the first line whose normalized text contains ``needle`` (or, failing that, whose
    join with the next line does -- evidence copied across a wrapped line). None if absent."""
    if not needle:
        return None
    normalized = [normalize(line) for line in lines]
    for index, line in enumerate(normalized):
        if needle in line:
            return index
    for index in range(len(normalized) - 1):
        if needle in f"{normalized[index]} {normalized[index + 1]}":
            return index
    return None


def session_heading(lines, index):
    """Text of the nearest H2 at or above ``index``, or None."""
    for position in range(index, -1, -1):
        match = _H2_RE.match(lines[position])
        if match:
            return match.group(1)
    return None


def excerpt(lines, index):
    """The evidence line plus up to ``EXCERPT_CONTEXT`` lines either side, never crossing a heading."""
    start = index
    while start > max(index - EXCERPT_CONTEXT, 0) and not _HEADING_RE.match(lines[start - 1]):
        start -= 1
    end = index
    last = min(index + EXCERPT_CONTEXT, len(lines) - 1)
    while end < last and not _HEADING_RE.match(lines[end + 1]):
        end += 1
    chunk = list(lines[start : end + 1])
    while chunk and not chunk[0].strip():
        chunk.pop(0)
    while chunk and not chunk[-1].strip():
        chunk.pop()
    return chunk


def _shown(value):
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())[:60]
    return "?"


def _stub_basename(value, project, date):
    path = value.strip().replace("\\", "/")
    if "/" in path:
        prefix, _sep, name = path.rpartition("/")
        if prefix != f"sessions/{project}":
            return None, f"stub path must be the bare filename or sessions/{project}/<filename>"
    else:
        name = path
    if not re.fullmatch(re.escape(date) + r"_\d{6}\.stub\.md", name):
        return None, f"stub name must be {date}_######.stub.md for the compose date"
    return name, None


def validate_record(wt, date, record):
    """Check one trigger record against the stub it cites.

    Returns ``(accepted, None)`` or ``(None, (project, stub, type, reason))``. The rejection carries
    whatever the record had, so the report can name it.
    """
    if not isinstance(record, dict):
        return None, ("?", "?", "?", "record is not a JSON object")
    shown = (_shown(record.get("project")), _shown(record.get("stub")), _shown(record.get("type")))

    def reject(reason):
        return None, shown + (reason,)

    fields = {}
    for key in ("project", "type", "stub", "reason", "evidence"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            return reject(f"missing or empty field: {key}")
        fields[key] = value.strip()

    project = fields["project"]
    if project == "meta":
        return reject("project 'meta' is skipped: meta triggers are never reported for meta")
    if not _PROJECT_RE.fullmatch(project):
        return reject(f"unsafe project name {project!r}")
    project_dir = os.path.join(wt, "sessions", project)
    if not os.path.isdir(project_dir):
        return reject(f"no sessions/{project}/ directory in the compose worktree")

    slug = fields["type"].lower().replace("_", "-")
    if slug not in _SLUG_TO_INDEX:
        known = ", ".join(name for name, _label in TRIGGER_TYPES)
        return reject(f"unknown type {fields['type']!r} (expected one of: {known})")

    stub_name, problem = _stub_basename(fields["stub"], project, date)
    if problem:
        return reject(problem)
    stub_path = os.path.join(project_dir, stub_name)
    if not _inside(wt, stub_path):
        return reject("stub path escapes the compose worktree")
    if not os.path.isfile(stub_path):
        return reject(f"stub not found: sessions/{project}/{stub_name}")
    text, error = read_utf8(stub_path)
    if text is None:
        return reject(f"stub unreadable: {error}")

    needle = normalize(fields["evidence"])
    lines = text.split("\n")
    index = find_evidence(lines, needle)
    if index is None:
        return reject(
            "evidence not found in the cited stub (copy a short phrase from ONE line, "
            "character for character)"
        )
    return {
        "project": project,
        "type_index": _SLUG_TO_INDEX[slug],
        "stub": stub_name,
        "reason": " ".join(fields["reason"].split()),
        "heading": session_heading(lines, index),
        "excerpt": excerpt(lines, index),
        "evidence_norm": needle,
    }, None


# ---------------------------------------------------------------------------
# Derived stub construction
# ---------------------------------------------------------------------------

def build_derived_stub(date, label, items):
    """Markdown for one derived stub: one H2, an H3 per record, evidence quoted with ``> ``."""
    projects = sorted({item["project"] for item in items})
    lines = [
        f"{DERIVED_MARKER} generated by journal-compose for {date} from verified trigger records; "
        "compose-internal, not a session. Quoted lines are copied verbatim from the cited source "
        "stubs. -->",
        "",
        f"## {label} — detected in {', '.join(projects)} ({date})",
        "",
        f"{len(items)} trigger(s) reported by the per-project composers; each was verified "
        "against the stub it cites.",
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


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _load_records(path, date):
    """``(records, error)``; exactly one is None."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, str(exc)
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        return None, f"not valid JSON: {exc}"
    if isinstance(data, dict):
        if data.get("date") not in (None, date):
            return None, f"records file is for {data.get('date')!r}, not {date}"
        data = data.get("records")
    if not isinstance(data, list):
        return None, "expected a JSON list of records (or an object with a 'records' list)"
    return data, None


def cmd_stub(wt, date, records_path):
    records, error = _load_records(records_path, date)
    if records is None:
        return _fail(f"cannot read records {records_path!r}: {error}")

    accepted, rejected, seen, deduped = [], [], set(), 0
    for record in records:
        item, rejection = validate_record(wt, date, record)
        if rejection is not None:
            rejected.append(rejection)
            continue
        key = (item["project"], item["stub"], item["type_index"], item["evidence_norm"])
        if key in seen:
            deduped += 1
            continue
        seen.add(key)
        accepted.append(item)

    report = [
        f"META_RECORDS_RECEIVED={len(records)}",
        f"META_TRIGGERS_ACCEPTED={len(accepted)}",
        f"META_TRIGGERS_REJECTED={len(rejected)}",
        f"META_TRIGGERS_DEDUPED={deduped}",
    ]
    report += [f"META_TRIGGER_REJECTED {proj} {stub} {kind} -- {why}" for proj, stub, kind, why in rejected]
    if not accepted:
        report.append("META_STATUS=none")
        _emit(report)
        return 2 if records else 0

    real, _derived_old = list_meta_stubs(wt, date)
    by_type = {}
    for item in accepted:
        by_type.setdefault(item["type_index"], []).append(item)
    try:
        names = allocate_names(date, by_type, {os.path.basename(path) for path in real})
    except ValueError as exc:
        return _fail(str(exc))

    planned = []
    for index in sorted(by_type):
        items = sorted(by_type[index], key=lambda entry: (entry["project"], entry["stub"]))
        label = TRIGGER_TYPES[index][1]
        projects = sorted({entry["project"] for entry in items})
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
        planned.append((names[index], build_derived_stub(date, label, items), json.dumps(manifest) + "\n"))

    # Replace semantics: this run's derived set supersedes any earlier one for the date.
    remove_derived(wt, date)
    meta_dir = _meta_dir(wt)
    total = 0
    for stub_name, stub_text, manifest_text in planned:
        write_text_atomic(os.path.join(meta_dir, stub_name), stub_text)
        write_text_atomic(
            os.path.join(meta_dir, stub_name[: -len(".stub.md")] + ".manifest.jsonl"), manifest_text
        )
        report.append(f"META_STUB=sessions/meta/{stub_name}")
        total += len(stub_text.splitlines())
    report += [f"META_DERIVED_LINES={total}", "META_STATUS=derived"]
    _emit(report)
    return 0


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

    sources = []
    for path in derived:
        stub_text, error = read_utf8(path)
        if stub_text is None:
            return _fail(f"derived stub {rel(wt, path)} unreadable: {error}")
        found = _SOURCE_RE.findall(stub_text)
        if not found:
            return _fail(
                f"derived stub {rel(wt, path)} has no extractable 'Source:' line -- refusing a "
                "citation check that would pass vacuously"
            )
        sources.extend(found)

    missing = missing_headings(text)
    uncited = sorted({source for source in sources if source not in text})
    if missing or uncited:
        report = ["INSTALL_REFUSED"]
        if missing:
            report.append("STRUCTURE=missing:" + ",".join(missing))
        if uncited:
            report.append("SOURCES_UNCITED=" + ",".join(uncited))
        _emit(report)
        return 2

    meta_dir = _meta_dir(wt)
    target_name = f"{date}-{slug}.md"
    try:
        existing = sorted(
            name for name in os.listdir(meta_dir) if name.startswith(f"{date}-") and name.endswith(".md")
        )
    except OSError:
        existing = []
    other = [name for name in existing if name != target_name]
    if other:
        return _fail(
            f"a composed meta journal for {date} already exists (sessions/meta/{other[0]}): "
            "one per date -- remove it first"
        )

    write_text_atomic(os.path.join(meta_dir, target_name), text if text.endswith("\n") else text + "\n")

    source_lines = 0
    for path in real + derived:
        stub_text, _error = read_utf8(path)
        source_lines += len(stub_text.splitlines()) if stub_text else 0
    _emit(
        [
            f"META_JOURNAL=sessions/meta/{target_name}",
            "STRUCTURE=ok",
            f"SOURCES_CITED={len(set(sources))}/{len(set(sources))}" if sources else "SOURCES_CITED=n/a",
            f"LINE_COUNT={len(text.splitlines())}",
            f"SOURCE_LINES={source_lines}",
            f"FIDELITY={len(text.splitlines())}/{source_lines}",
        ]
    )
    return 0


def cmd_abandon(wt, date):
    removed = remove_derived(wt, date)
    _emit(["META_ABANDONED=" + (",".join(removed) if removed else "none")])
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
            if is_stub or name in (f"{date}.manifest.jsonl", f"{date}_draft.md"):
                leftovers.append(f"sessions/{project}/{name}")
    if not leftovers:
        _emit(["CHECK_CLEAN=ok"])
        return 0
    _emit(["CHECK_CLEAN=leftover"] + [f"LEFTOVER {path}" for path in leftovers])
    return 2


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

USAGE = (
    "usage: py -3 journal-compose-meta.py stub <worktree> <YYYY-MM-DD> <records.json>\n"
    "       py -3 journal-compose-meta.py install <worktree> <YYYY-MM-DD> <staged.md> <slug>\n"
    "       py -3 journal-compose-meta.py abandon <worktree> <YYYY-MM-DD>\n"
    "       py -3 journal-compose-meta.py check-clean <worktree> <YYYY-MM-DD>\n"
)

# Positional arguments each subcommand takes after its own name.
ARITY = {"stub": 3, "install": 4, "abandon": 2, "check-clean": 2}


def main(argv):
    _utf8_stdio()
    args = argv[1:]
    if not args or args[0] not in ARITY or len(args) - 1 != ARITY[args[0]]:
        sys.stderr.write(f"{LOG_PREFIX} {USAGE}")
        return 1
    name, params = args[0], args[1:]
    wt, date = os.path.normpath(params[0]), params[1]
    if not valid_date(date):
        return _fail(f"{date!r} is not a YYYY-MM-DD date")
    if not os.path.isdir(os.path.join(wt, "sessions")):
        return _fail(f"no sessions/ directory under {wt!r} -- is that a compose worktree?")
    if name == "stub":
        return cmd_stub(wt, date, params[2])
    if name == "install":
        return cmd_install(wt, date, params[2], params[3])
    if name == "abandon":
        return cmd_abandon(wt, date)
    return cmd_check_clean(wt, date)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
