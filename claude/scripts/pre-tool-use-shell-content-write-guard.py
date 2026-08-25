#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- blocks a Bash/PowerShell command that writes
file content supplied as an INLINE LITERAL in the command string, when that
literal carries shell-quoting hazards. ADR-138 generalizes ADR-129's
journal-path-scoped rule to a content-shaped one: the hazard was never the
location, only ever "prose- or escape-bearing content routed through a shell."
See dev-env#1041 for the three non-journal occurrences in one session that
motivated it, and ADR-138 for the full rationale this docstring does not
repeat.

The load-bearing distinction, and what makes a fleet-wide guard safe:

  INLINE LITERAL (guarded)  -- a heredoc body, an `echo`/`printf` argument, a
      `node -e`/`py -c` script, a `sed -i` script, a PowerShell `-Value` or
      `@'...'@` here-string. The content crosses a shell string-parsing
      boundary, so the shell can mangle or reject it.
  PROGRAM OUTPUT (never matched) -- `gh ... > "$TMPFILE"`, `npm test > out.log`,
      `git diff > patch`, `... | tee f`. No literal crosses a quoting
      boundary, so there is no hazard to guard. This is the "short,
      known-safe, machine-generated" carve-out `claude/CLAUDE.md` ->
      Authoring File Content names, and it is structural here, not a
      heuristic: no inline literal found means no match, full stop.

Detection contract (see each function for its own mechanism):
  - `find_content_writes` -- the aggregate walk. Per top-level segment
    (`_shell_write_detect.segments_or_whole`): locate a genuine file-write
    destination (a non-quoted `>`/`>>` redirect, or Bash `tee`), then find
    the inline literal feeding it. No destination or no literal -> no match.
  - `extract_heredoc_literal` -- heredoc/here-string body, found via a
    genuine (non-quoted) `<<` located in the masked first line.
  - `extract_echo_literal` -- the raw argument region of an `echo`/`printf`
    before the redirect operator.
  - `find_serializer_write` -- `node -e`/`py -3 -c`/`python[3] -c` whose
    script either contains a file-write call or is redirected to a file.
  - `find_inplace_edit` -- `sed -i` / `perl -pi -e`; the script argument is
    the literal. No separate destination needed -- the file IS the target.
  - `find_stdin_content_arg` -- a CONTENT destination that is not a file:
    a heredoc piped into `gh`/`git`'s own authored-prose argument
    (`--body-file -`, `--notes-file -`, `-F -`). Added by ADR-138
    Amendment 1 after measurement; an interpreter reading a PROGRAM from
    stdin (`py -3 - <<'PY'`) carries no such flag and stays an accepted gap.
  - `find_powershell_content_write` -- `Set-Content`/`Add-Content`/
    `Out-File`/`Tee-Object`/`New-Item -Value` with a literal `-Value` or a
    piped `@'...'@` here-string. Gated on tool_name == "PowerShell".
  - `body_hazard` / `arg_hazard` -- the two hazard tests. A match is
    reported ONLY when its literal is hazardous; a single-line literal with
    no apostrophe, backtick, or backslash passes (`echo done > flag.txt`).
  - `_is_overridden` -- a genuine `ALLOW_SHELL_CONTENT_WRITE=1` Bash prefix
    (segment-scoped) or `$env:ALLOW_SHELL_CONTENT_WRITE=1` PowerShell
    statement (applies forward), matching real shell semantics. Also
    honours ADR-129's `ALLOW_JOURNAL_SHELL_WRITE=1` -- without that, a
    deliberate journal-guard override would clear that hook and be blocked
    by this one, silently breaking a live escape hatch.
  - Blocks (exit 2) via `_hookout.emit_block` (ADR-103). Fails OPEN (exit 0)
    everywhere else, including on any internal exception. No `_winsubp`
    import: this hook spawns no subprocess, and its own detection logic
    touches no filesystem -- every question it answers comes from the
    command text alone. (The one unconditional filesystem write is the
    `_hookutil.record_heartbeat` call every wired hook makes, ADR-106; it
    predates and is independent of this hook's own no-I/O property.)

Wired AFTER `pre-tool-use-journal-shell-write-guard.py` in `claude/settings.json`
so a journal-path write keeps that hook's more specific remedy message.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",  # or "PowerShell"
    "tool_input": {"command": "..."},
    "session_id": "...",
    "cwd": "..."
  }
