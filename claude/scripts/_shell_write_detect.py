#!/usr/bin/env python3
"""Shared shell-command write-detection primitives for the two content-write
guard hooks: `pre-tool-use-journal-shell-write-guard.py` (ADR-129 -- the four
engineering-journal content-file kinds, blocked unconditionally) and
`pre-tool-use-shell-content-write-guard.py` (ADR-138 -- any file, blocked when
the content is a hazard-bearing inline literal).

Every function here was extracted VERBATIM from the ADR-129 guard as a pure
move, no behavior change. That guard's Amendment 1 landed 16 post-`/review`
findings, several of them inside these exact functions
(`neutralize_unquoted_escaped_quotes` alone absorbed findings #1 and #5), which
is precisely why the second hook imports them rather than hand-copying: a
future fix to subtle quote-state logic must reach both callers, and the
amendment history proves such fixes do happen. Same rationale as
`_journal_canon.py` (ADR-133).

The ADR-129 guard's own 63-case suite passing UNCHANGED across this extraction
is the safety claim -- see `## Testing` item 94, which runs both suites
together the way item 92 does for `_journal_canon.py`.

Deliberately NOT here: anything either hook decides on its own -- path-shape
classification, hazard-marker scoring, override-token names, block messages.
This module answers only "what does this command's shell syntax say", never
"is that a problem".

Import-only; no `main()`, no I/O, no subprocess, no filesystem access.
"""
import re
import shlex

from _hookio import mask_quoted_spans, split_top_level

# --- Segment-local helpers -------------------------------------------------

HEREDOC_MARKER_RE = re.compile(r"<<")

# A '>' or '>>' not immediately adjacent to another '>' (so '>>' is matched
# once, as a 2-char operator, not twice as two single ones).
REDIRECT_OP_RE = re.compile(r"(?<!>)(>{1,2})(?!>)")


def first_line(segment):
    """segment's own first physical line -- a heredoc/here-string body must
    never be mistaken for invocation syntax."""
    return segment.split("\n", 1)[0]


def neutralize_unquoted_escaped_quotes(line):
    """Neutralize a backslash-escaped quote (`\\'` or `\\"`) exactly where
    real Bash treats it as a literal character rather than a quote
    boundary -- i.e. everywhere EXCEPT while already inside an open
    single-quoted span, where backslash has no special meaning at all and
    any bare `'` genuinely closes the span. A blind, context-free
    substitution gets this backwards for that one case: neutralizing a
    `\\'` immediately before a span's real closing quote (e.g. a
    single-quoted Windows path ending in a literal backslash, `'C:\\dir\\'`)
    would prevent that quote from closing the span at all, masking away
    everything after it -- INCLUDING a real redirect target. (An earlier
    version of this function used exactly that blind substitution; it
    fixed the target case but silently regressed this one -- caught only
    by directly re-testing the pre-fix behavior, not by inspection.)

    This walker tracks just enough of `_hookio._opaque_spans`'s three
    quote-relevant states to make the context-dependent call correctly
    (unquoted and `$()`-subshell content are folded into one 'top' bucket,
    since backslash-escape semantics for a quote character are identical
    in both):
      - 'top' (unquoted, or inside a subshell): `\\'`/`\\"` is a literal
        escaped character in real Bash and must not open a span --
        NEUTRALIZE. This is the actual reported hazard, e.g.
        `echo Claude\\'s > ...` -- the standard Bash workaround for
        embedding an apostrophe in unquoted prose.
      - inside `'...'`: no escape processing exists at all; any bare `'`
        closes the span regardless of what precedes it -- NEVER touch it.
      - inside `"..."`: `\\"` legitimately escapes a literal embedded
        double-quote without closing the span (real Bash behavior) --
        NEUTRALIZE, to prevent the false close. A bare `'` here is inert
        literal content either way and never toggles single-quote state.

    Length-preserving (every branch emits exactly as many characters as it
    consumes), so offsets stay aligned with the original text -- the same
    invariant `<<` neutralization below relies on."""
    out = []
    state = "top"  # "top" (unquoted + subshell), "single", or "double"
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if state == "single":
            out.append(c)
            if c == "'":
                state = "top"
            i += 1
            continue
        if state == "double":
            if c == "\\" and i + 1 < n:
                nxt = line[i + 1]
                out.append("\\")
                out.append("#" if nxt in ("'", '"') else nxt)
                i += 2
                continue
            out.append(c)
            if c == '"':
                state = "top"
            i += 1
            continue
        # state == "top"
        if c == "\\" and i + 1 < n and line[i + 1] in ("'", '"'):
            out.append("\\")
            out.append("#")
            i += 2
            continue
        out.append(c)
        if c == "'":
            state = "single"
        elif c == '"':
            state = "double"
        i += 1
    return "".join(out)


