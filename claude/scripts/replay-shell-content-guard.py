#!/usr/bin/env python3
"""On-demand reader that replays `pre-tool-use-shell-content-write-guard.py`
(ADR-138) over recorded session transcripts and reports what it would block.

This is ADR-138 Amendment 1's answer to dev-env#1046 item 1 -- "the guard
leaves no record of what it blocks." The finding that shaped it: THE RECORD
ALREADY EXISTS. Every Bash/PowerShell call Claude Code makes is written to a
session transcript under ~/.claude/projects/<project>/<session>.jsonl,
together with its tool_result. That is strictly MORE than a hook-side block
log could hold -- a block log records only that a block happened; the
transcript records the command, whether it actually failed, and what was done
next -- so the missing half was never the record, only a reader for it.

Why a reader and not an append-only log in the hook (the shape #1046
suggested, weighed and declined -- see ADR-138 Amendment 1):

  - A forward log answers nothing until months of traffic accumulate. This
    reader answered the same question immediately over 54,101 commands.
  - A log can only ever describe the hook version that was live when each
    line was written. A replay re-runs the CURRENT code over the SAME corpus,
    so it doubles as a regression instrument: change a detector, re-run, see
    exactly which real commands changed classification.
  - A log records blocks; it cannot record whether a command that was NOT
    blocked went on to fail. The enrichment ratio below -- the one number
    that actually distinguishes "targeting the right population" from
    "over-matching" -- needs both arms, so it is only computable here.

Metrics, and how to read them:

  BLOCK RATE      -- share of unique commands the guard would block. Volume,
                     not correctness: a high rate is only bad if the blocks
                     are wrong.
  SHELL-FAIL RATE -- share of commands whose tool_result carries a shell
                     PARSE failure (`unexpected EOF while looking for
                     matching`, `syntax error near unexpected token`, ...) --
                     i.e. the exact harm ADR-138 exists to prevent, observed.
  ENRICHMENT      -- blocked-population shell-fail rate / baseline rate. THE
                     headline number. >1 means the guard concentrates real
                     failures; ~1 means it is firing on ordinary traffic and
                     is over-matching. Measured 12x at Amendment 1.
  OVERRIDES       -- commands carrying an override token: a human explicitly
                     disagreeing with a block. The strongest false-positive
                     signal available, and it needs no new instrumentation.

Instrument-calibration note (learned the hard way while producing Amendment
1's numbers, and the reason SHELL_FAIL_RE is as narrow as it is): an earlier
signature set also counted `IndentationError` and `SyntaxError: invalid
syntax`. For a `py -3 - <<'PY'` heredoc those are almost always the AUTHOR's
own Python bug, not the shell mangling the body -- the heredoc delivers it
verbatim. Counting them inflated the interpreter-stdin failure rate from
1.66% to 10.2% and pointed at the opposite conclusion. Only errors the SHELL
itself emits are counted here. If you widen this pattern, re-derive the
baseline in the same run rather than comparing against a number in a doc.

Usage:
    py -3 claude/scripts/replay-shell-content-guard.py
    py -3 claude/scripts/replay-shell-content-guard.py --gap
    py -3 claude/scripts/replay-shell-content-guard.py --samples 10
    py -3 claude/scripts/replay-shell-content-guard.py --json
    py -3 claude/scripts/replay-shell-content-guard.py --scan-dir <dir>

Reads only; writes nothing and mutates nothing. Commands are TRUNCATED in all
output -- transcripts can carry secrets, so nothing here should be pasted into
a public issue without reading it first.
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
GUARD_FILENAME = "pre-tool-use-shell-content-write-guard.py"

# Only failures the SHELL itself emits -- see the calibration note above.
SHELL_FAIL_RE = re.compile(
    r"(unexpected EOF while looking for matching"
    r"|syntax error near unexpected token"
    r"|syntax error: unexpected end of file"
    r"|unterminated quoted string)",
    re.IGNORECASE,
)

OVERRIDE_TOKENS = ("ALLOW_SHELL_CONTENT_WRITE", "ALLOW_JOURNAL_SHELL_WRITE")

# Cheap line prefilter -- a transcript is mostly prose, and json.loads on every
# line of a multi-GB corpus dominates runtime otherwise.
_INTERESTING = ('"tool_use"', '"tool_result"')


def load_guard(scripts_dir=None):
    """Import the guard module by path (its filename has hyphens, so it is not
    importable as a normal module name)."""
    scripts_dir = Path(scripts_dir) if scripts_dir else SCRIPTS_DIR
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "shell_content_write_guard", scripts_dir / GUARD_FILENAME)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def truncate(text, limit=160):
    """One-line, length-capped rendering. Transcripts can carry secrets, so
    every command this tool prints goes through here."""
    flat = " ".join((text or "").split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


def result_text(block):
    """Flatten a tool_result's content to searchable text."""
    content = block.get("content")
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def live_matches(guard, cmd, tool_name):
    """Exactly what the hook's main() would block on, minus the I/O."""
    if not guard.might_write_content(cmd):
        return []
    segments = guard.segments_or_whole(cmd)
    matches = guard.find_content_writes(cmd, tool_name, segments=segments)
    return [m for m in matches
            if not guard._is_overridden(segments, m["segment_index"])]


