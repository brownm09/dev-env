---
name: journal-compose
description: Compose the end-of-day engineering journal from today's stub files. Runs inside an isolated engineering-journal worktree — the shared canonical checkout is never branch-switched or committed to. Discovers all YYYY-MM-DD_*.stub.md files, sorts and merges them, produces the canonical 11-section document, updates READMEs, commits, and opens the PR. Invoke as /journal-compose [YYYY-MM-DD] [--force].
argument-hint: "[YYYY-MM-DD] [--force]"
allowed-tools: Read Edit Write Bash Glob Grep Agent
---

You are composing the end-of-day engineering journal from the day's draft file.
Follow every step in order. Do not skip steps.

Supporting files:
- `~/.claude/skills/sources.md` — shared primary source library, organized by topic tag;
  use Grep on this file before spawning any research subagent (see Section 11)

## Step 0 — Session isolation check

Before doing anything else, check the conversation history *prior to the message that
triggered this skill invocation*. Tool calls made during this skill's own execution do not
count. Only pre-invocation user turns and assistant responses are evidence of prior task work.

If any user turns or assistant responses exist in the conversation before the
`/journal-compose` invocation (i.e., other tasks were handled first), stop immediately and respond:

> "Journal composition must run in a dedicated session with no prior task work.
> Open a fresh Claude Code session and invoke `/journal-compose` there."

Do not proceed to Step 1.

## Step 0.5 — Plan-then-optimize (required)

Before reading any file or spawning any agent, write out:
1. The steps you are about to execute (numbered)
2. Which file reads can be skipped entirely (e.g., if manifest data is sufficient, skip the stub read)
3. Which agent spawns will be batched in a single message with `run_in_background: true`

Do not proceed to Step 1 until this plan is written. No tool calls before the revision pass completes.

## Step 0.6 — Resolve compose date and create the isolated compose worktree

