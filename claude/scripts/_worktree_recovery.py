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
    work when the admin dir survived -- and EXITS 1 while doing so, printing
    `error: unable to locate repository; .git file broken`. That text describes
    the state it FOUND, not a failure.
  * **The exit code and the error text cannot tell success from failure here.**
    Exit 1 occurs in all three shapes, and the both-sides-gone FAILURE prints the
    byte-identical `.git file broken` message that the success case does. (The
    link-intact/admin-deleted shape differs only in wording -- `.git file does not
    reference a repository` -- and is likewise a failure.) This is why step 2 is a
    separate, mandatory verification rather than a courtesy: it is the ONLY
    reliable signal. Global CLAUDE.md "Error Message Diligence" in its sharpest
    form -- here the message understates success in one case and overstates it in
    another, using the same words.
  * `worktree repair` therefore CANNOT recover the both-sides-gone shape at all.
    In that shape the destructive step below is the only forward path, and the
    uncommitted work in the orphan is genuinely unrecoverable by git -- hence the
    salvage-copy step, which is not optional decoration.
  * Plain `worktree add` onto a still-registered path fails with `missing but
    already registered worktree`; `prune` clears that.
  * `worktree add` -- WITH OR WITHOUT `--force`/`-f` -- dies `fatal: '<path>'
    already exists` on any NON-EMPTY target directory. git checks
    `file_exists(path) && !is_empty_dir(path)` BEFORE it ever consults the flag,
    so the flag is irrelevant to this failure. It overrides only the
    stale-registration / branch-checked-out-elsewhere safeguards (dev-env#751,
    re-confirmed; the `-f` short alias fails identically). Note this also means
    dev-env#862's report that a plain `add` "tolerates the leftover junk" does not
    hold: the directory must be emptied.
  * Emptying the directory IN PLACE works even when it is the shell's own cwd --
    the directory itself survives, so the shell keeps a valid cwd. `rm -rf
    <orphan>` removes the directory itself and can fail with `Device or resource
    busy` when a handle is held on it, which is the common case for the blocked
    session's own cwd (dev-env#862). That is why the empty step empties rather
    than removes, and why dev-env#751's proposed `rm -rf` step is NOT the recipe.

