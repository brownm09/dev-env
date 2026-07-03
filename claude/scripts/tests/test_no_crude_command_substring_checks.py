#!/usr/bin/env python3
"""Repo-wide regression test: no crude `<literal> {in|not in} command`-shaped
substring checks anywhere in claude/scripts/*.py.

ADR-050 documents a recurring bug class: a raw substring test like
`if "gh pr merge" not in command: return False` used to detect whether a Bash
command string invoked a specific CLI command. It false-positives whenever the
literal text appears inside a heredoc body, a quoted argument, or a `$()`
subshell rather than as a real top-level statement -- the fix is
`_hookio.scan_top_level(command, check_fn)`. The fix was converged piecemeal
across Amendments 5, 6, 9, and 10; each sweep missed at least one sibling hook
because it scoped itself to "which hooks conceptually need this kind of
detection" rather than to the textual shape of the bug itself. Amendment 9's
closing "General lesson" proposed a manual grep as a durable mechanical proxy
to run before declaring any future sweep complete -- this test is that proxy,
automated and enforced instead of remembered (dev-env#534).

Generalizes beyond the ADR's three originally-named literals (`gh pr merge` /
`gh pr create` / `git push`) to flag ANY string literal checked this way
against a variable named `command` -- an AST walk costs no more to make
generic, and doing so is what caught two previously-untracked instances of the
identical shape in stub-push-archive-reminder.py (see _KNOWN_EXCEPTIONS below)
that a grep scoped to the three original literals alone -- including the
repo-wide grep Amendment 10 itself ran -- would not have found.

AST-based rather than grep-based: comments and docstrings are structurally
invisible to ast.parse (a comment is stripped before the parser ever sees it;
a docstring's string constant is never a child of a Compare node unless it is
literally live code), so this cannot be fooled by text that merely *mentions*
the pattern -- exactly the "live check vs. explanatory comment" distinction
PR #530's own diff had to navigate by hand via careful grep phrasing. See
test_ignores_pr530_style_explanatory_comment below for a direct proof against
real PR #530 comment text.

Known non-goal: an f-string with no interpolation (`f"gh pr merge"`) parses to
a JoinedStr node, not a Constant, so it is not detected. No check in this repo
uses an f-string for a literal comparison today; unwrapping JoinedStr is not
worth the complexity unless that changes.

Usage:
    py -3 claude/scripts/tests/test_no_crude_command_substring_checks.py

Exit 0 = all pass.
"""

import ast
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"

# (filename, literal) pairs known to still carry the crude-check shape. Matched
# on file + literal text (not line number), so a line shift doesn't silently
# stop covering the real exception and a genuinely new/different check on a
# nearby line isn't silently swallowed by an old entry. The gate below is
# two-sided: an exception that no longer matches a live offense (because the
# underlying code was fixed) fails the test too, so this set cannot silently
# rot -- see test_repo_has_no_unexpected_or_stale_command_substring_checks.
#
# Both entries below check whether the raw command references the
# engineering-journal repo by name, via the same unanchored substring shape
# ADR-050 documents for CLI-invocation detection -- just applied to a repo-name
# literal instead of a `gh`/`git` subcommand literal. Neither is named by any
# prior ADR or issue; both were surfaced by this test (dev-env#534), not by
# Amendment 9's or Amendment 10's own narrower, three-literal greps (Amendment
# 10's own repo-wide grep, run immediately before that fix, checked only for
# the "gh pr create" / "gh pr merge" / "git push" literals -- exactly the scope
# this test intentionally goes beyond). Converging them onto scan_top_level is
# a hook-behavior change and is out of scope for this test-only PR.
_KNOWN_EXCEPTIONS = {
    ("stub-push-archive-reminder.py", "engineering-journal"),
    ("stub-push-archive-reminder.py", "engineering_journal"),
}


def find_command_substring_checks(source: str, filename: str = "<string>") -> list[tuple[int, str]]:
    """Return (lineno, literal) for each live `<str literal> {in|not in} command`
    Compare node in *source*.

    Only flags the literal-then-command operand order (`"x" not in command`)
    -- not the reverse (`command not in "x"`, a different semantic test, not
    this bug class) -- and only a `Name` whose id is exactly "command" (the
    variable name this entire hook family uses for the raw Bash command
    string; a differently-named variable, e.g. `commands` or `cmd`, is a
    different thing and out of scope). Walks every adjacent operand pair in a
    chained comparison (`"a" not in command not in other`), not just the
    first, so an offense that isn't the first link is still caught.
    """
    tree = ast.parse(source, filename=filename)
    offenses: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for op, before, after in zip(node.ops, operands, operands[1:]):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if (
                isinstance(before, ast.Constant)
                and isinstance(before.value, str)
                and isinstance(after, ast.Name)
                and after.id == "command"
            ):
                offenses.append((node.lineno, before.value))
    return offenses


