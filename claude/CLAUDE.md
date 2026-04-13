<!-- SOURCE OF TRUTH: C:/Users/brown/Git/dev-env/claude/CLAUDE.md -->
<!-- ~/.claude/CLAUDE.md is a symlink to this file. Edit here, not at ~/.claude/. -->
<!-- To commit: cd C:/Users/brown/Git/dev-env && git add claude/CLAUDE.md && git commit -->

# Claude Code — Global Configuration

This file is loaded automatically in every session, across all projects.
Project-specific CLAUDE.md files extend these conventions — they do not repeat them.

---

## Platform & Environment

- **OS:** Windows 11, Git Bash terminal
- **Node:** 20.11.1 (managed by nvm for Windows; `.nvmrc` is set — run `nvm use` at session start if not already active)
- **Package manager:** npm (workspaces where applicable)
- **`jq` is NOT available.** Use `node -e` with a temp file for JSON parsing:
  ```bash
  TMPFILE="C:/Users/brown/.claude/scratch/tmp_$$.json"
  some-command --format json > "$TMPFILE"
  node -e "
    const d = JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));
    console.log('VAR=' + d.field);
  "
  rm -f "$TMPFILE"
  ```
- **Never use `/tmp/`** for temp files — Node.js on Windows cannot resolve Git Bash Unix paths.
- **Scratch directory:** `C:/Users/brown/.claude/scratch/` — all processing tmp files (`gh` output, JSON parsing intermediaries, etc.) go here regardless of which project is active. Never write tmp files into a project repo working directory.
- **`gh` CLI** is available and authenticated. The `project` scope must be added separately when needed: `gh auth refresh -s project`.
- **Prefer Git Bash** over PowerShell for scripting — PowerShell handles arrays and arithmetic differently and has caused failures in this environment.

---

## CLI Scripting Checklist

Before writing a `gh` or other CLI automation script:

1. Run `<command> --help` first to confirm flag names and syntax
2. Confirm which JSON tools are available (`jq` is NOT available — use `node -e`)
3. Write temp files to `C:/Users/brown/.claude/scratch/`, not `/tmp/` or a project repo directory
4. Check whether any additional `gh` auth scopes are needed

---

## Git Workflow

- **Never commit directly to `main`.** All changes go through a branch and PR, regardless of repo.
- **Branch naming:** `feat/`, `fix/`, `config/`, `chore/`, `draft/` prefixes — match the convention already in use in the repo.
- **PR first, then merge.** Open the PR immediately after pushing the branch; do not prompt the user to run `gh pr create` themselves.
- **Exception:** Local-only repos with no remote may commit to main directly.

---

## Dev-Env

`~/.claude/` is split between two categories. Treat them differently.

**Owned by `brownm09/dev-env` — symlinked, version-controlled:**

| Path | dev-env source |
|---|---|
| `~/.claude/CLAUDE.md` | `claude/CLAUDE.md` |
| `~/.claude/scripts/` | `claude/scripts/` (directory junction) |
| `~/.claude/skills/` | `claude/skills/` (directory junction) |
| `~/.claude/settings.json` | `claude/settings.json` |

**Machine-local only — never commit:**

`scratch/`, `projects/`, `sessions/`, `backups/`, `ide/`, `plans/`, `shell-snapshots/`

**Rule:** Any addition or modification to a dev-env-owned artifact — new hook script, new skill, settings change, CLAUDE.md edit — must be committed to `brownm09/dev-env` via branch and PR before the session ends. Do not leave global tooling as untracked files.

**Repo path:** `C:/Users/brown/Git/dev-env`

---

## Model Selection

Route tasks to the least powerful model that can handle them reliably:

| Task type | Model |
|-----------|-------|
| Mechanical: search, format, summarize, diff, rename | Haiku |
| Standard dev: code review, feature implementation, debugging | Sonnet |
| Complex: architectural decisions, novel problems, multi-file reasoning | Opus |

Default to Sonnet when uncertain. Never use Opus for tasks a Haiku prompt handles correctly on the first try.

---

## Documentation and Citations

When writing or updating any architectural documentation (ADRs, design docs, READMEs):

- **Cite primary sources, not summaries.** Three categories:
  - *Official documentation* — for technology and framework choices (NestJS, Next.js, Prisma, etc.)
  - *Specifications* — for protocol and standard choices (IETF RFCs, OASIS specs, GraphQL spec, OpenID Connect)
  - *Foundational writings* — for architectural patterns (Cockburn's Hexagonal Architecture, Uncle Bob's Clean Architecture, Fowler articles)
- **Regulatory references** (GDPR, HIPAA, SOC 2) must link to the primary regulatory source, not a summary or blog post.
- **When making any technical recommendation in a response** — a technology, Claude Code feature, workflow pattern, or architectural approach — include a primary source link in that same response. If no authoritative primary source exists, explicitly label the recommendation as based on observed behavior or heuristic.

