# ADR-081 — Write-Time Journal Shard Validation: PostToolUse Advisory Hook + Shared `_journal_schema` Module

**Date:** 2026-07-03 (amended 2026-07-17, 2026-07-23, 2026-08-26)
**Status:** Accepted
**Closes:** [dev-env#556](https://github.com/brownm09/dev-env/issues/556)
**Tags:** hooks, post-tool-use, journal, manifest, open-prs, schema-validation, bom, silent-skip, shared-module, advisory, tiles, cwd, path-validation, task-id, stub
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

---

## Amendment 1 — 2026-07-17: `tokens` field type validation (`malformed_manifest_fields`)

**Closes:** [dev-env#824](https://github.com/brownm09/dev-env/issues/824)

**Incident.** Two manifest shards written on 2026-07-17 had `"tokens": 0` (a bare integer)
instead of `{"input": 0, "output": 0, "cost": 0}` (the required dict shape). Both the write-time
hook and `validate-manifest.py` accepted this silently because `missing_required_fields` only
checks key *presence* — `"tokens"` was present so the check passed. The compose-time gate
caught it, but the earlier gate should have caught it first.

**Design decision — separate presence checks from type/value checks.**
Rather than adding type logic to `missing_required_fields` (which is a pure key-absence check
and readable as such), a new function `malformed_manifest_fields(entry)` was added to
`_journal_schema.py`. It validates that `tokens` is a dict with keys `input`, `output`, `cost`,
each numeric (int or float). The function returns `[]` when `tokens` is absent (the presence
check already covers that case) — the two functions are designed to be called in sequence
without double-reporting the same problem.

This split has two benefits: (1) `missing_required_fields` stays a single-responsibility
predicate that is easy to read and test; (2) type/value checks for future fields can be added
to `malformed_manifest_fields` without touching the presence-check logic.

**Wiring.** Both consumers call `malformed_manifest_fields` after `missing_required_fields`:
- `journal-shard-write-advisory.py` — `problems.extend(malformed_manifest_fields(entry))`
  inside the manifest-entry loop in `validate_shard_bytes`.
- `validate-manifest.py` — `type_errors` list accumulated per entry; reported as a separate
  "Entries with malformed field values:" section in the FAIL output.

---

## Amendment 2 — 2026-07-23: `cwd` path-plausibility validation (`malformed_tile_fields`)

**Closes:** [dev-env#904](https://github.com/brownm09/dev-env/issues/904)

**Incident.** Three tile shards written on 2026-07-23 — `sessions/dev-env/tiles/{898,899,900}.json`
— carried `"cwd": "C:Users<U+0008>rownGitdev-env"`. That value names no directory. Every check the
hook then performed reported the shards healthy: the files existed, parsed as JSON objects, had
numeric filenames matching their embedded `issue`, and carried all seven `TILE_REQUIRED_FIELDS`.

This is a strictly worse failure than Amendment 1's. ADR-118's premise is that the shard is the
durable **payload** that lets a lost `spawn_task` chip be re-spawned *exactly* after a crash or
app restart (the paired issue is the anchor; the shard is the payload). A `cwd` naming no
directory means the re-spawn fails or lands in the wrong repo — so for those tiles the payload
was **already lost**, silently, with no gate anywhere downstream that would ever notice.
`malformed_manifest_fields` had a compose-time backstop; this has none.

**Root cause — the second escaping layer, on the one field nobody guarded.** ADR-118's write
recipe says *"build the JSON with a serializer, never `echo`"*, and justifies it entirely by
`prompt`: free prose, so interpolation corrupts the shard or escapes into the shell. `cwd` is the
*other* free-form field and the only one that is a Windows path, and the guidance never mentions
it. A serializer invoked as `node -e "…"` from bash puts a JS string literal between the path and
`JSON.stringify`, which eats `\U` and `\G` and turns `\b` into U+0008:

```
C:\Users\brown\Git\dev-env   ->   C:Users<U+0008>rownGitdev-env
```

Two properties made this a recipe defect rather than one session's slip: at least two independent
sessions produced the identical value on the same day, and the failure is **silent in JS
specifically** — Python's literal parser raises on `\U`, which is why the `py -3 -c` form in the
documented recipe never produced it. Following the documented recipe was sufficient to avoid the
bug; nothing said so, and the ambient `node -e` habit (the `jq`-unavailable JSON idiom in the
global CLAUDE.md) pointed the other way.

**Design decision — same split as Amendment 1.** A new `malformed_tile_fields(entry)` in
`_journal_schema.py`, called after `missing_tile_fields` and returning `[]` when `cwd` is absent,
so the two never double-report. It flags: a non-string value, an empty value, any control
character (reported *alone*, since it names the cause and the fix), surrounding whitespace, and a
value that is not absolute — a drive-letter root (`C:/…`, `C:\…`), a UNC root (`\\host\share`,
`//host/share`), or POSIX absolute (`/…`).

The UNC alternative and the whitespace check both came out of this PR's own `/review`, and both
are corrections in the *same* direction — toward the non-flag rule below. The first draft rejected
`\\wsl$\Ubuntu\…`, a valid absolute Windows path, as corrupt; the pattern now requires two
separators followed by a non-separator, which admits UNC while still rejecting a single-backslash
`\Users\brown\…` (drive-relative, not absolute — a genuine finding, and pinned as such). The
whitespace check exists because the absolute-path regex is start-anchored: leading whitespace was
reported with the misleading "not an absolute path" and *trailing* whitespace passed entirely,
even though Windows silently strips trailing spaces from path components, so both values compare
unequal to the path they resolve to.

**Two deliberate non-flags**, both narrowing the check to unambiguous corruption:

- **A correctly-escaped backslash path** (`C:\Users\brown\Git\dev-env`) is a *correct* value on
  Windows; only the escaping layer it must survive is fragile. Flagging it would fire this
  advisory on healthy shards every time one is merely named in a command, turning an advisory
  into a nag. The forward-slash prescription is therefore a **documentation** rule (ADR-118
  Amendment 4), not a validation one — layer 2 removes the failure mode, layer 1 only catches it.
- **Whether the directory exists.** `_journal_schema.py` is import-only and unit-tests offline,
  and shards are read on machines other than the one that wrote them, where a correct path
  legitimately resolves to nothing. Plausibility is the honest bar; a value with no path
  separator at all is corrupt regardless of host.

**Wiring.** `journal-shard-write-advisory.py` only —
`problems.extend(malformed_tile_fields(entry))` in `validate_shard_bytes`'s `kind == "tile"`
branch. Unlike Amendment 1 there is no second consumer: `validate-manifest.py` validates manifest
shards, and no compose-time gate reads tile shards at all. That asymmetry *is* the argument for
catching this at write time — the write-time hook is the only gate this class will ever pass
through. Its advisory text also gained a forward-slash prescription, and the tile schema template
it prints now shows a concrete `C:/Users/brown/Git/<target-repo>` rather than the placeholder
`<target repo path>` that left slash direction unstated.

---

## Amendment 3 — 2026-08-26: `stub`/`task_id` field validation (`malformed_tile_fields`)

**Closes:** [dev-env#907](https://github.com/brownm09/dev-env/issues/907)

**Incident.** `sessions/career-playbook/tiles/849.json`, found while investigating dev-env#904's
`cwd` corruption and deliberately left untouched there as out of scope, carried two ADR-118
deviations simultaneously: a stored `"task_id": "task_cdc4d05c"` (ADR-118 says explicitly this is
"deliberately not stored") and a bare-filename `"stub": "2026-07-23_021500.stub.md"` (ADR-118
requires project-qualified, `sessions/<project>/…`). Both passed every existing check: all seven
`TILE_REQUIRED_FIELDS` were present, `cwd` was syntactically fine, and neither
`missing_tile_fields` nor `malformed_tile_fields` (Amendment 2, `cwd`-only at the time) had any
opinion about either field. By the time this amendment landed, `849.json` no longer existed — its
issue had been closed and `reconcile-pending-tiles.py` had pruned the shard — so the specific file
is gone, but the class it represents was not: a live inventory sweep at fix time found 143 of the
(then-)246 existing tile shards, across 5 different projects, carrying at least one of the two
deviations (120 with `task_id`, 32 with a non-qualified `stub`, 9 with both) — six-to-ten times the
"19 shards" issue #907 itself estimated when filed, a month earlier.

**Root cause — two documented-but-unenforced rules.** ADR-118 states both rules in prose
(`task_id` "deliberately not stored"; `stub` "must be project-qualified… rather than the open-PR
shard's bare filename") but neither had ever had a mechanical check, unlike `cwd` (Amendment 2). A
tile shard is written by a session mid-flow — right after a `spawn_task` call — under pressure to
move on to the actual work the tile is capturing, which is exactly the condition under which a
documented-but-unchecked convention drifts: the open-PR shard's `stub` convention (bare filename)
is the more familiar sibling shape, and copying it is the easier mistake to make than inventing
the tile-specific rule from memory each time.

**Design decision — extend the existing checker, and change its return shape from short-circuit
to accumulate.** `malformed_tile_fields` gained two more field checks alongside its existing `cwd`
one, following the same "return nothing for an absent field, so `missing_tile_fields` remains the
sole reporter of omission" contract. But `cwd`'s internal branches are mutually exclusive facets
of *one* field (a value can't simultaneously be "empty" and "not absolute" in a way both need
reporting), so the original implementation could get away with returning as soon as it found the
single applicable branch. `cwd`, `stub`, and `task_id` are three *independent* fields, and
`849.json` was live proof a shard can have more than one of these wrong at once (it had exactly
two) — so the function was restructured to accumulate a `problems: list[str]` across all three
checks rather than return early on the first field examined. Existing `cwd`-only tests were
unaffected by the restructure (all 69 passed unchanged before any new test was added) since the
accumulation is additive: a `cwd`-only defect still produces exactly one problem, the same one it
always did.

`stub`, when present, gets the same control-character and surrounding-whitespace checks as `cwd`
(the write recipe that can corrupt one free-form-adjacent field can corrupt the other), then is
flagged unless it starts with `"sessions/"` after normalizing backslashes to forward slashes —
catching a value with *a* project-like prefix that isn't rooted there (e.g.
`"career-playbook/2026-08-03_012340.stub.md"`, also found live in the same sweep), not just a
fully bare filename. `task_id` needs no shape check: presence alone is the defect, so any present
value is flagged regardless of content.

**Found by this PR's own `/review` — three defects in the first draft, all fixed before merge:**

1. **The `stub` prefix test initially anchored on a literal `"sessions/"`**, so a
   backslash-separated but otherwise-qualified value (`sessions\dev-env\….stub.md`) was reported
   `is not project-qualified` — a wrong diagnosis for a value with no real defect, and a direct
   contradiction of the same function's own established rule for `cwd` (a backslash Windows path
   is a *correct* value; only the escaping layer it must survive is fragile). Fixed by normalizing
   before the prefix test, matching `cwd`'s existing leniency.
2. **`{value!r}` echoes non-ASCII intact.** Both the pre-existing `cwd` echo and the new `stub`
   echo interpolated the corrupt value via `!r}` (Python's `repr()`), which leaves printable
   non-ASCII characters as-is. Every message in this function rides the hook's exit-2 stderr,
   documented elsewhere in this repo as cp1252-decoded on Windows (dev-env#952's class) — so a
   non-ASCII corrupt value rendered the *diagnostic message itself* as mojibake, inside the text
   meant to explain the corruption. `cwd` carried this hole from Amendment 2 onward, unnoticed
   because no test fed it a non-ASCII fixture; `stub`'s new echo copied the same pattern. Fixed at
   both sites with `ascii(value)` in place of `{value!r}`, which force-escapes non-ASCII to
   `\xNN`/`\uNNNN` while keeping identical output for the ASCII case.
3. **The hook's "fix each file now, in this session — the write already happened" framing
   stopped being a safe default once these two checks landed.** It was accurate when the only
   tile shape check was `cwd` (three shards ever matched, all long since fixed) — over-matching
   was genuinely harmless at that rarity, per this ADR's own Decision point 2 (the Bash harvest
   validates *referenced* files, not only *written* ones, deliberately). At the ~60% match rate
   these two checks hit against the live shard inventory, a command that merely *names* an old
   shard — for example the ADR-118 Amendment 5 anomaly-restore recipe
   (`git checkout HEAD -- sessions/<project>/tiles/<N>.json`), which leaves the file on disk for
   this hook's next pass to read — now routinely tells a session it wrote and must fix a file it
   never touched, instructing it to redo work this same amendment's own non-scope decision
   explicitly deferred to dev-env#1064. Fixed by gating a new caveat sentence on whether any
   reported problem is `stub`/`task_id`-shaped: when it is, the advisory now says the problem may
   be pre-existing data merely referenced, not necessarily written by this session, and points at
   dev-env#1064. `cwd`/BOM/missing-field problems keep the original, more confident framing —
   those stayed rare enough that "you just wrote this" remains a safe default for them.

**Deliberate non-scope: no bulk historical sweep in this change.** The 143-shard finding above is
cleanup of *pre-existing* data, not a live bug this hook needs to also fix — and bulk-editing 143
files across 5 other projects' journal data in the same PR that adds the validator would be a far
larger, far riskier change than the validator itself, touching data this PR's own author does not
own the full context for (which of those shards' underlying issues are still live vs. already
stale). Filed separately: [dev-env#1064](https://github.com/brownm09/dev-env/issues/1064) — the
same issue point 3 above's caveat message points a session at, live.

**Wiring.** Same as Amendment 2 — `journal-shard-write-advisory.py` only, via the same
`problems.extend(malformed_tile_fields(entry))` call site, which needed no change since it already
treats the function's return value generically. Its advisory text gained a new guidance paragraph
stating both rules explicitly (gated on relevance — printed only when a reported problem is
actually `stub`/`task_id`-shaped, so a manifest shard's missing-field advisory doesn't also ship
the tile rules), a gated pre-existing-data caveat (point 3 above), and the tile schema template's
inline note changed from `(stub optional, project-qualified)` to `(stub optional, must start with
"sessions/<project>/"; no task_id key)`.
