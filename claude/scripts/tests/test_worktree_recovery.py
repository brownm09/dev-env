#!/usr/bin/env python3
"""Unit + drift gates for `_worktree_recovery.py` (Testing item 78, dev-env#862/ADR-116).

Three layers, all pure offline file parse -- no subprocess, network, or git. Auto-
discovered by `run-hook-tests.py`, so this also gates CI on every PR.

  1. UNIT -- `recovery_recipe()` renders every `RECOVERY_STEPS` command, in order,
     with the path placeholders substituted, and stays `.isascii()` (the reason
     crosses Claude Code's cp1252 exit-2 stderr pipe; hook authoring rules 4/5).
  2. DOC PARITY -- docs/REFERENCE.md's "Worktree deregistration recovery" runbook
     contains the same commands, in the same order. Modelled on
     `test_testing_index_parity.py` (item 76, ADR-114).
  3. ANTI-REGRESSION -- the `git worktree add --force` recipe dev-env#751 disproved
     appears in NO live surface (`claude/scripts/*.py`, the REFERENCE.md runbook,
     the REFERENCE.md hooks table).

Why all three: the hook message and the runbook were two hand-maintained copies of
one recipe. dev-env#751 corrected the runbook and left the hook untouched, so the
disproven recipe survived for another six weeks on the surface a blocked session
actually reads -- which is dev-env#862. Layer 1 makes the module the source, layer
2 pins the runbook to it, layer 3 stops the specific disproven form from coming
back through some third surface neither of the first two covers.

`docs/adr/` is DELIBERATELY exempt from layer 3: ADRs are a historical record of
what was decided when, and ADR-024's 2026-06-06 addendum legitimately quotes the
recipe that was correct-as-believed at the time. ADR-116 supersedes it; rewriting
the history would defeat the point of keeping it.

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

# The exact form dev-env#751 disproved and dev-env#862 found still live in the hook.
# Matches `worktree add --force` with any run of whitespace, so a reflowed or
# line-wrapped reintroduction is caught too.
DISPROVEN_RECIPE_RE = re.compile(r"worktree\s+add\s+--force")


def _section(text, heading):
    """The lines under `heading`, up to the next same-or-higher-level heading.

    Prefix match, not equality: the live heading carries a parenthetical suffix
    ("... (lost `.git` link routes git to main)") that is editorial, and pinning the
    whole line would make this gate fail on a harmless reword of the parenthetical.

    Fence-aware: the runbook's own code block is full of `# 1. ...` shell comments,
    which are indistinguishable from an `#` heading line by prefix alone. A
    fence-blind scan ends the section at the first such comment and silently yields
    a section with no closing fence -- i.e. the parity check below would compare
    against nothing and pass vacuously.
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


