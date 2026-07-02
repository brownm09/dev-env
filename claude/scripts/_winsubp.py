"""Suppress console-window flashes for subprocess spawns on Windows, and
default text-mode subprocess output to UTF-8 decoding.

When Claude Code launches a hook via `pyw -3` (`pythonw.exe`, Windows
subsystem), the hook process itself has no console. If the hook then
spawns a console application (`git.exe`, `gh.exe`, `bash.exe`,
`py.exe`, ...) via `subprocess.run` / `subprocess.Popen`, Windows
allocates a fresh console window for that child — visible as a flash
on every spawn. ADR-007's 2026-06-01 decision eliminated the flash
from the launcher itself; this module eliminates it from the children.

The console-flash fix is a single Win32 flag: `CREATE_NO_WINDOW`
(0x08000000) passed in `creationflags`. See:
  https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW

Separately, a text-mode subprocess call (`text=True` / `universal_newlines=True`)
that does not pass `encoding=` decodes the child's stdout/stderr using
`locale.getpreferredencoding(False)` — the OS ANSI codepage, cp1252 on this
machine — instead of UTF-8. `gh`/`git` can emit UTF-8 multi-byte output (e.g.
em-dashes or curly quotes echoed back from a PR/issue title), and cp1252
cannot represent every UTF-8 byte sequence, so the read crashes with
`UnicodeDecodeError` (dev-env#503). This module defaults such calls to
`encoding="utf-8", errors="replace"` when the caller did not already specify
an encoding.

This module, when imported, monkey-patches `subprocess.Popen.__init__` to:
  1. OR `CREATE_NO_WINDOW` into the `creationflags` keyword argument on every
     call (including the ones `subprocess.run` makes internally).
  2. Default `encoding="utf-8", errors="replace"` when the call requests text
     mode (`text=True` or `universal_newlines=True`) and does not already
     specify `encoding`.

Properties:
  - Idempotent — guarded by `subprocess._winsubp_patched`. Importing twice
    is harmless.
  - No-op on non-Windows (`os.name != 'nt'`).
  - Safe-fallback — any unexpected failure during patch installation OR at
    Popen call time is swallowed so a hook never crashes because of this
    module. If the patch fails for any reason, the worst outcome is the
    original cosmetic flash / cp1252 decode behavior returns.
  - Caller-respecting — if the caller already passed `creationflags`, the
    flag is OR-merged, not overwritten. Callers that explicitly want a
    console (none currently exist among hooks) can still set
    `CREATE_NEW_CONSOLE` and both flags coexist. Likewise, an explicit
    `encoding=` (or `errors=`) from the caller is never overridden.

Assumptions:
  - Callers pass `creationflags`/`encoding`/`errors`/`text`/`universal_newlines`
    as keyword arguments, never positionally. `encoding`, `errors`, and `text`
    are keyword-only on `subprocess.Popen.__init__` (they follow the bare `*`
    in its signature) in every Python version this repo targets, so this holds
    by construction for those three. `creationflags` is positional-capable
    (13th parameter); every hook in this repo uses kwargs, so the simple
    kwargs-only path is correct in practice, and the runtime `try/except`
    below covers the hypothetical regression.
  - The generic `(self, *args, **kwargs)` signature on the patched init
    intentionally shadows `Popen.__init__`'s detailed signature — IDE and
    type-checker parameter help should be consulted on `subprocess.Popen`
    directly.

Usage:
    import _winsubp  # noqa: F401  -- suppress console windows on Windows

Place the import near the top of any hook script that spawns
subprocesses, before any `import subprocess` use-site. (Because the patch
mutates the `subprocess.Popen` class, even later `import subprocess`
statements see the patched class.)

See ADR-007 (2026-06-01, amended 2026-07-02) for `pyw -3` launcher rationale
and the followups that motivated and extended this module (dev-env#297,
dev-env#503).
"""

from __future__ import annotations

import os
import subprocess

# CREATE_NO_WINDOW — documented at:
# https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags
_CREATE_NO_WINDOW = 0x08000000


def _apply_windows_subprocess_defaults(kwargs: dict) -> dict:
    """Mutate and return *kwargs* with the two Windows subprocess defaults
    applied: `CREATE_NO_WINDOW` OR-merged into `creationflags`, and
    `encoding="utf-8", errors="replace"` defaulted onto a text-mode call that
    doesn't already specify an encoding.

    Pure aside from the in-place `kwargs` mutation, and independent of
    `os.name` — the platform gate lives at the call site that wires this into
    `Popen.__init__` (below), not here — so it is safe and meaningful to unit
    test directly on any platform, matching this repo's pure-helper
    convention (dev-env#503, extending dev-env#297's original
    creationflags-only version).
    """
    try:
        existing = kwargs.get("creationflags", 0) or 0
        kwargs["creationflags"] = existing | _CREATE_NO_WINDOW
    except Exception:
        # Defensive — if anything goes wrong merging the flag (e.g.,
        # a future caller passes creationflags positionally and the
        # kwargs injection would collide), fall through unchanged. Cosmetic
        # flash returns; hook never crashes.
        pass
    try:
        # `errors=` alone (no text=/universal_newlines=) also puts Popen into
        # text mode -- confirmed empirically: Popen(cmd, stdout=PIPE,
        # errors="replace") returns str, not bytes. Without checking it here,
        # a caller that sets only errors= would still fall through to
        # CPython's own cp1252 default for encoding.
        wants_text = kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("errors")
        if wants_text and not kwargs.get("encoding"):
            kwargs["encoding"] = "utf-8"
            kwargs.setdefault("errors", "replace")
    except Exception:
        # Defensive — same rationale as above. Worst case, the pre-fix
        # cp1252 decode behavior returns; hook never crashes because of
        # this module itself.
        pass
    return kwargs


try:
    if os.name == "nt" and not getattr(subprocess, "_winsubp_patched", False):
        _orig_popen_init = subprocess.Popen.__init__

        def _patched_popen_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _apply_windows_subprocess_defaults(kwargs)
            _orig_popen_init(self, *args, **kwargs)

        subprocess.Popen.__init__ = _patched_popen_init  # type: ignore[method-assign]
        subprocess._winsubp_patched = True  # type: ignore[attr-defined]
except Exception:
    # Never let this module block a hook. If patching fails for any reason
    # (e.g., a future Python release changes Popen's signature shape), the
    # worst outcome is the original cosmetic flash / cp1252 decode returns.
    pass
