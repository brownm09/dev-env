# Contributing to `<repo-name>`

> **Template usage:** Copy this file to `CONTRIBUTING.md` in the target repo. Replace
> bracketed placeholders and remove this notice block.

---

## Workflow

All changes go through a branch and PR — never commit directly to `main`.

**Branch naming:**

| Prefix | Use for |
|---|---|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `docs/` | Documentation only |
| `config/` | Configuration, tooling, CI |
| `chore/` | Housekeeping, dependency bumps |
| `draft/` | Work-in-progress not ready for review |

**Pull requests:**
- Open the PR as soon as you push the branch — draft PRs are fine for early feedback
- PR titles follow the same prefix convention: `[feat]`, `[fix]`, `[docs]`, `[config]`, `[chore]`
- Squash merge into `main`; delete the branch after merge

## Adding a feature

1. Open or find the relevant GitHub issue first
2. Create a branch: `git checkout -b feat/<short-description>`
3. For non-trivial changes, add a proposal under `docs/proposals/` using `/propose`
4. Implement, test, and commit
5. Open the PR and link the issue (`Closes #N` in the PR body)

## README and documentation hygiene

When your PR introduces or changes a named artifact — a skill, script, config file, template,
or API endpoint — update the relevant README table in the same PR. Do not leave READMEs
trailing by a commit.

**Specifically:**
- New skills → `README.md` Skills table
- New scripts → `README.md` Scripts table
- New templates → `README.md` Templates table
- New CLI flags or API endpoints → the section that documents them
- Changes to setup or onboarding → `README.md` Setup section

## Commit messages

```
[prefix] Short imperative summary (≤72 chars)

Optional longer explanation — why, not what.
```

Examples:
- `[feat] Add bodyweight exercise tracking`
- `[fix] Correct calorie calculation for AMRAP sets`
- `[docs] Propose: sleep-aware recommendation weighting`
- `[config] Add post-tool-use hook for token tracking`

## Claude Code sessions

This repo uses Claude Code for assisted development. Session conventions:

- All Claude-assisted changes still go through the normal branch/PR workflow
- Engineering journal entries for this project live in
  `brownm09/engineering-journal` → `sessions/<project>/`
- Use `/propose` to capture significant feature ideas before implementation
- Use `/journal-compose` at end of day to publish the session transcript
