#!/usr/bin/env python3
"""Single source of truth for the orphaned-worktree recovery recipe (dev-env#862).

An *orphaned* worktree is a worktree-shaped directory that git no longer resolves
to itself: its `.git` link file is missing, or the `gitdir:` target that link points
at (`<canonical>/.git/worktrees/<name>/`) was pruned away. git from inside it
silently walks up and resolves to the CANONICAL repo, so every write lands on the
wrong tree (dev-env#328, ADR-024's liveness guard).

Why this module exists at all
-----------------------------
The recipe had two independent copies -- `pre-tool-use-worktree-path-check.py`'s
block message (the one a stuck session actually reads, since it cannot Write or
Edit until it acts on that text) and `docs/REFERENCE.md`'s "Worktree
deregistration recovery" runbook -- and they drifted. dev-env#751 disproved the
`git worktree add --force` recipe and corrected the runbook, but never touched the
hook message, so the disproven recipe stayed on the surface that matters most for
another six weeks (dev-env#862, hit live 2026-07-22 in career-playbook #823).
`RECOVERY_STEPS` below is now the only copy; the hook renders it and
`tests/test_worktree_recovery.py` pins the runbook against it. See ADR-116.

Ground truth (git 2.37.1.windows.1, throwaway-fixture matrix, 2026-07-22)
------------------------------------------------------------------------
Every claim encoded in the steps below was verified against real repos, because
the whole point of dev-env#862 is that an unverified recipe here costs a blocked
session real recovery time:

  * `worktree repair <path>` RESTORES the `.git` link and preserves uncommitted
    work whenever the admin dir survived -- but it EXITS 1 and prints
    `error: unable to locate repository; .git file broken` while doing so. That
    text describes the state it FOUND, not a failure (a second run exits 0
    silently). Reading the exit code alone makes a successful repair look failed;
    hence the explicit `rev-parse` verification step (global CLAUDE.md
    "Error Message Diligence" -- the message is evidence of what was reported,
    not of what is true).
  * `worktree repair` cannot resurrect a DELETED admin dir (exits 0, does
    nothing), and cannot help when both sides are gone. Those fall through to the
    prune/add path.
  * Plain `worktree add` onto a still-registered path fails with `missing but
    already registered worktree`; `prune` clears that.
  * `worktree add` -- WITH OR WITHOUT `--force` -- dies `fatal: '<path>' already
    exists` on any NON-EMPTY target directory. git checks
    `file_exists(path) && !is_empty_dir(path)` BEFORE it ever consults `--force`,
    so `--force` is irrelevant to this failure. It overrides only the
    stale-registration / branch-checked-out-elsewhere safeguards (dev-env#751,
    re-confirmed). Note this also means dev-env#862's report that a plain `add`
    "tolerates the leftover junk" does not hold: the directory must be emptied.
  * Emptying the directory IN PLACE (`find <orphan> -mindepth 1 -delete`) works
    even when it is the shell's own cwd -- the directory itself survives, so the
    shell keeps a valid cwd. `rm -rf <orphan>` removes the directory itself and
    can fail with `Device or resource busy` when a handle is held on it, which is
    the common case for the blocked session's own cwd (dev-env#862). That is why
    step 5 empties rather than removes, and why dev-env#751's proposed `rm -rf`
    step is NOT the recipe here.

Pure -- no I/O, no subprocess, ASCII-only strings (a block reason crosses Claude
Code's cp1252 hook-output pipe; hook authoring rules 4/5). Imported as a sibling
module in `scripts/` via `sys.path`, the same way as `_worktree_canon` /
`_hookutil` / `_hookout`. Per docs/REFERENCE.md, library modules like this one get
no hooks/utilities table row -- they are documented in prose next to their
consumer.
"""
from __future__ import annotations

from typing import NamedTuple

# Placeholder tokens used in `RECOVERY_STEPS`. `recovery_recipe()` substitutes the
# first two with real paths; `<branch>` stays a placeholder because the hook cannot
# know the branch (the orphan's HEAD is unreadable by definition) -- the trailing
# hint below tells the reader how to find it.
CANONICAL = "<canonical>"
ORPHAN = "<orphan>"
BRANCH = "<branch>"


