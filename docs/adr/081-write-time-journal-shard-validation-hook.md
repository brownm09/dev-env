# ADR-081 — Write-Time Journal Shard Validation: PostToolUse Advisory Hook + Shared `_journal_schema` Module

**Date:** 2026-07-03
**Status:** Accepted
**Closes:** [dev-env#556](https://github.com/brownm09/dev-env/issues/556)
**Tags:** hooks, post-tool-use, journal, manifest, open-prs, schema-validation, bom, silent-skip, shared-module, advisory
**Related:** [ADR-050](050-shared-hookio-sibling-hook-fixes.md), [ADR-057](057-shared-journal-shard-reader.md), [ADR-048](048-memory-immortalization-issue-pairing.md), [ADR-073](073-shared-worktree-canon-gh-project-modules.md)

---

## Context

`validate-manifest.py` ([#423](https://github.com/brownm09/dev-env/issues/423)) gates engineering-journal
manifest shards only at compose time — the **next day's** `/journal-compose` Step 0.7 run. A shard written
(or re-created) after the morning compose bypasses that gate for a full day; the gap surfaces only when a
subagent trips over it mid-compose and a human hand-patches the manifest.

This is exactly what happened on 2026-07-02 (hand-repaired 2026-07-03, engineering-journal
`draft/2026-07-02` commit `153966e`):

- 15 career-playbook shards missing `topic`/`tokens` — PR-merge bookkeeping sessions re-creating manifests
  after the morning compose had already consumed the originals.
- 3 meta shards (`2026-07-02_140000`/`210254`/`214323`) missing `stub`/`topic`/`tokens`, carrying a
  `summary` field where `topic` was required.
- 1 meta shard (`2026-07-02_130000`) plus `sessions/meta/open-prs/147.json` written with a UTF-8 BOM.
  Node's `JSON.parse` rejects a BOM outright, so the compose Step 8a dashboard aggregation silently
  skipped both files via `try`/`catch`; the Python shard readers (`_journal_shards.py`) independently
  skip a BOM'd file because `json.loads` on BOM-prefixed text also raises.

Three different readers (`/journal-compose`'s Node aggregation, `_journal_shards.iter_pr_shards`,
`reconcile-open-prs.py`) all silently drop a malformed shard rather than surfacing it — by design, each
one individually reasons "a human will find this eventually," but no single one closes the loop, so the
"eventually" was a full day and 19 files.

## Decision

Add a new PostToolUse hook, `journal-shard-write-advisory.py`, wired on the `Write`, `Edit`, and `Bash`
matchers, that validates any engineering-journal manifest shard (`sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`)
or open-PR shard (`sessions/<project>/open-prs/<N>.json`) touched by the tool call — **in the writing
session, at write time** — against the same schema `validate-manifest.py` already enforces at compose
time. On a violation it prints a stderr advisory and exits 2 (the write already happened; this surfaces
the problem, it does not block), mirroring `memory-write-advisory.py`'s established exit-2-advisory
convention (ADR-048).

Four structural choices carry the design:

1. **Validate on-disk bytes, not tool-call payload content.** For `Write`/`Edit` the hook reads the file
   at `tool_input.file_path` directly. For `Bash`, it harvests candidate shard-path tokens out of the raw
   command text via regex, resolves each to a real file (against `cwd`, a harvested `cd`/`git -C`/
   `--git-dir=` directory argument, or a constant `~/Git/engineering-journal` fallback), and reads
   whichever resolve to files that exist. Reading the actual bytes — not the payload's `content` field —
   is what catches a BOM: the encoding is a property of what landed on disk, and a BOM can survive
   regardless of which tool wrote it.

2. **The Bash token harvest is a raw regex scan, deliberately not anchored via `_hookio.scan_top_level`.**
   Every other command-shape predicate in this hook family (`pr-merge-reminder.py`, `pre-merge-numbering-check.py`,
   `stub-push-archive-reminder.py`) anchors its detection to real top-level command *intent*, because an
   unanchored match would misfire *action* on text that was never actually a command (ADR-050's whole
   amendment sequence). This hook's harvest answers a different question — "does this string, wherever it
   appears in the command, name a real file on disk?" — and validating an existing file is side-effect-free
   even when the mention was inside a heredoc body or a quoted argument. Over-matching is harmless here in
   a way it is not for the anchored hooks, so anchoring would only cost coverage (e.g. missing a shard path
   embedded in a `node -e` snippet) for no safety benefit.

3. **Schema logic is extracted, not duplicated**, into a new shared module `claude/scripts/_journal_schema.py`
   — `REQUIRED_FIELDS`, `OPEN_PR_REQUIRED_FIELDS`, `missing_required_fields()`, `missing_open_pr_fields()`,
   `find_entries_missing_fields()`, `parse_manifest_text()`, and a new `decode_shard_bytes()` that names a
   BOM instead of letting it surface as an opaque line-1 JSON parse failure. `validate-manifest.py`
   re-imports the four pre-existing names (module-attribute indirection, the same pattern ADR-073
   established for `_worktree_canon.py`), so its own tests are unaffected. This follows the repo's standing
   convention: production scripts never `importlib` a hyphenated sibling; shared logic lives in an
   underscore module both consumers import directly (ADR-057's `_journal_shards.py` is the precedent).

4. **The open-PR shard schema is enforced at full strictness** (`pr`, `url`, `topic`, `stub`, `opened` —
   every field `docs/REFERENCE.md` documents, none optional), plus two filename-level checks reusing
   `_journal_shards.shard_pr_number`: a non-numeric stem (e.g. `index.json`) is invisible to every existing
   reader, which all enumerate `open-prs/*.json` by numeric filename; and a stem/embedded-`pr` mismatch
   (`147.json` containing `"pr":148`) is a real, previously-undetected hazard since every reader acts on
   the embedded field but a human editing the file by hand only sees the filename.

## Rationale

**Why an advisory (exit 2), not a `PreToolUse` block.** A `PreToolUse` hook can't see the final bytes a
`Bash` command is about to write — it only sees the command string before execution. Blocking would also
mean refusing legitimate journal bookkeeping (e.g. a merge-time `prs_closed` update) on a false positive,
which is a worse failure mode than a one-line nag the agent can act on immediately. The write already
happening and the hook surfacing the problem is the same tradeoff `memory-write-advisory.py` made for the
same reason (ADR-048).

**Why not validate payload content instead of disk state.** The `tool_input.content` field for a `Write`
reflects what Claude *intended* to write, not necessarily the final on-disk bytes (encoding, line-ending
normalization, or a `Bash` heredoc's shell-level transformations can differ). Re-reading the file after the
tool call is the only way to see what a reader will actually load.

**Why the schema extraction happens now rather than leaving `validate-manifest.py` alone.** Duplicating
`REQUIRED_FIELDS` and the parsing helpers into the new hook would create exactly the drift risk the
Suppression/Test-Integrity/lockfile policies exist to prevent elsewhere in this repo: a future schema
change (a sixth required field, say) would need to be made in two places, and nothing enforces that. A
shared module makes the schema singular by construction.

**Why full strictness for open-PR shards instead of a minimal `pr`+`url` check.** `reconcile-open-prs.py`
already treats a shard missing a resolvable repo/PR as permanently unactionable — it *leaves malformed
shards in place* rather than guessing. A shard missing `topic`/`stub`/`opened` doesn't fail that hook, but
it does silently degrade the compose-time PR-grouping heuristic and cross-day cross-referencing that read
those fields. Enforcing less than the documented schema would just create a second, looser, undocumented
schema — a new drift source of its own.

## Alternatives considered

- **A `PreToolUse` block instead of a `PostToolUse` advisory.** Rejected — see Rationale above; it cannot
  see final `Bash`-written bytes and risks blocking legitimate bookkeeping on a false positive.
- **Validate `tool_input` payload content instead of re-reading the file.** Rejected — misses the BOM and
  any transformation between what Claude requested and what actually landed on disk.
- **Extend the compose-time validator's schedule (e.g. run it more often) instead of adding a write-time
  hook.** Rejected — this doesn't change *when* the gate fires relative to the write; it just shrinks the
  blind window without closing it, and still requires a human to notice and run it.
- **A git `pre-commit` hook inside the engineering-journal repo.** Rejected — dev-env's hook
  infrastructure (`claude/settings.json`, the `pyw -3` launcher convention, the shared `_hookio`/`_journal_schema`
  modules) all live in dev-env, not engineering-journal; and a pre-commit hook only fires on `git commit`,
  missing a shard that's written but never committed (or committed from a different tool entirely).

## Consequences

**Positive:**
- The 2026-07-02-shaped incident (missing fields, wrong field name, BOM) is now caught in the same session
  that introduces it, not the next day's compose.
- `validate-manifest.py` and `journal-shard-write-advisory.py` share one schema source of truth
  (`_journal_schema.py`) — a future schema change updates one module, not two gates that can drift.
- The open-PR shard schema gains two previously-unchecked failure modes (non-numeric filename,
  stem/`pr` mismatch) for free, since the hook validates that shard kind too.

**Negative / accepted tradeoffs:**
- A `Bash` command that merely *mentions* a shard path (inside a heredoc, a comment, or an unrelated
  string) and that path happens to resolve to a real, broken shard will nag every time it's touched until
  fixed — this is intended pressure, not a bug, and mirrors `memory-write-advisory.py`'s equivalent
  self-quieting behavior (fix the file, the next check passes; no sentinel needed).
- The hook adds a small, bounded amount of work to every `Write`/`Edit`/`Bash` PostToolUse dispatch outside
  the journal repo too (a cheap path-classification check that returns `None` immediately for anything
  that isn't journal-shaped) — negligible relative to the other seven Bash-matched PostToolUse hooks
  already on this dispatch path.

## References

- [dev-env#556](https://github.com/brownm09/dev-env/issues/556) — this ADR's issue.
- [dev-env#423](https://github.com/brownm09/dev-env/issues/423) — the original compose-time gate.
- engineering-journal `draft/2026-07-02` commit `153966e` — the hand-repair this ADR's hook exists to make
  unnecessary going forward.
- [ADR-056](056-per-session-sharding-journal-companion-files.md) — the per-session/per-PR shard model this
  hook validates.
- [ADR-057](057-shared-journal-shard-reader.md) — the shared-module extraction precedent (`_journal_shards.py`)
  this ADR's `_journal_schema.py` extraction follows.
- [ADR-073](073-shared-worktree-canon-gh-project-modules.md) — the module-attribute re-export pattern that
  keeps `validate-manifest.py`'s existing tests green through the extraction.
- [ADR-048](048-memory-immortalization-issue-pairing.md) — the exit-2, non-blocking PostToolUse advisory
  convention this hook follows.
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) — `scan_top_level` and the anchoring doctrine this
  ADR's Rationale distinguishes the token harvest from.
- `docs/REFERENCE.md` → Engineering Journal Internals — the manifest and open-PR shard schemas this hook
  enforces at write time.
