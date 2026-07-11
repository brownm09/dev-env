#!/usr/bin/env python3
"""Output-contract + ASCII-literal gates for wired hooks (dev-env#720, ADR-103).

Claude Code delivers a hook's output through different physical channels depending
on the hook event and the exit code, and getting the mapping wrong is silent by
construction (an advisory on the wrong stream simply never appears). The contract
is encoded once in `_hookout` (ADR-103); this file is the mechanical gate that
flags a hook emitting into the void, so the class stays fixed once the migrations
(PRs 5-7) route each hook through `_hookout`.

Per-event contract (from `_hookout`'s table; `STDOUT_MODEL_VISIBLE_EVENTS` is the
single source of truth for which events make exit-0 stdout model-visible):
  * exit-0 stdout reaches the MODEL only on the context events
    (UserPromptSubmit / SessionStart / UserPromptExpansion); everywhere else it is
    transcript-only (invisible to the model).
  * exit-0 stderr is invisible on EVERY event.
  * on exit 2, stderr reaches the model and stdout is ignored on EVERY event.

Three output-contract checks, each an "explicit shape" (plan gotcha #6):
  A (stderr -> exit 0): a `sys.stderr.write(...)` / `print(..., file=sys.stderr)`
     whose governing exit is 0 -- invisible everywhere.
  B (bare stdout -> exit 0, non-context event): a *bare* `print(...)` /
     `sys.stdout.write` (plain text) whose governing exit is 0, in a hook wired
     ONLY to non-context events -- model-invisible there. A hook also wired to a
     context event is not flagged (the same emission is legitimately model-visible
     on that event), and a `json.dumps(...)`-wrapped stdout write is not flagged
     either: that is the structured `{"systemMessage": ...}` channel, which the
     contract delivers to the USER on ANY event at exit 0 (so it is not invisible).
  C (stdout -> exit 2): a stdout write whose governing exit is 2 -- silently
     dropped, since exit 2 ignores stdout. NOT json.dumps-exempt: a systemMessage
     JSON printed on an exit-2 path is dropped exactly like bare text.

Plus an ASCII-literal lint: a non-`.isascii()` string literal passed DIRECTLY to a
raw-stream call (`print` / `sys.std*.write`) crashes or vanishes under Claude Code's
cp1252 hook-output pipe on Windows (dev-env#355/#670). A `json.dumps(...)` argument
is exempt: `ensure_ascii=True` (the default) escapes non-ASCII to \\uXXXX on the
wire. This is the `.isascii()` guarantee `_hookout`'s `ascii_sanitize` /
`ensure_ascii` enforce for the migrated hooks.

Detection limitations (documented, per gotcha #6):
  - The non-ASCII literal must appear directly in the emission call's args. A
    literal reaching the stream INDIRECTLY -- through a variable or a helper's
    return value -- is not detected. usage-snapshot is the worked example: its
    emoji come from `status_emoji()` and reach stderr via the built `snapshot`
    string, so this lint flags it only via an incidental direct em-dash (L486);
    the emoji flow is covered by PR5's per-hook `.isascii()` self-pin instead.
  - Check B's `json.dumps` exemption is blanket: a `json.dumps({"systemMessage":
    ...})` stdout write is the correct user channel (delivered on any event at
    exit 0), but a `json.dumps({"hookSpecificOutput": {"additionalContext": ...}})`
    write on a NON-context event would be invisible (additionalContext is
    context-events-only) and is NOT flagged. No wired hook emits additionalContext
    on a non-context event today; a dedicated structured-channel check is deferred.

Governing exit (reaching approximation): from an emission, scan forward through the
rest of its block, then ascend to each enclosing block's remainder, to the scope
end. The first literal `sys.exit(N)` / `raise SystemExit(N)` reached wins; a `return
<int literal>` -> that code (a `sys.exit(main())` entrypoint propagates main's
return value as the exit code), and a bare `return` / non-literal return / falling
off the scope end -> exit 0. Compound statements passed *over* (an `if` after the
emission) are treated as pass-through, and cross-function pairing (a helper that
emits then returns to a caller which exits 2) is NOT traced -- both are documented
approximations.

For **Check A (stderr)** this can only *over*-flag (-> an allowlist entry), never
miss: the dominant real shape (block reason then `sys.exit(2)`) and the
if/else-branch-then-exit-2 shape both resolve to gov 2 by ascent and so are not
flagged, and the only error direction left is calling a genuinely-invisible stderr
write "co-located with an exit 2" it is not -- i.e. over-flagging. **Check C
(stdout->exit 2) does have a cross-function blind spot**: a helper that writes
stdout then returns to a caller which exits 2 is classified gov 0, so the dropped
stdout escapes Check C (and if it is a `json.dumps({"systemMessage": ...})` write it
escapes Check B's json exemption too -- the "escape both flag and allowlist" case).
No wired hook uses that shape today (every stdout emitter co-locates its exit); a
cross-function structured-channel check is deferred (see the Check-B limitation
below).

Two-sided allowlists (the `test_no_crude_command_substring_checks.py` mechanism: a
stale entry fails the suite too). `_OUTPUT_CONTRACT_ALLOWLIST` maps `(script, check)`
-> the count of currently-accepted offense lines for that check in that script -- a
hook is a known offender for check A/B/C until every one of that check's sites is
migrated onto `_hookout`, at which point the entry goes stale and must be removed. A
*new* offense line added to an already-listed hook makes the live count EXCEED the
recorded count and fails the gate (the "grew" bucket -- mirroring the sibling's
`duplicated` check, so a second offense can't hide behind an existing entry; a
migration that removes some-but-not-all lines leaves the count higher than live,
which is tolerated as harmless over-count until the entry is fully removed).
`_NONASCII_EMISSION_ALLOWLIST` maps `script -> count` the same way.

Two-sided allowlists (the `test_no_crude_command_substring_checks.py` mechanism: a
stale entry fails the suite too). `_OUTPUT_CONTRACT_ALLOWLIST` is keyed by
`(script, check)` -- a hook is a known offender for check A/B/C until every one of
that check's sites is migrated onto `_hookout`, at which point the entry goes stale
and must be removed. `_NONASCII_EMISSION_ALLOWLIST` is keyed by script.

Usage:
    py -3 claude/scripts/tests/test_hook_output_contract.py

Exit 0 = all pass.
"""

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import _hook_wiring as wiring  # noqa: E402
from _hookout import STDOUT_MODEL_VISIBLE_EVENTS  # noqa: E402  (the SSOT, ADR-103)


