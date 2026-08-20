#!/usr/bin/env python3
"""dev-env#1031/#1033: migration coverage for the sibling hooks moved onto
_hookio's hardened read_command/read_tool_input_field/read_cwd/read_exit_code
helpers.

Two responsibilities in one file, since they're two views of the same fix:

1. MECHANICAL REGRESSION TEST (dev-env#1031 task item 3, /review's own
   suggestion on PR #1030). An AST-based scan asserting no
   claude/scripts/*.py file contains an unguarded "read tool_input/
   tool_response as a dict" chain. Two independent detector arms, since
   `/review` on PR #1035 found the first version of this file's detector had
   real, live blind spots:

   - **Inline arm** (`find_inline_offenses`): a single expression of the
     shape `X.get("tool_input"/"tool_response", <empty-dict-like>)` (or the
     `X.get(key) or <empty-dict-like>` spelling) immediately used via
     `.get(...)` or `[...]`. `X` may be ANY expression (`self.data`,
     `payload[0]`, `json.loads(raw)`, not just a bare name) and the
     empty-dict-like default may be `{}` or `dict()`.
   - **Two-statement arm** (`find_two_statement_offenses`): the identical
     risky extraction assigned to a name in one statement, then used via
     `.get(...)`/`[...]` in a LATER statement in the same block with no
     intervening `isinstance(name, dict)` / falsy-or-None check on that name.
     This is the dominant house style for this exact read in this directory
     (`memory-write-advisory.py`, `pre-tool-use-canonical-mutate-guard.py`,
     and others each read `tool_input` this way) -- some pair it with a
     guard (safe), some didn't until dev-env#1033's review round (found live,
     fixed in the same PR that hardened this detector).

   Both arms recognize the SAME underlying risk, not just the ORIGINAL
   grep's narrow `X.get(key, {}).get(...)` shape: `data.get(key, {})` only
   substitutes `{}` when the KEY is absent, and `data.get(key) or {}` only
   substitutes `{}` when the VALUE is falsy -- neither protects against a
   PRESENT, TRUTHY, non-dict value (a non-empty string, list, or number),
   which survives either spelling unchanged and raises on the next
   `.get()`/`[...]`.

2. MALFORMED-PAYLOAD SMOKE TEST (dev-env#1031 task item 2) for 12 of the 13
   dev-env#1033-migrated files (pre-tool-use-worktree-path-check.py's own
   coverage lives in test_worktree_path_check.py instead, since that file
   already carries a loaded-module reference this file would otherwise
   duplicate). Each hook's real `main()` is called DIRECTLY -- bypassing its
   `__main__` try/except safe-exit wrapper -- with the dev-env#1028 payload
   shapes (`tool_input: null` + `cwd: null` combined; a non-dict top-level
   payload), asserting a clean, expected `SystemExit` rather than an
   uncaught exception.

   Direct calls, NOT subprocess: an earlier revision of this file drove
   each hook via `subprocess.run([sys.executable, hook_path], ...)` and
   asserted only the process's exit code. `/review` on PR #1035 verified,
   by running the PRE-FIX code against these exact payloads, that every one
   of those subprocess assertions passed IDENTICALLY whether or not the fix
   was present -- because every hook's own `__main__` guard (`except
   Exception: sys.exit(0)`, or `_fail_closed()` -> exit 2) launders a crash
   into the SAME exit code a correct, deliberate early-return also
   produces. An exit code observed from OUTSIDE the process cannot tell
   "handled cleanly" from "crashed, caught by the outer safe-exit guard"
   apart. Calling `main()` directly removes that safety net: a pre-fix
   crash now propagates as an uncaught Python exception IN THIS TEST
   PROCESS, which this file's own test runner reports as a failure (see
   `main()` below) -- a genuinely discriminating assertion. Mirrors
   test_usage_snapshot.py's `_run_main_capturing_trace` pattern (stdin
   monkeypatch, `SystemExit` capture) and test_worktree_path_check.py's
   module-loading pattern (`_load_module()`).

   `HOOK_HEARTBEAT_DIR_OVERRIDE` (a dev-env#1031/#1033 review finding, added
   to `_hookutil.record_heartbeat` in the same PR) is set for the duration
   of this file's `main()` so these direct calls -- which, unlike the old
   subprocess design, run every migrated hook's own unconditional
   `_hookutil.record_heartbeat(...)` call IN THIS PROCESS -- do not write to
   the developer's real `~/.claude/scratch/hook-heartbeat/` and silently
   blind `hook-liveness-check.py`'s staleness detector (up to its 7-day
   cadence, ADR-106) on every test run.

   read_command/read_tool_input_field/read_cwd/read_exit_code's own
   correctness (normal/missing/None/non-dict/non-string inputs, the
   required `default` contract) is already exhaustively covered in
   test_hookio.py -- NOT re-tested per-caller here. Each hook's own existing
   pure-helper test file is unaffected and still covers its own business
   logic; this file only proves the migrated main() dispatch reaches the
   helpers and doesn't crash.

   Scope note on tool_response/exit_code coverage: of the six migrated
   files that also read exit_code, only post-tool-use.py and
   pr-merge-reminder.py read it UNCONDITIONALLY (right after command/cwd
   extraction, before any business-logic branch) -- safe to drive with an
   ordinary non-matching command ("git status"), since the exit_code VALUE
   never changes the outcome for a non-matching command. The other four
   (post-merge-tile-checkpoint.py, post-pr-merge-project.py,
   post-pr-merge-pull.py, post-pr-merge-reclaim.py) read exit_code only
   INSIDE the "marker didn't confirm the merge" fallback branch, gated
   behind should_confirm_via_gh() -- reaching that line via a genuinely
   malformed tool_response (which also empties `output`, so no marker can
   survive) makes should_confirm_via_gh() return True, which would then
   attempt a REAL `gh pr view` subprocess call. Forcing that call (or
   monkeypatching confirm_merge_via_gh, which none of these four files' own
   test files do -- ADR-050 Amendment 3/8's "the repo avoids subprocess
   mocks" convention) is exactly the kind of environment-dependent
   assertion dev-env#1028's own post-review CI failure warned against (PR
   #1030: an assertion that passed locally and failed in CI for reasons
   unrelated to the fix). These four files' own tool_input:null coverage
   below already proves the PRIMARY, most-severe crash class (the exact
   dev-env#1028 incident shape) doesn't crash their main() dispatch; their
   exit_code line's correctness rests on read_exit_code's own exhaustive
   test_hookio.py coverage plus the per-file default-value verification
   recorded in each file's own inline migration comment and ADR-050
   Amendment 28 -- a deliberate scope boundary, not an oversight.

Usage:
    py -3 claude/scripts/tests/test_sibling_hooks_hardened_io.py

Exit 0 = all pass.
"""