def mask_first_line_quotes(line):
    """Quote-mask an ALREADY first-line-truncated string without
    mis-triggering `_hookio`'s heredoc-opener handling, which assumes a real
    multi-line body may still follow -- never true here. Fed directly, a
    `<<'EOF'` opener with no following newline makes `_find_heredoc_end`
    consume the rest of the string (including a same-line redirect target)
    as an unterminated heredoc declaration. Neutralizing `<<` (same length,
    so offsets stay aligned with the original) before masking sidesteps
    this; a genuine `<<` inside a quote was never treated as an opener by
    `_opaque_spans` in the first place, so this neutralization is safe.
    Also neutralizes a backslash-escaped quote where real Bash would --
    see `neutralize_unquoted_escaped_quotes`.

    This is the load-bearing fix for detecting the single most common
    real-world shape of the reported bug (`cat <<'EOF' > <file>`); the
    ADR-129 guard's suite pins it as
    `test_find_bash_redirect_targets_heredoc_declaration_line`."""
    neutralized = HEREDOC_MARKER_RE.sub("<#", line)
    neutralized = neutralize_unquoted_escaped_quotes(neutralized)
    return mask_quoted_spans(neutralized)


def next_token(text):
    """Read the next whitespace- or quote-delimited token from the start of
    *text* (after skipping leading spaces/tabs). Used to read a redirect's
    real target from the RAW (unmasked) line at the offset a match was found
    in the masked line -- offsets stay aligned because masking preserves
    string length."""
    text = text.lstrip(" \t")
    if not text:
        return ""
    if text[0] in ("'", '"'):
        end = text.find(text[0], 1)
        return text[1:end] if end != -1 else text[1:]
    end = 0
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[:end]


def find_redirect_targets(line, masked=None):
    """Return [(operator, target), ...] for every genuine (non-quoted)
    '>'/'>>' redirect on *line*, in original-command order. Applies to both
    tool_name values -- PowerShell supports `>`/`>>` natively too, as
    aliases for `Out-File`/`Out-File -Append`.

    *masked* -- the already-computed `mask_first_line_quotes(line)` -- lets
    a caller compute the mask once per segment and share it across
    detectors rather than each recomputing it independently (measured ~35%
    of detector time as pure duplicate work before this sharing).
    Computed here if not supplied, so a direct/standalone call (tests, the
    REPL) needs no change."""
    if masked is None:
        masked = mask_first_line_quotes(line)
    out = []
    for m in REDIRECT_OP_RE.finditer(masked):
        target = next_token(line[m.end():])
        if target:
            out.append((m.group(1), target))
    return out


def tokenize_posix(line):
    """POSIX-mode tokenization for a genuinely Bash-context command, where
    backslash really does mean escape. Falls back to a naive whitespace
    split on an unterminated-quote `ValueError` -- the shared convention
    for every tokenizer in this module."""
    try:
        return shlex.split(line, posix=True)
    except ValueError:
        return line.split()


def tokenize_powershell(line):
    """PowerShell-appropriate tokenization: groups quoted multi-word values
    into single tokens the same way `shlex.split(posix=True)` does, but
    without POSIX backslash-escape processing -- PowerShell uses backtick
    (`` ` ``) as its escape character, not backslash, so `posix=True`
    silently eats every backslash in an unquoted Windows path
    (`sessions\\dev-env\\tiles\\961.json` -> `sessionsdev-envtiles961.json`,
    destroying it before any path check ever sees it -- verified live,
    dev-env#962 review). `posix=False` leaves surrounding quote characters
    attached to a token instead of stripping them (e.g. a single-quoted
    value token comes back as `'...'`, not `...`), so callers that compare a
    token against a path shape must strip one matching leading/trailing
    quote pair themselves. Falls back to a naive whitespace split on the
    rare unterminated-quote `ValueError` (dev-env#620 follow-up: a
    PowerShell here-string opener like a `-Value @'` invocation's trailing
    `@'` is an unterminated quote to `shlex` either way)."""
    try:
        return shlex.split(line, posix=False)
    except ValueError:
        return line.split()


def segments_or_whole(cmd, segments=None):
    """`split_top_level(cmd, split_pipe=True)` with a fallback for one shape
    that function's own docstring calls out as deliberate: "If *command*
    ends with an unterminated quote/subshell/heredoc, the trailing
    (malformed) segment is dropped rather than returned." Its single-quote
    state has no escape-awareness (correctly matching real Bash, where
    backslash means nothing inside real single quotes) -- so a backslash
    -escaped apostrophe in UNQUOTED prose (`echo Claude\\'s > ...`, the
    standard Bash workaround, and the exact motivating hazard) opens a
    single-quote span at the bare `'` and never finds a closing one,
    dropping the ENTIRE command as "unterminated" even though it's
    well-formed, executable Bash. `split_top_level` is a heavily-tested
    shared primitive (~30 tests, several other hook callers) --
    deliberately not touched here (see its own module comment on why
    `mask_quoted_spans` was written as an independent walker rather than
    risk perturbing it). Falling back to the whole raw command as one
    opaque segment when segmentation returns nothing is strictly safer
    than losing the command (and the hazard) entirely: `first_line()`
    still truncates it to one physical line downstream, so the only cost
    is that a command sharing this exact shape AND a real `&&`/`;`/`|`
    boundary is seen as one segment instead of several -- narrower than
    the miss this avoids, and only relevant if the redirect/cmdlet lands
    on a different top-level statement than the escaped quote."""
    if segments is not None:
        return segments
    out = split_top_level(cmd, split_pipe=True)
    if not out and cmd.strip():
        return [cmd]
    return out