# ---------------------------------------------------------------------------
# Two-sided allowlists (populated with current offenders; migrations shrink them)
# Verified against origin/main @ 2cc6afa (2026-07-11).
# ---------------------------------------------------------------------------
# {(script, check): count} — check in {"A","B","C"}, count = currently-accepted
# offense lines for that check in that script. A hook stays listed until every site
# of that check is routed through _hookout (PRs 5-7); then it drops off `offenses`
# entirely and the entry goes stale (must be deleted). A NEW offense line in a listed
# hook pushes the live count above `count` and fails the gate (the "grew" bucket), so
# a second offense can't hide behind an existing entry. Line numbers are deliberately
# NOT recorded here (they rot); the gate prints live line numbers on failure.
# Migration owner per ADR-103:
#   PR5 -> post-pr-merge-pull, post-pr-merge-reclaim
#   PR6 -> token-tracker, journal-stop-check (checks 2-3), posttooluse-inert-advisory
#   (post-compact was surfaced by this gate, not in the named T1 set -- its exit-0
#    stderr status is invisible on PostCompact; follow-up sweep dev-env#727.)
_OUTPUT_CONTRACT_ALLOWLIST: dict[tuple[str, str], int] = {
    ("journal-stop-check.py", "B"): 1,        # checks 2-3 print()+exit0 (PR6)
    ("post-compact.py", "A"): 4,              # stderr status on PostCompact (follow-up)
    ("post-pr-merge-pull.py", "A"): 8,        # stderr status/park warnings, all exit 0 (PR5)
    ("post-pr-merge-reclaim.py", "A"): 1,     # stderr status, exit 0 (PR5)
    ("posttooluse-inert-advisory.py", "B"): 1,  # bare stdout on Stop, exit 0 (PR6)
    ("token-tracker.py", "A"): 1,             # stderr diagnostic, exit 0 (PR6)
    ("token-tracker.py", "B"): 2,             # stdout echoes on Stop, exit 0 (PR6)
}

