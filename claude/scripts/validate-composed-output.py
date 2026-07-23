#!/usr/bin/env python3
"""Pre-commit scanner for composed journal output (dev-env #894, ADR-121).

Wired into the `journal-compose` skill as the Step 8b gate: it runs after every file the
compose writes exists (the journal entry from Step 6, the folder README from Step 7, the
top-level README from Steps 8/8a) and *before* Step 9 deletes the source stubs -- so a hit
can still be reconciled against the stubs that produced it.

Rationale, signature list, and the fence/inline-code exemptions live in
``_composed_output_scan.py``. In short: the 2026-07-11 compose spliced a `git rebase` usage
message mid-paragraph into a README `## Progress Summary`, and it survived ~8 further compose
passes and 11 days because dev-env#467's Step 6.5 check validates *headings* on journal
*entries* only.

Usage (paths may be shell globs -- non-matching / absent paths are skipped, so an unmatched
glob the shell passes through literally is harmless):

    py -3 validate-composed-output.py <markdown-path> [<markdown-path> ...]

Exit 0 -- no stray terminal output found (or no readable files were named).
Exit 1 -- at least one region needs review, or a named file could not be read/decoded.

**Advisory, not corrective.** This never edits a file. The motivating corruption was
self-concealing -- the paste ate the middle of a sentence and welded a surviving prose
fragment onto the tail of a `git branch --set-upstream-to` line -- so deleting the
machine-looking block would have silently dropped real content. Every hit is a region to
read, and a hit can legitimately be prose (in which case wrap it in a code fence or an
inline code span, which is also how it should have been written).
"""
from __future__ import annotations

import os
import sys

from _composed_output_scan import scan_text


def main(argv) -> int:
    paths = argv[1:]
    scanned = 0
    read_errors = []                   # list[str] -- "path (unreadable: ...)"
    hits = []                          # list[tuple[str, dict]] -- (path, finding)

    for path in paths:
        if not os.path.isfile(path):
            # Unmatched shell glob passed through literally, or a file this compose did
            # not write (e.g. no top-level README) -- not a failure; nothing to check.
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as exc:
            read_errors.append(f"{path} (unreadable: {exc})")
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            read_errors.append(f"{path} (not valid UTF-8: {exc})")
            continue
        scanned += 1
        for finding in scan_text(text):
            hits.append((path, finding))

    if not hits and not read_errors:
        noun = "file" if scanned == 1 else "files"
        print(
            f"[validate-composed-output] OK - {scanned} composed {noun} scanned; "
            "no stray terminal output found."
        )
        return 0

    sys.stderr.write(
        "[validate-composed-output] FAIL - composed output contains text that looks like "
        "raw terminal output.\n"
        "Read each region below before committing. This gate does NOT edit anything: the "
        "2026-07-11\ncorruption it exists to catch had a real sentence welded onto the tail "
        "of a pasted git usage\nmessage, so blind deletion loses content.\n\n"
        "For each hit: if it is stray output, remove it and restore whatever prose it "
        "overwrote (the\nsource stubs still exist -- Step 9 has not run yet). If it is "
        "intentional documentation, wrap\nit in a code fence or an inline code span, which "
        "also silences this check.\n\n"
    )
    if hits:
        current = None
        for path, finding in hits:
            if path != current:
                sys.stderr.write(f"{path}\n")
                current = path
            sys.stderr.write(
                f"  - line {finding['line']} [{finding['kind']}] {finding['detail']}\n"
                f"      {finding['text']}\n"
            )
        sys.stderr.write("\n")
    if read_errors:
        sys.stderr.write("Unreadable files:\n")
        for src in read_errors:
            sys.stderr.write(f"  - {src}\n")
        sys.stderr.write("\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