def gap_heredocs(guard, cmd, tool_name):
    """Heredoc bodies with a hazard that reach NO file destination and NO
    content argument -- i.e. what remains in ADR-138's accepted gap after
    Amendment 1. Overwhelmingly an interpreter reading a program from stdin."""
    out = []
    try:
        segments = guard.segments_or_whole(cmd)
    except Exception:
        return out
    for seg in segments:
        try:
            found = guard.extract_heredoc_literal(seg)
        except Exception:
            continue
        if not found:
            continue
        line = guard.first_line(seg)
        masked = guard.mask_first_line_quotes(line)
        if guard.find_write_destinations(line, masked, tool_name):
            continue
        if guard.find_stdin_content_arg(line, masked):
            continue
        reason = guard.body_hazard(found[1])
        if reason:
            out.append((reason, seg))
    return out


def iter_commands(scan_dir):
    """Yield (tool_name, command, result_text) for every Bash/PowerShell call
    in every transcript under *scan_dir*, pairing each tool_use with its
    tool_result. Malformed lines and unreadable files are skipped."""
    for path in sorted(Path(scan_dir).rglob("*.jsonl")):
        pending = {}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if not any(marker in line for marker in _INTERESTING):
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    message = record.get("message") or {}
                    content = message.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        kind = block.get("type")
                        if kind == "tool_use" and block.get("name") in ("Bash", "PowerShell"):
                            payload = block.get("input")
                            if isinstance(payload, dict) and isinstance(
                                    payload.get("command"), str):
                                pending[block.get("id")] = (
                                    block["name"], payload["command"])
                        elif kind == "tool_result":
                            found = pending.pop(block.get("tool_use_id"), None)
                            if found:
                                yield found[0], found[1], result_text(block)
        except Exception:
            continue
        # Calls whose result never came back (session ended mid-turn) still count.
        for tool_name, cmd in pending.values():
            yield tool_name, cmd, ""


def analyze(guard, scan_dir, sample_limit=5, include_gap=False):
    stats = Counter()
    mechanisms = Counter()
    reasons = Counter()
    overrides = Counter()
    samples = {}
    gap_samples = []
    seen = set()

    for tool_name, cmd, output in iter_commands(scan_dir):
        stats["commands_total"] += 1
        key = (tool_name, cmd)
        if key in seen:
            continue
        seen.add(key)
        stats["commands_unique"] += 1

        shell_failed = bool(SHELL_FAIL_RE.search(output))
        if shell_failed:
            stats["shellfail_all"] += 1

        for token in OVERRIDE_TOKENS:
            if token in cmd:
                overrides[token] += 1

        try:
            matches = live_matches(guard, cmd, tool_name)
        except Exception:
            stats["replay_errors"] += 1
            continue

        if matches:
            match = matches[0]
            stats["blocked"] += 1
            mechanisms[match["mechanism"]] += 1
            reasons[match["reason"]] += 1
            if shell_failed:
                stats["blocked_shellfail"] += 1
            bucket = samples.setdefault(match["mechanism"], [])
            if len(bucket) < sample_limit:
                bucket.append({
                    "reason": match["reason"],
                    "detail": match["detail"],
                    "tool": tool_name,
                    "failed": shell_failed,
                    "command": truncate(cmd),
                })

        if include_gap:
            for reason, seg in gap_heredocs(guard, cmd, tool_name):
                stats["gap_total"] += 1
                if shell_failed:
                    stats["gap_shellfail"] += 1
                if len(gap_samples) < sample_limit * 3:
                    gap_samples.append({
                        "reason": reason,
                        "failed": shell_failed,
                        "command": truncate(seg),
                    })
                break

    return {
        "stats": dict(stats),
        "mechanisms": dict(mechanisms.most_common()),
        "reasons": dict(reasons.most_common()),
        "overrides": dict(overrides),
        "samples": samples,
        "gap_samples": gap_samples,
    }


