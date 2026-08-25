#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- blocks a Bash/PowerShell command that would
write content to an engineering-journal content file (stub `.md`, manifest
`.jsonl`, open-PR `.json`, tile `.json`) via a shell mechanism (redirect,
heredoc, `tee`/`Tee-Object`, a `node -e`/`py -c` serializer, or a PowerShell
content-write cmdlet) instead of the Write/Edit tool. All four file kinds
carry prose or a path field that routinely breaks shell quoting, either
failing the write outright or silently corrupting the file -- see ADR-129
and dev-env#961/#904 for the full incident history and rationale this
docstring does not repeat; `journal-shard-write-advisory.py` (PostToolUse)
remains the complementary schema check on an already-written file, not a
replacement for blocking the write attempt itself.

Detection contract (see each function below for its own mechanism -- this
is the aggregate, not a restatement):
  - `find_bash_redirect_targets` -- a genuine `>`/`>>` on a segment's first
    physical line. Applies to both tool_name values (PowerShell supports
    `>` natively too).
  - `find_tee_targets` -- a `tee [-a] <path>` invocation. Gated on
    tool_name == "Bash".
  - `find_serializer_journal_mentions` -- a `node -e`/`py -c` segment
    MENTIONING a journal-shaped path anywhere in its full text (not just
    the first line) -- coarser by design; see its own docstring.
  - `find_powershell_write_targets` -- one of five named cmdlets, anchored
    to segment-start. Gated on tool_name == "PowerShell".
  - Every match above is additionally filtered through
    `_target_is_genuinely_journal` (a `sessions/` path component in the
    target, or *cwd* resolving under the engineering-journal checkout)
    before being reported, and through `_might_write_journal_content` (a
    cheap pre-filter) before any of the above runs at all.
  - `_is_overridden` -- a genuine `ALLOW_JOURNAL_SHELL_WRITE=1` Bash prefix
    (scoped to its own segment) or `$env:ALLOW_JOURNAL_SHELL_WRITE=1`
    PowerShell statement (applies forward, matching real PowerShell
    semantics) exempts a match; `main()` blocks on the first match that
    isn't.
  - Blocks (exit 2) via `_hookout.emit_block` (ADR-103's current
    convention -- NOT this hook's two closest structural siblings'
    hand-rolled `sys.stderr.write(json.dumps(...))`, a pre-`_hookout`
    -migration pattern a new hook must not join). Fails open (exit 0)
    everywhere else, including on any internal exception -- not one of the
    two specially fail-closed gates in this repo
    (`pre-auto-merge-checkpoint-gate.py`,
    `pre-tool-use-journal-compose-force-guard.py`). No `_winsubp` import:
    unlike its git-context-resolving siblings, this hook spawns no
    subprocess at all -- every question it answers comes from the command
    text alone.

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

# Shell-syntax primitives shared with `pre-tool-use-shell-content-write-guard.py`
# (ADR-138). Extracted from this file as a pure move -- imported under their
# original private names here so every call site below, and this hook's own
# 63-case suite, are unchanged by the extraction. `find_bash_redirect_targets`
# keeps its original public name because that suite calls it directly.
from _shell_write_detect import (
    find_redirect_targets as find_bash_redirect_targets,
    first_line as _first_line,
    mask_first_line_quotes as _mask_first_line_quotes,
    segments_or_whole as _segments_or_whole,
    tokenize_posix as _tokenize_posix,
    tokenize_powershell as _tokenize_line,
)
import _hookout
import _hookutil

# --- Path-shape classification --------------------------------------------

_STUB_RE = re.compile(r"(?:^|[/\\])[^/\\]+\.stub\.md$", re.IGNORECASE)
_MANIFEST_RE = re.compile(r"(?:^|[/\\])[^/\\]+\.manifest\.jsonl$", re.IGNORECASE)
_OPEN_PR_RE = re.compile(r"(?:^|[/\\])open-prs[/\\]\d+\.json$", re.IGNORECASE)
_TILE_RE = re.compile(r"(?:^|[/\\])tiles[/\\]\d+\.json$", re.IGNORECASE)

