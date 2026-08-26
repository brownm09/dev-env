#!/usr/bin/env python3
"""Claude Code Stop hook — state-keyed tile-enumeration gate (ADR-088, extended ADR-092, ADR-094 addendum, ADR-097 per-trigger sentinels, ADR-109 deferral-question trigger, ADR-118 shard trigger, ADR-140 unchained-merge trigger).

The command-keyed ``post-merge-tile-checkpoint.py`` (ADR-060) fires on a
``gh pr merge`` *you run*, but is BLIND to a PR that reaches merged state some
other way: auto-merge landing it server-side while you were away, or a pure
``gh api`` merge. Those are exactly the cases where the tile checkpoint's
salience is lowest (post-merge is pure bookkeeping) — the lifting-logbook
PR #700 incident that motivated the ADR-046 2026-07-05 forcing-function
refinement (dev-env#595).

This hook is STATE-keyed instead: at every Stop it scans the just-ended session
transcript for SIX independent triggers, each requiring its own recorded
follow-up artifact before the stop is allowed:

1. **Merged PR** (ADR-088): a PR reached MERGED state this session (by any
   path) but no enumeration was recorded.
2. **Dangling created issue** (ADR-092, dev-env#638): a `gh issue create`
   ran this session and the created issue was NOT resolved by session end
   (resolved = closed via a same-session merged PR carrying a GitHub
   auto-close keyword, or explicitly closed via `gh issue close`) but no
   enumeration was recorded. Investigation sessions that file well-scoped
   issues and implement nothing get no mechanical nudge otherwise — unlike a
   merged PR, which this hook already covers.
3. **Tiles spawned without a table** (ADR-094 addendum, dev-env#656): a
   `spawn_task` tile was spawned this session but no assistant message
   carries the stable heading marker ``### Tiles spawned this session`` (the
   end-of-session tile table PR1/#663 introduced in `claude/CLAUDE.md` ->
   Session Summaries & Tile Tracking). Reuses the SAME spawn-detection this
   hook already performs for enumeration (a spawned tile always satisfies
   ``enumeration_recorded`` for triggers 1/2), so a merge/issue trigger can
   be *resolved* by the very spawn that leaves trigger 3 still *unsatisfied*
   — the table is a stricter, independent bar than "an enumeration happened".

3b. **Tile spawned without its shard** (ADR-118, dev-env#870): a ``spawn_task``
   tile was spawned this session but nothing evidences a write of its shard
   ``sessions/<project>/tiles/<issue-number>.json``. Numbered here because it
   is trigger 3's sibling — both key on the same spawn, and both are stricter,
   independent bars than "an enumeration happened" — but it is
   ``_TRIGGER_SHARD`` in code and evaluated fifth. The two guard different
   losses: the table is what tells the USER a tile exists; the shard is what
   lets the TILE be re-spawned once the chip dies, which it does on every app
   restart with no API to re-create it. The paired issue survives either way,
   so what is actually lost is the one-click restart the tile existed to
   provide — in exactly the crash case where restarting by hand costs most.
   Blocking, like 1-3: whether a path was written is objectively verifiable.
   Session-global rather than per-tile — see ``evaluate_tile_shard``'s
   docstring for why per-spawn correspondence is not detectable and why
   counting breaks on the documented serializer recipe.

4. **Deferral-question anti-pattern** (new ADR, dev-env#772): a PR merged or
   an issue was created this session, and the assistant's OWN final response
   asks the user a scheduling/permission question about apparent follow-up
   work ("let me know if you want me to start it now", "should I implement
   this now or leave it for a fresh session") instead of tiling it directly,
   with no "skip tiles" override waiving it. The motivating incident: a
   known, user-named follow-up (the next PR in a multi-PR initiative,
   ADR-059) was asked about instead of tiled, even though the tile-now rule
   already existed in plain language, and even though OTHER genuine
   follow-ups WERE correctly tiled in that very same turn — a
   judgment/classification failure, not a missing rule, so this trigger
   exists to catch it mechanically rather than relying on better prose (and
   deliberately does NOT accept "an enumeration happened somewhere this
   session" as resolution, unlike triggers 1-3 — see ``evaluate_deferral``'s
   own docstring for why that would have missed the motivating incident
   entirely). Unlike triggers 1-3, this is a natural-language pattern match,
   not an objectively verifiable fact, so a false positive is possible (a
   legitimate design question can resemble the pattern) — it therefore does
   NOT block Claude via exit 2 (which would force a pointless
   self-correction loop on a maybe-false alarm). It rides the non-blocking
   ``_hookout.emit_advisory(audience="user")`` channel instead (a
   systemMessage toast, exit 0), so a human judges the specific case, same as
   the user caught the motivating incident directly in chat.

5. **Unchained merge** (ADR-140, dev-env#1044): a PR merged this session, the
   session's OWN opening prompt named no follow-on work, and the session
   neither spawned a tile nor asked the user anything. ADR-137 says every tile
   spawn is a look-ahead moment for what comes after it; its own
   Alternatives-considered deferred a hook as premature, with the explicit
   precondition "a hook can follow later if the anti-pattern recurs despite
   the documented rule." This is that hook, and it enforces the *floor*
   ADR-137 leaves unenforced: a merge in a session that was not itself handed
   a next thread must end with one queued, or with the choice put to the user.
   Chain-bearing-ness is read from the FIRST genuine user prompt
   (``_hookutil.first_user_prompt_text``) — an issue/PR reference, or the
   ``=== CHAIN`` marker a ``retro-chain-refill`` / ADR-132 / ADR-137 chained
   tile prompt carries — means the session already had its next thread named
   for it, so whatever it chains next is ADR-137's prose rule to judge, not
   this hook's. A mid-session mention of an issue number is deliberately NOT
   consulted: it says nothing about what the session was *asked* to do, which
   is the whole scope test. Resolution is any of three paths ADR-140's
   CLAUDE.md rule offers, each counted only when it happens AT OR AFTER the
   merge — a ``spawn_task`` (chained the next thread), an ``AskUserQuestion``
   (no next thread was determinable, so the ranked open-issue survey was put
   to the user), or a ``gh issue create`` (the documented fallback for a
   session where ``spawn_task`` itself is unavailable) — plus the universal
   "skip tiles" override. Blocking, like 1-3b: every input is an objectively
   verifiable fact (a merge marker, a tool_use name, a regex over one fixed
   prompt), not a natural-language judgment, so it does NOT ride trigger 4's
   advisory channel.

Triggers 1-3b and 5 are the direct Stop-hook analog of
``pre-merge-findings-gate`` (ADR-039) and BLOCK the stop (exit 2) with a
reminder on stderr. A recorded enumeration (triggers 1/2) is either an actual
``spawn_task`` tile, or the prescribed text ("Follow-ups considered: ... ->
tiled (task_id / #N) / -> not tiled, because <reason>") — session-global and
shared across triggers 1 and 2 (one enumeration satisfies either or both). A
bare "no follow-ups" assertion does NOT satisfy the gate: per the ADR-046
refinement, "No follow-ups" is valid only as the visible result of an
enumeration, never as a bare assertion (the #700 skip). The fourth
(deferral-question) trigger reuses ``skip_override`` but deliberately NOT
``enumeration_recorded`` (see ``evaluate_deferral``'s docstring for why),
and — as described above — surfaces via the advisory channel rather than
blocking.

Complements — does not replace — the command-keyed hook: that one is the
immediate in-the-moment nudge when ``gh pr merge`` runs; this one is the
Stop-time verification that the enumeration actually happened, covering every
merge path (and, since ADR-092, the dangling-issue path too). It is also NOT
inert in background / SDK-launched sessions (ADR-053), where every
PostToolUse hook — including the command-keyed sibling — silently never
fires; the Stop event still dispatches, so this is the only tile enforcement
that survives there.

Detection is a pure transcript scan — no ``gh`` calls, no network, no
subprocess (so no ``_winsubp``). Fail-open: any error exits 0. Each trigger
fires at most once per session via its OWN independent scratch sentinel
(ADR-097, dev-env#677), and honors the ``stop_hook_active`` loop-guard flag
(Claude Code hooks reference: a Stop hook must check it and exit 0 early once
continuing, or it can block forever).

Per-trigger sentinels (ADR-097) replace the single sentinel ADR-088 originally
introduced and ADR-092/ADR-094 extended unchanged. Under one shared sentinel,
whichever trigger fired or resolved FIRST set the one flag file, and every
later Stop in the session skipped evaluating ALL THREE triggers — including
one whose own condition (e.g. a tile spawned in a later, separate turn) had
not even happened yet when the sentinel was set (dev-env#677, found during the
PR #674 review that landed trigger 3). Splitting the sentinel per trigger
means a later-arising trigger is still caught, while a session where every
trigger has already fired or resolved still skips reading the transcript at
all — the pre-#677 fully-resolved-session fast path, preserved. Trigger 4
(deferral-question) was added directly onto this per-trigger architecture —
it has no historical shared-sentinel bug of its own to describe, since it
launched already using the same independent-sentinel model as the other
three.

The transcript-record readers (``load_records`` / ``_parse_records`` /
``iter_bash_calls`` / ``_result_text`` / ``_content_items``) now live in
``_hookutil`` — the same shared module the sentinels / transcript-locate come
from — so this hook and ``posttooluse-inert-advisory.py`` can no longer drift on
how a transcript is parsed (ADR-090, reversing ADR-088's original replicate-them
decision after both PR #604 reviewers flagged the duplication). This gate imports
only the three it uses: ``_content_items`` and ``_parse_records`` (its ``main()``
parses the transcript text directly, after the cheap pre-filter), and
the shared ``iter_bash_calls`` (aliased) — wrapped in a thin 2-tuple adapter below,
since ``_hookutil``'s ``iter_bash_calls`` returns ``(command, output, cwd)`` and
this gate never needs ``cwd``. (``load_records`` and ``_result_text`` also live in
``_hookutil`` but the gate needs neither directly — ``_result_text`` is used only
inside the shared ``iter_bash_calls``.) The merge-marker / segment parser is
imported from ``_hookio``; ``_first_line`` stays local — a command-segment helper
(not a transcript reader), intentionally duplicated with ``_hookio._first_line``
as a separate decision (ADR-088).

Stdin JSON shape (Stop):
  {"session_id": "...", "transcript_path": "/abs/path.jsonl",
   "stop_hook_active": false, ...}

Exit 0 — no merged-state PR, no dangling created issue, no spawned tile
         missing its table or its shard, and no unchained merge this session
         (triggers 1-3b and 5); enumeration/table/shard already recorded, the
         next thread chained or put to the user, a "skip tiles" override
         present, that trigger already fired (its own per-trigger sentinel),
         stop_hook_active set, or any error (fail-open). A deferral-question
         hit (trigger 4) with no blocking trigger firing still exits 0, but
         carries a systemMessage advisory on stdout (see below).
Exit 2 — a PR merged and/or a created issue remains unresolved and/or a tile
         was spawned with no table and/or with no shard write and/or an
         unchained session merged a PR without queuing what comes next
         (triggers 1-3b and 5); blocking reminder(s) on stderr. Trigger 4 never
         contributes to this exit code — when any blocking trigger also fires,
         trigger 4's advisory is skipped this turn in favor of the blocking
         reminder (there is only one exit code per invocation, and the harder
         enforcement wins; a persisting deferral-question condition can still
         surface on a later Stop).
"""
from __future__ import annotations