"""
import json
import re
import sys

from _shell_write_detect import (
    find_redirect_targets,
    first_line,
    mask_first_line_quotes,
    next_token,
    segments_or_whole,
    tokenize_posix,
    tokenize_powershell,
)
import _hookout
import _hookutil

# --- Hazard tests -----------------------------------------------------------
#
# Both answer one question -- "would a shell mangle or choke on this
# content?" -- and both return a short human-readable reason, or None when
# the literal is safe. The blocking line is the same sentence
# `claude/CLAUDE.md` -> Authoring File Content states, so guidance and
# mechanism cannot drift: prose, a quote/backtick/backslash, or more than
# one line.

# A literal apostrophe is the single most common breaker (an unescaped `'`
# closes a single-quoted string early); a backtick is command substitution;
# a backslash is consumed as an escape wherever the shell can see it.
_BODY_MARKERS = (("'", "an apostrophe"), ("`", "a backtick"), ("\\", "a backslash"))


def body_hazard(text):
    """Hazard test for a RAW body -- a heredoc/here-string body or a
    PowerShell `@'...'@` here-string. These are not shell-quoted content,
    so there is no quoting structure to discount: every character is
    content, and the markers are tested literally.

    Deliberately NOT exempting a quote-delimiter heredoc (`<<'EOF'`), even
    though POSIX says it suppresses expansion. dev-env#1041 occurrences 1
    and 3 BOTH used that exact form -- the documented-safe mitigation --
    and failed anyway: one collapsed `'\\\\'` to `'\\'` so node rejected the
    file, the other died on `unexpected EOF while looking for matching`.
    Whatever re-processing produces that, "I quoted the delimiter" is not
    evidence of safety, so this test does not treat it as such."""
    if not text or not text.strip():
        return None
    if "\n" in text.strip():
        return "spans more than one line"
    for marker, label in _BODY_MARKERS:
        if marker in text:
            return "contains {}".format(label)
    return None


def arg_hazard(raw):
    """Hazard test for a shell-QUOTED argument region -- an `echo`/`printf`
    argument list, a `sed -i` script, a `node -e` script, a PowerShell
    `-Value`. Unlike `body_hazard`, the quote characters here are structure,
    not content: `echo 'done' > f` must pass, so counting its delimiting
    quotes as hazards would be wrong.

    So this walks the argument's quote state and reports a marker only where
    the shell would actually act on it -- the general form of the principle,
    not a per-mechanism special case:

      - `'` while inside a double-quoted span, or backslash-escaped in
        unquoted context (`\\'`, the second half of the `'\\''` idiom) --
        a literal apostrophe the author had to fight the shell for. HAZARD.
      - `'` opening or closing a single-quoted span -- structure. Safe.
      - a backtick outside single quotes -- command substitution. HAZARD.
      - a backslash outside single quotes -- consumed as an escape. HAZARD.
      - a backslash INSIDE single quotes -- literal, untouched by the shell.
        Safe, which is what keeps the common `sed -i 's/a\\.b/c/'` idiom
        working while `sed -i "s/a\\\\b/c/"` (where the shell really does eat
        it, dev-env#1041 occurrence 1's shape) still blocks.

    A raw embedded newline is reported the same way `body_hazard` does."""
    if not raw or not raw.strip():
        return None
    if "\n" in raw.strip():
        return "spans more than one line"
    state = "top"  # "top" (unquoted/subshell), "single", or "double"
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if state == "single":
            if c == "'":
                state = "top"
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "'":
                return "contains an escaped apostrophe"
            return "contains a backslash"
        if c == "\\":
            return "contains a backslash"
        if c == "`":
            return "contains a backtick"
        if state == "double":
            if c == "'":
                return "contains an apostrophe"
            if c == '"':
                state = "top"
            i += 1
            continue
        if c == "'":
            state = "single"
        elif c == '"':
            state = "double"
        i += 1
    return None


# --- Destinations -----------------------------------------------------------

# Targets that consume content without creating a file, plus fd duplications
# (`>&1`, `>&2`) -- a redirect to any of these is not a file write at all.
_NON_FILE_TARGETS = ("/dev/null", "/dev/stdout", "/dev/stderr", "$null", "nul")


def is_file_target(target):
    """True iff *target* names a real file rather than a discard sink, a
    standard stream, or a file-descriptor duplication."""
    if not target:
        return False
    t = target.strip().strip("'\"")
    if not t or t.startswith("&"):
        return False
    return t.lower() not in _NON_FILE_TARGETS


_TEE_RE = re.compile(r"(?i)^tee(?![\w-])")


def find_write_destinations(line, masked, tool_name):
    """Return every genuine file-write destination on *line*: `>`/`>>`
    redirect targets (both shells -- PowerShell aliases `>` to `Out-File`),
    plus a Bash `tee [-a] <path>` invocation's own targets. Discard sinks
    and fd duplications are filtered out by `is_file_target`."""
    out = [t for _op, t in find_redirect_targets(line, masked=masked) if is_file_target(t)]
    # Cheap guard before the shlex walk: `tee` must be the segment's first token,
    # so a line that cannot start with it never pays for tokenization. This hook
    # runs on every Bash call fleet-wide, which is the same reason the module-level
    # pre-filter avoids a regex (ADR-129 Amendment 1 #11).
    if tool_name == "Bash" and _TEE_RE.match(line.lstrip()):
        tokens = tokenize_posix(line)
        if tokens and _TEE_RE.match(tokens[0]):
            out.extend(t for t in tokens[1:] if not t.startswith("-") and is_file_target(t))
    return out


# A CONTENT destination that is not a file: a command that publishes authored
# prose read from its own stdin. ADR-138 Amendment 1 -- v1's rule reached only
# an inline literal bound for a *file*, so `gh pr create --body-file - <<'EOF'`
# passed while the same body written via `cat > body.md <<'EOF'` blocked. That
# is not a principled distinction, and it inverted the incentive: the form the
# guard let through is the one that leaves no artifact behind to inspect when
# the shell mangles it.
#
# Anchored to a closed allowlist of commands AND flags, which is what keeps
# this from becoming the "any heredoc anywhere" widening v1 declined. An
# interpreter reading a PROGRAM from stdin (`py -3 - <<'PY'`) carries no such
# flag, so it is excluded STRUCTURALLY, not by a heuristic -- the same property
# that makes the inline-literal/program-output split safe. Measured: that
# interpreter-stdin class is 1041 of the 1198 gap commands (87%), and the Write
# tool is the wrong remedy for a program, so it stays an accepted gap.
_CONTENT_CMD_RE = re.compile(r"(?i)^(gh|git)(?=\s)")
# A leading `VAR=value ` assignment does not change which command runs -- real Bash
# applies it to the statement and then execs the next word. Skipping past it is what
# lets the OVERRIDE prefix reach the detector at all: without this,
# `ALLOW_SHELL_CONTENT_WRITE=1 gh pr create --body-file - <<EOF` never matched, so
# `_is_overridden` was never consulted for this mechanism and its override test
# passed vacuously (it asserted "allowed" against a detector that had already
# missed). Bounded to unquoted values, the only form either override token takes.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+")
# `--body-file -` / `--notes-file -` / `--file=-` (gh, git) and `-F -` / `-F-`
# (git commit/tag/notes). Deliberately NOT `gh api --input -`: a JSON payload is
# machine-generated, not authored prose, and pulling it in is exactly the
# false-positive surface ADR-138 named.
_STDIN_CONTENT_FLAG_RE = re.compile(
    r"(?i)(?:^|\s)(?:"
    r"(--body-file|--notes-file|--file)(?:\s*=\s*|\s+)(?:-|/dev/stdin)"
    r"|(-F)\s*(?:-|/dev/stdin)"
    r")(?=\s|$)"
)


def find_stdin_content_arg(line, masked):
    """Return the flag (e.g. `--body-file`) when *line* routes STDIN into a
    content-publishing command's body/message argument, else None.

    The command check is a cheap anchored regex on the raw line -- deliberately
    NOT `shlex.split`, which this hook pays on every Bash call fleet-wide. That
    is the same reasoning behind the `tee` detector's `startswith` guard (see
    ADR-138's post-review hardening): tokenizing merely to read the first word
    measured ~23% of the whole segment walk on a command that only happens to
    contain `<<`. Leading `VAR=value` assignments are skipped first, matching
    real Bash.

    The flag is matched against the MASKED line so a `--body-file -` sitting
    inside a quoted argument is not mistaken for a real one."""
    stripped = line.lstrip()
    while True:
        assignment = _ENV_ASSIGN_RE.match(stripped)
        if not assignment:
            break
        stripped = stripped[assignment.end():]
    if not _CONTENT_CMD_RE.match(stripped):
        return None
    m = _STDIN_CONTENT_FLAG_RE.search(masked)
    if not m:
        return None
    return m.group(1) or m.group(2)


# --- Inline-literal extraction ----------------------------------------------


def extract_heredoc_literal(segment):
    """Return (kind, body) for the FIRST genuine heredoc or here-string on
    *segment*'s first line, or None.

    A genuine (non-quoted) `<<` is located via `mask_first_line_quotes`,
    which rewrites every unquoted `<<` to `<#` -- length-preserving, so the
    offset it reports indexes straight back into the raw line. That is the
    same masking fix ADR-129 needed to see `cat <<'EOF' > f` at all; here it
    doubles as the "is this `<<` real, or is it inside a quote" test.

    Only the first opener is used. A single command line bearing two
    heredocs is vanishingly rare, and one hazardous body is enough to
    block -- a named, accepted boundary rather than an unnoticed one."""
    lines = segment.split("\n")
    raw_first = lines[0]
    masked = mask_first_line_quotes(raw_first)
    # The `<#` sentinel is what masking rewrites a genuine, unquoted `<<` into --
    # but `<#` is ALSO real syntax: it opens a PowerShell block comment. Confirming
    # the RAW line carries `<<` at the same offset (masking is length-preserving,
    # so offsets align) separates the two, and skipping past a literal `<#` means a
    # real `<<` later on the same line is still found rather than shadowed by it.
    idx = -1
    for m in re.finditer(r"<#", masked):
        if raw_first[m.start():m.start() + 2] == "<<":
            idx = m.start()
            break
    if idx == -1:
        return None
    rest = raw_first[idx + 2:]
    if rest.startswith("<"):
        # `<<<word` -- a here-string; its own single token is the literal.
        return ("here-string", next_token(rest[1:]))
    m = re.match(r"(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", rest)
    if not m:
        return None
    strip_tabs = m.group(1) == "-"
    delim = m.group(3)
    body = []
    for ln in lines[1:]:
        probe = ln.lstrip("\t") if strip_tabs else ln
        if probe.strip() == delim:
            break
        body.append(ln)
    return ("heredoc", "\n".join(body))


_ECHO_RE = re.compile(r"(?i)^(echo|printf)$")


def extract_echo_literal(line, masked):
    """Return (command, raw-argument-region) for an `echo`/`printf` whose
    arguments precede a redirect on *line*, or None.

    The region is taken RAW -- never tokenized -- because tokenizing would
    resolve exactly the quoting this hook exists to judge. `arg_hazard`
    does the quote-state walk instead.

    For `printf` carrying two or more arguments the first is dropped: it is
    the format string, whose `%s`/`\\n` are machine syntax rather than
    authored content, so `printf '%s\\n' "$X" > f` stays allowed. A lone
    `printf 'text' > f` argument IS the content and is kept."""
    stripped = line.lstrip()
    lead = stripped.split(None, 1)
    if not lead or not _ECHO_RE.match(lead[0]):
        return None
    offset = len(line) - len(stripped)
    cut = masked.find(">", offset)
    if cut == -1:
        return None
    region = line[offset + len(lead[0]):cut]
    if lead[0].lower() == "printf":
        parts = tokenize_powershell(region)
        if len(parts) >= 2:
            first_arg = parts[0]
            at = region.find(first_arg)
            if at != -1:
                region = region[at + len(first_arg):]
    return (lead[0], region)


_SERIALIZER_RE = re.compile(
    r"(?i)^(node\s+-e|py(?:thon)?(?:3(?:\.\d+)?)?(?:\s+-\d[\w.]*)?\s+-c)(?![\w-])"
)
# Substrings that mean the script writes a file. Deliberately a small,
# explicit list rather than a general "does this script do I/O" analysis --
# this hook does not parse JS or Python. A read-only `node -e` (the
# documented `jq` replacement in claude/CLAUDE.md -> Platform & Environment)
# matches none of these and is never blocked.
_WRITE_CALL_MARKERS = (
    "writefilesync",
    "appendfilesync",
    "createwritestream",
    "writefile(",
    "write_text",
    "write_bytes",
    "outputfile",
)
_OPEN_WRITE_RE = re.compile(r"(?i)open\s*\([^)]*['\"][wax]b?\+?['\"]")


def script_writes_a_file(script):
    """True iff *script* contains a recognized file-write call."""
    lowered = script.lower()
    if any(marker in lowered for marker in _WRITE_CALL_MARKERS):
        return True
    return bool(_OPEN_WRITE_RE.search(script))


def find_serializer_write(segment, has_destination):
    """Return (interpreter, raw-script-region) for a `node -e`/`py -3 -c`/
    `python[3] -c` segment that either writes a file itself or is redirected
    into one, or None.

    The script region is the segment's full remaining text (every physical
    line -- a `-e`/`-c` payload is opaque DATA to the shell and routinely
    spans lines), taken raw for `arg_hazard`. dev-env#1041 occurrence 2 was
    exactly this: an apostrophe in the word `user's` inside the script
    closed the enclosing single-quoted shell string."""
    m = _SERIALIZER_RE.match(first_line(segment).lstrip())
    if not m:
        return None
    stripped = segment.lstrip()
    script = stripped[m.end():]
    if not has_destination and not script_writes_a_file(script):
        return None
    return (m.group(1), script)


_INPLACE_RE = re.compile(r"(?i)^(sed|perl)$")
# Short clustered form: -i, -i.bak, -pi, -ni. The `[a-z]*` cannot cross a second
# `-`, so GNU sed's long form needs its own arm -- without it `sed --in-place`
# sailed through while `sed -i` blocked, an inconsistency inside the same
# documented scope.
_INPLACE_SHORT_RE = re.compile(r"(?i)^-[a-z]*i")
_INPLACE_LONG_RE = re.compile(r"(?i)^--in-place(=|$)")


def _is_inplace_flag(flag):
    """True iff *flag* requests an in-place edit, in either sed/perl spelling."""
    return bool(_INPLACE_SHORT_RE.match(flag) or _INPLACE_LONG_RE.match(flag))


def find_inplace_edit(line):
    """Return (command, raw-script-argument) for an in-place edit -- `sed -i`
    or `perl -pi -e` -- or None. No separate write destination is needed:
    an in-place edit's target IS the file, which is why the bypass-mode
    instruction naming `sed` sits squarely in this hook's scope.

    The script argument is located in the RAW line (a `-e` flag's value, else
    the first non-flag argument after the flags) so `arg_hazard` sees the
    author's own quoting. GNU `sed -i.bak` and clustered short flags
    (`-ni`, `-pi`) are both recognized."""
    stripped = line.lstrip()
    tokens = tokenize_powershell(stripped)
    if not tokens or not _INPLACE_RE.match(tokens[0]):
        return None
    flags = [t for t in tokens[1:] if t.startswith("-")]
    if not any(_is_inplace_flag(f) for f in flags):
        return None
    script_token = None
    for idx, tok in enumerate(tokens[1:]):
        if tok == "-e" and idx + 2 < len(tokens):
            script_token = tokens[idx + 2]
            break
    if script_token is None:
        for tok in tokens[1:]:
            if not tok.startswith("-"):
                script_token = tok
                break
    if script_token is None:
        return None
    at = stripped.find(script_token)
    raw = stripped[at:at + len(script_token)] if at != -1 else script_token
    return (tokens[0], raw)


_PS_CMDLET_RE = re.compile(r"(?i)^(Out-File|Set-Content|Add-Content|Tee-Object|New-Item)$")
_PS_VALUE_FLAG_RE = re.compile(r"(?i)^-Value$")
_PS_HERESTRING_RE = re.compile(r"@(['\"])\r?\n(.*?)\r?\n\1@", re.DOTALL)


def find_powershell_content_write(segment, upstream=None):
    """Return EVERY inline literal feeding a PowerShell content-write cmdlet on
    *segment*, as a list of (cmdlet, kind, literal) -- possibly empty.

    Returning a list rather than the first hit is load-bearing: an upstream
    here-string and a `-Value` on the same cmdlet are independent literals, and
    returning only the here-string let a SAFE one mask a genuinely hazardous
    `-Value` downstream of it (`@'...safe...'@ | Set-Content a.md;
    Set-Content b.md -Value "it's hazardous"` reported nothing, while the same
    `-Value` alone blocked correctly). The caller evaluates each candidate and
    blocks on the first hazardous one, so one benign literal can no longer
    suppress a hazardous sibling.

    Two literal shapes: a `-Value` argument (judged by `arg_hazard`) and a
    `@'...'@` here-string (judged by `body_hazard`, since a here-string body
    is raw content, not quoted structure). A pipeline whose input is a
    command (`Get-Process | Out-File f`) reaches neither and is never
    matched -- program output, the same carve-out as Bash.

    *upstream* is the text of every earlier pipeline segment plus this one.
    The canonical here-string form pipes INTO the cmdlet
    (`@'...'@ | Set-Content f`), so `segments_or_whole`'s pipe splitting puts
    the literal and its cmdlet in different segments -- searching only this
    segment misses the whole shape. Scoped to upstream segments rather than
    the entire command so a here-string appearing AFTER the cmdlet, in an
    unrelated later statement, cannot be misattributed to it.

    Anchored to the segment's first token, matching the ADR-129 sibling's
    fix for a cmdlet-shaped word appearing mid-line as an argument."""
    tokens = tokenize_powershell(first_line(segment).lstrip())
    if not tokens or not _PS_CMDLET_RE.match(tokens[0]):
        return []
    out = []
    here = _PS_HERESTRING_RE.search(upstream if upstream is not None else segment)
    if here:
        out.append((tokens[0], "here-string", here.group(2)))
    for idx, tok in enumerate(tokens[1:]):
        if _PS_VALUE_FLAG_RE.match(tok) and idx + 2 <= len(tokens) - 1:
            out.append((tokens[0], "value", tokens[idx + 2]))
            break
    return out


# --- Top-level combination --------------------------------------------------

OVERRIDE_VAR_NAME = "ALLOW_SHELL_CONTENT_WRITE"
OVERRIDE_TOKEN = OVERRIDE_VAR_NAME + "=1"
# ADR-129's own override, honoured here too: a deliberate journal write that
# clears that hook must not then be blocked by this one.
JOURNAL_OVERRIDE_VAR_NAME = "ALLOW_JOURNAL_SHELL_WRITE"
JOURNAL_OVERRIDE_TOKEN = JOURNAL_OVERRIDE_VAR_NAME + "=1"

# Cheap, NECESSARY-but-not-sufficient pre-filter. Plain `in` checks against
# one `.lower()` call, NOT a compiled regex -- ADR-129 Amendment 1 finding
# #11 measured a lookaround-anchored regex at ~11.7ms against ~0.28ms for
# plain substring checks on the same 100k-char input, i.e. SLOWER than the
# full walk it was meant to short-circuit. Deliberately not word-boundary
# precise ("py" also matches inside "copy"): a false "maybe" costs only the
# always-correct full walk on an input with no real match.
_PREFILTER_MARKERS = (
    "tee", "node", "py", "sed", "perl",
    "out-file", "set-content", "add-content", "tee-object", "new-item",
    # `<<` -- required by the stdin-content-arg detector (ADR-138 Amendment 1),
    # whose shapes (`gh pr create --body-file - <<'EOF'`) carry NO redirect and
    # none of the markers above, so without this they never reach the walk.
    "<<",
)


def might_write_content(cmd):
    """True iff *cmd* carries at least one lexical marker some detector
    structurally requires. False means no detector below can match, so the
    whole segment walk is safely skippable -- every branch this rules out is
    ruled out identically, but more expensively, by the real detectors."""
    if ">" in cmd:
        return True
    lowered = cmd.lower()
    return any(marker in lowered for marker in _PREFILTER_MARKERS)


def find_content_writes(cmd, tool_name, segments=None):
    """Return every hazardous inline-literal content write across *cmd*'s
    top-level segments, as a list of dicts: {"segment", "segment_index",
    "mechanism", "detail", "target", "reason"}.

    Every mechanism follows the same two-part shape -- an inline literal AND
    a file destination -- with two deliberate exceptions that carry their
    own destination: an in-place edit (the file IS the target) and a
    PowerShell content cmdlet (the cmdlet names its own path). A match is
    emitted only when the literal is hazardous, so a short, clean literal
    (`echo done > flag.txt`) passes at this layer, not by override."""
    segments = segments_or_whole(cmd, segments)
    out = []
    for idx, seg in enumerate(segments):
        line = first_line(seg)
        masked = mask_first_line_quotes(line)
        targets = find_write_destinations(line, masked, tool_name)
        target = targets[0] if targets else None

        # Checked BEFORE the file-destination arms: when a command carries both
        # a stdin content flag and an unrelated redirect, the flag is the more
        # accurate description of where the literal is actually going.
        content_flag = find_stdin_content_arg(line, masked)
        if content_flag:
            heredoc = extract_heredoc_literal(seg)
            if heredoc:
                reason = body_hazard(heredoc[1])
                if reason:
                    out.append({
                        "segment": seg.strip(), "segment_index": idx,
                        "mechanism": "stdin-content-arg", "detail": content_flag,
                        "target": "(stdin of {})".format(content_flag),
                        "reason": reason,
                    })
                    continue

        if target:
            heredoc = extract_heredoc_literal(seg)
            if heredoc:
                reason = body_hazard(heredoc[1])
                if reason:
                    out.append({
                        "segment": seg.strip(), "segment_index": idx,
                        "mechanism": "heredoc", "detail": heredoc[0],
                        "target": target, "reason": reason,
                    })
                    continue
            echoed = extract_echo_literal(line, masked)
            if echoed:
                reason = arg_hazard(echoed[1])
                if reason:
                    out.append({
                        "segment": seg.strip(), "segment_index": idx,
                        "mechanism": "inline-literal", "detail": echoed[0],
                        "target": target, "reason": reason,
                    })
                    continue

        serializer = find_serializer_write(seg, bool(target))
        if serializer:
            reason = arg_hazard(serializer[1])
            if reason:
                out.append({
                    "segment": seg.strip(), "segment_index": idx,
                    "mechanism": "serializer", "detail": serializer[0],
                    "target": target or "(the script's own write call)",
                    "reason": reason,
                })
                continue

        inplace = find_inplace_edit(line)
        if inplace:
            reason = arg_hazard(inplace[1])
            if reason:
                out.append({
                    "segment": seg.strip(), "segment_index": idx,
                    "mechanism": "in-place-edit", "detail": inplace[0],
                    "target": "(edited in place)", "reason": reason,
                })
                continue

        if tool_name == "PowerShell":
            upstream = "\n".join(segments[: idx + 1])
            for cmdlet, kind, literal in find_powershell_content_write(seg, upstream=upstream):
                reason = body_hazard(literal) if kind == "here-string" else arg_hazard(literal)
                if reason:
                    out.append({
                        "segment": seg.strip(), "segment_index": idx,
                        "mechanism": "powershell-cmdlet", "detail": cmdlet,
                        "target": target or "(the cmdlet's own -Path)",
                        "reason": reason,
                    })
                    break
    return out


def _segment_has_bash_override(segment):
    """True iff either override token prefixes *segment* itself -- never a
    mere substring elsewhere. Mirrors real Bash `VAR=1 cmd` scoping: the
    assignment applies only to the statement it prefixes, so this is checked
    against the one segment a match was found in."""
    stripped = segment.strip()
    for token in (OVERRIDE_TOKEN, JOURNAL_OVERRIDE_TOKEN):
        if stripped == token or stripped.startswith(token + " "):
            return True
    return False


_PS_OVERRIDE_RE = re.compile(
    r"(?i)^\$env:(?:"
    + re.escape(OVERRIDE_VAR_NAME) + r"|" + re.escape(JOURNAL_OVERRIDE_VAR_NAME)
    + r")\s*=\s*'?1'?\s*$"
)


def _segment_is_ps_override_statement(segment):
    """True iff *segment*, stripped, is a standalone PowerShell `$env:<token>=1`
    assignment for either override variable."""
    return bool(_PS_OVERRIDE_RE.match(segment.strip()))


def _is_overridden(segments, segment_index):
    """True iff the match at `segments[segment_index]` is overridden. The two
    forms are deliberately asymmetric, matching each shell: a Bash `VAR=1`
    prefix counts only on the matched segment itself, while a PowerShell
    `$env:` assignment is its own statement that persists forward, so any
    such statement at or before the match counts."""
    if _segment_has_bash_override(segments[segment_index]):
        return True
    for seg in segments[: segment_index + 1]:
        if _segment_is_ps_override_statement(seg):
            return True
    return False


# --- Block message -----------------------------------------------------------

BLOCK_MESSAGE = """[shell-content-write-guard] BLOCKED: this command writes file content supplied as an inline literal in the command string, and that literal {reason}.

