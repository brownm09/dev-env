#!/usr/bin/env python3
"""Shared pure helpers for the journal-compose mechanical force-guard pair
(dev-env#631, ADR-094): `journal-compose-force-resolve.py` (writes the
marker) and `pre-tool-use-journal-compose-force-guard.py` (reads it to gate
same-day worktree-add / commit / push commands). A single module keeps the
marker path template and JSON schema from drifting between writer and
reader, mirroring this repo's other shared-module precedents (_journal_schema.py,
_hookio.py).

Design note -- why FORCE is resolved once and recorded, not re-derived by the
gate: the agent that constructs the resolve script's command line is handed
the literal, harness-substituted `$ARGUMENTS` text by the SKILL.md template
BEFORE it does any of its own reasoning about the task's intent (dev-env#631
-- an agent reasoned its way past a purely prose-level guard without ever
running or reading it). Recording that resolution in a file the *hook*
independently checks converts the guard from "prose the agent must choose to
obey" into "a fact the harness enforces regardless of what the agent decides
to believe about the task."

Both the writer and the reader compute "today" independently via their own
local clock (never trusting anything the other passes) and only ever agree
because they happen to run on the same real calendar day -- see each
script's own module docstring.
"""
import datetime
import json
import os
import re

MARKER_DIR_ENV = "JOURNAL_COMPOSE_FORCE_MARKER_DIR"
_DEFAULT_MARKER_DIR = "C:/Users/brown/.claude/scratch"

# Generous on purpose: a multi-project compose with Further-Reading subagent
# research (journal-compose SKILL.md Step 11) can genuinely run for hours.
# This bounds only truly ancient, crash-orphaned markers -- not realistic
# compose durations.
MAX_MARKER_AGE_SECONDS = 4 * 60 * 60

# Whitespace-bounded so a longer flag ("--force-push") or a substring inside
# prose never counts -- only a literal, standalone `--force` token does.
_FORCE_TOKEN_RE = re.compile(r"(?:^|\s)--force(?:\s|$)")


def resolve_force(raw_args):
    """True iff `raw_args` contains a literal, whitespace-bounded `--force`
    token. `raw_args` is expected to be the literal, harness-substituted
    `$ARGUMENTS` text of the `/journal-compose` invocation -- not something
    reconstructed or paraphrased by the agent.
    """
    return bool(_FORCE_TOKEN_RE.search(raw_args or ""))


def marker_dir():
    return os.environ.get(MARKER_DIR_ENV) or _DEFAULT_MARKER_DIR


def marker_path_for(date_str):
    """`date_str`: a 'YYYY-MM-DD' string. Both the writer (resolve script,
    keyed to *its own* `date.today()`) and the reader (guard hook, keyed to
    *its own* `date.today()`) call this independently -- they only ever
    agree on a path because the two processes happen to run on the same real
    calendar day, which is exactly the condition under which the guard needs
    to consult the marker at all.
    """
    return os.path.join(marker_dir(), f"journal-compose-force-{date_str}.json")


def build_marker(force, raw_args, now):
    """`now`: a `datetime.datetime` (caller's real clock, kept out of this
    pure function so tests can inject a fixed value)."""
    return {
        "force": bool(force),
        "raw_arguments": raw_args or "",
        "resolved_at": now.isoformat(),
    }


def write_marker(path, marker):
    """Atomic write (write-then-rename) so a reader never observes a
    partially-written marker. The temp filename includes this process's PID
    so two `journal-compose-force-resolve.py` invocations racing for the
    same date never share (and tear) the same temp file before the rename
    (review finding on PR #671) -- the atomic `os.replace` alone only
    protects a reader from a *single* writer's partial write, not two
    concurrent writers from colliding on one shared temp path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(marker, f)
    os.replace(tmp_path, path)


def read_marker(path):
    """Return the parsed marker dict, or None if absent/unreadable/malformed.

    The caller decides how to treat None -- the guard hook fails CLOSED on
    it, deliberately, unlike this repo's usual fail-open hook convention;
    see that hook's own module docstring for why.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_marker_fresh(marker, now, max_age_seconds=MAX_MARKER_AGE_SECONDS):
    """True iff `marker` has a parseable `resolved_at` timestamp within
    `max_age_seconds` of `now`. A marker timestamped in the future (e.g.
    clock skew, or a tampered file) is treated as not fresh rather than
    trusted blindly -- age must be non-negative too.

    `now` is always naive (both callers pass `datetime.datetime.now()`), but
    `resolved_at` is untrusted file content -- a tz-aware ISO string (e.g.
    "...+00:00", an ordinary `.isoformat()` shape, not an exotic input)
    parses successfully via `fromisoformat` and then raises `TypeError` on
    naive-minus-aware subtraction, which is NOT a `ValueError` and was
    previously uncaught here -- propagating out of the guard hook's `main()`
    as an unhandled exception, exiting non-zero-but-not-2, which Claude Code
    treats as a non-blocking hook error and lets the gated command PROCEED.
    That inverted this hook's central, deliberately-chosen fail-CLOSED
    guarantee for exactly the "corrupt marker" case it exists to cover
    (review finding on PR #671) -- so any exception during the age
    computation, not just a parse failure, must resolve to "not fresh."
    """
    if not isinstance(marker, dict):
        return False
    raw = marker.get("resolved_at")
    if not isinstance(raw, str):
        return False
    try:
        resolved_at = datetime.datetime.fromisoformat(raw)
        age = (now - resolved_at).total_seconds()
    except (ValueError, TypeError):
        return False
    return 0 <= age <= max_age_seconds
