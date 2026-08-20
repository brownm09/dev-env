#!/usr/bin/env python3
"""dev-env#1031/#1033: migration coverage for the sibling hooks moved onto
_hookio's hardened read_command/read_cwd/read_exit_code helpers.

Two responsibilities in one file, since they're two views of the same fix:

1. MECHANICAL REGRESSION TEST (dev-env#1031 task item 3, /review's own
   suggestion on PR #1030). An AST-based scan -- mirroring
   test_no_crude_command_substring_checks.py's detector/allowlist/self-test
   shape exactly -- asserting no claude/scripts/*.py file contains the
   unguarded `X.get("tool_input"/"tool_response", {}).get(...)` chain as a
   live expression. This is the EXACT shape dev-env#1028 (usage-snapshot.py,
   PR #1030) and dev-env#1031/#1032/#1033 (the 14 sibling call sites --
   pre-merge-findings-gate.py fixed separately in PR #1034, the other 13
   fixed alongside this file) fixed across 15 files total. Makes the class
   mechanically unrepresentable going forward rather than merely documented.

2. MALFORMED-PAYLOAD SMOKE TEST (dev-env#1031 task item 2) for 12 of the 13
   files migrated alongside this one (pre-tool-use-worktree-path-check.py's
   own coverage lives in test_worktree_path_check.py instead, since that
   file already has a loaded-module reference and a `_run_hook` subprocess
   helper this file would otherwise duplicate). Each hook is driven
   end-to-end via subprocess with the dev-env#1028 payload shapes
   (tool_input: null, cwd: null, non-dict top-level data) and asserted not
   to crash or hang, exiting with the expected "safe" code.

   read_command/read_cwd/read_exit_code's OWN correctness (normal/missing/
   None/non-dict/non-string inputs, the required `default` contract) is
   already exhaustively covered in test_hookio.py -- NOT re-tested per-caller
   here. Each hook's own existing pure-helper test file is unaffected and
   still covers its own business logic; this file only proves the migrated
   main() dispatch reaches the helpers and doesn't crash.

   Scope note on tool_response/exit_code coverage: of the six migrated files
   that also read exit_code, only post-tool-use.py and pr-merge-reminder.py
   read it UNCONDITIONALLY (right after command/cwd extraction, before any
   business-logic branch) -- safe to drive via subprocess with an ordinary
   non-matching command (e.g. "git status"), since the exit_code VALUE never
   changes the outcome for a non-matching command. The other four
   (post-merge-tile-checkpoint.py, post-pr-merge-project.py,
   post-pr-merge-pull.py, post-pr-merge-reclaim.py) read exit_code only
   INSIDE the "marker didn't confirm the merge" fallback branch, gated
   behind should_confirm_via_gh() -- reaching that line via a genuinely
   malformed tool_response (which also empties `output`, so no marker can
   survive) makes should_confirm_via_gh() return True, which would then
   attempt a REAL `gh pr view` subprocess call. Forcing that call in a test
   (or monkeypatching confirm_merge_via_gh, which none of these four files'
   own test files do -- ADR-050 Amendment 3/8's "the repo avoids subprocess
   mocks" convention) is exactly the kind of environment-dependent assertion
   dev-env#1028's own post-review CI failure warned against (PR #1030: an
   assertion that passed locally and failed in CI for reasons unrelated to
   the fix). These four files' own tool_input:null coverage below already
   proves the PRIMARY, most-severe crash class (the exact dev-env#1028
   incident shape) doesn't crash their main() dispatch; their exit_code
   line's correctness rests on read_exit_code's own exhaustive test_hookio.py
   coverage plus the per-file default-value verification recorded in each
   file's own inline migration comment (dev-env#1033) and ADR-050 Amendment
   28 -- a deliberate scope boundary, not an oversight.

Usage:
    py -3 claude/scripts/tests/test_sibling_hooks_hardened_io.py

Exit 0 = all pass.
"""

import ast
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"

# ---------------------------------------------------------------------------
# Part 1 -- AST-based regression test
# ---------------------------------------------------------------------------

# (filename, field) pairs known to still carry the unguarded-chain shape.
# Matched on file + field text (not line number), mirroring
# test_no_crude_command_substring_checks.py's own _KNOWN_EXCEPTIONS
# convention exactly, including its two-sided gate (an exception with no
# matching live offense fails the test too, so this set cannot silently rot
# -- see test_diff_against_known_exceptions below). Empty: this migration's
# whole point is that no live offense should remain anywhere in the tree.
_KNOWN_EXCEPTIONS: set[tuple[str, str]] = set()


