#!/usr/bin/env python3
"""Stray-terminal-output scanner for composed journal files (dev-env #894, ADR-121).

`/journal-compose` writes three kinds of file: the journal entry (Step 6), the folder
README (Step 7), and the top-level README (Steps 8/8a). Nothing checked any of them for
raw terminal output that leaked into the prose.

Motivating incident: the 2026-07-11 compose emitted the standard `git rebase` "no tracking
information" usage message spliced mid-paragraph into the `## Progress Summary` of
engineering-journal's `sessions/dev-env/README.md`. ``git log -S "See git-rebase(1)" --all``
puts the earliest occurrence in that compose's own commit -- the paragraph was *born*
corrupted -- and it then survived ~8 further compose passes and 11 days undetected. The same
pass also mis-attributed an issue number two screens down (engineering-journal#185), so the
failure mode is "one bad compose run produces several silent corruptions", not a one-off slip.

dev-env#467's Step 6.5 structural assertion does not cover this: it checks *headings*, and
only on journal *entries*, so mid-paragraph body text in a README passes clean. That check
shipped 2026-07-04; this corruption landed 2026-07-11.

Two complementary checks, both deliberately narrow:

``signature``
    A known git usage/error string in prose. Fenced code blocks and inline code spans are
    exempt -- journal entries legitimately quote these errors as documented content, and a
    check that flagged them would be noise and get ignored. Indented lines are NOT exempt:
    the motivating paste arrived as an indented block, and these journals fence their code
    rather than indenting it.

``progress-summary-indent``
    An indented line inside a ``## Progress Summary`` section. That section is narrative
    prose and is never indented -- a scan of all 10 ``sessions/*/README.md`` in
    engineering-journal found exactly two such lines, both being this bug.

    **Known limitation, accepted deliberately:** a *nested markdown list* inside a Progress
    Summary is indented, and so trips this check. Zero instances exist across the whole
    corpus (431 files), and the skill's Step 7 template specifies a "2-3 sentence narrative"
    rather than a list, so this is unrealized in practice. It is left unexempted because the
    indent check exists precisely to catch pasted text carrying *no* known signature --
    exempting lines that start with ``-``/``*``/``N.`` would trade a measured-zero false
    positive for an unmeasured false negative in the one check designed for the unknown case.
    The remedy when it does fire is trivial (fence the block, or reword), and the gate is
    advisory. Pinned by ``test_nested_list_in_progress_summary_is_a_known_false_positive``
    so the behavior is a decision rather than an accident.

Both are *advisory*: this module reports regions, and never edits. The corruption was
self-concealing -- the paste ate the middle of a sentence and welded a surviving prose
fragment (``" pattern that auto-closes open PRs. ADR count now at 101+."``) onto the tail of
a ``git branch --set-upstream-to`` line -- so a cleanup that deleted the machine-looking
block would have silently dropped real content. A human/agent must read each hit.

Pure-helper convention (matches the rest of ``claude/scripts/``): everything here is pure
(str in, data out) with no disk, network, or subprocess, and is unit-tested offline in
``tests/test_composed_output_scan.py``. The impure surface is ``validate-composed-output.py``'s
``main()``, which reads the files named on argv.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------------------

# (needle, anchored) -- ``anchored`` means the needle must start the line (after leading
# whitespace). ``hint: `` and ``fatal: `` are common enough as mid-sentence prose fragments
# ("the fatal: line was suppressed") that an unanchored match would be noisy; git always
# emits them at the start of a line. The rest are distinctive enough to match anywhere.
SIGNATURES = (
    ("See git-rebase(1)", False),
    ("--set-upstream-to", False),
    ("Please specify which branch", False),
    ("There is no tracking information", False),
    ("Everything up-to-date", False),
    ("nothing added to commit", False),
    ("hint: ", True),
    ("fatal: ", True),
)

PROGRESS_SUMMARY_HEADING = "## Progress Summary"

# A fence opener/closer: 3+ backticks or tildes, optionally indented, optional info string.
_FENCE_RE = re.compile(r"^\s{0,3}(?P<mark>`{3,}|~{3,})\s*(?P<info>.*)$")

# An inline code span: a run of N backticks, the shortest body, then the same run.
# The backreference is what makes ``a `b` c`` (a double-tick span containing single ticks)
# match as ONE span -- a naive ``\`[^\`]*\`` would split it and leak the inner text.
_CODE_SPAN_RE = re.compile(r"(?P<ticks>`+)(?:(?!(?P=ticks)).)+(?P=ticks)", re.DOTALL)

# An indented line with actual content (4+ spaces or a tab). Blank lines never count.
_INDENTED_RE = re.compile(r"^(?: {4,}|\t)\S")

# Any ATX heading, used to bound the Progress Summary section.
_HEADING_RE = re.compile(r"^#{1,6}\s")


def strip_code_spans(line: str) -> str:
    """Blank out inline code spans so their contents cannot trip a signature match.

    Replaces each span with a single space rather than deleting it, so character content
    cannot accidentally be joined across the removal into a new false match.

    Unbalanced backticks leave the line untouched, which is deliberately the conservative
    direction for an advisory check: it may over-report, never under-report.
    """
    return _CODE_SPAN_RE.sub(" ", line)


def find_signature_hits(line: str):
    """Return the signatures present in ``line``, ignoring inline code spans.

    Returns a list of matched needles (in ``SIGNATURES`` order), empty when clean.
    """
    scrubbed = strip_code_spans(line)
    stripped = scrubbed.lstrip()
    hits = []
    for needle, anchored in SIGNATURES:
        if anchored:
            if stripped.startswith(needle):
                hits.append(needle)
        elif needle in scrubbed:
            hits.append(needle)
    return hits


def _fence_state(lines):
    """Yield ``(lineno, line, in_fence)`` for each line, 1-indexed.

    ``in_fence`` is True for the fence delimiter lines themselves as well as their
    contents, so a signature cannot hide in an info string.

    Closing a fence requires the same character as the opener and at least as many of them
    (CommonMark), so a ``~~~`` inside a ``` block does not terminate it.
    """
    fence_mark = None
    for lineno, line in enumerate(lines, start=1):
        m = _FENCE_RE.match(line)
        if fence_mark is None:
            if m:
                fence_mark = m.group("mark")
                yield lineno, line, True
                continue
            yield lineno, line, False
        else:
            closes = (
                m is not None
                and m.group("mark")[0] == fence_mark[0]
                and len(m.group("mark")) >= len(fence_mark)
                and not m.group("info")
            )
            yield lineno, line, True
            if closes:
                fence_mark = None


def scan_text(text: str):
    """Scan composed markdown for stray terminal output.

    Returns a list of findings, each a dict with ``line`` (1-indexed), ``kind``
    (``"signature"`` or ``"progress-summary-indent"``), ``detail``, and ``text`` (the
    offending line, right-stripped). Findings are ordered by line number, and a line
    produces at most one finding of each kind.
    """
    lines = text.splitlines()
    findings = []
    in_progress_summary = False

    for lineno, line, in_fence in _fence_state(lines):
        if not in_fence and _HEADING_RE.match(line):
            # A heading closes any open Progress Summary and may open a new one.
            in_progress_summary = line.strip() == PROGRESS_SUMMARY_HEADING

        if in_fence:
            # Fenced content is documented output by construction -- the whole point of
            # the exemption. Never scan it.
            continue

        hits = find_signature_hits(line)
        if hits:
            findings.append({
                "line": lineno,
                "kind": "signature",
                "detail": "matched " + ", ".join(repr(h) for h in hits),
                "text": line.rstrip(),
            })

        if in_progress_summary and _INDENTED_RE.match(line):
            findings.append({
                "line": lineno,
                "kind": "progress-summary-indent",
                "detail": "indented line inside '## Progress Summary' (prose is never indented)",
                "text": line.rstrip(),
            })

    findings.sort(key=lambda f: (f["line"], f["kind"]))
    return findings
