#!/usr/bin/env python3
"""Claude Code skill-invoked script (NOT a hook) -- mechanically resolves
FORCE for /journal-compose's today-guard from the literal, unparsed
`$ARGUMENTS` text of the invocation, and records the result to a same-day
marker file (dev-env#631, ADR-096).

This is the very first Bash action of Step 0.6 in
claude/skills/journal-compose/SKILL.md -- run BEFORE any of the skill's own
date/branch resolution or reasoning, so FORCE is fixed by whatever the
harness actually substituted into `$ARGUMENTS` at invocation time, not by
the agent's own inference about what the task "must have meant" (the
failure mode dev-env#631's transcript evidence documents: an agent noticed
the today-guard should apply, reasoned that the task's framing implied
`--force` anyway, and proceeded without the guard's refusal text ever being
emitted or read).

The companion PreToolUse hook, pre-tool-use-journal-compose-force-guard.py,
is what actually enforces this -- it hard-blocks same-day worktree-add /
commit / push commands unless this script already wrote a fresh,
force=true marker for today. This script itself never blocks anything; it
only records a fact.

Usage (from SKILL.md, using the literal $ARGUMENTS substitution verbatim --
never a hand-typed or paraphrased argument):
    py -3 C:/Users/brown/.claude/scripts/journal-compose-force-resolve.py "$ARGUMENTS"

Prints exactly one line: `FORCE=true` or `FORCE=false`.
"""
import datetime
import sys

from _journal_compose_force import build_marker, marker_path_for, resolve_force, write_marker


def main() -> None:
    raw_args = sys.argv[1] if len(sys.argv) > 1 else ""
    force = resolve_force(raw_args)
    now = datetime.datetime.now()
    today = now.date().isoformat()
    marker = build_marker(force, raw_args, now)
    write_marker(marker_path_for(today), marker)
    print(f"FORCE={'true' if force else 'false'}")


if __name__ == "__main__":
    main()
