#!/usr/bin/env python3
"""On-demand replay of `pre-tool-use-scratch-rm-allow.py` (ADR-147) over
recorded session transcripts: what would the hook approve?

ADR-147 section 6 calibrated the hook against real traffic -- 10,312 unique
Bash commands, 158 containing both `rm` and "scratch", 0 approved -- and set
the success signal for later: re-run the replay once the "clean up scratch in
its own call" guidance has been live a while, expect a non-zero approval
count, and spot-check every approval. This script is that replay, committed
so the measurement is reproducible (PR #1135 review finding 3). It runs the
hook's own `check_command` in-process -- the exact function `decide` calls --
so it always measures the CURRENT hook code against the SAME corpus, and
doubles as a regression instrument after any change to the lexer.

What it reports:

  CORPUS      -- transcripts read, Bash commands seen, unique Bash commands.
  CANDIDATES  -- unique commands with an `rm` word and "scratch" in them:
                 the population the hook exists for.
  APPROVED    -- unique commands the hook would approve, each listed (with
                 its resolved targets) for a human to spot-check. An approval
                 outside the candidate set is counted separately; it is not
                 necessarily wrong (e.g. a scratch override in effect), but
                 it deserves a look.
  REJECTIONS  -- why the candidates were NOT approved, by the hook's own
                 Reject message. Shows which shapes sessions still write.

The hook's live approval log (`scratch-rm-allow.log` in scratch) records what
it actually approved from the moment it was deployed; this replay answers
the counterfactual for the whole history, including traffic from before the
hook existed.

Usage:
    py -3 claude/scripts/replay-scratch-rm-allow.py
    py -3 claude/scripts/replay-scratch-rm-allow.py --json
    py -3 claude/scripts/replay-scratch-rm-allow.py --width 400 --reasons 30
    py -3 claude/scripts/replay-scratch-rm-allow.py --scan-dir <dir>

Reads only; writes nothing (in-process `check_command` never logs -- only the
hook's `main()` does). Commands are TRUNCATED in all output: transcripts can
carry secrets, so read the output before pasting it anywhere public. `~` and
`$HOME` resolve against this process's environment, exactly as the hook's
would.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_SCAN_DIR = Path.home() / ".claude" / "projects"
HOOK_FILENAME = "pre-tool-use-scratch-rm-allow.py"

# An `rm` word at a command position or after a separator. Deliberately loose:
# it only selects the population to explain, never what gets approved.
_RM_WORD_RE = re.compile(r"(?:^|[\s;&|(`])rm(?=\s|$)")

# Cheap line prefilter -- transcripts are mostly prose, and json.loads on every
# line of a multi-GB corpus dominates runtime otherwise.
_INTERESTING = '"tool_use"'


def load_hook(scripts_dir=None):
    """Import the hook by path (its filename has hyphens)."""
    scripts_dir = Path(scripts_dir) if scripts_dir else SCRIPTS_DIR
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("scratch_rm_allow", scripts_dir / HOOK_FILENAME)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def truncate(text, limit=200):
    """One-line, length-capped rendering. Every command this tool prints goes
    through here, because transcripts can carry secrets."""
    flat = " ".join((text or "").split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


def is_candidate(cmd):
    """True for a command with an `rm` word and "scratch" in it."""
    return "scratch" in cmd.lower() and _RM_WORD_RE.search(cmd) is not None


def iter_bash_commands(scan_dir, stats=None):
    """Yield the command of every Bash tool_use in every transcript under
    *scan_dir*. Malformed lines and unreadable files are skipped. When
    *stats* (a Counter) is given, `transcripts` counts the files read."""
    for path in sorted(Path(scan_dir).rglob("*.jsonl")):
        if stats is not None:
            stats["transcripts"] += 1
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if _INTERESTING not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    message = record.get("message") if isinstance(record, dict) else None
                    content = message.get("content") if isinstance(message, dict) else None
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not (isinstance(block, dict) and block.get("type") == "tool_use"
                                and block.get("name") == "Bash"):
                            continue
                        payload = block.get("input")
                        if isinstance(payload, dict) and isinstance(payload.get("command"), str):
                            yield payload["command"]
        except Exception:
            continue


def analyze(hook, scan_dir, width=200):
    stats = Counter()
    reasons = Counter()
    approved = []
    seen = set()
    for cmd in iter_bash_commands(scan_dir, stats):
        stats["commands_total"] += 1
        if cmd in seen:
            continue
        seen.add(cmd)
        stats["commands_unique"] += 1
        candidate = is_candidate(cmd)
        if candidate:
            stats["candidates"] += 1
        try:
            targets = hook.check_command(cmd)
        except hook.Reject as exc:
            if candidate:
                reasons[str(exc)] += 1
            continue
        except Exception:
            stats["replay_errors"] += 1
            continue
        stats["approved"] += 1
        if not candidate:
            stats["approved_outside_candidates"] += 1
        approved.append({
            "command": truncate(cmd, width),
            "targets": list(targets),
            "candidate": candidate,
        })
    return {
        "scratch_root": hook.scratch_root(),
        "stats": dict(stats),
        "reject_reasons": dict(reasons.most_common()),
        "approved": approved,
    }


def format_report(report, max_reasons=15):
    stats = report["stats"]
    lines = ["=== replay-scratch-rm-allow (ADR-147) ===", ""]
    lines.append("Scratch root       : {}".format(report["scratch_root"]))
    lines.append("")
    lines.append("Corpus")
    lines.append("  transcripts        : {}".format(stats.get("transcripts", 0)))
    lines.append("  Bash commands seen : {}".format(stats.get("commands_total", 0)))
    lines.append("  unique commands    : {}".format(stats.get("commands_unique", 0)))
    if stats.get("replay_errors"):
        lines.append("  replay errors      : {}  (unexpected exceptions -- the hook fails open on these)".format(
            stats["replay_errors"]))
    lines.append("")
    lines.append("Would approve")
    lines.append("  rm+scratch candidates      : {}".format(stats.get("candidates", 0)))
    lines.append("  approved                   : {}".format(stats.get("approved", 0)))
    lines.append("  approved, not a candidate  : {}".format(stats.get("approved_outside_candidates", 0)))
    lines.append("")
    lines.append("Why candidates were not approved (top {})".format(max_reasons))
    if report["reject_reasons"]:
        for reason, count in list(report["reject_reasons"].items())[:max(0, max_reasons)]:
            lines.append("  {:>6}  {}".format(count, truncate(reason, 100)))
    else:
        lines.append("  none")
    lines.append("")
    lines.append("Approved commands -- spot-check every one (truncated)")
    if report["approved"]:
        for item in report["approved"]:
            flag = "" if item["candidate"] else "[not a candidate] "
            lines.append("  - {}{}".format(flag, item["command"]))
            lines.append("      targets: {}".format(", ".join(item["targets"])))
    else:
        lines.append("  none")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Replay the ADR-147 scratch-rm-allow hook over recorded session "
                    "transcripts and report what it would approve.")
    parser.add_argument("--scan-dir", default=str(DEFAULT_SCAN_DIR),
                        help="transcript root (default: ~/.claude/projects)")
    parser.add_argument("--width", type=int, default=200,
                        help="truncate each printed command to this many characters (default 200)")
    parser.add_argument("--reasons", type=int, default=15,
                        help="rejection reasons to list in the text report (default 15)")
    parser.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    parser.add_argument("--scripts-dir", default=None,
                        help="directory holding the hook (default: this script's own)")
    args = parser.parse_args(argv)

    scan_dir = Path(args.scan_dir)
    if not scan_dir.is_dir():
        print("no transcript directory at {} -- nothing to replay".format(scan_dir), file=sys.stderr)
        return 1
    hook = load_hook(args.scripts_dir)
    report = analyze(hook, scan_dir, width=max(1, args.width))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report, max_reasons=args.reasons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
