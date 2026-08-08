#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- blocks a Bash/PowerShell command that would
write content to an engineering-journal content file via a shell mechanism
(heredoc, `echo`/redirect, or a PowerShell content-write cmdlet) instead of
the Write/Edit tool.

Problem: all four engineering-journal content-file kinds --
  1. the stub `.md` (100% free-form prose -- session summaries)
  2. the manifest `.jsonl` shard (a free-text `topic` field)
  3. the open-PR `.json` shard (free-text `topic`/path fields)
  4. the tile `.json` shard (a free-text `prompt` field, a `cwd` path field)
-- carry prose or paths that routinely contain apostrophes, quotes,
backticks, `$`, and markdown code fences. A Bash/PowerShell heredoc,
`echo`/redirect, or content-write cmdlet targeting one of these paths has
the shell (or a serializer's own string-literal layer -- see dev-env#904)
parse that content before it reaches disk: an apostrophe closes a
single-quoted string early, a `$`/backtick gets interpreted, a nested `"`
breaks a double-quoted string, a code fence collides with a heredoc
delimiter. This either fails the write outright (wasting a turn) or --
worse -- silently corrupts the file.

This has already recurred once from under-generalizing the fix: dev-env#904
found a tile shard's `cwd` field corrupted by a `node -e` serializer's own
string-literal layer eating \\U/\\G/\\b -- fixed by a forward-slash prescription
plus write-time schema validation (ADR-118 Amendment 4), NOT by removing the
shell step. ADR-118 Amendment 4 says plainly the prior rule "guarded the
wrong field." The documented recipes for three of the four kinds WERE
THEMSELVES the anti-pattern: docs/REFERENCE.md's manifest and open-PR shard
sections showed `echo '{...}' > file`; the tile section showed a "safer"
quoted-heredoc / `py -3 -c` serializer, still shell-based; and the stub `.md`
had no documented creation mechanism at all. See ADR-129 and dev-env#961.

This hook removes the shell step mechanically rather than relying on a
fourth documentation-only fix: `journal-shard-write-advisory.py`
(PostToolUse) validates a shard's schema AFTER it is written -- useful, but
it cannot stop the wasted turn or a silent corruption before it happens, and
it has no schema at all for the prose-only stub `.md`. This hook blocks the
shell-based WRITE ATTEMPT itself, for all four file kinds, before any
content is ever parsed by a shell. Complementary, not a replacement.

Detection (cheapest checks first -- no filesystem or subprocess work at all,
unlike the two sibling hooks below, since "is this command's target path
SHAPED like one of the four journal content files, and is it being
shell-written-to?" is answerable from the command text alone, regardless of
which repo/cwd issued it):
  1. Read stdin JSON. Fail open (exit 0) on anything unparseable, or a
     tool_name that is neither `Bash` nor `PowerShell`.
  2. Split the command into logical segments via the shared
     `_hookio.split_top_level(cmd, split_pipe=True)` engine -- `split_pipe`
     matters here specifically because a PowerShell `Tee-Object` invocation
     is idiomatically reached via a pipe (`... | Tee-Object -FilePath
     <path>`), and pipe-splitting isolates it onto its own segment so a
     segment-scoped scan sees it.
  3. For each segment, examine only its own first physical line
     (`segment.split("\n", 1)[0]`) -- same convention as both sibling hooks'
     `_first_line()`: a heredoc/here-string BODY is opaque data, never
     additional invocation syntax, but a redirect operator or cmdlet name on
     the SAME line as a heredoc OPENER (`cat <<'EOF' > path/to/x.stub.md`)
     is real invocation syntax and must be visible.
  4. On that first line, locate a content-write mechanism: a genuine (not
     inside a quote) Bash `>`/`>>` redirect, or a PowerShell `Out-File` /
     `Set-Content` / `Add-Content` / `Tee-Object` / `New-Item ... -Value`
     invocation.
  5. Classify the mechanism's target path against the four journal
     content-file shapes: `*.stub.md`, `*.manifest.jsonl`,
     `open-prs/<digits>.json`, `tiles/<digits>.json`.
  6. If the `ALLOW_JOURNAL_SHELL_WRITE=1` override token appears as a
     genuine leading prefix on the command or one of its split-out segments
     (never a mere substring, e.g. inside a commit message argument), exit 0
     -- a deliberate, visible human override.
  7. Otherwise, block (exit 2) via `_hookout.emit_block`, naming the matched
     command/target and the Write/Edit-tool remedy.

Load-bearing implementation subtlety: masking quoted spans on an ALREADY
first-line-truncated string needs its own small helper
(`_mask_first_line_quotes`), not a direct call to `_hookio.mask_quoted_spans`.
That function's heredoc-opener handling (`_find_heredoc_end`) assumes a real
multi-line body + terminator may still follow in the string it's given --
never true once a segment has already been truncated to its own first line.
Fed such a string directly, `_find_heredoc_end` runs off the end of the
string looking for the declaration line's terminating newline, consuming
everything after the heredoc opener -- INCLUDING a same-line redirect target,
which is exactly what this hook needs to see. `_mask_first_line_quotes`
neutralizes `<<` (same-length, `<<` -> `<#`) before masking so the
heredoc-opener branch never fires on this input shape at all; this is safe
because a genuine `<<` inside a quote was never treated as an opener by
`_opaque_spans` in the first place. See the regression test
`test_find_bash_redirect_targets_heredoc_declaration_line` in this hook's
test file -- the single most important case in that suite.

Path-shape matching is intentionally lexical only (no `sessions/` or
`engineering-journal` path-component requirement, unlike
`journal-shard-write-advisory.py`'s path classifier, which can afford that
check because it resolves against a real on-disk file first). This hook has
no on-disk resolution step to lean on, and the four shapes -- `*.stub.md`,
`*.manifest.jsonl`, a numeric-named file under `open-prs/`, a numeric-named
file under `tiles/` -- are already distinctive enough that requiring more
would risk under-matching a relative-path invocation issued with cwd already
inside `sessions/<project>/`.

Also fires for the PowerShell tool: registered under both the Bash and
PowerShell PreToolUse matchers in settings.json, mirroring both sibling
hooks -- load-bearing here specifically, since PowerShell's own `>`/`>>`
redirect operators and the five named cmdlets are a PowerShell tool_name
concern in the first place, not merely "PowerShell as an alternate way to
run a Bash-shaped command."

No `_winsubp` import: this hook spawns no subprocess at all (unlike
`pre-tool-use-canonical-mutate-guard.py` / `pre-tool-use-journal-draft-worktree-guard.py`,
which shell out to `git` to resolve worktree/repo context) -- matches the
precedent of `pre-tool-use-skill-file-size-guard.py`, the only other
PreToolUse hook with no subprocess work and no `_winsubp` import.

Emits via `_hookout.emit_block` (ADR-103) -- the current convention for a
NEW PreToolUse hook, confirmed against `pre-tool-use-skill-file-size-guard.py`
(the most recently added PreToolUse hook as of this writing). The two older
sibling hooks named above hand-roll `sys.stderr.write(json.dumps(...))`
instead; that pattern predates the `_hookout` migration (ADR-103) and is
allowlisted in `test_hook_output_contract.py` as a known pre-migration
offender -- a NEW hook must not join that allowlist.

Fail-open (exit 0) throughout, matching every sibling hook's contract -- not
one of the two specially-designated fail-closed gates
(`pre-auto-merge-checkpoint-gate.py`, `pre-tool-use-journal-compose-force-guard.py`).
A missed block here leaves today's status quo (a corruption risk
`journal-shard-write-advisory.py` still catches after the fact for three of
the four kinds); a crash must never additionally block an unrelated Bash/
PowerShell call.

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
import shlex
import sys

