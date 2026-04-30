<!-- SOURCE OF TRUTH: C:/Users/brown/Git/dev-env/claude/CLAUDE.md -->
<!-- ~/.claude/CLAUDE.md is a symlink to this file. Edit here, not at ~/.claude/. -->
<!-- To commit: cd C:/Users/brown/Git/dev-env && git add claude/CLAUDE.md && git commit -->

# Claude Code — Global Configuration

This file is loaded automatically in every session, across all projects.
Project-specific CLAUDE.md files extend these conventions — they do not repeat them.

> **ADRs:** The design decisions behind the rules in this file are recorded in [`docs/adr/`](../docs/adr/INDEX.md) in the dev-env repo. Consult the relevant ADR before overriding any rule, hook, skill, or config.

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

## Per-Project CLAUDE.md Requirements

Every project CLAUDE.md **must** include a `## Testing` section specifying the command(s) used to verify a solution before opening a PR. Examples:

```markdown
## Testing
Run `npm test` to execute the full test suite.
Run `npm run build` first if touching compiled output.
```

```markdown
## Testing
Run `pytest` from the repo root. Integration tests require `DATABASE_URL` set.
```

If the project has no automated tests, the section must say so explicitly and describe the manual verification steps instead. The `## Testing` section is used by the global "Test before PR" rule — **if no `## Testing` section exists in the project CLAUDE.md, stop before running `gh pr create` and ask the user to add one. Do not open the PR until the section is present.**

---

## Git Workflow

- **Create an issue before changing files.** When a user's question or request will result in file changes, create a GitHub issue first with `gh issue create` — describe the problem or goal, not the implementation. Do this before writing any code or editing any files. For a single-line change, ask the user whether an issue is warranted before creating one; anything longer than one line warrants an issue without prompting. Exception: engineering-journal draft branches (`draft/YYYY-MM-DD`) may omit an issue. Every PR must then reference the issue via a `Closes #N` line in the PR body.
- **Test before PR.** Before running `gh pr create`, execute the project's test command defined in `## Testing` in the project CLAUDE.md. Tests must pass (or the failure must be explained and documented). Include what was tested and the outcome in the PR body. **If no `## Testing` section exists in the project CLAUDE.md, stop and ask the user to add one — do not open the PR until it is present.**
- **Never commit directly to `main`.** All changes go through a branch and PR, regardless of repo.
- **Branch naming:** `feat/`, `fix/`, `config/`, `chore/`, `draft/` prefixes — match the convention already in use in the repo.
- **PR first, then merge.** Open the PR immediately after pushing the branch; do not prompt the user to run `gh pr create` themselves.
- **Write the journal stub immediately after `gh pr create`.** Do not defer until merge — if `/compact` fails or the session is corrupted, all context is permanently lost. Write the stub, then report the PR URL and prompt the user to run `/compact`. Once compaction is complete, immediately run `/review <PR-URL> --post-comment`. Address any blocking findings and merge in the same session. Rationale: `/compact` reduces implementation context to a small summary before review fires, preserving most token savings without a session break. The review skill applies the `reviewed-by-claude` label; a PR without that label has not been reviewed.
  - **Multiple PRs in one session:** if the plan opens more than one PR, defer stub writing until after the last `gh pr create` completes. Produce one stub covering all `prs_opened` entries and `open-prs.jsonl` additions together. Do not write an intermediate stub between PR opens.
- **Write a stub on PR merge.** A merge is a session boundary that requires stub coverage.
  - **Multiple merges in one session:** if the plan includes more than one PR merge, defer all stub work until after the last merge completes. Produce one consolidated stub covering every `prs_closed` entry and `open-prs.jsonl` removal together. Do not write an intermediate stub or stop between merges.
  - **Same session as PR open:** the existing stub covers the full lifecycle. Before the final commit below, update the stub's session block with any of the following that arose after the PR was opened: decisions made, discoveries surfaced, dependencies introduced, or deviations from documented process encountered. Then append a second manifest line with `prs_closed: [N]`, remove the PR from `open-prs.jsonl`, commit, and stop.
  - **New session (merge happens later):** choose the cheaper option:
    - **Update the opening stub in place** if the merge session adds only minor content (e.g., the merge itself with no follow-up work) — avoids the token cost `/journal-compose` pays to read and merge two stubs.
    - **Write a new stub** if the merge session involves substantial new content (review responses, follow-up fixes, etc.) — the PR grouping heuristic will combine them under one H2.
  - Either way: set `prs_closed: [N]` in the relevant manifest entry, remove the PR from `open-prs.jsonl`, and stop.
