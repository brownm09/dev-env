"""Keep test-driven hook fires out of the production heartbeat ledger (dev-env#1163).

Every wired hook's ``main()`` calls ``_hookutil.record_heartbeat()`` as its first
statement (ADR-106), which writes ``~/.claude/scratch/hook-heartbeat/<hook>.ts``
and, since dev-env#848, appends a timing line to ``<hook>.dur.jsonl``. A test that
runs a real hook -- as a subprocess, or by calling its ``main()`` in-process --
writes there too unless ``HOOK_HEARTBEAT_DIR_OVERRIDE`` points somewhere else.
That refreshes a hook's liveness heartbeat (masking a hook that has stopped
firing in production for up to the 7-day cadence) and mixes test fires into the
timing data.

Call ``isolate_heartbeat()`` once at module level in any test file that runs a
real hook. It points the override at a fresh temp directory for the rest of the
test process, so in-process ``main()`` calls and every subprocess that inherits
``os.environ`` (``env=None``, or ``env=dict(os.environ, ...)``) are covered. A
subprocess built from a hand-assembled environment uses ``child_env()`` instead.

Process-wide, not suite-wide: ``run-hook-tests.py`` runs each test file as its
own process, and must not set the override itself, because ``test_hookutil.py``'s
precedence tests depend on it being unset. ``test_hook_heartbeat_isolation.py``
is the structural gate that finds a hook-driving test file without this call.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

ENV_VAR = "HOOK_HEARTBEAT_DIR_OVERRIDE"


def isolate_heartbeat() -> str:
    """Point ``HOOK_HEARTBEAT_DIR_OVERRIDE`` at a temp directory for this process.

    Idempotent: a value already set (by an earlier call, or by the caller's own
    environment) is kept. Returns the directory in effect.
    """
    existing = os.environ.get(ENV_VAR)
    if existing:
        return existing
    path = tempfile.mkdtemp(prefix="dev_env_test_heartbeat_")
    os.environ[ENV_VAR] = path
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def child_env(base: dict | None = None, **overrides: str) -> dict:
    """Return a copy of *base* (default ``os.environ``) with the override set,
    plus any *overrides*, for a subprocess that does not inherit ``os.environ``."""
    env = dict(os.environ if base is None else base)
    env[ENV_VAR] = isolate_heartbeat()
    env.update(overrides)
    return env