def find_unguarded_tool_field_chains(source: str, filename: str = "<string>") -> list[tuple[int, str]]:
    """Return (lineno, field) for each live `X.get("tool_input"|"tool_response",
    {}).get(<anything>)` Call node in *source*, where X is a bare Name.

    This is the exact shape dev-env#1028/#1031 fixed: `data.get("key", {})`
    only substitutes the `{}` default when the KEY is absent -- a
    present-but-non-dict value (most commonly `None`) passes straight
    through and the chained outer `.get()` raises `AttributeError`. Detects
    the shape regardless of whether it's assigned to a variable, passed as
    an argument, or used inline -- broader than (and a superset of) the
    original repo-wide grep that discovered these 14 sites, which was
    anchored to an assignment statement only (`^\\s*\\w+\\s*=\\s*data\\.get(...)`)
    and would miss the identical bug written as e.g. `foo(data.get("tool_input",
    {}).get("command", ""))`.

    Deliberately does NOT require the outer `.get()`'s key to be the literal
    "command"/"exitCode" -- `pre-tool-use-worktree-path-check.py`'s own
    pre-fix shape read a COMPUTED field name (`_PATH_FIELD[tool_name]`) via
    this identical inner-chain pattern, and a future caller reading some
    other field off tool_input/tool_response via the same unguarded chain
    shape is the same bug regardless of which field it reads.

    AST-based rather than grep-based, mirroring
    find_command_substring_checks's own rationale in
    test_no_crude_command_substring_checks.py: comments and docstrings are
    structurally invisible to ast.parse, so this cannot be fooled by text
    that merely *mentions* the pattern (e.g. this very file's own docstring
    and comments, which quote the pattern verbatim as prose several times).
    """
    tree = ast.parse(source, filename=filename)
    offenses: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            continue
        inner = node.func.value
        if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == "get"):
            continue
        base = inner.func.value
        if not isinstance(base, ast.Name):
            continue
        if not inner.args:
            continue
        key_arg = inner.args[0]
        if not (isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str)
                and key_arg.value in ("tool_input", "tool_response")):
            continue
        # The inner .get()'s own default must be an empty-dict literal `{}` --
        # that's what makes the chain crash on a present-but-non-dict value
        # rather than substituting a safe fallback. A caller with no default
        # at all raises KeyError on a missing key, a different bug from this
        # one, and is not flagged. A non-empty-dict or non-dict default is
        # not the established shape in this codebase and is left unflagged
        # too, rather than guessing at an unfamiliar pattern's intent.
        if len(inner.args) < 2:
            continue
        default_arg = inner.args[1]
        if not (isinstance(default_arg, ast.Dict) and not default_arg.keys):
            continue
        offenses.append((node.lineno, key_arg.value))
    return offenses


def _scan_real_repo() -> dict[str, list[tuple[int, str]]]:
    """Run the detector over every top-level claude/scripts/*.py file (the
    tests/ subdirectory and __pycache__ are excluded automatically by the
    non-recursive glob, mirroring test_no_crude_command_substring_checks.py's
    identical scan). Returns {filename: [(lineno, field), ...]}."""
    results: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            offenses = find_unguarded_tool_field_chains(source, filename=path.name)
        except (SyntaxError, UnicodeDecodeError) as e:
            raise RuntimeError(f"failed to scan {path.name}: {e}") from e
        if offenses:
            results[path.name] = offenses
    return results


def _diff_against_known_exceptions(
    found: dict[str, list[tuple[int, str]]], known_exceptions: set[tuple[str, str]]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]], dict[tuple[str, str], int]]:
    """Mirrors test_no_crude_command_substring_checks.py's identically-named
    helper exactly: returns (unexpected, stale, duplicated)."""
    occurrence_counts = Counter(
        (filename, field) for filename, offenses in found.items() for _, field in offenses
    )
    live_offenses = set(occurrence_counts)
    unexpected = live_offenses - known_exceptions
    stale = known_exceptions - live_offenses
    duplicated = {key: occurrence_counts[key] for key in known_exceptions if occurrence_counts[key] > 1}
    return unexpected, stale, duplicated


# --- detector self-tests (synthetic fixtures -- no disk I/O) ---------------

