---
name: weekly-memory-audit
description: Every Monday, reconcile agent memory against the repos across all projects (read-only on memory) — auto-file deduped promote issues for never-ported durables in the correct repo, report stale/drift findings, and commit a reconciliation report to engineering-journal.
schedule: "0 9 * * 1"
# Monday 09:00 LOCAL time (the scheduled-tasks scheduler evaluates cron in local time, not UTC).
# Weekly, every week — NO parity gate (unlike biweekly-retro, which gates to even ISO weeks).
---

Reconcile agent memory against the version-controlled instructions across every project. Run
**fully autonomously** — never call `AskUserQuestion`, never wait for input, never prompt for
approval.

> **Autonomous-run guard (do not strip when regenerating the live copy).** This is an unattended
> scheduled run with no human present. Do **not** open with a greeting, a question, or any "how can I
> help" / "what would you like to work on" reply — your **first output must be a tool call** (begin with
> the first step below). The live scheduled-task copy must carry this same imperative at the very top
> *and* bottom of its prompt, because the greeting-instead-of-execute failure it guards against happens
> *before* any canonical read-through step is reached. Rationale and incident history: the
> [`prune-stale-worktrees` reliability caveat](../prune-stale-worktrees/SKILL.md),
> [dev-env#698](https://github.com/brownm09/dev-env/issues/698), and
> [dev-env#703](https://github.com/brownm09/dev-env/issues/703) (which confirmed the frontmatter `model:`
> pin is **inert** — the scheduler ignores it — making this imperative the sole effective, model-agnostic
> mitigation). See the **Restorable live-copy imperative** at the bottom of this file.

**Objective:** Every Monday, walk every project's memory store, classify each entry against the
repos, and act in a deliberately **safe** shape: **read-only on memory — never edit or delete a
memory file**. Auto-file a deduped *promote* issue for each never-ported durable (a durable rule
with no current instruction home and no open tracking issue), routed to the **correct repo**.
Report stale/drift findings without auto-actioning them (they need human judgment). Produce a
committed cross-project reconciliation report in the engineering-journal repo, and report
completion via push notification.

This is the audit-time, cross-project, **non-destructive** complement to the single-project,
interactive `/memory-audit` skill (which can delete, human-in-the-loop). It extends the
memory-immortalization family — write-time porting (ADR-038), the write-time advisory hook
(ADR-048) — with a recurring audit-time backstop. The weekly cadence and read-only-on-memory shape
were chosen by the user on 2026-06-30 (see dev-env#439, child of dev-env#363).

---

## Step 0 — Sync the engineering-journal working tree

Read `~/.claude/skills/sync-routine-worktree/SKILL.md` and execute its Behavior section end-to-end
with these parameters:

- `REPO` = `C:/Users/brown/Git/engineering-journal`
- `VERIFY_FILE` = `sessions/meta/README.md`
- `PREFIX` = `weekly-memory-audit`

On **SUCCESS**, continue. On **ABORT**, exit cleanly — the push notification has already been sent;
do not commit, do not open a PR, do not create an issue.

The other repos (lifting-logbook, career-playbook, dev-env, …) are **not** synced here — their
instruction homes are verified by reading `origin/main` directly (`git fetch` + `git show
origin/main:<path>` in Step 2), so no working-tree sync is needed for them. The memory stores under
`~/.claude/projects/.../memory/` are machine-local live state, not a repo — nothing to sync.

---

## Step 1 — Enumerate the memory stores in scope

```bash
RUN_DATE=$(date +%Y-%m-%d)
SCRATCH="C:/Users/brown/.claude/scratch"
PROJECTS="C:/Users/brown/.claude/projects"
mkdir -p "$SCRATCH"
DIRS="$SCRATCH/memaudit_dirs_${RUN_DATE}.txt"
: > "$DIRS"

for memdir in "$PROJECTS"/*/memory/; do
  [ -d "$memdir" ] || continue
  projdir=$(basename "$(dirname "$memdir")")
  # EXCLUDE Claude-managed worktree project dirs — they hold subagent JSON, not durable memory.
  case "$projdir" in *--claude-worktrees-*) continue ;; esac
  # Require at least one memory entry (any .md, including MEMORY.md).
  shopt -s nullglob; mds=("$memdir"*.md); shopt -u nullglob
  [ ${#mds[@]} -gt 0 ] || continue
  echo "${projdir}|${memdir}" >> "$DIRS"
done
```

If `$DIRS` is empty, send a push notification — `weekly-memory-audit: no memory stores found —
nothing to audit` — and exit cleanly with status 0.

**Decode each project dir to its repo + GitHub slug** (used for instruction-home verification and
issue routing). The project dir encodes the working-tree path with separators replaced by `-`. Apply
this decode **once per entry when reading `$DIRS` in Step 2** — it is per-entry pseudocode, not a
sequential post-loop script:

```bash
# projdir e.g. "C--Users-brown-Git-lifting-logbook"
base="${projdir#C--Users-brown-Git-}"     # -> "lifting-logbook"
wt="C:/Users/brown/Git/${base}"           # working tree (may not exist)
slug=""                                    # GitHub "owner/repo", empty if no remote
if [ -d "$wt" ]; then
  url=$(git -C "$wt" remote get-url origin 2>/dev/null || true)
  if [ -n "$url" ]; then
    slug=$(printf '%s' "$url" | sed -E 's#\.git$##; s#^git@[^:]+:##; s#^https?://[^/]+/##')
  fi
fi
```

Resolve the slug from the **actual git remote**, not the dir name — e.g. the `job-search` working
tree may push to `brownm09/job-search-agent`. A project with no working tree or no remote routes its
findings to **dev-env** (see Step 3).

---

## Step 2 — Classify each project's memory in parallel (read-only)

For each project line in `$DIRS`, spawn **one background subagent** (`Agent` tool,
`subagent_type: Explore`, `run_in_background: true`) in a **single message with all spawns together**
(no synchronous preflight agent). `Explore` cannot Edit/Write, which structurally enforces the
read-only-on-memory guarantee. Give each a self-contained prompt naming that project's exact
`memory/` path and its decoded repo worktree + GitHub slug, and asking it to apply the read-only
classification subset of the `/memory-audit` skill.

For every `*.md` file in the memory dir **except `MEMORY.md`**, the subagent must:

1. Parse the frontmatter: `name`, `description`, and the entry type (accept either a top-level
   `type:` or a nested `metadata.type:` — both spellings exist). Read the body.
2. Detect an **immortalization link** in the body using the same patterns as
   `memory-write-advisory.py`: a GitHub ref `#\d+`, an `ADR-\d+` (case-insensitive), or the
   substrings `CLAUDE.md` or `Documented in repo`.
3. When the body **claims** an instruction home (a `CLAUDE.md`/docs path or "Documented in repo:
   <path>"), verify it on the **current remote**, not the local worktree:
   `git -C <repo-worktree> fetch origin --quiet` then `git show origin/main:<claimed-path>` — the
   claim is real only if the path resolves on `origin/main`. (The worktree can be a commit behind;
   verifying against `origin/main` avoids false "gap" flags.)
4. Assign exactly one **disposition**:
   - **remain** — durable (`user`/`feedback`/`project` encoding a cross-session rule) **and** a
     verified, current instruction home exists. Keep as a recall cache.
   - **promote** — durable, **no** immortalization link, **and** no verified instruction home: a
     *never-ported durable*. This is the forbidden state ADR-038 targets. (If the entry has a link
     to an **open** tracking issue but no instruction home yet, it is **tracked-pending** —
     report-only, do **not** promote; the existing issue is its dedup.)
   - **stale** — the body cites merged/closed/shipped work as still-pending, or is contradicted by
     current code. Report-only.
   - **drift** — the body names a file/function/flag that has moved or no longer exists.
     Report-only.
   - **transient** — session-local / fast-changing (open-PR lists, in-flight state). Not durable;
     no action (ADR-048 exempts these).
   - Also flag **index-drift**: the entry is missing from `MEMORY.md`, or its `MEMORY.md` line
     disagrees with the file. Report-only.

The subagent returns a structured findings response with two top-level fields:
- **`scanned`** (bool, required): `true` if the subagent successfully read and classified the memory
  files; `false` if it could not (permission error, empty-dir race, unexpected crash). A missing or
  null `scanned` field is treated as `false`. This field distinguishes a "subagent failure" from a
  legitimate "0 findings" result.
- **`reason`** (string, optional): when `scanned` is `false`, a one-line explanation of the failure
  (e.g., "permission error reading memory dir", "memory dir was empty during scan"). Omit when
  `scanned: true`. The orchestrator falls back to "subagent returned no data" when this field is
  absent and the subagent returned nothing at all.
- **`findings`** (list): one record per memory file with: file name, type, durable? (yes/no),
  instruction-home (path + verified yes/no, or "none"), disposition, a one-line rationale, and
  **for `promote` records additionally**: the rule text (verbatim or tight paraphrase), the suggested
  instruction home (which `CLAUDE.md`/doc it belongs in), and the entry's `name` slug. Must be an
  empty list when `scanned: false`.

If a subagent returns `scanned: false`, or returns nothing at all, **do not abort the whole run** —
record the project name and the `reason` field value (or "subagent returned no data" if the
subagent returned nothing) in a running not-scanned list for Step 5, and continue with a partial
report.

---

## Step 3 — Aggregate and route the promote findings

Collect every subagent's findings. Responses where `scanned` is `false` (or where nothing was
returned) are already tracked in the not-scanned list — exclude them from promote routing. For each
**promote** finding from a successfully-scanned project, determine the target repo:

- **Project-specific durable** (a rule that governs only that project) → that project's own repo
  (its resolved GitHub `slug`), when it has a remote with Issues enabled.
- **Global / cross-cutting durable** (a workflow rule that applies across projects) → **dev-env**
  (`brownm09/dev-env`).
- A project with **no working tree / no remote**, and the **engineering-journal** project
  (no issue tracker by convention) → **dev-env**.

Build a project-qualified dedup slug for each promote finding: `memory-slug = <projdir>/<name>`
(qualified by project dir so two projects' identically-named entries — and global rules from
different projects, which all land in dev-env — never collide).

---

## Step 4 — File the deduped promote issues (one per never-ported durable)

File **one issue per never-ported durable** — not one consolidated issue per repo. Per-rule issues
match ADR-048's immortalization model (each durable gets its own issue that drives it into the
instructions).

**Dedup guard (mandatory — keeps the weekly cadence from re-filing the same gap).** For each target
repo `R`, read its existing open `memory-audit` issues once, then skip any finding whose slug already
appears (no `jq` — parse with `node -e`):

```bash
# R, PROJ, NAME, RULE_TEXT, SUGGESTED_HOME, MEMORY_FILE are model-filled placeholders —
# the agent substitutes values from its Step 2/3 findings context (not shell assignments).
# R is the FULL owner/repo slug (e.g., "merickvaughn/lifting-logbook") from Step 3 routing.
ISSUES="$SCRATCH/memaudit_issues_${R//\//_}.json"
gh issue list --repo "${R}" --label memory-audit --state open --limit 500 \
  --json number,title,body > "$ISSUES" 2>/dev/null
if [ $? -ne 0 ]; then
  echo "WARN: gh issue list failed for ${R} — skipping filing this run to avoid duplicates" >&2
  continue
fi
if ! node -e "JSON.parse(require('fs').readFileSync(process.argv[1],'utf8'))" "$ISSUES" 2>/dev/null; then
  echo "WARN: gh issue list returned non-JSON for ${R} — skipping filing this run to avoid duplicates" >&2
  continue
fi

# returns DUP or NEW for a given slug
node -e '
  const fs=require("fs");
  const issues=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));
  const slug=process.argv[2];
  const hit=issues.some(i=>((i.title||"")+"\n"+(i.body||"")).includes(slug));
  console.log(hit?"DUP":"NEW");
' "$ISSUES" "$slug"
```

For each remaining (NEW) finding, ensure the label exists then file the issue:

Ensure the label exists, and pick a unique body-file path (Bash, so `$$` still supplies the
uniqueness):

```bash
gh label create memory-audit --repo "${R}" --color 5319e7 \
  --description "Never-ported durable surfaced by the weekly-memory-audit routine" 2>/dev/null || true
echo "$SCRATCH/memaudit_body_$$.md"
```

Then **write that file with the Write tool** — never a heredoc or a chain of `printf … >>` appends
(`claude/CLAUDE.md` → Authoring File Content, [ADR-138](../../../docs/adr/138-shell-content-write-guard.md);
`pre-tool-use-shell-content-write-guard.py` blocks the shell form). The body carries a verbatim rule
quotation and backticked paths, which is exactly the content shell quoting mangles. Substitute the
values you already hold — `$MEMORY_FILE`, `$RULE_TEXT`, `$SUGGESTED_HOME`, `$PROJ`, `$NAME` — as you
write it:

```markdown
A durable rule in agent memory has no current instruction home and no open tracking issue. The
weekly-memory-audit routine surfaced it for promotion into the version-controlled instructions
(per ADR-038 / ADR-048): memory is a private cache, not the source of truth.

**Memory file:** `<MEMORY_FILE>`
**Rule (from memory):**
> <RULE_TEXT>

**Suggested instruction home:** <SUGGESTED_HOME>

**To resolve:** port the rule into the suggested instruction file, link this issue from both the
memory body and its `MEMORY.md` pointer (ADR-048), then close.

memory-slug: <PROJ>/<NAME>

_Filed automatically by the `weekly-memory-audit` routine (dev-env
`claude/routines/weekly-memory-audit/`). Parents: dev-env#363, dev-env#439._
```

Then file the issue and clean up:

```bash
gh issue create --repo "${R}" --label memory-audit \
  --title "[memory-audit] Promote durable: ${NAME} (${PROJ})" \
  --body-file "<the path from above>"
rm -f "<the path from above>"
```

Capture every filed issue URL, and keep a running count of **filed** vs **deduped** (skipped) per
repo. If a repo has zero genuinely-new promote findings this run, file nothing for it.

Stale, drift, index-drift, tracked-pending, and remain dispositions are **never** auto-actioned —
they go to the report only (Step 5).

---

## Step 5 — Write and PR the reconciliation report

1. Ensure the audit folder exists, then create a one-time README if it is missing:

```bash
EJ="C:/Users/brown/Git/engineering-journal"
AUDIT_DIR="$EJ/sessions/meta/memory-audit"
mkdir -p "$AUDIT_DIR"
ls "$AUDIT_DIR/README.md"   # absent -> write it with the Write tool, below
```

If that `ls` reports the file is missing, write `${AUDIT_DIR}/README.md` **with the Write tool**
(never a heredoc — `claude/CLAUDE.md` → Authoring File Content, [ADR-138](../../../docs/adr/138-shell-content-write-guard.md);
`pre-tool-use-shell-content-write-guard.py` blocks the shell form) with this content:

```markdown
# Cross-Project Memory→Repo Reconciliation Audits

Auto-generated by the `weekly-memory-audit` routine (dev-env `claude/routines/weekly-memory-audit/`).
Each `YYYY-MM-DD-audit.md` reconciles every project's agent memory against the version-controlled
instructions. The routine is **read-only on memory** — it never edits or deletes a memory file
(deletion stays human-in-the-loop via the `/memory-audit` skill). It auto-files deduped *promote*
issues (label `memory-audit`) for never-ported durables, routed to the correct repo
(project-specific → that repo; global/cross-cutting → dev-env); stale / drift / index-drift findings
are reported here only. See ADR-069, ADR-038, ADR-048.
```

2. Write the report to `${AUDIT_DIR}/${RUN_DATE}-audit.md`:
   - A title + the run date + the projects scanned.
   - A one-line summary: `<N> projects, <F> memory files; <G> never-ported durables → <I> issues
     filed (<D> deduped); <S> stale/drift/index-drift findings (report-only)`.
   - The full **cross-project reconciliation table**, grouped by project:

     ```
     | Project | Memory file | Type | Durable? | Instruction home (verified) | Drift | Disposition |
     |---|---|---|---|---|---|---|
     ```
   - A **"Promote issues filed"** subsection — each new issue (repo#N + URL) and the deduped/skipped
     slugs.
   - A **"Stale / drift / index-drift (report-only)"** subsection — each finding with file, what is
     stale/wrong, and the suggested human fix. These are *not* auto-actioned by design.
   - A **"Projects not scanned (subagent failures)"** subsection — **include only when at least one
     subagent returned `scanned: false` or returned no data at all**. Format:
     ```
     ## Projects not scanned (subagent failures)
     | Project | Reason |
     |---|---|
     | lifting-logbook | Subagent returned no data |
     ```
     Omit this section entirely when every subagent returned `scanned: true`. A missing section means
     "no scan failures" — not that failures were silently swallowed.

3. Commit on a dedicated branch and open a PR to `main` (this repo squash-merges; do **not** commit
   to `main` directly, and do **not** auto-merge — auto-merge is disabled by ADR-031, the user
   reviews and merges):

```bash
git -C "$EJ" checkout -b "memory-audit/${RUN_DATE}" origin/main 2>/dev/null \
  || git -C "$EJ" checkout "memory-audit/${RUN_DATE}"
git -C "$EJ" add "sessions/meta/memory-audit/${RUN_DATE}-audit.md" "sessions/meta/memory-audit/README.md"
git -C "$EJ" diff --cached --quiet \
  || git -C "$EJ" commit -m "[docs] Weekly memory audit ${RUN_DATE} (cross-project reconciliation)"
git -C "$EJ" push -u origin "memory-audit/${RUN_DATE}"
gh pr create --repo brownm09/engineering-journal \
  --base main --head "memory-audit/${RUN_DATE}" \
  --title "Weekly memory audit ${RUN_DATE}" \
  --body "Automated cross-project memory→repo reconciliation for ${RUN_DATE}. Read-only on memory (no memory file was edited or deleted). Promote issues filed: <Step 4 urls>. Stale/drift findings are report-only — see the report."
```

If any git/PR step fails, push-notify `weekly-memory-audit: report git/PR step failed — draft at
${AUDIT_DIR}/${RUN_DATE}-audit.md` and continue to Step 6 (the report file still exists locally for
recovery).

---

## Step 6 — Report

Send a push notification summarizing the run:

```
weekly-memory-audit ${RUN_DATE} complete — <N> projects, <F> memory files.
<G> never-ported durables; <I> issues filed (<D> deduped).
<S> stale/drift findings (report-only).
Report PR: <url>
```

Clean up any scratch files this run created.

---

## Constraints

- **Read-only on memory.** The routine **never** edits or deletes a memory file or `MEMORY.md`.
  Deletion and in-place fixes stay human-in-the-loop via the interactive `/memory-audit` skill. An
  unattended cron must not mutate the user's memory.
- **Memory stores:** `C:/Users/brown/.claude/projects/*/memory/`; exclude `*--claude-worktrees-*`
  project dirs.
- **Engineering-journal repo:** `C:/Users/brown/Git/engineering-journal`; report path
  `sessions/meta/memory-audit/YYYY-MM-DD-audit.md`.
- **Cadence:** weekly Monday 09:00 local trigger — **no parity gate** (runs every week).
- **One promote issue per never-ported durable**, labelled `memory-audit`, deduped by the
  project-qualified `memory-slug`. **Never** call `AskUserQuestion`.
- **Never** commit to `main`; open a PR. **Never** auto-merge (ADR-031).
- **Scratch dir:** `C:/Users/brown/.claude/scratch/` — all temp files; never `/tmp/`.
- **No `jq`** — use `node -e` for JSON parsing.
- **Platform:** Windows 11, Git Bash syntax.
- **App-open caveat:** scheduled tasks run while the Claude app is open; if it was closed when the
  task was due, the run happens on next launch.

> **Dual-copy registration caveat (dev-env#344).** This file is the **canonical, version-controlled**
> definition (`dev-env/claude/routines/weekly-memory-audit/`, surfaced at
> `~/.claude/routines/weekly-memory-audit/` via the directory junction). The scheduler reads a
> **separate** live copy at `~/.claude/scheduled-tasks/weekly-memory-audit/SKILL.md`, materialized by
> the `create_scheduled_task` MCP tool — the two do **not** auto-sync. Any edit to this routine must
> be applied to **both** copies (update this file in a dev-env PR, then re-register / update the live
> task via the scheduled-tasks MCP). See ADR-069.

---

**Restorable live-copy imperative ([dev-env#703](https://github.com/brownm09/dev-env/issues/703) item 3, [dev-env#767](https://github.com/brownm09/dev-env/issues/767)).**
The execute-now / do-not-greet mitigation ([dev-env#698](https://github.com/brownm09/dev-env/issues/698))
is the **only** effective, model-agnostic guard against an autonomous scheduled run greeting instead of
executing — the frontmatter `model:` pin is confirmed **inert** (dev-env#703 item 2). It lives verbatim
only in the machine-local live copy (`~/.claude/scheduled-tasks/weekly-memory-audit/SKILL.md`, which is
**not** version-controlled), so the exact deployed strings are captured here — a machine rebuild, or a
live-copy regeneration from this canonical file, restores the hardened guard **deterministically**
rather than reconstructing it from memory. When (re)creating the live copy, paste the **top** block as
its first line (immediately after the YAML frontmatter) and the **bottom** block as its last line; keep
both verbatim, including the ASCII `--` in the top block and the em dash in the bottom block.

_Top — first line of the live prompt:_

```text
EXECUTE NOW -- DO NOT GREET. This is an autonomous scheduled run; no human is present. Do NOT reply with a greeting, a question, or any variant of "how can I help" / "what would you like to work on" -- a concrete task is defined below and your FIRST output MUST be a tool call (begin with the first step below). If you catch yourself about to acknowledge, greet, or ask what to do, stop and begin executing the first step instead.
```

_Bottom — last line of the live prompt:_

```text
REMINDER: Begin immediately. Your first action is a tool call for the first step below — not a text reply. Do not greet or ask what to work on.
```
