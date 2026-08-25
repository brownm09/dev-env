#!/usr/bin/env python3
"""Unit tests for _shell_write_detect.py -- the shell-syntax primitives shared
by `pre-tool-use-journal-shell-write-guard.py` (ADR-129) and
`pre-tool-use-shell-content-write-guard.py` (ADR-138).

Every function here arrived as a pure move out of the ADR-129 guard. That
guard's own 63-case suite already exercises them end-to-end through its public
detectors, and its passing UNCHANGED is the extraction's safety claim -- so
`## Testing` item 94 runs both suites together, the way item 92 does for
`_journal_canon.py`. This file adds what that indirect coverage cannot: direct
assertions on each helper's contract, so a future edit that breaks one is
attributed to the helper rather than surfacing as a puzzling failure two hooks
away.

The load-bearing cases, both inherited from ADR-129 Amendment 1 findings:

  - `mask_first_line_quotes` must neutralize `<<` BEFORE masking. Fed a
    first-line-truncated `cat <<'EOF' > f`, `_hookio`'s heredoc handling
    otherwise treats the rest of the string as an unterminated declaration
    and masks the redirect target away with it (finding, and the single most
    common real-world shape of the reported bug).
  - `neutralize_unquoted_escaped_quotes` must be quote-state aware, NOT a
    blind substitution: neutralizing a `\\'` that sits immediately before a
    single-quoted span's real closing quote would stop that span from
    closing at all. Both directions are pinned here.

Usage:
    py -3 claude/scripts/tests/test_shell_write_detect.py

Exit 0 = all pass.
"""
import importlib.util
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "_shell_write_detect.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("shell_write_detect", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


swd = _load_module()


# --------------------------------------------------------------------------
# first_line / segments_or_whole
# --------------------------------------------------------------------------


def test_first_line_truncates_at_newline() -> str:
    if swd.first_line("cat > f <<'EOF'\nbody\nEOF") != "cat > f <<'EOF'":
        raise AssertionError("first_line must return only the first physical line")
    return "first_line() returns only the first physical line of a segment"


def test_first_line_single_line_unchanged() -> str:
    if swd.first_line("echo hi") != "echo hi":
        raise AssertionError("a single-line segment must come back unchanged")
    return "a single-line segment passes through first_line() unchanged"


def test_segments_or_whole_splits_top_level() -> str:
    segs = swd.segments_or_whole("echo a > x && echo b > y")
    if len(segs) != 2:
        raise AssertionError(f"expected 2 top-level segments, got {len(segs)}: {segs!r}")
    return "segments_or_whole() splits on a top-level && boundary"


def test_segments_or_whole_respects_supplied_segments() -> str:
    supplied = ["already", "split"]
    if swd.segments_or_whole("ignored", segments=supplied) is not supplied:
        raise AssertionError("a supplied segments list must be returned as-is, not recomputed")
    return "a caller-supplied segments list is returned unchanged (no recompute)"


def test_segments_or_whole_falls_back_to_whole_command() -> str:
    # An escaped apostrophe in unquoted prose opens a quote span split_top_level
    # never closes, so it drops the whole command -- the fallback must recover it.
    cmd = "echo Claude\\'s note > out.txt"
    segs = swd.segments_or_whole(cmd)
    if not segs:
        raise AssertionError("an unterminated-looking command must fall back to one whole segment")
    return "an unterminated-quote command falls back to the whole command as one segment"


def test_segments_or_whole_fallback_needs_real_content() -> str:
    # The fallback exists to recover a command `split_top_level` DROPPED, and is
    # guarded on `cmd.strip()` so it can never invent a segment out of nothing.
    # A blank command is not the trigger -- split_top_level returns a segment for
    # it already, so the guard is what matters, tested directly.
    from _hookio import split_top_level
    for blank in ("", "   ", "\n"):
        upstream = split_top_level(blank, split_pipe=True)
        got = swd.segments_or_whole(blank)
        if got != upstream:
            raise AssertionError(
                f"for content-free input {blank!r} the fallback must not engage: "
                f"split_top_level gave {upstream!r} but segments_or_whole gave {got!r}"
            )
    return "content-free input passes split_top_level through; the fallback never invents a segment"


# --------------------------------------------------------------------------
# neutralize_unquoted_escaped_quotes
# --------------------------------------------------------------------------


