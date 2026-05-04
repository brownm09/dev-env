---
name: journal-onboard
description: Scaffold a new project's journal home (sessions/<project>/) in engineering-journal and optionally create a .claude/CLAUDE.md in the project repo.
argument-hint: "[project-slug]"
allowed-tools: Read Write Bash Glob
---

You are implementing the `/journal-onboard` workflow: create a journal home for a new project in the engineering-journal repo and optionally scaffold `.claude/CLAUDE.md` in the project repo.

---

## Step 0 — Determine project slug

If `$ARGUMENTS` is provided, use it as the slug.

Otherwise, infer from the current git repo:
```bash
git rev-parse --git-common-dir
```
Take the parent directory of the output and use its basename as the slug. For example,
`C:/Users/brown/Git/my-project/.git` → slug = `my-project`.

Confirm with the user:
> "Onboarding project `<slug>` — is this the right journal slug?"

Wait for confirmation before proceeding.

---

## Step 1 — Safety check

Check whether the journal home already exists:
```bash
ls ~/Git/engineering-journal/sessions/<slug>/
```

If the directory exists: report "Journal home for `<slug>` already exists at `sessions/<slug>/`. Nothing to do." and stop.

---

## Step 2 — Prepare engineering-journal

```bash
git -C ~/Git/engineering-journal checkout main
git -C ~/Git/engineering-journal pull
```

---

## Step 3 — Scaffold journal home

Create the directory and `README.md`:

```bash
mkdir -p ~/Git/engineering-journal/sessions/<slug>
```

Write `~/Git/engineering-journal/sessions/<slug>/README.md`:

```markdown
# <slug>

<!-- one-line description of the project -->

## Progress Summary

_No sessions yet._

## Where to Start Next Session

_First session — no prior context._

## Sessions

| Date | Session | Topics |
|---|---|---|
```

---

## Step 4 — Check project CLAUDE.md

Check whether `.claude/CLAUDE.md` exists in the current project repo:
```bash
cat .claude/CLAUDE.md 2>/dev/null
```

**If the file does not exist:**

Ask the user:
> "No `.claude/CLAUDE.md` found. Create one from the standard template? (yes/no)"

If yes: read `~/.claude/templates/project-claude.md`, replace `<REPO_NAME>` with the repo
basename and `<PROJECT_SLUG>` with the slug, then write it to `.claude/CLAUDE.md`. Remind
the user to commit it in the project repo.

If no: note that the journal path convention requires `.claude/CLAUDE.md` to contain a
`## Journal` section with the path `sessions/<slug>/`, and that the user should add it manually.

**If the file exists but contains no `sessions/` path reference:**

Ask the user:
> "`.claude/CLAUDE.md` exists but has no journal path. Append the `## Journal` section? (yes/no)"

If yes: append to the file:
```markdown

## Journal

Project journal path: `sessions/<slug>/`
```

Remind the user to commit the updated file.

**If the file exists and already references `sessions/<slug>/`:** no action needed.

---

## Step 5 — Commit and push engineering-journal

```bash
git -C ~/Git/engineering-journal add sessions/<slug>/
git -C ~/Git/engineering-journal commit -m "feat: add journal home for <slug>"
git -C ~/Git/engineering-journal push origin main
```

---

## Step 6 — Report completion

Tell the user:

- Journal home created at `sessions/<slug>/` and pushed to `engineering-journal` main.
- First stub for this project must include the `<!-- opening-brief -->` block. Set it to:
  `"First session — no prior context."`
- If `.claude/CLAUDE.md` was created or updated, remind the user to commit it in the project repo before the first session stub is written.
