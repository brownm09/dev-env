#!/usr/bin/env python3
"""Shared `gh project item-add` wrapper (dev-env#454).

`post-tool-use.py` (the PostToolUse project-board add-hook) and
`reconcile-project-board.py` (its background-session backstop, ADR-068) each had their own
`add_to_project()` subprocess wrapper. This module reconciles them onto one superset shape:

  - Returns `(item_id, stderr_or_exception_str)` — reconcile-project-board.py's shape.
    post-tool-use.py's caller discards the second element: `item_id, _ = add_to_project(...)`.
    reconcile-project-board.py needs it to distinguish a transient failure from a missing
    `project`-OAuth-scope failure via `looks_like_scope_error(stderr)`.
  - `encoding="utf-8"` always (reconcile-project-board.py's original behavior, now shared).
    This is a DELIBERATE behavior change for post-tool-use.py's call site: it previously
    used no explicit encoding (OS default locale — cp1252 on Windows), which could raise
    `UnicodeDecodeError` on a non-ASCII issue title, silently swallowed by its old bare
    `except Exception: return None`. `gh` emits UTF-8 JSON, so this is a correctness fix,
    not a risky change.
  - `timeout` is keyword-only, defaulting to 20 (post-tool-use.py's original — tighter
    because it runs inside a live interactive-session hook). reconcile-project-board.py's
    call site passes `timeout=30` explicitly to preserve its own original, looser
    (unattended-nightly-batch) timeout exactly.
  - `str(project_number)` normalizes the arg before it reaches the subprocess call —
    reconcile-project-board.py's original already did this; post-tool-use.py's original
    passed `config["project_number"]` straight through. A no-op today (the JSON config
    schema always stores it as a string already), carried over from reconcile's contract
    for completeness (/review, dev-env#454).

Imports `_winsubp` itself even though the entry-point script will already have — the patch
is a one-time, idempotent mutation of `subprocess.Popen.__init__` guarded on the
`subprocess` module object itself (not per-importing-module), so a second import is a true
no-op. Self-contained defensiveness per `_winsubp.py`'s own instruction to place the import
"near the top of any hook script that spawns subprocesses," removing any cross-module
import-order assumption.

Not unit-tested: the `subprocess.run` call is a live `gh` network boundary, matching this
repo's no-subprocess-mock convention — neither original `add_to_project` was tested either
(see reconcile-project-board.py's own former docstring note on this).

See ADR-073.
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import subprocess


def add_to_project(
    url: str, project_number: str, owner: str, *, timeout: int = 20
) -> tuple[str | None, str]:
    """Add `url` to the project; return (new item ID, stderr). item ID is None on
    failure; stderr is "" on success, else gh's stripped stderr or the caught
    exception's str(). Never raises."""
    try:
        result = subprocess.run(
            [
                "gh", "project", "item-add", str(project_number), "--owner", owner,
                "--url", url, "--format", "json",
            ],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
        if result.returncode != 0:
            return None, (result.stderr or "").strip()
        return json.loads(result.stdout).get("id"), ""
    except Exception as e:
        return None, str(e)