from _hookio import mask_quoted_spans, split_top_level
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
    None if it doesn't match any of them."""
    t = token.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        t = t[1:-1]
    for kind, pattern in _JOURNAL_PATH_KINDS:
        if pattern.search(t):
            return kind
    return None


# --- Segment-local helpers -------------------------------------------------

def _first_line(segment):
    """segment's own first physical line -- a heredoc/here-string body must
    never be mistaken for invocation syntax. See module docstring."""
    return segment.split("\n", 1)[0]


_HEREDOC_MARKER_RE = re.compile(r"<<")


def _mask_first_line_quotes(first_line):
    """Quote-mask an ALREADY first-line-truncated string without
    mis-triggering `_hookio`'s heredoc-opener handling, which assumes a real
    multi-line body may still follow -- never true here. See module
    docstring for the failure mode this avoids: fed directly, a `<<'EOF'`
    opener with no following newline makes `_find_heredoc_end` consume the
    rest of the string (including a same-line redirect target) as an
    unterminated heredoc declaration. Neutralizing `<<` (same length, so
    offsets stay aligned with the original) before masking sidesteps this;
    a genuine `<<` inside a quote was never treated as an opener by
    `_opaque_spans` in the first place, so this neutralization is safe."""
    return mask_quoted_spans(_HEREDOC_MARKER_RE.sub("<#", first_line))


def _next_token(text):
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


# --- Bash redirect detection ------------------------------------------------

# A '>' or '>>' not immediately adjacent to another '>' (so '>>' is matched
# once, as a 2-char operator, not twice as two single ones).
_REDIRECT_OP_RE = re.compile(r"(?<!>)(>{1,2})(?!>)")


def find_bash_redirect_targets(first_line):
    """Return [(operator, target), ...] for every genuine (non-quoted)
    '>'/'>>' redirect on *first_line*, in original-command order."""
    masked = _mask_first_line_quotes(first_line)
    out = []
    for m in _REDIRECT_OP_RE.finditer(masked):
        target = _next_token(first_line[m.end():])
        if target:
            out.append((m.group(1), target))
    return out


