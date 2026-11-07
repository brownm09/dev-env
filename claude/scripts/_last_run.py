"""What a maintenance script's last --scan-dir run found, so a repeat run prints only changes.

The nightly routines re-printed the same lists every night, which uses plan limit and hides
the new entry that matters (dev-env#1307, ADR-161). A script records its findings per repo and
per section, as {key: value}, and gets back which keys are new or changed and which have gone.

A repo or section a run never records keeps its stored findings, so a repo that failed to
answer doesn't read as "everything resolved" now and "everything new" tomorrow. A state file
that is missing or unreadable makes the run a baseline: is_baseline is True and the caller
prints its full report. Losing the file therefore costs one long report and nothing else.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "scratch" / "last-run"


def _load(path: Path) -> dict | None:
    """The stored {repo: {section: {key: value}}}, or None if missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):  # ValueError covers bad JSON and bad UTF-8.
        return None
    valid = isinstance(data, dict) and all(
        isinstance(sections, dict)
        and all(
            isinstance(findings, dict) and all(isinstance(v, str) for v in findings.values())
            for findings in sections.values()
        )
        for sections in data.values()
    )
    return data if valid else None


class LastRun:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        loaded = _load(self.path)
        self.is_baseline = loaded is None
        self._prior = loaded or {}
        self._current = {repo: dict(sections) for repo, sections in self._prior.items()}
        self.changed: Counter = Counter()  # section -> keys new or changed this run
        self.gone: Counter = Counter()  # section -> keys present last run, absent now
        if self.is_baseline:
            print(f"[last-run] no usable state at {self.path} - printing the full report")

    def prior(self, repo: str, section: str) -> dict:
        return self._prior.get(repo, {}).get(section, {})

    def record(self, repo: str, section: str, findings: dict) -> tuple[list[str], list[str]]:
        """Store this run's findings; return (new or changed keys, gone keys), each sorted."""
        before = self.prior(repo, section)
        changed = sorted(k for k, v in findings.items() if before.get(k) != v)
        gone = sorted(k for k in before if k not in findings)
        self._current.setdefault(repo, {})[section] = findings
        self.changed[section] += len(changed)
        self.gone[section] += len(gone)
        return changed, gone

    def save(self) -> None:
        """Replace the state file atomically. A failed write warns and doesn't raise: the
        run's work is done, and the only cost is reporting the same changes again next run."""
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._current, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[last-run] WARNING: could not write {self.path}: {exc}")