def test_detects_live_assignment_chain() -> str:
    source = 'def f(data):\n    command = data.get("tool_input", {}).get("command", "")\n    return command\n'
    offenses = find_unguarded_tool_field_chains(source)
    assert offenses == [(2, "tool_input")], offenses
    return "live assignment-form chain -> 1 offense"


def test_detects_tool_response_field() -> str:
    source = 'def f(data):\n    ec = data.get("tool_response", {}).get("exitCode", -1)\n    return ec\n'
    offenses = find_unguarded_tool_field_chains(source)
    assert offenses == [(2, "tool_response")], offenses
    return "tool_response variant -> 1 offense"


def test_detects_computed_outer_key_not_just_command_or_exitcode() -> str:
    # pre-tool-use-worktree-path-check.py's actual pre-fix shape: the OUTER
    # .get()'s key is a computed expression, not the literal "command" -- the
    # detector must not require that.
    source = 'def f(data, tool_name):\n    p = data.get("tool_input", {}).get(_PATH_FIELD[tool_name], "")\n    return p\n'
    offenses = find_unguarded_tool_field_chains(source)
    assert offenses == [(2, "tool_input")], offenses
    return "computed outer-key form (dev-env#1031's actual pre-fix worktree-path-check.py shape) -> 1 offense"


def test_detects_inline_non_assignment_usage() -> str:
    # Broader than the original assignment-anchored grep: the same bug
    # written as a direct function argument, never assigned to a variable.
    source = 'def f(data):\n    return len(data.get("tool_input", {}).get("command", ""))\n'
    offenses = find_unguarded_tool_field_chains(source)
    assert offenses == [(2, "tool_input")], offenses
    return "inline (non-assignment) usage -> still detected"


def test_ignores_hardened_read_command_implementation() -> str:
    # _hookio.py's own read_command() -- the FIX -- must not trip the
    # detector: it splits the isinstance guard onto its own statement rather
    # than chaining .get() calls inline.
    source = (
        "def read_command(data):\n"
        "    if not isinstance(data, dict):\n"
        '        return ""\n'
        '    ti = data.get("tool_input") or {}\n'
        "    if not isinstance(ti, dict):\n"
        '        return ""\n'
        '    cmd = ti.get("command", "")\n'
        "    return cmd if isinstance(cmd, str) else cmd\n"
    )
    assert find_unguarded_tool_field_chains(source) == []
    return "the hardened read_command() implementation itself -> 0 offenses (split statements, not a chain)"


def test_ignores_missing_default_arg() -> str:
    # No default at all is a different bug (KeyError on a missing key) --
    # out of scope for this detector.
    source = 'def f(data):\n    return data.get("tool_input").get("command", "")\n'
    assert find_unguarded_tool_field_chains(source) == []
    return "inner .get() with no default argument -> 0 offenses (different bug, out of scope)"


def test_ignores_non_empty_dict_default() -> str:
    source = 'def f(data):\n    return data.get("tool_input", {"x": 1}).get("command", "")\n'
    assert find_unguarded_tool_field_chains(source) == []
    return "inner .get() with a non-empty-dict default -> 0 offenses (not the established shape)"


def test_ignores_unrelated_field_name() -> str:
    source = 'def f(data):\n    return data.get("other_field", {}).get("x", "")\n'
    assert find_unguarded_tool_field_chains(source) == []
    return '"other_field" (not tool_input/tool_response) -> 0 offenses'


def test_ignores_comment_and_docstring_mentions() -> str:
    source = (
        "def f(data):\n"
        '    """Old code was data.get("tool_input", {}).get("command", "").  Now fixed."""\n'
        "    # data.get(\"tool_response\", {}).get(\"exitCode\", -1) -- the old bug\n"
        "    return read_command(data)\n"
    )
    assert find_unguarded_tool_field_chains(source) == []
    return "docstring + comment mentioning the pattern -> 0 offenses (invisible to ast.parse)"


def test_diff_detects_duplicated_offense_behind_allowlist_entry() -> str:
    found = {"x.py": [(10, "tool_input"), (40, "tool_input")]}
    known = {("x.py", "tool_input")}
    unexpected, stale, duplicated = _diff_against_known_exceptions(found, known)
    assert unexpected == set(), unexpected
    assert stale == set(), stale
    assert duplicated == {("x.py", "tool_input"): 2}, duplicated
    return "2 occurrences of an allowlisted (file, field) -> flagged as duplicated, not silently absorbed"