import _hookout
import _hookutil
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

from _hookio import (
    is_rest_merge_command,
    mask_prose_flag_values,
    merge_pr_number_from_output,
    output_has_merge_marker,
    output_has_rest_merge_marker,
    split_top_level,
)
from _hookutil import (
    _content_items,
    _is_synthetic_user,
    _parse_records,
    _user_message_texts,
    first_user_prompt_text,
    iter_bash_calls as _iter_bash_calls,
)
from _repo_target import (
    issue_number_from_issue_url,
    iter_issue_urls,
    iter_pr_urls,
    positional_number,
    pr_number_from_pr_url,
    repo_from_flag,
    repo_from_pr_url,
)

SENTINEL_PREFIX = "tile-enumeration-gate-"
# Per-trigger sentinel suffixes (ADR-097, dev-env#677): each trigger fires and
# resolves via its OWN "{SENTINEL_PREFIX}{suffix}{session_id}.flag" file rather
# than the one file all three triggers used to share. cleanup_stale_sentinels
# (called with the bare SENTINEL_PREFIX) still sweeps all three unchanged --
# its glob (f"{prefix}*.flag") matches every suffixed filename, since each one
# still starts with SENTINEL_PREFIX.
_TRIGGER_PR = "pr-"
_TRIGGER_ISSUE = "issue-"
_TRIGGER_TABLE = "table-"
_TRIGGER_DEFER = "defer-"
_TRIGGER_SHARD = "shard-"
_TRIGGER_WORKSTREAM = "workstream-"
# _TRIGGERS is DERIVED from _TRIGGER_SPECS (below, after the evaluators and
# formatters it references) rather than hand-listed here, so the sentinel set
# and the trigger table can never drift apart (dev-env#696).

# --- command-shape detection (anchored via split_top_level, not raw substring) --
# Each of these is matched against the lstripped FIRST LINE of a top-level
# segment, so a phrase appearing inside a heredoc body / quoted argument / $()
# subshell is never mistaken for a genuine invocation (the dev-env#499 class).
_MERGE_STMT_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+merge\b", re.IGNORECASE)
_PR_CREATE_STMT_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+create\b", re.IGNORECASE)
_PR_VIEW_STMT_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+(?:view|checks)\b", re.IGNORECASE)
# The `gh api` verb + PUT-method-flag check and the `"merged":true` output check
# (formerly this file's own `_GH_API_STMT_RE`/`_MERGED_TRUE_RE`) now come from
# `_hookio.is_rest_merge_command`/`output_has_rest_merge_marker` (dev-env#992,
# ADR-050 Amendment 23's own "deliberately still NOT fully converged" note,
# revisited here as its named follow-up). That amendment left this file's looser
# `has_api` check (any `gh api` verb, no method-flag requirement) unconverged on
# the theory that a false positive here is low-stakes -- a passive PR-number
# addition to a Stop hook's already-merged set, not a state-changing side effect.
# On audit, tightening turned out to have no offsetting cost: GitHub's read-only
# "check if merged" GET variant of this same path returns 204/404, never a
# `"merged":true` JSON body (that shape is unique to the PUT merge response), so
# the untightened check was never actually exploitable via a genuine GET call --
# the PUT-flag requirement only removes slack, not real coverage. Converging
# closes the drift instead of leaving it to be rediscovered as "unexplained
# duplication" a second time.

# Strip the `gh pr <verb>` prefix so the positional PR arg can be read.
_STRIP_VERB_RE = re.compile(r"\s*gh(?:\.exe)?\s+pr\s+\w+\b(.*)", re.IGNORECASE | re.DOTALL)
# The PR-URL / issue-URL parsing, the bare positional-number token, and the
# `--repo`/`-R` flag extraction (with their quote-aware masking) now come from
# the shared `_repo_target` module (ADR-111) — one implementation across the
# five sibling hooks. This file's own `_PR_URL_RE`/`_ISSUE_URL_RE` fallbacks
# were the last members of that family still searched UNMASKED; routing them
# through `_repo_target` lets each call site mask its input with
# `mask_prose_flag_values` (dev-env#685), so a `--subject`/`--body` value
# containing a decoy `/pull/N` URL can no longer hijack the extracted number —
# even when a real positional number is also present — while a bare quoted URL
# argument (never preceded by `--subject`/`--body`) still resolves.

# --- dangling-created-issue detection (ADR-092, dev-env#638) -------------------
_ISSUE_CREATE_STMT_RE = re.compile(r"gh(?:\.exe)?\s+issue\s+create\b", re.IGNORECASE)
_ISSUE_CLOSE_STMT_RE = re.compile(r"gh(?:\.exe)?\s+issue\s+close\b", re.IGNORECASE)
# `gh pr edit` can attach a Closes-keyword body to an already-created PR
# (review of PR #639) -- reuses _STRIP_VERB_RE / _target_pr below, which
# already generically strip "gh pr <any-verb>" and were written for
# merge/view/checks, since gh's syntax (`gh pr edit <number|url> ...`) is
# identical in shape.
_PR_EDIT_STMT_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+edit\b", re.IGNORECASE)
# Strip the `gh issue close` prefix so the positional issue number can be read.
_STRIP_ISSUE_CLOSE_VERB_RE = re.compile(r"\s*gh(?:\.exe)?\s+issue\s+close\b(.*)", re.IGNORECASE | re.DOTALL)
# GitHub's documented auto-close keywords (close/closes/closed, fix/fixes/fixed,
# resolve/resolves/resolved) immediately followed by an optional colon and a
# same-repo issue reference -- https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/linking-a-pull-request-to-an-issue
_CLOSES_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*#(\d+)", re.IGNORECASE,
)

# --- merged-state signals in command output ------------------------------------
# `_PULLS_MERGE_PATH_RE` stays local rather than importing `_hookio`'s own
# `_PULLS_MERGE_PATH_RE` (dev-env#992): that module's copy is deliberately
# non-capturing (nothing there reads the PR number out of it -- see its own
# comment), while this file needs the captured `(\d+)` to know WHICH PR merged.
# The "is this actually a REST merge" gate itself (verb + PUT flag + a bare,
# non-capturing path search) now comes from `is_rest_merge_command` (imported
# above); this regex runs only after that gate already passed, purely to pull
# the number out for `directly.add(int(pm.group(1)))` below.
_PULLS_MERGE_PATH_RE = re.compile(r"/pulls/(\d+)/merge\b")     # gh api PUT target
_MERGED_STATE_RE = re.compile(r'"state"\s*:\s*"MERGED"')      # gh pr view state
_OUTPUT_NUMBER_RE = re.compile(r'"number"\s*:\s*(\d+)')       # gh pr view number

# --- enumeration / override text markers ---------------------------------------
# An actual spawn_task tool call (MCP name mcp__ccd_session__spawn_task) — the
# strongest "the checkpoint fired" signal. Bare verb so any namespacing hits.
_SPAWN_TASK_RE = re.compile(r"spawn_task", re.IGNORECASE)
# Text forms of the prescribed enumeration. A BARE "no follow-ups" matches none
# of these on purpose — that is the #700 skip the ADR-046 refinement invalidated.
_ENUM_MARKERS = (
    re.compile(r"follow[\s-]*ups?\b[^\n]{0,60}\bconsidered", re.IGNORECASE),
    re.compile(r"\bconsidered\b[^\n]{0,60}\bfollow[\s-]*ups?", re.IGNORECASE),
    re.compile(r"\bnot\s+tiled\b", re.IGNORECASE),
    re.compile(r"(?:->|→)\s*tiled\b", re.IGNORECASE),
)
# The only valid waiver — an explicit user instruction naming the tile step.
_SKIP_RE = re.compile(r"\b(?:skip\s+tiles?|don'?t\s+(?:spawn\s+)?tiles?|no\s+tiles?)\b",
                      re.IGNORECASE)

# --- tile-table detection (ADR-094 addendum, dev-env#656) -----------------------
# The bare substring "spawn_task", matching _SPAWN_TASK_RE's own "any
# namespacing hits" bare-verb philosophy (review of PR #674) -- an earlier
# version of this pre-filter checked the exact fully-qualified tool name
# (mcp__ccd_session__spawn_task) instead, which is a STRICT SUBSET of what
# _SPAWN_TASK_RE (used inside session_spawned_tiles, below) can match: a
# spawn_task call recorded under any other MCP namespace would satisfy the
# real detector but be silently skipped by that narrower pre-filter, defeating
# the detector's own documented namespace-robustness. The bare substring costs
# a few extra full-transcript reparses in sessions that merely MENTION
# "spawn_task" in prose (empirically ~8x in one real no-tile-spawned
# transcript) -- a bounded, accepted perf cost, the same tradeoff the
# pre-existing "merged" pre-filter branch below already makes.
_SPAWN_TASK_SUBSTRING = "spawn_task"
# The stable heading marker PR1 (#663) defined in claude/CLAUDE.md -- Session
# Summaries & Tile Tracking: "### Tiles spawned this session". Requires a `#`
# heading prefix (1-6 levels) anchored to the START of a line (re.MULTILINE)
# so a bare prose mention of the phrase mid-sentence (e.g. this hook's own
# reminder text quoted back, or CLAUDE.md's rule text) is never mistaken for
# an actual emitted heading -- a real markdown heading always starts its own
# line, which this mirrors.
_TABLE_MARKER_RE = re.compile(r"^#{1,6}\s*tiles\s+spawned\s+this\s+session",
                              re.IGNORECASE | re.MULTILINE)

