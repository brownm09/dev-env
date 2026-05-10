# ADR 019 — Documentation Reconciliation Enforcement

**Date:** 2026-05-10
**Status:** Accepted

---

## Context

Documentation reconciliation — keeping README.md and docs/REFERENCE.md current when skills,
hooks, scripts, or routines change — has been purely instruction-based since the Documentation
Maintenance table was added to `dev-env/CLAUDE.md`. The rule is a prose paragraph in a long file;
nothing actually checks whether the docs were updated.

The gap surfaces in practice: a PR that renames a skill or adds a hook script currently passes
`pre-pr-create-check.py` (which only checks testing) and passes `/review` (which has no
documentation finding category) with no warnings. The stale documentation is only caught if
Claude happens to re-read the instruction during implementation — an unreliable trigger.

The same asymmetry identified for ADR-warrant enforcement (ADR-011) applies here: the cost
of a missing doc update is invisible at the time of the PR and accumulates as reader confusion,
broken README links, and outdated REFERENCE.md entries.

---

## Decision

Three-layer enforcement, ordered from cheapest (early warning) to most reliable (Opus review):

**Layer 1 — `/review` SKILL.md: blocking finding category for documentation gaps.**

Add a Step 2b (Documentation Reconciliation Check) that runs before analysis. Using the diff
already fetched in Step 2, the step checks whether any changed path matches
`claude/skills/**`, `claude/hooks/**`, `claude/scripts/**`, or `claude/routines/**`. If yes,
and neither `README.md` nor `docs/REFERENCE.md` appears in the diff, emit a **blocking**
finding. Update Step 6 to list "Documentation" as a fourth blocking category alongside
correctness, security, and reliability.

The check is gated on the repo having a Documentation Maintenance table in its CLAUDE.md —
repos without such a rule are skipped with a note. This keeps the check project-aware rather
than noisy.

**Layer 2 — `pre-pr-create-check.py`: advisory warning at PR creation.**

Extend the existing pre-PR checklist with a diff-based check (`git diff origin/main --name-only`).
If the branch touches `claude/skills/`, `claude/hooks/`, `claude/scripts/`, or `claude/routines/`
without also touching `README.md` or `docs/REFERENCE.md`, append a warning line to the checklist
output. Exits 0 (advisory), consistent with the hook's existing enforcement model.

This provides early feedback before the PR exists — if the author catches it here they avoid
needing to address a blocking `/review` finding later.

**Layer 3 — `claude/CLAUDE.md`: explicit three-checkpoint pattern.**

Replace the single "Reference doc maintenance" paragraph with a `**Doc-reconciliation checkpoint**`
block that mirrors the ADR-warrant check structure: evaluate at post-ExitPlanMode,
post-`gh pr create`, and pre-merge. This makes the rule a first-class workflow gate rather
than a paragraph in a subsection, and aligns its rhythm with the ADR-warrant check so both
fire at the same moments.

---

## Consequences

**Positive.**

- A PR renaming a skill without updating README.md now produces a blocking `/review` finding
  and an advisory warning at PR creation — two visible prompts before merge.
- The CLAUDE.md change ensures the rule is evaluated at the plan stage (before any code is
  written), catching the case where an author knows they'll need to update docs but forgets
  to plan for it.
- Implementation cost is low: the check is mechanical (file path pattern matching), so no
  Opus reasoning is consumed beyond what `/review` already uses.
- The advisory hook fires cheaply (one `git diff` subprocess) and is filtered to the
  `gh pr create` command, so it adds no latency to other operations.

**Negative.**

- The `/review` blocking finding will surface on any PR touching the listed paths — including
  internal refactors that don't change the public interface (e.g., renaming a helper function
  inside a skill). Mitigation: the check is scoped to files under `claude/skills/`,
  `claude/hooks/`, `claude/scripts/`, and `claude/routines/` — purely internal implementation
  changes that don't touch these top-level paths are unaffected.
- The hook check is mechanical (path-based); it cannot detect whether the README was updated
  *correctly*. The `/review` Opus step provides the semantic judgment; the hook provides
  the early mechanical signal.

---

## Alternatives Considered

**Single checkpoint only (pre-merge).** Catch missing doc updates only in the final ADR-warrant
pass. Rejected: by that point the PR is open and ready to merge; a doc gap becomes a blocking
review finding rather than an early warning. Two-stage detection (hook at creation + review
at merge) is cheaper for the author.

**Hard block in hook (exit 1).** Make `pre-pr-create-check.py` exit 1 to block `gh pr create`
when docs are not updated. Rejected: the hook's existing enforcement model is advisory (always
exits 0) per the hook invariants in `docs/REFERENCE.md`. Blocking should come from the review
skill, which has Opus reasoning and can correctly distinguish "paths touched but no public
interface change" from "skill renamed without README update."

**Separate doc-check script.** Add a new `doc-check.py` script rather than extending the
existing pre-PR hook. Rejected: the existing hook already has the right trigger (`gh pr create`),
already handles the advisory pattern, and the check is three lines of path inspection — not
enough complexity to warrant a new script.

---

## Verification

1. Create a test branch modifying a file under `claude/skills/` without touching `README.md`.
2. Attempt `gh pr create` — confirm hook emits the documentation warning.
3. Run `/review` on that PR — confirm a blocking "Documentation" finding appears.
4. Add the README.md change, re-run `/review` — confirm finding is absent.
5. Run `python3 -m py_compile claude/scripts/pre-pr-create-check.py` — no syntax errors.
6. Tracked in [dev-env#217](https://github.com/brownm09/dev-env/issues/217).
