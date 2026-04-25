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
- **Auto-review on PR creation.** After opening any PR, immediately run `/review <PR-URL>` before reporting the task as complete. Do not skip this step.
- **Exception:** Local-only repos with no remote may commit to main directly.
- **Branch creation in squash-merge repos:** Use `new-branch <name>` (source `~/.claude/scripts/new-branch.sh` in `.bashrc`) or `git checkout -b <name> origin/main` explicitly. Never cut from a branch that has been squash-merged — its commits no longer exist on main and a rebase will fail. Verify with `git merge-base HEAD origin/main` — output should equal `git rev-parse origin/main`.
- **Pre-push hook wiring (one-time setup):** Before setting, check for an existing value: `git config --system core.hooksPath` and `git config --global core.hooksPath`. If a system-level path exists (enterprise-managed hooks), migrate its hooks into `~/.claude/hooks/` rather than overriding. If another tool (Husky, Lefthook) owns the global value, coordinate rather than overwrite — two tools cannot share `core.hooksPath`. Once clear: `git config --global core.hooksPath ~/.claude/hooks`. The hook chains to any per-repo `.git/hooks/pre-push` so existing repo-level hooks are preserved.

---

## Dev-Env

`~/.claude/` is split between two categories. Treat them differently.

**Owned by `brownm09/dev-env` — symlinked, version-controlled:**

| Path | dev-env source |
|---|---|
| `~/.claude/CLAUDE.md` | `claude/CLAUDE.md` |
| `~/.claude/scripts/` | `claude/scripts/` (directory junction) |
| `~/.claude/skills/` | `claude/skills/` (directory junction) |
| `~/.claude/hooks/` | `claude/hooks/` (directory junction) |
| `~/.claude/scheduled-tasks/` | `claude/routines/` (directory junction to `~/.claude/scheduled-tasks/`) |
| `~/.claude/settings.json` | `claude/settings.json` |

**Machine-local only — never commit:**

`scratch/`, `projects/`, `sessions/`, `backups/`, `ide/`, `plans/`, `shell-snapshots/`

**Rule:** Any addition or modification to a dev-env-owned artifact — new hook script, new skill, settings change, CLAUDE.md edit — must be committed to `brownm09/dev-env` via branch and PR before the session ends. Do not leave global tooling as untracked files.

**Routines note:** `dev-env/claude/routines/` is a directory junction pointing at `~/.claude/scheduled-tasks/`, so the scheduler tool writes directly to the version-controlled path. After creating a new routine, commit it to dev-env under `claude/routines/`.

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

## Context & Token Efficiency

**Directory reads:** When reading from a directory with more than ~3 files, read `INDEX.md` or a top-level manifest first. Load individual files on demand, not by globbing the whole directory. Flag to the user before reading more than 5 files in a single pass.

**Session length:** A `UserPromptSubmit` hook warns at turn 50 (and every 25 turns thereafter). When warned, consider running `/clear` or `/compact` if the task scope has shifted — accumulated context inflates cost toward the end of long sessions. The default threshold can be overridden per-project: add `"turn_threshold": N` to `.claude/hook-config.json`.

**Mechanical operations:** If a task is fully scriptable with known inputs, write the script rather than running an interactive session. Candidate operations: stale PR remediation, branch cleanup, rebase-and-merge sequences. Use `~/.claude/scripts/merge-stale-pr.sh` for engineering-journal stale draft PRs.