def _scan_real_repo() -> dict[str, list[tuple[int, str]]]:
    """Run the detector over every top-level claude/scripts/*.py file (the
    tests/ subdirectory and __pycache__ are excluded automatically by the
    non-recursive glob). Returns {filename: [(lineno, literal), ...]}."""
    results: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        offenses = find_command_substring_checks(source, filename=path.name)
        if offenses:
            results[path.name] = offenses
    return results


# ---------------------------------------------------------------------------
# detector self-tests (synthetic fixtures -- no disk I/O)
# ---------------------------------------------------------------------------

def test_detects_live_check() -> str:
    source = 'def f(command):\n    if "gh pr merge" not in command:\n        return False\n'
    offenses = find_command_substring_checks(source)
    assert offenses == [(2, "gh pr merge")], offenses
    return 'live `"gh pr merge" not in command` check -> 1 offense'


def test_detects_general_shape_not_just_three_named_literals() -> str:
    # A 4th literal, never named by the ADR -- proves this isn't hardcoded to
    # exactly the three historical strings.
    source = 'def f(command):\n    if "rm -rf" not in command:\n        return False\n'
    offenses = find_command_substring_checks(source)
    assert offenses == [(2, "rm -rf")], offenses
    return "a literal outside the ADR's original 3 is still detected (general shape, not 3 hardcoded strings)"


def test_ignores_full_line_comment() -> str:
    source = '# if "gh pr merge" not in command: return False\n'
    assert find_command_substring_checks(source) == []
    return "full-line comment mentioning the pattern -> 0 offenses (comments are invisible to ast.parse)"


def test_ignores_trailing_inline_comment() -> str:
    source = (
        "def f(command):\n"
        '    if not scan_top_level(command, fn):  # replaces "gh pr merge" not in command\n'
        "        return False\n"
    )
    assert find_command_substring_checks(source) == []
    return "trailing inline comment on a live code line -> 0 offenses"


def test_ignores_docstring_mention() -> str:
    source = (
        "def f(command):\n"
        '    """Old code was: `"gh pr merge" not in command`. Now uses scan_top_level."""\n'
        "    return scan_top_level(command, fn)\n"
    )
    assert find_command_substring_checks(source) == []
    return "docstring mentioning the pattern -> 0 offenses (a docstring Constant is never a Compare operand)"


def test_ignores_unrelated_variable_names() -> str:
    source = (
        "def f(cmd, commands):\n"
        '    if "gh pr merge" not in cmd:\n'
        "        return False\n"
        "    if tid in commands:\n"
        "        return True\n"
    )
    assert find_command_substring_checks(source) == []
    return "`cmd` and `commands` (not exactly `command`) -> 0 offenses"


def test_ignores_unrelated_membership_check() -> str:
    # The real shape from this same file's own has_push_error().
    source = 'def has_push_error(output):\n    lower = output.lower()\n    return "error:" in lower\n'
    assert find_command_substring_checks(source) == []
    return '`"error:" in lower` (different variable, different purpose) -> 0 offenses'


def test_ignores_reverse_operand_order() -> str:
    source = 'def f(command):\n    if command not in "some literal":\n        return False\n'
    assert find_command_substring_checks(source) == []
    return 'reverse operand order (`command not in "literal"`) -> 0 offenses (different semantics)'


def test_detects_offense_in_non_first_link_of_chained_comparison() -> str:
    # A genuine 3-term chained comparison (one Compare node, two ops) where the
    # crude-check-shaped pair is the SECOND link, not the first -- empirically
    # verified via ast.dump before writing this assertion. Proves the walk
    # checks every adjacent operand pair rather than only
    # node.left/comparators[0]: a naive first-link-only check would see
    # (Name('other'), Constant) for link 1 -- not a match -- and never look at
    # link 2 at all, silently returning zero offenses instead of one.
    source = 'def f(other, command):\n    if other in "gh pr merge" not in command:\n        pass\n'
    offenses = find_command_substring_checks(source)
    assert offenses == [(2, "gh pr merge")], offenses
    return "offending pair is the 2nd link of a chained comparison, not the 1st -> still detected"