Content routed through a shell gets parsed by the shell first: an apostrophe closes a single-quoted string early, a backtick runs a command, a backslash is eaten as an escape. That either fails the write outright (one wasted turn) or silently corrupts the file (dev-env#1041 occurrence 1 wrote a script that looked fine until node rejected it). Quoting the heredoc delimiter is NOT a reliable mitigation -- two of the three occurrences behind this rule used exactly that form and failed anyway.

  Mechanism: {mechanism} ({detail})
  Command  : {segment}
  Target   : {target}

{remedy}

Redirecting another program's OUTPUT (`gh ... > "$TMPFILE"`, `npm test > out.log`) is never blocked -- no literal crosses a quoting boundary there.

See claude/CLAUDE.md -> Authoring File Content, and ADR-138.

Genuine exception? Bash: prefix the command with {override}
                    PowerShell: precede it with its own statement, $env:{override_var}=1"""

_MECHANISM_LABELS = {
    "heredoc": "a heredoc body redirected to a file",
    "inline-literal": "an inline literal redirected to a file",
    "serializer": "an inline interpreter script that writes a file",
    "in-place-edit": "an in-place edit script",
    "powershell-cmdlet": "a PowerShell content-write cmdlet",
    "stdin-content-arg": "a heredoc body piped into a command's content argument",
}

_DEFAULT_REMEDY = """Use a file tool instead:
  - New file, or replacing it wholesale -> Write tool, full content in one call.
  - Existing file, a targeted change -> Edit tool."""

# The default remedy ("use Write/Edit") does not name a destination for a
# stdin-fed body, so this mechanism gets its own -- write the body to a file,
# then pass that path in place of `-`.
_MECHANISM_REMEDIES = {
    "stdin-content-arg": """Write the body to a file, then pass that path instead of `-`:
  - Write tool -> "$TMPFILE" (full content, one call), then re-run with {detail} "$TMPFILE".

An interpreter reading a PROGRAM from stdin (`py -3 - <<'PY'`, `python - <<'EOF'`) is NOT blocked -- only a command's authored-prose argument is.""",
}


