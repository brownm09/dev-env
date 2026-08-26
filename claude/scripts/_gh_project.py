#!/usr/bin/env python3
"""Shared `gh project item-add` wrapper (dev-env#454) plus a best-effort item-ID
cache (dev-env#1057).

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

--- Item-ID cache (dev-env#1057, ADR-141) ---

A newly-added item's ID is computed by `add_to_project` on every successful add but was,
before this cache, only ever printed by the caller (post-tool-use.py's stderr reminder) and
discarded — every later "resolve an item ID from an issue/PR number" lookup re-fetched the
*entire* board (`gh project item-list ... --limit 1000`, ~719 items as of dev-env#1057) just
to find the one item it already knew moments earlier.

`add_to_project` now best-effort-caches every item ID it successfully creates, keyed by the
issue/PR number parsed from the URL it was given. Because `add_to_project` is already the
single shared choke point for `gh project item-add` — used by both post-tool-use.py's
hook-triggered adds AND reconcile-project-board.py's orphan-add step — this one change covers
both creation paths with no signature change at either call site.

Cache format: a single flat JSON dict at `CACHE_PATH`, `"<owner>/<repo>#<number>": "<item-id>"`.
Not per-issue-sharded (unlike engineering-journal's tile shards) — this is small and
mostly-append, not a high-concurrency multi-writer system. A read-modify-write race between two
concurrent sessions can lose an entry; that's an accepted, self-healing trade-off (a lost write
is just a future cache miss, not data loss), the same trade-off `dev-env-sync.py`'s single
global scratch-state file already accepts.

Every cache function is best-effort and never raises, matching every other sentinel writer in
this repo (`_hookutil.record_heartbeat`, whose exact tmp-file + `os.replace` atomic-write idiom
this reuses) — a cache miss or a failed cache write must never be indistinguishable from, or
turn into, an `add_to_project` failure. `PROJECT_ITEM_CACHE_PATH_OVERRIDE`, checked at call
time (not import time), lets tests redirect every cache function to a throwaway path — mirrors
`_hookutil.py`'s `HOOK_HEARTBEAT_DIR_OVERRIDE` exactly, including the "checked per-call, not
just once at import" property that lets a test set it via `os.environ` after this module was
already imported.

Item IDs are immutable once assigned (unlike the `single_select` field *options* ADR-076
live-fetches, which really can change), so a cache entry is never stale — it can only be
missing, which safely degrades to the pre-existing full-fetch fallback each caller already had.

See ADR-073, ADR-141.
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
from pathlib import Path

# Literal Windows-style path (not "~/.claude/..." or an env-var-built path) so every
# consumer -- this module, get-project-item.sh's bash+node -- resolves the identical
# file. Matches the convention get-project-item.sh's own comment documents for the same
# reason (dev-env#334: Git Bash and Node-on-Windows resolve a leading /c/... differently).
CACHE_PATH = Path("C:/Users/brown/.claude/scratch/project-item-cache.json")

_ISSUE_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/(?:issues|pull)/(\d+)/?$")


def _cache_path(cache_path: Path | None = None) -> Path:
    """`cache_path` if given, else `CACHE_PATH` — but `PROJECT_ITEM_CACHE_PATH_OVERRIDE`
    (checked at call time, not import time, so a test can set it via `os.environ` even
    after this module is imported — see module docstring) wins over either, matching
    `_hookutil.record_heartbeat`'s override-beats-explicit-param precedence exactly."""
    override = os.environ.get("PROJECT_ITEM_CACHE_PATH_OVERRIDE")
    if override:
        return Path(override)
    return cache_path if cache_path is not None else CACHE_PATH


def _parse_issue_url(url: str) -> tuple[str, int] | None:
    """Pure parse of a `https://github.com/<owner>/<repo>/(issues|pull)/<number>` URL
    into (`"owner/repo"`, number). None on any non-match — not a github.com URL, wrong
    path shape, non-numeric trailing segment, etc. Never raises (a regex match can't)."""
    m = _ISSUE_URL_RE.match(url.strip())
    if not m:
        return None
    owner, repo, number = m.groups()
    return f"{owner}/{repo}", int(number)


def read_item_cache(cache_path: Path | None = None) -> dict[str, str]:
    """Best-effort read of the whole item-ID cache. `{}` on any failure — missing
    file, corrupt JSON, I/O error, or a JSON value that isn't an object. Never
    raises. `cache_path` is a test-only override (see `_cache_path`)."""
    try:
        data = json.loads(_cache_path(cache_path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_item_cache_entry(
    repo: str, number: int, item_id: str, cache_path: Path | None = None
) -> None:
    """Best-effort read-modify-write of one `f"{repo}#{number}"` -> `item_id` entry,
    via a per-process tmp file + `os.replace` atomic swap (matching
    `_hookutil.record_heartbeat`'s idiom exactly) — no locks, no subprocess. Never
    raises; a write failure here must never surface as (or be mistaken for) a failure
    of whatever caller just successfully created or fetched `item_id`. `cache_path`
    is a test-only override (see `_cache_path`)."""
    try:
        cache = read_item_cache(cache_path)
        cache[f"{repo}#{number}"] = item_id
        target = _cache_path(cache_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f"{target.name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        pass


def lookup_cached_item_id(repo: str, number: int, cache_path: Path | None = None) -> str | None:
    """Best-effort cache lookup. `None` on a miss OR any read failure — the two are
    indistinguishable by design, since both mean "fall back to a live fetch." Never
    raises. `cache_path` is a test-only override (see `_cache_path`)."""
    return read_item_cache(cache_path).get(f"{repo}#{number}")


def _cache_new_item(url: str, item_id: str) -> None:
    """Best-effort: parse `url` and cache `item_id` under it. Never raises — a
    failure here must never look like the `add_to_project` call itself failed, since
    by the time this runs the add has already succeeded."""
    try:
        parsed = _parse_issue_url(url)
        if parsed:
            write_item_cache_entry(parsed[0], parsed[1], item_id)
    except Exception:
        pass


def add_to_project(
    url: str, project_number: str, owner: str, *, timeout: int = 20
) -> tuple[str | None, str]:
    """Add `url` to the project; return (new item ID, stderr). item ID is None on
    failure; stderr is "" on success, else gh's stripped stderr or the caught
    exception's str(). Never raises.

    On success, also best-effort-caches the new item ID keyed by the issue/PR number
    parsed from `url` (dev-env#1057, ADR-141) — see module docstring. Purely
    additive: this function's return contract is byte-identical to before the cache
    existed, so neither existing call site (post-tool-use.py, reconcile-project-board.py)
    needs to change to benefit from it."""
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
        item_id = json.loads(result.stdout).get("id")
        if item_id:
            _cache_new_item(url, item_id)
        return item_id, ""
    except Exception as e:
        return None, str(e)