# {script: count} — scripts emitting a non-ASCII string literal DIRECTLY in a
# raw-stream call (mostly em-dash U+2014 / ellipsis U+2026 -- cp1252-safe but not
# .isascii(), the stronger _hookout guarantee), with the count of such emission
# lines. PR5 clears usage-snapshot (its emoji reach stderr via a variable -- see the
# indirection limitation in the module docstring -- so it is flagged here only via a
# direct em-dash; PR5's own .isascii() pin covers the emoji). PR5 also clears
# post-pr-merge-pull/reclaim; PR6 token-tracker; PR7 dev-env-sync. post-compact /
# post-merge-tile-checkpoint / pre-merge-findings-gate are follow-up sweep targets
# (dev-env#727).
_NONASCII_EMISSION_ALLOWLIST: dict[str, int] = {
    "dev-env-sync.py": 4,
    "post-compact.py": 1,
    "post-merge-tile-checkpoint.py": 1,
    "post-pr-merge-pull.py": 7,
    "post-pr-merge-reclaim.py": 1,
    "pre-merge-findings-gate.py": 1,
    "token-tracker.py": 2,
    "usage-snapshot.py": 1,
}


# ---------------------------------------------------------------------------
# Emission detection + governing-exit reaching approximation (pure)
# ---------------------------------------------------------------------------

def _is_sys_std_stream(node: ast.AST) -> str | None:
    """'stdout'/'stderr' if *node* is `sys.stdout` / `sys.stderr`, else None.

    Requires the `sys` base explicitly so a captured subprocess stream
    (`proc.stdout.write(...)`, `result.stderr`) is NOT mistaken for a std-stream
    emission (dev-env#726 review). The `from sys import stderr` bare-name form is
    not matched -- no wired hook uses it; the dominant `sys.stderr` attribute form
    is what the hooks and _hookout use."""
    if (
        isinstance(node, ast.Attribute)
        and node.attr in ("stdout", "stderr")
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    ):
        return node.attr
    return None


def _emission_stream(call: ast.Call) -> str | None:
    """'stdout' / 'stderr' if *call* is a raw output write, else None.

    `print(...)` -> 'stdout' unless `file=sys.stderr` (-> 'stderr'); an explicit
    `file=sys.stdout` -> 'stdout'. `sys.stdout.write` / `sys.stderr.write` -> that
    stream. A `print(file=<other>)` (e.g. an open log file, a captured
    `proc.stdout`) -> None (not a std-stream emission)."""
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr == "write":
        return _is_sys_std_stream(f.value)
    if isinstance(f, ast.Name) and f.id == "print":
        for kw in call.keywords:
            if kw.arg == "file":
                return _is_sys_std_stream(kw.value)  # file=<non-sys-stream> -> None
        return "stdout"
    return None


def _literal_exit(stmt: ast.stmt) -> int | None:
    """If *stmt* is a definite `sys.exit(N)` / `exit(N)` / `raise SystemExit(N)`
    with a literal int, return N; else None."""
    call = None
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
    elif isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
        call = stmt.exc
    if call is None:
        return None
    f = call.func
    is_exit = (
        (isinstance(f, ast.Attribute) and f.attr == "exit")
        or (isinstance(f, ast.Name) and f.id in ("exit", "SystemExit"))
    )
    if is_exit and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, int):
        return call.args[0].value
    return None