import ast
import importlib.util
import io
import json
import os
import sys
from collections import Counter
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Part 1 -- AST-based regression test
# ---------------------------------------------------------------------------

# (filename, field, lineno) triples known to still carry an unguarded chain.
# Keyed by line number (not just filename+field, dev-env#1033 review finding
# on the first version of this file): a (filename, field) key alone can only
# ever represent ONE justified exception per file for a given field -- a
# second, genuinely distinct call site reusing the same field in the same
# file would be silently absorbed into (or collide with) the first entry's
# allowance. Line-keying, mirroring test_no_crude_command_substring_checks.py's
# own reasoning for why it matches on literal text rather than line number
# (the opposite tradeoff, chosen there because THAT detector's literals are
# stable across refactors while line numbers are not) -- here the risk runs
# the other way: two distinct sites can share the exact same (filename,
# field), so the extra line-number component is what keeps them distinguishable.
# Empty as of dev-env#1033: this migration's whole point is that no live
# offense should remain anywhere in the tree -- see the review-round Follow-up
# note in ADR-050 Amendment 28 for the five additional files (six sites) this
# hardened detector found and fixed beyond the originally-scoped 13.
_KNOWN_EXCEPTIONS: set[tuple[str, str, int]] = set()


def _is_empty_dict_like(node: ast.AST) -> bool:
    """True if *node* is `{}` or `dict()` -- an empty-dict-literal spelling."""
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "dict" and not node.args and not node.keywords):
        return True
    return False


def _matched_risky_field(node: ast.AST) -> str | None:
    """If *node* is one of the risky "extract tool_input/tool_response as a
    dict" shapes this whole bug class is built on, return the matched field
    ("tool_input" or "tool_response"); else None.

    Two shapes, both sharing the identical risk: whatever downstream code
    reads off the result assumes it's a dict, but a present-and-TRUTHY
    non-dict value (a non-empty string, a non-empty list, a non-zero number)
    survives BOTH of them unchanged:
      (a) `X.get(key, {})`             -- .get()'s own default only substitutes
                                           when the KEY is absent, not when the
                                           VALUE is falsy-but-present.
      (b) `X.get(key, <anything>) or {}`  -- the `or` only substitutes when the
                                           VALUE is falsy (None, "", 0, [], {}),
                                           not when it's truthy-but-wrong-type.
    `X` may be ANY expression, not just a bare `Name` -- `self.data.get(...)`,
    `payload[0].get(...)`, `json.loads(raw).get(...)` are the identical bug
    in a different receiver shape (dev-env#1033 review finding: the first
    version of this detector required a bare-Name receiver and silently
    missed all three).
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        # Exactly 2 args required here: `X.get(key)` with NO default at all
        # is a different bug (KeyError risk on a missing key), out of scope
        # for this detector -- only `X.get(key, {})` (or `dict()`) is the
        # "wrong default silently substitutes for the wrong condition" shape.
        # The BoolOp branch below separately handles `X.get(key) or {}`
        # (no default, backstopped by `or` instead) -- that IS this shape.
        if len(node.args) != 2:
            return None
        key_arg, default_arg = node.args
        if not (isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str)
                and key_arg.value in ("tool_input", "tool_response")):
            return None
        if not _is_empty_dict_like(default_arg):
            return None  # a non-empty-dict default is not the established risky shape
        return key_arg.value
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and len(node.values) == 2:
        left, right = node.values
        if not _is_empty_dict_like(right):
            return None
        if isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute) and left.func.attr == "get":
            if not left.args:
                return None
            key_arg = left.args[0]
            if (isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str)
                    and key_arg.value in ("tool_input", "tool_response")):
                return key_arg.value
    return None


def find_inline_offenses(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, field) for each single-expression offense: a
    `_matched_risky_field`-matching node immediately used as the receiver of
    an outer `.get(...)` call or `[...]` subscript, in the SAME expression.
    """
    offenses: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            field = _matched_risky_field(node.func.value)
            if field:
                offenses.append((node.lineno, field))
        elif isinstance(node, ast.Subscript):
            field = _matched_risky_field(node.value)
            if field:
                offenses.append((node.lineno, field))
    return offenses