# --- PowerShell cmdlet detection --------------------------------------------

_PS_WRITE_CMDLET_RE = re.compile(r"(?i)(?<![\w-])(Out-File|Set-Content|Add-Content|Tee-Object)(?![\w-])")
_PS_NEW_ITEM_RE = re.compile(r"(?i)(?<![\w-])New-Item(?![\w-])")
_PS_VALUE_FLAG_RE = re.compile(r"(?i)(?<![\w-])-Value(?![\w-])")


def _tokenize_line(line):
    """POSIX-ish tokenization with a naive whitespace-split fallback --
    mirrors both sibling hooks' `_tokenize()` convention (the documented
    POSIX-vs-PowerShell shlex quoting gap, dev-env#620 follow-up: a
    PowerShell here-string opener like the trailing `@'` of a `-Value @'`
    invocation is an unterminated POSIX quote to `shlex`, which raises)."""
    try:
        return shlex.split(line, posix=True)
    except ValueError:
        return line.split()


def find_powershell_write_targets(first_line):
    """Return [(cmdlet, target, kind), ...] for every journal-path-shaped
    target on *first_line* following a genuine PowerShell content-write
    cmdlet. `New-Item` counts only alongside a `-Value` flag (bare `New-Item
    -ItemType Directory ...` is scaffolding, not a content-write). The
    token immediately after `-Value` is never itself treated as a target --
    it's the payload, not a path (mirrors `_hookio.mask_prose_flag_values`'s
    "a flag's own value isn't the thing being matched" technique)."""
    masked = _mask_first_line_quotes(first_line)
    is_write = bool(_PS_WRITE_CMDLET_RE.search(masked))
    is_new_item_with_value = bool(_PS_NEW_ITEM_RE.search(masked)) and bool(_PS_VALUE_FLAG_RE.search(masked))
    if not is_write and not is_new_item_with_value:
        return []
    tokens = _tokenize_line(first_line)
    cmdlet = next(
        (t for t in tokens if _PS_WRITE_CMDLET_RE.fullmatch(t) or _PS_NEW_ITEM_RE.fullmatch(t)),
        None,
    )
    if cmdlet is None:
        return []
    out = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok.lower() == "-value":
            skip_next = True
            continue
        kind = journal_path_kind(tok)
        if kind:
            out.append((cmdlet, tok, kind))
    return out


# --- Top-level combination --------------------------------------------------

OVERRIDE_TOKEN = "ALLOW_JOURNAL_SHELL_WRITE=1"


def find_journal_shell_writes(cmd, segments=None):
    """Return every shell-based journal-content-file write detected across
    *cmd*'s top-level segments, as a list of dicts:
    {"segment", "mechanism", "operator_or_cmdlet", "target", "kind"}."""
    if segments is None:
        segments = split_top_level(cmd, split_pipe=True)
    out = []
    for seg in segments:
        line = _first_line(seg)
        for op, target in find_bash_redirect_targets(line):
            kind = journal_path_kind(target)
            if kind:
                out.append({
                    "segment": seg.strip(),
                    "mechanism": "bash-redirect",
                    "operator_or_cmdlet": op,
                    "target": target,
                    "kind": kind,
                })
        for cmdlet, target, kind in find_powershell_write_targets(line):
            out.append({
                "segment": seg.strip(),
                "mechanism": "powershell-cmdlet",
                "operator_or_cmdlet": cmdlet,
                "target": target,
                "kind": kind,
            })
    return out


def _has_override(cmd, segments=None):
    """True iff OVERRIDE_TOKEN appears as a genuine leading prefix on one of
    *cmd*'s top-level segments -- never a mere substring (e.g. mentioned
    inside a commit message argument)."""
    if segments is None:
        segments = split_top_level(cmd, split_pipe=True)
    for seg in segments:
        stripped = seg.strip()
        if stripped == OVERRIDE_TOKEN or stripped.startswith(OVERRIDE_TOKEN + " "):
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

Genuine exception? Prefix the command with {override}."""


def _mechanism_desc(match):
    if match["mechanism"] == "bash-redirect":
        return "a `{}` redirect".format(match["operator_or_cmdlet"])
    return "the `{}` cmdlet".format(match["operator_or_cmdlet"])


def _build_block_message(match):
    return BLOCK_MESSAGE.format(
        kind_label=_KIND_LABELS.get(match["kind"], match["kind"]),
        mechanism_desc=_mechanism_desc(match),
        target=match["target"],
        segment=match["segment"],
        override=OVERRIDE_TOKEN,
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

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)
    cmd = tool_input.get("command", "")
    if not cmd or not isinstance(cmd, str):
        sys.exit(0)

    segments = split_top_level(cmd, split_pipe=True)
    matches = find_journal_shell_writes(cmd, segments)
    if not matches:
        sys.exit(0)
    if _has_override(cmd, segments):
        sys.exit(0)

    _hookout.emit_block(_build_block_message(matches[0]))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
