#!/usr/bin/env python3
"""Unit + drift gates for `_worktree_recovery.py` (Testing item 78, dev-env#862/ADR-116).

Three layers, all pure offline file parse -- no subprocess, network, or git. Auto-
discovered by `run-hook-tests.py`, so this also gates CI on every PR.

  1. UNIT -- `recovery_recipe()` renders every `RECOVERY_STEPS` command, in order,
     with the path placeholders substituted, keeps the destructive step OUT of the
     numbered sequence, and stays `.isascii()`.
  2. RUNBOOK PARITY -- docs/REFERENCE.md's "Worktree deregistration recovery"
     fenced block carries the same commands, in the same order. Asserted as
     EQUALITY over the block's *runnable* lines (comments and blanks stripped,
     plus a narrow named allowlist), not as a substring subsequence -- see below.
  3. ANTI-REGRESSION -- the `worktree add --force` / `-f` recipe dev-env#751
     disproved appears in no live surface.

Why layer 2 is equality over runnable lines
-------------------------------------------
The first version matched each command as a plain substring of the whole fence
body, comments included. Four separate drift mutations passed that gate: every
step line commented out; `checkout main` and `rm -rf` injected at the top; a step
replaced while its canonical text survived only inside a `# (formerly: ...)`
comment; and the destructive step hoisted to the first line. The last is worse
than a vacuous pass -- ordering is pinned in the module only, so the doc a human
follows could lead with the delete and stay green. Equality over runnable lines
closes all four.

Why layer 3 is BOTH an AST pass and a text pass
-----------------------------------------------
The AST pass exists to tell a *prescription* from an *explanation*: this module,
the hook, and ADR-116 all discuss `--force` at length, and a naive text scan flags
that prose. Comments never reach the AST and docstrings are excluded, so what
remains is what a script could actually emit.

But the AST pass alone is a weak backstop -- it misses `+` concatenation,
f-strings with the flag in a variable, `.format()`, `%`-formatting, `bytes`
literals, and `" ".join([...])`, and `glob("*.py")` is non-recursive so it never
sees `tests/`, `*.sh`, `claude/CLAUDE.md`, or `README.md`. So a cheap text scan
covers the *live operational surfaces* where a runnable prescription would
actually land. Neither pass is claimed to be exhaustive; together they cover the
shapes that have actually occurred.

`docs/adr/` is DELIBERATELY exempt from both: ADRs are a historical record, and
ADR-024's 2026-06-06 addendum legitimately quotes the recipe that was
correct-as-believed at the time. ADR-116 supersedes it; rewriting the history
would defeat the point of keeping it.

Usage:
    py -3 claude/scripts/tests/test_worktree_recovery.py

Exit 0 = all pass.
"""
import ast
import re
import sys
import unittest
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_MD = REPO_ROOT / "docs" / "REFERENCE.md"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _worktree_recovery as wr  # noqa: E402

RUNBOOK_HEADING = "### Worktree deregistration recovery"

# The forms dev-env#751 disproved. `--force` AND the `-f` short alias (verified to
# fail identically: `fatal: '<path>' already exists`), in either flag position --
# git accepts `worktree add <path> --force` as readily as the flag-first ordering.
#
# The gap between verb and flag is bounded, and excludes backticks and pipes, so the
# pattern cannot span a markdown code-span boundary or a table-cell boundary. An
# unbounded `[^\n]*?` matched 874 characters across an entire REFERENCE.md hooks-table
# row -- pairing a `worktree add` in one sentence with an unrelated `-f` far later.
DISPROVEN_RECIPE_RE = re.compile(r"worktree\s+add\b[^`|\n]{0,60}?(?:--force\b|(?<![\w-])-f\b)")

# Commands the runbook may carry that the hook message deliberately does not emit.
# Named explicitly so the exception is visible rather than structural: the hook's
# reader has no repo checked out to install into, the runbook's does.
RUNBOOK_ONLY_COMMANDS = ("npm install",)

# Runnable lines that must never reappear in the runbook: the ADR-071-blocked
# canonical checkout, and the removal form that fails on the session's own cwd.
FORBIDDEN_RUNBOOK_RE = (
    re.compile(r"^\s*rm\s+-rf\b"),
    re.compile(r"\bcheckout\s+main\b"),
)