_JOURNAL_PATH_KINDS = (
    ("stub", _STUB_RE),
    ("manifest", _MANIFEST_RE),
    ("open-pr", _OPEN_PR_RE),
    ("tile", _TILE_RE),
)

# Human-readable label per kind, for the block message.
_KIND_LABELS = {
    "stub": "stub .md",
    "manifest": "manifest .jsonl shard",
    "open-pr": "open-PR .json shard",
    "tile": "tile .json shard",
}


def journal_path_kind(token):
    """Classify *token* (a raw command-line word, possibly quoted) against
    the four journal content-file path shapes. Returns the kind name, or
    None if it doesn't match any of them. Purely lexical/shape-based --
    see `_target_is_genuinely_journal` for the additional sessions/-or-cwd
    check `find_journal_shell_writes` applies on top of this before a
    shape match is treated as a genuine hazard."""
    t = token.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        t = t[1:-1]
    for kind, pattern in _JOURNAL_PATH_KINDS:
        if pattern.search(t):
            return kind
    return None


_SESSIONS_COMPONENT_RE = re.compile(r"(?:^|[/\\])sessions(?:[/\\]|$)", re.IGNORECASE)
_ENGINEERING_JOURNAL_CWD_RE = re.compile(r"(?:^|[/\\])engineering-journal(?:[/\\]|$)", re.IGNORECASE)


def _target_is_genuinely_journal(target, cwd):
    """A target already classified as journal-shaped by `journal_path_kind`
    still needs one more check before it's treated as a genuine hazard:
    all four path shapes are lexically distinctive but not UNIQUE to the
    engineering journal -- `*.manifest.jsonl` is an established ML/data
    convention outside this repo, and `tiles/<digits>.json` is a plausible
    game-asset-pipeline output. Blocking on shape alone false-blocked an
    entirely unrelated repo's command, e.g. `python gen.py >
    data/train.manifest.jsonl` -- verified live, dev-env#962 review.

    Requiring a `sessions/` path component in the target itself covers the
    common case, where the target already names its `sessions/<project>/`
    directory. A target issued as a bare relative path (cwd already inside
    `sessions/<project>/`) has no such component, so this also accepts a
    *cwd* that itself resolves under the engineering-journal checkout --
    exactly the case `journal_path_kind`'s own docstring/ADR-129 cite as
    the reason path matching stays lexical rather than requiring a
    `sessions/` prefix outright. Lexical only, no filesystem access --
    consistent with this hook's whole no-I/O design; a *cwd* that merely
    LOOKS like the engineering-journal checkout (rather than genuinely
    being it) is treated the same as the real thing, which only widens
    the allowed set, never narrows the blocked one."""
    if _SESSIONS_COMPONENT_RE.search(target):
        return True
    return bool(cwd) and bool(_ENGINEERING_JOURNAL_CWD_RE.search(cwd))


# --- Segment-local helpers --------------------------------------------------
#
# `_first_line`, `_neutralize_unquoted_escaped_quotes`, `_mask_first_line_quotes`,
# `_next_token`, `find_bash_redirect_targets`, `_tokenize_posix`, `_tokenize_line`,
# and `_segments_or_whole` now live in `_shell_write_detect.py` (imported above).
# They were extracted as a pure move so ADR-138's sibling guard shares them
# rather than hand-copying quote-state logic that Amendment 1 findings #1 and #5
# already had to fix once. Their docstrings -- and the reasoning behind every
# branch -- moved with them.


