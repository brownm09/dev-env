#!/usr/bin/env python3
"""Static checks on the scheduled-task registration scripts (dev-env#1227).

Required when changing any claude/scripts/register-*.ps1.
Run: py -3 claude/scripts/tests/test_register_task_backups.py

Two bugs this pins, in every register-*.ps1:

1. A task backup must be written with `Out-File -Encoding unicode`.
   `Export-ScheduledTask` emits XML declaring encoding="UTF-16", so a `-Encoding utf8`
   backup carries a UTF-8 byte order mark under a UTF-16 declaration and
   `XmlDocument.Load` throws "There is no Unicode byte order mark" on it: the ADR-079
   backup isn't restorable.
2. No script may read `.Source` straight off `(Get-Command ...)`. Under
   `Set-StrictMode -Version Latest` a missing command makes that throw "The property
   'Source' cannot be found" before the script's own helpful error can run.

Scope gap: this reads source text; it doesn't write or load a real backup. The
behaviour behind each rule was checked by hand on Windows PowerShell 5.1 (PR #1431).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# The pipeline from Export-ScheduledTask to Out-File may span lines (a trailing `|`).
BACKUP_WRITE = re.compile(r"Export-ScheduledTask\b[^\n]*\|\s*\n?\s*Out-File\b([^\n]*)")
ENCODING = re.compile(r"-Encoding\s+(\w+)", re.IGNORECASE)
UNCHECKED_SOURCE = re.compile(r"\(\s*Get-Command\b[^)]*\)\s*\.Source\b", re.IGNORECASE)


def backup_problems(text: str) -> list[str]:
    problems = []
    for m in BACKUP_WRITE.finditer(text):
        enc = ENCODING.search(m.group(1))
        if not enc or enc.group(1).lower() != "unicode":
            problems.append(f"task backup written with {enc.group(0) if enc else 'no -Encoding'}")
    return problems


def source_problems(text: str) -> list[str]:
    return [f"unchecked {m.group(0)!r}" for m in UNCHECKED_SOURCE.finditer(text)]


def self_test() -> None:
    # Known-bad: the two lines register-auto-resume.ps1 had before dev-env#1227.
    bad = (
        "Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop |\n"
        "    Out-File -FilePath $BackupPath -Encoding utf8 -ErrorAction Stop\n"
        "$pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source\n"
    )
    assert backup_problems(bad), "scanner missed a utf8 backup"
    assert source_problems(bad), "scanner missed an unchecked .Source"
    # Known-good: the fixed forms.
    good = (
        "Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop |\n"
        "    Out-File -FilePath $BackupPath -Encoding unicode -ErrorAction Stop\n"
        "$pywCmd = Get-Command pyw.exe -ErrorAction SilentlyContinue\n"
        "$pyw = if ($pywCmd) { $pywCmd.Source }\n"
    )
    assert not backup_problems(good), backup_problems(good)
    assert not source_problems(good), source_problems(good)


def main() -> int:
    self_test()
    scripts = sorted(SCRIPTS_DIR.glob("register-*.ps1"))
    assert scripts, f"no register-*.ps1 found under {SCRIPTS_DIR}"
    failures, backups_seen = [], 0
    for path in scripts:
        text = path.read_text(encoding="utf-8-sig")
        backups_seen += len(BACKUP_WRITE.findall(text))
        failures += [f"{path.name}: {p}" for p in backup_problems(text) + source_problems(text)]
    # Non-vacuity: every register script on main backs up its task before removing it.
    assert backups_seen >= len(scripts), (
        f"found {backups_seen} backup writes in {len(scripts)} scripts; the pattern no longer matches"
    )
    for f in failures:
        print(f"FAIL {f}")
    print(f"{len(scripts)} scripts, {backups_seen} backup writes, {len(failures)} problems")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
