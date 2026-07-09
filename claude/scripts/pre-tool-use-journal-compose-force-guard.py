#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- mechanical enforcement of journal-compose's
today-guard (dev-env#631, ADR-094).

Problem: /journal-compose's today-guard (ADR-017) existed only as prose in
SKILL.md ("if the resolved date equals today and FORCE is false, stop") --
nothing mechanically enforced it. A transcript (dev-env#631) confirms an
autonomous agent explicitly noticed the guard should apply, reasoned that
the task's framing implied FORCE=true anyway, and proceeded -- without the
guard's own refusal text ever actually being emitted or read. This hook
closes that gap the same way pre-merge-findings-gate.py and
pre-tool-use-canonical-mutate-guard.py close analogous prose-only-guard gaps
elsewhere in this repo: convert "an agent must choose to obey a written
rule" into "the harness blocks the action regardless of what the agent
believes."

How it decides:
  1. Read stdin JSON. Fail OPEN (exit 0) on anything unparseable, a missing
     `command`, or a non-Bash `tool_name` -- these are payload-shape issues
     unrelated to the guard itself; blocking blind on an unparseable command
     would not even correctly target the same-day compose case this hook
     exists for.
  2. Compute TODAY from this process's own local clock
     (`datetime.date.today()`) -- never from anything the command or a
     marker file claims.
  3. Segment the command via the shared `_hookio.split_top_level` engine
     (quote/subshell/heredoc-aware -- the same dev-env#499 lesson every
     sibling merge-hook already observes) and classify each segment: is it a
     `git` invocation (optionally prefixed by `-C <dir>` /
     `--git-dir[=]<dir>` / `--work-tree[=]<dir>`) whose subcommand is
     `worktree` (loosely matched -- `worktree add` is the only shape
     SKILL.md ever emits for a same-day target, but `remove`/`prune` also
     legitimately touch the same worktree during cleanup, and gating those
     too is harmless once the marker exists), `commit`, or `push`? If so,
     does ANY of its tokens -- excluding the VALUE of a
     `-m`/`--message`/`-F`/`--file` flag, so a commit message merely
     mentioning "draft/2026-07-09" or "compose-2026-07-09" as prose can
     never trigger this -- contain a `draft/<TODAY>` or `compose[-/]<TODAY>`
     substring?
  4. If no segment matches, exit 0 -- nothing here touches a same-day
     journal-compose target; every other git/gh command on the machine, and
     every past-day compose (the nightly routine's normal path, ADR-084), is
     completely untouched by this hook.
  5. If a segment matches, read today's marker
     (`_journal_compose_force.marker_path_for(TODAY)`, written only by
     `journal-compose-force-resolve.py`). Require it to be present, valid,
     `force == True`, and fresh (`is_marker_fresh`). Any failure of that
     chain -- missing, corrupt, `force == False`, or stale -- BLOCKS
     (exit 2). This is a deliberate reversal of this repo's usual
     fail-OPEN-on-hook-error convention (see e.g. pre-merge-findings-gate.py,
     pre-merge-message-check.py): those hooks gate extremely common,
     everyday operations (every merge, every push) where a hook bug wedging
     all of them is a large, costly blast radius. This hook only ever
     evaluates same-day compose mutations -- already meant to be rare per
     ADR-017 -- so failing closed on an unreadable/stale marker costs at
     most "re-run journal-compose-force-resolve.py and retry," never "no PR
     can ever merge."

No override token (unlike pre-tool-use-canonical-mutate-guard.py's
ALLOW_CANONICAL_MUTATE=1). Deliberate: the only legitimate way to satisfy
this guard is to have genuinely passed `--force` to `/journal-compose`,
which already produces a fresh, valid marker via the normal path. An
override token here would just be a second, easier route to reason past --
the exact vulnerability dev-env#631 is about. Recovery from a stale/missing
marker (e.g. a compose that ran long enough to outlive
MAX_MARKER_AGE_SECONDS) is to re-run `journal-compose-force-resolve.py`
with the SAME literal `$ARGUMENTS` text used at Step 0.6 -- never with a
hand-typed `--force` unless the user has explicitly said so earlier in the
current conversation.

Stdin JSON shape (PreToolUse): {"tool_name":"Bash","tool_input":{"command":...},"cwd":...}

Exit 2 -- block (same-day compose mutation, no valid fresh force=true marker).
Exit 0 -- allow (not a same-day compose mutation, or a valid marker exists).
"""
import datetime
import json
import re
import shlex
import sys

from _hookio import split_top_level
from _journal_compose_force import is_marker_fresh, marker_path_for, read_marker

_GIT_INVOCATION_RE = re.compile(r"^git(?:\.exe)?\s+(.*)$", re.IGNORECASE)
_LEADING_ENV = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*")
_PREFIX_VALUE_FLAGS = {"-C", "--git-dir", "--work-tree"}
_PREFIX_EQ_FLAGS = ("--git-dir=", "--work-tree=")
# Git-level flags that take a value with no scan relevance of their own (a
# `-c core.hooksPath=x` config override never legitimately encodes a
# draft/compose date) -- unlike _PREFIX_VALUE_FLAGS, the value is discarded
# outright, not kept as a scan candidate. Review finding on PR #671: without
# this, `-c <value>` fell through to the "not a flag we know" branch, its
# value token was then mistaken for the verb (nothing else claimed it), and
# a same-day `git -c core.hooksPath=x -C <compose-worktree> commit -m "..."`
# silently bypassed the gate entirely -- verb resolved to the config value,
# never matched _GATED_VERBS.
_SKIP_VALUE_FLAGS = {"-c"}
_MESSAGE_VALUE_FLAGS = {"-m", "--message", "-F", "--file"}
_MESSAGE_EQ_FLAGS = ("--message=", "--file=")
# Review finding on PR #671: the exact-match/prefix checks above only cover
# `-m <value>` and `-F <value>` as SEPARATE tokens. Two other ordinary git
# invocation shapes glue the value on instead and fell through to
# scan_tokens uninspected: `-m<value>`/`-F<value>` (no space -- e.g.
# `git commit -m"draft/2026-07-09 fix"`) and a combined short-option cluster
# like `-am <value>` (`-a` + `-m` combined, an extremely common idiom for
# `git commit -am "..."`) where the value is still its own following token.
# Both let a commit message merely mentioning today's date as prose
# false-trigger the guard -- confirmed via test_commit_glued_message_flag_*
# and test_commit_combined_short_flag_message_no_match.
_MESSAGE_GLUED_RE = re.compile(r"^-[mF](.+)$")
_MESSAGE_CLUSTER_RE = re.compile(r"^-[A-Za-z]*[mF]$")
_GATED_VERBS = {"worktree", "commit", "push"}
# Matches draft/<date> or compose-<date> / compose/<date> anywhere within a
# single already-tokenized argument (e.g. a -C path, a ref spec) -- not a
# raw substring search over free text, since scan_tokens already excludes
# message-flag values (see _verb_and_scan_tokens).
_DATE_TOKEN_RE = re.compile(r"(?:draft/|compose[-/])(\d{4}-\d{2}-\d{2})")


def _strip_leading_env(segment: str) -> str:
    return _LEADING_ENV.sub("", segment, count=1)


def _first_line(segment: str) -> str:
    """Only the segment's own first physical line ever carries real
    invocation syntax -- `split_top_level` can return a segment spanning
    multiple physical lines when a heredoc/`$()` span is part of it, and
    everything after the first line is body data, never additional flags.
    Mirrors the identically-purposed, independently-defined helper of the
    same name in pre-tool-use-canonical-mutate-guard.py and _hookio.py (each
    module stays self-contained; see those files' own docstrings for why).
    """
    return segment.split("\n", 1)[0]


def _tokenize(rest: str) -> list:
    try:
        return shlex.split(rest, posix=True)
    except ValueError:
        return rest.split()


def _git_rest_tokens(segment: str):
    stripped = _strip_leading_env(segment).strip()
    m = _GIT_INVOCATION_RE.match(_first_line(stripped))
    if not m:
        return None
    rest = m.group(1).strip()
    if not rest:
        return None
    return _tokenize(rest)


def _verb_and_scan_tokens(tokens):
    """Return (verb, scan_tokens).

    `verb` is the first non-flag token found after skipping
    `-C`/`--git-dir`/`--work-tree` (space or `=` form) -- their VALUE is kept
    as a scan candidate, not discarded, since a `-C "$WT"`-scoped push
    (journal-compose SKILL.md Step 10.5's recovery push) carries the
    compose-dated worktree path there, not after the verb -- and skipping
    `-c <name>=<value>` (git's per-invocation config override, e.g.
    `-c core.hooksPath=x`), whose value is discarded outright rather than
    kept as a scan candidate, since a config override never legitimately
    encodes a draft/compose date and treating it as an unrecognized flag
    let its value get mistaken for the verb itself (review finding on
    PR #671).

    `scan_tokens` is every remaining token EXCEPT a commit message's value in
    any of its ordinary shapes: `-m`/`--message`/`-F`/`--file` as a separate
    token, `--message=`/`--file=`-prefixed, glued with no space
    (`-m<value>`/`-F<value>`), or via a combined short-option cluster whose
    last flag is `m`/`F` (e.g. `-am <value>`, the common `git commit -am`
    idiom) -- so a commit message can never itself supply the date match
    this hook looks for (a real risk in THIS repo: its own commits
    legitimately discuss "draft/YYYY-MM-DD" as prose).
    """
    verb = None
    scan_tokens = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if verb is None and tok in _PREFIX_VALUE_FLAGS:
            if i + 1 < n:
                scan_tokens.append(tokens[i + 1])
                i += 2
            else:
                i += 1
            continue
        if verb is None and any(tok.startswith(f) for f in _PREFIX_EQ_FLAGS):
            scan_tokens.append(tok.split("=", 1)[1])
            i += 1
            continue
        if verb is None and tok in _SKIP_VALUE_FLAGS:
            i += 2 if i + 1 < n else 1  # flag AND its value discarded, never scanned
            continue
        if verb is None and not tok.startswith("-"):
            verb = tok.lower()
            i += 1
            continue
        if tok in _MESSAGE_VALUE_FLAGS:
            i += 2  # skip the flag AND its value -- never scanned
            continue
        if any(tok.startswith(f) for f in _MESSAGE_EQ_FLAGS):
            i += 1  # whole '--message=value' token skipped outright
            continue
        if _MESSAGE_GLUED_RE.match(tok):
            i += 1  # whole '-mvalue'/'-Fvalue' (no space) token skipped outright
            continue
        if _MESSAGE_CLUSTER_RE.match(tok):
            i += 2  # combined short cluster (e.g. '-am'); value is the next token
            continue
        scan_tokens.append(tok)
        i += 1
    return verb, scan_tokens


def segment_targets_today_compose(segment: str, today: str) -> bool:
    """True if `segment` is a git worktree/commit/push invocation whose
    non-message tokens reference a `draft/<today>` or `compose[-/]<today>`
    path/ref. Only the segment's first physical line is examined (via
    `_git_rest_tokens` -> `_first_line`), so a heredoc/`$()` body can't
    inject a spurious match -- the same career-playbook #442 / dev-env#499
    lesson every sibling hook in this repo already observes.
    """
    tokens = _git_rest_tokens(segment)
    if not tokens:
        return False
    verb, scan_tokens = _verb_and_scan_tokens(tokens)
    if verb not in _GATED_VERBS:
        return False
    for tok in scan_tokens:
        m = _DATE_TOKEN_RE.search(tok)
        if m and m.group(1) == today:
            return True
    return False


def command_targets_today_compose(command: str, today: str, segments=None) -> bool:
    """This hook is globally registered and runs on every Bash call on the
    machine, so the cheap `today not in command` substring check below
    matters: a match is only ever possible when today's literal date string
    appears somewhere in the command (`_DATE_TOKEN_RE` requires an exact
    match against `today`), so this short-circuits the interpreted
    `split_top_level` parse plus per-segment tokenization for the
    overwhelming majority of calls. Deliberately placed here rather than
    inside `segment_targets_today_compose`, so the prose-mention exclusion
    tests (which do contain the date) still exercise the real parse path.
    Review finding on PR #671 (performance).
    """
    if today not in command:
        return False
    if segments is None:
        segments = split_top_level(command, split_pipe=True)
    return any(segment_targets_today_compose(seg, today) for seg in segments)


def _emit_block(today: str) -> None:
    reason = (
        f"[journal-compose-force-guard] BLOCKED: this command mutates a "
        f"draft/{today} or compose-{today} journal-compose target for TODAY "
        f"({today}), and no valid, fresh force=true marker was found.\n\n"
        f"Per ADR-017, /journal-compose only proceeds on today's date with "
        f"an explicit --force. Per dev-env#631, that guard is no longer "
        f"prose you can reason past -- it is enforced here regardless of "
        f"why you believe proceeding is correct.\n\n"
        f"If the user has explicitly told you, in this conversation, to "
        f"force-compose today's journal: re-run\n"
        f"  py -3 C:/Users/brown/.claude/scripts/journal-compose-force-resolve.py \"--force\"\n"
        f"Otherwise, stop -- this is the guard working as intended, not an "
        f"error to work around.\n"
    )
    sys.stderr.write(json.dumps({"reason": reason}) + "\n")
    sys.exit(2)


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)

    command = (data.get("tool_input") or {}).get("command", "") or ""
    if not command:
        sys.exit(0)

    today = datetime.date.today().isoformat()
    if not command_targets_today_compose(command, today):
        sys.exit(0)

    marker = read_marker(marker_path_for(today))
    now = datetime.datetime.now()
    if marker and marker.get("force") is True and is_marker_fresh(marker, now):
        sys.exit(0)

    _emit_block(today)


if __name__ == "__main__":
    main()