---

## Engineering Journal

After each session (or at natural breakpoints for long sessions), create or update a session
transcript in `brownm09/engineering-journal`.

**Repo path:** `C:/Users/brown/Git/engineering-journal`

Each project's CLAUDE.md defines its **project journal path** (e.g., `sessions/lifting-logbook/`).
Use that path wherever `sessions/<project>/` appears below.

---

### Draft file workflow

One draft file per calendar day, living on a dedicated branch in the engineering-journal repo.
Slug is determined at day end when the overall theme is clear.

**Branch:** `draft/YYYY-MM-DD` — created at the first session of the day, merged to main at day end.

**First session of the day:**
1. `git -C C:/Users/brown/Git/engineering-journal checkout main && git pull`
2. `git -C C:/Users/brown/Git/engineering-journal checkout -b draft/YYYY-MM-DD`
3. Create `sessions/<project>/YYYY-MM-DD_draft.md` with the opening brief and first
   `<!-- session: <slug> -->` block
4. `git add`, `git commit -m "draft: YYYY-MM-DD session 1"`, `git push -u origin draft/YYYY-MM-DD`

**Subsequent sessions:**
1. `git -C C:/Users/brown/Git/engineering-journal pull origin draft/YYYY-MM-DD`
2. Get the file's line count (`wc -l`), then `Read` with offset to retrieve only the last
   `<!-- next-session-context -->` block — do not read the full draft
3. Append the new `<!-- session: <slug> -->` block and `<!-- next-session-context -->` paragraph
   using `Edit`
4. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block,
   drawn from the Claude Code CLI session summary
5. `git add`, `git commit -m "draft: YYYY-MM-DD session N"`, `git push`

**End of day (last session):**
1. Read the full draft once to compose the final 11-section document
2. Write as `sessions/<project>/YYYY-MM-DD-<slug>.md`
3. Delete the draft file
4. `git add`, `git commit -m "[docs] Add YYYY-MM-DD journal: <slug>"`
5. Open a PR from `draft/YYYY-MM-DD` into `main`, squash merge, delete branch

---

### Draft structure during the day

```
<!-- draft: YYYY-MM-DD -->
Opening brief: ...

<!-- session: <first-slug> -->
## <Topic>
...
<!-- tokens: input=12,450 output=3,200 cost≈$0.08 -->
<!-- next-session-context -->
<one paragraph — copy to open next session>

<!-- session: <second-slug> -->
## <Topic>
...
<!-- tokens: input=18,900 output=4,100 cost≈$0.12 -->
<!-- next-session-context -->
<one paragraph — copy to open next session>
```

---

### Canonical 11-section structure (composed once at day end)

1. Header block (Topic, Repo/Branch, Issues closed, PRs merged)
2. Table of Contents
3. Opening Brief (paste the Next Session Context from the previous day verbatim)
4. Key Decisions (bullet list with links to sections, issues, PRs, ADRs)
5. Dialogue sections (one H2 per task or topic, drawn from draft)
6. Open Items / Next Steps (checkbox list)
7. Token Usage (per-session breakdown tables: model, est. input tokens, est. output tokens,
   est. cost — drawn from `<!-- tokens: ... -->` comments in the draft; when comments are
   absent use retroactive estimates based on session scope, labeled as "retroactive estimate";
   close with a Combined totals table)
8. Token Optimization Suggestions (2–4 per-session observations grouped under a `### Session N`
   heading; close with a `### Cross-Session Patterns` subsection for generalizable findings
   that apply across multiple sessions)
9. Next Session Context (the final `<!-- next-session-context -->` block from the draft)
10. Reflection (gaps, risks, strategic questions — written last)
11. Further Reading (1–3 primary sources per session that explain the reasoning behind key
    decisions; intended for deliberate study between sessions — link + one sentence on why
    it matters)

---

### Update triggers

**Project journal** (`sessions/<project>/`):
- Append to draft when a PR is merged or a strategic decision is made
- Compose and publish the daily document at end of last session of the day

**Meta journal** (`sessions/meta/`):
- When a `CLAUDE.md` is modified — record what changed, why, and which session prompted it
- When a new platform constraint is discovered — record the symptom, root cause, and fix pattern
- When a workflow failure mode is discovered and remediated — record the symptom, root cause,
  and fix pattern
- When a cross-project convention is established — record the convention and which projects it
  affects
- When the journal structure itself changes — record the new section, placement, and rationale
- When a new canonical reference repo or external resource is identified — record the resource
  and its role
- When a `brownm09/dev-env` PR is merged — record what changed (script, skill, settings, or
  CLAUDE.md), why it was introduced, and which project or session prompted it

**Full journal conventions:** See [`brownm09/engineering-journal`](https://github.com/brownm09/engineering-journal) → `sessions/meta/2026-04-05-workflow-and-journal-setup.md`