def _is_isinstance_check_on(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance"
            and bool(node.args) and isinstance(node.args[0], ast.Name) and node.args[0].id == name)


def _is_none_or_falsy_check_on(node: ast.AST, name: str) -> bool:
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Name) and node.operand.id == name):
        return True
    if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == name:
        if any(isinstance(op, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)) for op in node.ops):
            if any(isinstance(c, ast.Constant) and c.value is None for c in node.comparators):
                return True
    return False


def _guards_name(stmt: ast.AST, name: str) -> bool:
    """True if *stmt* is an `if` statement whose TEST contains a genuine
    `isinstance(name, ...)` or None/falsy check ON `name` specifically --
    deliberately narrow (not "does the name appear anywhere in the test"):
    an earlier version of this check matched any mention of the name,
    which incorrectly treated the name's OWN risky USE inside a test
    expression (e.g. `if some_fn(x.get("f", ""))`) as if it were a guard
    protecting that very use, silently swallowing the offense it should
    have caught. Deliberately permissive in the other direction -- matches
    the check anywhere in a compound boolean expression (`and`/`or`
    combinations), not just as the whole test -- since a false negative
    here (treating real, safe code as still-risky) only costs a
    to-be-reviewed _KNOWN_EXCEPTIONS entry, while a false positive (missing
    a guard that IS there) would leave a real bug undetected -- the
    direction this whole detector exists to avoid.
    """
    if not isinstance(stmt, ast.If):
        return False
    for sub in ast.walk(stmt.test):
        if _is_isinstance_check_on(sub, name) or _is_none_or_falsy_check_on(sub, name):
            return True
    return False