def _emittable_string_literals(source):
    """Every string literal in `source` that is NOT a docstring, as (lineno, value).

    The layer-3 scan below must flag a *prescription* of the disproven recipe, never
    an *explanation* of why it is wrong -- and this module, the hook, and the runbook
    all deliberately explain it at length. Comments never reach the AST at all, and
    docstrings are excluded here, so what remains is the set of literals a script
    could actually emit to a user. Mirrors the AST approach
    `test_hook_output_contract.py` already uses for the hook output contract.
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

    def test_steps_are_non_empty(self):
        self.assertTrue(wr.RECOVERY_STEPS, "RECOVERY_STEPS is empty")

    def test_every_command_appears_in_order(self):
        commands = wr.recovery_commands(self.ORPHAN, self.CANONICAL)
        cursor = -1
        for command in commands:
            found = self.recipe.find(command, cursor + 1)
            self.assertNotEqual(found, -1,
                                f"recipe is missing (or misorders) the command: {command}")
            cursor = found

    def test_path_placeholders_are_substituted(self):
        for token in (wr.CANONICAL, wr.ORPHAN):
            self.assertNotIn(token, "\n".join(
                line for line in self.recipe.splitlines() if line.strip().startswith(tuple("12345"))
            ), f"unsubstituted {token} left in a rendered command line")
        self.assertIn(self.ORPHAN, self.recipe)
        self.assertIn(self.CANONICAL, self.recipe)

    def test_branch_placeholder_is_preserved(self):
        # The hook cannot read an orphan's HEAD, so <branch> stays the reader's job.
        self.assertIn(wr.BRANCH, self.recipe)
        self.assertIn(wr.BRANCH_HINT.replace(wr.BRANCH, wr.BRANCH), self.recipe)

    def test_recipe_is_ascii(self):
        # A non-ASCII byte on the exit-2 stderr channel is mangled by Claude Code's
        # cp1252 hook-output pipe (hook authoring rules 4/5).
        self.assertTrue(self.recipe.isascii(), "rendered recipe contains non-ASCII characters")
        for step in wr.RECOVERY_STEPS:
            self.assertTrue(step.command.isascii(), f"non-ASCII command: {step.command!r}")
            self.assertTrue(step.note.isascii(), f"non-ASCII note: {step.note!r}")

    def test_recipe_does_not_carry_the_disproven_form(self):
        self.assertIsNone(DISPROVEN_RECIPE_RE.search(self.recipe),
                          "the rendered recipe reintroduced `worktree add --force` "
                          "(disproved by dev-env#751, re-verified in dev-env#862)")

    def test_repair_is_first_and_destructive_step_is_last(self):
        # Ordering is load-bearing, not cosmetic: `worktree repair` preserves
        # uncommitted work, `find -delete` destroys it. A reordering that put the
        # destructive step first would silently cost a session its uncommitted files.
        self.assertIn("worktree repair", wr.RECOVERY_STEPS[0].command)
        self.assertIn("-delete", wr.RECOVERY_STEPS[-1].command)


class TestRunbookParity(unittest.TestCase):
    """Layer 2 -- docs/REFERENCE.md's runbook agrees with the module, in order."""

    @classmethod
    def setUpClass(cls):
        cls.text = REFERENCE_MD.read_text(encoding="utf-8")
        cls.runbook = _section(cls.text, RUNBOOK_HEADING)
        cls.runbook_code = _fenced_blocks(cls.runbook)

    def test_runbook_section_exists(self):
        self.assertTrue(self.runbook.strip(),
                        f"{REFERENCE_MD.name} has no '{RUNBOOK_HEADING}' section "
                        "(renamed? update RUNBOOK_HEADING here in the same PR)")

    def test_runbook_has_a_code_block(self):
        self.assertTrue(self.runbook_code.strip(),
                        "the runbook section has no fenced code block to compare against")

    def test_runbook_carries_every_command_in_order(self):
        cursor = -1
        for step in wr.RECOVERY_STEPS:
            found = self.runbook_code.find(step.command, cursor + 1)
            self.assertNotEqual(
                found, -1,
                f"docs/REFERENCE.md's runbook is missing (or misorders) the canonical "
                f"command:\n    {step.command}\n"
                "Both surfaces render one recipe -- update the runbook in the same PR as "
                "_worktree_recovery.RECOVERY_STEPS (ADR-116).")
            cursor = found


class TestNoDisprovenRecipeInLiveSurfaces(unittest.TestCase):
    """Layer 3 -- the disproven form is gone from every LIVE surface.

    docs/adr/ is exempt by design (see the module docstring).
    """

    def test_no_script_emits_the_disproven_recipe(self):
        offenders = []
        for script in sorted(SCRIPTS_DIR.glob("*.py")):
            for lineno, value in _emittable_string_literals(
                    script.read_text(encoding="utf-8")):
                if DISPROVEN_RECIPE_RE.search(value):
                    offenders.append(f"{script.name}:{lineno}: {value.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "`worktree add --force` reintroduced in an emittable string -- it "
                         "does nothing for a non-empty target directory (dev-env#751, "
                         "re-verified dev-env#862). Render "
                         "_worktree_recovery.recovery_recipe() instead. (Comments and "
                         "docstrings explaining why it fails are exempt by design.)")

    def test_runbook_does_not_prescribe_the_disproven_recipe(self):
        runbook = _section(REFERENCE_MD.read_text(encoding="utf-8"), RUNBOOK_HEADING)
        code = _fenced_blocks(runbook)
        self.assertIsNone(DISPROVEN_RECIPE_RE.search(code),
                          "docs/REFERENCE.md's runbook code block prescribes "
                          "`worktree add --force` again (dev-env#751/#862). Prose may "
                          "still explain why it does NOT work; a runnable step may not.")

    def test_hooks_table_does_not_quote_the_disproven_recipe(self):
        # The hooks table row for this hook used to repeat the recipe as a third copy
        # (dev-env#862). It should point at the runbook/module instead.
        for line in REFERENCE_MD.read_text(encoding="utf-8").splitlines():
            if "pre-tool-use-worktree-path-check.py" in line and line.startswith("|"):
                self.assertIsNone(
                    DISPROVEN_RECIPE_RE.search(line),
                    "the REFERENCE.md hooks-table row still quotes `worktree add --force`")


if __name__ == "__main__":
    unittest.main()
