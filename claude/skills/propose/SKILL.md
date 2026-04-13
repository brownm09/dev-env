---
name: propose
description: One-sentence idea → PRD in docs/proposals/ → linked GitHub issue → ROADMAP.md update. Run from the lifting-logbook repo root on a clean working tree.
argument-hint: "<one-line idea>"
allowed-tools: Read Edit Write Bash Glob Grep
---

You are implementing the `/propose` workflow: expand a one-line idea into a proposal document,
create the linked GitHub issue, and update the roadmap. Follow every step in order.

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

Skip any question whose answer is already obvious from the idea or from `docs/PRD.md`.
Skip questions the user has already answered in their opening message.

---

## Step 3 — Read supporting files

Read these two files before drafting:

1. The master proposal template:
   ```bash
   cat ~/.claude/templates/proposal.md
   ```

2. The project PRD (for persona and milestone context):
   ```bash
   cat docs/PRD.md
   ```

---

## Step 4 — Collect metadata

Ask the user to choose:

**Milestone** (pick one):
- `v0.1 — Foundation`
- `v0.2 — Core API`
- `v0.3 — Client Applications`

**Epic** (pick one):
- Monorepo Scaffolding
- Package & App Scaffolding
- Port Interfaces
- Shared Types
- CI/CD Foundation
- Architecture & Documentation

The epic option IDs (needed later for GitHub project assignment) are in `CLAUDE.md`.

---

## Step 5 — Draft the proposal

Using the template from Step 3 and the answers from Steps 1–4, compose the full proposal.

Produce:
- A **slug**: lowercase, hyphens, 3–5 words (e.g., `bodyweight-exercise-tracking`)
- A **filename**: `docs/proposals/YYYY-MM-DD-<slug>.md` (use today's date)
- The **full proposal document**, filled in from the template

Show the draft to the user. Ask: "Does this look right? Any edits before I write the file?"

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
mkdir -p docs/proposals
```

Write the confirmed proposal to `docs/proposals/YYYY-MM-DD-<slug>.md`.

---

## Step 8 — Create the GitHub issue

Issue title format: `[feat] <proposal title>` (use `[docs]` or `[chore]` if more appropriate).

Issue body: paste the **Problem**, **Proposed Solution**, and **Acceptance Criteria** sections
from the proposal, then append:

```
---
Proposal: [docs/proposals/YYYY-MM-DD-<slug>.md](docs/proposals/YYYY-MM-DD-<slug>.md)
```

Create the issue:
```bash
gh issue create \
  -R brownm09/lifting-logbook \
  --title "[feat] <title>" \
  --body "..."
```

Then run the full project/milestone/epic assignment workflow from `CLAUDE.md`:
1. Set milestone: `gh issue edit <N> --milestone "<milestone-title>"`
2. Add to project and capture item ID
3. Set Epic field with the option ID for the chosen epic

After the issue is created and assigned, update the `**Issue:**` line in the proposal file
with the issue number and URL.

---

## Step 9 — Update ROADMAP.md

Read `ROADMAP.md`. Find the section for the milestone chosen in Step 4.

Add a new row to that milestone's proposal table:

```
| [<Title>](docs/proposals/YYYY-MM-DD-<slug>.md) | <one-line description> | [#N](<issue-url>) |
```

If the milestone section has no proposal table yet, add one before adding the row:

```markdown
| Proposal | Description | Issue |
|---|---|---|
```

---

## Step 10 — Commit and push

```bash
git add docs/proposals/YYYY-MM-DD-<slug>.md ROADMAP.md
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
  -R brownm09/lifting-logbook \
  --title "[docs] Propose: <title>" \
  --body "..."
```

Return the PR URL to the user.