def find_two_statement_offenses(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, field) for each two-statement offense: `NAME =
    <risky extraction>` in one statement, then a `.get(...)`/`[...]` access
    on `NAME` in a LATER statement of the SAME block, with no intervening
    `if`-guard (per `_guards_name`) referencing `NAME` in between.

    Scans every statement-holding node's own direct `.body` list
    (`Module`, `FunctionDef`, `If`, `For`, `While`, `With`, `Try`, etc.) as
    one independent linear pass -- `ast.walk`'s own recursion visits nested
    blocks separately, each getting its OWN pass, so a use inside a nested
    `if` is caught by that inner block's scan, not lost. Deliberately NOT a
    full control-flow analysis (no branch-merge reasoning, no loop-aware
    widening) -- a straightforward same-block, same-nesting-level scan,
    which is what every real instance of this pattern in this codebase
    actually is (sequential statements in `main()`'s own body).
    """
    offenses: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        risky_names: dict[str, str] = {}  # name -> field, only while still unguarded
        for stmt in body:
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)):
                field = _matched_risky_field(stmt.value)
                if field:
                    risky_names[stmt.targets[0].id] = field
                    continue  # the assignment itself is not a USE
            for name in list(risky_names):
                if _guards_name(stmt, name):
                    del risky_names[name]
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "get":
                    base = sub.func.value
                    if isinstance(base, ast.Name) and base.id in risky_names:
                        offenses.append((sub.lineno, risky_names[base.id]))
                elif isinstance(sub, ast.Subscript):
                    base = sub.value
                    if isinstance(base, ast.Name) and base.id in risky_names:
                        offenses.append((sub.lineno, risky_names[base.id]))
    return offenses


def _scan_real_repo() -> dict[str, list[tuple[int, str]]]:
    """Run both detector arms over every top-level claude/scripts/*.py file
    (the tests/ subdirectory and __pycache__ are excluded automatically by
    the non-recursive glob, mirroring test_no_crude_command_substring_checks.py's
    identical scan). Returns {filename: [(lineno, field), ...]}."""
    results: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path.name)
            offenses = find_inline_offenses(tree) + find_two_statement_offenses(tree)
        except (SyntaxError, UnicodeDecodeError) as e:
            raise RuntimeError(f"failed to scan {path.name}: {e}") from e
        if offenses:
            results[path.name] = sorted(offenses)
    return results


def _diff_against_known_exceptions(
    found: dict[str, list[tuple[int, str]]], known_exceptions: set[tuple[str, str, int]]
) -> tuple[set[tuple[str, str, int]], set[tuple[str, str, int]]]:
    """Returns (unexpected, stale). Line-keyed (see _KNOWN_EXCEPTIONS'
    own comment for why), so there is no "duplicated" class to report here
    the way the sibling command-substring detector has -- two distinct
    (file, field) sites at different line numbers are already distinct
    keys, not a collision.
    """
    live_offenses = {
        (filename, field, lineno) for filename, offenses in found.items() for lineno, field in offenses
    }
    unexpected = live_offenses - known_exceptions
    stale = known_exceptions - live_offenses
    return unexpected, stale


# --- detector self-tests (synthetic fixtures -- no disk I/O) ---------------

def test_detects_live_assignment_chain() -> str:
    source = 'def f(data):\n    command = data.get("tool_input", {}).get("command", "")\n    return command\n'
    offenses = find_inline_offenses(ast.parse(source))
    assert offenses == [(2, "tool_input")], offenses
    return "live single-expression chain -> 1 offense"


def test_detects_tool_response_field() -> str:
    source = 'def f(data):\n    ec = data.get("tool_response", {}).get("exitCode", -1)\n    return ec\n'
    offenses = find_inline_offenses(ast.parse(source))
    assert offenses == [(2, "tool_response")], offenses
    return "tool_response variant -> 1 offense"


def test_detects_computed_outer_key() -> str:
    # pre-tool-use-worktree-path-check.py's actual pre-fix shape: the OUTER
    # .get()'s key is a computed expression, not the literal "command".
    source = 'def f(data, tool_name):\n    p = data.get("tool_input", {}).get(_PATH_FIELD[tool_name], "")\n    return p\n'
    offenses = find_inline_offenses(ast.parse(source))
    assert offenses == [(2, "tool_input")], offenses
    return "computed outer-key form (worktree-path-check.py's real pre-fix shape) -> 1 offense"


def test_detects_inline_non_assignment_usage() -> str:
    source = 'def f(data):\n    return len(data.get("tool_input", {}).get("command", ""))\n'
    offenses = find_inline_offenses(ast.parse(source))
    assert offenses == [(2, "tool_input")], offenses
    return "inline (non-assignment) usage -> still detected"


def test_detects_subscript_outer_accessor() -> str:
    source = 'def f(data):\n    return data.get("tool_input", {})["command"]\n'
    offenses = find_inline_offenses(ast.parse(source))
    assert offenses == [(2, "tool_input")], offenses
    return "subscript outer accessor ([...] instead of .get()) -> 1 offense"


def test_detects_dict_call_default() -> str:
    source = 'def f(data):\n    return data.get("tool_input", dict()).get("command", "")\n'
    offenses = find_inline_offenses(ast.parse(source))
    assert offenses == [(2, "tool_input")], offenses
    return "dict() spelled as the empty-default (not just {}) -> 1 offense"


def test_detects_non_name_bases() -> str:
    cases = {
        "self.data": "class C:\n    def f(self):\n        return self.data.get(\"tool_input\", {}).get(\"command\", \"\")\n",
        "subscript base": 'def f(payload):\n    return payload[0].get("tool_input", {}).get("command", "")\n',
        "call base": 'def f(raw):\n    return json.loads(raw).get("tool_input", {}).get("command", "")\n',
    }
    for label, source in cases.items():
        offenses = find_inline_offenses(ast.parse(source))
        assert offenses and offenses[0][1] == "tool_input", f"{label}: {offenses}"
    return "non-Name receiver bases (self.data / subscript / call) -> all detected (dev-env#1033 review finding)"


def test_detects_bare_or_inline() -> str:
    source = 'def f(data):\n    return (data.get("tool_input") or {}).get("command", "")\n'
    offenses = find_inline_offenses(ast.parse(source))
    assert offenses == [(2, "tool_input")], offenses
    return "`(X.get(key) or {}).get(...)` inline BoolOp form -> 1 offense (only protects falsy, not truthy, non-dict)"


def test_ignores_hardened_read_command_implementation() -> str:
    # _hookio.py's own read_command()/read_tool_input_field() -- the FIX --
    # must not trip either detector arm: it splits the isinstance guard onto
    # its own statement rather than chaining .get() calls inline, and IS
    # correctly recognized by find_two_statement_offenses as GUARDED (not
    # merely absent from find_inline_offenses).
    source = (
        "def read_tool_input_field(data, field):\n"
        "    if not isinstance(data, dict):\n"
        '        return ""\n'
        '    ti = data.get("tool_input") or {}\n'
        "    if not isinstance(ti, dict):\n"
        '        return ""\n'
        '    val = ti.get(field, "")\n'
        '    return val if isinstance(val, str) else ""\n'
    )
    tree = ast.parse(source)
    assert find_inline_offenses(tree) == []
    assert find_two_statement_offenses(tree) == []
    return "the hardened read_tool_input_field() implementation itself -> 0 offenses on both arms"


def test_ignores_missing_default_arg() -> str:
    source = 'def f(data):\n    return data.get("tool_input").get("command", "")\n'
    assert find_inline_offenses(ast.parse(source)) == []
    return "inner .get() with no default argument -> 0 offenses (a different bug -- KeyError risk -- out of scope)"


def test_ignores_non_empty_dict_default() -> str:
    source = 'def f(data):\n    return data.get("tool_input", {"x": 1}).get("command", "")\n'
    assert find_inline_offenses(ast.parse(source)) == []
    return "inner .get() with a non-empty-dict default -> 0 offenses (not the established risky shape)"


def test_ignores_unrelated_field_name() -> str:
    source = 'def f(data):\n    return data.get("other_field", {}).get("x", "")\n'
    assert find_inline_offenses(ast.parse(source)) == []
    return '"other_field" (not tool_input/tool_response) -> 0 offenses'


def test_ignores_comment_and_docstring_mentions() -> str:
    source = (
        "def f(data):\n"
        '    """Old code was data.get("tool_input", {}).get("command", "").  Now fixed."""\n'
        "    # data.get(\"tool_response\", {}).get(\"exitCode\", -1) -- the old bug\n"
        "    return read_command(data)\n"
    )
    tree = ast.parse(source)
    assert find_inline_offenses(tree) == []
    assert find_two_statement_offenses(tree) == []
    return "docstring + comment mentioning the pattern -> 0 offenses on both arms (invisible to ast.parse)"


def test_two_statement_detects_unguarded_real_shape() -> str:
    # memory-write-advisory.py's ACTUAL pre-fix shape (dev-env#1033 review
    # finding, live in the repo until this same PR fixed it): assign via
    # `or {}`, then use in a LATER statement's nested call arguments, with
    # NO isinstance guard anywhere in between.
    source = (
        "def f(data):\n"
        '    tool_input = data.get("tool_input", {}) or {}\n'
        "    if should_advise(\n"
        '        data.get("tool_name", ""),\n'
        '        tool_input.get("file_path", ""),\n'
        '        tool_input.get("content", ""),\n'
        "    ):\n"
        "        pass\n"
    )
    offenses = find_two_statement_offenses(ast.parse(source))
    assert offenses == [(5, "tool_input"), (6, "tool_input")], offenses
    return "the real, pre-fix memory-write-advisory.py shape -> 2 offenses (both unguarded .get() uses)"


def test_two_statement_ignores_properly_guarded_real_shape() -> str:
    # journal-shard-write-advisory.py's ACTUAL shape: the identical
    # assignment, but paired with an explicit isinstance check before use --
    # must NOT be flagged.
    source = (
        "def f(data):\n"
        '    tool_input = data.get("tool_input", {}) or {}\n'
        "    if not isinstance(tool_input, dict):\n"
        "        return\n"
        '    cwd = data.get("cwd", "") or ""\n'
        "    return candidate_paths(tool_input, cwd)\n"
    )
    assert find_two_statement_offenses(ast.parse(source)) == []
    return "a properly isinstance-guarded two-statement pattern -> 0 offenses (no false positive)"


def test_two_statement_guard_check_does_not_confuse_use_with_guard() -> str:
    # The bug in an earlier version of this detector: a naive "does the
    # name appear anywhere in the if's test" check treated the risky NAME'S
    # OWN unguarded use (inside the if's test expression) as if it were a
    # guard protecting itself, silently clearing the risk before the
    # use-check ever saw it. This fixture is exactly
    # test_two_statement_detects_unguarded_real_shape's source; kept as a
    # separate, narrowly-named test so a regression in `_guards_name`
    # specifically (as opposed to the detector as a whole) fails legibly.
    source = (
        "def f(data):\n"
        '    tool_input = data.get("tool_input", {}) or {}\n'
        '    if should_advise(tool_input.get("file_path", "")):\n'
        "        pass\n"
    )
    offenses = find_two_statement_offenses(ast.parse(source))
    assert offenses == [(3, "tool_input")], offenses
    return "a bare mention of the risky name inside an unrelated if-test is NOT mistaken for a guard"


def test_diff_reports_stale_and_unexpected() -> str:
    found = {"x.py": [(10, "tool_input")]}
    known = {("x.py", "tool_input", 99), ("y.py", "tool_response", 1)}
    unexpected, stale = _diff_against_known_exceptions(found, known)
    assert unexpected == {("x.py", "tool_input", 10)}, unexpected
    assert stale == {("x.py", "tool_input", 99), ("y.py", "tool_response", 1)}, stale
    return "a live offense at a different line than any allowlist entry -> unexpected; unmatched entries -> stale"


def test_diff_matches_known_exception_by_exact_line() -> str:
    found = {"x.py": [(10, "tool_input")]}
    known = {("x.py", "tool_input", 10)}
    unexpected, stale = _diff_against_known_exceptions(found, known)
    assert unexpected == set() and stale == set(), (unexpected, stale)
    return "an exact (file, field, line) match against _KNOWN_EXCEPTIONS -> 0 unexpected, 0 stale"


def test_repo_has_no_unexpected_or_stale_unguarded_chains() -> str:
    """The actual regression gate. Fails on (a) a live offense not covered
    by _KNOWN_EXCEPTIONS (a new or regressed unguarded chain anywhere in
    the tree, on either detector arm), or (b) a _KNOWN_EXCEPTIONS entry
    with no matching live offense (a stale exception left behind)."""
    found = _scan_real_repo()
    unexpected, stale = _diff_against_known_exceptions(found, _KNOWN_EXCEPTIONS)

    if unexpected or stale:
        lines = []
        if unexpected:
            lines.append("New/unlisted unguarded tool_input/tool_response chains found:")
            for filename, field, lineno in sorted(unexpected):
                lines.append(f"  {filename}:{lineno}: X.get({field!r}, ...) read as a dict, unguarded")
            lines.append(
                "Fix: converge onto _hookio.read_command / read_tool_input_field / read_cwd / "
                "read_exit_code (or a small local wrapper mirroring their contract for a computed "
                "field name), or add a justified entry to _KNOWN_EXCEPTIONS in this file."
            )
        if stale:
            lines.append("Stale _KNOWN_EXCEPTIONS entries (no longer a live offense -- remove them):")
            for filename, field, lineno in sorted(stale):
                lines.append(f"  {(filename, field, lineno)!r}")
        raise AssertionError("\n".join(lines))

    scanned = len(list(SCRIPTS_DIR.glob("*.py")))
    return (
        f"scanned {scanned} files in claude/scripts/ (2 detector arms: inline + two-statement): "
        f"{len(_KNOWN_EXCEPTIONS)} known exception(s), 0 unexpected, 0 stale"
    )


# ---------------------------------------------------------------------------
# Part 2 -- malformed-payload smoke tests (dev-env#1031 task item 2)
# ---------------------------------------------------------------------------

_LOADED_MODULES: dict[str, object] = {}


def _load_hook_module(hook_filename: str):
    """Load a claude/scripts/ hook as a Python module, WITHOUT executing its
    `if __name__ == "__main__":` block (that guard is False here, since the
    loaded module's own `__name__` is the synthetic name below, not
    `"__main__"`). Cached by filename so repeated calls in the same test run
    reuse the same module object -- mirrors test_worktree_path_check.py's
    own `_load_module()` pattern.
    """
    if hook_filename not in _LOADED_MODULES:
        mod_name = "_sibhook_" + hook_filename[:-3].replace("-", "_")
        spec = importlib.util.spec_from_file_location(mod_name, SCRIPTS_DIR / hook_filename)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LOADED_MODULES[hook_filename] = mod
    return _LOADED_MODULES[hook_filename]


def _run_hook_main(hook_filename: str, payload) -> int:
    """Call a hook's real `main()` directly (bypassing its `__main__`
    try/except safe-exit wrapper) so a crash propagates as an uncaught
    Python exception -- a genuinely discriminating test failure -- instead
    of being laundered into the same exit code a correctly-handled
    malformed payload also produces. See this file's own module docstring
    ("MALFORMED-PAYLOAD SMOKE TEST") for the full rationale and the
    dev-env#1031/#1033 /review finding that motivated this design.

    *payload* is JSON-serialized via `json.dumps` -- typically a dict, but
    any JSON-serializable value works (e.g. a bare list, for the
    non-dict-top-level-data case).
    """
    mod = _load_hook_module(hook_filename)
    real_stdin = mod.sys.stdin
    mod.sys.stdin = io.StringIO(json.dumps(payload))
    try:
        try:
            mod.main()
            return 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1
    finally:
        mod.sys.stdin = real_stdin


# Every migrated hook except pre-tool-use-worktree-path-check.py (own
# coverage in test_worktree_path_check.py) and pre-merge-findings-gate.py
# (own coverage in test_pre_merge_findings_gate.py / test-merge-findings-gate.sh,
# dev-env#1032). tool_input:null + cwd:null is safe for all of them: command
# becomes "" via read_command(), which fails every one of these files' own
# command-shape gates (is_pr_merge_command / is_issue_create_command /
# is_pr_create_command / _GH_PR_CREATE_RE / is_git_commit_command / scan_top_level)
# before any subprocess/network call, so this never risks a live `gh` call.
_BASH_HOOKS = [
    "post-merge-tile-checkpoint.py",
    "post-pr-merge-project.py",
    "post-pr-merge-pull.py",
    "post-pr-merge-reclaim.py",
    "post-tool-use.py",
    "pr-merge-reminder.py",
    "pre-auto-merge-checkpoint-gate.py",
    "pre-commit-branch-check.py",
    "pre-merge-branch-check.py",
    "pre-merge-message-check.py",
    "pre-merge-numbering-check.py",
    "pre-pr-create-check.py",
]


def test_malformed_tool_input_and_cwd_does_not_crash_any_bash_hook() -> str:
    """dev-env#1028's exact payload shape (tool_input present-but-null),
    combined with cwd:null, driven directly through each hook's own
    main(). All twelve return a clean SystemExit(0) -- including
    pre-auto-merge-checkpoint-gate.py, whose command="" correctly fails
    is_pr_merge_command() (see that file's own inline migration comment for
    the full reasoning on why this specific payload shape stays fail-open
    there, unlike the file's OWN fail-closed default for other unanticipated
    exceptions)."""
    failures = []
    for hook in _BASH_HOOKS:
        payload = {"tool_name": "Bash", "tool_input": None, "cwd": None}
        try:
            code = _run_hook_main(hook, payload)
        except Exception as exc:  # noqa: BLE001 -- a pre-fix crash IS the failure this test exists to catch
            failures.append(f"{hook}: main() raised {type(exc).__name__}: {exc} (expected a clean exit)")
            continue
        if code != 0:
            failures.append(f"{hook}: expected exit 0, got {code}")
    if failures:
        raise AssertionError("\n".join(failures))
    return f"tool_input:null + cwd:null -> clean SystemExit(0), no crash, for all {len(_BASH_HOOKS)} hooks"


def test_non_dict_top_level_data_does_not_crash_most_bash_hooks() -> str:
    """A valid-JSON-but-non-dict top-level payload (a bare list) crashed at
    `data.get("tool_name", ...)` pre-fix -- one level above every read_*
    helper's own guard. Eleven of the twelve hooks now guard
    `isinstance(data, dict)` explicitly and exit 0.

    pre-auto-merge-checkpoint-gate.py is excluded here and asserted
    separately (test_pre_auto_merge_checkpoint_gate_exits_open_on_non_dict_data
    below): a corrected post-review design decision (see that file's own
    inline comment) means it NOW also exits 0 for this payload -- the same
    isinstance(data, dict) guard the other eleven get -- but it's pinned as
    its own test since an EARLIER revision of this migration deliberately
    left it fail-CLOSED (exit 2) for this exact case, and that reversal is
    worth a dedicated, clearly-labeled regression pin rather than folding
    silently into this loop."""
    failures = []
    for hook in _BASH_HOOKS:
        if hook == "pre-auto-merge-checkpoint-gate.py":
            continue
        try:
            code = _run_hook_main(hook, ["not", "an", "object"])
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{hook}: main() raised {type(exc).__name__}: {exc} (expected a clean exit)")
            continue
        if code != 0:
            failures.append(f"{hook}: expected exit 0, got {code}")
    if failures:
        raise AssertionError("\n".join(failures))
    checked = len(_BASH_HOOKS) - 1
    return f"non-dict top-level JSON -> clean SystemExit(0), no crash, for {checked} hooks"


def test_pre_auto_merge_checkpoint_gate_exits_open_on_non_dict_data() -> str:
    """dev-env#1033, corrected post-review: pre-auto-merge-checkpoint-gate.py
    now guards `isinstance(data, dict)` the same as its eleven siblings above
    -- an earlier revision of this migration deliberately did NOT add that
    guard (reasoning a non-dict top-level payload should stay fail-CLOSED,
    matching ADR-083's general posture), but review found that inconsistent
    with this same migration's own rejected-fail-closed rationale for the
    narrower tool_input:null case: both malformed shapes destroy the same
    information (whether the command was ever a `gh pr merge --auto`), so
    failing closed for one but not the other has no principled basis, and
    the fail-closed direction has the WORSE consequence (blocking every
    Bash/PowerShell call on a rare payload glitch, not just --auto merges).
    See main()'s own inline comment in that file for the full reasoning.
    This does NOT touch the file's fail-closed posture for any OTHER
    unanticipated exception, which test_pre_auto_merge_checkpoint_gate.py's
    own test suite (a separate file) continues to cover."""
    code = _run_hook_main("pre-auto-merge-checkpoint-gate.py", ["not", "an", "object"])
    if code != 0:
        raise AssertionError(f"expected exit 0 (corrected, fail-open), got {code}")
    return "non-dict top-level JSON -> exit 0 for pre-auto-merge-checkpoint-gate.py too (corrected post-review)"


def test_malformed_tool_response_does_not_crash_unconditional_exit_code_readers() -> str:
    """post-tool-use.py and pr-merge-reminder.py read exit_code
    UNCONDITIONALLY (before any business-logic branch) -- safe to drive
    with a genuinely malformed tool_response plus an ordinary non-matching
    command ("git status"), since the exit_code VALUE never changes the
    outcome for a command neither file's own is_*_command gate recognizes.
    See this file's own module docstring for why the other four
    exit_code-reading hooks are NOT driven this way (would require either a
    live `gh` call or subprocess-mocking neither file's own test convention
    uses)."""
    failures = []
    for hook in ("post-tool-use.py", "pr-merge-reminder.py"):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_response": None,
            "cwd": ".",
        }
        try:
            code = _run_hook_main(hook, payload)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{hook}: main() raised {type(exc).__name__}: {exc} (expected a clean exit)")
            continue
        if code != 0:
            failures.append(f"{hook}: expected exit 0, got {code}")
    if failures:
        raise AssertionError("\n".join(failures))
    return "tool_response:null (non-matching command) -> clean SystemExit(0), no crash, for post-tool-use.py and pr-merge-reminder.py"


def main() -> int:
    tests = [
        ("detects a live single-expression chain", test_detects_live_assignment_chain),
        ("detects the tool_response variant", test_detects_tool_response_field),
        ("detects a computed outer key (worktree-path-check.py's real pre-fix shape)", test_detects_computed_outer_key),
        ("detects inline (non-assignment) usage -- broader than the original grep", test_detects_inline_non_assignment_usage),
        ("detects a subscript outer accessor ([...], not just .get())", test_detects_subscript_outer_accessor),
        ("detects dict() as the empty-default spelling (not just {})", test_detects_dict_call_default),
        ("detects non-Name receiver bases (self.data / subscript / call)", test_detects_non_name_bases),
        ("detects the bare `(X.get(key) or {}).get(...)` inline form", test_detects_bare_or_inline),
        ("ignores the hardened read_tool_input_field() implementation itself", test_ignores_hardened_read_command_implementation),
        ("ignores inner .get() with no default arg (different bug)", test_ignores_missing_default_arg),
        ("ignores inner .get() with a non-empty-dict default", test_ignores_non_empty_dict_default),
        ("ignores an unrelated field name", test_ignores_unrelated_field_name),
        ("ignores comment/docstring mentions", test_ignores_comment_and_docstring_mentions),
        ("two-statement arm: detects the real, unguarded memory-write-advisory.py shape", test_two_statement_detects_unguarded_real_shape),
        ("two-statement arm: ignores the real, properly-guarded journal-shard-write-advisory.py shape", test_two_statement_ignores_properly_guarded_real_shape),
        ("two-statement arm: a bare mention inside an if-test is not mistaken for a guard", test_two_statement_guard_check_does_not_confuse_use_with_guard),
        ("diff: reports unexpected vs. stale correctly", test_diff_reports_stale_and_unexpected),
        ("diff: exact (file, field, line) match -> 0 unexpected, 0 stale", test_diff_matches_known_exception_by_exact_line),
        ("repo-wide gate: no unexpected or stale unguarded chains (2 arms)", test_repo_has_no_unexpected_or_stale_unguarded_chains),
        ("malformed tool_input+cwd does not crash any of the 12 Bash hooks (direct main() calls)", test_malformed_tool_input_and_cwd_does_not_crash_any_bash_hook),
        ("non-dict top-level data does not crash 11 of the 12 Bash hooks (direct main() calls)", test_non_dict_top_level_data_does_not_crash_most_bash_hooks),
        ("pre-auto-merge-checkpoint-gate.py now also exits open on non-dict data (corrected post-review)", test_pre_auto_merge_checkpoint_gate_exits_open_on_non_dict_data),
        ("malformed tool_response does not crash the 2 unconditional exit_code readers", test_malformed_tool_response_does_not_crash_unconditional_exit_code_readers),
    ]

    # dev-env#1031/#1033 /review finding: every hook's own main() calls
    # _hookutil.record_heartbeat(...) as its unconditional first statement.
    # Direct main() calls (unlike the earlier subprocess design) run that
    # call IN THIS PROCESS -- redirect it for this file's whole run so the
    # smoke tests above never touch the developer's real
    # ~/.claude/scratch/hook-heartbeat/ (see _hookutil.record_heartbeat's
    # own docstring for the full rationale).
    import tempfile
    heartbeat_tmp = tempfile.mkdtemp(prefix="dev_env_sibling_hooks_heartbeat_")
    real_override = os.environ.get("HOOK_HEARTBEAT_DIR_OVERRIDE")
    os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = heartbeat_tmp
    try:
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
    finally:
        if real_override is None:
            os.environ.pop("HOOK_HEARTBEAT_DIR_OVERRIDE", None)
        else:
            os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = real_override
        import shutil
        shutil.rmtree(heartbeat_tmp, ignore_errors=True)

    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
