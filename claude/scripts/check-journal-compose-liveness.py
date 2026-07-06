#!/usr/bin/env python3
"""Detect an in-flight session that may still be writing engineering-journal
stubs for the date journal-compose is about to merge.

ADR-084 documents a race activated by dev-env#579: the nightly automation
merges yesterday's draft/YYYY-MM-DD branch, but a session that started before
local midnight and is still uncommitted past compose time writes its stub
under that same "yesterday" date. If compose merges the branch before the
session commits and pushes, the session's later push hits the pre-push
hook's merged-draft-branch block.

engineering-journal is a single shared checkout (sessions across every
project write to it via `git -C`, not a per-session worktree of the journal
itself — see claude/CLAUDE.md's Stub file workflow), so an uncommitted stub
for the target date shows up as a dirty working tree there. This script reads
`git status --porcelain` output from stdin (the caller runs git; this script
stays pure I/O so it's testable without subprocess mocking, matching this
repo's established convention — see _hookio.py, _journal_shards.py) and exits
1 if any changed path is a stub or manifest shard for the given date.

Usage:
    git -C C:/Users/brown/Git/engineering-journal status --porcelain | \\
        py -3 claude/scripts/check-journal-compose-liveness.py YYYY-MM-DD

Exit 0: no uncommitted changes for this date — safe to proceed.
Exit 1: uncommitted changes for this date found — a session may still be
        active; caller should abort/retry rather than compose now.
Exit 2: usage error (including a date argument that isn't YYYY-MM-DD — this
        also catches a caller that failed to substitute a "YYYY-MM-DD"
        placeholder literal, which would otherwise match nothing and pass
        vacuously).
"""
import re
import sys

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def has_uncommitted_target_date_changes(porcelain_output, date):
    """True if any changed path under sessions/ belongs to the given date's
    stub/manifest shard (filenames are YYYY-MM-DD_HHMMSS.stub.md or
    .manifest.jsonl — see claude/CLAUDE.md Stub file workflow). Requires the
    matching shard suffix, not just the date marker, so a hypothetical future
    path merely containing "/YYYY-MM-DD_" (e.g. a differently-named directory)
    can't false-positive."""
    marker = f"/{date}_"
    for line in porcelain_output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            # Rename: "OLD -> NEW" — the destination is what's live now.
            path = path.split(" -> ", 1)[1]
        if marker in path and (path.endswith(".stub.md") or path.endswith(".manifest.jsonl")):
            return True
    return False


def format_abort_message(date):
    return (
        f"ABORT: engineering-journal has uncommitted sessions/ changes for {date} - "
        "a session may still be writing stubs for this date. Not composing this run."
    )


def main(argv):
    if len(argv) != 2:
        print("usage: check-journal-compose-liveness.py YYYY-MM-DD", file=sys.stderr)
        return 2
    date = argv[1]
    if not DATE_RE.match(date):
        print(
            f"usage: check-journal-compose-liveness.py YYYY-MM-DD (got {date!r}, "
            "not a YYYY-MM-DD date)",
            file=sys.stderr,
        )
        return 2
    porcelain = sys.stdin.read()
    if has_uncommitted_target_date_changes(porcelain, date):
        print(format_abort_message(date), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
