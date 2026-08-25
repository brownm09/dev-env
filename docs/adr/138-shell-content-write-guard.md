# ADR-138: Content-Shaped Guard Against Shell-Written File Content (Generalizing ADR-129 Past Its Path Test)

**Date:** 2026-08-25
**Status:** Accepted
**Tags:** hooks, pre-tool-use, bash, powershell, shell-quoting, silent-corruption, prose, write-tool, edit-tool, global-rule, bypass-permissions, precedence, session-mode-prompt, shared-module, hookout, correction, adr-010, adr-024, adr-103, adr-129, adr-133

---

## Context

[ADR-129](129-journal-shell-write-guard.md) established that shell-based writes of file *content*
routinely fail or silently corrupt on prose quoting, and mandated the Write/Edit tools — backed
mechanically by `pre-tool-use-journal-shell-write-guard.py`. Its scope is **the four
engineering-journal content-file kinds only** (`*.stub.md`, `*.manifest.jsonl`,
`open-prs/<N>.json`, `tiles/<N>.json`).

The failure mode was never journal-specific. It is *"prose- or escape-bearing content routed
through a shell."* The journal was simply where it was noticed first — the same
under-generalization ADR-129 itself diagnosed one level down, where the tile-shard rule "guarded
the wrong field" ([ADR-118](118-tile-persistence-shards.md) Amendment 4). ADR-129 fixed the field
and the file kind; it left the *class* uncovered.

### Evidence: three occurrences in one session, none of them journal files