# --- tee + retired-serializer-invocation detection --------------------------
#
# Two shapes the redirect/cmdlet detectors above cannot see at all:
#
# 1. Bash `tee [-a] <path>` -- the direct Bash equivalent of PowerShell's
#    `Tee-Object` (already detected above), but `tee` itself was missing
#    entirely from the original design.
# 2. A `node -e "..."` / `py -3 -c '...'` / `python[3] -c '...'` serializer
#    invocation -- the literal retired recipe ADR-129 replaces, and the
#    exact shape of the dev-env#904 incident that motivated this whole
#    hook (`node -e "...fs.writeFileSync('sessions/.../tiles/961.json'...)"`
#    corrupted a tile shard's `cwd` field). Every detector above only ever
#    inspects a segment's first physical line, by design (a heredoc/here
#    -string BODY must never be mistaken for invocation syntax) -- but
#    these recipes place their hazardous path argument on a LATER physical
#    line, inside the -e/-c script text itself, which is real DATA to a
#    shell (an opaque string argument), not more shell syntax. Detecting
#    it needs its own, deliberately coarser mechanism: scan the interpreter
#    invocation's FULL segment text (every physical line) for any mention
#    of a journal-shaped path, rather than pinpointing one exact write
#    target the way the redirect/cmdlet detectors do -- this hook cannot
#    parse arbitrary JS/Python to confirm a mention is really a write
#    call's argument. Verified live, dev-env#962 review: without this, the
#    exact recipes this PR's own documentation retires were not blocked.

_TEE_RE = re.compile(r"(?i)^tee(?![\w-])")


def find_tee_targets(first_line):
    """Return [(target, kind)] -- at most one entry -- for a genuine Bash
    `tee [-a] <path> [<path> ...]` invocation anchored at the START of
    *first_line*. `tee` can name more than one output file; every
    non-flag token is checked, not just the first, so `tee other.txt
    sessions/.../x.stub.md` is still caught regardless of argument
    order."""
    tokens = _tokenize_posix(first_line)
    if not tokens or not _TEE_RE.match(tokens[0]):
        return []
    for tok in tokens[1:]:
        if tok.startswith("-"):
            continue
        kind = journal_path_kind(tok)
        if kind:
            return [(tok, kind)]
    return []


# `py(?:thon)?(?:3(?:\.\d+)?)?` matches py / python / py3 / python3 /
# python3.11; the optional `(?:\s+-\d[\w.]*)?` group separately matches
# this repo's own `py -3 -c` convention (a SEPARATE `-3` version flag
# before `-c`, not fused into the interpreter name the way "py3" is).
_SERIALIZER_INTERPRETER_RE = re.compile(
    r"(?i)^(node\s+-e|py(?:thon)?(?:3(?:\.\d+)?)?(?:\s+-\d[\w.]*)?\s+-c)(?![\w-])"
)
# Any run of non-whitespace, non-quote, non-paren/comma/semicolon
# characters -- deliberately loose, since this only needs to isolate
# candidate WORDS to test against journal_path_kind(), not tokenize the
# interpreter's own script syntax (which could be JS or Python).
_SERIALIZER_WORD_RE = re.compile(r"[^\s\"'(),;]+")


def find_serializer_journal_mentions(segment):
    """Return [(interpreter, word, kind)] -- at most one entry -- for a
    `node -e`/`py -3 -c`/`python[3] -c` segment that MENTIONS a
    journal-shaped path anywhere in its full text (every physical line,
    not just the first). Deliberately coarser than the redirect/cmdlet
    detectors: it flags any journal-shaped mention rather than confirming
    the mention is really a write call's argument, since parsing
    arbitrary JS/Python is out of scope here. This is an intentional,
    documented tradeoff -- these recipes are fully retired per ADR-129,
    dev-env#904's own incident was exactly this shape, and the cost of a
    false positive is only an inconvenient block (with the override token
    still available), not a missed hazard."""
    m = _SERIALIZER_INTERPRETER_RE.match(_first_line(segment).lstrip())
    if not m:
        return []
    for word in _SERIALIZER_WORD_RE.findall(segment):
        kind = journal_path_kind(word)
        if kind:
            return [(m.group(1), word, kind)]
    return []


