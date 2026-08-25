#!/usr/bin/env python3
"""Unit + integration tests for pre-tool-use-shell-content-write-guard.py
(ADR-138 -- the content-shaped generalization of ADR-129's journal-path rule).

Two layers, both hermetic (Layer 2 spawns the real hook as a subprocess but
touches no real files or git repos -- this hook does no filesystem or git work
at all):

  1. Pure-function tests of `body_hazard()` / `arg_hazard()` /
     `is_file_target()` / `extract_heredoc_literal()` / `extract_echo_literal()`
     / `find_serializer_write()` / `find_inplace_edit()` /
     `find_powershell_content_write()` / `find_content_writes()` /
     `_is_overridden()` / `might_write_content()`.

  2. End-to-end main() via subprocess -- asserting exit codes and plain-text
     `in proc.stderr` checks, since `_hookout.emit_block` writes ASCII-sanitized
     text rather than a `{"reason": ...}` JSON envelope.

The three cases that matter most are the verbatim reproductions of
dev-env#1041's occurrences, since this hook exists to stop exactly them:
`test_blocks_1041_occurrence_*`. Two of the three used a QUOTED heredoc
delimiter -- the documented-safe mitigation -- and failed in real life anyway,
which is why `body_hazard` deliberately does not exempt that form. If a future
change ever makes those three pass through, this suite fails loudly.

Equally load-bearing are the must-ALLOW cases. ADR-129 Amendment 1 found its
sibling hook, despite a green 63-case suite, over-matching legitimate commands
fleet-wide; this hook runs on every Bash and PowerShell call in every repo, so
the allow set (program-output redirection, a read-only `node -e`, a
single-quoted `sed` regex, a short clean `echo`) is tested as seriously as the
block set.

Two scope boundaries are pinned as EXPLICIT accepted gaps rather than left
silent, following ADR-129 Amendment 1 finding #9's precedent -- if either
starts behaving differently, the test says so instead of quietly changing:
`test_accepted_gap_heredoc_to_command_stdin` and
`test_accepted_gap_second_heredoc_on_one_line`.

Usage:
    py -3 claude/scripts/tests/test_shell_content_write_guard.py

Exit 0 = all pass.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "pre-tool-use-shell-content-write-guard.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("shell_content_write_guard", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scwg = _load_module()


def _live_matches(cmd, tool_name="Bash"):
    """Every match that is not overridden -- i.e. what main() would block on."""
    segments = scwg.segments_or_whole(cmd)
    matches = scwg.find_content_writes(cmd, tool_name, segments=segments)
    return [m for m in matches if not scwg._is_overridden(segments, m["segment_index"])]


def _assert_blocks(cmd, tool_name="Bash", why=""):
    live = _live_matches(cmd, tool_name)
    if not live:
        raise AssertionError(f"expected a block{' -- ' + why if why else ''}\n  command: {cmd!r}")
    return live[0]


def _assert_allows(cmd, tool_name="Bash", why=""):
    live = _live_matches(cmd, tool_name)
    if live:
        raise AssertionError(
            f"expected NO block{' -- ' + why if why else ''}\n"
            f"  command: {cmd!r}\n"
            f"  wrongly matched as {live[0]['mechanism']} ({live[0]['reason']})"
        )


# --------------------------------------------------------------------------
# Layer 1a: the dev-env#1041 reproductions
# --------------------------------------------------------------------------

OCC1 = (
    "cat > scripts/check-turbo-dev-build-dep.mjs << 'EOF'\n"
    "const SEP = '\\\\';\n"
    "console.log(SEP);\n"
    "EOF"
)
OCC2 = "node -e \"const fs=require('fs');fs.writeFileSync('a.md','the user's name')\""
OCC3 = (
    "cat > body.md << 'BODY_EOF'\n"
    "## Summary\n"
    "It's a fix for `foo`.\n"
    "BODY_EOF"
)


def test_blocks_1041_occurrence_1_heredoc_script() -> str:
    m = _assert_blocks(OCC1, why="a quoted heredoc collapsed '\\\\' and node rejected the file")
    if m["mechanism"] != "heredoc":
        raise AssertionError(f"expected the heredoc mechanism, got {m['mechanism']!r}")
    return "occurrence 1 (quoted heredoc authoring a .mjs guard script) blocks"


def test_blocks_1041_occurrence_2_node_e_apostrophe() -> str:
    m = _assert_blocks(OCC2, why="the apostrophe in user's closed the shell string")
    if m["mechanism"] != "serializer":
        raise AssertionError(f"expected the serializer mechanism, got {m['mechanism']!r}")
    if "apostrophe" not in m["reason"]:
        raise AssertionError(f"expected an apostrophe reason, got {m['reason']!r}")
    return "occurrence 2 (node -e file edit, apostrophe in user's) blocks, named as an apostrophe"


def test_blocks_1041_occurrence_3_pr_body_heredoc() -> str:
    m = _assert_blocks(OCC3, why="the quoted heredoc still died on unexpected EOF")
    if m["mechanism"] != "heredoc":
        raise AssertionError(f"expected the heredoc mechanism, got {m['mechanism']!r}")
    return "occurrence 3 (quoted heredoc writing a PR body) blocks"


def test_quoted_delimiter_is_not_treated_as_safe() -> str:
    # The whole point: <<'EOF' is what the old guidance recommended.
    for cmd in (OCC1, OCC3):
        _assert_blocks(cmd, why="a quoted delimiter must not be an exemption")
    return "a quoted heredoc delimiter is not an exemption (both 1041 heredocs used it)"


# --------------------------------------------------------------------------
# Layer 1b: hazard tests
# --------------------------------------------------------------------------


def test_body_hazard_markers() -> str:
    if scwg.body_hazard("It's here") is None:
        raise AssertionError("an apostrophe in a body must be hazardous")
    if scwg.body_hazard("run `cmd`") is None:
        raise AssertionError("a backtick in a body must be hazardous")
    if scwg.body_hazard("a \\ b") is None:
        raise AssertionError("a backslash in a body must be hazardous")
    if scwg.body_hazard("one\ntwo") is None:
        raise AssertionError("a multi-line body must be hazardous")
    return "body_hazard() flags apostrophe, backtick, backslash, and multi-line bodies"


def test_body_hazard_allows_short_clean_content() -> str:
    for safe in ("hello", "done", "", "   ", "some plain words"):
        if scwg.body_hazard(safe) is not None:
            raise AssertionError(f"{safe!r} must be treated as safe")
    return "body_hazard() passes short, single-line, marker-free content"


def test_arg_hazard_treats_quotes_as_structure() -> str:
    if scwg.arg_hazard(" 'done'") is not None:
        raise AssertionError("delimiting single quotes are structure, not content")
    if scwg.arg_hazard(' "done"') is not None:
        raise AssertionError("delimiting double quotes are structure, not content")
    return "arg_hazard() treats delimiting quotes as structure, so `echo 'done'` stays safe"


def test_arg_hazard_flags_apostrophe_the_author_fought_for() -> str:
    if scwg.arg_hazard(" \"the user's name\"") is None:
        raise AssertionError("a literal apostrophe inside double quotes must be hazardous")
    if scwg.arg_hazard(" Claude\\'s") is None:
        raise AssertionError("an escaped apostrophe in unquoted context must be hazardous")
    return "arg_hazard() flags an apostrophe inside double quotes and the escaped `\\'` idiom"


def test_arg_hazard_backslash_only_where_the_shell_sees_it() -> str:
    if scwg.arg_hazard(" 's/a\\.b/c/'") is not None:
        raise AssertionError(
            "a backslash inside single quotes is literal -- the common sed idiom must pass"
        )
    if scwg.arg_hazard(' "s/a\\\\b/c/"') is None:
        raise AssertionError("a backslash inside double quotes IS consumed and must be hazardous")
    return "arg_hazard() exempts a single-quoted backslash but flags a double-quoted one"


def test_arg_hazard_flags_backtick_outside_single_quotes() -> str:
    if scwg.arg_hazard(' "run `cmd`"') is None:
        raise AssertionError("a backtick outside single quotes is command substitution")
    if scwg.arg_hazard(" 'run `cmd`'") is not None:
        raise AssertionError("a backtick inside single quotes is inert")
    return "arg_hazard() flags a backtick only where the shell would substitute it"


def test_is_file_target_rejects_sinks_and_fd_dups() -> str:
    for sink in ("/dev/null", "$null", "&1", "&2", "", "   "):
        if scwg.is_file_target(sink):
            raise AssertionError(f"{sink!r} is not a file target")
    for real in ("out.md", "'my file.md'", "sessions/x/y.md"):
        if not scwg.is_file_target(real):
            raise AssertionError(f"{real!r} is a real file target")
    return "is_file_target() rejects /dev/null, $null, and fd duplications; accepts real paths"


# --------------------------------------------------------------------------
# Layer 1c: extraction + per-mechanism detection
# --------------------------------------------------------------------------


def test_extract_heredoc_literal_body_and_delimiter() -> str:
    got = scwg.extract_heredoc_literal("cat > f.md <<'EOF'\nline one\nline two\nEOF")
    if got != ("heredoc", "line one\nline two"):
        raise AssertionError(f"expected the body between the delimiters, got {got!r}")
    return "extract_heredoc_literal() returns the body between the opener and its delimiter"


def test_extract_heredoc_literal_dash_form_strips_tabs() -> str:
    got = scwg.extract_heredoc_literal("cat > f.md <<-EOF\n\tbody\n\tEOF")
    if got is None or got[1] != "\tbody":
        raise AssertionError(f"the <<- form must find a tab-indented delimiter, got {got!r}")
    return "extract_heredoc_literal() handles the tab-stripping `<<-` form"


def test_extract_heredoc_literal_ignores_quoted_marker() -> str:
    if scwg.extract_heredoc_literal("echo 'use << here' > f.md") is not None:
        raise AssertionError("a `<<` inside quotes is not a heredoc opener")
    return "a `<<` inside a quoted string is not mistaken for a heredoc opener"


def test_extract_echo_literal_drops_printf_format_string() -> str:
    got = scwg.extract_echo_literal('printf \'%s\\n\' "$X" > f.txt',
                                    scwg.mask_first_line_quotes('printf \'%s\\n\' "$X" > f.txt'))
    if got is None or "%s" in got[1]:
        raise AssertionError(f"printf's format string must be dropped when args follow, got {got!r}")
    return "extract_echo_literal() drops printf's format string when payload args follow"


def test_extract_echo_literal_keeps_lone_printf_argument() -> str:
    cmd = "printf 'It is content' > f.txt"
    got = scwg.extract_echo_literal(cmd, scwg.mask_first_line_quotes(cmd))
    if got is None or "It is content" not in got[1]:
        raise AssertionError(f"a lone printf argument IS the content and must be kept, got {got!r}")
    return "extract_echo_literal() keeps a lone printf argument (it is the content)"


def test_script_writes_a_file_markers() -> str:
    if not scwg.script_writes_a_file("fs.writeFileSync('a','b')"):
        raise AssertionError("writeFileSync must count as a file write")
    if not scwg.script_writes_a_file("open('a.txt', 'w').write(x)"):
        raise AssertionError("open(..., 'w') must count as a file write")
    if scwg.script_writes_a_file("console.log(JSON.parse(d).field)"):
        raise AssertionError("a read-only script must NOT count as a file write")
    return "script_writes_a_file() recognizes write calls and ignores read-only scripts"


def test_find_inplace_edit_recognizes_flag_forms() -> str:
    for cmd in ('sed -i "s/a\\\\b/c/" f', 'sed -i.bak "s/a\\\\b/c/" f',
                'perl -pi -e "s/a\\\\b/c/" f'):
        if scwg.find_inplace_edit(cmd) is None:
            raise AssertionError(f"in-place edit not recognized: {cmd!r}")
    if scwg.find_inplace_edit("sed 's/a/b/' f") is not None:
        raise AssertionError("a non-in-place sed has no write target and must not match")
    return "find_inplace_edit() recognizes -i, -i.bak, and perl -pi -e; ignores non-in-place sed"


def test_powershell_here_string_across_a_pipe() -> str:
    # The canonical form pipes the literal INTO the cmdlet, so the two land in
    # different pipeline segments -- searching only the cmdlet's own segment misses it.
    _assert_blocks("@'\nIt's content\n'@ | Set-Content a.md", "PowerShell",
                   why="a here-string piped into Set-Content is an inline literal")
    return "a PowerShell here-string piped into Set-Content is matched across the pipe boundary"


def test_powershell_value_flag_literal() -> str:
    m = _assert_blocks('Set-Content -Path a.md -Value "the user\'s name"', "PowerShell")
    if m["mechanism"] != "powershell-cmdlet":
        raise AssertionError(f"expected powershell-cmdlet, got {m['mechanism']!r}")
    return "a PowerShell -Value literal carrying an apostrophe blocks"


def test_powershell_detector_gated_on_tool_name() -> str:
    _assert_allows("rg Set-Content -Value notes.md", "Bash",
                   why="a cmdlet-shaped word in a Bash grep pattern is not a PowerShell call")
    return "the PowerShell detector is gated on tool_name (a Bash grep pattern is untouched)"


def test_might_write_content_prefilter() -> str:
    if scwg.might_write_content("git status"):
        raise AssertionError("a command with no marker at all must be pre-filtered out")
    if not scwg.might_write_content("echo hi > f"):
        raise AssertionError("a `>` must pass the pre-filter")
    if not scwg.might_write_content("Set-Content -Path f -Value x"):
        raise AssertionError("a cmdlet name must pass the pre-filter")
    return "might_write_content() short-circuits marker-free commands and admits real candidates"


# --------------------------------------------------------------------------
# Layer 1d: the must-ALLOW set (over-matching is the fleet-wide risk)
# --------------------------------------------------------------------------


def test_allows_program_output_redirection() -> str:
    for cmd in ('gh issue view 1 --json body > "$TMPFILE"',
                "npm test > out.log 2>&1",
                "git diff origin/main > patch.txt",
                "cat a.txt | tee b.txt"):
        _assert_allows(cmd, why="program output never crosses a shell quoting boundary")
    return "program-output redirection (gh/npm/git/pipe-into-tee) is never matched"


def test_allows_short_clean_inline_literals() -> str:
    for cmd in ("echo done > flag.txt", 'echo "done" > flag.txt', "echo 1 > f"):
        _assert_allows(cmd, why="a single-line, marker-free literal is the documented carve-out")
    return "a short, single-line, marker-free literal is allowed (echo done > flag.txt)"


def test_allows_read_only_node_e() -> str:
    _assert_allows(
        "node -e \"const d=JSON.parse(require('fs').readFileSync('t.json','utf8'));"
        "console.log(d.field);\"",
        why="the documented jq replacement reads; it never writes a file",
    )
    return "the documented read-only `node -e` jq replacement is not matched"


def test_allows_single_quoted_sed_regex() -> str:
    _assert_allows("sed -i 's/a\\.b/c/' f.txt",
                   why="a backslash inside single quotes is literal to the shell")
    return "the common single-quoted `sed -i 's/a\\.b/c/'` idiom is allowed"


def test_allows_redirect_to_dev_null() -> str:
    _assert_allows("echo it's fine > /dev/null", why="/dev/null is not a file write")
    return "a redirect to /dev/null is not a file write and is allowed"


def test_allows_command_substitution_into_a_file() -> str:
    # `echo "$(cmd)" > f` is program output wearing an echo: the literal region is a
    # substitution, not authored content. Copied from a live instruction
    # (claude/skills/journal-compose/SKILL.md, the .draft-compose.lock write).
    _assert_allows('echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$WT/sessions/proj/.draft-compose.lock"',
                   why="a command substitution is program output, not an authored literal")
    return "an `echo \"$(cmd)\" > f` command substitution is allowed (program output, not content)"


def test_allows_multiline_read_only_node_e() -> str:
    # The jq-replacement recipe in claude/CLAUDE.md spans several physical lines and
    # is full of quotes -- but it only READS, so no write-call marker and no redirect
    # to a file means no match. This repo tells Claude to run it constantly.
    _assert_allows(
        'node -e "\n'
        "  const d = JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));\n"
        "  console.log('VAR=' + d.field);\n"
        '"',
        why="the documented jq replacement reads and prints; it writes no file",
    )
    return "the multi-line, quote-heavy read-only `node -e` jq recipe is allowed (no write, no redirect)"


def test_allows_non_write_commands_mentioning_content() -> str:
    for cmd in ("cat body.md", "git commit -m \"fix: the user's name\"",
                "grep -n \"user's\" *.md"):
        _assert_allows(cmd, why="no file-write destination and no inline literal write")
    return "reads, commit messages, and greps carrying prose are untouched"


# --------------------------------------------------------------------------
# Layer 1e: overrides + accepted gaps
# --------------------------------------------------------------------------


def test_own_override_token_exempts() -> str:
    _assert_allows("ALLOW_SHELL_CONTENT_WRITE=1 cat > a.md <<'EOF'\nIt's ok\nEOF")
    return "a leading ALLOW_SHELL_CONTENT_WRITE=1 prefix exempts its segment"


def test_journal_override_token_also_exempts() -> str:
    # Without this, a deliberate ADR-129 override would clear that hook and then
    # be blocked by this one -- a live escape hatch silently broken.
    _assert_allows("ALLOW_JOURNAL_SHELL_WRITE=1 cat > x.stub.md <<'EOF'\nIt's ok\nEOF")
    return "ADR-129's ALLOW_JOURNAL_SHELL_WRITE=1 is honoured here too (escape hatch stays live)"


def test_override_does_not_exempt_a_later_segment() -> str:
    _assert_blocks(
        "ALLOW_SHELL_CONTENT_WRITE=1 echo a > ok.txt && cat > b.md <<'EOF'\nIt's not ok\nEOF",
        why="real Bash scopes VAR=1 to the one statement it prefixes",
    )
    return "a Bash override on one segment does not exempt an unrelated later segment"


def test_powershell_env_override_applies_forward() -> str:
    _assert_allows("$env:ALLOW_SHELL_CONTENT_WRITE=1; Set-Content -Path a.md -Value \"it's ok\"",
                   "PowerShell")
    return "a standalone PowerShell $env: override applies forward (real PowerShell semantics)"


def test_accepted_gap_heredoc_to_command_stdin() -> str:
    # ACCEPTED GAP, pinned deliberately: same hazard, but no file destination, so
    # the inline-literal-to-a-FILE rule does not reach it. If this ever starts
    # matching, that is a scope change to make on purpose -- not silently.
    live = _live_matches("gh pr create --body-file - <<'EOF'\nIt's a body.\nEOF")
    if live:
        raise AssertionError(
            "this is a documented accepted gap (ADR-138). It now matches as "
            f"{live[0]['mechanism']} -- if that is intended, update ADR-138 and this test."
        )
    return "ACCEPTED GAP confirmed: a heredoc to a command's stdin (no file target) is not matched"


def test_accepted_gap_second_heredoc_on_one_line() -> str:
    # ACCEPTED GAP: only the FIRST heredoc opener on a line is inspected. One
    # hazardous body is enough to block, so this costs nothing in practice.
    got = scwg.extract_heredoc_literal("cat > a.md <<'A' > b.md <<'B'\nfirst\nA\nsecond\nB")
    if got is None or got[1] != "first":
        raise AssertionError(
            "expected only the FIRST heredoc body (documented accepted gap), got "
            f"{got!r} -- if multi-heredoc support was added, update ADR-138 and this test."
        )
    return "ACCEPTED GAP confirmed: only the first heredoc opener on a line is inspected"


# --------------------------------------------------------------------------
# Layer 2: end-to-end main() via subprocess
# --------------------------------------------------------------------------


def _run_hook(payload) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=json.dumps(payload) if not isinstance(payload, str) else payload,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _payload(cmd, tool_name="Bash"):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": cmd},
        "session_id": "test-session",
        "cwd": "C:/Users/brown/Git/lifting-logbook",
    }


def test_main_blocks_each_1041_occurrence() -> str:
    for label, cmd in (("1", OCC1), ("2", OCC2), ("3", OCC3)):
        proc = _run_hook(_payload(cmd))
        if proc.returncode != 2:
            raise AssertionError(
                f"occurrence {label} must block (exit 2), got {proc.returncode}\n"
                f"  stderr: {proc.stderr[:300]}"
            )
        if "shell-content-write-guard" not in proc.stderr:
            raise AssertionError(f"occurrence {label} block message must name the guard")
        if "Write tool" not in proc.stderr:
            raise AssertionError(f"occurrence {label} block message must name the remedy")
    return "main(): all three dev-env#1041 occurrences block (exit 2) with the Write-tool remedy"


def test_main_message_names_mechanism_and_reason() -> str:
    proc = _run_hook(_payload(OCC2))
    for expected in ("Mechanism:", "Command  :", "Target   :", "apostrophe"):
        if expected not in proc.stderr:
            raise AssertionError(f"block message must contain {expected!r}\n  got: {proc.stderr[:400]}")
    return "main(): the block message names the mechanism, command, target, and hazard reason"


def test_main_allows_program_output_and_clean_literals() -> str:
    for cmd in ('gh issue view 1 --json body > "$TMPFILE"',
                "echo done > flag.txt",
                "sed -i 's/a\\.b/c/' f.txt",
                "git status"):
        proc = _run_hook(_payload(cmd))
        if proc.returncode != 0:
            raise AssertionError(
                f"{cmd!r} must be allowed (exit 0), got {proc.returncode}\n"
                f"  stderr: {proc.stderr[:300]}"
            )
    return "main(): program output, clean literals, single-quoted sed, and plain commands exit 0"


def test_main_blocks_powershell_content_write() -> str:
    proc = _run_hook(_payload("Set-Content -Path a.md -Value \"the user's name\"", "PowerShell"))
    if proc.returncode != 2:
        raise AssertionError(f"a PowerShell -Value literal must block, got {proc.returncode}")
    return "main(): a PowerShell Set-Content -Value literal blocks end-to-end"


def test_main_override_bypasses_block() -> str:
    proc = _run_hook(_payload("ALLOW_SHELL_CONTENT_WRITE=1 cat > a.md <<'EOF'\nIt's ok\nEOF"))
    if proc.returncode != 0:
        raise AssertionError(f"the override must bypass the block, got {proc.returncode}")
    return "main(): the override token bypasses the block end-to-end (exit 0)"


def test_main_no_cwd_in_payload_still_works() -> str:
    payload = _payload(OCC3)
    del payload["cwd"]
    proc = _run_hook(payload)
    if proc.returncode != 2:
        raise AssertionError("this hook needs no cwd; a payload omitting it must still block")
    return "main(): a payload with no cwd at all still blocks (this hook needs no cwd)"


def test_main_fails_open_on_malformed_input() -> str:
    cases = [
        ("", "empty stdin"),
        ("not json", "malformed JSON"),
        (json.dumps([1, 2, 3]), "non-object JSON"),
        (json.dumps({"tool_name": "Bash"}), "missing tool_input"),
        (json.dumps({"tool_name": "Bash", "tool_input": "notadict"}), "non-dict tool_input"),
        (json.dumps({"tool_name": "Read", "tool_input": {"command": OCC3}}), "non-shell tool_name"),
        (json.dumps({"tool_name": "Bash", "tool_input": {"command": None}}), "null command"),
    ]
    for raw, label in cases:
        proc = _run_hook(raw)
        if proc.returncode != 0:
            raise AssertionError(f"must fail open on {label}, got exit {proc.returncode}")
    return "main(): fails open (exit 0) on every malformed/irrelevant payload shape"


def main() -> int:
    tests = [
        ("blocks dev-env#1041 occurrence 1 (heredoc script)", test_blocks_1041_occurrence_1_heredoc_script),
        ("blocks dev-env#1041 occurrence 2 (node -e apostrophe)", test_blocks_1041_occurrence_2_node_e_apostrophe),
        ("blocks dev-env#1041 occurrence 3 (PR-body heredoc)", test_blocks_1041_occurrence_3_pr_body_heredoc),
        ("a quoted delimiter is not an exemption", test_quoted_delimiter_is_not_treated_as_safe),
        ("body_hazard(): markers", test_body_hazard_markers),
        ("body_hazard(): allows short clean content", test_body_hazard_allows_short_clean_content),
        ("arg_hazard(): quotes are structure", test_arg_hazard_treats_quotes_as_structure),
        ("arg_hazard(): flags a fought-for apostrophe", test_arg_hazard_flags_apostrophe_the_author_fought_for),
        ("arg_hazard(): backslash only where the shell sees it", test_arg_hazard_backslash_only_where_the_shell_sees_it),
        ("arg_hazard(): backtick outside single quotes", test_arg_hazard_flags_backtick_outside_single_quotes),
        ("is_file_target(): sinks and fd dups", test_is_file_target_rejects_sinks_and_fd_dups),
        ("extract_heredoc_literal(): body and delimiter", test_extract_heredoc_literal_body_and_delimiter),
        ("extract_heredoc_literal(): <<- tab form", test_extract_heredoc_literal_dash_form_strips_tabs),
        ("extract_heredoc_literal(): ignores quoted marker", test_extract_heredoc_literal_ignores_quoted_marker),
        ("extract_echo_literal(): drops printf format", test_extract_echo_literal_drops_printf_format_string),
        ("extract_echo_literal(): keeps lone printf arg", test_extract_echo_literal_keeps_lone_printf_argument),
        ("script_writes_a_file(): markers", test_script_writes_a_file_markers),
        ("find_inplace_edit(): flag forms", test_find_inplace_edit_recognizes_flag_forms),
        ("PowerShell here-string across a pipe", test_powershell_here_string_across_a_pipe),
        ("PowerShell -Value literal", test_powershell_value_flag_literal),
        ("PowerShell detector gated on tool_name", test_powershell_detector_gated_on_tool_name),
        ("might_write_content(): pre-filter", test_might_write_content_prefilter),
        ("allows program-output redirection", test_allows_program_output_redirection),
        ("allows short clean inline literals", test_allows_short_clean_inline_literals),
        ("allows read-only node -e", test_allows_read_only_node_e),
        ("allows single-quoted sed regex", test_allows_single_quoted_sed_regex),
        ("allows redirect to /dev/null", test_allows_redirect_to_dev_null),
        ("allows command substitution into a file", test_allows_command_substitution_into_a_file),
        ("allows multi-line read-only node -e", test_allows_multiline_read_only_node_e),
        ("allows reads/commits/greps carrying prose", test_allows_non_write_commands_mentioning_content),
        ("own override token exempts", test_own_override_token_exempts),
        ("journal override token also exempts", test_journal_override_token_also_exempts),
        ("override does not exempt a later segment", test_override_does_not_exempt_a_later_segment),
        ("PowerShell $env: override applies forward", test_powershell_env_override_applies_forward),
        ("ACCEPTED GAP: heredoc to command stdin", test_accepted_gap_heredoc_to_command_stdin),
        ("ACCEPTED GAP: second heredoc on one line", test_accepted_gap_second_heredoc_on_one_line),
        ("main(): blocks each 1041 occurrence", test_main_blocks_each_1041_occurrence),
        ("main(): message names mechanism and reason", test_main_message_names_mechanism_and_reason),
        ("main(): allows program output and clean literals", test_main_allows_program_output_and_clean_literals),
        ("main(): blocks PowerShell content write", test_main_blocks_powershell_content_write),
        ("main(): override bypasses block", test_main_override_bypasses_block),
        ("main(): no cwd in payload still works", test_main_no_cwd_in_payload_still_works),
        ("main(): fails open on malformed input", test_main_fails_open_on_malformed_input),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
