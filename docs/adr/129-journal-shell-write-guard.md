# ADR-129: Mechanical Guard Against Shell-Based Writes to Engineering-Journal Content Files

**Date:** 2026-08-07
**Status:** Accepted
**Tags:** hooks, pre-tool-use, bash, powershell, journal, stubs, manifest, open-prs, tiles, shell-quoting, silent-corruption, prose, documentation, write-tool, edit-tool, global-rule, correction, hookout, adr-056, adr-081, adr-118

---

## Context

Every session maintaining the engineering journal creates or updates four content-file kinds
under `sessions/<project>/` in `brownm09/engineering-journal`: the stub `.md` (100% free-form
prose — session summaries), the manifest `.jsonl` shard (a free-text `topic` field), the
open-PR `.json` shard (free-text `topic`/path fields), and the tile `.json` shard (a
free-text `prompt` field plus a `cwd` path field). Sessions repeatedly reach for a
Bash/PowerShell heredoc or `echo`/redirect to write these, and shell quoting breaks on the
prose: an apostrophe closes a single-quoted string early, a `$`/backtick gets interpreted, a
nested `"` breaks a double-quoted string, a markdown code fence collides with a heredoc
delimiter. This either fails the write outright — wasting a turn on a retry, reported by the
user as happening "basically every time" — or, worse, silently corrupts the file.