class RecoveryStep(NamedTuple):
    """One ordered step: the literal command, and why it is there / what to watch for.

    `command` is the exact string that must also appear, in this order, in
    docs/REFERENCE.md's "Worktree deregistration recovery" fenced block -- that is
    what `tests/test_worktree_recovery.py`'s parity check asserts, so the hook
    message and the runbook cannot drift apart again (ADR-116).
    """

    command: str
    note: str


RECOVERY_STEPS: tuple[RecoveryStep, ...] = (
    RecoveryStep(
        command=f"git -C {CANONICAL} worktree repair {ORPHAN}",
        note=(
            "Try first: non-destructive, and preserves uncommitted work. It can exit 1 "
            'printing "error: unable to locate repository; .git file broken" and STILL '
            "have fixed it -- that text describes the state it found, not a failure. "
            "Do not judge it by the exit code; use the next step."
        ),
    ),
    RecoveryStep(
        command=f"git -C {ORPHAN} rev-parse --show-toplevel",
        note=(
            # Deliberately phrased without the <orphan> token: `recovery_recipe()`
            # substitutes placeholders in commands only, so a token here would reach
            # the reader unexpanded next to fully-expanded paths.
            "Verification, not a fix. Prints the worktree path -> recovered, stop "
            "here. Prints the canonical root instead -> still orphaned, continue."
        ),
    ),
    RecoveryStep(
        command=f"git -C {CANONICAL} worktree prune",
        note=(
            "Clears the stale registration. Without it the next step fails with "
            '"missing but already registered worktree".'
        ),
    ),
    RecoveryStep(
        command=f"git -C {CANONICAL} worktree add {ORPHAN} {BRANCH}",
        note=(
            "Plain add -- NOT --force. --force overrides only the stale-registration and "
            "branch-checked-out-elsewhere safeguards; it does nothing for a non-empty "
            "target directory (dev-env#751)."
        ),
    ),
    RecoveryStep(
        command=f"find {ORPHAN} -mindepth 1 -delete",
        note=(
            'Only if the previous step said "already exists". Empties the directory IN '
            "PLACE, then repeat that step. Do NOT rm -rf the directory itself: it is "
            "typically this session's own cwd (the shell cwd resets back to it between "
            'Bash calls) and a held handle fails with "Device or resource busy". This '
            "discards uncommitted work in the orphan, which is why step 1 comes first."
        ),
    ),
)

# Appended after the numbered steps. The hook cannot read the orphan's HEAD, so the
# branch stays the reader's job.
BRANCH_HINT = (
    f"{BRANCH} is typically claude/<worktree-name>; confirm with `git branch -a`."
)

_WRAP_WIDTH = 78


def _wrap(text: str, indent: str) -> list[str]:
    """Greedy word-wrap to `_WRAP_WIDTH`, prefixing every line with `indent`.

    Hand-rolled rather than `textwrap` only to keep the emitted note lines stable
    across Python versions -- this text is asserted on by the tests and read by a
    blocked session, so it should not shift under an interpreter upgrade.
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(indent) + len(current) + 1 + len(word) > _WRAP_WIDTH:
            lines.append(indent + current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        lines.append(indent + current)
    return lines


def recovery_commands(orphan_root: str, canonical_root: str) -> list[str]:
    """`RECOVERY_STEPS`' commands with the two path placeholders substituted.

    `<branch>` is deliberately left as a placeholder -- see `BRANCH_HINT`.
    """
    return [
        step.command.replace(CANONICAL, canonical_root).replace(ORPHAN, orphan_root)
        for step in RECOVERY_STEPS
    ]


def recovery_recipe(orphan_root: str, canonical_root: str) -> str:
    """The formatted, ASCII recovery block a blocking hook embeds in its reason.

    Rendered from `RECOVERY_STEPS` so the hook message, the runbook, and the tests
    all trace to one definition (ADR-116). Guaranteed `.isascii()` as long as the
    step text is -- pinned by `tests/test_worktree_recovery.py`, since a non-ASCII
    byte on the exit-2 stderr channel is mangled by Claude Code's cp1252 hook-output
    pipe (hook authoring rules 4/5).
    """
    commands = recovery_commands(orphan_root, canonical_root)
    lines = ["Recover the worktree, in order -- stop as soon as it is live again:", ""]
    for number, (command, step) in enumerate(zip(commands, RECOVERY_STEPS), start=1):
        lines.append(f"  {number}. {command}")
        lines.extend(_wrap(step.note, "       "))
    lines.append("")
    lines.append(BRANCH_HINT)
    return "\n".join(lines)
