# ADR-121: `/journal-compose` Scans Its Own Output for Stray Terminal Text, Advisorily

**Date:** 2026-07-23
**Status:** Accepted
**Tags:** journal, composition, skill, validation, gate, stray-output, silent-failure, data-loss, fence-aware, advisory, readme, progress-summary, adr-011, adr-081, adr-104, adr-114

---

## Context

The 2026-07-11 `/journal-compose` run wrote the standard `git rebase` "no tracking information"
usage message into the middle of a paragraph in engineering-journal's
`sessions/dev-env/README.md` `## Progress Summary`:

```
...and (6) PR [#725](...) opened documenting that bare `git rebase --force`/`--onto` auto-closes open PRs.
Please specify which branch you want to rebase against.
See git-rebase(1) for details.

    git rebase '<branch>'

If you wish to set tracking information for this branch you can do so with:

    git branch --set-upstream-to=<remote>/<branch> claude/festive-carson-43d65a/ pattern that auto-closes open PRs. ADR count now at 101+.
```

`git log -S "See git-rebase(1)" --all -- sessions/dev-env/README.md` returns that compose's own
commit (`de23d2f0` / `27669e28`) as the **earliest** containing the text. The paragraph was *born*
corrupted — no later edit introduced it — and it then survived roughly **eight subsequent compose
passes and eleven days**, each of which read, rewrote, and re-committed that README while carrying
the junk forward untouched. It was found by a human reading the file, not by any check.

Three properties of the incident drove the design:

**1. It was self-concealing, not merely additive.** The paste did not append a tidy block; it ate
the middle of a sentence and welded a surviving real fragment — `" pattern that auto-closes open
PRs. ADR count now at 101+."` — onto the tail of the `git branch --set-upstream-to` line. A tool
that recognised the machine-generated shape and deleted it would have silently destroyed a real
sentence, converting a visible corruption into an invisible one. It also truncated the clause that
*preceded* it, leaving behind ``bare `git rebase --force`/`--onto` `` — a phrase describing a flag
that does not exist (`--force-rebase` is the real one), which is itself evidence the surviving text
was contaminated rather than original.