All from the lifting-logbook session of 2026-08-25
([PR #945](https://github.com/merickvaughn/lifting-logbook/pull/945)), recorded in
[dev-env#1041](https://github.com/brownm09/dev-env/issues/1041):

1. **Authoring a guard script.** `scripts/check-turbo-dev-build-dep.mjs` written via a quoted
   heredoc; bash collapsed `'\\'` to `'\'` and node rejected the file with
   `SyntaxError: Invalid or unexpected token`. The file *looked* written until it was executed.
2. **A `node -e` one-liner editing a file.** The apostrophe in the word `user's` closed the
   enclosing single-quoted shell string: `syntax error near unexpected token '('`. Same shape as
   dev-env#904, which was fixed for tile shards only.
3. **Writing a PR-body markdown file.** `cat > body.md << 'BODY_EOF'` — the **quoted**-delimiter
   form — still failed with `unexpected EOF while looking for matching '''`.

**Occurrence 3 is the load-bearing one.** The quoted heredoc is precisely the mitigation someone
reaches for after reading the existing guidance, and it failed on content with no obvious hazard.
Occurrence 1 used it too. Whatever re-processing produces that, "I quoted the delimiter" cannot be
treated as evidence of safety — so this ADR's hazard test deliberately does **not** exempt the
quoted form, and the prose rule stops naming quoting as the fix.

### The contradiction that kept the gap open

Bypass-permissions sessions receive a standing harness instruction pointing the opposite way:

> Do your work through the Bash tool wherever it can accomplish the job: … make file changes with
> `sed`, heredocs, or short scripts, rather than using the dedicated Read, Edit, or Write tools.

Measured live from `session-mode-prompt.py`'s own log (5824 entries): `permission_mode` is
`bypassPermissions` in **4579 of them — 79% of all sessions**, against `plan` 1219, `auto` 17,
`default` 4. The contradicting instruction is present in four sessions out of five, and no
tiebreaker existed anywhere. A session following it literally reproduces all three failures above.
That is why prose alone had not closed this, and why a tiebreaker — not merely a wider rule — is
the core of the decision.

---

## Decision

Three changes, same PR.

### 1. The rule becomes content-shaped, not path-shaped

A new `## Authoring File Content` section in `claude/CLAUDE.md` (between `## Tool Discovery` and
`## CLI Scripting Checklist`): **Write/Edit is the default for file *content*; Bash is the default
for *commands*.** Shell redirection is correct for exactly two things — another program's output
(`gh … > "$TMPFILE"`), and a single-line literal with no apostrophe, backtick, or backslash
(`echo done > flag.txt`). Editing an existing file goes through Edit, not `sed -i` / `node -e`.

The four journal kinds become the explicitly-named **strict special case**: blocked
unconditionally, however short or clean, because they always carry free prose or a path field.

### 2. The bypass-mode contradiction is resolved by scope, and delivered where it is created

The precedence statement is a **reconciliation, not an override**: the bypass instruction governs
*shell work* — running commands, inspecting state, reading files, invoking tooling — where Bash
genuinely is the right default and stays so. It does not govern *authoring file content*. The two
only ever looked contradictory because nothing had said which half of "file changes" each one owns.

The harness prompt is not editable from this repo (verified: zero in-repo occurrences of that
instruction's text, in any file). So the carve-out is *also* delivered into the same context, in
the same sessions, at the same moment, by extending `session-mode-prompt.py` — which already reads
`permission_mode` and already fires exactly once per session. Non-bypass modes get the base
reminder unchanged; there is no contradiction to resolve there.

### 3. `pre-tool-use-shell-content-write-guard.py`

A new `PreToolUse` hook under both the `Bash` and `PowerShell` matchers, wired **after** the
ADR-129 guard so a journal-path write keeps that hook's more specific remedy message. Blocks a
command that writes an **inline literal** to a file when the literal is multi-line or carries an
apostrophe, backtick, or backslash. Emits via `_hookout.emit_block()` (ADR-103), timeout 10, no
`_winsubp` (no subprocess, no filesystem access). Override: `ALLOW_SHELL_CONTENT_WRITE=1` (Bash
prefix, segment-scoped) or `$env:ALLOW_SHELL_CONTENT_WRITE=1` (PowerShell statement, applies
forward).

Detectors, each requiring an inline literal *and* a file destination: heredoc/here-string body
redirected to a file; an `echo`/`printf` literal redirected to a file; a `node -e`/`py -3 -c`
script that writes a file or is redirected into one; `sed -i` / `perl -pi -e` (whose target is the
file itself); and PowerShell `Set-Content`/`Add-Content`/`Out-File`/`Tee-Object`/`New-Item -Value`
fed a `-Value` literal or a piped `@'…'@` here-string.

### Shared module: `_shell_write_detect.py`

The quote-masking, redirect-finding, and tokenizing primitives move out of the ADR-129 guard into a
shared module both hooks import — a pure move, no behavior change. Hand-copying was rejected:
ADR-129 Amendment 1 findings #1 and #5 both landed *inside* those exact functions, so a second copy
would be a second place a future fix must remember to reach. Same reasoning, and the same
"consumer suites must stay green" safety claim, as [ADR-133](133-shared-journal-canon-module.md)'s
`_journal_canon.py`. The ADR-129 guard's 63-case suite passes **unchanged** across the extraction.

---

## Judgment calls

### Why this is not the doc-only fix ADR-129 rejected

ADR-129 rejected a doc-only fix because "prose-only guidance for this exact problem class already
existed once (the tile-shard rule) and still failed to generalize." That reasoning applies with
*more* force here, not less — the guidance now has to survive being contradicted, in 79% of
sessions, by an instruction that is more proximate and more specific than any CLAUDE.md section.
Widening the prose alone would have shipped a rule with a known standing counterparty. Hence a
mechanical block, and hence the second delivery channel in bypass sessions specifically.

### Why the inline-literal / program-output split is what makes a fleet-wide block safe

The obvious objection to a guard on every Bash call in every repo is over-matching, and ADR-129
Amendment 1 is the cautionary precedent: that hook, with a green 39-case suite, both under-matched
its own motivating incident and over-matched unrelated commands globally.

The split that avoids it here is **structural, not heuristic**. An inline literal (a heredoc body,
an `echo` argument, a `-Value`, a `-e` script) crosses a shell string-parsing boundary, so the
shell can mangle it. Program output (`gh … > f`, `npm test > out.log`, `… | tee f`) never does.
"No inline literal found" is not a judgment call the hook makes — it is a detector that fires or
does not, so the entire redirect-heavy everyday command surface is outside the hook by
construction. This is also exactly the "short, known-safe, machine-generated" carve-out the prose
rule names, which is why the two cannot drift apart.

### The hazard line is the same sentence in the doc and in the code

Block unless the literal is single-line and free of `'`, backtick, and backslash. Deliberately the
same wording as `## Authoring File Content`, so guidance and mechanism cannot diverge the way
ADR-129's REFERENCE.md recipes diverged from its own tile-shard warning.

### Quote characters are structure, not content — but only where the shell agrees

Two hazard tests, not one. `body_hazard` treats a heredoc/here-string body as raw content and tests
the markers literally. `arg_hazard` walks a shell-quoted argument's quote state and reports a marker
only where the shell would act on it: a delimiting quote is structure (so `echo 'done' > f` passes),
an apostrophe inside double quotes or an escaped `\'` is content the author had to fight for (so it
blocks), and a backslash is a hazard **outside** single quotes but not inside them.

That last clause is what keeps `sed -i 's/a\.b/c/'` — an everyday, genuinely safe idiom — working
while `sed -i "s/a\\b/c/"` (occurrence 1's shape, where the shell really does eat the backslash)
still blocks. Notably this is **not** a `sed` special case: it falls out of the general principle
that the shell only mangles what it can see, and it applies identically to `echo`, `node -e`, and a
PowerShell `-Value`. One rule, no per-mechanism exceptions.

### `sed -i` is in scope; `sed` without `-i` is not

The bypass instruction names `sed` explicitly, so leaving in-place edits out would leave the
contradiction half-resolved. An in-place edit needs no separate destination — the file *is* the
target. A `sed` without `-i` writes nothing and is never matched.

### Both override tokens are honoured

The new guard also accepts ADR-129's `ALLOW_JOURNAL_SHELL_WRITE=1`. Without that, a deliberate
journal-guard override would clear hook 1 and then be blocked by hook 2 — silently breaking a live,
documented escape hatch. Pinned by `test_journal_override_token_also_exempts`.

### Pre-filter: plain `in`, not a regex

`might_write_content` uses plain substring checks against one `.lower()` call. ADR-129 Amendment 1
finding #11 measured a lookaround-anchored regex at ~11.7ms against ~0.28ms for plain checks on the
same 100k-char input — i.e. *slower* than the full walk it was meant to short-circuit. Recorded
again here because the intuition that a compiled pattern must be faster is durable and wrong.

### Two named, accepted scope boundaries

Pinned by explicit tests that fail loudly if the behavior changes, following Amendment 1 finding
#9's precedent rather than leaving them silent:

- **A heredoc to a command's stdin, not a file** (`gh pr create --body-file - <<'EOF'`,
  `git commit -m "$(cat <<'EOF' … )"`). Same hazard, no file destination, so the
  inline-literal-to-a-*file* rule does not reach it. Widening to "any heredoc anywhere" would pull
  in `gh api --input -` and similar, and was judged not worth the false-positive surface in v1.
  Test: `test_accepted_gap_heredoc_to_command_stdin`.
- **Only the first heredoc opener on a line is inspected.** Two heredocs on one command line is
  vanishingly rare, and one hazardous body is enough to block.
  Test: `test_accepted_gap_second_heredoc_on_one_line`.

### Narrow supersession of ADR-010

[ADR-010](010-skill-tmpfile-allow-rule.md) reached the opposite conclusion for scratch writes —
"The Bash heredoc is more natural and idiomatic" — and added `Bash(TMPFILE=*)` to
`permissions.allow`. That reasoning predates this evidence and is superseded **for content
authoring only**:

- **The allow rule stands.** The canonical `TMPFILE` recipe is
  `some-command --format json > "$TMPFILE"` — program output, never an inline literal, entirely
  outside this hook, and it still needs its prompt exemption.
- **ADR-010's `$$`-uniqueness argument stands too**, and is preserved by generating the filename in
  Bash and writing the *content* with Write.

Only the heredoc-for-content endorsement is retired. ADR-010 carries a pointer to here.

### Two live in-repo instructions had to be fixed in the same PR

`biweekly-retro` and `weekly-memory-audit` each told Claude to `cat > …/README.md <<'EOF'` — so the
guard would have blocked its own repo's routines on day one. Both now prescribe the Write tool.
This is the same class as Amendment 1 finding #10 (`journal-shard-write-advisory.py` still
instructing the anti-pattern its own ADR retired): a new rule requires sweeping the repo for
instructions that contradict it, not just adding the rule. The `cat > … <<'JSON'` occurrences in
`claude/scripts/tests/*.sh` are unaffected — they are file *contents* of scripts invoked as
`bash <file>`, and the hook only ever sees the outer `bash …` command.

### Alternatives considered and rejected

- **Doc-only widening + the bypass carve-out, no hook.** Rejected — see the first judgment call.
- **Widening the ADR-129 guard's path set.** Rejected as structurally impossible: outside the
  journal there is no path shape that identifies content-bearing files. The hazard is the content,
  so the test has to be too. This is the ADR's central finding.
- **A PostToolUse advisory instead of a PreToolUse block.** Rejected for ADR-129's reason (it
  arrives after the corruption) and one more: `PreToolUse` forwards neither stream on exit 0, so an
  always-exit-0 advisory at this event would be invisible regardless of stream — see this repo's
  `## Observability` section. Block or nothing.
- **Blocking only on a hazard marker, ignoring length.** Rejected: it would let a long clean prose
  document through a shell, which is occurrence 3's shape, and it would make the doc rule and the
  mechanism say different things.

---

## Consequences

- A hazard-bearing inline-literal file write now fails fast, before any content reaches a shell
  parser, naming the mechanism, the command, the target, and the specific hazard.
- Everyday redirection is untouched by construction: program output, pipelines, `/dev/null`, plain
  reads, `git commit -m`, greps carrying prose, and short clean literals all pass at the detector
  layer — not by override.
- Bypass-permissions sessions receive the precedence carve-out in-context, once per session.
- `_shell_write_detect.py` is now a shared dependency of two `PreToolUse` hooks; a change to it must
  keep **both** consumer suites green (`## Testing` item 94 runs them together).
- One more pure-Python `PreToolUse` process per Bash/PowerShell call, short-circuited by the cheap
  pre-filter on the overwhelmingly common no-marker command.
- Covered by `claude/scripts/tests/test_shell_content_write_guard.py` (Testing item 95, 43 cases,
  including a verbatim reproduction of each dev-env#1041 occurrence) and
  `claude/scripts/tests/test_shell_write_detect.py` (item 94, 21 cases). Item 70
  (`test_session_mode_prompt.py`) grew two cases for the bypass carve-out.
- Documentation reconciliation in the same PR: `README.md`, `claude/scripts/README.md`,
  `claude/scripts/tests/README.md`, `docs/REFERENCE.md`, `docs/TESTING.md`, and this repo's root
  `CLAUDE.md` `## Testing` index.

---

## References

- `claude/scripts/pre-tool-use-shell-content-write-guard.py` — the guard implementation
- `claude/scripts/_shell_write_detect.py` — the shared primitives, extracted from ADR-129's guard
- `claude/scripts/session-mode-prompt.py` — where the bypass-mode carve-out is delivered
- `claude/CLAUDE.md` → Authoring File Content — the rule and its precedence statement
- [ADR-129](129-journal-shell-write-guard.md) — the journal-scoped predecessor this generalizes
- [ADR-010](010-skill-tmpfile-allow-rule.md) — narrowly superseded (heredoc-for-content only)
- [ADR-024](024-worktree-path-guard-hook.md) — already recorded that Bash redirects/`tee`/heredocs
  slip past the Write/Edit path guard; this change narrows that gap as a side effect
- [ADR-103](103-shared-hookout-emitter.md) — the `_hookout` emitter contract
- [ADR-133](133-shared-journal-canon-module.md) — the shared-module extraction precedent
- [ADR-118](118-tile-persistence-shards.md) Amendment 4 — the "guarded the wrong field"
  under-generalization this ADR is the next level up from
- `brownm09/dev-env#1041` — tracking issue, with all three occurrences
- `brownm09/dev-env#961`, `#904` — the journal-scoped incidents that preceded it
