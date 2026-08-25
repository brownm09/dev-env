# ADR-138: Content-Shaped Guard Against Shell-Written File Content (Generalizing ADR-129 Past Its Path Test)

**Date:** 2026-08-25
**Status:** Accepted
**Tags:** hooks, pre-tool-use, bash, powershell, shell-quoting, silent-corruption, prose, write-tool, edit-tool, global-rule, bypass-permissions, precedence, session-mode-prompt, shared-module, hookout, correction, measurement, over-match, replay, transcripts, instrument-calibration, heredoc, adr-010, adr-024, adr-103, adr-106, adr-129, adr-133

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
  **→ Narrowed by [Amendment 1](#amendment-1-measured-over-match-rate-and-the-stdin-heredoc-gap-dev-env1046)
  on measured evidence: the half feeding a content-publishing command's own prose argument is now
  in scope; the interpreter-stdin half remains an accepted gap.**
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
- Covered by `claude/scripts/tests/test_shell_content_write_guard.py` (Testing item 95, 53 cases
  after Amendment 1, including a verbatim reproduction of each dev-env#1041 occurrence) and
  `claude/scripts/tests/test_shell_write_detect.py` (item 94, 21 cases). Item 70
  (`test_session_mode_prompt.py`) grew two cases for the bypass carve-out. Amendment 1 adds
  `claude/scripts/tests/test_replay_shell_content_guard.py` (item 96, 15 cases).
- Documentation reconciliation in the same PR: `README.md`, `claude/scripts/README.md`,
  `claude/scripts/tests/README.md`, `docs/REFERENCE.md`, `docs/TESTING.md`, and this repo's root
  `CLAUDE.md` `## Testing` index.

---

## References

- `claude/scripts/pre-tool-use-shell-content-write-guard.py` — the guard implementation
- `claude/scripts/replay-shell-content-guard.py` — the Amendment 1 reader: replays the guard over
  recorded session transcripts and reports block rate, mechanism mix, override use, and the
  failure-rate enrichment ratio
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

---

## Post-review hardening (same PR, dev-env#1042)

`/review` on PR #1042 surfaced three findings in the new hook, each **verified by executing the
hook's own functions** before being reported — not accepted from inspection, the discipline ADR-129
Amendment 1 established after several of its own findings turned out to need direct re-testing.
All three were fixed in the same PR, with a checked-in regression test each (the suite grew 43 → 47).

**Blocking, correctness:**

1. **GNU sed's long form was not detected.** The in-place flag test was `^-[a-z]*i`, whose `[a-z]*`
   cannot cross a second `-` — so `sed -i "s/a\\b/c/"` blocked while `sed --in-place "s/a\\b/c/"`
   sailed through, an inconsistency *inside* the scope this ADR explicitly claims. Fixed with a
   second `--in-place(=|$)` arm, factored into `_is_inplace_flag`.
2. **A safe PowerShell here-string masked a hazardous `-Value`.**
   `find_powershell_content_write` returned only its first candidate, and checked the here-string
   first — so `@'…safe…'@ | Set-Content a.md; Set-Content b.md -Value "it's hazardous"` reported
   **nothing**, while the identical `-Value` in isolation blocked correctly (both verified live).
   One benign literal suppressing a hazardous sibling is exactly the under-match class Amendment 1
   warned about. Fixed by returning every candidate and letting the caller block on the first
   hazardous one.

**Non-blocking, maintainability:**

3. **The `<#` sentinel collides with real PowerShell syntax.** Masking rewrites a genuine unquoted
   `<<` to `<#` — which is also a PowerShell block-comment opener, so `Get-Process > procs.txt
   <# note … #>` parsed as a heredoc. Worth recording precisely, because the investigation did
   **not** confirm the hypothesis it started from: no false block and no false negative could be
   produced from it (in every reachable shape the misparsed "body" was either benign or still
   blocked). Its only demonstrated effect was a *wrong reason string* — `cat <#tag > notes.md
   <<'EOF'` blocked as "spans more than one line" instead of "contains an apostrophe", because the
   misparse swallowed the rest of the segment. So it was reported as maintainability, not
   correctness. The one-line fix — confirm the raw line carries `<<` at the matched offset, which
   masking's length-preservation guarantees is comparable — removes the whole class and, as a
   bonus, stops a literal `<#` from shadowing a real `<<` later on the same line.

**Also fixed (performance):** the `tee` detector ran a full `shlex.split` on every Bash segment
before checking whether the line could start with `tee` at all. Guarded with a cheap `startswith`
match first — the same "this runs on every call fleet-wide" reasoning as the module-level
pre-filter.

---

## Amendment 1: measured over-match rate, and the stdin-heredoc gap (dev-env#1046)

**Date:** 2026-08-25 · **Tracking:** [dev-env#1046](https://github.com/brownm09/dev-env/issues/1046)

Two follow-ups, sequenced because the second needed the first's data: *(1)* the guard leaves no
record of what it blocks, so nobody can say whether it over-matches; *(2)* the stdin-heredoc gap
above was left open for want of exactly that evidence.

### The finding that reframed item 1: the record already existed

The issue proposed an append-only block log under `scratch/`, and asked that it be weighed rather
than assumed. Weighing it produced a different answer: **every Bash and PowerShell command Claude
Code has ever run is already recorded**, in the session transcripts under
`~/.claude/projects/<project>/<session>.jsonl`, together with its `tool_result`. What was missing
was never the record — only a reader for it.

That is strictly more than a hook-side log could hold, and the difference is not incidental:

- A block log records *that a block happened*. A transcript records the command, **whether it
  actually failed**, and what was done next. The one metric that distinguishes "targeting the right
  population" from "over-matching" is a ratio needing **both** arms — blocked and unblocked — so it
  is computable only from the transcripts, never from a log of blocks alone.
- A log describes only the hook version live when each line was written. A replay runs the
  **current** code over the **same** corpus, so it doubles as a regression instrument: change a
  detector, re-run, see precisely which real commands changed classification.
- A forward log answers nothing until months of traffic accumulate. The replay answered it the same
  afternoon, over 54,176 unique commands spanning 2026-06-13 → 2026-08-25.

So: **no forward log. Ship the reader** — `claude/scripts/replay-shell-content-guard.py`
(`## Testing` item 96), on-demand, reading only, zero hot-path cost.

One incidental correction the weighing turned up, recorded because this ADR's own cost argument
leaned on it: the hook's docstring claimed it "touches no filesystem." Its *detection logic* does
not, but `_hookutil.record_heartbeat` ([ADR-106](106-hook-heartbeat-liveness-ledger.md)) performs an
unconditional tmp-file write plus `os.replace` on **every** invocation, as the first statement of
`main()`. A log written only on a match would therefore have been *cheaper* than what the hook
already pays unconditionally — the no-I/O objection was never the real reason to decline it, and
saying so mattered more than winning the point. The docstring now states this precisely.

### What the replay measured

Corpus: 2,487 transcripts, 59,928 Bash/PowerShell calls, 54,176 unique.

| Population | n | Shell-parse-failure rate | vs baseline |
|---|---:|---:|---:|
| **Baseline** — all commands | 54,176 | **0.28%** (152) | 1× |
| **Blocked** — v1 rule | 1,584 (2.93%) | **3.41%** (54) | **12.2×** |
| **Blocked** — with Amendment 1 | 1,740 (3.21%) | **3.28%** (57) | **11.7×** |
| Gap: content argument (`--body-file -`, `-F -`) | 161 | 1.86% (3) | 6.6× |
| Gap: interpreter stdin (`py -3 - <<'PY'`) | 1,029 | 1.65% (17) | 5.9× |

**The guard is not over-matching.** Its block set concentrates real shell-parse failures **~12×**
over baseline, and holds 36% of every such failure in the corpus while covering under 3% of
commands. Mechanism breakdown confirms it at the detector level: the `serializer` arm fired on a
script's own write call 595 times against just 5 redirect-only matches, so the "program output that
merely happens to be redirected" edge is negligible rather than dominant. Sampling the blocks shows
PR bodies, review comments, outreach prose, and `py -3 -c` edits of ADRs — the targeted class, and
one sampled body already carries mojibake from a real corruption.

Two things the numbers do **not** say, stated so they are not read into them. Blocks are frequent in
absolute terms — ~21/day fleet-wide — so this is a high-volume intervention, correct but not cheap.
And 98% of blocks fire on *"spans more than one line"* rather than a hazard marker, exactly as
designed (see *Alternatives rejected → blocking only on a hazard marker*); the failure-rate
enrichment is what justifies that length rule, and it is now measured rather than asserted.

`ALLOW_SHELL_CONTENT_WRITE` has been used **0** times (it shipped the same day); its ADR-129 sibling
`ALLOW_JOURNAL_SHELL_WRITE`, **11**. Override frequency is the strongest available
false-positive signal — a human explicitly disagreeing — and it needs no new instrumentation, so the
reader reports it.

### Item 2: widen narrowly, on the evidence

The gap splits cleanly in two, and v1 had lumped them together:

- **A heredoc feeding a content-publishing command's own prose argument** — `gh pr create
  --body-file -`, `gh issue comment --body-file -`, `git commit -F -`. Authored prose crossing a
  shell boundary, 6.6× baseline, with three observed `unexpected EOF while looking for matching '''`
  failures on exactly this shape. **Now blocked** (`stdin-content-arg`).
- **A heredoc feeding an interpreter's stdin** — `py -3 - <<'PY'`, `python - <<'EOF'`. **1,029 of
  the 1,190 gap commands (86%). Remains an accepted gap**, and not as a concession: that body is a
  *program*, not file content, so "use the Write tool" is the wrong remedy, and blocking it would
  break the most common multi-line-Python idiom in this fleet at ~15/day.

The decisive argument was not the failure rate but an **inverted incentive** v1 created. The same PR
body written as `cat > body.md <<'EOF'` blocked, while `gh pr create --body-file - <<'EOF'` passed —
the form with *no artifact left behind to inspect* when the shell mangles it. A model that hit the
block could "fix" it by switching to the more dangerous shape. That is not a scope boundary; it is a
hole, and PR #1042's own commit used exactly that shape.

Widening stays safe for the same structural reason the original inline-literal/program-output split
does. The detector is anchored to a **closed allowlist** — first token `gh` or `git`, plus an
explicit stdin content flag (`--body-file`, `--notes-file`, `--file`, `-F`). An interpreter reading a
program from stdin carries no such flag, so the 86% majority is excluded **by construction, not by
judgment**. `gh api --input -` stays deliberately out: a JSON payload is machine-generated, not
authored prose, and it was the specific false-positive surface v1 named. Pinned by
`test_amendment1_content_arg_allowlist_stays_closed`.

`git commit -m "$(cat <<'EOF' … )"` — a heredoc inside a command substitution — is **still** out of
scope. Same hazard; detecting it means reaching inside `$()`, which no detector here does. Recorded
as a known remaining gap rather than silently left.

### Instrument calibration (the part that nearly produced the opposite conclusion)

The first outcome pass scored a command as "shell-mangled" on a signature set that included
`IndentationError` and `SyntaxError: invalid syntax`. For a `py -3 - <<'PY'` heredoc those are
almost always the *author's own Python bug* — the heredoc delivers the body verbatim — so they
measured authoring quality, not shell mangling. That inflated the interpreter-stdin rate from 1.66%
to 10.2% and made it look like the **highest-risk** class in the corpus, i.e. the strongest argument
for exactly the broad widening the evidence ultimately refutes. Narrowing to errors only the shell
itself emits reversed the ranking.

Recorded because the failure mode is general and quiet: an uncalibrated instrument does not return
an error, it returns a confident number pointing the wrong way. This is the global `## Experimental
Rigor` rule's "instruments calibrated against known-good AND known-bad references before any arm is
scored," met the hard way. `SHELL_FAIL_RE` carries the lesson in a comment, and the reader
recomputes its own baseline in the same run so a future widening of that pattern cannot be compared
against a stale figure copied from this document.

### Consequences

- `claude/scripts/replay-shell-content-guard.py` is the standing instrument for this question.
  Re-run it after any detector change: `py -3 claude/scripts/replay-shell-content-guard.py --gap`.
- **Tripwire:** enrichment falling toward ~1×, or `ALLOW_SHELL_CONTENT_WRITE` use becoming routine,
  means the guard has started firing on ordinary traffic. Either is cause to re-scope it.
- The hook gains one detector and one pre-filter marker (`<<` — without it the new shapes, which
  carry no redirect and no mechanism keyword, would never reach the walk; pinned by
  `test_prefilter_admits_heredoc_with_no_redirect`).
- `stdin-content-arg` carries its own remedy text, because "New file → Write, existing file → Edit"
  names no destination for a body piped into stdin. It also states in the block message that an
  interpreter reading a program from stdin is *not* blocked — otherwise the natural over-correction
  is to abandon `py -3 - <<'PY'` too, which this rule never asked for.
- Suite: 47 → 53 cases; the accepted-gap test was **renamed and rewritten**, not deleted, so the
  narrowed boundary still fails loudly if it moves again.