# Live operational surfaces for the layer-3 text scan. docs/adr/ is excluded by
# design; docs/REFERENCE.md is covered here in full, not just its runbook fence.
LIVE_TEXT_SURFACES = (
    "CLAUDE.md",
    "README.md",
    "claude/CLAUDE.md",
    "docs/REFERENCE.md",
    "docs/TESTING.md",
)


def _section(text, heading):
    """The lines under `heading`, up to the next same-or-higher-level heading.

    Prefix match, not equality: the live heading carries a parenthetical suffix
    ("... (lost `.git` link routes git to main)") that is editorial, and pinning the
    whole line would make this gate fail on a harmless reword of the parenthetical.

    Fence-aware: the runbook's own code block is full of `# 1. ...` shell comments,
    which are indistinguishable from an `#` heading line by prefix alone. A
    fence-blind scan ends the section at the first such comment and silently yields
    a section with no closing fence -- i.e. the parity check would compare against
    nothing and pass vacuously.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip().startswith(heading))
    except StopIteration:
        return ""
    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    in_fence = False
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        depth = len(lines[i]) - len(lines[i].lstrip("#"))
        if lines[i].startswith("#") and 0 < depth <= level:
            end = i
            break
    return "\n".join(lines[start + 1:end])


def _fenced_blocks(text):
    """Every fenced code block's body in `text`, concatenated."""
    return "\n".join(re.findall(r"^```[a-zA-Z]*\n(.*?)^```", text, re.DOTALL | re.MULTILINE))


def _runnable_lines(code):
    """Non-blank, non-comment lines of a shell code block, stripped.

    Comment lines are excluded deliberately: accepting them as satisfying a step is
    exactly how the first version of this gate let four drift mutations through.
    """
    out = []
    for raw in code.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.split("#", 1)[0].strip() if " #" in line else line)
    return out