**Plan-then-optimize before acting:** Any task involving an Agent spawn, a skill invocation, or reads across more than one file requires this protocol. State a numbered plan first, then do one explicit token-efficiency revision pass before taking any action. The revision pass must check:
- Sequential tool calls that can be parallelized
- `Agent` spawns: all independent subagents must go in a single message with `run_in_background: true` — no synchronous preflight agent that a parallel sibling will redo (root cause of dev-env#51)
- File reads that can be skipped by reading an index or manifest instead of globbing
- Data a downstream step will recompute anyway

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

### Composition rules

- **`/journal-compose` is a dedicated-session operation.** Never run it alongside other tasks.
  If composition is requested in a session that has already processed other work, respond:
  > "Journal composition must run in its own session. Open a new Claude Code session and invoke `/journal-compose` there."
  Then stop — do not compose.

- **Never compose proactively.** If a `draft/YYYY-MM-DD` branch is encountered during any
  work (e.g., while running `git branch`, checking git status, or reading branch output),
  emit a single line at the next natural pause:
  > "Incomplete journal detected — run `/journal-compose` in a dedicated session."
  Then continue with the user's actual request. Do not read stubs, do not compose, do not
  ask whether to compose.

---

### Stub file workflow

Each session writes an isolated stub file — no shared mutable draft. This eliminates write
contention when multiple sessions run in parallel. Slug is determined at day end.

**Branch:** `draft/YYYY-MM-DD` — created at the first session of the day, merged to main at day end.

**Stub filename:** `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`
where `HHMMSS` is the UTC start time of the session (`date -u +%H%M%S`).

**First session of the day:**
1. `git -C C:/Users/brown/Git/engineering-journal checkout main && git pull`
2. `git -C C:/Users/brown/Git/engineering-journal checkout -b draft/YYYY-MM-DD`
3. Create `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` (see stub structure below)
4. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
5. Append a manifest entry to `sessions/<project>/YYYY-MM-DD.manifest.jsonl` (see Manifest format below)
6. `git add`, `git commit -m "draft: YYYY-MM-DD session 1"`, `git push -u origin draft/YYYY-MM-DD`

**Subsequent sessions:**
1. `git -C C:/Users/brown/Git/engineering-journal pull origin draft/YYYY-MM-DD`
2. Find the most recent stub and read only its `<!-- next-session-context -->` paragraph:
   ```bash
   ls C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.stub.md | sort | tail -1
   ```
3. Create a new `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` with the current session block
4. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
5. Append a manifest entry to `sessions/<project>/YYYY-MM-DD.manifest.jsonl` (see Manifest format below)
6. `git add`, `git commit -m "draft: YYYY-MM-DD session N"`, `git push`

**Manifest format (`YYYY-MM-DD.manifest.jsonl`):**

One JSON line per session, appended after the token comment is known (end of session):
```bash
echo '{"stub":"YYYY-MM-DD_HHMMSS.stub.md","topic":"<H2 heading>","tokens":{"input":N,"output":N,"cost":N}}' \
  >> "C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD.manifest.jsonl"
```

The manifest lets `/journal-compose` see the session count, topics, and token data without
reading individual stubs. It is advisory: if the manifest is missing or has fewer entries
than the stub glob, stubs are authoritative. Never commit the manifest separately from its stubs
(include it in the same `git add` / commit step).

**End of day (last session):**
1. Run `/journal-compose` — it discovers all stubs via manifest (or glob fallback), merges them,
   produces the canonical 11-section document, and auto-merges the PR

---

### Stub structure

Each stub file contains exactly one session block:

```
<!-- stub: YYYY-MM-DD HHMMSS -->

<!-- opening-brief (first stub of the day only) -->
Opening brief: <paste the Next Session Context from the previous day's published journal verbatim;
               use "First session — no prior context." only if this is the project's very first entry>

<!-- session: <slug> -->
## <Topic>
...
<!-- tokens: input=12,450 output=3,200 cost≈$0.08 -->
<!-- next-session-context -->
<one paragraph — for the next session to read and open with>
```

The `<!-- opening-brief -->` block appears **only in the first stub of the day**.
Subsequent stubs begin directly at `<!-- session: <slug> -->`.

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
9. Next Session Context (the final `<!-- next-session-context -->` block from the stubs)
10. Reflection (gaps, risks, strategic questions — written last)
11. Further Reading (1–3 primary sources per session that explain the reasoning behind key
    decisions; intended for deliberate study between sessions — link + one sentence on why
    it matters)

---

### Update triggers

**Project journal** (`sessions/<project>/`):
- Add to the current session's stub when a PR is merged or a strategic decision is made
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