# --- PowerShell cmdlet detection --------------------------------------------

_PS_WRITE_CMDLET_RE = re.compile(r"(?i)(?<![\w-])(Out-File|Set-Content|Add-Content|Tee-Object)(?![\w-])")
_PS_NEW_ITEM_RE = re.compile(r"(?i)(?<![\w-])New-Item(?![\w-])")
_PS_VALUE_FLAG_RE = re.compile(r"(?i)(?<![\w-])-Value(?![\w-])")
_PS_PATH_FLAG_RE = re.compile(r"(?i)^-(Path|LiteralPath|FilePath)$")


def find_powershell_write_targets(first_line, masked=None):
    """Return [(cmdlet, target, kind), ...] -- at most one entry -- for a
    genuine PowerShell content-write invocation anchored at the START of
    *first_line* (its first token, after tokenizing).

    *masked* -- an already-computed `_mask_first_line_quotes(first_line)`
    -- lets the New-Item `-Value`-flag check below reuse
    `find_bash_redirect_targets`'s mask instead of recomputing it (see
    that function's docstring). Computed here if not supplied.

    Anchoring to
    segment-start (rather than searching the whole line for a cmdlet-name
    substring) is what makes tool_name gating in `find_journal_shell_writes`
    fully effective: a Bash command whose argument merely CONTAINS a
    cmdlet-shaped word (`rg Add-Content sessions/.../tiles/961.json`, a
    grep pattern) is never PowerShell to begin with, but an unanchored
    search would still misdetect it as one if this function were ever
    called on Bash input -- verified live, dev-env#962 review. `New-Item`
    counts only alongside a `-Value` flag (bare `New-Item -ItemType
    Directory ...` is scaffolding, not a content-write).

    Target selection is restricted to the cmdlet's actual BOUND path
    argument: the value of a `-Path`/`-LiteralPath`/`-FilePath` flag if one
    is present anywhere in the invocation, else the first positional
    (non-flag) argument after the cmdlet. This is a deliberate heuristic,
    not full PowerShell parameter-binding -- it does not handle every
    legal reordering of named parameters, but it removes the prior
    design's real bug: treating ANY journal-shaped token anywhere on the
    line as a target, which false-blocked a quoted log message merely
    mentioning a path (`Set-Content log.txt "wrote .../tiles/54.json"`)
    and a legitimate read inside a `-Value` sub-expression
    (`Set-Content -Path backup.json -Value (Get-Content .../tiles/961.json
    -Raw)`) -- both verified live, dev-env#962 review. Because target
    selection no longer scans `-Value`'s own payload at all, the previous
    single-token "skip the word right after -Value" guard is no longer
    needed as a separate step -- it falls out of only ever consulting the
    bound path argument."""
    tokens = _tokenize_line(first_line)
    if not tokens:
        return []
    cmdlet_token = tokens[0]
    is_write_cmdlet = bool(_PS_WRITE_CMDLET_RE.fullmatch(cmdlet_token))
    is_new_item = bool(_PS_NEW_ITEM_RE.fullmatch(cmdlet_token))
    if is_new_item:
        if masked is None:
            masked = _mask_first_line_quotes(first_line)
        if not _PS_VALUE_FLAG_RE.search(masked):
            return []
    elif not is_write_cmdlet:
        return []

    rest = tokens[1:]
    target = None
    for idx, tok in enumerate(rest):
        if _PS_PATH_FLAG_RE.match(tok) and idx + 1 < len(rest):
            target = rest[idx + 1]
            break
    if target is None:
        skip_next = False
        for tok in rest:
            if skip_next:
                skip_next = False
                continue
            if tok.startswith("-"):
                skip_next = True
                continue
            target = tok
            break
    if target is None:
        return []

    kind = journal_path_kind(target)
    if not kind:
        return []
    return [(cmdlet_token, target, kind)]


# --- Top-level combination --------------------------------------------------