# --- tile-shard write detection (ADR-118 enforcement, dev-env#870) -------------
# A tile shard is `sessions/<project>/tiles/<issue-number>.json`. This matches the
# `tiles/<N>.json` tail only -- deliberately NOT anchored to `sessions/<project>/`,
# and deliberately permissive about surrounding characters -- because this pattern
# is used to find EVIDENCE THAT THE WRITE HAPPENED, where over-matching is the safe
# direction (an extra match means the trigger does not fire) and under-matching is
# the dangerous one (a false block on a session that did write its shard). It is
# matched against tool inputs AND tool OUTPUT; see `tile_shard_write_present`.
_TILE_SHARD_PATH_RE = re.compile(r"\btiles[/\\]\d+\.json\b", re.ASCII)

# --- unchained-merge detection (ADR-140, dev-env#1044) -------------------------
# An `AskUserQuestion` tool call -- the second of the two resolution paths
# ADR-140's CLAUDE.md rule offers (the first is a spawn_task). Bare verb with
# no namespace anchor, deliberately mirroring _SPAWN_TASK_RE's own "so any
# namespacing hits" philosophy: this is matched against a tool_use item's
# `name`, so a rehosted/renamed host prefix must still resolve.
_ASK_USER_RE = re.compile(r"AskUserQuestion", re.IGNORECASE)
# What makes an opening prompt "chain-bearing": a same-repo issue/PR reference,
# a full github issues/pull URL, or the `=== CHAIN` marker a chained tile prompt
# carries (the shape `retro-chain-refill` and ADR-132/ADR-137 chained tiles use).
# Any one of the three means the session was HANDED its next thread, so trigger 5
# is out of scope -- what it chains after that is ADR-137's prose rule to judge.
#
# A bare `#\d+` is ambiguous: real tile/issue prompts use it ("Implement #1044",
# "work on dev-env#696"), but ordinary prose also uses "#<digit>" as an ordinal or
# step marker ("do step #2 of the runbook") that names no issue at all. `re.ASCII`
# keeps a non-ASCII decimal digit (e.g. Arabic-Indic) from matching either --
# `_TILE_SHARD_PATH_RE` above already carries the same flag.
#
# Over-matching here used to be treated as the SAFE direction on the theory that
# an extra match just means the trigger does not fire -- but the branch it feeds
# returns (None, True), which `main()` writes as a RESOLVED sentinel, so an
# over-match instead silently and PERMANENTLY disarms a blocking trigger for the
# rest of the session (review of PR #1053, dev-env#1044). `_has_chain_issue_reference`
# below narrows the bare-`#N` case with a small denylist of common ordinal/step
# words immediately preceding it -- necessarily incomplete, but it closes the
# demonstrated false-positive class without narrowing either real form tile
# prompts use: a bare "#N" at a sentence boundary, or a repo-prefixed compound
# ("dev-env#696", detected by ANY non-alnum-adjacent word character before "#",
# which always counts regardless of the word list).
_CHAIN_ISSUE_REF_RE = re.compile(r"#\d+", re.ASCII)
_CHAIN_URL_RE = re.compile(r"github\.com/[^\s/]+/[^\s/]+/(?:issues|pull)/\d+", re.IGNORECASE)
_CHAIN_MARKER_RE = re.compile(r"===\s*CHAIN", re.IGNORECASE)
# Words that commonly precede a bare "#N" as an ordinal/step marker rather than an
# issue/PR reference. Deliberately small and conservative: missing a real ordinal
# usage here just means `prompt_is_chain_bearing` over-matches as it always did
# (safe-ish, per the comment above), while an entry that's wrong in the OTHER
# direction (a word that's sometimes part of a genuine issue mention) would
# silently disarm the trigger -- so this list only grows on a demonstrated case.
_CHAIN_ORDINAL_WORDS = frozenset({
    "step", "item", "number", "no", "point", "part", "phase", "priority",
    "rank", "level", "stage", "tier", "round", "page", "line", "row",
    "column", "slot",
})


def _has_chain_issue_reference(text: str) -> bool:
    """True iff *text* contains a bare issue/PR reference (``#1044``,
    ``dev-env#696``) rather than only an ordinal/step marker that merely looks
    like one (``step #2``). See ``_CHAIN_ISSUE_REF_RE``'s comment for why this
    exists instead of a plain ``_CHAIN_ISSUE_REF_RE.search(text)``."""
    for match in _CHAIN_ISSUE_REF_RE.finditer(text):
        start = match.start()
        if start > 0 and text[start - 1].isalnum():
            return True  # a compound like "dev-env#696" or "gh#123" -- always real
        before = text[:start].rstrip()
        word = before.rsplit(None, 1)[-1].lower() if before else ""
        if word not in _CHAIN_ORDINAL_WORDS:
            return True
    return False

# --- deferral-question detection (new ADR, dev-env#772) ------------------------
# A deliberately narrow, bounded set of phrasings for "asked the user a
# scheduling/permission question about apparent follow-up work instead of
# tiling it" -- the motivating incident's exact shape ("Let me know if you
# want me to start it now or leave it for a fresh session"). This is a
# heuristic natural-language match, not an objective fact (see the module
# docstring's trigger-4 section) -- err toward a few concrete phrasings
# rather than a fully generalized pattern, matching the bounded-keyword-group
# style already used by _SKIP_RE / stop-journal-stub-checkpoint.py's
# report_intent, rather than the anchored command-shape regexes above (there
# is no "command shape" for prose). Cheap substring pre-filter tokens are
# kept in sync with these regexes by hand (see the main() pre-filter
# comment) -- there is no mechanical link between the two, so a new phrase
# added to one must be added to the other.
_DEFERRAL_QUESTION_RES = (
    # The space sits INSIDE the alternation (not before it), so both "you
    # want me to" and the natural contraction "you'd like me to" (no space
    # before the apostrophe) match. A first draft put the space before the
    # group (`you (?:want|'d like) me to`), which requires "you 'd like" --
    # nobody writes that; the actual contraction "you'd" was silently
    # undetectable despite being one of the two phrasings this trigger is
    # documented (ADR-109) to target. Caught by /review, verified by hand.
    re.compile(r"let me know if you(?: want|'d like) me to", re.IGNORECASE),
    re.compile(r"\bshould i (?:start|begin|implement|proceed|go ahead|do this|tackle)\b",
               re.IGNORECASE),
    re.compile(r"\bwant me to (?:start|begin|implement|proceed|tackle|do (?:this|that))\b"
               r".{0,40}\bnow\b", re.IGNORECASE),
)
# Cheap lowercase substrings used ONLY as a fast pre-filter (main()'s
# skip-the-parse gate) -- each is a REQUIRED prefix/substring of at least one
# _DEFERRAL_QUESTION_RES pattern, so this is a true SUPERSET of what those
# regexes can match (mirroring the "merged" / issue-create / spawn_task
# pre-filter clauses' own superset relationship to their real detectors): if
# none of these substrings are present, none of the regexes can match either.
# Kept deliberately tight (no bare "now"/"later"/"wait" -- too common in
# ordinary text to usefully short-circuit anything) at the cost of a
# correspondingly narrower _DEFERRAL_QUESTION_RES; see that tuple's own
# comment on why it stays a few concrete phrasings rather than a broader
# pattern.
_DEFER_PREFILTER_SUBSTRINGS = ("let me know if you", "should i ", "want me to")


# --- transcript readers -------------------------------------------------------
# The record readers (``load_records`` / ``_parse_records`` / ``iter_bash_calls`` /
# ``_result_text`` / ``_content_items``) now live in ``_hookutil`` (ADR-090); this
# gate imports the three it uses (``_content_items``, ``_parse_records``, and the
# shared ``iter_bash_calls`` aliased as ``_iter_bash_calls``). Only ``_first_line``
# (a command-segment helper, not a transcript reader) and the 2-tuple
# ``iter_bash_calls`` adapter below stay local.

def _first_line(segment: str) -> str:
    """A segment's own first physical line — its invocation/flags only ever live
    here; everything after is heredoc/`$()` body (cf. _hookio._first_line)."""
    return segment.split("\n", 1)[0].split("\r", 1)[0]


def iter_bash_calls(records: list) -> list:
    """The gate's ``(command, output)`` view of ``_hookutil.iter_bash_calls``.

    The shared reader yields ``(command, output, cwd)``; this gate never uses
    ``cwd`` (only posttooluse-inert-advisory.py's dev-env-cwd scoping does), so
    this thin adapter drops it — keeping ``session_merged_prs`` and the existing
    tests on the historical 2-tuple contract while the transcript-pairing logic
    lives in one shared place (ADR-090).
    """
    return [(command, output) for command, output, _cwd in _iter_bash_calls(records)]


# --- pure detection helpers (offline-testable) ---------------------------------

def _target_pr(segment_first_line: str) -> int | None:
    """PR number a ``gh pr merge|view|checks`` invocation targets (a ``/pull/N``
    URL or the first bare positional integer), else ``None`` (bare form — infers
    from the current branch, no number in the command).

    Both checks now route through the shared ``_repo_target`` module (ADR-111).
    The URL check masks ``--subject``/``--body`` decoy values with
    ``mask_prose_flag_values`` first (dev-env#685), so a decoy ``/pull/N`` URL in
    a prose flag can no longer hijack the target number — the URL check runs
    FIRST, so before this fix a decoy won even when a real positional number was
    also present. A bare quoted ``/pull/N`` URL argument is not a prose-flag
    value and is left matchable, exactly as before. The positional fallback
    (``positional_number``) masks quoted spans internally (dev-env#650) so a bare
    decoy number ("resolves 42 items") can't be mistaken for the real one.
    """
    m = _STRIP_VERB_RE.match(segment_first_line)
    tail = m.group(1) if m else ""
    n = pr_number_from_pr_url(mask_prose_flag_values(tail))
    if n is not None:
        return n
    return positional_number(tail)