Pure -- no I/O, no subprocess, ASCII-only strings. The ASCII constraint is kept so
the recipe survives the RAW exit-2 stderr channel once dev-env#865 migrates this
hook onto `_hookout.emit_block`; today's `_block()` writes
`json.dumps({"reason": ...})`, whose default `ensure_ascii=True` would already
escape non-ASCII on the wire (see `_hookout.py`'s docstring, and
`test_hook_output_contract.py`'s json.dumps exemption). Cheap insurance either way,
since the interpolated paths are unsanitized on both channels.

Imported as a sibling module in `scripts/` via `sys.path`, the same way as
`_worktree_canon` / `_hookutil` / `_hookout`. Per docs/REFERENCE.md, library
modules like this one get no hooks/utilities table row in that file -- they are
documented in prose next to their consumer (the per-directory
`claude/scripts/README.md` index DOES carry a row for every `_foo.py`, and is a
separate obligation).

`collections.namedtuple`, not `typing.NamedTuple`: importing `typing` costs
~7-11 ms of cold-import time, and this module is imported at module scope by a
PreToolUse hook that spawns a fresh interpreter on EVERY Write/Edit/NotebookEdit
call -- including the overwhelming majority that exit 0 immediately because the
cwd is not a worktree at all. `namedtuple` gives the identical structure for
~0.06 ms.
"""
from __future__ import annotations

import textwrap
from collections import namedtuple

# Placeholder tokens used in `RECOVERY_STEPS`. `recovery_recipe()` substitutes the
# first two with real paths; `<branch>` stays a placeholder because the hook cannot
# know the branch (the orphan's HEAD is unreadable by definition) -- the trailing
# hint below tells the reader how to find it.
#
# Quoted at every use site in the templates below, so a worktree path containing a
# space still produces a copy-pasteable command -- and so the RUNBOOK carries the
# same quotes, since parity is asserted on these exact strings.
CANONICAL = "<canonical>"
ORPHAN = "<orphan>"
BRANCH = "<branch>"

RecoveryStep = namedtuple("RecoveryStep", "command note conditional destructive")
RecoveryStep.__doc__ = """One step: the literal command, why it is there, and two flags.

`command` is the exact string that must also appear, in this order, among the
RUNNABLE lines of docs/REFERENCE.md's "Worktree deregistration recovery" fenced
block -- `tests/test_worktree_recovery.py`'s parity check asserts equality against
those lines (comments excluded), so neither surface can drift (ADR-116).

`conditional` marks a step that is NOT part of the top-to-bottom sequence: it runs
only when the preceding numbered step reports `already exists`. Rendering these in
the numbered list is how a stressed reader ends up executing a destructive step
unconditionally, so `recovery_recipe()` puts them in a separate trailer block.

`destructive` marks a step that irreversibly discards data. The ordering invariant
the tests pin is expressed through this flag, not through list position.
"""


RECOVERY_STEPS: tuple[RecoveryStep, ...] = (
    RecoveryStep(
        command=f'git -C "{CANONICAL}" worktree repair "{ORPHAN}"',
        note=(
            "Try first: non-destructive, and preserves uncommitted work. Do NOT judge it "
            "by its exit code or its message -- it exits 1 and prints "
            '"error: unable to locate repository; .git file broken" both when it '
            "SUCCEEDS and when it cannot help. Step 2 is the only reliable signal."
        ),
        conditional=False,
        destructive=False,
    ),
    RecoveryStep(
        command=f'git -C "{ORPHAN}" rev-parse --show-toplevel',
        note=(
            # Deliberately phrased without the <orphan> token: `recovery_recipe()`
            # substitutes placeholders in commands only, so a token here would reach
            # the reader unexpanded next to fully-expanded paths.
            "Verification, not a fix, and the real decision point. Prints the worktree "
            "path -> recovered, stop here. ANYTHING ELSE -> still orphaned, continue: "
            "that includes the canonical root, and includes a "
            '"fatal: not a git repository" error (which is what a sibling-convention '
            "orphan prints, since it sits outside any repo for git to walk up to)."
        ),
        conditional=False,
        destructive=False,
    ),
    RecoveryStep(
        command=f'git -C "{CANONICAL}" worktree prune',
        note=(
            "Clears the stale registration. Without it the next step fails with "
            '"missing but already registered worktree".'
        ),
        conditional=False,
        destructive=False,
    ),
    RecoveryStep(
        command=f'git -C "{CANONICAL}" worktree add "{ORPHAN}" {BRANCH}',
        note=(
            "Plain add -- NOT --force/-f. Neither flag helps here: they override only the "
            "stale-registration and branch-checked-out-elsewhere safeguards, and git "
            "rejects a non-empty target before it ever reads them (dev-env#751). If this "
            'succeeds you are done; if it says "already exists", do the two steps below.'
        ),
        conditional=False,
        destructive=False,
    ),
    RecoveryStep(
        command=f'cp -r "{ORPHAN}" "{ORPHAN}.salvage"',
        note=(
            "Capture BEFORE emptying. Step 1 cannot recover the shape where both the "
            "`.git` link and the admin dir are gone, so at this point the orphan's "
            "uncommitted work exists nowhere else and the next step destroys it. Copy to "
            "a path OUTSIDE the worktree if disk is tight. (PowerShell: "
            "Copy-Item -Recurse <orphan> <orphan>.salvage)"
        ),
        conditional=True,
        destructive=False,
    ),
    RecoveryStep(
        command=f'find "{ORPHAN}" -mindepth 1 -delete',
        note=(
            "Git Bash. IRREVERSIBLE -- empties the directory, then repeat step 4. In "
            "PowerShell `find` resolves to find.exe (the text-search tool) and fails with "
            '"FIND: Parameter format not correct"; use '
            "Get-ChildItem -Force <orphan> | Remove-Item -Recurse -Force instead. Empties "
            "IN PLACE by design: do NOT rm -rf the directory itself -- it is typically "
            "this session's own cwd (the shell cwd resets back to it between Bash calls) "
            'and a held handle fails with "Device or resource busy".'
        ),
        conditional=True,
        destructive=True,
    ),
)

# Appended after the steps. The hook cannot read the orphan's HEAD, so the branch
# stays the reader's job.
BRANCH_HINT = (
    f"{BRANCH} is typically claude/<worktree-name>; confirm with `git branch -a`."
)

_WRAP_WIDTH = 78
# `break_on_hyphens=False` so technical terms (`non-empty`, `rev-parse`) are not split
# mid-word; `break_long_words=False` so a long path is never hard-broken into an
# uncopyable fragment. Those two flags are the whole difference from the default.
_WRAP_KWARGS = {"width": _WRAP_WIDTH, "break_on_hyphens": False, "break_long_words": False}


def _wrap(text: str, indent: str) -> list[str]:
    return textwrap.wrap(text, initial_indent=indent, subsequent_indent=indent, **_WRAP_KWARGS)


def _substitute(template: str, orphan_root: str, canonical_root: str) -> str:
    """Fill both path placeholders in ONE pass.

    Chained `.replace(CANONICAL, ...).replace(ORPHAN, ...)` is order-dependent: a
    canonical path containing the literal text `<orphan>` would be rewritten by the
    second call, which on the destructive step would name a path the reader never
    intended. Impossible on Windows, possible on POSIX -- and the cost of not caring
    is a wrong `rm`-shaped command, so it is handled rather than assumed away.
    """
    out = []
    i = 0
    while i < len(template):
        if template.startswith(CANONICAL, i):
            out.append(canonical_root)
            i += len(CANONICAL)
        elif template.startswith(ORPHAN, i):
            out.append(orphan_root)
            i += len(ORPHAN)
        else:
            out.append(template[i])
            i += 1
    return "".join(out)


def recovery_commands(orphan_root: str, canonical_root: str) -> list[str]:
    """`RECOVERY_STEPS`' commands with the two path placeholders substituted.

    `<branch>` is deliberately left as a placeholder -- see `BRANCH_HINT`.
    """
    return [_substitute(step.command, orphan_root, canonical_root) for step in RECOVERY_STEPS]


def recovery_recipe(orphan_root: str, canonical_root: str) -> str:
    """The formatted, ASCII recovery block a blocking hook embeds in its reason.

    Rendered from `RECOVERY_STEPS` so the hook message, the runbook, and the tests
    all trace to one definition (ADR-116). Steps flagged `conditional` are rendered
    in a separate trailer rather than as numbered items, so a reader working
    top-to-bottom never executes the destructive step by default.
    """
    commands = recovery_commands(orphan_root, canonical_root)
    pairs = list(zip(commands, RECOVERY_STEPS))
    sequential = [(c, s) for c, s in pairs if not s.conditional]
    conditional = [(c, s) for c, s in pairs if s.conditional]

    lines = [
        f"Recover the worktree. Run steps 1-{len(sequential)} in order and stop as soon "
        "as it is live again:",
        "",
    ]
    for number, (command, step) in enumerate(sequential, start=1):
        lines.append(f"  {number}. {command}")
        lines.extend(_wrap(step.note, "       "))
    if conditional:
        lines.append("")
        lines.append(
            f'ONLY if step {len(sequential)} failed with "already exists" -- not part of '
            "the sequence above:"
        )
        for command, step in conditional:
            marker = "  !! " if step.destructive else "  -  "
            lines.append(f"{marker}{command}")
            lines.extend(_wrap(step.note, "       "))
        lines.append(f"     ...then repeat step {len(sequential)}.")
    lines.append("")
    lines.append(BRANCH_HINT)
    return "\n".join(lines)
