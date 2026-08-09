#!/usr/bin/env python3
"""Unit tests for retro-chain-status.py -- the read-only classifier behind the chained-tile
retro-action backlog refill mechanism (dev-env#967, ADR-131).

Three properties carry real risk and get the most coverage here, each traced to a live
incident or a concretely-identified failure mode found while designing this mechanism:

1. **Escalation bullets must never be read as spawnable checklist items.** A repo's queue
   issue can carry an "Escalations" section (`- **bold** ...` prose citing already-tracked
   older issues) alongside its real `- [ ]` checklist. `test_parse_checklist_*` pins that
   `parse_checklist` only ever matches real checkbox syntax -- confirmed against a fixture
   modeled on the real dev-env#963 body observed during design, which has both sections.
   This is a deliberate scoping choice (ADR-131), not an oversight: dev-env's own #966 was
   in fact seeded from an escalation bullet by hand, and this classifier will never
   reproduce that pick going forward.

2. **A bare `#NNN` extraction must not misfire on a cross-repo mention.** dev-env#963's own
   body contains a `dev-env#945`-shaped mention inside prose -- a naive `#(\\d+)` regex
   would misextract `945` as if it were a same-repo reference. `test_extract_bare_issue_refs_*`
   pins the negative lookbehind that correctly excludes it while still matching a standalone
   `(#814)` or `#817`.

3. **`classify_repo_status`'s seven-way decision table must route every input combination to
   exactly the right outcome**, including two boundary pairs that are easy to conflate:
   QUEUE_EXHAUSTED (no unchecked items at all) vs. ALL_TILED (unchecked items exist, but
   every one already has a shard at its cited issue) -- and NO_QUEUE_FOUND vs. a chain shard
   that resolved CLOSED with no queue issue behind it at all. Also pinned: the
   item-skip-and-continue walk (an item whose candidate is already tiled is skipped in favor
   of the next unchecked item, mirroring the CHAIN block's own "skip if already tiled" step);
   that AMBIGUOUS is scoped to the most recent chain-tagged shard's own `spawned` date (not
   the queue's much-earlier creation date -- a dev-env#967 /review finding: the queue lives
   ~2 weeks between biweekly runs, so the unfixed window flagged nearly all routine tile
   activity as ambiguous); that an unrecognized chain-issue state is treated as UNRESOLVED,
   never assumed CLOSED (another /review finding -- an unqualified `else` previously risked a
   duplicate spawn against a chain that might still be alive); and that `newest_chain_shard`
   picks by each shard's own `spawned` date, not issue number (issue number is not a safe
   recency proxy once a repo reuses a pre-existing, lower-numbered issue as a later link's
   anchor -- exactly what ADR-131 documents most chained repos actually do).

`fetch_open_labeled_issues` (the live `gh` boundary) is not tested here -- subprocess
boundary, matching this repo's fixture-only convention. Everything that consumes its output
(`find_queue_issue`, `is_queue_body`, `parse_checklist`, `classify_repo_status`) is fully
covered offline.

Usage:
    py -3 claude/scripts/tests/test_retro_chain_status.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "retro-chain-status.py"

# The script imports _winsubp / _journal_shards / _gh_issue_state (siblings in scripts/);
# make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename -- import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("retro_chain_status", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

parse_checklist = mod.parse_checklist
is_queue_body = mod.is_queue_body
find_queue_issue = mod.find_queue_issue
extract_bare_issue_refs = mod.extract_bare_issue_refs
chain_field = mod.chain_field
is_chain_shard = mod.is_chain_shard
newest_chain_shard = mod.newest_chain_shard
classify_repo_status = mod.classify_repo_status
CHAIN_BLOCK_SIGNATURE = mod.CHAIN_BLOCK_SIGNATURE

QUEUE_URL = "https://github.com/brownm09/dev-env/issues/963"

# A fixture modeled on the real dev-env#963 queue-issue body observed while designing this
# mechanism (2026-08-08 biweekly-retro run) -- both a real `- [ ]` checklist section and a
# non-checkbox "Escalations" section, plus the specific dev-env#945-shaped cross-repo mention
# that a naive bare-#NNN regex would misextract. Reconstructed for test purposes, not a
# verbatim copy of the original issue text.
DEV_ENV_963_BODY = """\
## Action items — new this window (global / cross-cutting + dev-env)
- [ ] **Add a `node_modules`-truncation provisioning / `npm ci` gate** — windows: 07-29..08-05.
- [ ] **Make "gate on measured properties, not shape" a standing Pass-3 review dimension** (#940)
- [ ] **Fix `prune-merged-worktrees.py` aborting on uncaught `subprocess.TimeoutExpired`** (#942)
- [x] **Clean up the `modest-noyce-56be08` orphaned-worktree registration** (#938)

## Escalations to existing open items (not refiled — this window adds evidence)
- **#910 item 1 (session-start fetch + ff-only-or-warn) — still the #1 cross-repo friction, still unbuilt.**
  Recurred again this window (cover-letter-runtime (4x 08-04/08-05, dev-env#945 open), win11-init-tools).
- **#920 — recurring worktree cleanup lag, still not automated.**
"""


# --- parse_checklist: real checklist items only, never escalation bullets ----


def test_parse_checklist_extracts_checkbox_lines_only() -> str:
    items = parse_checklist(DEV_ENV_963_BODY)
    texts = [text for _checked, text in items]
    assert len(items) == 4, f"expected 4 checkbox lines (3 unchecked + 1 checked), got {len(items)}: {texts}"
    assert any("node_modules" in t for t in texts)
    assert any("modest-noyce-56be08" in t for t in texts)
    for _checked, text in items:
        assert "Escalations" not in text and "#910 item 1" not in text
    return "only real `- [ ]`/`- [x]` lines are extracted; escalation bullets never match"


def test_parse_checklist_tracks_checked_state() -> str:
    items = parse_checklist(DEV_ENV_963_BODY)
    checked_texts = [text for checked, text in items if checked]
    unchecked_texts = [text for checked, text in items if not checked]
    assert len(checked_texts) == 1 and "modest-noyce" in checked_texts[0]
    assert len(unchecked_texts) == 3
    return "checked (`[x]`) and unchecked (`[ ]`) items are distinguished correctly"


def test_parse_checklist_skips_fenced_code_blocks() -> str:
    body = (
        "- [ ] a real item\n"
        "```\n"
        "- [ ] this is inside a code fence, not a real item\n"
        "```\n"
        "- [ ] another real item\n"
    )
    items = parse_checklist(body)
    texts = [text for _checked, text in items]
    assert texts == ["a real item", "another real item"], texts
    return "a checklist-shaped line inside a fenced code block is not read as a real item"


def test_parse_checklist_tolerates_non_string_and_empty() -> str:
    assert parse_checklist(None) == []
    assert parse_checklist(42) == []
    assert parse_checklist("") == []
    assert parse_checklist("no checklist here, just prose") == []
    return "a non-string, empty, or checklist-free body yields []"


def test_parse_checklist_accepts_asterisk_and_plus_bullets() -> str:
    body = "* [ ] asterisk item\n+ [x] plus item\n- [ ] dash item\n"
    items = parse_checklist(body)
    texts = [text for _checked, text in items]
    assert texts == ["asterisk item", "plus item", "dash item"], texts
    return "all three GFM task-list bullet markers (-, *, +) are recognized, not just -"


def test_parse_checklist_unterminated_fence_disables_fence_awareness_rather_than_truncating() -> str:
    # An odd number of fence markers makes it impossible to tell which lines were meant to be
    # "inside" -- silently discarding everything after the stray fence would drop real
    # checklist items from a queue body that merely has a formatting mistake.
    body = (
        "- [ ] item before the stray fence\n"
        "```\n"
        "- [ ] item after an unterminated fence\n"
    )
    items = parse_checklist(body)
    texts = [text for _checked, text in items]
    assert texts == ["item before the stray fence", "item after an unterminated fence"], texts
    return "an unterminated fence disables fence-awareness for the whole body, rather than truncating the tail"


def test_is_queue_body() -> str:
    assert is_queue_body(DEV_ENV_963_BODY) is True
    assert is_queue_body("Just a single work item, no checklist.") is False
    assert is_queue_body(None) is False
    return "is_queue_body is exactly parse_checklist(body) non-empty"


# --- find_queue_issue: newest-by-created_at, structural (not label/title) ----


def test_find_queue_issue_picks_newest_queue_shaped_issue() -> str:
    issues = [
        {"number": 910, "created_at": "2026-07-25T13:14:32Z", "body": DEV_ENV_963_BODY},
        {"number": 966, "created_at": "2026-08-08T15:54:21Z", "body": "A single anchor work item, no checklist."},
        {"number": 963, "created_at": "2026-08-08T13:14:32Z", "body": DEV_ENV_963_BODY},
    ]
    got = find_queue_issue(issues)
    assert got is not None and got["number"] == 963, \
        f"expected #963 (newest queue-shaped), got {got and got['number']}"
    return "the newest issue whose body is a real checklist wins, even when a non-queue issue is newer still"


def test_find_queue_issue_none_when_no_queue_shaped_issue() -> str:
    issues = [
        {"number": 1204, "created_at": "2026-08-08T15:54:21Z", "body": "A single anchor item, no checklist."},
    ]
    assert find_queue_issue(issues) is None
    assert find_queue_issue([]) is None
    assert find_queue_issue(None) is None
    return "no queue-shaped issue (or no issues at all) -> None, never a guess"


# --- extract_bare_issue_refs: the dev-env#945 false-positive regression ------


def test_extract_bare_issue_refs_excludes_cross_repo_mentions() -> str:
    # The load-bearing regression: a naive `#(\d+)` extraction over the escalation text
    # would misread "dev-env#945" as issue 945 of the *current* repo.
    text = "Recurred again (cover-letter-runtime (4x 08-04/08-05, dev-env#945 open), win11-init-tools)."
    assert extract_bare_issue_refs(text) == [], \
        f"dev-env#945 must NOT be extracted as a bare same-repo reference, got {extract_bare_issue_refs(text)}"
    return "a cross-repo `owner-shaped#NNN` mention is excluded (dev-env#963's own live false positive)"


def test_extract_bare_issue_refs_matches_bare_and_parenthesized() -> str:
    assert extract_bare_issue_refs("Add fit-screen arithmetic validation (#832)") == [832]
    assert extract_bare_issue_refs("See #817 for the follow-up.") == [817]
    assert extract_bare_issue_refs("No reference here at all.") == []
    return "a standalone or parenthesized #NNN is extracted correctly"


def test_extract_bare_issue_refs_dedupes_and_preserves_order() -> str:
    assert extract_bare_issue_refs("See #5 and also #5 again, then #9.") == [5, 9]
    return "repeated references are deduped, first-appearance order preserved"


def test_extract_bare_issue_refs_tolerates_non_string() -> str:
    assert extract_bare_issue_refs(None) == []
    assert extract_bare_issue_refs(42) == []
    return "a non-string input yields [] rather than raising"


def test_extract_bare_issue_refs_excludes_markdown_link_text() -> str:
    # The visible text of a markdown link, `[#945](url)`, must not be read as a bare
    # same-repo reference -- `[` is not a word character, so the word-char-only lookbehind
    # alone would still match it.
    text = "See [#945](https://github.com/brownm09/dev-env/issues/945) for background."
    assert extract_bare_issue_refs(text) == [], extract_bare_issue_refs(text)
    return "a markdown link's visible #NNN text is excluded, not just a word-char-prefixed one"


# --- chain_field / is_chain_shard: two independent recognition signals -------


def test_chain_field_accepts_well_formed_entry() -> str:
    entry = {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "biweekly-retro 2026-08-08"}}
    got = chain_field(entry)
    assert got == entry["chain"]
    return "a `chain` field with a valid queue_issue URL is returned as-is"


def test_chain_field_rejects_malformed_or_absent() -> str:
    assert chain_field({}) is None, "no chain field at all"
    assert chain_field({"chain": "not a dict"}) is None, "chain field wrong type"
    assert chain_field({"chain": {"queue_issue": "not a url"}}) is None, "invalid queue_issue URL"
    assert chain_field({"chain": {"queue_issue": "https://evil.com/o/r/issues/1"}}) is None, \
        "queue_issue must pass the same strict host validation as everywhere else"
    assert chain_field(None) is None
    return "an absent, malformed, or unvalidated-URL chain field all return None"


def test_is_chain_shard_recognizes_chain_field_or_signature() -> str:
    by_field = {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}, "prompt": "unrelated text"}
    by_signature = {"prompt": f"...some setup...\n\n=== {CHAIN_BLOCK_SIGNATURE} ...) ===\n1. tick..."}
    neither = {"prompt": "an ordinary, unrelated tile prompt"}
    assert is_chain_shard(by_field) is True, "a valid chain field alone is sufficient"
    assert is_chain_shard(by_signature) is True, \
        "the CHAIN block signature text alone is sufficient (covers pre-Amendment-6 shards)"
    assert is_chain_shard(neither) is False
    assert is_chain_shard({}) is False
    return "either signal (chain field or CHAIN block signature in prompt) recognizes a chain shard"


def test_is_chain_shard_signature_match_requires_line_start() -> str:
    # dev-env#967 /review finding: a bare substring test would also fire on a tile that
    # merely quotes or discusses the block in ordinary prose (e.g. a follow-up tile about
    # this very mechanism) -- not a hypothetical, since this PR's own items-3/4 follow-ups
    # are exactly such tiles. The real block always starts a line with `=== CHAIN (...`.
    quoted_in_prose = {
        "prompt": (
            "This follow-up is about the CHAIN (do this before you finish...) block "
            "mentioned in dev-env#967 -- it is not itself a chain link."
        )
    }
    assert is_chain_shard(quoted_in_prose) is False, \
        "a mid-sentence mention of the signature text must not be read as a real chain link"
    return "the signature match is anchored to the block's actual line-start header, not a bare substring"


def test_newest_chain_shard_picks_most_recently_spawned() -> str:
    # dev-env#967 /review finding: issue number is NOT a safe recency proxy -- ADR-131's own
    # Context states several repos anchor a chain link on a pre-existing, often
    # lower-numbered issue, so a newer link can carry a lower number than an older one.
    older_link = {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}, "spawned": "2026-07-01"}
    newer_link = {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}, "spawned": "2026-08-05"}
    ordinary_entry = {"prompt": "unrelated"}
    numbered = [(966, older_link), (700, newer_link), (955, ordinary_entry), (1000, ordinary_entry)]
    got = newest_chain_shard(numbered)
    assert got == {"issue": 700, "entry": newer_link}, got
    return (
        "the CHAIN-tagged shard with the LATEST spawned date wins, even carrying a lower "
        "issue number than an older link; untagged shards (even higher-numbered) are ignored"
    )


def test_newest_chain_shard_ties_on_spawned_date_break_by_issue_number() -> str:
    same_day_low = {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}, "spawned": "2026-08-08"}
    same_day_high = {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}, "spawned": "2026-08-08"}
    numbered = [(966, same_day_low), (970, same_day_high)]
    got = newest_chain_shard(numbered)
    assert got == {"issue": 970, "entry": same_day_high}, got
    return "when spawned dates tie, the higher issue number breaks the tie"


def test_newest_chain_shard_missing_spawned_sorts_earliest() -> str:
    missing_spawned = {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}}  # no "spawned" key
    has_spawned = {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}, "spawned": "2026-01-01"}
    numbered = [(999, missing_spawned), (500, has_spawned)]
    got = newest_chain_shard(numbered)
    assert got == {"issue": 500, "entry": has_spawned}, got
    return "a shard missing `spawned` (defensive -- ADR-118 requires it) never wins over one that has it"


def test_newest_chain_shard_none_when_no_chain_tagged_entries() -> str:
    assert newest_chain_shard([(1, {"prompt": "x"}), (2, {"prompt": "y"})]) is None
    assert newest_chain_shard([]) is None
    return "no chain-tagged shard at all -> None (never had a chain, or it was already pruned)"


# --- classify_repo_status: the full decision table ----------------------------


QUEUE_ISSUE = {"number": 963, "created_at": "2026-08-08T13:14:32Z", "body": DEV_ENV_963_BODY}
CHAIN_CANDIDATE = {"issue": 970, "entry": {"chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}}}


def test_classify_alive_when_chain_confirmed_open() -> str:
    got = classify_repo_status(CHAIN_CANDIDATE, "OPEN", [QUEUE_ISSUE], set(), [])
    assert got["status"] == "ALIVE" and got["chain_issue"] == 970, got
    return "a chain-tagged shard whose issue is confirmed OPEN -> ALIVE, no refill"


def test_classify_unresolved_when_chain_state_unknown() -> str:
    got = classify_repo_status(CHAIN_CANDIDATE, None, [QUEUE_ISSUE], set(), [])
    assert got["status"] == "UNRESOLVED" and got["chain_issue"] == 970, got
    return "a chain-tagged shard whose state could not be confirmed -> UNRESOLVED, not refilled this round"


def test_classify_unresolved_when_chain_state_is_unrecognized() -> str:
    # Neither OPEN, unconfirmable (None), nor a value `_gh_issue_state.is_closed` recognizes
    # as closed -- dev-env#967 /review finding: an earlier version fell through an
    # unqualified `else` and treated this the same as a confirmed CLOSED, risking a duplicate
    # spawn against a chain that might still be alive.
    got = classify_repo_status(CHAIN_CANDIDATE, "MERGED", [QUEUE_ISSUE], set(), [])
    assert got["status"] == "UNRESOLVED" and got["chain_issue"] == 970, got
    return "an unrecognized chain-issue state (neither OPEN, None, nor CLOSED) -> UNRESOLVED, not assumed dead"


def test_classify_no_queue_found_when_chain_closed_and_no_queue() -> str:
    got = classify_repo_status(CHAIN_CANDIDATE, "CLOSED", [], set(), [])
    assert got["status"] == "NO_QUEUE_FOUND", got
    return "chain finished (CLOSED) and no open queue issue exists -> NO_QUEUE_FOUND"


def test_classify_no_queue_found_when_never_had_a_chain() -> str:
    # The "never had a chain" path (chain_candidate=None) must behave identically to the
    # "chain finished" path (CLOSED) once past the chain-liveness check -- both mean "no
    # live chain right now", regardless of why.
    got = classify_repo_status(None, None, [], set(), [])
    assert got["status"] == "NO_QUEUE_FOUND", got
    return "no chain shard ever existed, and no open queue issue -> NO_QUEUE_FOUND (same as a finished chain)"


def test_classify_queue_exhausted_when_all_items_checked() -> str:
    all_checked = {**QUEUE_ISSUE, "body": "- [x] done one\n- [x] done two\n"}
    got = classify_repo_status(None, None, [all_checked], set(), [])
    assert got["status"] == "QUEUE_EXHAUSTED" and got["queue_issue"] == 963, got
    return "a queue issue with zero unchecked items -> QUEUE_EXHAUSTED"


def test_classify_needs_refill_first_unchecked_item_no_candidate() -> str:
    got = classify_repo_status(None, None, [QUEUE_ISSUE], set(), [])
    assert got["status"] == "NEEDS_REFILL", got
    assert "node_modules" in got["item_text"], got
    assert got["candidate_issue"] is None, \
        "the first unchecked item cites no inline #NNN in this fixture"
    return "the first unchecked checklist item is returned as the refill candidate"


def test_classify_needs_refill_skips_already_tiled_items() -> str:
    # Item 1 ("...standing Pass-3...") cites #940; item 2 cites #942. #940 already has a
    # shard on disk -> skip it, return item 2 instead.
    body = (
        "- [ ] **standing Pass-3 review dimension** (#940)\n"
        "- [ ] **fix the timeout bug** (#942)\n"
    )
    queue = {**QUEUE_ISSUE, "body": body}
    got = classify_repo_status(None, None, [queue], {940}, [])
    assert got["status"] == "NEEDS_REFILL", got
    assert got["candidate_issue"] == 942, \
        f"expected the second item (#942) after skipping the already-tiled first item, got {got}"
    return "an unchecked item whose cited issue already has a shard is skipped for the next one"


def test_classify_all_tiled_when_every_candidate_already_has_a_shard() -> str:
    body = (
        "- [ ] **item one** (#940)\n"
        "- [ ] **item two** (#942)\n"
    )
    queue = {**QUEUE_ISSUE, "body": body}
    got = classify_repo_status(None, None, [queue], {940, 942}, [])
    assert got["status"] == "ALL_TILED" and got["queue_issue"] == 963, got
    return "every unchecked item's candidate already has a shard -> ALL_TILED, distinct from QUEUE_EXHAUSTED"


def test_classify_ambiguous_when_recent_untagged_shard_exists() -> str:
    # An untagged shard for #999, spawned the SAME DAY the queue issue was created --
    # plausibly already covers the top item, so this must not guess.
    got = classify_repo_status(None, None, [QUEUE_ISSUE], set(), [(999, "2026-08-08")])
    assert got["status"] == "AMBIGUOUS" and got["queue_issue"] == 963, got
    assert "999" in got["notes"][0]
    return "an untagged shard spawned on/after the queue's creation date -> AMBIGUOUS, never a guess"


def test_classify_ambiguous_ignores_older_untagged_shards() -> str:
    # An untagged shard from well before this queue issue existed must NOT suppress a
    # genuine refill -- this repo accumulates dozens of ordinary, unrelated tiles.
    got = classify_repo_status(None, None, [QUEUE_ISSUE], set(), [(500, "2026-07-01")])
    assert got["status"] == "NEEDS_REFILL", got
    return "an untagged shard from before the queue's creation date does not trigger AMBIGUOUS"


def test_classify_ambiguous_window_uses_chain_candidates_spawned_date_not_queue_creation() -> str:
    # The actual dev-env#967 /review bug: a queue issue lives ~2 weeks between biweekly runs
    # (QUEUE_ISSUE here was created 2026-08-08), so scoping AMBIGUOUS to "on/after the queue's
    # creation date" unconditionally flags routine, unrelated tile activity across that whole
    # window. The correct reference point, once a chain has existed for this queue, is the
    # last time the chain itself actually moved -- here, a link spawned a week later.
    closed_chain = {"issue": 970, "entry": {
        "chain": {"queue_issue": QUEUE_URL, "seeded_by": "x"}, "spawned": "2026-08-15",
    }}
    # Spawned after the queue's creation (08-08) but BEFORE the chain's own last move (08-15):
    # under the pre-fix logic this would wrongly flag AMBIGUOUS; under the fix it does not,
    # because it predates the actual current gap.
    got_before_chain_moved = classify_repo_status(
        closed_chain, "CLOSED", [QUEUE_ISSUE], set(), [(500, "2026-08-10")])
    assert got_before_chain_moved["status"] == "NEEDS_REFILL", got_before_chain_moved

    # Spawned AFTER the chain's own last move -> genuinely ambiguous.
    got_after_chain_moved = classify_repo_status(
        closed_chain, "CLOSED", [QUEUE_ISSUE], set(), [(999, "2026-08-16")])
    assert got_after_chain_moved["status"] == "AMBIGUOUS", got_after_chain_moved
    assert "2026-08-15" in got_after_chain_moved["notes"][0], got_after_chain_moved
    return (
        "once a chain-tagged shard exists for this queue, AMBIGUOUS is scoped to shards "
        "spawned on/after THAT shard's own spawned date, not the queue's much-earlier "
        "creation date"
    )


def test_classify_chain_closed_falls_through_to_next_item() -> str:
    # A CLOSED chain candidate must fall all the way through to the same NEEDS_REFILL logic
    # as chain_candidate=None -- confirms the "finished chain" and "no chain" paths converge.
    got_closed = classify_repo_status(CHAIN_CANDIDATE, "CLOSED", [QUEUE_ISSUE], set(), [])
    got_none = classify_repo_status(None, None, [QUEUE_ISSUE], set(), [])
    assert got_closed["status"] == got_none["status"] == "NEEDS_REFILL"
    assert got_closed["item_text"] == got_none["item_text"]
    return "a CLOSED chain candidate and no chain candidate at all reach an identical outcome"


def main() -> int:
    tests = [
        ("parse_checklist extracts checkbox lines only", test_parse_checklist_extracts_checkbox_lines_only),
        ("parse_checklist tracks checked state", test_parse_checklist_tracks_checked_state),
        ("parse_checklist skips fenced code blocks", test_parse_checklist_skips_fenced_code_blocks),
        ("parse_checklist tolerates non-string/empty", test_parse_checklist_tolerates_non_string_and_empty),
        ("parse_checklist accepts */+  bullets, not just -", test_parse_checklist_accepts_asterisk_and_plus_bullets),
        ("parse_checklist: unterminated fence disables fence-awareness", test_parse_checklist_unterminated_fence_disables_fence_awareness_rather_than_truncating),
        ("is_queue_body", test_is_queue_body),
        ("find_queue_issue picks newest queue-shaped issue", test_find_queue_issue_picks_newest_queue_shaped_issue),
        ("find_queue_issue none when no queue-shaped issue", test_find_queue_issue_none_when_no_queue_shaped_issue),
        ("extract_bare_issue_refs excludes cross-repo mentions", test_extract_bare_issue_refs_excludes_cross_repo_mentions),
        ("extract_bare_issue_refs matches bare and parenthesized", test_extract_bare_issue_refs_matches_bare_and_parenthesized),
        ("extract_bare_issue_refs dedupes, preserves order", test_extract_bare_issue_refs_dedupes_and_preserves_order),
        ("extract_bare_issue_refs tolerates non-string", test_extract_bare_issue_refs_tolerates_non_string),
        ("extract_bare_issue_refs excludes markdown link text", test_extract_bare_issue_refs_excludes_markdown_link_text),
        ("chain_field accepts well-formed entry", test_chain_field_accepts_well_formed_entry),
        ("chain_field rejects malformed or absent", test_chain_field_rejects_malformed_or_absent),
        ("is_chain_shard recognizes chain field or signature", test_is_chain_shard_recognizes_chain_field_or_signature),
        ("is_chain_shard signature match requires line start", test_is_chain_shard_signature_match_requires_line_start),
        ("newest_chain_shard picks most recently spawned", test_newest_chain_shard_picks_most_recently_spawned),
        ("newest_chain_shard ties on spawned date, breaks by issue number", test_newest_chain_shard_ties_on_spawned_date_break_by_issue_number),
        ("newest_chain_shard missing spawned sorts earliest", test_newest_chain_shard_missing_spawned_sorts_earliest),
        ("newest_chain_shard none when no chain-tagged entries", test_newest_chain_shard_none_when_no_chain_tagged_entries),
        ("classify: ALIVE when chain confirmed open", test_classify_alive_when_chain_confirmed_open),
        ("classify: UNRESOLVED when chain state unknown", test_classify_unresolved_when_chain_state_unknown),
        ("classify: UNRESOLVED when chain state is unrecognized", test_classify_unresolved_when_chain_state_is_unrecognized),
        ("classify: NO_QUEUE_FOUND when chain closed, no queue", test_classify_no_queue_found_when_chain_closed_and_no_queue),
        ("classify: NO_QUEUE_FOUND when never had a chain", test_classify_no_queue_found_when_never_had_a_chain),
        ("classify: QUEUE_EXHAUSTED when all items checked", test_classify_queue_exhausted_when_all_items_checked),
        ("classify: NEEDS_REFILL, first unchecked item, no candidate", test_classify_needs_refill_first_unchecked_item_no_candidate),
        ("classify: NEEDS_REFILL skips already-tiled items", test_classify_needs_refill_skips_already_tiled_items),
        ("classify: ALL_TILED when every candidate already has a shard", test_classify_all_tiled_when_every_candidate_already_has_a_shard),
        ("classify: AMBIGUOUS when a recent untagged shard exists", test_classify_ambiguous_when_recent_untagged_shard_exists),
        ("classify: AMBIGUOUS ignores older untagged shards", test_classify_ambiguous_ignores_older_untagged_shards),
        ("classify: AMBIGUOUS window uses chain's spawned date, not queue creation", test_classify_ambiguous_window_uses_chain_candidates_spawned_date_not_queue_creation),
        ("classify: CLOSED chain falls through like no chain", test_classify_chain_closed_falls_through_to_next_item),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