def test_neutralize_is_length_preserving() -> str:
    for s in ("echo Claude\\'s > f", "'C:\\dir\\'", 'say "he\\"s" > f', "plain text"):
        if len(swd.neutralize_unquoted_escaped_quotes(s)) != len(s):
            raise AssertionError(f"length changed for {s!r} -- offsets would desynchronize")
    return "neutralization is length-preserving, so masked offsets stay aligned"


def test_neutralize_escaped_apostrophe_in_unquoted_prose() -> str:
    out = swd.neutralize_unquoted_escaped_quotes("echo Claude\\'s > f")
    if "'" in out:
        raise AssertionError("an escaped apostrophe in unquoted context must not remain a quote")
    return "an unquoted `\\'` is neutralized (real Bash treats it as a literal)"


def test_neutralize_leaves_single_quoted_backslash_alone() -> str:
    # 'C:\dir\' -- the trailing backslash is literal and the final quote really closes.
    src = "echo 'C:\\dir\\' > f"
    out = swd.neutralize_unquoted_escaped_quotes(src)
    if out != src:
        raise AssertionError(
            "inside a single-quoted span backslash has no meaning; the closing quote "
            f"must still close. got {out!r}"
        )
    return "a backslash inside single quotes is left alone (the closing quote still closes)"


def test_neutralize_escaped_double_quote_inside_double_quotes() -> str:
    out = swd.neutralize_unquoted_escaped_quotes('echo "he said \\"hi\\"" > f')
    if out.count('"') != 2:
        raise AssertionError("an escaped double-quote inside a double-quoted span must not close it")
    return "an escaped `\\\"` inside double quotes is neutralized, not treated as a close"


# --------------------------------------------------------------------------
# mask_first_line_quotes
# --------------------------------------------------------------------------


def test_mask_neutralizes_heredoc_marker() -> str:
    masked = swd.mask_first_line_quotes("cat <<'EOF' > out.md")
    if "<<" in masked:
        raise AssertionError("`<<` must be neutralized before masking")
    if "<#" not in masked:
        raise AssertionError("`<<` must become the same-length `<#` sentinel")
    return "mask_first_line_quotes() rewrites a genuine `<<` to the same-length `<#`"


def test_mask_preserves_length() -> str:
    for s in ("cat <<'EOF' > out.md", "echo 'a b c' > f", 'printf "x" > f'):
        if len(swd.mask_first_line_quotes(s)) != len(s):
            raise AssertionError(f"masking changed length for {s!r}")
    return "masking preserves string length for every shape (offset alignment invariant)"


def test_mask_keeps_redirect_visible_after_heredoc_opener() -> str:
    # The ADR-129 load-bearing case: without <<-neutralization the redirect
    # target is swallowed as part of an "unterminated heredoc declaration".
    line = "cat <<'EOF' > sessions/dev-env/x.stub.md"
    masked = swd.mask_first_line_quotes(line)
    if ">" not in masked:
        raise AssertionError("the redirect operator must survive masking on a heredoc opener line")
    return "a same-line redirect after a heredoc opener stays visible to the redirect scan"


# --------------------------------------------------------------------------
# next_token / find_redirect_targets
# --------------------------------------------------------------------------


def test_next_token_plain_and_quoted() -> str:
    if swd.next_token("  out.md rest") != "out.md":
        raise AssertionError("a plain token must be read up to whitespace")
    if swd.next_token("  'my file.md' rest") != "my file.md":
        raise AssertionError("a quoted token must be read whole, without its quotes")
    if swd.next_token("   ") != "":
        raise AssertionError("whitespace-only input must yield an empty token")
    return "next_token() reads plain, quoted, and empty targets correctly"


def test_find_redirect_targets_single_and_append() -> str:
    if swd.find_redirect_targets("echo hi > a.txt") != [(">", "a.txt")]:
        raise AssertionError("a single `>` redirect must be found")
    if swd.find_redirect_targets("echo hi >> a.txt") != [(">>", "a.txt")]:
        raise AssertionError("`>>` must be matched once as a 2-char operator")
    return "find_redirect_targets() reports `>` and `>>` with their targets"