def test_repo_has_no_unexpected_or_stale_unguarded_chains() -> str:
    """The actual regression gate. Fails on (a) a live offense not covered by
    _KNOWN_EXCEPTIONS (a new or regressed unguarded chain anywhere in the
    tree), (b) a _KNOWN_EXCEPTIONS entry with no matching live offense (a
    stale exception left behind), or (c) a _KNOWN_EXCEPTIONS entry whose
    field now has more than one live occurrence in its file."""
    found = _scan_real_repo()
    unexpected, stale, duplicated = _diff_against_known_exceptions(found, _KNOWN_EXCEPTIONS)

    if unexpected or stale or duplicated:
        lines = []
        if unexpected:
            lines.append("New/unlisted unguarded tool_input/tool_response chains found:")
            for filename, offenses in sorted(found.items()):
                for lineno, field in offenses:
                    if (filename, field) in unexpected:
                        lines.append(f"  {filename}:{lineno}: X.get({field!r}, {{}}).get(...)")
            lines.append(
                "Fix: converge onto _hookio.read_command/read_cwd/read_exit_code (or a small local "
                "wrapper mirroring their contract for a computed field name), or add a justified "
                "entry to _KNOWN_EXCEPTIONS in this file."
            )
        if stale:
            lines.append("Stale _KNOWN_EXCEPTIONS entries (no longer a live offense -- remove them):")
            for filename, field in sorted(stale):
                lines.append(f"  {(filename, field)!r}")
        if duplicated:
            lines.append("Allowlisted field has more than one live occurrence -- audit each site:")
            for (filename, field), count in sorted(duplicated.items()):
                lines.append(f"  {(filename, field)!r}: {count} occurrences")
        raise AssertionError("\n".join(lines))

    scanned = len(list(SCRIPTS_DIR.glob("*.py")))
    return (
        f"scanned {scanned} files in claude/scripts/: {len(_KNOWN_EXCEPTIONS)} known exception(s), "
        "0 unexpected, 0 stale, 0 duplicated"
    )


# ---------------------------------------------------------------------------
# Part 2 -- malformed-payload smoke tests (dev-env#1031 task item 2)
# ---------------------------------------------------------------------------

def _run_hook(hook_filename: str, payload) -> subprocess.CompletedProcess:
    """Run a claude/scripts/ hook over stdin with *payload* (already
    JSON-serializable -- a dict OR a bare list, to exercise the non-dict
    top-level case). Mirrors test_worktree_path_check.py's own `_run_hook`
    helper."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / hook_filename)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


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
    combined with cwd:null, driven end-to-end through each hook's own
    main(). All twelve exit 0 -- including pre-auto-merge-checkpoint-gate.py,
    whose command="" correctly fails is_pr_merge_command() before its own
    fail-CLOSED exception handler would ever engage (see that file's own
    inline migration comment for the full reasoning on why this specific
    payload shape stays fail-open there, unlike a non-dict top-level payload
    -- tested separately below)."""
    failures = []
    for hook in _BASH_HOOKS:
        payload = {"tool_name": "Bash", "tool_input": None, "cwd": None}
        proc = _run_hook(hook, payload)
        if proc.returncode != 0:
            failures.append(f"{hook}: expected exit 0, got {proc.returncode} (stderr={proc.stderr!r})")
    if failures:
        raise AssertionError("\n".join(failures))
    return f"tool_input:null + cwd:null -> exit 0, no crash, for all {len(_BASH_HOOKS)} hooks"


def test_non_dict_top_level_data_does_not_crash_most_bash_hooks() -> str:
    """A valid-JSON-but-non-dict top-level payload (a bare list) crashed at
    `data.get("tool_name", ...)` pre-fix -- one level above every read_*
    helper's own guard. Eleven of the twelve hooks now guard
    `isinstance(data, dict)` explicitly and exit 0.

    pre-auto-merge-checkpoint-gate.py is excluded here and asserted
    separately (test_pre_auto_merge_checkpoint_gate_fails_closed_on_non_dict_data
    below): it deliberately does NOT get that guard, so this exact payload
    still crashes into its own `except Exception: _fail_closed(...)` handler
    and exits 2 -- ADR-083's fail-closed posture, preserved on purpose for
    this maximally out-of-contract case (see that file's own inline
    migration comment)."""
    failures = []
    for hook in _BASH_HOOKS:
        if hook == "pre-auto-merge-checkpoint-gate.py":
            continue
        proc = _run_hook(hook, ["not", "an", "object"])
        if proc.returncode != 0:
            failures.append(f"{hook}: expected exit 0, got {proc.returncode} (stderr={proc.stderr!r})")
    if failures:
        raise AssertionError("\n".join(failures))
    checked = len(_BASH_HOOKS) - 1
    return f"non-dict top-level JSON -> exit 0, no crash, for {checked} hooks (excludes pre-auto-merge-checkpoint-gate.py)"