OVERRIDE_VAR_NAME = "ALLOW_JOURNAL_SHELL_WRITE"
OVERRIDE_TOKEN = OVERRIDE_VAR_NAME + "=1"

# Cheap, NECESSARY-but-not-sufficient pre-filter -- see _might_write_journal_content.
# Deliberately plain `in` checks, NOT a compiled regex: a first attempt using
# a regex with lookaround word-boundary assertions (to avoid "py" matching
# inside "copy"/"empty") measured SLOWER than the full detection walk it was
# meant to short-circuit (~11.7ms vs ~6.5ms on a 100k-char no-match command --
# Python's `re` doesn't optimize an alternation-of-lookarounds into a fast
# literal scan the way a plain substring search is). Plain `in` checks
# against one `.lower()` call measured ~0.28ms on the same input -- roughly
# 23x faster than the full walk, not the false-precision word-boundary
# version. Verified live, dev-env#962 review -- benchmark before trusting a
# regex "should be fast" intuition.
_PREFILTER_MARKERS = ("tee", "node", "py", "out-file", "set-content", "add-content", "new-item")


def _might_write_journal_content(cmd):
    """True iff *cmd* contains at least one lexical marker every detector
    in this module structurally requires: a `>` character (bash-redirect),
    the substring `tee` (bash-tee), `node`/`py` (covers `python`/`py3`/
    `python3` too, since they all contain `py`) (serializer-mention), or
    one of the five PowerShell cmdlet names. False means NO detector below
    can possibly match, so the full `split_top_level` + per-segment
    quote-masking walk is safely skippable entirely -- every branch this
    rules out is also ruled out, more expensively but identically, by the
    real detectors, so this can never cause a missed block. Deliberately
    NOT word-boundary-anchored (so "py" also matches inside "copy"/
    "empty"): a false-positive "maybe" here only costs doing the full,
    always-correct walk on an input that turns out to have no real match
    -- no worse than this filter not existing at all for that one input --
    while an anchored regex measured slower than the thing it's meant to
    speed up (see the module-level comment above). This only saves the
    overwhelmingly common case (a Bash/PowerShell call using none of these
    constructs at all) from paying the full walk's cost on every single
    tool call fleet-wide."""
    if ">" in cmd:
        return True
    lowered = cmd.lower()
    return any(marker in lowered for marker in _PREFILTER_MARKERS)


