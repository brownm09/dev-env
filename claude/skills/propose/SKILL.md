---
name: propose
description: One-sentence idea → PRD → linked GitHub issue → ROADMAP entry. Reads per-project config from .claude/propose.json; scaffolds config interactively for unconfigured repos.
argument-hint: "<one-line idea>"
allowed-tools: Read Edit Write Bash Glob Grep Agent
---

You are implementing the `/propose` workflow: expand a one-line idea into a proposal document,
create the linked GitHub issue, and update the roadmap. Follow every step in order.

---

## Step 0 — Detect project context

Run:
```bash
cat .claude/propose.json 2>/dev/null
```

### If the file exists

Read it. All subsequent steps use these keys from the config object (referred to below as
`config`):

| Key | Type | Used in |
|---|---|---|
| `proposals_dir` | string | Steps 7, 8, 9, 10 |
| `roadmap_file` | string \| null | Steps 4, 9 |
| `prd_file` | string \| null | Step 3 |
| `github_repo` | string | Steps 8, 11 |
| `milestones` | string[] | Step 4 |
| `epics` | {name, id}[] | Step 4 |
| `github_project` | object \| null | Step 8 |

Proceed to Step 1.

### If the file does not exist

Present the user with three options:

> **No `/propose` config found for this repo.** Choose how to proceed:
>
> **A — Scaffold full infrastructure** (proposals directory, ROADMAP.md stub, full config)
> **B — Minimal setup** (proposals directory + GitHub issues only; no roadmap, no project assignment)
> **C — Exit** — I'll configure `.claude/propose.json` manually and run `/propose` again.

**Option A — Scaffold full infrastructure:**

1. Ask:
   - "What is the GitHub repo? (format: owner/repo)"
   - "Do you have a GitHub Project set up for this repo? If yes, what is its number?"
2. Create `docs/proposals/` directory and `docs/proposals/README.md`:
   ```
   # Proposals

   Feature proposals for this project. Each file is a PRD-lite document linked to a GitHub issue.
   Files follow the naming convention `YYYY-MM-DD-<slug>.md`.
   ```
3. Create a `ROADMAP.md` stub if none exists:
   ```markdown
   # Roadmap

   _This file is the editorial view of planned and in-progress work. It is not auto-synced from GitHub._

   ## Proposals

   | Proposal | Description | Issue |
   |---|---|---|
   ```
4. Read `~/.claude/templates/propose-config.json` and populate `github_repo` from the answer
   above. If the user provided a project number, set `github_project.number` and
   `github_project.owner` (the owner segment of `github_repo`); otherwise set
   `github_project` to `null`.
5. Write the populated object to `.claude/propose.json`.
6. Tell the user:
   > "Scaffold complete. Commit `.claude/propose.json` (and `ROADMAP.md` if new) before your
   > next `/propose` run so the config is version-controlled."
7. Use the newly written config for the rest of this session. Proceed to Step 1.

**Option B — Minimal setup:**

1. Ask:
   - "Where should proposals live? (default: `docs/proposals/`)"
   - "What is the GitHub repo? (format: owner/repo)"
2. Write `.claude/propose.json`:
   ```json
   {
     "proposals_dir": "<answer or docs/proposals>",
     "roadmap_file": null,
     "prd_file": null,
     "github_repo": "<answer>",
     "milestones": [],
     "epics": [],
     "github_project": null
   }
   ```
3. Tell the user:
   > "Minimal config written. Steps that require a roadmap or GitHub Project will be skipped.
   > Commit `.claude/propose.json` before your next `/propose` run."
4. Proceed to Step 1.

**Option C:** Stop. Return the message above and exit.

---

## Step 1 — Capture the idea

If `$ARGUMENTS` is provided, use it as the idea.
If not, ask: "What's the idea? (one sentence is enough)"

---

## Step 2 — Ask clarifying questions

Ask at most three targeted questions to fill in the PRD sections that cannot be inferred
from the one-liner. Typical questions:

- What problem does this solve, and for which user? (if not clear from the idea)
- What would "done" look like? (anchor for acceptance criteria)
- Is anything explicitly out of scope?

Skip any question whose answer is already obvious from the idea.
If `config.prd_file` is non-null, skip questions answerable from that file (read it in Step 3).
Skip questions the user has already answered in their opening message.

---

## Step 3 — Read supporting files

Read these files before drafting:

1. The master proposal template:
   ```bash
   cat ~/.claude/templates/proposal.md
   ```

2. The project PRD (for persona and milestone context) — **only if** `config.prd_file` is non-null:
   ```bash
   cat <config.prd_file>
   ```

---

## Step 4 — Collect metadata

**Only if** `config.milestones` is non-empty, ask the user to choose a milestone from the list.