def test_pre_auto_merge_checkpoint_gate_fails_closed_on_non_dict_data() -> str:
    """The one deliberate asymmetry in this migration (dev-env#1033, mirroring
    ADR-050 Amendment 27's identical reasoning for pre-merge-findings-gate.py's
    PreToolUse situation): pre-auto-merge-checkpoint-gate.py's own `__main__`
    fails CLOSED on any uncaught exception (ADR-083 Decision point 3), and
    this migration intentionally does NOT add the isinstance(data, dict)
    top-level guard every other sibling hook gets here -- a non-dict
    top-level payload is left to crash naturally into that fail-closed
    handler, preserving ADR-083's stricter posture for this maximally
    out-of-contract case (Claude Code's hook contract always sends a JSON
    object at the top level; this is far less plausible than tool_input
    specifically arriving malformed, dev-env#1028's actual confirmed shape)."""
    proc = _run_hook("pre-auto-merge-checkpoint-gate.py", ["not", "an", "object"])
    if proc.returncode != 2:
        raise AssertionError(f"expected exit 2 (fail closed), got {proc.returncode}. stderr={proc.stderr!r}")
    return "non-dict top-level JSON -> exit 2 (fails CLOSED, deliberately, ADR-083) for pre-auto-merge-checkpoint-gate.py"


def test_malformed_tool_response_does_not_crash_unconditional_exit_code_readers() -> str:
    """post-tool-use.py and pr-merge-reminder.py read exit_code
    UNCONDITIONALLY (before any business-logic branch) -- safe to drive with
    a genuinely malformed tool_response plus an ordinary non-matching
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
        proc = _run_hook(hook, payload)
        if proc.returncode != 0:
            failures.append(f"{hook}: expected exit 0, got {proc.returncode} (stderr={proc.stderr!r})")
    if failures:
        raise AssertionError("\n".join(failures))
    return "tool_response:null (non-matching command) -> exit 0, no crash, for post-tool-use.py and pr-merge-reminder.py"


def main() -> int:
    tests = [
        ("detects a live assignment-form chain", test_detects_live_assignment_chain),
        ("detects the tool_response variant", test_detects_tool_response_field),
        ("detects a computed outer key (worktree-path-check.py's real pre-fix shape)", test_detects_computed_outer_key_not_just_command_or_exitcode),
        ("detects inline (non-assignment) usage -- broader than the original grep", test_detects_inline_non_assignment_usage),
        ("ignores the hardened read_command() implementation itself", test_ignores_hardened_read_command_implementation),
        ("ignores inner .get() with no default arg (different bug)", test_ignores_missing_default_arg),
        ("ignores inner .get() with a non-empty-dict default", test_ignores_non_empty_dict_default),
        ("ignores an unrelated field name", test_ignores_unrelated_field_name),
        ("ignores comment/docstring mentions", test_ignores_comment_and_docstring_mentions),
        ("diff: duplicated offense behind an allowlist entry is flagged", test_diff_detects_duplicated_offense_behind_allowlist_entry),
        ("repo-wide gate: no unexpected, stale, or duplicated unguarded chains", test_repo_has_no_unexpected_or_stale_unguarded_chains),
        ("malformed tool_input+cwd does not crash any of the 12 Bash hooks", test_malformed_tool_input_and_cwd_does_not_crash_any_bash_hook),
        ("non-dict top-level data does not crash 11 of the 12 Bash hooks", test_non_dict_top_level_data_does_not_crash_most_bash_hooks),
        ("pre-auto-merge-checkpoint-gate.py fails CLOSED on non-dict data (deliberate)", test_pre_auto_merge_checkpoint_gate_fails_closed_on_non_dict_data),
        ("malformed tool_response does not crash the 2 unconditional exit_code readers", test_malformed_tool_response_does_not_crash_unconditional_exit_code_readers),
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