def _pct(num, den):
    return 0.0 if not den else 100.0 * num / den


def format_report(report, include_gap=False):
    stats = report["stats"]
    unique = stats.get("commands_unique", 0)
    blocked = stats.get("blocked", 0)
    baseline = _pct(stats.get("shellfail_all", 0), unique)
    blocked_rate = _pct(stats.get("blocked_shellfail", 0), blocked)

    lines = []
    lines.append("=== replay-shell-content-guard (ADR-138) ===")
    lines.append("")
    lines.append("Corpus")
    lines.append("  commands seen      : {}".format(stats.get("commands_total", 0)))
    lines.append("  unique commands    : {}".format(unique))
    if stats.get("replay_errors"):
        lines.append("  replay errors      : {}".format(stats["replay_errors"]))
    lines.append("")
    lines.append("Would block")
    lines.append("  blocked            : {} ({:.2f}% of unique)".format(
        blocked, _pct(blocked, unique)))
    for mech, count in report["mechanisms"].items():
        lines.append("    {:<20} {}".format(mech, count))
    if report["reasons"]:
        lines.append("  by hazard reason")
        for reason, count in report["reasons"].items():
            lines.append("    {:<30} {}".format(reason, count))
    lines.append("")
    lines.append("Is it targeting the right population?")
    lines.append("  shell-fail, all commands   : {:.2f}%  ({}/{})".format(
        baseline, stats.get("shellfail_all", 0), unique))
    lines.append("  shell-fail, blocked set    : {:.2f}%  ({}/{})".format(
        blocked_rate, stats.get("blocked_shellfail", 0), blocked))
    if baseline > 0:
        lines.append("  ENRICHMENT                 : {:.1f}x".format(
            blocked_rate / baseline))
        lines.append("    (>1 concentrates real shell-parse failures; ~1 = over-matching)")
    else:
        lines.append("  ENRICHMENT                 : n/a (no baseline failures observed)")
    lines.append("")
    lines.append("Human disagreement (override tokens used)")
    if report["overrides"]:
        for token, count in sorted(report["overrides"].items()):
            lines.append("  {:<28} {}".format(token, count))
    else:
        lines.append("  none observed")

    if include_gap:
        gap_total = stats.get("gap_total", 0)
        lines.append("")
        lines.append("Accepted gap still open (heredoc -> interpreter stdin)")
        lines.append("  commands           : {}".format(gap_total))
        lines.append("  shell-fail rate    : {:.2f}%  ({}/{})".format(
            _pct(stats.get("gap_shellfail", 0), gap_total),
            stats.get("gap_shellfail", 0), gap_total))

    for mech, bucket in report["samples"].items():
        lines.append("")
        lines.append("Samples -- {} (truncated)".format(mech))
        for item in bucket:
            lines.append("  [{}] {}{}".format(
                item["reason"], "FAILED " if item["failed"] else "", item["command"]))

    if include_gap and report["gap_samples"]:
        lines.append("")
        lines.append("Samples -- accepted gap (truncated)")
        for item in report["gap_samples"]:
            lines.append("  [{}] {}{}".format(
                item["reason"], "FAILED " if item["failed"] else "", item["command"]))

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Replay the ADR-138 shell-content-write guard over recorded "
                    "session transcripts and report what it would block.")
    parser.add_argument("--scan-dir", default=str(DEFAULT_SCAN_DIR),
                        help="transcript root (default: ~/.claude/projects)")
    parser.add_argument("--samples", type=int, default=5,
                        help="truncated samples per mechanism (default 5)")
    parser.add_argument("--gap", action="store_true",
                        help="also size ADR-138's remaining accepted gap")
    parser.add_argument("--json", action="store_true",
                        help="emit the raw report as JSON")
    parser.add_argument("--scripts-dir", default=None,
                        help="directory holding the guard (default: this script's own)")
    args = parser.parse_args(argv)

    scan_dir = Path(args.scan_dir)
    if not scan_dir.is_dir():
        print("no transcript directory at {} -- nothing to replay".format(scan_dir),
              file=sys.stderr)
        return 1

    guard = load_guard(args.scripts_dir)
    report = analyze(guard, scan_dir, sample_limit=max(0, args.samples),
                     include_gap=args.gap)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report, include_gap=args.gap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