def _explicit_repo(segment_first_line: str) -> str | None:
    """owner/repo explicitly named on a ``gh pr …`` invocation — a ``--repo``/``-R``
    flag value or a ``/pull/N`` URL — else ``None`` (the command infers its repo
    from cwd, which this hook cannot resolve). Makes the auto-merge correlation
    repo-aware so a same-numbered PR in a *different* repo, merely inspected as
    MERGED in-session, cannot be mistaken for this session's own PR (review of
    PR #604).

    Both checks now route through the shared ``_repo_target`` module (ADR-111).
    ``repo_from_flag`` masks its input with ``mask_quoted_spans`` internally
    (dev-env#626/#634), so a ``--subject``/``--body`` value's "-R other/repo"
    substring can't be mistaken for a real flag. The URL fallback masks
    ``--subject``/``--body`` decoy values with ``mask_prose_flag_values`` first
    (dev-env#685) — this file's own ``_PR_URL_RE`` was the last of the family
    still searched entirely unmasked — while a bare quoted ``/pull/N`` URL
    argument (not a prose-flag value) still resolves, exactly as before.
    """
    flag_repo = repo_from_flag(segment_first_line)
    if flag_repo:
        return flag_repo
    return repo_from_pr_url(mask_prose_flag_values(segment_first_line))


def session_merged_prs(calls: list) -> set:
    """PRs that reached merged state this session, by any path.

    One pass over the paired Bash calls — ``split_top_level`` is run once per
    command, not once per merge-path helper (review of PR #604). The result is
    direct merge evidence UNION an observed MERGED state correlated with a PR the
    session actually acted on (the auto-merge / pure-``gh api`` case):

    - **Direct evidence:** a real ``gh pr merge`` success marker (a manual merge
      or the two-step workaround's first command; ``--help`` / a queued
      ``--auto`` print no marker, dev-env#485), or a ``gh api .../pulls/N/merge``
      whose result is ``"merged": true``.
    - **Observed (auto-merge):** a ``gh pr view|checks`` output at ``"state":
      "MERGED"`` for a PR the session created (``gh pr create`` URL) or targeted
      (``gh pr merge`` incl. ``--auto``). The PR number is taken from the
      authoritative JSON ``"number"`` first, falling back to the positional arg.
      Correlation is by ``(repo, number)``: a ``None`` repo on either side (the
      command named no explicit repo → cwd's) falls back to a number match,
      preserving every same-repo true positive, while two *explicit* differing
      repos never match — so an unrelated same-numbered PR in another repo,
      inspected as MERGED, does not false-fire.
    """
    acted: dict = {}      # number -> set of repos (or None) the session acted on it in
    observed: list = []   # (repo_or_None, number) seen at MERGED state
    directly: set = set()  # numbers with direct in-session merge evidence

    for command, output in calls:
        output = output or ""
        firsts = [_first_line(seg).lstrip() for seg in split_top_level(command)]
        merge_firsts = [f for f in firsts if _MERGE_STMT_RE.match(f)]
        view_firsts = [f for f in firsts if _PR_VIEW_STMT_RE.match(f)]
        has_create = any(_PR_CREATE_STMT_RE.match(f) for f in firsts)

        # --- acted-on: created PRs (repo from the URL) + merge targets ----------
        if has_create:
            for repo, num in iter_pr_urls(output):
                acted.setdefault(num, set()).add(repo)
        for f in merge_firsts:
            n = _target_pr(f)
            if n is not None:
                acted.setdefault(n, set()).add(_explicit_repo(f))

        # --- direct merge evidence ---------------------------------------------
        if merge_firsts and output_has_merge_marker(output):
            n = merge_pr_number_from_output(output)
            if n is None:
                for f in merge_firsts:
                    n = _target_pr(f)
                    if n is not None:
                        break
            if n is not None:
                directly.add(n)
        if is_rest_merge_command(command) and output_has_rest_merge_marker(output):
            pm = _PULLS_MERGE_PATH_RE.search(command)
            if pm:
                directly.add(int(pm.group(1)))

        # --- observed MERGED state (auto-merge) --------------------------------
        if view_firsts and _MERGED_STATE_RE.search(output):
            nm = _OUTPUT_NUMBER_RE.search(output)  # authoritative -> prefer it
            n = int(nm.group(1)) if nm else None
            repo = None
            for f in view_firsts:
                repo = _explicit_repo(f)
                if n is None:
                    n = _target_pr(f)
                if repo is not None:
                    break
            if n is not None:
                observed.append((repo, n))

    auto: set = set()
    for repo, n in observed:
        acted_repos = acted.get(n)
        if acted_repos is None:
            continue
        if repo is None or None in acted_repos or repo in acted_repos:
            auto.add(n)
    return directly | auto


def _closed_issue_number(segment_first_line: str) -> int | None:
    """Issue number a `gh issue close` invocation targets: an `.../issues/N`
    URL first, else the first bare positional integer after the verb, else
    ``None``. `gh issue close` accepts `{<number> | <url>}` -- a session
    commonly copy-pastes the URL `gh issue create` itself just printed, and
    the bare-integer-only lookup previously missed that form entirely (the
    issue number in a URL is preceded by `/`, never whitespace, so it never
    satisfied the positional token's `(?<!\\S)` boundary -- review of PR #639,
    confirmed independently by both reviewers). Both checks now route through
    the shared `_repo_target` module (ADR-111), mirroring `_target_pr`'s
    URL-first-then-positional precedence: the URL check runs
    `mask_prose_flag_values` first for consistency with the other four
    command-parsing call sites that share it, though `gh issue close`'s own
    free-text flags (`--comment`/`-c`, `--reason`/`-r`) are not in that
    helper's `--subject`/`--body`/`-t`/`-b` set -- a decoy `/issues/N` URL
    inside `--comment` is not masked here (pre-existing behavior, unchanged by
    this PR); the positional fallback (`positional_number`) masks quoted spans
    internally (dev-env#650) so a decoy bare number can't be mistaken for the
    real one."""
    m = _STRIP_ISSUE_CLOSE_VERB_RE.match(segment_first_line)
    tail = m.group(1) if m else ""
    n = issue_number_from_issue_url(mask_prose_flag_values(tail))
    if n is not None:
        return n
    return positional_number(tail)


def session_created_issues(calls: list) -> dict:
    """``{issue_number: repo_or_None}`` for every `gh issue create` this
    session, keyed off the created issue's URL in the command output —
    mirrors ``session_merged_prs``'s ``acted``-dict *construction* for
    `gh pr create` (same URL-in-output extraction shape). Unlike ``acted``,
    the captured repo is not yet consumed by any correlation logic —
    ``session_resolved_issue_numbers`` matches purely on issue number (see
    its own docstring and the ADR-092 Limitations section on cross-repo
    scoping) — so treat this as a captured-but-currently-unused field, not
    evidence that resolution is already repo-aware (review of PR #639)."""
    created: dict = {}
    for command, output in calls:
        firsts = [_first_line(seg).lstrip() for seg in split_top_level(command)]
        if not any(_ISSUE_CREATE_STMT_RE.match(f) for f in firsts):
            continue
        for repo, num in iter_issue_urls(output or ""):
            created[num] = repo
    return created


def session_resolved_issue_numbers(calls: list, merged_prs: set) -> set:
    """Issue numbers resolved this session.

    An issue counts as resolved iff EITHER:

    - A top-level `gh pr create` OR `gh pr edit` segment's own text (including
      its heredoc body, where a `--body "$(cat <<'EOF' ... EOF)"` value
      typically lives) contains a GitHub auto-close keyword (Closes/Fixes/
      Resolves #N) for it, AND that PR reaches merged state this session (per
      ``session_merged_prs``) — GitHub only auto-closes on merge, never on
      mere PR creation/editing, so a Closes-style reference in a PR that never
      merged does not resolve the issue. `gh pr edit` covers the "create the
      PR, then attach the Closes keyword afterward" flow (review of PR #639);
      its target PR number is read the same way `session_merged_prs` already
      reads a `gh pr merge|view|checks` target, via the shared `_target_pr`.
    - An explicit `gh issue close N` ran this session.

    The keyword search is scoped to each `gh pr create` / `gh pr edit`
    segment's own text (not the whole raw command), so an unrelated
    Closes-style mention on a different chained segment can never leak in —
    mirrors ``session_merged_prs``'s per-segment scoping discipline (the A4
    hardening for cross-repo/cross-segment leakage).

    Two forms are NOT covered here, both documented as accepted limitations
    (see the ADR-092 Limitations section): a Closes-keyword living only in a
    commit message (never the `gh pr create`/`gh pr edit` command text itself)
    is invisible to a pure command-transcript scan; and an explicit cross-repo
    reference (`Closes owner/repo#N`) never matches `_CLOSES_KEYWORD_RE`
    (which requires a bare `#N` immediately after the keyword) — this is a
    false NEGATIVE (the issue is treated as still dangling, prompting an
    enumeration that turns out to be unnecessary), the safe failure direction,
    never a false match against the wrong issue. No cross-repo scoping is
    applied to the resolution correlation itself either: a coincidentally
    same-numbered issue resolved in an unrelated repo this session would be
    misread as resolving this repo's own issue. Considered low-risk in
    practice (would require working across repos AND colliding issue numbers
    in the same session).
    """
    resolved: set = set()
    for command, output in calls:
        pr_create_segments: list = []
        pr_edit_segments: list = []  # (segment, its own first-line) pairs

        for seg in split_top_level(command):
            first = _first_line(seg).lstrip()
            if _ISSUE_CLOSE_STMT_RE.match(first):
                n = _closed_issue_number(first)
                if n is not None:
                    resolved.add(n)
            elif _PR_CREATE_STMT_RE.match(first):
                pr_create_segments.append(seg)
            elif _PR_EDIT_STMT_RE.match(first):
                pr_edit_segments.append((seg, first))

        if pr_create_segments:
            pr_numbers = {num for _repo, num in iter_pr_urls(output or "")}
            if pr_numbers & merged_prs:
                for seg in pr_create_segments:
                    resolved |= {int(n) for n in _CLOSES_KEYWORD_RE.findall(seg)}

        for seg, first in pr_edit_segments:
            n = _target_pr(first)
            if n is not None and n in merged_prs:
                resolved |= {int(m) for m in _CLOSES_KEYWORD_RE.findall(seg)}

    return resolved


def session_unresolved_created_issues(calls: list, merged_prs: set) -> set:
    """Issues created this session that remain unresolved at Stop."""
    created = session_created_issues(calls)
    if not created:
        return set()
    return set(created) - session_resolved_issue_numbers(calls, merged_prs)