def find_journal_shell_writes(cmd, tool_name, cwd=None, segments=None):
    """Return every shell-based journal-content-file write detected across
    *cmd*'s top-level segments, as a list of dicts:
    {"segment", "mechanism", "operator_or_cmdlet", "target", "kind"}.

    *tool_name* gates the PowerShell-cmdlet detector: it only ever runs
    when *tool_name* is "PowerShell". A Bash command whose argument merely
    CONTAINS a cmdlet-shaped word (`rg Add-Content sessions/.../tiles/961.json`,
    a grep pattern) is not itself a PowerShell invocation, and running
    that detector against Bash input misdetected it as one -- verified
    live, dev-env#962 review. The Bash-redirect detector is NOT gated the
    same way: PowerShell natively supports `>`/`>>` as aliases for
    `Out-File`/`Out-File -Append`, so it applies for both tool_name
    values.

    *cwd* feeds `_target_is_genuinely_journal`'s sessions/-or-cwd check --
    every shape match is filtered through it before being reported, so a
    same-shaped file in an unrelated repo (no `sessions/` component in its
    own path, and a *cwd* that isn't the engineering-journal checkout)
    never reaches the caller as a match at all.

    The `tee` detector is gated on *tool_name* == "Bash", symmetric with
    the PowerShell-cmdlet gate above (`tee` is Bash's own equivalent of
    `Tee-Object`). The serializer-mention detector (`node -e`/`py -3 -c`)
    is NOT gated by tool_name -- either shell can launch an external
    interpreter the same way `>`/`>>` redirection works from both.

    Each returned dict also carries `"segment_index"` -- the position of
    its segment within *segments* -- so `_is_overridden` can scope an
    override check to the segment (and, for the PowerShell `$env:` form,
    the segments up to and including it) that actually produced the
    match, rather than the whole command.

    The quote-mask for each segment's first line is computed exactly
    once here and shared with both `find_bash_redirect_targets` (always
    needed) and `find_powershell_write_targets`'s New-Item check (needed
    only sometimes), rather than each detector recomputing it
    independently -- measured ~35% of detector time as pure duplicate
    work before this sharing, dev-env#962 review."""
    segments = _segments_or_whole(cmd, segments)
    out = []
    for idx, seg in enumerate(segments):
        line = _first_line(seg)
        masked = _mask_first_line_quotes(line)
        for op, target in find_bash_redirect_targets(line, masked=masked):
            kind = journal_path_kind(target)
            if kind and _target_is_genuinely_journal(target, cwd):
                out.append({
                    "segment": seg.strip(),
                    "segment_index": idx,
                    "mechanism": "bash-redirect",
                    "operator_or_cmdlet": op,
                    "target": target,
                    "kind": kind,
                })
        if tool_name == "Bash":
            for target, kind in find_tee_targets(line):
                if _target_is_genuinely_journal(target, cwd):
                    out.append({
                        "segment": seg.strip(),
                        "segment_index": idx,
                        "mechanism": "bash-tee",
                        "operator_or_cmdlet": "tee",
                        "target": target,
                        "kind": kind,
                    })
        for interpreter, word, kind in find_serializer_journal_mentions(seg):
            if _target_is_genuinely_journal(word, cwd):
                out.append({
                    "segment": seg.strip(),
                    "segment_index": idx,
                    "mechanism": "serializer-invocation",
                    "operator_or_cmdlet": interpreter,
                    "target": word,
                    "kind": kind,
                })
        if tool_name == "PowerShell":
            for cmdlet, target, kind in find_powershell_write_targets(line, masked=masked):
                if _target_is_genuinely_journal(target, cwd):
                    out.append({
                        "segment": seg.strip(),
                        "segment_index": idx,
                        "mechanism": "powershell-cmdlet",
                        "operator_or_cmdlet": cmdlet,
                        "target": target,
                        "kind": kind,
                    })
    return out


def _segment_has_bash_override(segment):
    """True iff OVERRIDE_TOKEN appears as a genuine leading prefix on
    *segment* itself -- never a mere substring (e.g. mentioned inside a
    commit message argument). Mirrors real Bash `VAR=1 cmd` semantics:
    the assignment applies only to the single statement it prefixes, so
    this is checked against the ONE segment a match was found in, not the
    whole command -- an override on an unrelated earlier `&&`/`;`/`|`
    segment must not exempt a later, different segment's hazard.
    Verified live, dev-env#962 review:
    `ALLOW_JOURNAL_SHELL_WRITE=1 echo a > ok.txt && echo b > sessions/.../x.stub.md`
    used to bypass the block on the SECOND, unrelated segment."""
    stripped = segment.strip()
    return stripped == OVERRIDE_TOKEN or stripped.startswith(OVERRIDE_TOKEN + " ")


_PS_OVERRIDE_RE = re.compile(r"(?i)^\$env:" + re.escape(OVERRIDE_VAR_NAME) + r"\s*=\s*'?1'?\s*$")


def _segment_is_ps_override_statement(segment):
    """True iff *segment*, stripped, is a standalone PowerShell
    `$env:ALLOW_JOURNAL_SHELL_WRITE=1` (or `='1'`) assignment. The
    documented override token has no PowerShell equivalent at all --
    verified live, dev-env#962 review -- yet 5 of the 6 mechanisms this
    hook detects are PowerShell-exclusive, leaving no working escape
    hatch for most of what it blocks."""
    return bool(_PS_OVERRIDE_RE.match(segment.strip()))


