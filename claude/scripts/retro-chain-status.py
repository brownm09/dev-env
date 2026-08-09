#!/usr/bin/env python3
"""Read-only per-repo classifier for the chained-tile retro-action backlog refill mechanism
(dev-env#967, ADR-131).

## Background

A biweekly-retro run files a per-repo "queue" GitHub issue (label `retro-action`, a
markdown checklist of action items). A 2026-08-08 session manually seeded one "chained
tile" per repo: `spawn_task` a tile for the top unchecked checklist item, anchored to a
dedicated issue (ADR-094), with a tile shard (ADR-118) carrying a "CHAIN block" instructing
the *next* session to tick the item, pick the next unchecked one, and spawn the next link
itself. This mechanism was entirely **prompt-carried** -- it breaks silently and
permanently on a dismissed chip, a compacted session, an early exit, an API failure, or the
item being finished by hand outside the tile.

This script is the idempotent, stateless liveness check behind the self-healing fix: given
a repo, it answers "is there an open, unstarted retro-action chain tile? If not, what's the
current top unchecked item, and is there already a trusted anchor issue for it?" It never
mutates anything -- filing an issue, spawning a tile, and writing a shard are all session-
only actions (`gh issue create`, `spawn_task`, the Write tool) that only the
`retro-chain-refill` skill (invoked by a live Claude session) can perform. See ADR-131 for
why the script/skill split is drawn exactly here: everything in this file is deterministic
and offline-testable; the skill supplies the one thing a script structurally cannot --
`list_sessions` and `spawn_task` are session-only MCP tools.

## Real-world messiness this classifier accounts for (not hypothetical -- found live during
## the design of this mechanism)

  - The `retro-action` label always lives on the queue/checklist issue, but only on the
    anchor (work-item) issue when that anchor was freshly filed -- a repo can (and does)
    have several open `retro-action`-labeled issues at once (career-playbook: 5; dev-env:
    7, at design time). The queue issue is identified **structurally** (`is_queue_body`:
    does the body contain a real markdown checklist?), not by label or title alone.
  - "Top unchecked item" is scoped to real `- [ ]` checklist lines, never free-form
    "Escalation" bullets (`- **bold**` prose citing already-tracked older issues) -- a
    deliberate exclusion (see ADR-131), not an oversight.
  - A checklist item's inline `#NNN` reference can resolve to an already-merged pull
    request rather than an open issue (confirmed live: `merickvaughn/lifting-logbook`'s
    seeded anchor `#814`). `extract_bare_issue_refs` only *extracts* a candidate number;
    the caller (this script's `main()`, or the `retro-chain-refill` skill) must still
    verify it resolves to an OPEN, non-PR issue via `_gh_issue_state.check_issue_state`
    before trusting it -- never assume a cited number is valid.
  - A candidate issue can already have an unrelated tile shard sitting at that path (a real
    incident: `win11-init-tools/tiles/55.json`, 2026-07-22, silently clobbered by hand).
    `classify_repo_status` walks unchecked items in order and skips any whose candidate
    issue already has a shard on disk, rather than ever recommending an overwrite.

## Output contract

Reads no stdin, writes nothing. `main()` prints one JSON object to stdout, keyed by the
`owner/repo` strings passed via `--repo`, and always exits 0 -- a per-repo failure lands as
that repo's own `{"status": "ERROR", "error": "..."}`, never aborting the batch (mirrors
`reconcile-project-board.py`'s per-repo isolation). Each repo's value has a `status` of one
of:

  - `ALIVE` -- a chain-tagged tile shard exists and its issue is confirmed OPEN. No refill.
  - `UNRESOLVED` -- a chain-tagged shard exists but its issue's live state could not be
    confirmed (a `gh` failure). Conservative: do not refill this round rather than risk a
    duplicate spawn against a chain that might still be alive.
  - `NO_QUEUE_FOUND` -- no live chain shard, and no open `retro-action` issue with a
    checklist body was found for this repo.
  - `QUEUE_EXHAUSTED` -- a queue issue was found but every checklist item is already
    checked.
  - `ALL_TILED` -- a queue issue with unchecked items was found, but every unchecked item's
    inline issue reference already has a shard on disk (already tiled or in flight).
  - `AMBIGUOUS` -- a queue issue with unchecked items was found, but an untagged (non-chain)
    shard was spawned on or after the reference point below -- it may already cover the top
    item. The classifier does not guess; report for a session's own judgment (and a
    `list_sessions` cross-check) rather than risk a duplicate spawn. The reference point is the
    most recent chain-tagged shard's own `spawned` date when one exists (even a CLOSED one --
    the last confirmed chain activity), or the queue issue's creation date only when this
    project has never had a chain shard for this queue at all. Narrower than "the queue's
    creation date" unconditionally: a queue issue lives for roughly two weeks between biweekly
    runs, and scoping every check to that whole window would flag routine, unrelated tile
    activity as ambiguous for nearly the queue's entire life.
  - `NEEDS_REFILL` -- the current actionable item: `item_text`, and `candidate_issue` (an
    int, or `null` if the item cited no inline `#NNN`). This script does **not** validate
    `candidate_issue` (resolve it to an OPEN, non-PR issue) -- the caller must do that live,
    at mutation time, regardless of what this classification found: classification and
    mutation happen at different moments, and this repo's state moves fast enough between
    them that a stale validity check would be actively misleading, not merely redundant.
  - `ERROR` -- the `--repo` value failed shape validation, or an unexpected exception was
    raised while processing this repo; `error` names what happened.

Usage:
    py -3 claude/scripts/retro-chain-status.py \\
        --repo brownm09/career-playbook --repo brownm09/dev-env \\
        [--journal-repo C:/Users/brown/Git/engineering-journal]
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from _journal_shards import iter_tile_shards, shard_number
from _gh_issue_state import (
    GH_CALL_TIMEOUT,
    check_issue_state,
    is_closed,
    repo_from_issue_url,
)

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"

# The status vocabulary, defined once so code, tests, and callers can never drift apart on the
# literal strings (found already drifted once within this same PR -- an early draft of
# ADR-131 described this as "a five-way decision table" after the implementation had already
# grown to seven statuses; see the module docstring above for what each one means).
STATUS_ALIVE = "ALIVE"
STATUS_UNRESOLVED = "UNRESOLVED"
STATUS_NO_QUEUE_FOUND = "NO_QUEUE_FOUND"
STATUS_QUEUE_EXHAUSTED = "QUEUE_EXHAUSTED"
STATUS_ALL_TILED = "ALL_TILED"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_NEEDS_REFILL = "NEEDS_REFILL"
STATUS_ERROR = "ERROR"

# Unique substring of the CHAIN block's header line, confirmed present verbatim in every
# 2026-08-08 seeded tile's `prompt` field. Some historical shards predate the `chain` field
# being written consistently (ADR-118 Amendment 6) -- this signature is the fallback that
# still recognizes them as chain tiles.
CHAIN_BLOCK_SIGNATURE = "CHAIN (do this before you finish"

# The CHAIN block's actual header line, anchored to the start of a line -- deliberately
# stricter than a bare `CHAIN_BLOCK_SIGNATURE in prompt` substring test, which would also
# match a tile that merely quotes or discusses the block in ordinary prose (e.g. a follow-up
# tile about this very mechanism). re.MULTILINE makes `^` match after any `\n`, not only at
# the start of the whole string.
_CHAIN_BLOCK_HEADER_RE = re.compile(r"^===\s*CHAIN\s*\(do this before you finish", re.MULTILINE)

# A real markdown checklist line: optional leading whitespace, a GFM list marker (`-`, `*`, or
# `+` -- all three are valid task-list bullets), a checkbox, then the item text. Deliberately
# does not match an "Escalations" bullet (`- **bold** ...`), which carries no `[ ]`/`[x]` at
# all -- confirmed against the real dev-env#963 body during design.
_CHECKLIST_RE = re.compile(r"^[ \t]*[-*+][ \t]+\[([ xX])\][ \t]+(.*)$")

_FENCE_RE = re.compile(r"^[ \t]*```")

# A bare `#NNN` reference -- not preceded by a word character (so `dev-env#945`-style
# cross-repo mentions are excluded -- a real false positive found in the live dev-env#963 body
# during design; the char immediately before `#` there is `v`, which `\w` correctly rejects)
# or `[` (so the visible text of a markdown link, `[#945](url)`, is excluded too -- `[` is not
# a word character, so without this the lookbehind alone would still match it).
_BARE_ISSUE_REF_RE = re.compile(r"(?<![\w\[])#(\d+)\b")


# --- pure helpers (unit-tested in tests/test_retro_chain_status.py) -----------


def parse_checklist(body) -> list[tuple[bool, str]]:
    """Every `- [ ]`/`- [x]` (or `*`/`+`) line in *body*, as `(checked, text)`, in document
    order.

    Lines inside fenced code blocks (```` ``` ````) are skipped -- a checklist-shaped line
    quoted inside an example would otherwise be misread as a real item, mirroring
    `_composed_output_scan.py`'s fence-awareness (ADR-121), this repo's own precedent for
    exactly this class of bug. Fence-skipping only activates when the body's fence markers
    balance (an even count) -- an unterminated fence (a stray ```` ``` ```` in prose, a
    truncated body) makes it impossible to tell which lines were meant to be "inside," so
    fence-awareness is disabled entirely for that body rather than silently discarding every
    checklist item after the unterminated marker. A non-string *body* yields `[]` rather than
    raising.
    """
    if not isinstance(body, str):
        return []
    lines = body.splitlines()
    fence_count = sum(1 for line in lines if _FENCE_RE.match(line))
    fence_aware = fence_count % 2 == 0
    items: list[tuple[bool, str]] = []
    in_fence = False
    for line in lines:
        if fence_aware and _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if fence_aware and in_fence:
            continue
        m = _CHECKLIST_RE.match(line)
        if m:
            checked = m.group(1).lower() == "x"
            items.append((checked, m.group(2).strip()))
    return items


def is_queue_body(body) -> bool:
    """True iff *body* contains at least one real checklist line.

    This is the structural test that tells a queue/checklist issue apart from a single-item
    anchor issue -- deliberately not a title-string match, since a repo's queue-issue title
    convention ("Biweekly retro YYYY-MM-DD -- action items") is a secondary signal at best
    and this test is robust to it changing.
    """
    return len(parse_checklist(body)) > 0


def find_queue_issue(issues) -> dict | None:
    """The newest (by `created_at`) issue in *issues* whose body is a queue, or `None`.

    *issues* is assumed already filtered to open, `retro-action`-labeled issues for one
    repo -- multiple can legitimately be open at once (a repo's queue issues accumulate
    across retro runs rather than being edited in place), so "newest" is load-bearing, not
    redundant. `created_at` is compared as the raw ISO-8601 string GitHub emits
    (`"2026-08-08T13:14:30Z"`); that format sorts lexicographically in chronological order,
    so no datetime parsing is needed.
    """
    if not isinstance(issues, list):
        return None
    candidates = [i for i in issues if isinstance(i, dict) and is_queue_body(i.get("body"))]
    if not candidates:
        return None
    return max(candidates, key=lambda i: str(i.get("created_at") or ""))


def extract_bare_issue_refs(text) -> list[int]:
    """Every bare `#NNN` reference in *text*, in order of first appearance, deduped.

    "Bare" excludes anything immediately preceded by a word character (so `dev-env#945` is
    not read as issue 945 of the current repo) -- see the module-level regex comment for the
    live false-positive this guards against. A non-string *text* yields `[]`.
    """
    if not isinstance(text, str):
        return []
    seen: list[int] = []
    for m in _BARE_ISSUE_REF_RE.finditer(text):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def chain_field(entry) -> dict | None:
    """*entry*'s `chain` field, if present and its `queue_issue` is a well-formed GitHub
    issue/PR URL (per `_gh_issue_state.repo_from_issue_url`); else `None`.

    Optional field, formalized by ADR-118 Amendment 6 -- most historical shards (including
    most non-chain tiles) simply lack it, which is expected and not itself a problem.
    """
    if not isinstance(entry, dict):
        return None
    chain = entry.get("chain")
    if not isinstance(chain, dict):
        return None
    queue_issue = chain.get("queue_issue")
    if not isinstance(queue_issue, str) or repo_from_issue_url(queue_issue) is None:
        return None
    return chain


def is_chain_shard(entry) -> bool:
    """True if *entry* is recognizably a link in the chain mechanism.

    Two independent signals, either sufficient: a valid `chain` field (ADR-118 Amendment 6,
    going forward), or the CHAIN block's header line, anchored to the start of a line, inside
    `prompt` (present in every 2026-08-08 shard, which predates the `chain` field). The header
    match is deliberately anchored rather than a bare substring test -- an unanchored match
    would also fire on a tile that merely quotes or discusses the block in ordinary prose (for
    example, a follow-up tile about this very mechanism), misclassifying an unrelated tile as a
    live chain link. Checking both signals means a historical shard missing the newer field is
    still recognized correctly.
    """
    if chain_field(entry) is not None:
        return True
    prompt = entry.get("prompt") if isinstance(entry, dict) else None
    return isinstance(prompt, str) and bool(_CHAIN_BLOCK_HEADER_RE.search(prompt))


def newest_chain_shard(numbered_entries) -> dict | None:
    """The chain-tagged shard that was spawned most recently, as
    `{"issue": N, "entry": {...}}`.

    *numbered_entries* is `[(issue_number, entry), ...]`. Keyed by each shard's own `spawned`
    date (a `TILE_REQUIRED_FIELDS` member, ADR-118) -- tie-broken by issue number -- rather
    than issue number alone. Issue number is not a safe recency proxy here: per ADR-131, four
    of six chained repos anchor a chain link on a *pre-existing* issue, which is frequently
    lower-numbered than the link before it, so "the chain shard with the highest issue number"
    can pick a stale, superseded link over the genuinely newest one. A shard whose `spawned`
    is missing or malformed (defensive -- ADR-118 requires it) sorts as the earliest possible
    value so it never wins a tie against a shard that has one. Returns `None` if no entry is
    chain-tagged.
    """
    chained = [(n, e) for n, e in numbered_entries if is_chain_shard(e)]
    if not chained:
        return None

    def _recency_key(pair: tuple[int, dict]) -> tuple[str, int]:
        n, e = pair
        spawned = e.get("spawned") if isinstance(e, dict) else None
        return (spawned if isinstance(spawned, str) else "", n)

    n, e = max(chained, key=_recency_key)
    return {"issue": n, "entry": e}


def classify_repo_status(
    chain_candidate: dict | None,
    chain_issue_state: str | None,
    open_labeled_issues: list,
    known_shard_issues,
    other_shard_dates,
) -> dict:
    """The decision table at the heart of this module. Pure -- every input is already
    resolved by the caller (no `gh` calls, no filesystem reads happen here).

    Args:
      chain_candidate: `newest_chain_shard(...)`'s result, or `None` if this project has no
        chain-tagged shard at all.
      chain_issue_state: `'OPEN'` / `'CLOSED'` / `None` for `chain_candidate["issue"]`
        (already looked up by the caller); ignored when `chain_candidate` is `None`.
      open_labeled_issues: open, `retro-action`-labeled issues for this repo, each a dict
        with at least `number`, `body`, `created_at` (already fetched by the caller).
      known_shard_issues: a set of every issue number that currently has ANY tile shard on
        disk in this project (chain-tagged or not) -- the "already tiled, don't clobber"
        collision check.
      other_shard_dates: `[(issue_number, spawned_date_str), ...]` for shards that are NOT
        chain-tagged -- the same-window signal behind `AMBIGUOUS`.

    Returns a dict with at least a `status` key; see the module docstring for the full
    status vocabulary and each status's extra fields.
    """
    if chain_candidate is not None:
        if chain_issue_state == "OPEN":
            return {"status": STATUS_ALIVE, "chain_issue": chain_candidate["issue"], "notes": []}
        if chain_issue_state is None:
            return {
                "status": STATUS_UNRESOLVED,
                "chain_issue": chain_candidate["issue"],
                "notes": ["could not confirm this chain tile's issue state -- not refilling this round"],
            }
        if not is_closed(chain_issue_state):
            # An unrecognized state -- neither OPEN, unconfirmable (None), nor a value
            # `_gh_issue_state.is_closed` recognizes as closed. Treat the same as UNRESOLVED
            # rather than assuming CLOSED: guessing wrong in the "still alive" direction risks
            # the duplicate spawn this whole mechanism exists to prevent.
            return {
                "status": STATUS_UNRESOLVED,
                "chain_issue": chain_candidate["issue"],
                "notes": [f"chain issue state {chain_issue_state!r} not recognized -- not refilling this round"],
            }
        # else: CLOSED -- the most recent link finished; fall through to look for the next one.

    queue = find_queue_issue(open_labeled_issues)
    if queue is None:
        return {"status": STATUS_NO_QUEUE_FOUND, "notes": []}

    items = parse_checklist(queue.get("body"))
    unchecked = [text for checked, text in items if not checked]
    if not unchecked:
        return {"status": STATUS_QUEUE_EXHAUSTED, "queue_issue": queue.get("number"), "notes": []}

    queue_created = str(queue.get("created_at") or "")
    queue_created_date = queue_created[:10]  # "YYYY-MM-DD" prefix of the ISO-8601 timestamp
    # The AMBIGUOUS reference point: the most recent chain-tagged shard's own `spawned` date
    # when one exists (even a CLOSED one -- it's still the last confirmed chain activity), or
    # the queue's creation date only when this project has never had a chain shard for this
    # queue at all. A queue issue lives roughly two weeks between biweekly runs; scoping every
    # check to that whole window (as opposed to since the chain last actually moved) would flag
    # routine, unrelated tile activity as ambiguous for nearly the queue's entire life.
    window_start = queue_created_date
    if chain_candidate is not None:
        chain_spawned = chain_candidate["entry"].get("spawned")
        if isinstance(chain_spawned, str) and chain_spawned:
            window_start = chain_spawned
    same_window = sorted({
        n for n, spawned in other_shard_dates
        if isinstance(spawned, str) and spawned >= window_start
    })
    if same_window:
        return {
            "status": STATUS_AMBIGUOUS,
            "queue_issue": queue.get("number"),
            "notes": [
                f"untagged shard(s) for issue(s) {same_window} spawned on/after "
                f"{window_start} -- may already cover the top item; not guessing"
            ],
        }

    for text in unchecked:
        refs = extract_bare_issue_refs(text)
        candidate = refs[0] if refs else None
        if candidate is not None and candidate in known_shard_issues:
            continue  # already tiled at this exact issue number -- try the next item
        return {
            "status": STATUS_NEEDS_REFILL,
            "queue_issue": queue.get("number"),
            "item_text": text,
            "candidate_issue": candidate,
            "notes": [],
        }

    return {
        "status": STATUS_ALL_TILED,
        "queue_issue": queue.get("number"),
        "notes": ["every unchecked item already has a shard at its referenced issue number"],
    }


# --- network boundary (not unit-tested; repo avoids subprocess mocks) ---------


def fetch_open_labeled_issues(repo: str, label: str, per_page: int = 100, max_pages: int = 2,
                              timeout: int = GH_CALL_TIMEOUT) -> list[dict] | None:
    """REST `GET /repos/<repo>/issues?labels=<label>&state=open` -> open, non-PR issues
    carrying *label*, each `{number, title, body, created_at}`. `None` only when not even
    the first page was read; a later page's failure returns whatever was already collected
    -- a partial list can only omit a candidate queue issue, never fabricate one.

    The jq projection marks each row `is_pr` (mirroring `_gh_issue_state.issue_states_from_rows`'s
    marker-preserving shape) rather than dropping PR rows server-side -- dropping them server-side
    would shrink a full raw page below *per_page* whenever it happened to contain a labeled PR,
    and the short-page-stop check below would then wrongly conclude it had reached the last page.
    PR rows are filtered out in Python, after the raw page length has already been used for the
    stop decision.

    *label* is URL-encoded (`urllib.parse.quote`) before being interpolated into the query
    string -- a label containing a space or `&` would otherwise silently change the query
    rather than erroring.

    Not unit-tested -- subprocess boundary, matching `_gh_issue_state.fetch_repo_issue_states`'s
    convention. Everything that consumes this call's output (`find_queue_issue`,
    `is_queue_body`, `parse_checklist`) is fully covered offline.
    """
    projection = '[.[] | {number, title, body, created_at, is_pr: has("pull_request")}]'
    issues: list[dict] = []
    read_a_page = False
    encoded_label = quote(label, safe="")
    for page in range(1, max_pages + 1):
        try:
            result = subprocess.run(
                ["gh", "api",
                 f"repos/{repo}/issues?labels={encoded_label}&state=open&per_page={per_page}&page={page}",
                 "--jq", projection],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                break
            rows = json.loads(result.stdout)
        except Exception:
            break
        if not isinstance(rows, list):
            break
        read_a_page = True
        raw_page_len = len(rows)
        issues.extend(
            {"number": r.get("number"), "title": r.get("title"),
             "body": r.get("body"), "created_at": r.get("created_at")}
            for r in rows if isinstance(r, dict) and not r.get("is_pr")
        )
        if raw_page_len < per_page:
            break
    if not read_a_page:
        return None
    return issues


def classify_one_repo(repo: str, journal_repo: Path) -> dict:
    """All the I/O + classification for one repo, isolated so a failure here becomes that
    repo's own `ERROR` entry rather than aborting the whole batch (`main()`'s contract).
    """
    validated = repo_from_issue_url(f"https://github.com/{repo}")
    if validated is None:
        return {"status": STATUS_ERROR, "error": f"not a valid owner/repo: {repo!r}"}

    # Derived from `validated`, not the raw `repo` argument -- `repo_from_issue_url` only
    # validates the first two path segments and silently drops the rest, so an extra segment
    # (`owner/name/..`) would otherwise reach the filesystem path unvalidated.
    project = validated.split("/")[1]
    shards = iter_tile_shards(journal_repo / "sessions" / project / "tiles")
    numbered: list[tuple[int, dict]] = []
    for path, entry in shards:
        n = shard_number(path)
        if n is not None:
            numbered.append((n, entry))

    known_shard_issues = {n for n, _entry in numbered}
    other_shard_dates = [
        (n, entry.get("spawned")) for n, entry in numbered if not is_chain_shard(entry)
    ]

    chain_candidate = newest_chain_shard(numbered)
    chain_state = None
    if chain_candidate is not None:
        chain_state = check_issue_state(chain_candidate["issue"], validated)

    open_issues = fetch_open_labeled_issues(validated, "retro-action")
    if open_issues is None:
        # A total transport failure (auth, network, rate limit -- see
        # `fetch_open_labeled_issues`'s docstring), not "this repo genuinely has no queue
        # issue." Reporting it as `NO_QUEUE_FOUND` would make a `gh` outage indistinguishable
        # from a definitively empty backlog, and both callers take no action on
        # `NO_QUEUE_FOUND` -- so the outage would silently look like a clean run.
        return {"status": STATUS_ERROR, "error": "could not fetch open retro-action issues (gh failure)"}

    return classify_repo_status(
        chain_candidate, chain_state, open_issues, known_shard_issues, other_shard_dates,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only per-repo classifier for the chained-tile retro-action "
                    "backlog refill mechanism (dev-env#967, ADR-131). Never mutates "
                    "anything -- prints a JSON status per --repo to stdout."
    )
    parser.add_argument("--repo", action="append", dest="repos", default=[],
                        metavar="OWNER/REPO",
                        help="a repo to classify; repeat for multiple")
    parser.add_argument("--journal-repo", type=Path, default=JOURNAL_REPO,
                        help=f"path to the engineering-journal checkout (default: {JOURNAL_REPO})")
    args = parser.parse_args()

    if not args.repos:
        parser.error("at least one --repo is required")

    output: dict[str, dict] = {}
    for repo in args.repos:
        try:
            output[repo] = classify_one_repo(repo, args.journal_repo)
        except Exception as e:  # noqa: BLE001 -- never let one repo abort the batch
            output[repo] = {"status": STATUS_ERROR, "error": f"{type(e).__name__}: {e}"}

    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