**Only if** `config.epics` is non-empty, ask the user to choose an epic from the list.

If both lists are empty (minimal config), skip this step entirely.

---

## Step 5 — Draft the proposal (Opus subagent)

Spawn an Agent with `model: "opus"`. Pass all of the following inline — do not instruct
the subagent to read any files:

- The proposal template (from Step 3, verbatim)
- The project PRD (from Step 3, if loaded — otherwise omit)
- The idea (from Step 1)
- Answers to clarifying questions (from Step 2)
- Chosen milestone and epic (from Step 4, if applicable)
- Today's date

Subagent task (substitute `<proposals_dir>` with `config.proposals_dir` before passing):

> Using the template and context provided, produce a complete proposal. Return:
> 1. A **slug**: lowercase, hyphens, 3–5 words (e.g., `bodyweight-exercise-tracking`)
> 2. A **filename**: `<proposals_dir>/YYYY-MM-DD-<slug>.md` (use today's date)
> 3. The **full proposal document**, filled in from the template
>
> Return only the slug, filename, and proposal document — no preamble or commentary.

Collect the subagent's output. Show the draft to the user. Ask: "Does this look right? Any edits before I write the file?"

Wait for confirmation. Apply any requested edits, then confirm once more before proceeding.

---

## Step 6 — Check the working tree

```bash
git status --porcelain
```

If the output is non-empty, stop and tell the user:
"Working tree is not clean. Please commit or stash your changes before running /propose."

If clean, create a branch:
```bash
git checkout main
git pull
git checkout -b docs/propose-<slug>
```

---

## Step 7 — Write the proposal file

```bash
mkdir -p <config.proposals_dir>
```

Write the confirmed proposal to `<config.proposals_dir>/YYYY-MM-DD-<slug>.md`.

---

## Step 8 — Create the GitHub issue

Issue title format: `[feat] <proposal title>` (use `[docs]` or `[chore]` if more appropriate).

Issue body: paste the **Problem**, **Proposed Solution**, and **Acceptance Criteria** sections
from the proposal, then append:

```
---
Proposal: [<config.proposals_dir>/YYYY-MM-DD-<slug>.md](<config.proposals_dir>/YYYY-MM-DD-<slug>.md)
```

Create the issue:
```bash
gh issue create \
  -R <config.github_repo> \
  --title "[feat] <title>" \
  --body "..."
```

**Only if** a milestone was chosen in Step 4:
```bash
gh issue edit <N> --milestone "<milestone-title>" -R <config.github_repo>
```

**Only if** `config.github_project` is non-null **and** `config.github_project.node_id` is non-empty:

Run the project/epic assignment workflow from the project CLAUDE.md (it contains the exact
field IDs and option IDs for this repo's project):
1. Add issue to project and capture item ID
2. Set the Epic field using the option ID for the chosen epic

If `config.github_project` is non-null but `node_id` or `epic_field_id` is empty, skip
project assignment and tell the user: "GitHub Project field IDs are not yet configured in
`.claude/propose.json` — fill in `node_id` and `epic_field_id`, then assign the project
manually."

After the issue is created, update the `**Issue:**` line in the proposal file with the issue
number and URL.

---

## Step 9 — Update ROADMAP

**Skip this step entirely if** `config.roadmap_file` is null.

Read `<config.roadmap_file>`.

- If a milestone was chosen in Step 4, find that milestone's section and insert there.
- If `config.milestones` is empty, find the first existing proposals table in the file and
  insert there. If no proposals table exists anywhere in the file, append one at the end.

Add a new row:

```
| [<Title>](<config.proposals_dir>/YYYY-MM-DD-<slug>.md) | <one-line description> | [#N](<issue-url>) |
```

If no proposals table exists yet in the target section, add one before the row:

```markdown
| Proposal | Description | Issue |
|---|---|---|
```

---

## Step 10 — Commit and push

Stage the proposal file and, if updated, the roadmap:

```bash
git add <config.proposals_dir>/YYYY-MM-DD-<slug>.md
```

If `config.roadmap_file` is non-null:
```bash
git add <config.roadmap_file>
```

```bash
git commit -m "$(cat <<'EOF'
[docs] Propose: <title>

Adds proposal doc and links GitHub issue #N.

EOF
)"
git push -u origin docs/propose-<slug>
```

Do not use `Closes #N` — the linked issue tracks the feature work, not this proposal PR.

---

## Step 11 — Open the PR

Read `~/.claude/templates/pr-body.md` and use it as the structural guide for the PR body.
This is a docs PR — use the "Docs / proposal PR" pattern.

```bash
gh pr create \
  -R <config.github_repo> \
  --title "[docs] Propose: <title>" \
  --body "..."
```

Return the PR URL to the user.