def _is_overridden(segments, segment_index):
    """True iff the match found at `segments[segment_index]` is
    overridden: either that segment itself carries the Bash `VAR=1`
    prefix (real Bash scopes this to the one statement it prefixes, so
    only the matched segment itself counts), OR any segment AT OR BEFORE
    `segment_index` is a standalone PowerShell `$env:...=1` assignment
    statement. The two forms are deliberately NOT symmetric: a
    PowerShell `$env:` assignment is its own statement that sets the
    variable for the rest of the script (real PowerShell semantics, not
    scoped to one following command the way Bash's prefix is) -- so a
    `$env:ALLOW_JOURNAL_SHELL_WRITE=1; Out-File sessions/...` two
    -statement command genuinely IS overridden by real PowerShell rules,
    and checking only the matched segment itself would incorrectly
    reject that override (the assignment and the write are always
    different segments once split on `;`, since `$env:X=1 Out-File ...`
    with no separator is not valid PowerShell syntax to begin with)."""
    if _segment_has_bash_override(segments[segment_index]):
        return True
    for seg in segments[: segment_index + 1]:
        if _segment_is_ps_override_statement(seg):
            return True
    return False


# --- Block message -----------------------------------------------------------

BLOCK_MESSAGE = """[journal-shell-write-guard] BLOCKED: this command would write {kind_label} content via {mechanism_desc} to a path shaped like an engineering-journal content file.

All four journal content-file kinds (stub .md, manifest .jsonl, open-PR .json, tile .json) carry free prose or a path field that routinely contains apostrophes, quotes, backticks, $, or markdown code fences -- shell quoting breaks on this content, either failing the write outright or silently corrupting the file (dev-env#904: a tile shard's cwd field was corrupted this way before write-time validation caught it after the fact).

  Command: {segment}
  Target : {target}

Use a file tool instead:
  - File doesn't exist yet -> Write tool, full content in one call.
  - File exists, one field changing (e.g. prs_closed after a merge) -> Edit tool, a targeted replacement.

See claude/CLAUDE.md -> Engineering Journal -> Stub file workflow, docs/REFERENCE.md -> Engineering Journal Internals, and ADR-129.

Genuine exception? Bash: prefix the command with {override}
                    PowerShell: precede it with its own statement, $env:{override_var}=1"""


def _mechanism_desc(match):
    mechanism = match["mechanism"]
    if mechanism == "bash-redirect":
        return "a `{}` redirect".format(match["operator_or_cmdlet"])
    if mechanism == "bash-tee":
        return "a `tee` invocation"
    if mechanism == "serializer-invocation":
        return "a `{}` serializer invocation mentioning it".format(match["operator_or_cmdlet"])
    return "the `{}` cmdlet".format(match["operator_or_cmdlet"])


def _build_block_message(match):
    return BLOCK_MESSAGE.format(
        kind_label=_KIND_LABELS.get(match["kind"], match["kind"]),
        mechanism_desc=_mechanism_desc(match),
        target=match["target"],
        segment=match["segment"],
        override=OVERRIDE_TOKEN,
        override_var=OVERRIDE_VAR_NAME,
    )


# --- main --------------------------------------------------------------------

def main():
    _hookutil.record_heartbeat("pre-tool-use-journal-shell-write-guard")
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
    if not _might_write_journal_content(cmd):
        sys.exit(0)
    cwd = data.get("cwd")
    if not isinstance(cwd, str):
        cwd = None

    segments = _segments_or_whole(cmd)
    matches = find_journal_shell_writes(cmd, tool_name, cwd=cwd, segments=segments)
    if not matches:
        sys.exit(0)
    # Per-segment override scoping (see _is_overridden) means different
    # matches can have different override status -- e.g. an override on
    # segment 0 exempts only segment 0's hazard, not an unrelated one
    # found in segment 1. Block on the first match that is NOT overridden;
    # only exit 0 if every match found is genuinely overridden.
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