**This is not a new failure mode; it is the same one recurring after an under-generalized
fix.** [dev-env#904](https://github.com/brownm09/dev-env/issues/904) (closed) found a tile
shard's `cwd` field corrupted by the `node -e "..."` serializer recipe then documented for
tile shards: the JS string-literal layer ate `\U`/`\G`/`\b`, producing a shard that parsed,
kept its numeric filename, and satisfied every presence check while naming no real directory.
[ADR-118](118-tile-persistence-shards.md) Amendment 4 fixed it with a forward-slash
prescription plus write-time schema validation — **not** by removing the shell step — and
says plainly that the prior rule "guarded the wrong field" (`prompt`, when `cwd` was the
actual hazard). That is exactly the shape of the gap this ADR closes, one level up: the
tile-shard hazard guidance in `claude/CLAUDE.md` and `docs/REFERENCE.md` was real and
detailed, but scoped to tile shards only — and even there, the prescribed fix was a "safer"
quoted-heredoc-into-`py -3 -c` recipe, still shell-based, not eliminated. Meanwhile:

- `claude/CLAUDE.md`'s stub-creation steps gave **zero creation mechanism** at all for the
  stub `.md` — the single highest-risk file of the four (100% prose, never a schema-checked
  JSON field) had never been in scope of any hazard guidance whatsoever.
- `docs/REFERENCE.md`'s **documented** recipes for the manifest and open-PR shards were
  themselves `echo '{...}' > file` — the anti-pattern the tile-shard section already warned
  against, left standing for the other two kinds.
- No script under `claude/scripts/` performs any of these four writes. Every journal-related
  script (`validate-manifest.py`, `journal-shard-write-advisory.py`, `reconcile-open-prs.py`,
  etc.) validates, guards, or reconciles around the write — never performs it.

Prose guidance for this exact problem class already existed once (the tile-shard rule) and
still failed to generalize to the file that needed it most. See dev-env#961 (tracking issue).

---

## Decision

Two changes, same PR:

### 1. Documentation: the Write/Edit tool is the sole method, for all four kinds

Every `echo`/heredoc/`node -e` example across `claude/CLAUDE.md` and `docs/REFERENCE.md` is
retired in favor of one uniform rule: **create with the Write tool; update a single field in
an existing file with the Edit tool.** Never a shell redirect, heredoc, or serializer script
— the Write tool takes content as a parameter with no shell interpretation at all, so none of
the three failure modes above (silent corruption, outright failure, wasted turn) can occur.
This applies uniformly to the stub `.md` (which had no prior guidance to retire) and to the
manifest/open-PR/tile shards (whose prior recipes are replaced outright).

### 2. Mechanical guard: `pre-tool-use-journal-shell-write-guard.py`

A new `PreToolUse` hook, registered under both the `Bash` and `PowerShell` matchers in
`claude/settings.json` (mirroring `pre-tool-use-canonical-mutate-guard.py` and
`pre-tool-use-journal-draft-worktree-guard.py`'s own dual registration), blocks a command
whose first physical line (of a top-level segment from `_hookio.split_top_level`) carries a
genuine (non-quoted) Bash `>`/`>>` redirect, or a PowerShell `Out-File` / `Set-Content` /
`Add-Content` / `Tee-Object` / `New-Item ... -Value` invocation, targeting a path shaped like
one of the four journal content files (`*.stub.md`, `*.manifest.jsonl`,
`open-prs/<digits>.json`, `tiles/<digits>.json`). A genuine leading `ALLOW_JOURNAL_SHELL_WRITE=1`
prefix overrides. Detection is pure text/regex work — no filesystem or subprocess access, no
`_winsubp` import — since "is this command's target path shaped like a journal content file,
and is it being shell-written-to" is answerable from the command string alone.

Emits via `_hookout.emit_block()` (ADR-103), not a hand-rolled `sys.stderr.write(json.dumps(...))`
— see Judgment calls.

---

## Judgment calls

### A PreToolUse block, not a PostToolUse-only advisory

`journal-shard-write-advisory.py` already validates manifest/open-PR/tile shard schemas
*after* a write lands — useful, and unchanged by this ADR. But it cannot prevent the wasted
turn on an outright quoting failure, cannot stop a silent corruption *before* the bad bytes
hit disk, and has no schema to check the prose-only stub `.md` against at all, so it
structurally can't cover that file kind. A `PreToolUse` block removes the anti-pattern before
any content is ever parsed by a shell — directly addressing the reported friction ("this
happens basically every time") rather than only detecting it afterward.

### `_hookout.emit_block()`, not the older siblings' hand-rolled writer

`pre-tool-use-canonical-mutate-guard.py` and `pre-tool-use-journal-draft-worktree-guard.py`
(this hook's closest structural siblings) hand-roll `sys.stderr.write(json.dumps({"reason": ...}))`
— but that pattern predates the `_hookout` migration (ADR-103) and is allowlisted in
`test_hook_output_contract.py` as a *known pre-migration offender*; a new hook must not join
that allowlist. The most recently added `PreToolUse` hook as of this writing,
`pre-tool-use-skill-file-size-guard.py` (ADR-127), already uses `_hookout.emit_block()` —
confirmed as the live convention, not just the documented aspiration, by reading that hook's
`main()` directly. `docs/REFERENCE.md`'s Authoring rule 6 is explicit on this too. This also
means the block reason is plain ASCII-sanitized text, not a `{"reason": ...}` JSON envelope —
the new test file's Layer-2 assertions use plain-text `in proc.stderr` checks accordingly
(matching `test_skill_file_size_guard.py`'s convention), not `json.loads(proc.stderr)["reason"]`.

### No `_winsubp` import, timeout 10 not 30

This hook spawns no subprocess at all — unlike its two structural siblings, which shell out
to `git` to resolve worktree/repo context, this hook's question is answerable purely from the
command string. `docs/REFERENCE.md` Authoring rule 6 ties the settings.json timeout floor to
subprocess use (`_winsubp` → 30, pure-Python → 10); `pre-tool-use-skill-file-size-guard.py` is
the existing precedent for a `PreToolUse` guard with neither.

### Masking an already first-line-truncated string needs its own helper

`_hookio.mask_quoted_spans()` assumes it may be given a real multi-line command where a
heredoc's declaration line is genuinely terminated by a newline. That assumption is false
once a segment has already been truncated to its own first physical line (the convention
both sibling hooks use, so a heredoc *body* is never mistaken for invocation syntax). Traced
through `_find_heredoc_end`: fed a first-line-truncated string like `cat <<'EOF' >
sessions/.../x.stub.md` with no following newline, the "skip to end of declaration line" scan
runs off the end of the string, consuming the redirect target as part of the heredoc's
opening declaration — and `_opaque_spans` then masks the entire remainder, including the real
redirect target this hook needs to see. `_mask_first_line_quotes()` neutralizes `<<`
(same-length, so offsets stay aligned) before masking, so the heredoc-opener branch never
fires on this input shape — safe, because a genuine `<<` inside a quote was never treated as
an opener by `_opaque_spans` in the first place. This is the load-bearing fix for detecting
the single most common real-world shape of the reported bug (`cat <<'EOF' > <journal-file>`),
pinned by a dedicated regression test,
`test_find_bash_redirect_targets_heredoc_declaration_line`.

### Lexical path matching only, no `sessions/`-prefix requirement

Unlike `journal-shard-write-advisory.py`'s path classifier, which can require a real
`sessions/<project>/` prefix because it resolves against an already-written on-disk file,
this hook has no resolution step to lean on and must work from the command text alone. The
four path shapes (`*.stub.md`, `*.manifest.jsonl`, a numeric-named file under `open-prs/`, a
numeric-named file under `tiles/`) are distinctive enough on their own; requiring a
`sessions/` prefix would risk missing a relative-path invocation issued with cwd already
inside `sessions/<project>/`.

### Deletion, directory scaffolding, and git plumbing stay unaffected by design

`rm -f`/`Remove-Item` deleting an open-PR or tile shard (the documented removal mechanism),
`mkdir -p .../tiles`/`New-Item -ItemType Directory` (no `-Value`) scaffolding the `tiles/`
directory, and `git add`/`commit -m "..." -- <path>`/`push` referencing these paths as
arguments are all outside this hook's detection scope by construction — only a genuine
redirect operator or one of five named PowerShell cmdlets is ever inspected. None of these
write content through a shell string-interpolation boundary, so none carry the hazard this
hook exists to prevent.

### Alternatives considered and rejected

- **Doc-only fix** (retire the `echo`/heredoc examples, add prose mandating Write/Edit) —
  rejected as the sole fix. Prose-only guidance for this exact problem class already existed
  once (the tile-shard rule) and still failed to generalize to the file that needed it most;
  a documented-but-unenforced rule is one incident away from the same failure recurring a
  third time.
- **PostToolUse-only advisory** — rejected as the sole fix; see the first Judgment call above.
- **Per-field whack-a-mole** (patch the next field that turns out to be hazardous, the way
  `cwd` was patched after `prompt`) — rejected per the ADR-118 Amendment 4 lesson directly:
  narrowly patching the symptom most recently observed is exactly the pattern that produced
  this recurrence. The fix removes the shell step from the whole content-write surface, not
  the one field or file kind most recently observed to break.

---

## Consequences

- A shell-based attempt to write any of the four journal content-file kinds now fails fast,
  before any content reaches a shell parser, with a message naming the matched command/target
  and the concrete Write/Edit-tool remedy — instead of failing unpredictably or corrupting
  silently.
- `journal-shard-write-advisory.py` ([ADR-081](081-write-time-journal-shard-validation-hook.md))
  remains the complementary `PostToolUse` backstop for schema mistakes made *through* a
  correctly-used Write/Edit call (a missing field, a malformed `cwd`) — this ADR does not
  replace it; a shell-escaping corruption and a correctly-shell-avoided-but-incomplete write
  are different failure classes with different fixes.
- One more `PreToolUse(Bash/PowerShell)` process spawn per matched tool call, pure Python
  with no subprocess work — negligible relative to the two sibling guards, which spawn `git`.
- No change to any existing hook, script, or shared module — this is a pure addition that
  imports only the already-shared `_hookio`/`_hookout`/`_hookutil`.
- Covered by `claude/scripts/tests/test_journal_shell_write_guard.py` (Testing item 88).
- Documentation reconciliation in the same PR: `README.md`, `claude/scripts/README.md`,
  `claude/scripts/tests/README.md`, `docs/TESTING.md`, and this repo's own root `CLAUDE.md`
  `## Testing` index.

---

## References

- `claude/scripts/pre-tool-use-journal-shell-write-guard.py` — the guard implementation
- `claude/scripts/tests/test_journal_shell_write_guard.py` — self-test
- `claude/settings.json` — hook wiring (both `Bash` and `PowerShell` `PreToolUse` matchers)
- `claude/CLAUDE.md` → Engineering Journal → Stub file workflow — the updated Write/Edit-tool
  mandate
- `docs/REFERENCE.md` → Engineering Journal Internals — the updated write recipes for all
  four content-file kinds
- [ADR-118](118-tile-persistence-shards.md), especially Amendment 4 — the "guarded the wrong
  field" precedent this ADR generalizes past
- [ADR-081](081-write-time-journal-shard-validation-hook.md) — the complementary `PostToolUse`
  schema validator this ADR does not replace
- [ADR-056](056-per-session-sharding-journal-companion-files.md) — defines the manifest/
  open-PR shard formats this hook protects
- [ADR-103](103-shared-hookout-emitter.md) — the `_hookout` emitter and its per-event channel
  contract this hook's block emission relies on
- [ADR-127](127-skill-file-size-guard.md) — the most recently added `PreToolUse` blocking
  hook, and the direct precedent for `_hookout.emit_block()` over a hand-rolled writer
- `brownm09/dev-env#904` — the tile-shard `cwd` corruption incident that first exposed this
  problem class
- `brownm09/dev-env#961` — tracking issue for this fix
