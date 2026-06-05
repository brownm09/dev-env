# ADR-040 — Global CLAUDE.md Layering: Project-Specific Content Belongs in Project CLAUDE.md

**Date:** 2026-06-05
**Status:** Accepted
**Closes:** [dev-env#321](https://github.com/brownm09/dev-env/issues/321)

## Context

`claude/CLAUDE.md` is symlinked to `~/.claude/CLAUDE.md` and is loaded into **every** Claude Code
session in every project. Its own preamble states the layering rule: *"Project-specific CLAUDE.md
files extend these conventions — they do not repeat them."*

Over time the global file grew to 744 lines / 8.7k words / ~61.5 KB — roughly 50% over a reasonable
size budget for a file that is prepended to every session's context. A correctness audit found that
a meaningful fraction of the bloat was **dev-env-project-specific content living in the global
file**, which produced two concrete defects beyond the token cost:

1. **A dev-env-only `## Testing` section in the global file asserted the wrong "test before PR"
   command for every other project.** The commands (`py -3 … claude/scripts/*.py`, the pre-push
   self-test) are meaningful only in the dev-env repo. Loaded into lifting-logbook or any other
   repo, the global "Test before PR" rule pointed at a command that does not apply there.

2. **The duplicated Testing content had already drifted.** The global copy carried the pre-push
   self-test but not the `pyw -3` stdio test; the dev-env *project* `CLAUDE.md` carried the `pyw`
   test but not the pre-push self-test. Neither file listed the complete set, so a session following
   either one ran an incomplete verification — exactly the failure mode the "do not repeat" rule
   exists to prevent.

Two further dev-env-specific blocks (`## Dev-Env` architecture, ~37 lines; `## GitHub Project` IDs
and procedures, ~98 lines) were also global-but-project-specific. Separately, the `## Engineering
Journal` section (~257 lines) mixed behavioral rules with large blocks of pure mechanical reference
(file-format schemas, a stub template, the 11-section compose structure, a recovery runbook) that
do not need to be in the always-loaded global file.

## Decision

1. **Project-specific content moves to the project `CLAUDE.md`.** The dev-env `## Testing` commands,
   `## Dev-Env Architecture`, and the dev-env-specific `## GitHub Project` IDs/field-IDs/procedures
   now live in the repo-root `CLAUDE.md`. The global file retains only a short pointer and the genuinely
   cross-project content (the single-select option-mutation hazard, which the file explicitly scopes
   to "every project").

2. **The drifted Testing section is reconciled into one complete copy** in the project `CLAUDE.md`,
   containing all four dev-env checks (hook-script `ast.parse`, `pyw -3` stdio, pre-push self-test,
   docs-only `date -u` guard). The global "Test before PR" rule defers to each project's own
   `## Testing` section.

3. **Mechanical journal reference relocates to `docs/REFERENCE.md` → Engineering Journal Internals.**
   The `.manifest.jsonl` / `open-prs.jsonl` schemas, the stub template, the canonical 11-section
   structure, and the draft-branch recovery runbook move there (in-repo, so links resolve). The
   global file keeps every *behavioral* rule: composition guardrails, the per-session stub workflow
   steps, and the auto-stub update triggers, each pointing to REFERENCE for the formats.

Net: the global file drops from 744 to ~474 lines (−36%), landing below the size budget, with no
enforceable rule or command deleted — content was relocated, not removed.

## Consequences

- **The wrong-test-command defect is fixed:** the global file no longer asserts a dev-env-only test
  command for unrelated projects.
- **Single-source-of-truth for the dev-env test commands** eliminates the drift in defect #2; future
  edits touch one section.
- **General layering rule, restated and enforced going forward:** content that is true for one repo
  belongs in that repo's `CLAUDE.md`, not the global file. Reviewers should reject project-specific
  additions to `claude/CLAUDE.md`.
- **A small indirection cost:** sessions writing journal stubs now follow a pointer to
  `docs/REFERENCE.md` for file-format details. This is acceptable — the behavioral triggers that
  decide *whether* to act stay inline; only the mechanical schemas moved.

## Alternatives considered

- **Leave it and only trim prose.** Rejected: it would not fix the wrong-test-command defect or the
  Testing drift, which are correctness problems, not just size.
- **Aggressively compress the Code Quality grep policies too.** Rejected for this change: those
  sections are load-bearing (the grep patterns are executed verbatim before every PR), and the
  layering move plus journal relocation already met the size goal. Compressing them carried
  disproportionate risk for marginal additional savings.
