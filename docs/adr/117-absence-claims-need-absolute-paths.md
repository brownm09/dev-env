# ADR-117: An Absence Claim Needs an Absolute Path — One Habit for Four False-Absent Mechanisms

**Date:** 2026-07-22
**Status:** Accepted
**Tags:** claude-behavior, cli-scripting, absence-claims, false-absent, cwd, git, msys, windows, claude-md, global-rule, correction, adr-066, adr-101, adr-107

---

## Context

On 2026-07-22, a lifting-logbook session (PR #870) ran `cd apps/web` in one Bash call to inspect a
workspace. That `cd` **persisted** into later, separate Bash calls. Several calls later, from what
the session believed was the repo root:

```bash
ls -l package-lock.json                              # -> No such file or directory
git ls-files | grep -E 'package-lock.json|yarn.lock' # -> (none tracked)
git check-ignore -v package-lock.json                # -> (not ignored)
```

Every one of those commands ran truthfully — and every one was scoped to `apps/web`. The session
concluded the repo tracked **no lockfile anywhere**, in a repo with a 928 KB tracked
`package-lock.json` and a CI step named "Verify package-lock.json is in sync". The only visible
tell was a `../../` path prefix in `git status --short` output — a signal most commands never
print at all. The user caught the error.

The failure is not that a command ran in the wrong place. It is that **a subtree-scoped miss
produces output byte-identical to a repo-wide miss**, so nothing in the result distinguishes
"absent here" from "absent everywhere." The session had no evidence it was wrong.

Two sibling mechanisms produce the identical harm through entirely different means, and were
already known:

1. **Visibility blind spots** — `Glob` and plain `git status` silently skip gitignored files;
   `git ls-tree` shows only committed content. Already documented as CLI Scripting Checklist
   item 5, framed narrowly around "declaring a directory empty or fully processed."
2. **Suppressed command failure** ([dev-env#602](https://github.com/brownm09/dev-env/issues/602)) —
   MSYS path-conversion intermittently mangles `git show <ref>:<path>` into
   `origin\main;.github\…`; git exits non-zero, a `2>/dev/null` swallows the `fatal:`, and the
   empty pipeline output reads exactly like "the pattern is not present." Hit in the *same*
   2026-07-22 session: a workflow was reported as having no WIF configuration when it has five
   such lines.

A fourth surfaced while this ADR was being written, on a different axis — and is the reason the
rule's headline names the ref as well as the path. The ADR was numbered 116 after `ls docs/adr/`
and `INDEX.md` both showed 115 as the highest. Both readings were true, and both were scoped to
`origin/main`; open PR [dev-env#863](https://github.com/brownm09/dev-env/pull/863) had already
claimed 116. An absolute path would not have helped — the miss was on the **ref** axis, and the
open-PR set is a scope no checkout answers for. `pre-merge-numbering-check.py` would have caught it
([ADR-074](074-pre-merge-numbering-collision-check.md) covers the ADR table via `_ADR_ROW_RE`), but
only at merge time and only once #863 landed first. It was caught here by reading another session's
journal stub — not by any directory listing.

Four mechanisms, one reasoning shortcut: **treating empty output as proof of absence.** Each was
being tracked as its own isolated gotcha, which is why hitting two of them in one session did not
trigger recognition of the pattern — each looked like a fresh, unrelated surprise. The fourth
arriving mid-authorship, in the very session writing the fix, is the strongest available evidence
that the class needs one habit rather than N reminders.

The existing guidance does not cover the cwd-scoping case, and in one respect points the other
way. The Git Workflow section warns that once a worktree tool has fired, cwd **does not reliably
persist** across Bash calls ([dev-env#627](https://github.com/brownm09/dev-env/issues/627)), and
tells you to re-prefix each command. This incident is the inverse: cwd *did* persist. Both
warnings are correct; together they mean persistence is simply **not predictable in either
direction**, so the only safe posture is to scope every command explicitly rather than reason
about which regime is in effect.

Nor does the existing mechanical drift detection catch it. `pre-bash-drift-check.py`
([ADR-101](101-bash-drift-check-every-call.md)) and the three ADR-085 checkpoint hooks all
delegate to `_bash_state.format_drift_warning`, whose comparison is
`(recorded_repo, recorded_branch) == (current_repo_root, current_branch)` — `cwd` appears in the
warning text but is never compared. A `cd` into a subdirectory of the same repo on the same branch
changes neither term, so no warning fires. That is correct behavior for those hooks (they exist to
catch repo/branch reverts, not navigation), but it means nothing mechanical stands between a
persisted `cd` and a false-absent conclusion.

## Decision

Rewrite CLI Scripting Checklist **item 5** in the global `claude/CLAUDE.md` from a
gitignored-files note into a single rule — **"An absence claim needs an absolute path — and the
right ref"** — with the four mechanisms as sub-bullets under it:

- **cwd scoping** (new; [dev-env#864](https://github.com/brownm09/dev-env/issues/864))
- **Ref scoping** (new; the ADR-116 collision above)
- **Visibility blind spots** (the prior item 5 text, preserved verbatim)
- **Suppressed failure** (new; [dev-env#602](https://github.com/brownm09/dev-env/issues/602))

The operative instruction is one habit: before concluding something is *not present*, re-run the
check rooted at the repo root — an absolute path, or `git -C <root>` — and let stderr through.
Where the claim is that an identifier is *free* rather than that a file is absent (an ADR number,
a `## Testing` item, a migration or fixture filename), the same habit extends to the open-PR set,
which no checkout answers for.

Two supporting edits:

- The checklist preamble becomes "Before writing a `gh` or other CLI automation script — **or
  acting on what one reports**". The prior preamble scoped the list to *authoring* scripts, but
  item 5 has always been about *interpreting* their output; the new item makes that mismatch
  conspicuous.
- The Git Workflow scoping-traps bullet gains a one-sentence pointer to item 5, so the
  relationship is discoverable from the cwd-persistence side too — a reader arriving there is
  thinking about commands running in the wrong repo, and would not otherwise learn that the same
  persisted `cd` also poisons absence checks.

## Consequences

- The four mechanisms are now one habit with one trigger ("I am about to say something is not
  present") rather than four gotchas each needing independent recall. A session that has never
  hit the MSYS mangling still inherits the `2>/dev/null` guard, and vice versa.
- The **Ref scoping** bullet gives `pre-merge-numbering-check.py` a behavioral counterpart. That
  gate is real but late — it fires at merge, and only once the competing PR has landed first — so
  a collision discovered there costs a rebase plus a rename across the ADR body, its filename, and
  the PR title. Checking `gh pr list` before picking a number costs one call.
- [dev-env#602](https://github.com/brownm09/dev-env/issues/602) is **partially** addressed, not
  closed: its `MSYS_NO_PATHCONV=1` workaround now appears in the checklist, but its separate ask —
  a note in the `/review` skill, whose "read from remote" step per
  [ADR-004](004-pr-review-reads-from-remote.md) is a likely recurrence site — remains open. The
  issue stays open with a comment linking this rule.
- Net context weight in the always-loaded global CLAUDE.md grows by roughly four lines. Folding
  into item 5 rather than appending an item 6 keeps that growth bounded, which matters because
  this file is loaded on every prompt in every project
  ([ADR-114](114-slim-testing-section-index.md)).
- No code, hook, or test changes — a pure `claude/CLAUDE.md` + ADR change. The `## Testing`
  docs-only guard (item 4) applies.

## Alternatives considered

- **Add a separate item 6 and leave item 5 alone.** Rejected. Three adjacent items describing
  three ways to reach the same wrong conclusion read as three unrelated gotchas — which is
  precisely the framing that let one session hit two of them without noticing they were the same
  mistake. The issue itself proposed folding, and the folded form is also cheaper in context.
- **A PreToolUse hook that warns when `git ls-files` / `find` / `git grep` runs with a relative
  path.** Rejected — those commands run with relative paths constantly and legitimately; the hook
  would fire on nearly every invocation, and habituation would neutralize it well before it caught
  a real case. The detectable event is not the command but the *conclusion drawn from its empty
  output*, which has no bounded surface form to key on (contrast
  [ADR-109](109-tile-gate-deferral-question-trigger.md)'s tile gate, which works precisely because
  deferral questions do have a small idiom set).
- **Extend `_bash_state` drift detection to subdirectory granularity** so a within-repo `cd` warns.
  Rejected — an intentional `cd` into a subdirectory is ordinary navigation, so this would warn on
  correct behavior far more often than incorrect, and it addresses only one of the three
  mechanisms (it does nothing for gitignore blind spots or MSYS mangling).
- **Fix the underlying cwd persistence.** Not available — persistence is harness behavior, and
  [dev-env#627](https://github.com/brownm09/dev-env/issues/627) documents it failing in the
  opposite direction. Guidance that survives both regimes is the only durable form.
- **Rely on memory alone.** Rejected per the *Durable Preferences & Memory* rule
  ([ADR-038](038-durable-preferences-documented-in-repo.md)) — a durable, cross-session reasoning
  correction belongs in the instructions, not a private per-session cache.

## References

- [dev-env#864](https://github.com/brownm09/dev-env/issues/864) — the incident and issue this ADR
  resolves.
- [dev-env#602](https://github.com/brownm09/dev-env/issues/602) — the MSYS `git show <ref>:<path>`
  mangling, the same false-absent harm through a different mechanism; partially addressed here.
- [dev-env#627](https://github.com/brownm09/dev-env/issues/627) — the inverse cwd behavior (does
  *not* persist after a worktree tool fires), documented in the Git Workflow section.
- [dev-env#863](https://github.com/brownm09/dev-env/pull/863) — the open PR whose ADR-116 claim
  produced this ADR's own ref-scoping instance, and which keeps 116.
- [ADR-074](074-pre-merge-numbering-collision-check.md) — the merge-time numbering-collision gate
  the *Ref scoping* bullet complements: correct but late, and blind until the competing PR lands.
- [ADR-107](107-toolsearch-is-not-a-tool-availability-check.md) — the closest precedent: a
  false-absent reasoning correction (a zero-result `ToolSearch` read as "tool unavailable") fixed
  as a global CLAUDE.md instruction.
- [ADR-101](101-bash-drift-check-every-call.md),
  [ADR-085](085-bash-repo-branch-drift-detection.md) — the existing cwd/branch drift hooks, whose
  `(repo_root, branch)` comparison structurally cannot see a within-repo `cd`.
- [ADR-066](066-worktree-session-safety-rules.md) — worktree session safety, including the Bash
  `cd` rules this rule sits alongside.
- [ADR-034](034-error-message-diligence.md) — the sibling discipline for the opposite error:
  reading an emitted message as a diagnosis without tracing what actually produced it.
- [ADR-114](114-slim-testing-section-index.md) — the standing concern about context weight in the
  always-loaded global CLAUDE.md, which motivated folding rather than appending.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable corrections live in
  instructions, not memory.