- **PR closed without merging.** If a PR is closed without merging, the stub was already written at PR creation. Stopping is optional — the session may continue if follow-up work remains.
- **Exception:** Local-only repos with no remote may commit to main directly.
- **Branch creation in squash-merge repos:** Use `new-branch <name>` (source `~/.claude/scripts/new-branch.sh` in `.bashrc`) or `git checkout -b <name> origin/main` explicitly. Never cut from a branch that has been squash-merged — its commits no longer exist on main and a rebase will fail. Verify with `git merge-base HEAD origin/main` — output should equal `git rev-parse origin/main`.
- **Merging a PR developed in a worktree:** Call `ExitWorktree` with `action: "remove"` before running `gh pr merge`. `action: "remove"` deletes both the worktree directory and the local branch, so `gh pr merge --squash --delete-branch` only has to delete the remote branch (steps 1–2 of `gh`'s sequence) — the local step is already done. `action: "keep"` does NOT help: it only changes the working directory; the worktree remains registered and `git branch -d` still fails. If you see the "cannot delete branch checked out at …" error, the merge and remote branch delete already completed — only the local branch remains and will be collected by the weekly `prune-merged-worktrees.py` run.
- **Verify branch before making changes and before every commit.** Run `git branch --show-current` (1) before making any edits in a session and (2) immediately before each `git commit`. Do not assume the branch is correct because it was correct earlier — worktrees, `git checkout`, and multi-repo work can silently shift context. A `UserPromptSubmit` hook already emits the active worktree list on every prompt when multiple worktrees are open. If the branch is wrong: if no edits are on disk yet, switch branches immediately; if edits are already on disk but not committed, run `git stash`, switch to the correct branch, then `git stash pop` before proceeding.
- **Pre-push hook wiring (one-time setup):** Before setting, check for an existing value: `git config --system core.hooksPath` and `git config --global core.hooksPath`. If a system-level path exists (enterprise-managed hooks), migrate its hooks into `~/.claude/hooks/` rather than overriding. If another tool (Husky, Lefthook) owns the global value, coordinate rather than overwrite — two tools cannot share `core.hooksPath`. Once clear: `git config --global core.hooksPath ~/.claude/hooks`. The hook chains to any per-repo `.git/hooks/pre-push` so existing repo-level hooks are preserved.
- **PR branch state must come from the remote, not the local worktree.** Whenever checking whether a PR has addressed review findings or is ready to merge: (1) run `git fetch origin <headRefName>` first, (2) read files via `git show origin/<headRefName>:<path>`, never from the local working tree. The local worktree may be behind or on a different branch entirely — reading it produces false "still outstanding" results. (Incident: lifting-logbook#86 commit `50f7ac6` fixed all blockers but a follow-up check read the wrong branch and reported three still open.)

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

**Reference doc maintenance.** When a change adds, removes, or renames a hook script, skill, routine, or utility script, check whether the project README and any reference documentation need updating in the same PR. This includes command renames, new options, and behavior changes. Each project's CLAUDE.md specifies which reference files apply.

**Repo path:** `C:/Users/brown/Git/dev-env`

---

## GitHub Project

All new dev-env issues must be added to the **Dev Env** project and given an Impact rating and Why description before work begins.

**Project IDs:**
- Project number: `3`, owner: `brownm09`
- Project node ID: `PVT_kwHOAjEKvM4BWKFe`

**Field IDs:**

| Field | ID | Options |
|---|---|---|
| Status | `PVTSSF_lAHOAjEKvM4BWKFezhRgkMY` | Todo=`f75ad846`, In Progress=`47fc9ee4`, Done=`98236657` |
| Impact | `PVTSSF_lAHOAjEKvM4BWKFezhRgkNc` | High=`08de2558`, Medium=`6320e8a6`, Low=`d8a85c2f` |
| Why | `PVTF_lAHOAjEKvM4BWKFezhRgkN0` | (text) |

**Impact guidelines:**

| Level | Meaning |
|---|---|
| High | Causes manual recovery work or token waste on every occurrence |
| Medium | Recurs periodically or silently degrades correctness over time |
| Low | Nice-to-have; low frequency or easily worked around |

**Workflow — run after `gh issue create`:**

```bash
# Requires project scope — add once if needed: gh auth refresh -s project

# 1. Add issue to project, capture item ID
TMPFILE="C:/Users/brown/.claude/scratch/tmp_item_$$.json"
gh project item-add 3 --owner brownm09 --url <issue-url> --format json > "$TMPFILE"
ITEM_ID=$(node -e "const d=JSON.parse(require('fs').readFileSync('$TMPFILE','utf8')); console.log(d.id);")
rm -f "$TMPFILE"

# 2. Set Impact
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkNc \
  --single-select-option-id <option-id>   # 08de2558=High  6320e8a6=Medium  d8a85c2f=Low

# 3. Set Why (one sentence — the cost of not fixing it)
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTF_lAHOAjEKvM4BWKFezhRgkN0 \
  --text "<why this matters>"
```

To look up an item ID (e.g., when moving to In Progress or Done in a new session):

```bash
TMPFILE="C:/Users/brown/.claude/scratch/tmp_item_$$.json"
gh project item-list 3 --owner brownm09 --format json > "$TMPFILE"
ITEM_ID=$(node -e "
  const d=JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));
  const item=d.items.find(i=>i.content&&i.content.number===<N>);
  console.log(item.id);
")
rm -f "$TMPFILE"
```

**Move to In Progress when work begins:**

```bash
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkMY \
  --single-select-option-id 47fc9ee4
```

**Move to Done after PR merges:**

```bash
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkMY \
  --single-select-option-id 98236657
```

---

## Testing

Run `python3 -m py_compile claude/scripts/*.py` from the repo root to verify all hook scripts are free of syntax errors.

For docs-only changes to `claude/CLAUDE.md`: run `grep -n 'date -u' claude/CLAUDE.md` and confirm every match is in an internal operational artifact context (lock files, log timestamps) — not in stub filename or branch name descriptions.

---

## Hook Safety

PreToolUse hooks that exit non-zero **block the matched tool call silently** — the user sees the
tool refused with no error pointing to the hook. This has caused complete Bash outages more than
once. Three invariants prevent recurrence:

1. **Atomic commits.** A `settings.json` hook entry and its script file must land in the **same
   commit**. Never push a settings.json change that references a script not yet in
   `claude/scripts/` on main. Verify by running the script **from the dev-env repo root**
   (not via `~/.claude/scripts/` — that junction resolves against the main worktree checkout,
   not the branch being tested):
   ```bash
   python3 claude/scripts/<new-hook>.py < /dev/null; echo "exit: $?"
   # Must print "exit: 0"
   ```

2. **Safe-exit guard.** Advisory hooks (hooks that emit a systemMessage reminder but do not
   intend to block) must exit 0 on **every** code path — happy path, empty stdin, malformed
   JSON, and unhandled exception. Use a top-level exception handler so no code path escapes:
   ```python
   if __name__ == "__main__":
       try:
           main()
       except Exception:
           sys.exit(0)
   ```
   Never add `sys.exit(N)` where N > 0 to an advisory hook.

3. **No `bash -c` wrappers.** Hook commands call the interpreter directly:
   `python3 C:/Users/brown/.claude/scripts/foo.py` — never `bash -c 'python3 ...'`. The
   wrapper adds an exit-code-propagation layer and can fail independently on Windows (quoting
   issues, PATH differences). This was the root cause of dev-env#81.

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

**Plan-then-optimize before acting:** Any task involving an Agent spawn, a skill invocation, reads across more than one file, or a switch to a new primary objective within the same session (e.g., moving from a `/review` or other skill output to addressing findings, or from one issue to another) requires this protocol. State a numbered plan first, then apply two explicit revision passes before taking any action.

**Pass 1 — Token efficiency:** check:
- Sequential tool calls that can be parallelized
- `Agent` spawns: all independent subagents must go in a single message with `run_in_background: true` — no synchronous preflight agent that a parallel sibling will redo (root cause of dev-env#51)
- File reads that can be skipped by reading an index or manifest instead of globbing
- Data a downstream step will recompute anyway

**Pass 2 — Outcome correctness:** after the efficiency revision, verify the optimized plan still produces the intended result:
- No implicit ordering dependency was broken by parallelizing two steps
- No read was dropped that a later step actually depends on for its inputs
- No Agent scope was narrowed so far that it misses required context
- The final outputs (files written, PRs opened, commits made) match what the original plan intended
- If the plan includes multiple PR merges, the stub-writing step appears once, after the last merge — not once per merge

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
  first check whether it exists on the remote before warning:
  ```bash
  git -C C:/Users/brown/Git/engineering-journal ls-remote --heads origin draft/YYYY-MM-DD
  ```
  A local-only draft branch means the PR was already squash-merged and the local ref was not
  cleaned up — ignore it silently. Only if the remote branch exists, emit a single line at
  the next natural pause:
  > "Incomplete journal detected — run `/journal-compose` in a dedicated session."
  Then continue with the user's actual request. Do not read stubs, do not compose, do not
  ask whether to compose.

- **PR grouping heuristic.** When two or more stubs share a PR number (one stub has it in
  `prs_opened`, another in `prs_closed`), compose them under a single H2 dialogue section
  rather than producing a separate H2 per stub. Annotate with "→ merged in session N" (where
  N is the 1-based ordinal of the closing stub for that day) at the end of the section. Any
  stub written on a day where `open-prs.jsonl` shows the same PR as open should also be grouped
  under that H2, even if neither `prs_opened` nor `prs_closed` is set for that PR in its
  manifest entry — this covers PRs that span more than two sessions. This prevents the composed
  journal from fragmenting create-iterate-review sequences into unrelated-looking sections.

---

### Stub file workflow

Each session writes an isolated stub file — no shared mutable draft. This eliminates write
contention when multiple sessions run in parallel. Slug is determined at day end.

**Branch:** `draft/YYYY-MM-DD` — created at the first session of the day, merged to main at day end.

**Stub filename:** `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`
where `YYYY-MM-DD` is the **local calendar date** and `HHMMSS` is the **local start time**
of the session (`date +%Y-%m-%d` / `date +%H%M%S`). Local time is used so stub filenames
and branch names always share the same calendar day. UTC is reserved for internal
operational artifacts (compose lock files, log file timestamps).

**First session of the day:**
1. `git -C C:/Users/brown/Git/engineering-journal checkout main && git pull`
2. `git -C C:/Users/brown/Git/engineering-journal checkout -b draft/YYYY-MM-DD`
3. Read `sessions/<project>/open-prs.jsonl` if it exists — include its PR list as session context before starting work.
4. Create `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` (see stub structure below)
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Append a manifest entry to `sessions/<project>/YYYY-MM-DD.manifest.jsonl` (see Manifest format below)
7. `git add sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD.manifest.jsonl sessions/<project>/open-prs.jsonl`, `git commit -m "draft: YYYY-MM-DD session 1"`, `git push -u origin draft/YYYY-MM-DD`
   *(omit `open-prs.jsonl` from the add command if it was not modified this session)*

**Subsequent sessions:**
1. `git -C C:/Users/brown/Git/engineering-journal pull origin draft/YYYY-MM-DD`
2. Read `sessions/<project>/open-prs.jsonl` if it exists — include its PR list as session context before starting work.
3. Find the most recent stub and read only its `<!-- next-session-context -->` paragraph:
   ```bash
   ls C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.stub.md | sort | tail -1
   ```
4. Create a new `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md` with the current session block
5. Add a `<!-- tokens: input=N output=N cost≈$N -->` comment at the end of the session block
6. Append a manifest entry to `sessions/<project>/YYYY-MM-DD.manifest.jsonl` (see Manifest format below)
7. `git add sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md sessions/<project>/YYYY-MM-DD.manifest.jsonl sessions/<project>/open-prs.jsonl`, `git commit -m "draft: YYYY-MM-DD session N"`, `git push`
   *(omit `open-prs.jsonl` from the add command if it was not modified this session)*

**Manifest format (`YYYY-MM-DD.manifest.jsonl`):**

One JSON line per session, appended after the token comment is known (end of session):
```bash
echo '{"stub":"YYYY-MM-DD_HHMMSS.stub.md","topic":"<H2 heading>","tokens":{"input":N,"output":N,"cost":N},"prs_opened":[],"prs_closed":[]}' \
  >> "C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD.manifest.jsonl"
```
(`YYYY-MM-DD` and `HHMMSS` are local time — same as the stub filename spec above.)

- `prs_opened`: PR numbers opened during this session (e.g., `[54]`). Empty array if none.
- `prs_closed`: PR numbers reviewed/merged during this session (e.g., `[54]`). Empty array if none.

The manifest lets `/journal-compose` see the session count, topics, token data, and PR lifecycle
without reading individual stubs. It is advisory: if the manifest is missing or has fewer entries
than the stub glob, stubs are authoritative. Never commit the manifest separately from its stubs
(include it in the same `git add` / commit step).

**Open-PR tracking file (`sessions/<project>/open-prs.jsonl`):**

Tracks PRs whose full lifecycle (open → review → merge) spans multiple sessions. Lives in the
engineering-journal repo; carried forward from day to day via the draft branch merge to main.

Schema — one JSON line per open PR:
```json
{"pr":54,"url":"https://github.com/brownm09/dev-env/pull/54","topic":"<H2 heading from stub>","stub":"YYYY-MM-DD_HHMMSS.stub.md","opened":"YYYY-MM-DD"}
```

- `stub`: the stub filename that opened this PR — used by `/journal-compose` to cross-reference the opening session when a PR spans multiple days.

- **When a session opens a PR:** append a line and commit it alongside the stub (see step 7 above).
- **When a session merges/closes a PR:** remove the matching line using `node -e`, then commit:
  ```bash
  node -e "
    const fs = require('fs');
    const path = 'C:/Users/brown/Git/engineering-journal/sessions/<project>/open-prs.jsonl';
    if (!fs.existsSync(path)) process.exit(0);
    const kept = fs.readFileSync(path,'utf8').trim().split('\n')
      .filter(l => l && JSON.parse(l).pr !== <PR_NUMBER>);
    if (kept.length) fs.writeFileSync(path, kept.join('\n') + '\n');
    else fs.unlinkSync(path);
  "
  ```
  If the last line is removed, the script deletes the file rather than leaving it empty.
- `/journal-compose` preserves this file unchanged in the merge-to-main commit so it carries forward to the next day.

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
- **Auto-create stub without user prompt on these events:**
  - PR opened — follow the Stub file workflow immediately after `gh pr create`. If no further work is planned (e.g., waiting on CI or human review), stop after writing the stub.
  - PR merged (including auto-merge) — write or update a stub for the merge session (see Git Workflow → Write a stub on PR merge), then stop.
  - PR closed without merging — stub was already written at PR creation; stopping is optional (see Git Workflow → PR closed without merging)
- The following do **not** auto-create a stub — they are not session boundaries:
  - Review-only sessions (`/review <PR-URL>`)
  - Pushing commits to an existing PR (e.g., addressing review findings) — stub was written when the PR was first opened

- Add to the current session's stub when a strategic decision is made mid-session
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