def test_ignores_pr530_style_explanatory_comment() -> str:
    # Verbatim (trimmed) comment block PR #530 itself added to explain the old
    # bug -- the exact calibration case this test exists to get right. A naive
    # grep for the crude pattern's own text would false-positive on this; AST
    # does not.
    source = (
        "# ---------------------------------------------------------------------------\n"
        "# command-shape anchoring (dev-env#529, ADR-050 Amendment 9)\n"
        "#\n"
        '# Each command below contains the literal substring "gh pr merge" but not as\n'
        "# a genuine top-level invocation. Paired with an output that DOES carry a\n"
        "# real success marker, isolating the command-shape check: the old crude\n"
        '# `"gh pr merge" not in command` substring test would have proceeded past\n'
        "# this check straight to the (passing) marker check and fired -- a false\n"
        "# positive. The scan_top_level-anchored check returns False before the\n"
        "# marker is ever consulted.\n"
        "# ---------------------------------------------------------------------------\n"
    )
    naive_grep_hit = '"gh pr merge" not in command' in source
    assert naive_grep_hit, "fixture must reproduce the naive-grep false positive to prove the calibration case"
    assert find_command_substring_checks(source) == [], "AST detector must not flag comment-only text"
    return "PR #530's own explanatory comment: naive grep -> false positive; AST detector -> 0 offenses"


def test_repo_has_no_unexpected_or_stale_command_substring_checks() -> str:
    """The actual regression gate. Fails on (a) a live offense not covered by
    _KNOWN_EXCEPTIONS (a new or regressed crude check anywhere in the tree),
    or (b) a _KNOWN_EXCEPTIONS entry with no matching live offense (a stale
    exception left behind after the underlying code was fixed -- this is not
    hypothetical: dev-env#532/PR#533 converged stub-push-archive-reminder.py's
    `"git push" not in command` check onto scan_top_level while this very test
    was being authored, which is exactly why that exception is not in the set
    above -- had it been hardcoded and left in place, this check would have
    failed the moment that PR merged)."""
    found = _scan_real_repo()

    live_offenses = {
        (filename, literal)
        for filename, offenses in found.items()
        for _, literal in offenses
    }

    unexpected = live_offenses - _KNOWN_EXCEPTIONS
    stale = _KNOWN_EXCEPTIONS - live_offenses

    if unexpected or stale:
        lines = []
        if unexpected:
            lines.append("New/unlisted crude command-substring checks found:")
            for filename, offenses in sorted(found.items()):
                for lineno, literal in offenses:
                    if (filename, literal) in unexpected:
                        lines.append(f"  {filename}:{lineno}: {literal!r} not/in command")
            lines.append(
                "Fix: converge onto _hookio.scan_top_level(command, check_fn), or add a "
                "justified entry to _KNOWN_EXCEPTIONS in this file."
            )
        if stale:
            lines.append("Stale _KNOWN_EXCEPTIONS entries (no longer a live offense -- remove them):")
            for filename, literal in sorted(stale):
                lines.append(f"  {(filename, literal)!r}")
        raise AssertionError("\n".join(lines))

    scanned = len(list(SCRIPTS_DIR.glob("*.py")))
    return f"scanned {scanned} files in claude/scripts/: {len(live_offenses)} known exception(s), 0 unexpected, 0 stale"


def main() -> int:
    tests = [
        ("detects a live check", test_detects_live_check),
        ("detects general shape, not just the 3 named literals", test_detects_general_shape_not_just_three_named_literals),
        ("ignores full-line comment", test_ignores_full_line_comment),
        ("ignores trailing inline comment", test_ignores_trailing_inline_comment),
        ("ignores docstring mention", test_ignores_docstring_mention),
        ("ignores unrelated variable names (cmd/commands)", test_ignores_unrelated_variable_names),
        ('ignores unrelated membership check ("error:" in lower)', test_ignores_unrelated_membership_check),
        ("ignores reverse operand order", test_ignores_reverse_operand_order),
        ("detects offense in non-first link of chained comparison", test_detects_offense_in_non_first_link_of_chained_comparison),
        ("ignores PR #530-style explanatory comment (calibration proof)", test_ignores_pr530_style_explanatory_comment),
        ("repo-wide gate: no unexpected or stale checks", test_repo_has_no_unexpected_or_stale_command_substring_checks),
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
