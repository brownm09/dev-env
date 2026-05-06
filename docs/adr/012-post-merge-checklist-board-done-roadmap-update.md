# ADR 012 — Post-Merge Checklist: Board Done + Roadmap Update Rules

**Date:** 2026-05-06
**Status:** Accepted
**Tags:** git, workflow, project-board, roadmap, post-merge

---

## Context

Two post-merge actions were expected but not documented in the global workflow:

1. **Board item to Done:** After merging a PR, the linked project board item should move to Done. Without a rule, items accumulated in "In Progress" indefinitely — the board stopped reflecting reality.

2. **Roadmap update:** When a work stream (milestone, issue group, or multi-session feature sequence) completes, the project's active roadmap or work-tracking document should be updated to move the completed item into a Shipped section. Without a rule, roadmaps silently diverged from actual shipping history.

**Motivating incident:** lifting-logbook v0.1 and v0.2 shipped without corresponding ROADMAP.md updates ([lifting-logbook#97](https://github.com/brownm09/lifting-logbook/issues/97)). The roadmap became misleading rather than informative.

The board-update command is project-specific (each project has its own project ID, field ID, and option ID), so a global rule cannot encode the command — only the obligation.

---

## Decision

Add two rules to the **Git Workflow** section of `claude/CLAUDE.md` (global), placed immediately after "PR closed without merging":

1. **After merging a PR:** move the linked project board item to Done. The exact command is project-specific — each project's CLAUDE.md provides it.

2. **When a work stream completes** (a milestone, closed issue group, or multi-session feature sequence): update the project's active roadmap or work-tracking document — move the completed item out of Active Work and into a Shipped section (or equivalent).

Implementation details (commands, field IDs, option IDs) remain in each project's CLAUDE.md. The global rule states the principle and the obligation only.

---

## Consequences

- Board items reach Done state after every merge, keeping the project view accurate.
- Roadmaps reflect the actual shipping history rather than aspirational states.
- The principle/implementation split keeps the global rule stable as project boards evolve — adding a new project does not require updating the global CLAUDE.md.
- Projects without a board (or without a roadmap) are unaffected — the rule has no implementation to invoke.