def session_spawned_tiles(records: list) -> bool:
    """True iff a real ``spawn_task`` tool call happened this session, checked
    via ``_SPAWN_TASK_RE`` against a ``tool_use`` item's ``name``. The single
    source of truth for "was a tile spawned" — ``enumeration_recorded``
    delegates to this for its own tool_use check (triggers 1/2), and
    ``evaluate_tile_table`` uses it directly (trigger 3) to require a spawn
    independently of enumeration TEXT (which must NOT, by itself, require a
    table — only an actual spawn does). Extracting this out of
    ``enumeration_recorded`` (review of PR #674) means the two callers can
    never drift on what counts as "a tile was spawned."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and _SPAWN_TASK_RE.search(item.get("name", "") or ""):
                return True
    return False


def table_marker_present(records: list) -> bool:
    """True iff an assistant message this session carries the stable tile-
    table heading (``### Tiles spawned this session``, PR1/#663). Only
    ``assistant`` ``text`` items are scanned — a user message, a tool_result,
    or CLAUDE.md's own rule text merely mentioning the heading must never
    satisfy the gate (mirrors ``skip_override``'s user-text-only scoping,
    inverted to assistant-text-only here)."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            if _TABLE_MARKER_RE.search(item.get("text", "") or ""):
                return True
    return False


def enumeration_recorded(records: list) -> bool:
    """True iff the session recorded a tile-enumeration artifact: a ``spawn_task``
    tool call, or an assistant message carrying the prescribed enumeration text.

    Session-global by design (documented ADR-088 limitation): one enumeration
    satisfies the gate for the session — the gate targets the *total skip*
    (merged, nothing recorded), not per-merge enumeration quality. A bare "no
    follow-ups" matches none of the markers and so does NOT satisfy it.

    The tool_use check delegates to ``session_spawned_tiles`` (review of PR
    #674) rather than re-implementing it, so this and the tile-table trigger
    (``evaluate_tile_table``) can never disagree on what counts as a spawn.
    """
    if session_spawned_tiles(records):
        return True
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text", "") or ""
            if any(rx.search(text) for rx in _ENUM_MARKERS):
                return True
    return False


def skip_override(records: list) -> bool:
    """True iff a genuine user message this session waived the checkpoint
    ("skip tiles" / "don't spawn tiles" / "no tiles"). Only real user-typed text
    is considered — never tool_result output that merely contains the phrase, and
    never a synthetic (``isMeta`` / ``isCompactSummary``) record such as a compact
    summary restating an earlier "skip tiles" mention (the workflow prompts
    /compact right after PR-create — review of PR #604).

    Text extraction + synthetic-record filtering are the shared ``_hookutil``
    helpers (dev-env#710), the same pair ``stop-journal-stub-checkpoint.py``
    uses — this hook previously inlined the identical logic."""
    for rec in records:
        if _is_synthetic_user(rec):
            continue
        if any(_SKIP_RE.search(t) for t in _user_message_texts(rec)):
            return True
    return False


def evaluate(records: list) -> tuple:
    """Return ``(fire_pr, resolved)``.

    ``fire_pr`` — the representative merged PR number to block on, or ``None``.
    ``resolved`` — True when a merge happened but the checkpoint is satisfied
    (enumerated, or waived): the caller writes the once-per-session sentinel so
    later Stops skip the re-scan. A session with no merge yet returns
    ``(None, False)`` so a merge later in the session is still caught.
    """
    calls = iter_bash_calls(records)
    merged = session_merged_prs(calls)
    if not merged:
        return None, False
    if skip_override(records) or enumeration_recorded(records):
        return None, True
    return min(merged), False


def evaluate_issues(records: list) -> tuple:
    """Return ``(fire_issue, resolved)`` for the dangling-created-issue
    trigger (ADR-092). Mirrors ``evaluate()``'s shape/semantics exactly, as a
    fully independent sibling — zero impact on ``evaluate()`` or its existing
    callers/tests.

    ``fire_issue`` — the lowest unresolved created-issue number to block on
    (deterministic across multiple dangling issues, mirroring ``evaluate()``'s
    lowest-PR determinism), or ``None``.
    ``resolved`` — True whenever there is nothing left dangling to fire on
    *and* an issue was created this session — covers both "enumerated/waived"
    (the direct ``evaluate()`` analog) and "every issue created this session
    was already resolved outright" (e.g. created then explicitly closed, with
    no merge anywhere — a state ``evaluate()`` has no equivalent of, since a
    PR merge is binary and has no separate "resolved another way" case). Both
    set the sentinel so later Stops skip the re-scan. Without this, a
    create-then-close session with no merge would never set the sentinel and
    would re-pay the full scan on every subsequent turn (review of PR #639).
    A session that created no issue at all returns ``(None, False)`` (nothing
    to resolve) so an issue created later is still caught.

    Recomputes ``iter_bash_calls``/``session_merged_prs`` independently rather
    than sharing ``evaluate()``'s — a deliberate simplicity-over-micro-
    optimization choice (see ADR-092's "Alternatives considered" section):
    keeping the two evaluators fully independent means neither's contract
    depends on how the other is invoked, at the cost of a second linear pass
    over an already-cheap transcript scan.
    """
    calls = iter_bash_calls(records)
    merged = session_merged_prs(calls)
    created = session_created_issues(calls)
    if not created:
        return None, False
    unresolved = set(created) - session_resolved_issue_numbers(calls, merged)
    if not unresolved:
        return None, True
    if skip_override(records) or enumeration_recorded(records):
        return None, True
    return min(unresolved), False


def evaluate_tile_table(records: list) -> tuple:
    """Return ``(payload, resolved)`` for the tiles-spawned-without-a-table
    trigger (ADR-094 addendum, dev-env#656) — a THIRD fully independent
    sibling to ``evaluate()``/``evaluate_issues()``, zero impact on either.

    ``payload`` — ``True`` iff a tile was spawned this session and neither the
    table marker nor a skip override is present; ``None`` otherwise. This
    trigger has nothing to carry into its formatter (unlike triggers 1/2,
    which carry a PR/issue number), so ``True`` is simply the not-``None``
    sentinel of the shared contract — see ``_TriggerSpec`` (dev-env#696).
    ``resolved`` — True whenever a tile was spawned and the table (or skip
    override) is present, so the caller marks the sentinel and later Stops
    skip the re-scan. A session that spawned no tile at all returns
    ``(None, False)`` (nothing to resolve) so a tile spawned later in the
    session is still caught.

    NOTE the asymmetry with triggers 1/2: a spawned tile satisfies
    ``enumeration_recorded`` (so ``evaluate()``/``evaluate_issues()`` can
    resolve on it), but does NOT by itself satisfy THIS trigger — the table
    marker is a stricter, separate bar. A session that merges a PR, spawns a
    tile, and never emits the table therefore sees trigger 1 resolve
    silently while trigger 3 still fires.
    """
    if not session_spawned_tiles(records):
        return None, False
    if skip_override(records) or table_marker_present(records):
        return None, True
    return True, False


def tile_shard_write_present(records: list) -> bool:
    """True iff this session evidences a tile-shard write (ADR-118, dev-env#870).

    Three places are scanned, and all three are needed:

    1. **A ``Write``/``Edit`` ``file_path``** — the write recipe explicitly offers the
       ``Write`` tool as an equally acceptable alternative to the shell serializer.
    2. **A Bash/PowerShell ``command``** — the ``py -3 -c '...json.dump(...)'`` form names
       the shard path inside the command text.
    3. **Bash tool ``output``** — the load-bearing one, and the reason this is not just an
       input scan. The documented recipe writes the payload through a *serializer script*,
       and a session with several tiles naturally writes one script that emits all of them
       (observed in the very session that shipped the reader: three shards written by one
       ``py -3 <script>`` invocation whose command text contained no ``tiles/`` path at
       all — only the script's own path). Scanning inputs alone would have blocked that
       session for a write it had correctly performed.

    Over-matching is deliberately preferred here: a stray ``tiles/12.json`` mention that is
    not a real write merely means the trigger does not fire, whereas a missed real write is
    a false block. Same reasoning as the permissive ``_TILE_SHARD_PATH_RE``.

    **Named limitation:** this detects the path, not the *verb*. A session that only reads,
    stages, or even deletes a shard (``cat``/``git add``/``rm`` naming a ``tiles/<N>.json``)
    reads as evidence of a write. Distinguishing intent from command text is exactly the
    unreliable inference this hook avoids elsewhere, and the failure it admits is the benign
    direction (a missed nudge, never a false block on compliant work). The write-time half —
    ``journal-shard-write-advisory.py``, which validates real on-disk bytes — is what catches
    a shard that exists but is wrong; this trigger only catches the total absence.
    """
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            inp = item.get("input")
            if not isinstance(inp, dict):
                continue
            for key in ("file_path", "command"):
                value = inp.get(key)
                if isinstance(value, str) and _TILE_SHARD_PATH_RE.search(value):
                    return True

    for _command, output in iter_bash_calls(records):
        if output and _TILE_SHARD_PATH_RE.search(output):
            return True
    return False


def evaluate_tile_shard(records: list) -> tuple:
    """Return ``(payload, resolved)`` for the tile-spawned-without-a-shard trigger
    (ADR-118 enforcement, dev-env#870) — a FIFTH independent sibling.

    ``payload`` — ``True`` iff a tile was spawned this session and neither a tile-shard
    write nor a skip override is present; ``None`` otherwise (the shared ``_TriggerSpec``
    contract, dev-env#696 — this trigger carries nothing into its formatter).
    ``resolved`` — True whenever a tile was spawned and the shard write (or skip override)
    is present. A session that spawned no tile returns ``(None, False)`` so a tile spawned
    later is still caught.

    Shares ``session_spawned_tiles`` with triggers 1/2/3, so "was a tile spawned" cannot
    drift between them. Like trigger 3 (the table), a spawned tile does NOT satisfy this
    trigger by itself — the shard is a separate, stricter bar, and the two guard different
    losses: the table is what tells the *user* a tile exists, the shard is what lets the
    *tile* be re-spawned after the chip dies.

    **Session-global, not per-tile.** ADR-118 describes this as "a ``spawn_task`` call with
    no corresponding shard write", but per-tile correspondence is not reliably detectable:
    ``spawn_task``'s input carries no issue number (only title/tldr/prompt/cwd), so a spawn
    cannot be matched to the shard filename that preserves it. Counting instead — N spawns
    must produce N shard paths — fails on the documented serializer-script recipe, where one
    Bash call legitimately writes several shards. So this fires only on the total skip (a
    tile spawned, no shard write evidenced anywhere), matching the session-global bar every
    other trigger here already uses. See ADR-118 Amendment 2.

    Blocking (exit 2), unlike trigger 4: whether a path was written is an objectively
    verifiable fact, not a natural-language judgment, so a fired trigger is actionable
    rather than a maybe-false alarm.
    """
    if not session_spawned_tiles(records):
        return None, False
    if skip_override(records) or tile_shard_write_present(records):
        return None, True
    return True, False


def deferral_question_present(records: list) -> bool:
    """True iff an assistant TEXT item this session matches one of the bounded
    ``_DEFERRAL_QUESTION_RES`` phrasings (new ADR, dev-env#772) — the
    "asked instead of tiling" anti-pattern. Only assistant text is scanned
    (mirrors ``table_marker_present``'s assistant-only scoping): a user
    message or a tool_result merely containing one of these phrases must
    never satisfy the trigger."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text", "") or ""
            if any(rx.search(text) for rx in _DEFERRAL_QUESTION_RES):
                return True
    return False


def evaluate_deferral(records: list) -> tuple:
    """Return ``(payload, resolved)`` for the deferral-question trigger (new
    ADR, dev-env#772) — a FOURTH fully independent sibling to
    ``evaluate()``/``evaluate_issues()``/``evaluate_tile_table()``, zero
    impact on any of the three.

    Scoped to sessions where a PR merged or an issue was created (the same
    contexts triggers 1/2 already key on) — a deferral-question phrase
    outside that context is far more likely to be an unrelated, legitimate
    question than an instance of this anti-pattern, and scoping keeps the
    false-positive surface bounded to sessions that already have
    follow-up-worthy activity.

    ``payload`` — ``True`` iff a deferral-question phrase was used this
    session (in a merge/issue-create context) and no skip override waives it;
    ``None`` otherwise (the shared ``_TriggerSpec`` contract, dev-env#696 —
    this trigger carries nothing into its formatter).

    Deliberately does NOT accept ``enumeration_recorded`` as resolution,
    unlike triggers 1-3 — this is the fix for the exact incident that
    motivated this trigger: in the motivating session, OTHER genuine
    follow-ups were correctly tiled in the very same turn the deferral
    question was asked about a DIFFERENT item (PR10). Since
    ``enumeration_recorded`` is session-global ("any spawn_task counts"),
    accepting it here would have let those unrelated spawns silently
    resolve this trigger without ever firing for the one utterance it
    exists to catch — the reviewed-and-rejected first draft of this
    function. The phrase's own presence already means, by this trigger's
    definition, that the deferred item was punted to the user as a question
    rather than given a proper "-> tiled" / "-> not tiled, because <reason>"
    disposition — that IS the violation, independent of what else happened
    in the session. Only an explicit "skip tiles" user override legitimately
    waives it (mirrors the other triggers' one universal escape hatch).
    Higher recall than the other triggers is an accepted tradeoff: this
    trigger is advisory, not blocking (see below), so a false positive costs
    the user a moment's glance at a systemMessage, not a blocked/looping
    session.

    ``resolved`` — True whenever the phrase is present and the skip override
    waives it, so the caller marks the sentinel and later Stops skip the
    re-scan. A session with neither a merge/issue-create context nor the
    phrase yet returns ``(None, False)`` so a later turn is still caught.

    Unlike triggers 1-3, a fired payload here does NOT block the stop via exit 2
    — see the module docstring's trigger-4 section and ``main()``'s emission
    logic: this is a natural-language pattern match, not an objectively
    verifiable fact, so it rides the advisory ``_hookout.emit_advisory``
    channel instead.
    """
    calls = iter_bash_calls(records)
    if not session_merged_prs(calls) and not session_created_issues(calls):
        return None, False
    if not deferral_question_present(records):
        return None, False
    if skip_override(records):
        return None, True
    return True, False


def session_asked_user(records: list) -> bool:
    """True iff a real ``AskUserQuestion`` tool call happened this session,
    checked via ``_ASK_USER_RE`` against a ``tool_use`` item's ``name``.

    The exact structural mirror of ``session_spawned_tiles`` (its bare-verb
    tool-name match, its assistant-record scoping, its early return) for the
    OTHER resolution path ADR-140 accepts: when no next thread is determinable,
    the ranked open-issue survey is put to the user via ``AskUserQuestion``
    rather than chained as a tile. Only a genuine tool_use satisfies it —
    assistant prose merely *describing* asking the user does not, exactly as
    enumeration text alone never substitutes for a real spawn in trigger 3.
    """
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and _ASK_USER_RE.search(item.get("name", "") or ""):
                return True
    return False


def prompt_is_chain_bearing(text: str) -> bool:
    """True iff *text* — a session's opening user prompt — already names the
    follow-on work the session was handed (an issue/PR reference, a github
    issues/pull URL, or the ``=== CHAIN`` marker).

    A pure text predicate so the "what counts as chained" decision is testable
    on its own, independent of transcript shape. Empty/blank text is NOT
    chain-bearing here; ``evaluate_workstream`` handles the unreadable-prompt
    case separately, because "no prompt found" and "a prompt that names nothing"
    warrant opposite treatments (see that function).
    """
    return bool(
        _has_chain_issue_reference(text)
        or _CHAIN_URL_RE.search(text)
        or _CHAIN_MARKER_RE.search(text)
    )


def _bash_call_record_indices(records: list) -> list:
    """The index into *records* of each Bash ``tool_use`` item's own assistant
    record, one entry per occurrence in transcript-encounter order (so a turn
    with N parallel Bash calls contributes N indices, possibly repeated).

    A narrow, POSITION-ONLY mirror of ``iter_bash_calls``'s own first walk (``for
    rec in records: ... for item in _content_items(rec): tool_use named "Bash"``)
    -- it performs no pairing and no merge detection of its own; that stays
    entirely delegated to ``session_merged_prs`` via ``_records_after_merge``,
    below. Callers must not assume this list is the same length as
    ``iter_bash_calls(records)``: an unresolved trailing Bash call (no
    tool_result yet at Stop time) is counted here but dropped there, which is
    exactly why ``_records_after_merge`` compares the two lengths before use.
    """
    indices = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "Bash"
            ):
                indices.append(i)
    return indices


def _records_after_merge(records: list, calls: list, merged: set) -> list:
    """The suffix of *records* at or after the point ``session_merged_prs``
    first detects evidence for any PR in *merged* -- so a resolution check
    scoped to this suffix only counts a ``spawn_task``/``AskUserQuestion`` that
    happened AFTER the merge, not one from earlier in the session for unrelated
    reasons that ``evaluate_workstream``'s unscoped check used to accept (review
    of PR #1053, dev-env#1044).

    Delegates ALL merge detection to ``session_merged_prs`` itself, re-invoked on
    successively longer prefixes of *calls* -- never reimplemented, so this
    cannot diverge on what counts as evidence (the same non-duplication argument
    ``evaluate_workstream``'s own docstring makes for reusing
    ``session_merged_prs`` in the first place). The record-index mapping
    (``_bash_call_record_indices``) is a position-only mirror of
    ``iter_bash_calls``'s walk; when its length disagrees with *calls* (a
    parallel-call turn, or a trailing unresolved Bash call -- both uncommon),
    this falls back to returning *records* unchanged rather than guessing --
    conservative in the SAME direction the caller already accepted before this
    fix, so the fallback only ever widens scope back to the prior, unscoped
    behavior, never introduces a new false block.

    Quadratic in the session's Bash-CALL count (re-running ``session_merged_prs``
    on each growing prefix), not in transcript bytes or total record count --
    paid only once per Stop, and only on the already-narrow subset where the
    caller has already confirmed *merged* is non-empty.
    """
    indices = _bash_call_record_indices(records)
    if len(indices) != len(calls):
        return records
    for k in range(1, len(calls) + 1):
        if session_merged_prs(calls[:k]) & merged:
            return records[indices[k - 1]:]
    return records


def evaluate_workstream(records: list) -> tuple:
    """Return ``(payload, resolved)`` for the unchained-merge trigger (ADR-140,
    dev-env#1044) — a SIXTH fully independent sibling to the five above.

    Fires when all four hold: a PR merged this session; the session's opening
    prompt is not chain-bearing; and no ``spawn_task``, ``AskUserQuestion``, or
    ``gh issue create`` ran AFTER that merge (see ``_records_after_merge`` --
    evidence from earlier in the session, for unrelated reasons, does not count).
    ``payload`` is then the lowest merged PR number (mirroring ``evaluate()``'s
    lowest-PR determinism, so a two-merge session blocks on a stable one);
    ``None`` otherwise.

    ``session_merged_prs`` is REUSED, not re-implemented — its direct-marker /
    REST-fallback / observed-auto-merge detection is the single source of truth
    for "a PR reached merged state", and a second copy here would be exactly the
    drift ADR-090 hoisted the shared transcript readers to prevent.

    **Three distinct ways to return ``(None, True)``, all of which mark the
    sentinel so later Stops skip the re-scan:**

    - ``skip_override`` — the universal escape hatch every trigger honors.
    - **Out of scope: the opening prompt is chain-bearing.** The session was
      handed its next thread; ADR-137's prose rule governs what it chains after
      that, not this hook. Marking this resolved is safe *because the first
      genuine user prompt is immutable for the session* — a chain-bearing
      session can never become unchained later, so there is nothing left to
      re-evaluate.
    - **Out of scope: no genuine opening prompt is readable at all.** The scope
      test cannot be established, so a blocking trigger must not fire on it —
      the conservative direction for a gate whose false positive costs a blocked
      stop. Same immutability argument, so also resolved rather than deferred.

    Deliberately does NOT accept ``enumeration_recorded`` as resolution, and for
    the same reason ``evaluate_deferral`` doesn't: enumeration TEXT ("-> not
    tiled, because ...") is what a session writes when it decides there is no
    follow-up to tile, and that decision is precisely what this trigger exists
    to question. A real ``spawn_task`` (the next thread chained), a real
    ``AskUserQuestion`` (the choice put to the user), or a same-session
    ``gh issue create`` (the CLAUDE.md-documented fallback for a session where
    ``spawn_task`` itself is unavailable, e.g. some terminal sessions -- "file
    the follow-up issue anyway") resolves it, each ONLY when it happens at or
    after the merge (review of PR #1053, dev-env#1044 -- the original check
    accepted any of the three from anywhere in the session, including before a
    merge it had nothing to do with). Note that a spawn therefore resolves this
    trigger while independently *arming* triggers 3 and 3b, which demand its
    table and its shard: the same asymmetry those two already have with
    trigger 1.

    A session with no merge yet returns ``(None, False)`` so a merge later in
    the session is still caught.
    """
    calls = iter_bash_calls(records)
    merged = session_merged_prs(calls)
    if not merged:
        return None, False
    if skip_override(records):
        return None, True
    prompt = first_user_prompt_text(records)
    if not prompt.strip() or prompt_is_chain_bearing(prompt):
        return None, True
    post_merge = _records_after_merge(records, calls, merged)
    if (
        session_spawned_tiles(post_merge)
        or session_asked_user(post_merge)
        or session_created_issues(iter_bash_calls(post_merge))
    ):
        return None, True
    return min(merged), False


def format_reminder(pr: int) -> str:
    """The exit-2 stderr message. ASCII-only: Claude Code pipes hook output as
    cp1252 on Windows, so a char outside it (an arrow, em-dash) would raise
    UnicodeEncodeError and the whole reminder would vanish — use ``->`` not the
    Unicode arrow (mirrors posttooluse-inert-advisory.py's ASCII constraint)."""
    return (
        f"[tile-enumeration-gate] PR #{pr} reached merged state this session, but no "
        "post-merge tile enumeration was recorded. Per ADR-046, before ending the turn "
        "write out the follow-ups you considered -- scan for out-of-scope fixes, deferred "
        "work, tech debt, and ideas noticed in passing, and record each as "
        "'-> tiled (task_id / #N)' or '-> not tiled, because <reason>', spawning a "
        "spawn_task tile for each genuine follow-up. \"No follow-ups\" is valid only as "
        "the visible result of that scan, never a bare assertion. Only an explicit "
        "\"skip tiles\" instruction anywhere in this session exempts this checkpoint."
    )


def format_issue_reminder(issue: int) -> str:
    """The exit-2 stderr message for the dangling-created-issue trigger
    (ADR-092). ASCII-only, same constraint as ``format_reminder`` (Claude Code
    pipes hook output as cp1252 on Windows)."""
    return (
        f"[tile-enumeration-gate] Issue #{issue} was created this session and is still "
        "open (no same-session merged PR closed it via a Closes/Fixes/Resolves keyword, "
        "and it was not explicitly closed), but no tile enumeration was recorded. Per "
        "ADR-046/ADR-092, before ending the turn write out the follow-ups you considered "
        "-- spawn a spawn_task tile to pick up this issue, or record '-> not tiled, "
        "because <reason>'. \"No follow-ups\" is valid only as the visible result of "
        "that scan, never a bare assertion. Only an explicit \"skip tiles\" instruction "
        "anywhere in this session exempts this checkpoint."
    )


def format_table_reminder(payload: object = None) -> str:
    """The exit-2 stderr message for the tiles-spawned-without-a-table
    trigger (ADR-094 addendum). ASCII-only, same constraint as the other
    ``format_*_reminder`` functions (Claude Code pipes hook output as
    cp1252 on Windows)."""
    return (
        "[tile-enumeration-gate] One or more spawn_task tiles were spawned this "
        "session, but no end-of-session tile table was found. Per the Session "
        "Summaries & Tile Tracking section of claude/CLAUDE.md (ADR-094), close "
        "with a table under the exact heading '### Tiles spawned this session' "
        "(columns: Tile | Issue | Status | Next) before ending the turn. Only an "
        "explicit \"skip tiles\" instruction anywhere in this session exempts "
        "this checkpoint."
    )


def format_shard_reminder(payload: object = None) -> str:
    """The exit-2 stderr message for the tile-spawned-without-a-shard trigger
    (ADR-118 enforcement, dev-env#870). ASCII-only, same cp1252 constraint as the
    other ``format_*_reminder`` functions."""
    return (
        "[tile-enumeration-gate] One or more spawn_task tiles were spawned this "
        "session, but no tile-shard write was found. A chip does not survive an app "
        "restart and there is no API to re-create one, so the shard "
        "sessions/<project>/tiles/<issue-number>.json IS the durable payload -- without "
        "it the paired issue survives but the one-click restart does not (ADR-118). "
        "Write one shard per tile now, filed under the tile's TARGET project (from its "
        "cwd), not the spawning session's. Required fields: issue, url, title, tldr, "
        "prompt, cwd, spawned. Build it with a JSON serializer or the Write tool -- never "
        "echo, since `prompt` is free prose that corrupts the shard or escapes into the "
        "shell when interpolated. Recipe: docs/REFERENCE.md -> Tile shards. Only an "
        "explicit \"skip tiles\" instruction anywhere in this session exempts this "
        "checkpoint."
    )


def format_workstream_reminder(pr: int) -> str:
    """The exit-2 stderr message for the unchained-merge trigger (ADR-140,
    dev-env#1044). ASCII-only, same cp1252 constraint as its blocking siblings.

    Points at claude/CLAUDE.md for the survey ranking rather than restating it,
    matching post-merge-tile-checkpoint.py's own reminder (review of PR #1053):
    ADR-140's own Rationale argues the ranking belongs in prose, not in a hook
    that reads no gh state, specifically BECAUSE it will keep evolving --
    restating it here would just be a second, driftable copy of the same list."""
    return (
        f"[tile-enumeration-gate] PR #{pr} merged in a session whose opening prompt named "
        "no follow-on work, and nothing was queued at or after the merge -- no spawn_task "
        "tile, no AskUserQuestion, and no gh issue create (the fallback for a session where "
        "spawn_task itself is unavailable). Per ADR-137/ADR-140, a merge is a look-ahead "
        "moment: if this work predictably surfaces or unblocks a next thread, tile it now "
        "(tracking issue + spawn_task + shard); only if none is determinable, survey and "
        "rank the repo's open issues per claude/CLAUDE.md -> Capture follow-ups as tiles, "
        "then offer the top options via AskUserQuestion and tile the choice. The survey ask "
        "is the ONE bounded carve-out to the don't-ask-just-tile rule and applies only when "
        "nothing is determinable -- never a substitute for chaining a follow-up you already "
        "know. Only an explicit \"skip tiles\" instruction anywhere in this session exempts "
        "this checkpoint."
    )


def format_deferral_reminder(payload: object = None) -> str:
    """The systemMessage text for the deferral-question trigger (new ADR,
    dev-env#772) -- advisory and USER-facing, unlike the other three
    ``format_*_reminder`` functions, which are model-facing exit-2 stderr.
    ASCII-only for consistency with the others, though the systemMessage
    JSON channel ``ensure_ascii``-escapes regardless of this function's own
    output (see ``_hookout.plan_emission``)."""
    return (
        "Heads up: this session's last response, after a merge or issue-create, "
        "used deferral/permission phrasing (e.g. 'let me know if you want me to "
        "start it now') about what looks like known follow-up work, instead of "
        "spawning a spawn_task tile for it. Per the tile-now discipline in "
        "claude/CLAUDE.md, that kind of known follow-up should be tiled "
        "directly rather than asked about. This is a heuristic text match and "
        "may be a false positive -- worth a quick look, not necessarily an error."
    )


class _PrefilterCtx(NamedTuple):
    """Transcript signals precomputed once and shared by every ``prefilter``.

    Each spec's ``prefilter`` answers one question -- "is this trigger's signal
    anywhere in the transcript?" -- and must be a cheap read of these fields,
    never its own scan: the whole point of the pre-filter is to skip the parse.
    """

    text: str
    lower: str
    has_merge_or_issue_signal: bool


class _TriggerSpec(NamedTuple):
    """One row per trigger -- the single place a trigger's identity lives.

    Before dev-env#696 each trigger was spread across five hand-synchronized
    sites in ``main()``: the ``_TRIGGERS`` tuple, its pre-filter clause, the
    evaluate block's skip-default, the sentinel-marking loop's ``fired`` test,
    and the message-building block. Two of those carried a silent asymmetry --
    a ``(None, False)`` skip-default for triggers 1/2 against ``(False, False)``
    for the rest, and a matching ``is not None`` against bare-truthiness
    ``fired`` test -- so a trigger could be added with a subtly wrong shape and
    no test would say so. Adding a trigger is now one row here.

    ``evaluate`` returns the uniform ``(payload, resolved)`` contract, where
    ``payload is None`` means "not firing". Triggers with nothing to carry
    return ``True``; triggers 1/2 return the PR/issue number their formatter
    needs. ``formatter`` therefore takes exactly one argument for every trigger
    (the three that ignore it default it to ``None``, so the no-arg call the
    tests use still works).

    ``blocking`` decides both channels at once, which is what makes trigger 4's
    old hand-written special case principled: a blocking trigger emits exit-2
    stderr and marks its FIRED sentinel in the shared loop, while a non-blocking
    (advisory) one emits a ``systemMessage`` and marks FIRED only at the point
    of emission. Marking an advisory trigger in the shared loop would set its
    sentinel even on a turn where a co-firing blocking trigger preempts it via
    the early ``sys.exit(2)``, permanently silencing a condition that ADR-109
    promises "can still surface on a later Stop once the harder trigger
    resolves". The RESOLVED mark is unconditional for every trigger -- that
    state is stable regardless of what else fires this turn.
    """

    key: str           # sentinel suffix; one of the _TRIGGER_* constants above
    evaluate: object   # (records) -> (payload, resolved)
    prefilter: object  # (_PrefilterCtx) -> bool
    formatter: object  # (payload) -> str
    blocking: bool     # True: exit-2 stderr (model). False: systemMessage (user).


# Order is the order blocking messages are joined into the exit-2 stderr body.
_TRIGGER_SPECS = (
    _TriggerSpec(
        key=_TRIGGER_PR,
        evaluate=evaluate,
        prefilter=lambda c: "merged" in c.lower,
        formatter=format_reminder,
        blocking=True,
    ),
    _TriggerSpec(
        key=_TRIGGER_ISSUE,
        evaluate=evaluate_issues,
        prefilter=lambda c: bool(_ISSUE_CREATE_STMT_RE.search(c.text)),
        formatter=format_issue_reminder,
        blocking=True,
    ),
    _TriggerSpec(
        key=_TRIGGER_TABLE,
        evaluate=evaluate_tile_table,
        prefilter=lambda c: _SPAWN_TASK_SUBSTRING in c.lower,
        formatter=format_table_reminder,
        blocking=True,
    ),
    _TriggerSpec(
        key=_TRIGGER_SHARD,
        evaluate=evaluate_tile_shard,
        prefilter=lambda c: _SPAWN_TASK_SUBSTRING in c.lower,
        formatter=format_shard_reminder,
        blocking=True,
    ),
    _TriggerSpec(
        key=_TRIGGER_WORKSTREAM,
        evaluate=evaluate_workstream,
        # Reuses trigger 1's own signal verbatim -- evaluate_workstream fires
        # only when session_merged_prs() is non-empty, so this is a superset for
        # exactly the reason that clause is (every merge signal contains
        # "merged" case-insensitively). No new scan cost: `lower` is computed
        # once in main() and shared via _PrefilterCtx.
        prefilter=lambda c: "merged" in c.lower,
        formatter=format_workstream_reminder,
        blocking=True,
    ),
    _TriggerSpec(
        key=_TRIGGER_DEFER,
        evaluate=evaluate_deferral,
        prefilter=lambda c: (
            c.has_merge_or_issue_signal
            and any(sub in c.lower for sub in _DEFER_PREFILTER_SUBSTRINGS)
        ),
        formatter=format_deferral_reminder,
        blocking=False,
    ),
)

_TRIGGERS = tuple(spec.key for spec in _TRIGGER_SPECS)


# --- I/O (thin, untested per the pure-helper convention) -----------------------

def _trigger_sentinel_path(trigger: str, session_id: str) -> Path:
    """The per-trigger sentinel path for *trigger* (one of ``_TRIGGERS``) and
    *session_id* (ADR-097, dev-env#677)."""
    return _hookutil.sentinel_path(f"{SENTINEL_PREFIX}{trigger}", session_id)


def _mark_trigger_fired(trigger: str, session_id: str) -> None:
    if not session_id:
        return
    try:
        _hookutil.SCRATCH.mkdir(exist_ok=True)
        _trigger_sentinel_path(trigger, session_id).write_text("")
    except Exception:
        pass


def main() -> None:
    _hookutil.record_heartbeat("stop-tile-enumeration-gate")
    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)

    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    # Loop guard: once this hook has already blocked and Claude is continuing,
    # never block again (Claude Code hooks reference — check stop_hook_active).
    if data.get("stop_hook_active"):
        sys.exit(0)

    session_id = data.get("session_id") or ""
    # Per-trigger sentinels (ADR-097, dev-env#677): each trigger's "already
    # fired or resolved" state is tracked independently, so a trigger whose
    # condition arises later in the session -- after a SIBLING trigger already
    # set its own sentinel -- is still evaluated. Only when EVERY trigger's
    # sentinel is already set (the set is derived from _TRIGGER_SPECS, so this
    # needs no edit when a trigger is added) is there genuinely nothing left to
    # check this session; skip
    # even reading the transcript (the fully-resolved-session fast path the
    # original single shared sentinel also had).
    already_done = {
        t: bool(session_id and _trigger_sentinel_path(t, session_id).exists())
        for t in _TRIGGERS
    }
    if session_id and all(already_done.values()):
        sys.exit(0)

    tpath_str = data.get("transcript_path") or ""
    tpath = Path(tpath_str) if tpath_str else None
    if (tpath is None or not tpath.exists()) and session_id:
        tpath = _hookutil.find_transcript(session_id)
    if tpath is None or not tpath.exists():
        sys.exit(0)

    try:
        text = tpath.read_text(encoding="utf-8")
    except Exception:
        sys.exit(0)
    # Cheap pre-filter: every merge signal this hook detects contains the
    # substring "merged" case-insensitively — gh's "... merged pull request",
    # `"state":"MERGED"`, `"merged":true`. Every dangling-issue signal requires
    # a `gh issue create` to have run this session (ADR-092) — reuse the exact
    # detection regex (`.search()`, not the per-segment `.match()` the real
    # detector uses) rather than a hand-written substring, so the guard can
    # never drift from what the detector actually matches (a literal
    # single-space `"issue create"` substring check would silently miss a
    # tab/multi-space invocation the real `\s+`-based regex still matches --
    # review of PR #639, confirmed independently by both reviewers). The
    # resolution side (Closes-keyword / `gh issue close`) only ever matters
    # when there is a created issue to resolve in the first place, so gating
    # on creation alone is still sufficient for both trigger halves.
    #
    # Trigger 3 (tiles-spawned-without-a-table, ADR-094 addendum) requires a
    # spawn_task tool call, which is JSON-recorded with a tool name containing
    # "spawn_task" -- a bare substring check (matching what the real detector,
    # _SPAWN_TASK_RE, itself matches) is a third standalone OR-branch rather
    # than folded into either regex above.
    #
    # Trigger 5 (unchained merge, ADR-140, dev-env#1044) shares trigger 1's
    # "merged" clause verbatim rather than adding one -- it fires only when
    # session_merged_prs() is non-empty, so the same superset argument holds and
    # the pre-filter costs nothing new. Its own narrowing conditions (the opening
    # prompt, the spawn/ask resolution paths) are evaluator-side, deliberately:
    # a pre-filter clause may only READ _PrefilterCtx, never scan.
    #
    # Trigger 4 (deferral-question, new ADR, dev-env#772) requires one of the
    # _DEFER_PREFILTER_SUBSTRINGS to be present -- see that tuple's own
    # comment for why it's a true superset of _DEFERRAL_QUESTION_RES -- AND
    # (unlike the other three clauses) additionally requires the SAME
    # merge/issue-create signal the "merged"/issue-create clauses already
    # check. This is still a safe superset: evaluate_deferral() itself
    # returns (False, False) immediately unless session_merged_prs or
    # session_created_issues is non-empty (its own scoping decision, see
    # evaluate_deferral's docstring), so a transcript with neither signal can
    # never fire trigger 4 regardless of phrasing. Without this addition
    # (review finding), any session merely containing "should i "/"want me
    # to" -- common conversational phrases, unlike the other three clauses'
    # rare command/tool markers -- would force a full reparse on every Stop
    # even with no merge or issue-create anywhere in the transcript, defeating
    # the ADR-097 fast path for the overwhelmingly common no-signal case.
    #
    # Each clause is gated on that trigger's OWN already_done state (ADR-097
    # review, dev-env#677 follow-up): a transcript signal never disappears once
    # written (e.g. "merged" stays present for the rest of the session after the
    # PR trigger fires), so without this gate a session with an early merge and
    # no further issue/tile activity would re-parse the whole, ever-growing
    # transcript on every remaining Stop for the rest of the session -- the
    # common case, not a narrow edge case. Gating on already_done restores the
    # skip-the-parse fast path the moment every trigger with a live signal in
    # the transcript is either resolved or genuinely absent, while a
    # not-yet-resolved trigger's own clause is unaffected (reduces to the
    # original unconditional check).
    lower = text.lower()
    ctx = _PrefilterCtx(
        text=text,
        lower=lower,
        has_merge_or_issue_signal=(
            "merged" in lower or bool(_ISSUE_CREATE_STMT_RE.search(text))
        ),
    )
    if all(already_done[spec.key] or not spec.prefilter(ctx) for spec in _TRIGGER_SPECS):
        sys.exit(0)

    # Fail-open: a parse/scan failure is a deliberate exit-0 skip, not an
    # accident of the outer __main__ guard (review of PR #604). A trigger
    # whose sentinel is already set is skipped entirely here -- not
    # re-evaluated, never re-fired -- which preserves ADR-088's accepted
    # "session-global enumeration" limitation on a PER-TRIGGER basis (e.g. a
    # session merging two PRs and enumerating only once still isn't
    # re-flagged for the second, exactly as before ADR-097).
    try:
        records = _parse_records(text)
        # One uniform skip-default for every trigger, now that all six share
        # the (payload, resolved) contract -- the (None, False) vs (False,
        # False) split this replaced was the dev-env#696 asymmetry.
        results = {
            spec.key: (
                (None, False) if already_done[spec.key] else spec.evaluate(records)
            )
            for spec in _TRIGGER_SPECS
        }
    except Exception:
        sys.exit(0)

    # Mark each trigger's own sentinel BEFORE emitting, so a re-entrant Stop
    # cannot double-block (mirrors the original single-sentinel ordering). An
    # ADVISORY trigger's FIRED mark is deliberately excluded here and made at
    # the point of emission instead -- see _TriggerSpec's docstring for why
    # (marking it here would let a co-firing blocking trigger's early exit(2)
    # permanently silence a condition ADR-109 promises "can still surface on a
    # later Stop once the harder trigger resolves"). The RESOLVED mark is
    # unconditional -- that state is stable regardless of what else fires.
    for spec in _TRIGGER_SPECS:
        payload, resolved = results[spec.key]
        if (payload is not None and spec.blocking) or resolved:
            _mark_trigger_fired(spec.key, session_id)

    messages = [
        spec.formatter(results[spec.key][0])
        for spec in _TRIGGER_SPECS
        if spec.blocking and results[spec.key][0] is not None
    ]

    if messages:
        sys.stderr.write("\n\n".join(messages) + "\n")
        sys.exit(2)

    # Advisory triggers never contribute to the blocking exit-2 path above (see
    # the module docstring's trigger-4 section) -- only reached when no blocking
    # trigger fired, so there is no exit-code conflict with the advisory channel
    # (_hookout.emit_advisory always exits 0 here). emit_advisory is also
    # NoReturn, so at most one advisory is delivered per Stop; with exactly one
    # advisory trigger today that is the pre-dev-env#696 behavior unchanged, and
    # a second one would surface on a later Stop rather than being lost, since
    # its sentinel is only marked on the turn it actually emits.
    for spec in _TRIGGER_SPECS:
        payload, _resolved = results[spec.key]
        if spec.blocking or payload is None:
            continue
        _mark_trigger_fired(spec.key, session_id)
        _hookout.emit_advisory("Stop", spec.formatter(payload), audience="user")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