def _emittable_string_literals(source):
    """Every string literal in `source` that is NOT a docstring, as (lineno, value).

    The layer-3 AST scan must flag a *prescription* of the disproven recipe, never
    an *explanation* of why it is wrong -- and this module, the hook, and the ADR
    all explain it at length. Comments never reach the AST at all, and docstrings
    are excluded here, so what remains is the set of literals a script could emit.
    Mirrors the AST approach `test_hook_output_contract.py` already uses.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class TestRecipeRendering(unittest.TestCase):
    """Layer 1 -- the module is a faithful, wire-safe renderer of its own steps."""

    ORPHAN = "C:/Users/brown/Git/dev-env/.claude/worktrees/orphan-name"
    CANONICAL = "C:/Users/brown/Git/dev-env"

    def setUp(self):
        self.recipe = wr.recovery_recipe(self.ORPHAN, self.CANONICAL)
        self.commands = wr.recovery_commands(self.ORPHAN, self.CANONICAL)

    def test_steps_are_non_empty(self):
        self.assertTrue(wr.RECOVERY_STEPS, "RECOVERY_STEPS is empty")

    def test_every_command_appears_in_order(self):
        cursor = -1
        for command in self.commands:
            found = self.recipe.find(command, cursor + 1)
            self.assertNotEqual(found, -1,
                                f"recipe is missing (or misorders) the command: {command}")
            cursor = found

    def test_path_placeholders_are_substituted(self):
        # Keyed off the rendered commands, not a hardcoded digit prefix: a literal
        # set silently stops covering any step added beyond it, in a gate whose whole
        # value is non-vacuity.
        self.assertEqual(len(self.commands), len(wr.RECOVERY_STEPS))
        for command in self.commands:
            for token in (wr.CANONICAL, wr.ORPHAN):
                self.assertNotIn(token, command,
                                 f"unsubstituted {token} in rendered command: {command}")
            self.assertIn(command, self.recipe)
        self.assertIn(self.ORPHAN, self.recipe)
        self.assertIn(self.CANONICAL, self.recipe)

    def test_substitution_is_single_pass(self):
        # A canonical path containing the literal text `<orphan>` must not be
        # rewritten by a second replace -- on the destructive step that would name a
        # path the reader never intended.
        weird_canonical = "/tmp/repo<orphan>x"
        commands = wr.recovery_commands("/tmp/wt", weird_canonical)
        self.assertTrue(any(weird_canonical in c for c in commands),
                        f"canonical path was corrupted by chained substitution: {commands}")

    def test_branch_placeholder_is_preserved(self):
        # The hook cannot read an orphan's HEAD, so <branch> stays the reader's job.
        self.assertIn(wr.BRANCH, self.recipe)
        self.assertIn(wr.BRANCH_HINT, self.recipe)

    def test_recipe_is_ascii(self):
        # Kept ASCII so the recipe survives the RAW exit-2 stderr channel after the
        # _hookout.emit_block migration (dev-env#865). Today's json.dumps channel
        # would also tolerate non-ASCII -- see the module docstring.
        self.assertTrue(self.recipe.isascii(), "rendered recipe contains non-ASCII characters")
        for step in wr.RECOVERY_STEPS:
            self.assertTrue(step.command.isascii(), f"non-ASCII command: {step.command!r}")
            self.assertTrue(step.note.isascii(), f"non-ASCII note: {step.note!r}")

    def test_recipe_does_not_carry_the_disproven_form(self):
        # The recipe NAMES --force to warn against it, so match only a runnable shape:
        # a line that starts with `git` and carries the flag.
        for line in self.recipe.splitlines():
            stripped = line.strip().lstrip("!-. 0123456789")
            if stripped.startswith("git ") and DISPROVEN_RECIPE_RE.search(stripped):
                self.fail(f"recipe prescribes the disproven --force/-f form: {line!r}")

    def test_repair_is_first_and_destructive_steps_are_conditional(self):
        # Ordering is load-bearing: `worktree repair` preserves uncommitted work,
        # `find -delete` destroys it. Asserted on the flags, not on list position, so
        # adding a step cannot silently invalidate the invariant.
        self.assertIn("worktree repair", wr.RECOVERY_STEPS[0].command)
        self.assertFalse(wr.RECOVERY_STEPS[0].conditional)
        destructive = [s for s in wr.RECOVERY_STEPS if s.destructive]
        self.assertTrue(destructive, "no step is flagged destructive")
        for step in destructive:
            self.assertTrue(step.conditional,
                            f"destructive step is in the unconditional sequence: {step.command}")

    def test_destructive_step_is_not_rendered_as_a_numbered_item(self):
        # A reader working top-to-bottom must never reach the delete by default --
        # the failure mode a position-only ordering test does not catch.
        numbered = [ln.strip() for ln in self.recipe.splitlines()
                    if re.match(r"^\s+\d+\.\s", ln)]
        for step in wr.RECOVERY_STEPS:
            if not step.destructive:
                continue
            command = wr._substitute(step.command, self.ORPHAN, self.CANONICAL)
            for line in numbered:
                self.assertNotIn(command, line,
                                 f"destructive step rendered as numbered item: {line!r}")

    def test_a_salvage_step_precedes_the_destructive_one(self):
        order = list(wr.RECOVERY_STEPS)
        first_destructive = next(i for i, s in enumerate(order) if s.destructive)
        preceding = order[:first_destructive]
        self.assertTrue(any("cp -r" in s.command or "Copy-Item" in s.note for s in preceding),
                        "no salvage/capture step precedes the irreversible delete "
                        "(global CLAUDE.md -> Back up before you mutate)")


class TestRunbookParity(unittest.TestCase):
    """Layer 2 -- docs/REFERENCE.md's runbook agrees with the module, in order."""

    @classmethod
    def setUpClass(cls):
        cls.text = REFERENCE_MD.read_text(encoding="utf-8")
        cls.runbook = _section(cls.text, RUNBOOK_HEADING)
        cls.runnable = _runnable_lines(_fenced_blocks(cls.runbook))

    def test_runbook_section_exists(self):
        self.assertTrue(self.runbook.strip(),
                        f"{REFERENCE_MD.name} has no '{RUNBOOK_HEADING}' section "
                        "(renamed? update RUNBOOK_HEADING here in the same PR)")

    def test_runbook_has_runnable_lines(self):
        self.assertTrue(self.runnable,
                        "the runbook section has no runnable (non-comment) code lines "
                        "to compare against -- a vacuous pass, not a clean one")

    def test_runnable_lines_equal_the_canonical_commands(self):
        expected = [s.command for s in wr.RECOVERY_STEPS]
        actual = [ln for ln in self.runnable if not ln.startswith(RUNBOOK_ONLY_COMMANDS)]
        self.assertEqual(
            actual, expected,
            "docs/REFERENCE.md's runbook and _worktree_recovery.RECOVERY_STEPS disagree.\n"
            f"  runbook: {actual}\n  module : {expected}\n"
            "Both surfaces render one recipe -- edit the MODULE and mirror it here in the "
            "same PR (ADR-116). Runbook-only commands must be added to "
            "RUNBOOK_ONLY_COMMANDS so the exception stays visible.")

    def test_runbook_does_not_reintroduce_forbidden_commands(self):
        for line in self.runnable:
            for pattern in FORBIDDEN_RUNBOOK_RE:
                self.assertIsNone(
                    pattern.search(line),
                    f"runbook prescribes a removed command: {line!r} -- `rm -rf` fails on "
                    "the session's own cwd (dev-env#862) and `checkout main` is blocked by "
                    "ADR-071's canonical-mutate guard.")


