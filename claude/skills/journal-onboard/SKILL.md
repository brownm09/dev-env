---
name: journal-onboard
description: Scaffold a new project's journal home (sessions/<project>/) in engineering-journal and optionally create a .claude/CLAUDE.md in the project repo.
argument-hint: "[project-slug]"
allowed-tools: Read Write Edit Bash Glob AskUserQuestion
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

Ask through `AskUserQuestion` (single-select):
> "Onboarding project `<slug>` — is this the right journal slug?"

Options, in this order: **Use `<slug>` (Recommended)**, **Stop**.

If the user picks **Use `<slug>`**, continue with Step 1. If the user types a different slug in the
question's free-text option, use that slug and continue with Step 1.

If the user picks **Stop** or declines the question, end the run here, whichever way the slug was
given. Step 2 and everything after it don't run, so nothing is created. Someone was there and
didn't confirm the name.

If there's no response or the context is non-interactive, including a session without
`AskUserQuestion`:

- **Inferred slug:** end the run here, the same way. A repo name isn't always the journal slug, and
  Step 5 pushes straight to `main`, so a wrong guess would create a journal home under the wrong
  name. Say in the final message that nothing was created, and that whoever can confirm the slug
  can run `/journal-onboard <slug>`. Don't rerun it yourself with the inferred slug, because that
  would only turn the guess into an argument.
- **Slug passed as an argument:** continue with Step 1. Whoever started the run named the slug, so
  it isn't a guess. Step 6 reports that it wasn't confirmed.

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
git -C ~/Git/engineering-journal fetch origin main
git -C ~/Git/engineering-journal checkout main
git -C ~/Git/engineering-journal merge --ff-only origin/main
```

Not `git pull`: it merges `.git/FETCH_HEAD`, which concurrent sessions overwrite in this shared
checkout ([dev-env#1166](https://github.com/brownm09/dev-env/issues/1166)).

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

Ask through `AskUserQuestion` (single-select):
> "`<repo basename>` has no `.claude/CLAUDE.md`. Create one from the standard template?"

Options, in this order: **Yes (Recommended)**, **No**.

If yes: read `~/.claude/templates/project-claude.md`, replace `<REPO_NAME>` with the repo
basename and `<PROJECT_SLUG>` with the slug, then write it to `.claude/CLAUDE.md`. Remind
the user to commit it in the project repo.

If no, or if no answer can be had (a declined answer, no response, or a non-interactive context,
including a session without `AskUserQuestion`): don't write the file. Note that the journal path
convention requires `.claude/CLAUDE.md` to contain a `## Journal` section with the path
`sessions/<slug>/`, and that the user should add it manually. Then continue with Step 5.

**If the file exists but contains no `sessions/` path reference:**

Ask through `AskUserQuestion` (single-select):
> "`.claude/CLAUDE.md` in `<repo basename>` has no journal path. Append the `## Journal` section for `sessions/<slug>/`?"

Options, in this order: **Yes (Recommended)**, **No**.

If yes: use the Edit tool to append to the file. Add the following block at the end:
```markdown

## Journal

Project journal path: `sessions/<slug>/`
```

Remind the user to commit the updated file.

If no, or if no answer can be had (the same cases as above): leave the file unchanged. Note that
the user should add the `## Journal` section with the path `sessions/<slug>/` manually. Then
continue with Step 5.

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
- If a Step 4 question got no answer, repeat the question and the manual-add note here, so a session that couldn't ask still puts them in its final message.
- If Step 0 continued without an answer, say that the slug `<slug>` came from the argument and wasn't confirmed.