def _build_block_message(match):
    remedy = _MECHANISM_REMEDIES.get(match["mechanism"], _DEFAULT_REMEDY)
    return BLOCK_MESSAGE.format(
        reason=match["reason"],
        mechanism=_MECHANISM_LABELS.get(match["mechanism"], match["mechanism"]),
        detail=match["detail"],
        segment=match["segment"],
        target=match["target"],
        remedy=remedy.format(detail=match["detail"]),
        override=OVERRIDE_TOKEN,
        override_var=OVERRIDE_VAR_NAME,
    )


# --- main --------------------------------------------------------------------

def main():
    _hookutil.record_heartbeat("pre-tool-use-shell-content-write-guard")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_name = data.get("tool_name")
    if tool_name not in ("Bash", "PowerShell"):
        sys.exit(0)

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)
    cmd = tool_input.get("command", "")
    if not cmd or not isinstance(cmd, str):
        sys.exit(0)
    if not might_write_content(cmd):
        sys.exit(0)

    segments = segments_or_whole(cmd)
    matches = find_content_writes(cmd, tool_name, segments=segments)
    if not matches:
        sys.exit(0)
    blocking_match = next(
        (m for m in matches if not _is_overridden(segments, m["segment_index"])),
        None,
    )
    if blocking_match is None:
        sys.exit(0)

    _hookout.emit_block(_build_block_message(blocking_match))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