**2. One bad pass produced more than one defect.** The same compose also mis-attributed dev-env#728
as PR #725's issue in the Entries table two screens down, when the correct issue is #724
(engineering-journal#185). So the failure mode is "a compose run goes wrong in several places at
once", which means a hit is a reason to re-read the whole file, not just the flagged lines.

**3. The existing guard could not have caught it.** [ADR-011]-warranted work under dev-env#467
(closed 2026-07-04) added the Step 6.5 structural assertion, which validates that a composed file
carries all eleven canonical section headings. That is orthogonal on two axes: it inspects journal
*entries*, not README Progress Summaries, and it inspects *headings*, so arbitrary mid-paragraph
body text passes it cleanly. The corruption landed 2026-07-11 — a week after #467 shipped — which
is direct evidence rather than inference.

## Decision

Add `claude/scripts/validate-composed-output.py` (pure logic in `_composed_output_scan.py`) and wire
it into the `journal-compose` skill as **Step 8b**, running two narrow checks over every file the
compose writes.

### Placement: after the last write, before stub deletion

Step 8b sits after Step 8a (the final write) and before Step 9 (which deletes the source stubs).
Both boundaries are load-bearing:

- **After all writes** — Step 6.5 is too early. It runs before Step 7 and Step 8 have written the
  folder README and the top-level README, which is precisely where the motivating corruption lived.
- **Before Step 9** — when the gate fires, the stubs that produced the text still exist, so the
  original wording can be recovered by diffing the flagged region against its source. Placing the
  gate after stub deletion would leave a corrupted, truncated paragraph with its source destroyed.

### Two complementary checks

| Check | Fires on | Exemptions |
|---|---|---|
| `signature` | A known git usage/error string in prose | fenced code blocks, inline code spans |
| `progress-summary-indent` | An indented line inside a `## Progress Summary` section | fenced code blocks |

The signature list is deliberately short: `See git-rebase(1)`, `--set-upstream-to`, `Please specify
which branch`, `There is no tracking information`, `Everything up-to-date`, `nothing added to
commit`, plus `hint: ` and `fatal: ` **anchored to line start** (unanchored, "the `fatal:` line was
suppressed" is ordinary prose and would make the gate noise).

The indent check exists because Progress Summary prose is never indented — a scan of all ten
`sessions/*/README.md` found exactly two indented lines in the entire corpus, and both were this
bug. The two checks overlap on the real incident by design: the non-indented pasted lines trip
`signature`, the indented command lines trip `progress-summary-indent`, and the
`--set-upstream-to` line trips both. A regression in either check still leaves the incident caught.

### Fence- and span-awareness is a hard requirement, not a refinement

Journal entries legitimately quote git errors as documented content —
`sessions/career-playbook/2026-06-21-…:75`, `sessions/dev-env/2026-07-02-…:392`, and
`sessions/dev-env/2026-07-13-…:190` all contain `fatal:` inside code fences. A gate that flagged
those would be noise, and a noisy gate gets ignored, which is the same end state as no gate.

Inline code spans are exempt for a sharper reason: a journal entry *about this bug* necessarily
names the signatures in prose. The span matcher uses a backreferenced tick run, so
`` ``bare `git rebase --force`/`--onto` auto-closes`` `` — a real sentence from the #183 session
stub — is recognised as one span. A naive `` `[^`]*` `` regex splits it and leaks the inner text,
producing a false positive on the very journal documenting the fix.

Indented lines are **not** exempt from the signature check. This corpus fences its code rather than
indenting it, and the motivating paste arrived indented.

### Advisory: it reports, and never edits

Exit 1 stops the compose and prints `file:line`, which check fired, and the full offending line —
but the script never modifies a file. Property (1) above is the whole reason: blind removal of the
machine-looking text destroys real content. The skill step instructs the operator to recover
overwritten prose from the still-present stubs, or — when the hit is intentional documentation — to
wrap it in a fence or code span, which both silences the check and is how it should have been
written.

## Consequences

- A compose that leaks terminal output now fails loudly at Step 8b instead of silently shipping and
  compounding across later passes.
- False positives are possible by construction (unbalanced backticks disable span detection, which
  is deliberately the over-reporting direction for an advisory gate). The remedy — fence it — is
  cheap and improves the document.
- The gate is skill-invoked, not a hook: it fires only during `/journal-compose`. It does not
  retroactively scan the historical corpus, and it does not run on hand-edited journal files.
- Validated against the real corpus rather than fixtures alone: **431 markdown files** across all
  ten `sessions/*/` project journals plus the top-level README produced **zero** findings other
  than the known corruption itself. The pre-fix `sessions/dev-env/README.md` exits 1 at lines
  8, 9, 11, 15; the post-fix version (engineering-journal PR #184) exits 0. That measured
  false-positive rate is what justifies making the gate blocking rather than a printed warning.
- Rollback is a revert: removing Step 8b restores the prior behaviour with no data migration, since
  the gate owns no state.

## Alternatives considered

**Extend Step 6.5 instead of adding a step.** Rejected on ordering: Step 6.5 runs before the READMEs
are written, so it structurally cannot see the file where the incident occurred. Moving Step 6.5
later would delay the heading assertion it already performs well.

**Auto-strip the detected block.** Rejected — this is the failure mode the incident demonstrates.
The corruption's surviving prose fragment was inseparable from the pasted text by shape alone; only
a reader comparing against the source stub can tell which characters are real.

**A generic "does this look like terminal output" heuristic** (e.g. flagging lines with `$ `
prompts, exit codes, or high punctuation density). Rejected as premature: the narrow signature list
catches the observed failure with a measured zero false positives across 431 files, and a broad
heuristic trades that for noise on a corpus whose whole subject matter is command-line work.

**A PostToolUse hook on Write/Edit instead of a skill step.** Rejected for blast radius: it would
fire on every markdown write in every repo, whereas the failure is specific to composed journal
output. The skill step also has access to `$WT` and the still-undeleted stubs, which a generic hook
does not.

## References

- dev-env [#894](https://github.com/brownm09/dev-env/issues/894) — this issue
- engineering-journal [#183](https://github.com/brownm09/engineering-journal/issues/183) /
  [PR #184](https://github.com/brownm09/engineering-journal/pull/184) — the corruption and its repair
- engineering-journal [#185](https://github.com/brownm09/engineering-journal/issues/185) — the
  second defect from the same compose pass
- dev-env [#467](https://github.com/brownm09/dev-env/issues/467) — the Step 6.5 heading assertion
  this does not duplicate
- [ADR-081](081-write-time-journal-shard-validation-hook.md) — the write-time/compose-time
  two-gate pattern this follows
- [ADR-114](114-slim-testing-section-index.md) — the Testing-index parity rule the new item 83 obeys