_COMPOUND = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)
_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _continuation_exit(tails) -> int:
    """Governing exit for an emission whose forward+ascending continuation is
    *tails* (a list of (stmts, start_index), innermost first). First literal exit
    wins; a return or running off the end -> 0."""
    for stmts, start in tails:
        for s in stmts[start:]:
            code = _literal_exit(s)
            if code is not None:
                return code
            if isinstance(s, ast.Return):
                # `return <int literal>` -> that exit code (a `sys.exit(main())`
                # entrypoint propagates main's return value as the process exit
                # code; treating it as the governing exit matches that far better
                # than assuming 0 -- dev-env#726 review). A bare `return`, `return
                # None`, or a non-literal return -> fall-through exit 0.
                if isinstance(s.value, ast.Constant) and isinstance(s.value.value, int):
                    return s.value.value
                return 0
            # compound / plain statement -> pass through (documented approximation)
    return 0


def _child_blocks(stmt):
    blocks = []
    for field in ("body", "orelse", "finalbody"):
        b = getattr(stmt, field, None)
        if b:
            blocks.append(b)
    if isinstance(stmt, ast.Try):
        for h in stmt.handlers:
            blocks.append(h.body)
    return blocks


def _emits_json(call: ast.Call) -> bool:
    """True if the emission's first positional arg is a `json.dumps(...)` -- the
    structured systemMessage/additionalContext channel, not a bare-text write."""
    return bool(call.args) and _is_json_dumps(call.args[0])


def _walk_scope(stmts, outer_tails, results):
    """Record (lineno, stream, governing_exit, is_json) for every emission in this
    scope's statement list, recursing nested blocks but NOT nested function/class
    scopes."""
    for i, stmt in enumerate(stmts):
        tails = [(stmts, i + 1)] + outer_tails
        if isinstance(stmt, _SCOPE):
            continue  # separate scope, walked independently
        if isinstance(stmt, _COMPOUND):
            for block in _child_blocks(stmt):
                _walk_scope(block, tails, results)
        else:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    stream = _emission_stream(node)
                    if stream:
                        results.append(
                            (stmt.lineno, stream, _continuation_exit(tails), _emits_json(node))
                        )


def analyze_emissions(source: str, filename: str = "<string>"):
    """Return [(lineno, stream, governing_exit, is_json), ...] for every raw-stream
    emission, across the module top level and each function scope. `is_json` is True
    for a `json.dumps(...)`-wrapped write (the structured channel)."""
    tree = ast.parse(source, filename=filename)
    results: list[tuple[int, str, int, bool]] = []
    _walk_scope(tree.body, [], results)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _walk_scope(node.body, [], results)
    return results


# ---------------------------------------------------------------------------
# ASCII-literal detection (pure)
# ---------------------------------------------------------------------------

