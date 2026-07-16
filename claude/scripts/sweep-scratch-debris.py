#!/usr/bin/env python3
"""One-time / on-demand manual sweep of known sentinel and per-day marker
files in ~/.claude/scratch/ (dev-env#768).

Most sentinel writers now self-clean via _hookutil.cleanup_stale_sentinels()
(or an equivalent age-based cleanup) called from their own hook's main() --
but that cleanup only runs the next time each hook actually FIRES. A hook
that fires rarely (or that only started cleaning up as of this PR) can leave
a large backlog that won't be swept until its next natural invocation. This
script force-sweeps every KNOWN sentinel/marker family right now, without
waiting for each hook's own trigger. Verified scale at the 2026-07-10
hook-reliability assessment: ~5,687 sentinel files, ~285 MB, 771 files older
than 30 days -- journal_onboard_* alone accounted for 986 files.

KNOWN_PATTERNS is a deliberately explicit, hand-maintained list -- not a
blanket "any .flag file" sweep. A blanket sweep would also catch long-lived
singleton files that happen to share the .flag suffix by convention but are
NOT per-session debris. Update this list when a new per-session/per-day
sentinel family is added elsewhere in claude/scripts/.

Completeness of this list is NOT mechanically enforced -- there is no test
asserting every sentinel-writing prefix in claude/scripts/ has a
corresponding entry here (only the reverse: that dangerous filenames never
match an entry). A new hook that forgets to add itself here produces no test
failure; correctness relies on a human following the instruction above. This
is an accepted, bounded risk for a manual backlog-clearing utility -- every
hook already self-cleans on its own next invocation regardless of whether
this registry knows about it, so an omission here only delays a one-time
force-clear, never leaves debris permanently unswept.

Deliberately excluded (do not add to KNOWN_PATTERNS without re-reading this
docstring first):
  - awake.lock / awake.pid / awake.log[.1]   -- singleton state for a live
    background process (awake-blocker.py), not per-session debris. Sweeping
    awake.lock out from under a running watcher would corrupt its liveness
    tracking.
  - hook-heartbeat/*.ts                      -- one per HOOK (not per
    session), a small fixed count, actively read by hook-liveness-check.py
    for staleness detection. Sweeping these would defeat that monitor.
  - token-sessions.jsonl, session-mode-prompt.log -- single append-only
    logs, not per-session files; a log-rotation concern, not this script's.
  - baseline_<repo>_<branch>.json (baseline-tests.sh) -- scoped to a
    BRANCH's lifetime, not a session or calendar day. A long-lived branch's
    baseline can legitimately be older than any fixed age cutoff; sweeping
    it by age would silently break the ADR-030 fix-on-touch policy for that
    branch. Cleaned up by branch-existence (not age) instead: `baseline-tests
    gc` -- run automatically at the end of `baseline-tests snapshot`, and
    invocable on its own -- removes a repo's own baseline files whose branch
    no longer exists locally or on origin (dev-env#778).

Usage:
    py -3 claude/scripts/sweep-scratch-debris.py               # dry run (default)
    py -3 claude/scripts/sweep-scratch-debris.py --apply       # actually delete
    py -3 claude/scripts/sweep-scratch-debris.py --apply --max-age-days 14
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import _hookutil

# (prefix, extension) pairs -- see the module docstring for what's
# deliberately excluded and why. Ordered by contributor size at the
# 2026-07-10 assessment, largest first. "self-cleans" entries already call
# an age-based cleanup from their own hook's main() -- included here only to
# force-clear any backlog predating that cleanup (or this PR).
KNOWN_PATTERNS: list[tuple[str, str]] = [
    ("journal_onboard_", ".flag"),               # journal-onboard-check.py (986 files at assessment)
    ("session_mode_ack_", ".txt"),                # session-mode-prompt.py
    ("disk_space_check_", ".flag"),               # disk-space-check.py
    ("turn-count-", ".txt"),                      # turn-count-hook.py (self-cleans; backlog only)
    ("ctx-warn-", ".txt"),                        # turn-count-hook.py (self-cleans; backlog only)
    ("bash_state_", ".json"),                     # _bash_state.py (self-cleans; backlog only)
    ("journal_hook_", ".flag"),                   # new-day-journal-check.py (self-cleans; backlog only)
    ("hook_liveness_check_", ".flag"),             # hook-liveness-check.py (self-cleans; backlog only)
    ("tile-enumeration-gate-", ".flag"),           # stop-tile-enumeration-gate.py (self-cleans; backlog only)
    ("posttooluse-inert-resolved-", ".flag"),      # posttooluse-inert-advisory.py (self-cleans; backlog only)
    ("open-prs-reconciled-", ".flag"),             # reconcile-open-prs.py (self-cleans; backlog only)
    ("journal-stub-checkpoint-", ".flag"),         # stop-journal-stub-checkpoint.py (self-cleans; backlog only)
    ("token-tracker-locate-fail-", ".flag"),       # token-tracker.py (self-cleans; backlog only)
    ("journal-compose-force-", ".json"),           # journal-compose-force-resolve.py
]


def find_stale(prefix: str, ext: str, scratch: Path, max_age_days: int) -> list[Path]:
    """Return files matching {prefix}*{ext} in scratch older than max_age_days.
    Mirrors _hookutil.cleanup_stale_sentinels's own matching logic, but
    separates discovery from deletion so callers can report before acting."""
    cutoff = time.time() - max_age_days * 86400
    try:
        candidates = list(scratch.glob(f"{prefix}*{ext}"))
    except Exception:
        return []
    stale = []
    for f in candidates:
        try:
            if f.stat().st_mtime < cutoff:
                stale.append(f)
        except Exception:
            continue
    return stale


def sweep(scratch: Path, max_age_days: int, apply: bool) -> dict[str, tuple[int, int]]:
    """Sweep every KNOWN_PATTERNS family. Returns {pattern: (count, bytes)}.

    In dry-run mode (apply=False), (count, bytes) is every stale file matched
    -- nothing is deleted. In apply mode, (count, bytes) is only files
    actually confirmed gone afterward: a genuine unlink failure (permission,
    a locked handle) is NOT counted, so the report never overstates what was
    actually cleared (a file already removed by a race with the owning
    hook's own cleanup still counts -- the goal, an absent stale file, was
    met regardless of who removed it). Each family is guarded independently
    so one family's I/O error never aborts the rest."""
    results: dict[str, tuple[int, int]] = {}
    for prefix, ext in KNOWN_PATTERNS:
        stale = find_stale(prefix, ext, scratch, max_age_days)
        count = 0
        size = 0
        for f in stale:
            try:
                file_size = f.stat().st_size
            except OSError:
                file_size = 0
            if apply:
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass  # already gone (e.g. a race with the hook's own cleanup) -- goal met
                except Exception:
                    continue  # genuine failure -- do not count as removed
            count += 1
            size += file_size
        results[f"{prefix}*{ext}"] = (count, size)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete (default is dry-run)")
    parser.add_argument(
        "--max-age-days", type=int, default=_hookutil.MAX_AGE_DAYS,
        help=f"Age threshold in days (default {_hookutil.MAX_AGE_DAYS}, matching _hookutil.MAX_AGE_DAYS)",
    )
    args = parser.parse_args()

    results = sweep(_hookutil.SCRATCH, args.max_age_days, args.apply)
    total_count = sum(count for count, _ in results.values())
    total_bytes = sum(size for _, size in results.values())

    verb = "Removed" if args.apply else "Would remove (dry run -- pass --apply to delete)"
    for pattern, (count, size) in results.items():
        if count:
            print(f"{verb} {count:>5} file(s) ({size:>12,} bytes) matching {pattern}")
    print(f"\n{verb} {total_count} file(s) totaling {total_bytes:,} bytes, older than {args.max_age_days} days.")
    if not args.apply and total_count:
        print("Re-run with --apply to actually delete.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"sweep-scratch-debris: error: {e}", file=sys.stderr)
        sys.exit(1)
