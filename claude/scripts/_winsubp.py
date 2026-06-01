"""Suppress console-window flashes for subprocess spawns on Windows.

When Claude Code launches a hook via `pyw -3` (`pythonw.exe`, Windows
subsystem), the hook process itself has no console. If the hook then
spawns a console application (`git.exe`, `gh.exe`, `bash.exe`,
`py.exe`, ...) via `subprocess.run` / `subprocess.Popen`, Windows
allocates a fresh console window for that child — visible as a flash
on every spawn. ADR-007's 2026-06-01 decision eliminated the flash
from the launcher itself; this module eliminates it from the children.

The fix is a single Win32 flag: `CREATE_NO_WINDOW` (0x08000000) passed
in `creationflags`. See:
  https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW

This module, when imported, monkey-patches `subprocess.Popen.__init__`
to OR `CREATE_NO_WINDOW` into the `creationflags` keyword argument on
every call (including the ones `subprocess.run` makes internally).

Properties:
  - Idempotent — guarded by `subprocess._winsubp_patched`. Importing twice
    is harmless.
  - No-op on non-Windows (`os.name != 'nt'`).
  - Safe-fallback — any unexpected failure during patch installation is
    swallowed so a hook never crashes because of this module.
  - Caller-respecting — if the caller already passed `creationflags`, the
    flag is OR-merged, not overwritten. Callers that explicitly want a
    console (none currently exist among hooks) can still set
    `CREATE_NEW_CONSOLE` and both flags coexist.

Usage:
    import _winsubp  # noqa: F401  -- suppress console windows on Windows

Place the import near the top of any hook script that spawns
subprocesses, before any `import subprocess` use-site. (Because the patch
mutates the `subprocess.Popen` class, even later `import subprocess`
statements see the patched class.)

See ADR-007 (2026-06-01) for `pyw -3` launcher rationale and the
followup that motivated this module (dev-env#297).
"""

from __future__ import annotations

import os
import subprocess

try:
    if os.name == "nt" and not getattr(subprocess, "_winsubp_patched", False):
        # CREATE_NO_WINDOW — documented at:
        # https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags
        _CREATE_NO_WINDOW = 0x08000000

        _orig_popen_init = subprocess.Popen.__init__

        def _patched_popen_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            existing = kwargs.get("creationflags", 0) or 0
            kwargs["creationflags"] = existing | _CREATE_NO_WINDOW
            _orig_popen_init(self, *args, **kwargs)

        subprocess.Popen.__init__ = _patched_popen_init  # type: ignore[method-assign]
        subprocess._winsubp_patched = True  # type: ignore[attr-defined]
except Exception:
    # Never let this module block a hook. If patching fails for any reason
    # (e.g., a future Python release changes Popen's signature shape), the
    # worst outcome is the original cosmetic flash returns.
    pass