Every read, write, and git operation in the steps below happens inside a dedicated, disposable
worktree of the engineering-journal repo — **never** in the shared canonical checkout
`C:/Users/brown/Git/engineering-journal`, which other sessions may be using concurrently at any
moment ([ADR-082](https://github.com/brownm09/dev-env/blob/main/docs/adr/082-journal-compose-worktree-isolation.md)).
Define, and use verbatim throughout every remaining step:

```bash
EJ=C:/Users/brown/Git/engineering-journal
```

**These are not persisted shell variables.** `$EJ`, `$WT` (defined below), and `$SOURCE_BRANCH`
(resolved below) are referenced across Steps 0.6 through 11, each of which you run as a separate
Bash tool call — shell state does not persist between tool calls. Treat these exactly like the
`<project>`/`YYYY-MM-DD`/`<slug>` placeholder tokens used throughout the rest of this skill: carry
the concrete resolved value (the literal worktree path, the literal branch name) in your own
context, and substitute it verbatim into every command you construct in later steps. Do not assume
a bare `$WT`/`$EJ`/`$SOURCE_BRANCH` reference in a fresh Bash call will resolve to anything —
substitute the actual value every time.

**Resolve the compose date and source branch.** Normally the source branch is exactly
`draft/YYYY-MM-DD`; the one documented exception is the
[draft-branch recovery runbook](https://github.com/brownm09/dev-env/blob/main/docs/REFERENCE.md#engineering-journal-internals),
which recovers onto a `draft/YYYY-MM-DD-recovery` branch when the plain one was accidentally
merged or deleted mid-day. Resolve `SOURCE_BRANCH` (and the date), in order:

1. If `$ARGUMENTS` (minus any `--force`) matches a full branch name `draft/<anything>` (e.g.
   `draft/YYYY-MM-DD-recovery`), use it verbatim as `SOURCE_BRANCH` and extract the leading
   `YYYY-MM-DD` as the date. Otherwise, if it matches a bare `YYYY-MM-DD`, use that date with
   `SOURCE_BRANCH=draft/YYYY-MM-DD`.
2. Otherwise, check the canonical's current branch (read-only, legacy compatibility):
   ```bash
   git -C "$EJ" branch --show-current
   ```
   If it matches `draft/<anything>`, resolve it the same way as step 1.
3. Otherwise, scan the remote for draft branches (`ls-remote` queries the remote directly, so no
   prior `fetch` is needed here — the unconditional `fetch` below, right before worktree creation,
   covers what the rest of this step needs):
   ```bash
   git -C "$EJ" ls-remote --heads origin \
     | grep -oE 'refs/heads/draft/[0-9]{4}-[0-9]{2}-[0-9]{2}(-recovery)?$' \
     | sed 's#refs/heads/##' | sort -r
   ```
   Exactly one match → resolve it the same way as step 1. Multiple matches → list them (most
   recent first) and ask the user which to compose, noting that a long list likely means stale
   drafts need a separate cleanup pass. Zero matches → nothing to compose; stop.

**Parse `$ARGUMENTS` for `--force`:** if present, set `FORCE=true` and strip it before the date
match above; otherwise `FORCE=false`.

**Today-guard** (unchanged — [ADR-017](https://github.com/brownm09/dev-env/blob/main/docs/adr/017-journal-compose-today-guard.md)):

```bash
TODAY=$(date +%Y-%m-%d)
```

If the resolved date equals `$TODAY` **and** `FORCE` is false, stop immediately and respond:

> "`/journal-compose` targets completed days only. `draft/YYYY-MM-DD` is **today's** branch —
> stubs may still be written during later sessions today.
> To compose today's journal intentionally (all stubs written, end of day):
> `/journal-compose --force`"

Do **not** proceed to worktree creation or any further step. If the date equals `$TODAY` and
`FORCE` is true, or the date is not today, proceed.

**Create the isolated compose worktree:**

```bash
WT="$EJ/.claude/worktrees/compose-YYYY-MM-DD"

git -C "$EJ" fetch origin
git -C "$EJ" show-ref --verify --quiet "refs/remotes/origin/$SOURCE_BRANCH" || \
  { echo "No origin/$SOURCE_BRANCH — nothing to compose (or stubs were never pushed)"; exit 1; }

# Divergence guard: a local ref ahead of origin means unpushed stubs exist somewhere (a
# stub-writing session's own worktree, or the canonical) — worktrees share refs, so this
# check sees them regardless of which checkout holds the commits.
if git -C "$EJ" show-ref --verify --quiet "refs/heads/$SOURCE_BRANCH"; then
  git -C "$EJ" merge-base --is-ancestor "refs/heads/$SOURCE_BRANCH" \
      "refs/remotes/origin/$SOURCE_BRANCH" || \
    { echo "ABORT: local $SOURCE_BRANCH has commits not on origin — unpushed stubs in some session/worktree. Find and push them, then re-run."; exit 1; }
fi

# Liveness guard (ADR-086): the divergence guard above only catches a session that has
# already committed but not yet pushed. A session that hasn't committed at all — still
# actively writing this date's stub past compose time (dev-env#579 activated this race; see
# ADR-084) — leaves the shared $EJ checkout dirty instead, since sessions across every
# project write here via `git -C`, not a per-session worktree of the journal itself (ADR-051's
# worktree_session_is_live() doesn't transfer: there's no single worktree path to check here).
# This is a defense-in-depth check for a manual/interactive `/journal-compose` invocation —
# the automated nightly path's primary check runs earlier, in journal-compose-with-retry.sh,
# before this skill is even invoked.
#
# Substitute the actual resolved date for "YYYY-MM-DD" below before running this — the script
# validates its argument is a real YYYY-MM-DD date and exits 2 (loud usage error) rather than
# silently passing if the literal placeholder is left unsubstituted. The scoped
# `set -o pipefail` matches journal-compose-with-retry.sh's identical fix (review finding, PR
# #587): a bare `cmd1 | cmd2 || exit 1` only checks cmd2's exit code, so a `git status` failure
# would otherwise feed the script empty stdin and silently pass as "clean".
(set -o pipefail; git -C "$EJ" status --porcelain | py -3 C:/Users/brown/.claude/scripts/check-journal-compose-liveness.py "YYYY-MM-DD") || exit 1

# A pre-existing compose worktree is a concurrency signal, not an error: a lock file inside
# it younger than 10 minutes means another compose is genuinely active; otherwise it's stale
# (a crashed prior run) and safe to recreate — the worktree is fully regenerable from
# origin/$SOURCE_BRANCH. Also honor the .compose-creating sentinel (written immediately below,
# right after worktree add) — the per-project .draft-compose.lock isn't written until Step 1,
# so a SECOND compose landing in the narrow window between "worktree created" and "first lock
# written" would otherwise see no lock at all and wrongly conclude "stale", destroying the
# first invocation's still-initializing worktree.
if [ -d "$WT" ]; then
  FRESH=false
  if [ -f "$WT/.compose-creating" ]; then
    AGE=$(( $(date +%s) - $(date -d "$(cat "$WT/.compose-creating")" +%s 2>/dev/null || echo 0) ))
    [ "$AGE" -lt 600 ] && FRESH=true
  fi
  for LOCK in "$WT"/sessions/*/.draft-compose.lock; do
    [ -f "$LOCK" ] || continue
    AGE=$(( $(date +%s) - $(date -d "$(cat "$LOCK")" +%s 2>/dev/null || echo 0) ))
    [ "$AGE" -lt 600 ] && FRESH=true
  done
  if [ "$FRESH" = true ]; then
    echo "ABORT: another compose for YYYY-MM-DD appears active (fresh lock in $WT)"; exit 1
  fi
  git -C "$EJ" worktree remove --force "$WT" || { rm -rf "$WT"; git -C "$EJ" worktree prune; }
fi

git -C "$EJ" worktree add --detach "$WT" "refs/remotes/origin/$SOURCE_BRANCH"
date -u +%Y-%m-%dT%H:%M:%SZ > "$WT/.compose-creating"
```

`.compose-creating` is untracked (like `.draft-compose.lock`) and must never be committed — it
exists only so a racing second compose sees "creation in progress" instead of "no lock = stale"
during the window before Step 1 writes the first per-project lock.

The worktree is **detached** — deliberately, since `$SOURCE_BRANCH` may already be checked out
as a named branch by a stub-writing session's own worktree. A detached checkout never contends
for the branch ref.

**Push-failure rule (applies to every push from here through Step 10):** a push to
`$SOURCE_BRANCH` rejected because the remote has moved means new stubs landed mid-compose. Do
not rebase over content you haven't read — fetch, note what's new, and re-run from this step
(which recreates the worktree from the new tip). The one exception is the pre-push hook's
merged-draft-branch block (refuses a push to a `draft/YYYY-MM-DD` that already has a merged PR,
except same-day — the `-recovery` suffix exists specifically to bypass this for a branch that
already merged on a prior day). **This specific rejection does not "route to" Step 10.5
automatically — it requires an explicit jump:** the commit already succeeded (only the push was
rejected), so go directly to Step 10.5's `CONFLICT_LINES > 0` recovery block, treating the
rejection exactly like a detected conflict — skip the merge-tree probe and Step 10.5's normal
push entirely, since there is nothing to push to `$SOURCE_BRANCH` at that point.

From here on, every command in this skill runs against `"$WT"`, not `"$EJ"` — except where a
step explicitly says otherwise (a handful of read-only canonical queries, and the final branch
cleanup in Step 11, which can only happen after the worktree is removed).

## Step 0.7 — Validate manifest field completeness

Before reading any stubs or running any subagent, validate that every manifest shard has all
five required fields (`stub`, `topic`, `tokens`, `prs_opened`, `prs_closed`). Missing fields
cause mid-compose failures that are hand-patched; this gate surfaces them up front (dev-env #423).

Locate the manifest shards for the compose date, inside the compose worktree created in Step
0.6 (substitute the resolved date for `YYYY-MM-DD`):

```bash
ls "$WT"/sessions/*/YYYY-MM-DD_*.manifest.jsonl 2>/dev/null
ls "$WT"/sessions/*/YYYY-MM-DD.manifest.jsonl 2>/dev/null
```

Pass every path found to the validator:

```bash
py -3 C:/Users/brown/.claude/scripts/validate-manifest.py \
  "$WT"/sessions/*/YYYY-MM-DD_*.manifest.jsonl \
  "$WT"/sessions/*/YYYY-MM-DD.manifest.jsonl
```

- **Exit 0:** All manifest entries have the required fields — proceed to Step 0.8.
- **Exit 1:** Stop immediately. The script prints every entry with its missing fields and its
  file path + line number. Fix each entry (add the missing fields with correct values), then
  re-run the validator. Do not proceed with composition until every entry passes.

If no manifest files exist for the date, the validator exits 0 (nothing to validate) — proceed.

This is the compose-time (next-day) half of the gate; the same schema is enforced at write time,
in the writing session, by the `journal-shard-write-advisory.py` PostToolUse hook — both share
`claude/scripts/_journal_schema.py`, so a schema change updates one module instead of two gates
drifting apart. Per [ADR-081](https://github.com/brownm09/dev-env/blob/main/docs/adr/081-write-time-journal-shard-validation-hook.md)
(dev-env #556).

## Step 0.8 — Validate JSONL files

Before reading any stubs or manifests, run the JSONL validator against the compose worktree,
using its own copy of the script where available:

```bash
if [ -f "$WT/scripts/validate-jsonl.js" ]; then
  node "$WT/scripts/validate-jsonl.js"
else
  # draft branch predates the script — use the canonical's copy, targeted explicitly at $WT
  # (the script resolves its target from argv[2], or from its own __dirname/../sessions when
  # no argument is given — an explicit argument is required here since it isn't running from $WT)
  [ -f "$EJ/scripts/validate-jsonl.js" ] || \
    { echo "validate-jsonl.js not found — merge the companion engineering-journal PR first"; exit 1; }
  node "$EJ/scripts/validate-jsonl.js" "$WT/sessions"
fi
```

- **Exit 0:** All `.jsonl` files under `sessions/` are valid — proceed to Step 1.
- **Exit non-zero:** Stop immediately. Report the offending file(s) and line(s) printed by the validator. Do not proceed with composition until the errors are resolved — a malformed manifest line can cause sessions to be silently misread or omitted from the composed journal.

---

## Step 1 — Locate stubs and acquire compose lock

The compose date and `$WT` were already resolved in Step 0.6 — this step only discovers stubs
and acquires the lock, both inside the compose worktree.

**Check for manifests (fast path):**

Per [ADR-056](https://github.com/brownm09/dev-env/blob/main/docs/adr/056-per-session-sharding-journal-companion-files.md),
each session writes its own manifest shard `YYYY-MM-DD_HHMMSS.manifest.jsonl` (a single JSON object,
paired 1:1 with its stub). A day composed before ADR-056 may instead have one legacy per-day
`YYYY-MM-DD.manifest.jsonl` (one line per session). Read **both** and union the entries, deduped by the
`stub` field:

```bash
# per-session manifest shards (current format — one object per file)
ls "$WT"/sessions/*/YYYY-MM-DD_*.manifest.jsonl 2>/dev/null
# legacy per-day manifest (pre-ADR-056; one line per session; may be absent)
ls "$WT"/sessions/*/YYYY-MM-DD.manifest.jsonl 2>/dev/null
```

The two globs are disjoint (the shard glob requires the `_HHMMSS` underscore; the legacy name has none).
If any manifests exist, read them to get a session overview before touching stubs:
- Number of sessions per project (shard count + legacy line count, deduped by `stub`)
- Topics (for slug synthesis and day structure)
- Token data per session (supplemental for Step 4 — JSONL log is still authoritative)

If the manifest entry count (shards + any legacy lines, deduped by `stub`) differs from the stub glob
count below, treat stubs as authoritative.

**Check for open-PR context:**

Per [ADR-056](https://github.com/brownm09/dev-env/blob/main/docs/adr/056-per-session-sharding-journal-companion-files.md),
open PRs are tracked as per-PR shards `sessions/<project>/open-prs/<N>.json` (one object per open PR). A
pre-ADR-056 day may instead carry a single legacy `sessions/<project>/open-prs.jsonl`. Read **both**:

```bash
# per-PR shards (current format — one object per file)
ls "$WT"/sessions/*/open-prs/*.json 2>/dev/null
# legacy single file (pre-ADR-056; one line per open PR; may be absent)
ls "$WT"/sessions/*/open-prs.jsonl 2>/dev/null
```

If found, read each shard / line and record the union (deduped by `pr`) as `OPEN_PRS`. This is used in
Step 5 to group sessions that span multiple days under the same PR. For each entry:
- If the PR's `prs_closed` appears in today's manifest, the PR was opened in a previous session
  (possibly a previous day). The `stub` field identifies the opening session for cross-referencing.
- If the PR has no `prs_closed` in today's manifest, it is still open — do not group anything
  for it; the file carries forward unchanged to the next day.

**Find stub files:**

```bash
ls "$WT"/sessions/*/YYYY-MM-DD_*.stub.md 2>/dev/null | sort
```

If no stubs are found, fall back to a legacy draft:
```bash
find "$WT"/sessions -name "YYYY-MM-DD_draft.md"
```
If a legacy draft is found, use it as a monolithic draft (skip the lock step below and proceed
as in the old single-file workflow — read it once in Step 2).

If stubs span multiple project directories (e.g., both `sessions/lifting-logbook/` and
`sessions/meta/`), use **Multi-project mode** (see section below) — do NOT compose projects
sequentially in this session. Proceed directly to that section instead of Step 2.

**Acquire the compose lock:**

Check for a lock at `sessions/<project>/.draft-compose.lock`:
```bash
LOCK="$WT/sessions/<project>/.draft-compose.lock"
if [ -f "$LOCK" ]; then
  LOCK_TIME=$(cat "$LOCK")
  LOCK_EPOCH=$(date -d "$LOCK_TIME" +%s 2>/dev/null || echo 0)
  NOW_EPOCH=$(date +%s)
  AGE=$(( NOW_EPOCH - LOCK_EPOCH ))
  if [ "$AGE" -lt 600 ]; then
    echo "LOCK_ACTIVE=true AGE=$AGE"
  else
    echo "LOCK_STALE=true AGE=$AGE"
  fi
fi
```

- **`LOCK_ACTIVE`:** Abort. Tell the user: "Compose is already running (lock age: ${AGE}s). If
  this is stale, delete `sessions/<project>/.draft-compose.lock` and retry."
- **`LOCK_STALE`:** Warn the user ("Stale compose lock (${AGE}s old) — overriding."), then continue.
- **No lock:** Continue.

Create the lock:
```bash
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$WT/sessions/<project>/.draft-compose.lock"
```

Tell the user: "Composing journal from N stub(s): `<stub1>`, `<stub2>`, ..."

**Note for retries after a crash:** A crashed compose no longer needs manual lock deletion or
partial-output checks — Step 0.6 detects a stale lock in a pre-existing compose worktree and
recreates the worktree fresh. Just re-run `/journal-compose YYYY-MM-DD`.

## Multi-project mode

Use this when Step 1 finds stubs in more than one project directory.

Running projects sequentially inflates context with each pass — by project 3 you are paying
for projects 1 and 2 sitting idle in the window. Instead, spawn one isolated Haiku subagent
per project in parallel (Steps 2–6), then run the shared git work once in this session (Steps 7–11).

### Phase 1 — Parallel compose (one Haiku subagent per project)

For each project directory, spawn an Agent with `model: "haiku"`. Send all spawns in a
single message so they run concurrently. Use this prompt template per subagent (substitute
the bracketed values):

---

```
You are composing the engineering journal for one project. Follow these steps exactly.

**Date:** YYYY-MM-DD
**Project path:** sessions/<project>/
**Compose worktree root:** C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-YYYY-MM-DD
**Stub files (in order):** <stub1>, <stub2>, ...

<!-- mirrors Step 0.5 in main flow — keep in sync; intentional differences: item 2 scoped to skip/read (not grep), item 3 broader (all parallel tool calls, not only agent spawns) -->
Step 0.5 — Plan-then-optimize (required). Before any tool call, write out:
  1. The steps you will execute (numbered)
  2. Which reads can be skipped entirely (e.g., if manifest data is sufficient, skip the stub read)
  3. Confirm no sequential tool calls exist that could run in parallel
  Do not proceed until this plan is written.

Step 1 — Acquire compose lock for this project using this project-scoped path, rooted at the
  compose worktree root given above:
  <worktree-root>/sessions/<project>/.draft-compose.lock
  Follow the lock check/create procedure in SKILL.md Step 1 ("Acquire the compose lock").

Step 1b — Read this day's manifest for a session overview (topics, token data) before reading
  individual stubs. Per ADR-056 each session has its own shard `YYYY-MM-DD_HHMMSS.manifest.jsonl`
  (one object per file); a pre-ADR-056 day may instead have a legacy per-day
  `YYYY-MM-DD.manifest.jsonl` (one line per session). Read both globs and union by `stub`.

Step 2 — Read stubs following SKILL.md Step 2 extraction format.

Step 2b — Meta trigger check. Do NOT prompt the user, ask questions, or create any meta
  draft files — the Phase 2 coordinator handles user interaction. Instead, scan each session
  block against the trigger table in SKILL.md Step 2b and record any matches for the final
  report. Proceed immediately to Step 3.

Step 3 — Determine the slug. If unclear, synthesize from the session H2 headings; do not
  ask the user. Report your chosen slug.

Step 4 — Fetch real token data. Sanitize the project name first (slashes → underscores):
  PROJECT_SAFE=$(echo "<project>" | tr '/' '_')
  py -3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD --format json > \
    "C:/Users/brown/.claude/scratch/tmp_tokens_${PROJECT_SAFE}_$$.json"
  then parse as shown in SKILL.md Step 4.

Step 5 — Compose the 11-section document. Follow SKILL.md Step 5 exactly.
  **Fidelity floor.** Your composed dialogue sections must preserve the technical detail in the source stubs. As a rough heuristic, your composed document's line count should be ≥ 80% of the combined stub line count. A journal that compresses to < 50% of source-stub length is a quality failure — re-read the stubs and expand before writing. Reproduce code blocks, file paths, exact PR/issue numbers, and decision rationale verbatim where the stub provides them. Synthesis is allowed only when stubs duplicate each other; raw compression of unique content is not.

Step 6 — Write the output file to:
  <worktree-root>/sessions/<project>/YYYY-MM-DD-<slug>.md
  **Worktree-root path check.** Your output path must begin with the compose worktree root given
  above — NOT `C:/Users/brown/Git/engineering-journal/sessions/` (the shared canonical checkout,
  which this compose must never write to), and NOT any path under your own session's working
  directory. Writing anywhere else breaks the commit flow and can leak output into an unrelated
  repo's worktree.

Step 6.5 — Self-check before claiming done. After writing the file, run `wc -l` on it and compare to the combined source stub line count. If the journal is < 50% of source length, the compose is incomplete — expand it before proceeding. Report the ratio in your final status as `LINE_COUNT=<n> SOURCE_LINES=<m> FIDELITY=<n/m>`.

Step 6.6 — Structural assertion. <!-- mirrors the main flow's Step 6.5 structural-assertion block — keep the 11 chk() lines in sync between the two copies. --> A composed journal missing a required section (e.g. no
  "## Next Session Context") is a quality failure just like low fidelity. Verify the file you
  just wrote contains every required heading:
    FILE="<worktree-root>/sessions/<project>/YYYY-MM-DD-<slug>.md"
    MISSING=""
    chk() { grep -qE "$1" "$FILE" || MISSING="${MISSING:+$MISSING,}$2"; }
    chk '^# Session Transcript — '              'header'
    chk '^- \[Opening Brief\]\(#opening-brief\)' 'TOC'
    chk '^## Opening Brief$'                     'Opening Brief'
    chk '^## Key Decisions$'                     'Key Decisions'
    chk '^## Session [0-9]+ — '                  'Session dialogue H2'
    chk '^## Open Items / Next Steps$'           'Open Items / Next Steps'
    chk '^## Token Usage$'                       'Token Usage'
    chk '^## Token Optimization Suggestions$'    'Token Optimization Suggestions'
    chk '^## Next Session Context$'              'Next Session Context'
    chk '^## Reflection$'                        'Reflection'
    chk '^## Further Reading$'                   'Further Reading'
    [ -z "$MISSING" ] && echo "STRUCTURE=ok" || echo "STRUCTURE=missing:$MISSING"
  If STRUCTURE is not ok, fix the missing section(s) in the file you wrote — re-read SKILL.md
  Step 5 for the exact heading text and content — and re-run this check before reporting done.

Do NOT do Steps 7–11 (no README edits, no git add/commit/push, no PR).

When done, report exactly this structure:
  OUTPUT_FILE=<absolute path>
  SLUG=<slug>
  META_TRIGGERS=<none | comma-separated list of trigger types found>
  LINE_COUNT=<n>
  SOURCE_LINES=<m>
  FIDELITY=<n/m>
  STRUCTURE=<ok | missing:<list>>
  STATUS=done
```

---

### Phase 2 — Serial coordinator (this session)

After all subagents complete, collect `OUTPUT_FILE`, `SLUG`, `META_TRIGGERS`, and `STRUCTURE`
from each.

**Error check first:** If any subagent did not return `STATUS=done`, **or** returned
`STRUCTURE=missing:<list>`, stop immediately and report which project(s) failed — and, for a
structure failure, which sections were missing — before touching any README or running git
commands. Do not proceed with a partial set: a missing output file breaks the commit, and a
malformed journal ships a broken document. Re-spawn a failed subagent once, appending the
missing-heading list (or the failure reason) to its prompt; if it fails the same way twice, fix
the file by hand before continuing.

If all subagents returned `STATUS=done` and `STRUCTURE=ok`, check meta triggers.
If any subagent reported `META_TRIGGERS` (non-none), present them to the user now:
```
Meta triggers found in <project> session(s): <list>
Should I open a meta draft block? (y/n)
```
Handle the response as described in Step 2b before continuing.

Then for each project in sequence:
- **Step 7** — Update `sessions/<project>/README.md`
- **Step 8** — Update the top-level `README.md` (one pass covering all projects)
- **Step 9** — Delete stubs and release lock for this project
- **Step 9.5** — Reconcile this project's open-PR shards

Finally, do one combined commit and PR (**Steps 10–11**) that stages all projects' files:
```bash
WT=C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-YYYY-MM-DD
# Stage all composed files and README updates
git -C "$WT" add \
  sessions/project-a/YYYY-MM-DD-<slug-a>.md \
  sessions/project-b/YYYY-MM-DD-<slug-b>.md \
  ... \
  sessions/project-a/README.md \
  sessions/project-b/README.md \
  README.md
# Stage deleted stubs/shards and reconciled open-PR shards across all projects
git -C "$WT" add -u sessions/
git -C "$WT" commit -m \
  "[docs] Add YYYY-MM-DD journals: <slug-a>, <slug-b>, ..."
git -C "$WT" push origin "HEAD:refs/heads/$SOURCE_BRANCH"
```

Open one PR covering all projects (Step 11). List each composed journal in the PR body, plus
the combined `RECONCILED_SHARDS` list from every project's Step 9.5.

After completing Phase 2, skip to the end — do not re-run Steps 2–9 individually.

---

## Step 2 — Read all stubs

Read each stub file in ascending filename order (which equals chronological session order).
Do not re-read stubs after this step.

From the stubs, extract:
- **Date** — the `YYYY-MM-DD` prefix from any stub filename
- **Project path** — the `sessions/<project>/` directory component of the stub paths
- **Opening brief** — from the `<!-- opening-brief -->` block in the **first** stub only.
  If absent, use `"First session for this project — no prior Next Session Context."`
- **Session blocks** — from each stub, in filename order:
  `<!-- session: <slug> -->` … `<!-- tokens: ... -->` … `<!-- next-session-context -->` unit
  - Session slug, H2 heading, body content
  - Token comment (may be absent — note if missing)
  - Next-session-context paragraph
- **Last next-session-context** — the final `<!-- next-session-context -->` paragraph across
  all stubs (used in Section 9)

## Step 2b — Check for meta-relevant content (project drafts only)

Skip this step if the draft path is `sessions/meta/`.

Using the session blocks already extracted in Step 2, scan each block's body text for
content matching the meta journal trigger criteria from `~/.claude/CLAUDE.md`:

| Trigger | What to look for |
|---|---|
| `CLAUDE.md` modified | References to "CLAUDE.md" alongside a change, addition, or update |
| New platform constraint | Platform-specific discovery (Windows, Git Bash, nvm, PATH, temp file paths) paired with a fix or workaround |
| Workflow failure remediated | A hook, script, or process that failed, was diagnosed, and fixed |
| Cross-project convention established | Language like "all repos", "global", "convention", "from now on" |
| `dev-env` PR merged | Any `brownm09/dev-env#N` PR reference |
| Journal structure changed | Changes to journal sections, skill logic, draft format, or token tracking |
| New canonical reference identified | A new external resource, repo, or tool added as a persistent reference |

For each session block, note which triggers (if any) apply and why.

If **no triggers match**, continue to Step 3.

If **one or more triggers match**, present the findings to the user before continuing:

```
The following session blocks contain content that may belong in sessions/meta/:

- Session N (`<slug>`): <trigger type> — <one-line reason>

Should I open a meta draft block for sessions/meta/YYYY-MM-DD_draft.md now?
Enter: y (append meta block and continue), n (skip meta, continue composing), or describe what to do.
```

If the user confirms (`y` or equivalent):
1. Check whether `sessions/meta/YYYY-MM-DD_draft.md` exists in the compose worktree.
   - If not: create it with `<!-- draft: YYYY-MM-DD -->\nOpening brief: Meta entries from project session — see source journal.\n`
2. Append one `<!-- session: <meta-slug> -->` block per matched trigger, summarizing the
   meta-relevant content. Use a slug like `platform-constraint-<topic>` or `dev-env-pr-N`.
3. Add `<!-- tokens: input=0 output=0 cost≈$0.00 -->` and a `<!-- next-session-context -->`
   paragraph at the end of each block.
4. `git -C "$WT" add sessions/meta/YYYY-MM-DD_draft.md`, `git -C "$WT" commit -m "draft: YYYY-MM-DD meta — <topic>" -- sessions/meta/YYYY-MM-DD_draft.md`, `git -C "$WT" push origin "HEAD:refs/heads/$SOURCE_BRANCH"`.
   The compose worktree is private to this run, but keep the `--` pathspec discipline anyway as
   defense in depth (see `claude/CLAUDE.md` → Engineering Journal → Stub file workflow →
   "Commit with an explicit pathspec"). A rejected push follows the Step 0.6 push-failure rule.
5. Resume at Step 3.

If the user declines (`n` or equivalent), continue to Step 3 without creating a meta entry.

## Step 3 — Determine the slug

The slug for the final filename comes from the overall day's theme.

Check the draft for a clear unifying theme across session blocks:
- If one session dominated the day, use its slug
- If multiple sessions share a theme, synthesize a slug (e.g., `token-tracker-and-dev-env`)
- If the theme is unclear, ask the user: "What slug should I use for the final filename?
  (e.g., `v02-core-api-sprint`)"

The output filename will be: `sessions/<project>/YYYY-MM-DD-<slug>.md`

Tell the user the proposed filename and confirm before writing.

## Step 4 — Fetch real token data

Token data comes from two sources. Collect both; the JSONL log is authoritative.

**Source A — Real JSONL data (authoritative):**

```bash
TMPFILE="C:/Users/brown/.claude/scratch/tmp_tokens_$$.json"
py -3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD --format json > "$TMPFILE"
node -e "
  const d = JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));
  console.log('SESSION_COUNT=' + d.length);
  d.forEach((s, i) => {
    const t = s.tokens || {};
    console.log('S' + i + '_ID=' + (s.session_id||'').slice(0,8));
    console.log('S' + i + '_BRANCH=' + (s.git_branch||''));
    console.log('S' + i + '_TURNS=' + (s.turn_count||0));
    console.log('S' + i + '_INP=' + (t.input_tokens||0));
    console.log('S' + i + '_OUT=' + (t.output_tokens||0));
    console.log('S' + i + '_CR=' + (t.cache_read_input_tokens||0));
    console.log('S' + i + '_CW=' + (t.cache_creation_input_tokens||0));
    console.log('S' + i + '_COST=' + (s.estimated_cost_usd||0).toFixed(4));
    console.log('S' + i + '_SUB=' + (s.subagent_count||0));
  });
"
rm -f "$TMPFILE"
```

Also run the markdown report for use in the Token Usage section:
```bash
py -3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD
```

Note the session count from the JSONL log. **Important:** the current journal-compose
session will not appear in the log yet — the Stop hook fires after this conversation ends.
That session's data will need to be added retroactively if it matters.

**Source B — Draft token comments (supplemental):**

For each `<!-- session: <slug> -->` block in the draft, extract the
`<!-- tokens: ... -->` comment if present. Format variants:
- `<!-- tokens: input=N output=N cost≈$N -->`
- `<!-- tokens: input=N output=N cache_r=N cache_w=N cost≈$N -->`

These values were pulled from the CLI session summary at the time of writing and
should roughly match the JSONL data. Use them to correlate slugs to JSONL session rows.

**Matching JSONL rows to draft slugs:**

Sort JSONL sessions by `first_turn_ts` ascending. Match to draft session blocks in order
(first JSONL session → Session 1 slug, second → Session 2 slug, etc.).

If the JSONL count ≠ draft session block count, note the discrepancy. Possible causes:
- A scratch/exploratory Claude Code session ran that day with no corresponding draft block
- The journal-compose session itself (current) is absent from the log (expected)
- Multiple draft slugs correspond to one CLI session (unlikely but possible)

When counts don't match, use real JSONL data for Combined Totals and note which sessions
couldn't be matched by slug.

## Step 5 — Compose the 11-section document

Compose the complete document in order. Every section is required.

---

### Section 1 — Header block

```
# Session Transcript — YYYY-MM-DD

**Topic:** <one-line summary of the day's work — synthesize from all session H2s>
**Repo / Branch:** <repo and branches worked on, drawn from draft content>
**Issues closed:** <comma-separated linked issues, or "None">
**PRs merged:** <comma-separated linked PRs, or "None">
```

---

### Section 2 — Table of Contents

Generate anchor links for all H2 and H3 sections that follow.
Format: `- [Section Name](#anchor)` where anchor is lowercase, spaces → hyphens, special chars dropped.
Nest H3 entries under their parent H2 with two-space indent.

Always include these entries (in this order):
- Opening Brief
- Key Decisions
- (one entry per session dialogue section, with sub-entries for H3s)
- Open Items / Next Steps
- Token Usage
- Token Optimization Suggestions
- Next Session Context
- Reflection
- Further Reading

---

### Section 3 — Opening Brief

```
## Opening Brief

> *[paste the opening brief from the draft verbatim, or "First session for this project — no prior Next Session Context." if applicable]*
```

---

### Section 4 — Key Decisions

```
## Key Decisions

### Session N

- **<decision title>** — <rationale, one sentence>. ([§N.M](#anchor-to-dialogue-subsection)[, Issue #N](url)])
```

One `### Session N` heading per session block.
Pull decisions from the draft body — look for choices made, tradeoffs resolved, patterns adopted.
Link each decision to its dialogue subsection using the anchor format `#nN--slug`.
Link to issues/PRs where referenced in the draft.

---

### Section 5 — Dialogue sections

**PR grouping (before writing any H2s):** Scan the manifest(s) for `prs_opened` and
`prs_closed` fields, and check `OPEN_PRS` (from Step 1). For any PR number that either:
- appears in both `prs_opened` and `prs_closed` in today's manifests (same-day lifecycle), or
- appears in `OPEN_PRS` and also in `prs_closed` in today's manifests (cross-day lifecycle),

group all related session blocks under one `## Session N — <PR topic>` H2 instead of
producing a separate H2 per stub. Order: opening session content first, any iteration sessions
next, closing session content last. Annotate the end of the section with "→ merged in session N"
where N is the 1-based ordinal of the closing stub for the day. For cross-day PRs, the opening
stub is identified by the `stub` field in `OPEN_PRS` — note the original session date inline:
"(opened YYYY-MM-DD — see [stub-filename])".

Any stub that has neither `prs_opened` nor `prs_closed` set for a grouped PR but was written
on a day when that PR was open in `OPEN_PRS` should also be merged into that H2.

One `## Session N — <Title>` per session block, drawn from the draft's H2s.
Use H3s for sub-topics within a session.
Reproduce the draft content faithfully — do not summarize or omit technical detail.
Reformat for readability (code fences, bullets) but preserve meaning.

Label sessions: `## Session 1 — <slug title>`, `## Session 2 — <slug title>`, etc.

**Fidelity floor.** The composed dialogue sections must preserve the technical detail in the source stubs. As a rough heuristic, the composed document's line count should be ≥ 80% of the combined stub line count. A journal that compresses to < 50% of source-stub length is a quality failure — re-read the stubs and expand before writing. Reproduce code blocks, file paths, exact PR/issue numbers, and decision rationale verbatim where the stub provides them. Synthesis is allowed only when stubs duplicate each other; raw compression of unique content is not.

---

### Section 6 — Open Items / Next Steps

```
## Open Items / Next Steps

- [ ] <item>
- [ ] <item>
```

Extract from:
1. Any explicit "next steps" listed in draft session blocks
2. The last `<!-- next-session-context -->` block (convert implied next work to checkbox items)
3. Any items flagged as deferred or TODO in the draft

---

### Section 7 — Token Usage

```
## Token Usage
```

**Primary source is the JSONL log fetched in Step 4.** Draft `<!-- tokens: ... -->` comments
are supplemental and used only for slug labeling or when no JSONL data exists.

**7a — Per-session breakdown**

For each JSONL session row (matched to a draft slug where possible):

```
### Session N — <slug> (or <session-id[:8]> if unmatched)

| | Value |
|---|---|
| Model | claude-sonnet-4-6 |
| Input tokens | N |
| Output tokens | N |
| Cache read tokens | N |
| Cache write tokens | N |
| Turns | N (+N subagent turns if applicable) |
| Estimated cost | $N |
```

If a JSONL row cannot be matched to a draft slug, label it with the short session ID
and note: *"No corresponding draft session block — may be a scratch session or the
journal-compose session itself."*

If the current journal-compose session is absent from the log (expected, since Stop hook
hasn't fired yet), add a placeholder:

```
### Session N — journal-compose (current session, pending)

*Token data not yet available — Stop hook fires after this conversation ends.*
*Run `py -3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD` after session close*
*to get the actual figures and update this section if needed.*
```

If **no JSONL data exists at all** for this date (e.g., token tracking was not yet active
for this project's sessions), fall back to draft comments and label each table:
*"Source: draft token comment — retroactive estimate, not from JSONL log."*
If a draft comment is also absent, use a retroactive estimate based on session scope:
short session (< 30 min) ≈ 15k input / 3k output; medium (1–2 hours) ≈ 50k / 8k;
long (> 2 hours) ≈ 100k+ / 15k+. Label: *"Retroactive estimate — no JSONL data or draft comment."*

**7b — Raw session table from token-report.py**

Insert the markdown output of `token-report.py --date YYYY-MM-DD` verbatim under a
`#### All sessions (from token-report.py)` sub-heading. This is the authoritative
unmodified record.

**7c — Combined Totals**

Sum across all JSONL rows for the date (excluding the current compose session if absent):

```
### Combined Totals

| Session | Input | Output | Cache R | Cache W | Turns | Cost |
|---|---|---|---|---|---|---|
| 1 — <slug> | N | N | N | N | N | $N |
| 2 — <slug> | N | N | N | N | N | $N |
| *(compose — pending)* | — | — | — | — | — | — |
| **Total** | **N** | **N** | **N** | **N** | **N** | **$N** |
```

Total row excludes any pending/unresolved rows.

---

### Section 8 — Token Optimization Suggestions

```
## Token Optimization Suggestions

### Session N

- <observation about context efficiency, prompt length, or tool call patterns>
- <2–4 observations per session>

### Cross-Session Patterns

- <generalizable findings that apply across sessions>
```

Observations should be specific and actionable. Examples:
- "Draft opened with full file read; a targeted grep would have reduced input by ~20k tokens"
- "Three agent spawns in one turn — could have been batched with one multi-task prompt"
- "Session ran long without a context-reset; splitting at the halfway point would have saved cache misses"

---

### Section 9 — Next Session Context

```
## Next Session Context

<paste the final <!-- next-session-context --> paragraph from the draft verbatim>
```

This section is required in all published journals — project journals and meta entries alike.

---

### Section 10 — Reflection

```
## Reflection

<2–5 bullet points covering: gaps in the work, risks introduced, strategic questions
raised but not resolved, anything surprising or worth revisiting>
```

Write this section last (logically). Pull from the full day's content.

---

### Section 11 — Further Reading

```
## Further Reading
```

1–3 primary sources per session. The goal is sources that explain the *reasoning* behind
key decisions made in that session — not tutorials or summaries. Prefer:
- Named practitioners writing from real enterprise experience
- Official documentation for technology choices
- Peer-reviewed papers or specifications
- Books with free canonical URLs (SRE book, SE@Google, etc.)

**Do this in two passes:**

**Pass 1 — Grep the source library (zero token cost):**

For each session, extract the 2–4 most significant decision keywords from the Key Decisions
section (e.g., "hexagonal architecture", "testcontainers", "monorepo", "ADR", "REST").

Grep `~/.claude/skills/sources.md` for each keyword:
```bash
grep -i "<keyword>" "~/.claude/skills/sources.md" -A 2
```

A match in the tags line of a section means that section contains relevant sources.
Read the matched entries. Select the 1–3 most directly applicable to the decisions made.
Do not cite a source just because the keyword matched — read the one-sentence relevance
note and confirm it fits.

**Pass 2 — Spawn a research subagent only if Pass 1 yields fewer than 1 source for a session:**

Use the Agent tool to spawn a general-purpose subagent with this task:

> Find 1–2 primary sources (no summaries, no blog posts without named authors) that a
> senior engineer at a company like Stripe, Netflix, Google, or Uber would cite when
> making this decision: "<decision description from Key Decisions>".
> Prefer: named-practitioner engineering blog posts, peer-reviewed papers, official specs,
> or free book chapters. Return title, author/org, URL, and one sentence on relevance.
> Do not fabricate URLs — only return sources you can verify exist.

The subagent runs in isolation so its research does not expand this session's context.
Use its output to supplement the source library entry if the source is high-quality;
add it as a new entry to `~/.claude/skills/sources.md` under the appropriate tag
section for future use (this grows the library over time without extra effort).

**Output format per session:**

```
### Session N — <slug>

- [<Title>](<URL>) — <one sentence: what this source explains and why it matters for the
  specific decision made in this session>. *(<Author/Org>, <year if known>)*
```

If a session had no externally-referenceable decisions (pure implementation/tooling work
with no architectural choices), write:
```
### Session N — <slug>

*No primary sources — session was implementation work with no architectural decisions.*
```

Do not pad with tangentially related sources to hit a count. One precise citation
is better than three loose ones.

---

## Step 6 — Write the output file

Write the composed document to:
```
C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-YYYY-MM-DD/sessions/<project>/YYYY-MM-DD-<slug>.md
```

- **Worktree-root path check.** The output path must begin with the compose worktree root
  created in Step 0.6 (`$WT`) — NOT `C:/Users/brown/Git/engineering-journal/sessions/` (the
  shared canonical checkout, which this compose must never write to). Writing anywhere else
  breaks the commit flow.

## Step 6.5 — Self-check before claiming done

After writing the file, run `wc -l` on it and compare to the combined source stub line count. If the journal is < 50% of source length, the compose is incomplete — expand it before proceeding. Report the ratio: `LINE_COUNT=<n> SOURCE_LINES=<m> FIDELITY=<n/m>`.

**Structural assertion** (mirrors the multi-project subagent template's Step 6.6 — keep the 11
`chk()` lines in sync between the two copies). Also verify every required section heading is
present — a composed journal missing a section (e.g. no `## Next Session Context`) is a quality
failure just like low fidelity:

```bash
FILE="$WT/sessions/<project>/YYYY-MM-DD-<slug>.md"
MISSING=""
chk() { grep -qE "$1" "$FILE" || MISSING="${MISSING:+$MISSING,}$2"; }
chk '^# Session Transcript — '              'header'
chk '^- \[Opening Brief\]\(#opening-brief\)' 'TOC'
chk '^## Opening Brief$'                     'Opening Brief'
chk '^## Key Decisions$'                     'Key Decisions'
chk '^## Session [0-9]+ — '                  'Session dialogue H2'
chk '^## Open Items / Next Steps$'           'Open Items / Next Steps'
chk '^## Token Usage$'                       'Token Usage'
chk '^## Token Optimization Suggestions$'    'Token Optimization Suggestions'
chk '^## Next Session Context$'              'Next Session Context'
chk '^## Reflection$'                        'Reflection'
chk '^## Further Reading$'                   'Further Reading'
[ -z "$MISSING" ] && echo "STRUCTURE=ok" || echo "STRUCTURE=missing:$MISSING"
```

If `STRUCTURE` is not `ok`, fix the missing section(s) using Step 5's exact heading text and
content, then re-run this check before proceeding to Step 7.

## Step 7 — Update the folder README

Operating inside the compose worktree (`$WT`) for the rest of this skill. Check whether
`sessions/<project>/README.md` exists (i.e. `$WT/sessions/<project>/README.md`).

**If it does not exist**, create it with this structure:
```markdown
# <Project Name> — Journal

Session transcripts for the `<project>` project.

## Progress Summary

<2–3 sentence narrative: what the project is, what phase it is currently in,
and what the most recent session accomplished or decided. Write from the perspective
of someone opening this folder for the first time.>

**Where to start next session:** <paste the Next Session Context from the journal
you just wrote, so the folder README always points to the current thread.>

## Entries

| Date | Session | Topics |
|---|---|---|
| YYYY-MM-DD | [<slug title>](<filename>.md) | <comma-separated topic keywords> |
```

**If it already exists**, read it and append a new row to the Entries table and
update the Progress Summary and "Where to start next session" sections to reflect
today's journal.

## Step 8 — Update the top-level README

Read `$WT/README.md`.

The top-level README uses a hub-and-spoke layout: **no inline entry tables**. Each project
section uses a labeled-bullet structure (defined in `engineering-journal/CLAUDE.md` →
`## Top-level README Format`). Entry tables live only in the folder READMEs (already updated
in Step 7).

For the project you just composed:
- Find its `### <Project Name>` section in the top-level README
- Update the bullets to reflect any changes from today's journal (Open Items, Next Session
  Context, PRs opened/merged)
- **Hyperlink every PR and issue reference** — `[#N](https://github.com/owner/repo/pull/N)`
  for PRs, `[#N](https://github.com/owner/repo/issues/N)` for issues
- Omit any bullet whose value is empty — never write placeholder dashes
- Do not add entry rows — the folder README owns the table

Top-level entry format (labeled bullets, no prose block):
```markdown
### <Project Name>

<One-line lead sentence — what the project is; no dates or status.>

**Status:** Archived / Superseded / Complete   ← only for finished projects; omit for active ones
**Recent:** [#N](url) — description, [#M](url) — description   ← omit if no work in last 1–2 sessions
**Open:** [#N](url) — topic; [#M](url) — topic   ← use `None` if explicitly closed out; omit if not applicable
**Next:** One concrete sentence.   ← omit if no clear next step; no dates
**Repo:** [owner/repo](https://github.com/owner/repo)   ← always include
**Journal:** [sessions/<project>/](sessions/<project>/README.md)   ← always include
```

If the project does not yet appear in the README, add a new `### <Project>` section
under `## Projects` using this format.

## Step 8a — Update top-level "Start here" dashboard block

After Step 8, refresh the marker-delimited block at the top of `engineering-journal/README.md`. This block surfaces a freshness stamp and the top 3–5 cross-project priorities, drawn from (a) priorities flagged in today's manifests, (b) PRs currently open across projects, and (c) open issues labeled `start-here` across project repos — top 5 across all sources, deduped by ref. It is rewritten on every compose; it is not hand-edited between composes. See [ADR-032](https://github.com/brownm09/dev-env/blob/main/docs/adr/032-journal-start-here-dashboard.md) for rationale.

**Aggregate the priority list (max 5 entries, deduped by `ref`):**

```bash
TMPFILE="C:/Users/brown/.claude/scratch/tmp_start_here_$$.json"
node -e "
  const fs = require('fs'); const path = require('path');
  const root = '$WT';   // interpolated directly, same as \$TMPFILE below — no manual date
                         // substitution needed here (unlike the date constant right below this
                         // one), since \$WT already has the resolved date baked into its path
  const date = 'YYYY-MM-DD';   // <-- substitute the compose date
  const items = [];
  const seen = new Set();
  // Normalize refs to a single canonical form so manifest-style 'repo#N' and
  // open-prs-derived 'owner/repo#N' dedup against each other. Convention:
  // strip the owner prefix — manifests are owner-less by convention.
  const normRef = (r) => {
    const s = (r || '').trim();
    if (!s) return '';
    const m = s.match(/^[^\/]+\/([^#]+#\d+)$/);
    return m ? m[1] : s;
  };
  const push = (it) => {
    const norm = normRef(it.ref);
    const key = norm || ('__no_ref_' + items.length);
    if (seen.has(key)) return;
    seen.add(key);
    items.push(it);
  };
  const sessionsDir = path.join(root, 'sessions');
  const projects = fs.readdirSync(sessionsDir).filter(d =>
    fs.statSync(path.join(sessionsDir, d)).isDirectory());
  // Source 1: today's manifests — per-session shards (ADR-056) + legacy per-day file
  for (const proj of projects) {
    const projDir = path.join(sessionsDir, proj);
    const manifestLines = [];
    // per-session shards: <date>_*.manifest.jsonl (one JSON object each)
    let names = [];
    try { names = fs.readdirSync(projDir); } catch (e) { names = []; }
    for (const name of names) {
      if (name.startsWith(date + '_') && name.endsWith('.manifest.jsonl')) {
        try { manifestLines.push(fs.readFileSync(path.join(projDir, name), 'utf8').trim()); } catch (e) {}
      }
    }
    // legacy per-day file: <date>.manifest.jsonl (one object per line)
    const legacy = path.join(projDir, date + '.manifest.jsonl');
    if (fs.existsSync(legacy)) {
      for (const line of fs.readFileSync(legacy,'utf8').split('\n').filter(Boolean)) manifestLines.push(line);
    }
    for (const line of manifestLines) {
      try {
        const m = JSON.parse(line);
        for (const p of (m.priorities || [])) {
          push({ source:'manifest', project:proj, label:p.label, ref:p.ref||'', why:p.why||'' });
        }
      } catch (e) {}
    }
  }
  // Source 2: open PRs across projects — per-PR shards (ADR-056) + legacy file (fills to 5)
  if (items.length < 5) {
    for (const proj of projects) {
      if (items.length >= 5) break;
      const projDir = path.join(sessionsDir, proj);
      const prLines = [];
      // per-PR shards: open-prs/<N>.json (one object each)
      const shardDir = path.join(projDir, 'open-prs');
      if (fs.existsSync(shardDir)) {
        let shardNames = [];
        try { shardNames = fs.readdirSync(shardDir); } catch (e) { shardNames = []; }
        for (const name of shardNames) {
          if (name.endsWith('.json')) {
            try { prLines.push(fs.readFileSync(path.join(shardDir, name), 'utf8').trim()); } catch (e) {}
          }
        }
      }
      // legacy single file (pre-ADR-056)
      const legacy = path.join(projDir, 'open-prs.jsonl');
      if (fs.existsSync(legacy)) {
        for (const line of fs.readFileSync(legacy,'utf8').split('\n').filter(Boolean)) prLines.push(line);
      }
      for (const line of prLines) {
        if (items.length >= 5) break;
        try {
          const o = JSON.parse(line);
          const ref = (o.url||'').replace(/^https:\/\/github.com\//,'').replace(/\/pull\//,'#');
          push({ source:'open-prs', project:proj, label:o.topic||('PR #'+o.pr), ref, why:'open since '+o.opened, url:o.url });
        } catch (e) {}
      }
    }
  }
  // Source 3: open issues labeled 'start-here' across project repos (fills to 5).
  // Project -> repo slug is read from each sessions/<proj>/README.md 'Repo:' line;
  // projects without a parseable Repo: line are skipped.
  if (items.length < 5) {
    const { execSync } = require('child_process');
    for (const proj of projects) {
      if (items.length >= 5) break;
      const readme = path.join(sessionsDir, proj, 'README.md');
      if (!fs.existsSync(readme)) continue;
      const txt = fs.readFileSync(readme, 'utf8');
      const m = txt.match(/Repo:\s*\[([^\]]+)\]\(https:\/\/github\.com\/([^\/]+\/[^\/\)]+)\)/);
      if (!m) continue;
      const slug = m[2].replace(/\.git$/,'');
      let out = '';
      try {
        out = execSync('gh issue list --repo ' + slug + ' --label start-here --state open --json number,title,url --limit 5', { encoding: 'utf8' });
      } catch (e) { continue; }
      let issues = [];
      try { issues = JSON.parse(out); } catch (e) { continue; }
      for (const iss of issues) {
        if (items.length >= 5) break;
        const ref = slug + '#' + iss.number;
        push({ source:'issue', project:proj, label:iss.title, ref, why:'open issue', url:iss.url });
      }
    }
  }
  fs.writeFileSync('$TMPFILE', JSON.stringify(items.slice(0,5), null, 2));
  console.log('PRIORITY_COUNT=' + Math.min(items.length, 5));
"
```

**Render the block:**

Read `$TMPFILE` (an array of `{source, project, label, ref, why, url?}` objects, max 5).
For each entry, render one numbered bullet per the rules below the template.

```
<!-- start-here:begin -->
**Last composed:** YYYY-MM-DD

## Start here

Top priorities across all projects (max 5):

1. **[<ref-or-label>](<url>) — <label>** *(<project>)* — <why>
2. ...

<!-- start-here:end -->
```

If `PRIORITY_COUNT=0`, render the body as `*No flagged priorities and no open PRs.*` (omit the numbered list).

For each item:
- If the source is `open-prs` or `issue`, use `url` directly as the link target.
- Else if `ref` (post-normalization) matches `repo#N` or `owner/repo#N`, render the link as `https://github.com/<owner>/<repo>/pull/N`. Use `brownm09` as the owner when the manifest ref omits it. A GitHub `/pull/N` URL 302s to `/issues/N` when N is an issue, so always emitting `/pull/` is safe.
- If neither a `url` nor a parseable `ref` is available, render the label without a link.
- Always italicize the project name in parentheses after the label.
- Append `— <why>` only if `why` is non-empty.

**Insert or replace in `engineering-journal/README.md`:**

1. Read the current README.
2. If `<!-- start-here:begin -->` and `<!-- start-here:end -->` both exist, replace everything between (and including) the markers with the new block.
3. Otherwise, insert the new block immediately above the first `## Projects` heading, with one blank line before `## Projects`.
4. Use the Edit tool with `replace_all: false` — there is only ever one such block in the file.
5. **Anchor-missing abort:** if neither the marker pair nor a `## Projects` heading is present, do not silently skip. Report `START_HERE_INSERT_FAILED: anchor not found` to the user and continue the compose (Step 9 onward) — the rest of the journal still ships, but the dashboard refresh is flagged so the README structural drift gets diagnosed.

The block lives above any `## Projects` H2 and below the file's top-level `# Engineering Journal` title.

Tell the user: "Start here block refreshed with N item(s)."

Clean up:
```bash
rm -f "$TMPFILE"
```

**Promoting an issue to the dashboard:**

To promote an open issue to the top-level dashboard, apply the `start-here` label in its repo:
```bash
gh issue edit <N> --repo <owner/repo> --add-label start-here
```
The label is auto-created on first use. Remove it when the issue is no longer top-priority.

## Step 9 — Delete stub files and release lock

Delete all stubs and this day's manifest for the date and release the compose lock. Delete the
per-session manifest shards (ADR-056) **and** any legacy per-day manifest. **Do not** delete the
open-PR records (`open-prs.jsonl` or the `open-prs/` shard directory) here — that happens
deliberately in Step 9.5, which follows:
```bash
rm "$WT"/sessions/<project>/YYYY-MM-DD_*.stub.md
# per-session manifest shards (ADR-056)
rm -f "$WT"/sessions/<project>/YYYY-MM-DD_*.manifest.jsonl
# legacy per-day manifest (pre-ADR-056; harmless if absent)
rm -f "$WT"/sessions/<project>/YYYY-MM-DD.manifest.jsonl
rm -f "$WT"/sessions/<project>/.draft-compose.lock
```

For legacy single-file compose, delete the draft file instead:
```bash
rm "$WT"/<draft-file-path>
```

Tell the user: "Draft artifacts deleted."

**Lock file hygiene:** `.draft-compose.lock` is ephemeral and must never be committed. If it
appears in `git status` as an untracked file during compose, that is expected — `git add -u`
(Step 10) will not stage it because it has never been committed. Do not run `git add .` or
`git add sessions/<project>/` (without `-u`) — that would stage the lock file.

## Step 9.5 — Reconcile open-PR shards

This step deliberately replaces a sweep that worktree isolation removes: `reconcile-open-prs.py`
(a `UserPromptSubmit` hook) already unlinks merged-PR shards in the *canonical* working tree, but
never commits — by its own docstring, it leaves them "dirty for the next stub commit" to pick up.
Since compose no longer touches the canonical's working tree at all, that pickup never happens
anymore. This step is the deliberate, verified replacement, continuing the exact precedent
engineering-journal [PR #150](https://github.com/brownm09/engineering-journal/pull/150)'s body
already set ("...all verified merged via gh before deletion").

For every `open-prs/<N>.json` shard for this project in the compose worktree, look up its PR's
state and remove it if resolved:

```bash
RECONCILED=""
for SHARD in "$WT"/sessions/<project>/open-prs/*.json; do
  [ -f "$SHARD" ] || continue
  URL=$(node -e "console.log((JSON.parse(require('fs').readFileSync('$SHARD','utf8')).url)||'')")
  N=$(basename "$SHARD" .json)
  REPO=$(echo "$URL" | sed -E 's#https://github.com/([^/]+/[^/]+)/pull/.*#\1#')
  if [ -z "$REPO" ]; then
    echo "WARNING: shard $SHARD has no parseable url — cannot reconcile, keeping as-is"
    continue
  fi
  STATE=$(gh pr view "$N" --repo "$REPO" --json state --jq .state 2>/dev/null || echo "")
  case "$STATE" in
    MERGED|CLOSED) git -C "$WT" rm --quiet -- "$SHARD" && RECONCILED="$RECONCILED $REPO#$N($STATE)";;
    *) : ;;  # OPEN, or gh call failed → keep (conservative — mirrors reconcile-open-prs.py's own default)
  esac
done
echo "RECONCILED_SHARDS=${RECONCILED:-none}"
```

If a legacy `open-prs.jsonl` exists for this project, apply the same per-entry state check to
each line, rewrite the file with only the still-open entries (or delete it if now empty), and
`git -C "$WT" add` the result.

Carry `RECONCILED_SHARDS` forward — it goes into the Step 11 PR body, and (on the Step 10.5
conflict-recovery path only) may need to be re-applied after checking out `origin/main`'s tree.

## Step 10 — Commit

```bash
git -C "$WT" add sessions/<project>/YYYY-MM-DD-<slug>.md
git -C "$WT" add sessions/<project>/README.md
git -C "$WT" add README.md
# Stage deleted stubs/shards and Step 9.5's reconciled open-PR shards
git -C "$WT" add -u sessions/<project>/
```

**Verify what's staged before committing.** Unlike the per-session stub workflow (which
pathspecs its commit to a short, fixed file list), this step's `git add -u sessions/<project>/`
stages a variable number of deletions, so a static pathspec doesn't fit cleanly:

```bash
git -C "$WT" diff --cached --name-only
```

Every line must be `README.md`, `sessions/<project>/README.md`,
`sessions/<project>/YYYY-MM-DD-<slug>.md`, or a path under `sessions/<project>/` that this
compose run itself just consumed — a stub, a manifest shard, a legacy `.manifest.jsonl` /
`open-prs.jsonl` being drained, or an `open-prs/<N>.json` shard **that appears in this run's
`RECONCILED_SHARDS` list from Step 9.5**. Because the compose worktree is private to this run
(nothing else ever writes to it), anything else in the staged diff signals a mistake in the
compose flow itself — not a concurrent session — but still means **stop**; do not commit until
you understand what produced it.

```bash
git -C "$WT" commit -m "[docs] Add YYYY-MM-DD journal: <slug>"
git -C "$WT" push origin "HEAD:refs/heads/$SOURCE_BRANCH"
```

A rejected push follows the Step 0.6 push-failure rule — except a rejection from the pre-push
hook's merged-draft-branch block, which means the draft branch already has a merged PR from a
prior day (the #147-morning/#150-evening shape). This commit already succeeded, so skip Step
10.5's merge-tree probe entirely and jump directly to its `CONFLICT_LINES > 0` recovery block
below, exactly as if a conflict had been detected.

**Before proceeding to Step 11**, run Step 10.5 to check whether the draft branch can be
cleanly merged into main. Do not skip this check — a conflicting draft branch requires a
different PR head.

## Step 10.5 — Detect merge conflict; switch to compose branch if needed

After pushing the draft branch, check whether it can be cleanly merged into `origin/main`.
A draft branch that accumulated many commits (e.g., 50+ commits from iterative sessions)
may have diverged from main via squash-merges, making it unmergeable.

The snippet below probes for modern `git merge-tree --write-tree` (git >= 2.38: the exit
code carries the result) and falls back to the deprecated 3-argument `merge-tree` on older
git. Do not "tighten" the fallback grep back to an anchored `^<<<<<<<`: old-style
merge-tree emits diff-style output whose conflict markers are `+`-prefixed
(`+<<<<<<< .our`), so the anchored form matches nothing and silently reports 0 conflicts
on a genuinely conflicting branch (engineering-journal PR #150, 2026-07-03; ADR-080).

```bash
git -C "$WT" fetch origin main
MERGE_BASE=$(git -C "$WT" merge-base HEAD origin/main)
if [ -z "$MERGE_BASE" ]; then
  echo "ERROR: could not compute merge base — inspect manually before opening PR"
  exit 1
fi
# Modern git (>= 2.38): --write-tree reports via exit code (0 = clean, 1 = conflicts).
RC=0
git -C "$WT" merge-tree --write-tree HEAD origin/main >/dev/null 2>&1 || RC=$?
if [ "$RC" -eq 0 ]; then
  CONFLICT_LINES=0
elif [ "$RC" -eq 1 ]; then
  CONFLICT_LINES=1
else
  # Old git (< 2.38) rejects --write-tree; fall back to 3-arg merge-tree, whose
  # diff-style output '+'-prefixes conflict markers — hence "^\+?<<<<<<<", never
  # a bare "^<<<<<<<" (ADR-080).
  CONFLICT_LINES=$(git -C "$WT" \
    merge-tree "$MERGE_BASE" HEAD origin/main | grep -cE "^\+?<<<<<<<" || true)
fi
echo "CONFLICT_LINES=$CONFLICT_LINES"
```

**If `CONFLICT_LINES` is 0** — no conflicts detected; proceed to Step 11 using the
draft branch as the PR head. Set `PR_HEAD=$SOURCE_BRANCH`.

**If `CONFLICT_LINES` > 0** — conflicts detected. Recover via a clean compose branch:

```bash
# Capture the compose worktree's current (detached) HEAD before switching it onto a new
# branch — this is the commit Step 10 just pushed. The worktree is detached, so there is no
# guarantee a local branch named $SOURCE_BRANCH exists to check out FROM; the worktree's
# own HEAD is always correct regardless.
PREV=$(git -C "$WT" rev-parse HEAD)

# 1. Move the compose worktree onto a clean branch from origin/main
git -C "$WT" checkout -b compose/YYYY-MM-DD origin/main

# 2. Cherry-pick only the composed output files from the pre-recovery commit.
#    Include the open-PR records if present — today's sessions may have updated them.
#    Per ADR-056 these are per-PR shards under open-prs/; a pre-ADR-056 day uses open-prs.jsonl.
git -C "$WT" checkout "$PREV" -- \
  sessions/<project>/YYYY-MM-DD-<slug>.md \
  sessions/<project>/README.md \
  README.md
[ -f "$WT/sessions/<project>/open-prs.jsonl" ] && \
  git -C "$WT" checkout "$PREV" -- sessions/<project>/open-prs.jsonl
[ -d "$WT/sessions/<project>/open-prs" ] && \
  git -C "$WT" checkout "$PREV" -- sessions/<project>/open-prs

# 2b. Re-apply this run's Step 9.5 reconciliation — REQUIRED, not optional. Empirically
#     confirmed: `git checkout <commit> -- <dir>` does NOT delete files present in the current
#     working tree but absent from <commit> — it only adds/updates what <commit> has. The
#     `checkout -b ... origin/main` above left `open-prs/` at origin/main's STALE state (still
#     containing shards Step 9.5 already verified-and-removed in $PREV, since those removals
#     haven't reached main yet); the `checkout "$PREV" -- open-prs` line just above does NOT
#     remove them — $PREV doesn't have them either, so nothing changes them either way. Without
#     this step, every reconciled shard from Step 9.5 silently reappears in the branch about to
#     be pushed. For every PR number this run's Step 9.5 reconciled for this project (you already
#     have this list from Step 9.5 — do not skip re-deriving it), remove it again explicitly:
for N in <the exact PR numbers this run's Step 9.5 removed for this project, space-separated>; do
  [ -f "$WT/sessions/<project>/open-prs/$N.json" ] && \
    git -C "$WT" rm --quiet -- "sessions/<project>/open-prs/$N.json"
done

# 3. Commit and push
git -C "$WT" commit -m \
  "[docs] Add YYYY-MM-DD journal: <slug> (compose branch — draft had conflicts)"
git -C "$WT" push -u origin compose/YYYY-MM-DD

# 4. Delete the remote source branch so the stale-branch hook does not fire on it
git -C "$WT" push origin --delete "$SOURCE_BRANCH" || true
```

Set `PR_HEAD=compose/YYYY-MM-DD`.

**Caution (pre-existing, not introduced by this change):** step 4 above deletes the remote
`$SOURCE_BRANCH` unconditionally. If a stub-writing session's worktree still holds that branch and
intends to push more stubs to it later the same day, this delete forces that session's next push
to recreate a branch with no composed journal in its history — this assumes no further stubs will
be written for the date (an end-of-day invariant). This is narrow enough to leave as a documented
limitation rather than redesign here; do not delete this branch as part of a same-day, mid-day
recovery unless you've confirmed no other session is actively writing stubs for the same date.

Tell the user: "Draft branch had merge conflicts with main — composed journal pushed to
`compose/YYYY-MM-DD` instead. Remote draft branch deleted. Opening PR from clean branch."

**Multi-project mode:** apply this check once (after the combined `git push` at the end
of Phase 2). If conflicts are detected, run the recovery for all projects' composed files
together on a single `compose/YYYY-MM-DD` branch before opening the combined PR. Example
for two projects `meta` and `lifting-logbook`:

```bash
PREV=$(git -C "$WT" rev-parse HEAD)
git -C "$WT" checkout -b compose/YYYY-MM-DD origin/main
git -C "$WT" checkout "$PREV" -- \
  sessions/meta/YYYY-MM-DD-<slug-a>.md \
  sessions/meta/README.md \
  sessions/lifting-logbook/YYYY-MM-DD-<slug-b>.md \
  sessions/lifting-logbook/README.md \
  README.md
# open-PR records per project (conditional) — legacy file and/or ADR-056 shard dir
for proj in meta lifting-logbook; do
  [ -f "$WT/sessions/$proj/open-prs.jsonl" ] && \
    git -C "$WT" checkout "$PREV" -- "sessions/$proj/open-prs.jsonl"
  [ -d "$WT/sessions/$proj/open-prs" ] && \
    git -C "$WT" checkout "$PREV" -- "sessions/$proj/open-prs"
done
# REQUIRED, not optional — see the single-project note above for why: the checkouts above
# cannot delete a shard that's absent from $PREV but present in origin/main's stale tree. For
# every PR number each project's own Step 9.5 reconciled, remove it again explicitly:
for proj in meta lifting-logbook; do
  for N in <the exact PR numbers this run's Step 9.5 removed for $proj, space-separated>; do
    [ -f "$WT/sessions/$proj/open-prs/$N.json" ] && \
      git -C "$WT" rm --quiet -- "sessions/$proj/open-prs/$N.json"
  done
done
git -C "$WT" commit -m \
  "[docs] Add YYYY-MM-DD journals: <slug-a>, <slug-b> (compose branch — draft had conflicts)"
git -C "$WT" push -u origin compose/YYYY-MM-DD
git -C "$WT" push origin --delete "$SOURCE_BRANCH" || true
```

## Step 11 — Open PR

Open the PR immediately using `gh`, using `PR_HEAD` determined in Step 10.5
(`$SOURCE_BRANCH` if clean, `compose/YYYY-MM-DD` if conflicts were detected).

Before composing the PR body, read `~/.claude/templates/pr-body.md` and use it as the
structural guide. This is a journal PR — use the "Journal PR" pattern from that file. Include
the `RECONCILED_SHARDS` list from Step 9.5 in the body (or "none" if empty), continuing the
precedent set by engineering-journal PR #150.

```bash
gh pr create \
  --repo brownm09/engineering-journal \
  --base main \
  --head <PR_HEAD> \
  --title "YYYY-MM-DD: <slug>" \
  --body "$(cat <<'EOF'
End-of-day journal: <one-line topic summary>.

Open-PR shards reconciled (verified merged/closed via gh before removal): <RECONCILED_SHARDS or "none">.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Disregard the `pr-merge-reminder` hook's stub/shard instructions for this PR.** The hook fires
its generic "write the journal stub AND open-PR shard" advice on this `gh pr create` the same as
any other — it has no way to know this create call *is* the journal-compose operation. This PR
opens and merges in the same session, so writing a shard for it would go stale the instant it
merges (the exact defect this change fixes). **Exception:** if the merge below fails and the PR
is left open, the PR now genuinely spans sessions — write `sessions/<project>/open-prs/<N>.json`
inside `$WT` (fields per the ADR-056 schema; `stub` = the composed journal's filename, or
`"journal-compose YYYY-MM-DD"` if no meta journal was composed), `git -C "$WT" add` +
`git -C "$WT" commit -- <shard>` + `git -C "$WT" push origin HEAD:refs/heads/<PR_HEAD>`, tell
the user the compose worktree is intentionally left in place until the PR resolves, and
**stop — do not remove the worktree.**

**Merge — two calls, not `--delete-branch`.** `gh pr merge --delete-branch`'s local-branch-delete
step fails outright if the branch is checked out as a *named* branch anywhere in the repo. The
compose worktree itself is detached and never at risk, but `draft/YYYY-MM-DD` can still be
checked out as a named branch by a stub-writing session's own worktree at the exact moment this
merge runs (per `claude/CLAUDE.md`'s stub workflow). Split the merge instead:

```bash
gh pr merge <PR-URL> --repo brownm09/engineering-journal --squash
gh pr view <PR-URL> --repo brownm09/engineering-journal --json state --jq .state   # expect MERGED
gh api -X DELETE "repos/brownm09/engineering-journal/git/refs/heads/<PR_HEAD>"
```

The squash-merge is server-side only and always succeeds regardless of any local checkout
state; the ref delete is a pure REST call, independent of what any worktree currently holds.
Confirm `MERGED` before deleting the ref — if the merge call itself failed, stop and diagnose
rather than deleting anything.

**Clean up, in this order — the worktree must go first, since a branch checked out in a
worktree cannot be deleted:**

```bash
# 1. Remove the compose worktree
git -C "$EJ" worktree remove "$WT" || \
  { echo "warning: worktree dirty — forcing removal"; git -C "$EJ" worktree remove --force "$WT"; }

# 2. THEN local branch cleanup
git -C "$EJ" branch -D "$SOURCE_BRANCH" 2>/dev/null || true    # may be held by a stub-session worktree — leave it if so
git -C "$EJ" branch -D compose/YYYY-MM-DD 2>/dev/null || true  # only exists after a Step 10.5 recovery
```

**Post-merge shard-leak check** — surfaces a leak without ever mutating the canonical to fix it.
This checks for a leak of the shards this run's Step 9.5 reconciled (and Step 10.5's "2b", if the
conflict-recovery path ran) — **not** a shard for the compose PR itself, which by design (see
"Disregard the pr-merge-reminder hook's..." above) never exists in the first place:

```bash
git -C "$EJ" fetch origin main
for N in <the exact PR numbers this run's Step 9.5 (and Step 10.5's re-application, if it ran)
          reconciled, space-separated>; do
  git -C "$EJ" ls-tree -r origin/main --name-only | grep -q "open-prs/$N.json" && \
    echo "WARNING: shard open-prs/$N.json landed back on origin/main despite reconciliation — remove it in a follow-up commit"
done
```

Tell the user: "Merged: <PR-URL>. Journal published." (plus the shard-leak warning above, if any).
