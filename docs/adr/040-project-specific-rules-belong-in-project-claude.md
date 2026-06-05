# ADR-040 — Project-Specific Rules Belong in the Project `CLAUDE.md`

**Date:** 2026-06-06
**Status:** Accepted
**Refs:** [dev-env#321](https://github.com/brownm09/dev-env/issues/321)
**Tags:** claude, layering, documentation, global-rule, project-rule, dev-env
**Related:** [ADR-003](003-config-in-version-control.md), [ADR-019](019-doc-reconciliation-enforcement.md), [ADR-038](038-durable-preferences-documented-in-repo.md)

---

## Context

`claude/CLAUDE.md` is the global Claude Code configuration. It is symlinked to
`~/.claude/CLAUDE.md` and loaded in every session across every repository. That makes it the
right home for cross-project workflow rules such as branch discipline, test-before-PR, and
review gating.

Over time, dev-env-specific material accumulated in the global file:

- verification commands that only make sense in `brownm09/dev-env`
- repo-local path conventions for the dev-env checkout
- GitHub Project IDs and option IDs for the Dev Env board

This created two problems called out in [dev-env#321](https://github.com/brownm09/dev-env/issues/321):

1. **Wrong instructions leaked into unrelated repos.** A global file that tells every session to
   run dev-env-only verification commands is asserting the wrong test path in repositories that
   have nothing to do with dev-env.
2. **The same rule drifted in two places.** The dev-env project `CLAUDE.md` and the global file
   both carried overlapping verification instructions, which then diverged.

The repository already has the right structural model: the global file defines shared workflow
rules, while each repo's root `CLAUDE.md` extends those rules with local instructions. The
content had simply drifted away from that model.

---

## Decision

Adopt and document a layering rule:

1. `claude/CLAUDE.md` contains only **cross-project** rules.
2. A repository's root `CLAUDE.md` contains that repository's **local extension layer**:
   verification commands, repo-owned path conventions, board automation details, and other
   rules whose scope is that repository alone.
3. When a rule is discovered to be repo-local after landing in the global file, move it into
   the project's root `CLAUDE.md` rather than leaving duplicates in both places.

As the concrete application of this ADR for dev-env:

- move dev-env-specific `## Testing`, `## Dev-Env`, and `## GitHub Project` content out of
  `claude/CLAUDE.md`
- keep the global file focused on the shared workflow rules that every repo should inherit
- add a short pointer in the global file directing dev-env readers to `../CLAUDE.md`

---

## Rationale

**Why not keep the details in both places.** Duplication is what caused the drift. If one copy is
authoritative and the other is "helpful redundancy," the second copy still becomes stale and
starts giving contradictory instructions.

**Why the project root `CLAUDE.md` instead of README.** These are session-execution rules, not
general repository overview material. They need to sit in the file Claude loads as project-local
instructions, right next to the project's Documentation Maintenance table and other repo-specific
author guidance.

**Why leave a pointer in the global file.** The global file is still the first file many sessions
see. A short pointer preserves discoverability without reintroducing the duplicated content.

**Why this is broader than dev-env.** The problem surfaced in dev-env, but the rule is general:
any repo-specific command or board ID placed in the global file will leak into unrelated repos.
The ADR captures that layering principle once so future edits have a citation.

---

## Consequences

**Positive:**

- Cross-repo sessions stop receiving dev-env-only verification and board instructions.
- Dev-env-specific operational details now live next to the repo they govern.
- One authoritative copy reduces future drift.

**Negative:**

- Readers may need one extra click from the global file to the project file.
- Maintainers now need to decide explicitly whether a new rule is cross-project or repo-local
  before choosing where to document it.

---

## Alternatives considered

- **Keep the current duplication.** Rejected because it already drifted.
- **Move repo-local rules into README instead of `CLAUDE.md`.** Rejected because the instructions
  are for agent/session behavior, not for general human-oriented repo overview.
- **Split the global file into multiple includes.** Rejected as unnecessary complexity for a
  problem that is solved by obeying the existing global-vs-project layering model.

---

## References

- [dev-env#321](https://github.com/brownm09/dev-env/issues/321)
- [ADR-003](003-config-in-version-control.md)
- [ADR-019](019-doc-reconciliation-enforcement.md)
- [ADR-038](038-durable-preferences-documented-in-repo.md)