def _is_json_dumps(node: ast.AST) -> bool:
    """True if *node* is a `json.dumps(...)` call with ensure_ascii NOT set to a
    literal False (default True escapes non-ASCII on the wire -> exempt)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "dumps"):
        return False
    for kw in node.keywords:
        if kw.arg == "ensure_ascii" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return False
    return True


def _has_nonascii_str(node: ast.AST) -> bool:
    """True if *node*'s subtree has a non-ASCII str Constant, pruning json.dumps()
    subtrees (their output is ASCII-escaped on the wire)."""
    if _is_json_dumps(node):
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and not node.value.isascii():
        return True
    return any(_has_nonascii_str(c) for c in ast.iter_child_nodes(node))


def analyze_nonascii_emissions(source: str, filename: str = "<string>") -> list[int]:
    """Return line numbers of raw-stream emissions whose emitted positional args
    carry a non-ASCII str literal (excluding json.dumps-wrapped args)."""
    tree = ast.parse(source, filename=filename)
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _emission_stream(node):
            if any(_has_nonascii_str(arg) for arg in node.args):
                lines.append(node.lineno)
    return sorted(set(lines))


# ---------------------------------------------------------------------------
# Real-repo scan
# ---------------------------------------------------------------------------

def _scan_output_contract():
    """Return {(script, check): [linenos]} for every output-contract offense."""
    settings = wiring.load_settings()
    events_by_script = wiring.wired_script_events(settings)
    offenses: dict[tuple[str, str], list[int]] = {}
    for script in wiring.wired_scripts(settings):
        events = events_by_script[script]
        only_noncontext = not (events & STDOUT_MODEL_VISIBLE_EVENTS)
        source = (wiring.SCRIPTS_DIR / script).read_text(encoding="utf-8")
        for lineno, stream, gov, is_json in analyze_emissions(source, filename=script):
            check = None
            if stream == "stderr" and gov == 0:
                check = "A"
            elif stream == "stdout" and gov == 0 and only_noncontext and not is_json:
                check = "B"
            elif stream == "stdout" and gov == 2:
                check = "C"
            if check:
                offenses.setdefault((script, check), []).append(lineno)
    return offenses


def _scan_nonascii():
    """Return {script: [linenos]} for every non-ASCII raw emission."""
    settings = wiring.load_settings()
    out: dict[str, list[int]] = {}
    for script in wiring.wired_scripts(settings):
        source = (wiring.SCRIPTS_DIR / script).read_text(encoding="utf-8")
        lines = analyze_nonascii_emissions(source, filename=script)
        if lines:
            out[script] = lines
    return out


# ---------------------------------------------------------------------------
# Detector self-tests (synthetic fixtures)
# ---------------------------------------------------------------------------

def test_stderr_then_exit0_is_governing_0() -> str:
    src = "def main():\n    sys.stderr.write('x')\n    sys.exit(0)\n"
    assert analyze_emissions(src) == [(2, "stderr", 0, False)], analyze_emissions(src)
    return "stderr write then sys.exit(0) -> governing 0"


def test_stderr_then_exit2_is_governing_2() -> str:
    src = "def main():\n    sys.stderr.write('x')\n    sys.exit(2)\n"
    assert analyze_emissions(src) == [(2, "stderr", 2, False)], analyze_emissions(src)
    return "stderr write then sys.exit(2) -> governing 2 (a correct block, not flagged A)"


def test_emission_in_if_branch_ascends_to_exit2() -> str:
    # The post-tool-use 593/601 shape: emit inside an if/else, sys.exit(2) after.
    src = (
        "def main():\n"
        "    if cond:\n        print('a', file=sys.stderr)\n"
        "    else:\n        print('b', file=sys.stderr)\n"
        "    sys.exit(2)\n"
    )
    got = analyze_emissions(src)
    assert sorted(got) == [(3, "stderr", 2, False), (5, "stderr", 2, False)], got
    return "emit in if/else, sys.exit(2) after -> both ascend to governing 2 (not flagged)"


def test_bare_print_falls_through_to_0() -> str:
    src = "def main():\n    print('hello')\n"
    assert analyze_emissions(src) == [(2, "stdout", 0, False)], analyze_emissions(src)
    return "bare print, no exit (main fall-through) -> stdout governing 0"


def test_print_file_stderr_is_stderr() -> str:
    src = "def main():\n    print('x', file=sys.stderr)\n    sys.exit(0)\n"
    assert analyze_emissions(src) == [(2, "stderr", 0, False)], analyze_emissions(src)
    return "print(file=sys.stderr) classified as stderr"


def test_print_file_other_is_not_a_stream_emission() -> str:
    src = "def main():\n    print('x', file=logf)\n    sys.exit(0)\n"
    assert analyze_emissions(src) == [], analyze_emissions(src)
    return "print(file=<log file>) -> not a std-stream emission (ignored)"


def test_stdout_then_exit2_is_governing_2() -> str:
    src = "def main():\n    print('x')\n    sys.exit(2)\n"
    assert analyze_emissions(src) == [(2, "stdout", 2, False)], analyze_emissions(src)
    return "stdout then sys.exit(2) -> governing 2 (Check C shape)"


def test_json_dumps_stdout_flagged_is_json_true() -> str:
    # print(json.dumps({"systemMessage": ...})) on exit 0 is the correct USER
    # channel (delivered on any event) -- is_json True so _scan_output_contract
    # exempts it from Check B. The post-compact L108 shape.
    src = "def main():\n    print(json.dumps({'systemMessage': 'hi'}))\n"
    assert analyze_emissions(src) == [(2, "stdout", 0, True)], analyze_emissions(src)
    return "print(json.dumps({'systemMessage':...})) -> is_json True (Check B exempts the structured user channel)"


def test_nonascii_in_raw_emission_detected() -> str:
    src = "def main():\n    print('over \\U0001F534', file=sys.stderr)\n"
    assert analyze_nonascii_emissions(src) == [2], analyze_nonascii_emissions(src)
    return "emoji literal in a raw stderr print -> flagged"


def test_nonascii_in_json_dumps_is_exempt() -> str:
    src = "def main():\n    sys.stdout.write(json.dumps({'m': 'over \\u2264'}))\n"
    assert analyze_nonascii_emissions(src) == [], analyze_nonascii_emissions(src)
    return "non-ASCII inside json.dumps(...) -> exempt (ensure_ascii escapes it)"


def test_nonascii_in_json_dumps_ensure_ascii_false_not_exempt() -> str:
    src = "def main():\n    sys.stdout.write(json.dumps({'m': '\\u2264'}, ensure_ascii=False))\n"
    assert analyze_nonascii_emissions(src) == [2], analyze_nonascii_emissions(src)
    return "json.dumps(..., ensure_ascii=False) -> NOT exempt (non-ASCII reaches the wire)"


def test_ascii_only_raw_emission_not_flagged() -> str:
    src = "def main():\n    print('plain ascii', file=sys.stderr)\n"
    assert analyze_nonascii_emissions(src) == [], analyze_nonascii_emissions(src)
    return "ASCII-only literal in a raw emission -> not flagged"


def test_emission_stream_requires_sys_base() -> str:
    # A captured subprocess stream is NOT a std-stream emission (dev-env#726).
    assert analyze_emissions("def main():\n    proc.stdout.write('x')\n") == [], "proc.stdout.write should not match"
    assert analyze_emissions("def main():\n    print('x', file=proc.stderr)\n") == [], "file=proc.stderr should not match"
    got = analyze_emissions("def main():\n    sys.stdout.write('x')\n")
    assert got == [(2, "stdout", 0, False)], got
    return "only sys.stdout/sys.stderr count; proc.stdout / file=proc.stderr are ignored"


def test_return_int_literal_is_that_governing_exit() -> str:
    # `sys.stderr.write(r); return 2` -> gov 2 (a sys.exit(main()) entrypoint
    # propagates the return value), so a blocking stderr write is not false-flagged A.
    got = analyze_emissions("def main():\n    sys.stderr.write('r')\n    return 2\n")
    assert got == [(2, "stderr", 2, False)], got
    # A bare return still means fall-through exit 0.
    got0 = analyze_emissions("def main():\n    sys.stderr.write('r')\n    return\n")
    assert got0 == [(2, "stderr", 0, False)], got0
    return "`return 2` -> gov 2; bare `return` -> gov 0"


def test_diff_grew_flags_new_offense_behind_entry() -> str:
    unexpected, stale, grew = _diff_against_allowlist({("x.py", "A"): 3}, {("x.py", "A"): 2})
    assert unexpected == set() and stale == set(), (unexpected, stale)
    assert grew == {("x.py", "A"): (3, 2)}, grew
    return "live count 3 > accepted 2 -> flagged as grown (a new offense can't hide behind the entry)"


def test_diff_shrink_is_tolerated() -> str:
    unexpected, stale, grew = _diff_against_allowlist({("x.py", "A"): 1}, {("x.py", "A"): 2})
    assert unexpected == set() and stale == set() and grew == {}, (unexpected, stale, grew)
    return "live count 1 < accepted 2 -> tolerated (harmless over-count from a partial migration)"


def test_diff_stale_when_fully_migrated() -> str:
    unexpected, stale, grew = _diff_against_allowlist({}, {("x.py", "A"): 2})
    assert stale == {("x.py", "A")} and unexpected == set() and grew == {}, (unexpected, stale, grew)
    return "no live offense for an allowlisted key -> stale (must be removed)"


def test_diff_unexpected_new_key() -> str:
    unexpected, stale, grew = _diff_against_allowlist({("y.py", "B"): 1}, {})
    assert unexpected == {("y.py", "B")} and stale == set() and grew == {}, (unexpected, stale, grew)
    return "a live key with no allowlist entry -> unexpected"


def test_diff_exact_match_clean() -> str:
    unexpected, stale, grew = _diff_against_allowlist({("x.py", "A"): 2}, {("x.py", "A"): 2})
    assert unexpected == set() and stale == set() and grew == {}, (unexpected, stale, grew)
    return "live count == accepted count -> clean"


def test_all_wired_commands_parse_to_a_script() -> str:
    # Mirror the safe-exit gate: this gate's scan set comes from wired_scripts(),
    # which drops None-script entries -- fail loudly here rather than relying on the
    # wiring lint's separate run (dev-env#726 review).
    unparsed = wiring.unparsed_commands(wiring.load_settings())
    assert not unparsed, "Wired commands not resolving to a .py script:\n  " + "\n  ".join(
        f"{e.event}/{e.matcher}: {e.command!r}" for e in unparsed
    )
    return "all wired commands resolve to a .py script (none silently dropped from this scan)"


# ---------------------------------------------------------------------------
# Repo-wide gates
# ---------------------------------------------------------------------------

def _fmt(pairs):
    return ", ".join(f"{s}:{c}" for s, c in sorted(pairs))


def _diff_against_allowlist(live_counts: dict, allowed: dict):
    """Compare live {key: count} against an allowlist {key: accepted_count}.

    Returns (unexpected, stale, grew):
      - unexpected: live keys with no allowlist entry (a new offense elsewhere).
      - stale: allowlist keys with no live offense (fully migrated -- remove them).
      - grew: {key: (live, accepted)} where live > accepted (a NEW offense line
        hiding behind an existing entry -- the sibling's `duplicated` check, keyed
        by count rather than the implicit-1 the literal-based sibling assumes).
    A live count BELOW the accepted count is tolerated (harmless over-count from a
    partial migration); only growth and full-removal are flagged, matching the
    sibling's growth-only stance.
    """
    live = set(live_counts)
    allowed_keys = set(allowed)
    unexpected = live - allowed_keys
    stale = allowed_keys - live
    grew = {k: (live_counts[k], allowed[k]) for k in allowed_keys & live if live_counts[k] > allowed[k]}
    return unexpected, stale, grew


def test_repo_wide_output_contract_gate() -> str:
    """Fail on an output-contract offense not in _OUTPUT_CONTRACT_ALLOWLIST, a stale
    allowlist entry (its check's sites all migrated onto _hookout), or an allowlisted
    entry whose live offense count GREW (a new invisible emission hiding behind it)."""
    offenses = _scan_output_contract()
    live_counts = {k: len(v) for k, v in offenses.items()}
    unexpected, stale, grew = _diff_against_allowlist(live_counts, _OUTPUT_CONTRACT_ALLOWLIST)

    if unexpected or stale or grew:
        lines = []
        if unexpected:
            lines.append("New/unlisted output-contract offenses (route via _hookout, or allowlist):")
            for (script, check) in sorted(unexpected):
                ls = ", ".join(f"L{n}" for n in offenses[(script, check)])
                desc = {
                    "A": "stderr -> exit 0 (invisible everywhere)",
                    "B": "bare stdout -> exit 0 on a non-context event (model-invisible)",
                    "C": "stdout -> exit 2 (dropped; exit 2 ignores stdout)",
                }[check]
                lines.append(f"  {script} [{check}: {desc}] at {ls}")
        if stale:
            lines.append("Stale _OUTPUT_CONTRACT_ALLOWLIST entries (migrated -- remove them):")
            for pair in sorted(stale):
                lines.append(f"  {pair!r}")
        if grew:
            lines.append(
                "Allowlisted output-contract offenses that GREW (a new invisible emission "
                "is hiding behind an existing entry -- fix it via _hookout, or bump the count "
                "if it is genuinely the same accepted offense):"
            )
            for (script, check), (now, was) in sorted(grew.items()):
                ls = ", ".join(f"L{n}" for n in offenses[(script, check)])
                lines.append(f"  {script} [{check}]: {was} -> {now} lines, now at {ls}")
        raise AssertionError("\n".join(lines))

    return (
        f"{len(_OUTPUT_CONTRACT_ALLOWLIST)} known offender(s) [{_fmt(_OUTPUT_CONTRACT_ALLOWLIST)}], "
        "0 unexpected, 0 stale, 0 grown"
    )


def test_repo_wide_nonascii_emission_gate() -> str:
    """Fail on a non-ASCII raw emission not in _NONASCII_EMISSION_ALLOWLIST, a stale
    allowlist entry, or an allowlisted script whose non-ASCII emission count GREW."""
    found = _scan_nonascii()
    live_counts = {k: len(v) for k, v in found.items()}
    unexpected, stale, grew = _diff_against_allowlist(live_counts, _NONASCII_EMISSION_ALLOWLIST)

    if unexpected or stale or grew:
        lines = []
        if unexpected:
            lines.append("Non-ASCII string literal(s) emitted to a raw stream (ASCII-ify / route via _hookout, or allowlist):")
            for script in sorted(unexpected):
                ls = ", ".join(f"L{n}" for n in found[script])
                lines.append(f"  {script} at {ls}")
        if stale:
            lines.append("Stale _NONASCII_EMISSION_ALLOWLIST entries (now ASCII-clean -- remove them):")
            for script in sorted(stale):
                lines.append(f"  {script!r}")
        if grew:
            lines.append(
                "Allowlisted non-ASCII emitters that GREW (a new non-ASCII raw emission -- "
                "ASCII-ify it, or bump the count if genuinely the same accepted offense):"
            )
            for script, (now, was) in sorted(grew.items()):
                ls = ", ".join(f"L{n}" for n in found[script])
                lines.append(f"  {script}: {was} -> {now} lines, now at {ls}")
        raise AssertionError("\n".join(lines))

    return (
        f"{len(_NONASCII_EMISSION_ALLOWLIST)} known offender(s) "
        f"[{', '.join(sorted(_NONASCII_EMISSION_ALLOWLIST))}], 0 unexpected, 0 stale, 0 grown"
    )


def main() -> int:
    tests = [
        ("stderr then exit 0 -> governing 0", test_stderr_then_exit0_is_governing_0),
        ("stderr then exit 2 -> governing 2", test_stderr_then_exit2_is_governing_2),
        ("emit in if/else ascends to exit 2", test_emission_in_if_branch_ascends_to_exit2),
        ("bare print falls through to 0", test_bare_print_falls_through_to_0),
        ("print(file=sys.stderr) is stderr", test_print_file_stderr_is_stderr),
        ("print(file=<other>) not a stream emission", test_print_file_other_is_not_a_stream_emission),
        ("stdout then exit 2 -> governing 2 (C)", test_stdout_then_exit2_is_governing_2),
        ("json.dumps stdout is_json True (Check B exempt)", test_json_dumps_stdout_flagged_is_json_true),
        ("non-ASCII in raw emission detected", test_nonascii_in_raw_emission_detected),
        ("non-ASCII in json.dumps exempt", test_nonascii_in_json_dumps_is_exempt),
        ("json.dumps ensure_ascii=False not exempt", test_nonascii_in_json_dumps_ensure_ascii_false_not_exempt),
        ("ASCII-only raw emission not flagged", test_ascii_only_raw_emission_not_flagged),
        ("emission stream requires sys base", test_emission_stream_requires_sys_base),
        ("return <int> is that governing exit", test_return_int_literal_is_that_governing_exit),
        ("diff: grew flags new offense behind entry", test_diff_grew_flags_new_offense_behind_entry),
        ("diff: shrink is tolerated", test_diff_shrink_is_tolerated),
        ("diff: stale when fully migrated", test_diff_stale_when_fully_migrated),
        ("diff: unexpected new key", test_diff_unexpected_new_key),
        ("diff: exact match clean", test_diff_exact_match_clean),
        ("all wired commands parse to a script", test_all_wired_commands_parse_to_a_script),
        ("repo-wide output-contract gate", test_repo_wide_output_contract_gate),
        ("repo-wide non-ASCII emission gate", test_repo_wide_nonascii_emission_gate),
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