class TestNoDisprovenRecipeInLiveSurfaces(unittest.TestCase):
    """Layer 3 -- the disproven form is gone from every LIVE surface.

    docs/adr/ is exempt by design (see the module docstring).
    """

    def test_no_script_emits_the_disproven_recipe(self):
        # Non-recursive by design: `claude/scripts/*.py` is the set of hook and library
        # modules that can actually EMIT a recipe to a blocked session. `tests/` is
        # excluded deliberately -- a test that asserts the disproven form is *absent*
        # necessarily contains the string, and so does this test's own failure message,
        # both of which an AST pass cannot tell from a prescription. The breadth the
        # AST pass gives up here (skills, routines, shell scripts, docs) is covered by
        # the text pass below, which looks only at runnable fenced lines.
        offenders = []
        for script in sorted(SCRIPTS_DIR.glob("*.py")):
            for lineno, value in _emittable_string_literals(
                    script.read_text(encoding="utf-8")):
                if DISPROVEN_RECIPE_RE.search(value):
                    offenders.append(f"{script.name}:{lineno}: {value.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "`worktree add --force/-f` reintroduced in an emittable string -- "
                         "it does nothing for a non-empty target directory (dev-env#751, "
                         "re-verified dev-env#862). Render "
                         "_worktree_recovery.recovery_recipe() instead. (Comments and "
                         "docstrings explaining why it fails are exempt by design.)")

    def test_live_docs_do_not_prescribe_the_disproven_recipe(self):
        # Text pass over the live operational surfaces the AST pass cannot see.
        # Only *runnable* fenced lines count -- prose explaining why the form fails
        # is the point of ADR-116 and must stay allowed.
        paths = [REPO_ROOT / rel for rel in LIVE_TEXT_SURFACES]
        # Skills and routines are markdown that a session executes from, so a runnable
        # recipe can land there too -- the breadth the non-recursive AST pass gives up.
        paths += sorted((REPO_ROOT / "claude" / "skills").rglob("*.md"))
        paths += sorted((REPO_ROOT / "claude" / "routines").rglob("*.md"))
        offenders = []
        for path in paths:
            if not path.exists():
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            for line in _runnable_lines(_fenced_blocks(path.read_text(encoding="utf-8"))):
                if DISPROVEN_RECIPE_RE.search(line):
                    offenders.append(f"{rel}: {line}")
        self.assertEqual(offenders, [],
                         "a live doc prescribes `worktree add --force/-f` as a runnable "
                         "step (prose explaining why it fails is fine; a command is not)")

    def test_hooks_table_row_exists_and_is_clean(self):
        # Count first: asserting only inside a filtered loop means a reformatted or
        # renamed row silently runs ZERO assertions and the test passes -- the same
        # vacuous-pass class ADR-116 flags as safety-critical. Precedent:
        # test_testing_index_parity.py (item 76).
        rows = [line for line in REFERENCE_MD.read_text(encoding="utf-8").splitlines()
                if "pre-tool-use-worktree-path-check.py" in line and line.lstrip().startswith("|")]
        self.assertGreaterEqual(
            len(rows), 1,
            "no docs/REFERENCE.md hooks-table row found for "
            "pre-tool-use-worktree-path-check.py -- if the row was renamed, reformatted, "
            "or moved, update this test in the same PR rather than letting it no-op")
        for row in rows:
            self.assertIsNone(DISPROVEN_RECIPE_RE.search(row),
                              f"hooks-table row quotes the disproven recipe: {row[:120]}")


if __name__ == "__main__":
    unittest.main()
