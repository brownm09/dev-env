# ADR-090 — Extract Shared Transcript-Record Readers into `_hookutil`

**Date:** 2026-07-08
**Status:** Accepted
**Tags:** hooks, stop-hook, transcript, dry, maintainability, shared-module, adr-064, adr-088

---

## Context

Two Stop hooks scan the just-ended session transcript, and each carried its own near-identical copy
of the same transcript-record readers:

| Reader | `posttooluse-inert-advisory.py` (ADR-055) | `stop-tile-enumeration-gate.py` (ADR-088) |
|---|---|---|
| `load_records` | line-by-line parse, keeps every JSON value | `_parse_records(read())`, keeps only objects |
| `iter_bash_calls` | returns `(command, output, cwd)` | returns `(command, output)`; adds `isinstance` guards + a `_content_items` helper |
| `_result_text` | identical | identical |

`stop-tile-enumeration-gate.py` (merged in [dev-env#604](https://github.com/brownm09/dev-env/pull/604))
landed the second copy — ~80 lines both reviewers of PR #604 flagged as replicated.
[ADR-088](088-state-keyed-tile-enumeration-gate.md) recorded the replication as *deliberate*: an
"Alternatives considered" bullet ("Share the transcript readers via a new module") cited the repo's
tolerance for small-helper duplication (`_first_line`, intentionally duplicated across `_hookio.py`
and `pre-tool-use-canonical-mutate-guard.py`) and a concern that sharing would over-couple two
otherwise-independent hooks.

That call is the outlier for this class. The repo's consistent precedent is to *extract* a shared
module the moment a transcript/payload reader lives in more than one hook — `_hookio`
([ADR-050](050-shared-hookio-sibling-hook-fixes.md)), `_worktree_liveness`
([ADR-051](051-worktree-liveness-guard.md)), `_journal_shards`
([ADR-057](057-shared-journal-shard-reader.md)), and `_hookutil` itself
([ADR-064](064-shared-hookutil-sentinel-transcript-locate.md)). And the two hooks are *not* independent
for this concern: they parse the identical transcript-record format, so a change to that format (a new
content shape, a `toolUseResult` field rename) must land in both copies or one silently drifts. Unlike
`_first_line` — a two-line command-segment helper whose two copies encode a genuinely different intent
per hook — these are the same ~80 lines doing the same job. [dev-env#605](https://github.com/brownm09/dev-env/issues/605)
was filed to extract them.

## Decision

Move the transcript-record readers into `claude/scripts/_hookutil.py` — the shared module ADR-064
already established for this exact hook family (Stop / UserPromptSubmit) and its sibling helpers
(sentinels, transcript-locate). It now also exposes:

- `load_records(path)` / `_parse_records(text)` — read a JSONL transcript into its **JSON-object**
  records (non-object lines dropped). `_parse_records` is a separate export so a caller that already
  holds the transcript text (to run a cheap pre-filter first, as the gate does) reuses the identical
  parse rather than re-deriving it.
- `iter_bash_calls(records)` — pairs each Bash `tool_use` with its `tool_result` by `tool_use_id` and
  returns the **superset** `(command, output, cwd)` 3-tuple, carrying `stop-tile-enumeration-gate.py`'s
  PR #604-review `isinstance` guards (via `_content_items`) so it is safe on malformed / hand-built
  records.
- `_result_text(item, record)` / `_content_items(rec)` — the supporting helpers.

**Consumers:**

- `posttooluse-inert-advisory.py` imports `iter_bash_calls`, `load_records` (and re-exports
  `_result_text` so its test's `mod._result_text` access still resolves — the module-attribute
  indirection of [ADR-073](073-shared-worktree-canon-gh-project-modules.md)). It already consumed the
  3-tuple, so it uses the shared reader directly.
- `stop-tile-enumeration-gate.py` imports `_content_items`, `_parse_records`, and the shared
  `iter_bash_calls` (aliased), and keeps a **thin 2-tuple adapter** `iter_bash_calls` that drops `cwd`
  — the gate never uses it, and the adapter preserves the gate's historical 2-tuple contract so
  `session_merged_prs` and all existing tests are untouched. Its `main()` keeps its own cheap
  `"merged"` pre-filter (hook-specific) and its `_first_line` command-segment helper (a separate,
  still-deliberate duplication with `_hookio._first_line`).

`load_records` now dict-filters for both hooks (previously only the gate did). On a real transcript —
every line a JSON object — this is behaviorally identical; on a malformed transcript with a non-object
line, the advisory now skips that line instead of crashing and exiting 0 through its outer guard —
strictly safer, and matching the gate's existing robustness.

`tests/test_hookutil.py` gains coverage for all five moved helpers; the two consuming hooks' existing
test suites (`test_posttooluse_inert_advisory.py`, `test_stop_tile_enumeration_gate.py`) pass
**unchanged**, confirming the extraction preserved behavior.

## Considered alternatives

- **Leave them replicated (ADR-088's original call).** Rejected: two copies of the same ~80-line
  reader over the same transcript format are a drift surface both #604 reviewers flagged; this is the
  class the repo extracts (ADR-050/051/057/064), not the two-line-different-intent class `_first_line`
  belongs to.
- **A new `_transcript.py` module.** Rejected: the readers are the natural companions of `_hookutil`'s
  sentinel + transcript-locate helpers — same hook family, same "read the session transcript" concern.
  A second module would fragment it.
- **Gate consumes the 3-tuple directly (no adapter), updating its tests.** Rejected in favor of the
  adapter: the gate has no use for `cwd`, and the adapter keeps its 2-tuple contract and its entire
  test suite unchanged — a smaller, lower-risk diff than rewriting `session_merged_prs` and its ~12
  tests to the 3-tuple shape.

## Consequences

- One source of truth for transcript parsing across the two Stop hooks; a transcript-format change is a
  one-place edit. `_hookutil` now owns readers in addition to sentinels + transcript-locate.
- **No behaviour change** in either hook (the advisory's malformed-line robustness is a strict
  improvement, not a regression). Continues the shared-helper line: `_hookio` → `_worktree_liveness` →
  `_journal_shards` → `_hookutil` (sentinel/locate, ADR-064) → `_hookutil` (readers, this ADR).
- ADR-088's "Share the transcript readers via a new module" alternative is now adopted; that bullet is
  annotated as superseded here.

## References

- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — established `_hookutil` (sentinel +
  transcript-locate); this ADR extends it with the transcript-record readers.
- [ADR-088](088-state-keyed-tile-enumeration-gate.md) — added the second reader copy and recorded the
  replicate-them decision this ADR reverses.
- [ADR-055](055-reliable-event-inert-posttooluse-advisory.md) — `posttooluse-inert-advisory.py`, the
  other consumer.
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) / [ADR-051](051-worktree-liveness-guard.md) /
  [ADR-057](057-shared-journal-shard-reader.md) — the shared-module extraction precedents.
- [ADR-073](073-shared-worktree-canon-gh-project-modules.md) — the module-attribute indirection that
  keeps the consuming hooks' tests working through `from _hookutil import ...`.
- [dev-env#605](https://github.com/brownm09/dev-env/issues/605) — issue this ADR closes.
- [dev-env#604](https://github.com/brownm09/dev-env/pull/604) — the PR whose review flagged the
  duplication.
- `claude/scripts/_hookutil.py`; `claude/scripts/posttooluse-inert-advisory.py`;
  `claude/scripts/stop-tile-enumeration-gate.py`; `claude/scripts/tests/test_hookutil.py`.

---

## Amendment 1 (2026-07-10) — bounded tail reader `iter_records_reverse` (dev-env#679)

**The gap.** `load_records` (and the `_parse_records` it wraps) always reads and parses a transcript's
entire JSONL file, unconditionally. Two adversarial `/review` subagents reviewing PR #673
(`idle-refresher.py`, dev-env#655) independently flagged the same cost: `idle-refresher.py` fires on
every `UserPromptSubmit`, but only ever needs the single last assistant record's timestamp — yet it
paid a full read-and-parse of the whole transcript to get it. Tool results in a long session (embedded
file contents, command output) make transcripts easily tens of MB, and the sessions where a full parse
is most expensive are exactly the long-running ones this hook cares most about. Fixing it well meant
either diverging from the shared `_hookutil` convention for one caller alone, or extending
`_hookutil.load_records` itself — deferred to its own issue (dev-env#679) rather than done inline in
PR #673, since it has a larger blast radius across `load_records`'s existing callers and needs its own
dedicated testing.

**Decision.** Add `iter_records_reverse(transcript_path, chunk_size=DEFAULT_REVERSE_CHUNK_SIZE)` to
`_hookutil.py` — a generator that reads the file from the end in bounded chunks (64 KiB default),
yielding JSON-object records most-recent-first. A caller that only needs a small piece of tail state
can stop consuming the generator (`break`, or a bare `next()`) as soon as it finds a match; the unread,
earlier portion of the file is never touched. Chunk boundaries are found on raw bytes *before* any
UTF-8 decoding — the ASCII `\n` byte cannot appear inside a UTF-8 continuation or lead byte, so a
multi-byte character split across two chunk reads is never corrupted. Blank and malformed lines are
skipped and a non-object JSON value is dropped, mirroring `_parse_records`'s existing contract (shared
via the new `_record_from_line` helper). `load_records` is unchanged — this is a purely additive
alternative, not a replacement, matching the issue's explicit guidance to keep it as-is for callers
that genuinely need the whole transcript (`stop-tile-enumeration-gate.py`'s session-wide scan).

**Consumer: `idle-refresher.py`.** `last_activity_epoch`'s contract changed from "a forward-chronological
list, reversed internally" to "an already most-recent-first iterable, consumed with a plain `for` loop"
— dropping the internal `reversed()` call is what actually lets a lazy generator short-circuit; keeping
`reversed()` would have forced full materialization regardless of what was passed in, defeating the
point. `main()` now sources `last_activity_epoch` from `_hookutil.iter_records_reverse(path)` via a new
`_last_activity_epoch_from_path` wrapper, replacing the old `_read_records` (a full `load_records` call
wrapped in a fail-open `try/except`). The three existing `last_activity_epoch` test fixtures were
reordered newest-first to match the new contract, and a new `test_last_activity_epoch_consumes_lazily`
proves the laziness itself with a hand-rolled generator that raises `AssertionError` if pulled past its
first match.

**Coverage.** `test_hookutil.py` gains 11 new tests (21 -> 32): direct coverage of `_record_from_line`
(a valid line; blank/malformed/non-object -> `None`) and `iter_records_reverse` (basic reverse order; a
property check — results match `reversed(load_records(...))` — across 9 chunk sizes from 1 byte to
4096, forcing lines to be reassembled across many chunk boundaries; a file with no trailing newline;
blank/malformed lines skipped; multi-byte UTF-8 characters surviving small chunk sizes; an empty file;
`FileNotFoundError` on a missing path; `ValueError` on a non-positive `chunk_size`; and — the one test
in this file that mocks, following `test_prune_merged_worktrees.py`'s established precedent, since the
"doesn't read the whole file" property isn't otherwise observable from pure inputs/outputs alone — a
`builtins.open` read-call counter proving a ~4000-line fixture yields its single matching tail record
after just one chunk read). `test_idle_refresher.py` gains 1 new test and updates 3 existing fixtures
(14 -> 15 total). The other three callers named in dev-env#679 (`posttooluse-inert-advisory.py`,
`stop-tile-enumeration-gate.py`, `reconcile-open-prs.py`) import `_hookutil` but were confirmed via grep,
before starting, to never call `load_records`/`_parse_records` on a path this change touches; their
existing test suites (items 18, 48, 19) were re-run in full and pass unchanged (32 + 116 + 15 tests).

**Out of scope.** No second convenience wrapper (e.g. a `find_last_record(path, predicate)`) was added
alongside the generator — `idle-refresher.py` is the only current consumer and it's served directly by
consuming `iter_records_reverse` through the existing `last_activity_epoch`; adding an unused wrapper for
a hypothetical future caller would be speculative. `posttooluse-inert-advisory.py`'s own `load_records`
call was left untouched — the issue named it as one of the five hooks affected by the general "full parse
on every hook fire" pattern, not as a caller with the same "only needs the tail" shape `idle-refresher.py`
has; converting it, if warranted, is a separate, independently-scoped change.