def test_find_redirect_targets_ignores_quoted_operator() -> str:
    if swd.find_redirect_targets("echo 'a > b'") != []:
        raise AssertionError("a `>` inside a quoted string is not a redirect")
    return "a `>` inside quotes is not reported as a redirect"


def test_find_redirect_targets_multiple_in_order() -> str:
    got = swd.find_redirect_targets("cmd > a.txt 2> b.txt")
    if [t for _op, t in got] != ["a.txt", "b.txt"]:
        raise AssertionError(f"expected both targets in command order, got {got!r}")
    return "multiple redirects are reported in original command order"


def test_find_redirect_targets_accepts_shared_mask() -> str:
    line = "echo hi > a.txt"
    shared = swd.mask_first_line_quotes(line)
    if swd.find_redirect_targets(line, masked=shared) != swd.find_redirect_targets(line):
        raise AssertionError("a supplied mask must produce the same result as computing it")
    return "a caller-shared mask yields the same result as computing it internally"


# --------------------------------------------------------------------------
# tokenizers
# --------------------------------------------------------------------------


def test_tokenize_posix_strips_quotes_and_escapes() -> str:
    if swd.tokenize_posix("tee 'my file.md'") != ["tee", "my file.md"]:
        raise AssertionError("POSIX tokenization must group a quoted value and strip its quotes")
    return "tokenize_posix() groups quoted values and strips quotes (Bash semantics)"


def test_tokenize_powershell_preserves_backslashes() -> str:
    tokens = swd.tokenize_powershell(r"Set-Content sessions\dev-env\tiles\961.json")
    if not any("961.json" in t and "\\" in t for t in tokens):
        raise AssertionError(
            "PowerShell tokenization must NOT eat backslashes -- posix=True would "
            f"destroy the path before any check saw it. got {tokens!r}"
        )
    return "tokenize_powershell() preserves backslashes in a Windows path (posix=False)"


def test_tokenizers_fall_back_on_unterminated_quote() -> str:
    for fn in (swd.tokenize_posix, swd.tokenize_powershell):
        got = fn("Set-Content -Path f -Value @'")
        if not got:
            raise AssertionError(f"{fn.__name__} must fall back to a whitespace split, not raise")
    return "both tokenizers fall back to a whitespace split on an unterminated quote"


def main() -> int:
    tests = [
        ("first_line(): truncates at newline", test_first_line_truncates_at_newline),
        ("first_line(): single line unchanged", test_first_line_single_line_unchanged),
        ("segments_or_whole(): splits top level", test_segments_or_whole_splits_top_level),
        ("segments_or_whole(): respects supplied segments", test_segments_or_whole_respects_supplied_segments),
        ("segments_or_whole(): falls back to whole command", test_segments_or_whole_falls_back_to_whole_command),
        ("segments_or_whole(): fallback needs real content", test_segments_or_whole_fallback_needs_real_content),
        ("neutralize(): length preserving", test_neutralize_is_length_preserving),
        ("neutralize(): escaped apostrophe in prose", test_neutralize_escaped_apostrophe_in_unquoted_prose),
        ("neutralize(): single-quoted backslash untouched", test_neutralize_leaves_single_quoted_backslash_alone),
        ("neutralize(): escaped quote inside double quotes", test_neutralize_escaped_double_quote_inside_double_quotes),
        ("mask(): neutralizes heredoc marker", test_mask_neutralizes_heredoc_marker),
        ("mask(): preserves length", test_mask_preserves_length),
        ("mask(): redirect survives heredoc opener", test_mask_keeps_redirect_visible_after_heredoc_opener),
        ("next_token(): plain and quoted", test_next_token_plain_and_quoted),
        ("find_redirect_targets(): > and >>", test_find_redirect_targets_single_and_append),
        ("find_redirect_targets(): ignores quoted operator", test_find_redirect_targets_ignores_quoted_operator),
        ("find_redirect_targets(): multiple in order", test_find_redirect_targets_multiple_in_order),
        ("find_redirect_targets(): accepts shared mask", test_find_redirect_targets_accepts_shared_mask),
        ("tokenize_posix(): strips quotes", test_tokenize_posix_strips_quotes_and_escapes),
        ("tokenize_powershell(): preserves backslashes", test_tokenize_powershell_preserves_backslashes),
        ("tokenizers: fall back on unterminated quote", test_tokenizers_fall_back_on_unterminated_quote),
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
