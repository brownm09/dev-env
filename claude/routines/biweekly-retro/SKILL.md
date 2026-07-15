---
name: biweekly-retro
description: Every other Sunday, synthesize a retrospective from the trailing 4 weeks of engineering-journal entries across all projects (global readout + per-repo sections + tracked ratio) — and emit a committed report plus deduped action-item issues routed to the correct repo for each finding.
schedule: "0 9 * * 0"
# Sunday 09:00 LOCAL time (the scheduled-tasks scheduler evaluates cron in local time, not UTC).
# Weekly trigger; the ISO-week parity gate in Step 0.5 makes the effective cadence biweekly
# (runs only on EVEN ISO week numbers → every other Sunday).
---

Synthesize a biweekly engineering retrospective from the engineering-journal. Run **fully
autonomously** — never call `AskUserQuestion`, never wait for input, never prompt for approval.

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

**Objective:** Every other Sunday, read the trailing 4 weeks of composed daily journal entries
across all projects, synthesize a retrospective (what went well, where to push back, recommended
improvements, with a tracked process-to-product ratio), and produce durable artifacts: a committed
markdown report in the engineering-journal repo, and deduped action-item issues filed in the
**correct repo** for each finding (cross-cutting → dev-env). Report completion via push notification.

This automates the manual retrospective exercise; the cadence and 4-week window were chosen by the
user on 2026-06-09 (see dev-env#343).

---

## Step 0 — Sync the engineering-journal working tree

Read `~/.claude/skills/sync-routine-worktree/SKILL.md` and execute its Behavior section end-to-end
with these parameters:

- `REPO` = `C:/Users/brown/Git/engineering-journal`
- `VERIFY_FILE` = `sessions/meta/README.md`
- `PREFIX` = `biweekly-retro`

On **SUCCESS**, continue. On **ABORT**, exit cleanly — the push notification has already been sent;
do not commit, do not open a PR, do not create an issue.

---

## Step 0.5 — Biweekly parity gate

```bash
RUN_DATE=$(date +%Y-%m-%d)
ISO_WEEK=$(date +%V)        # zero-padded ISO 8601 week number, Monday-based
WEEK_NUM=$((10#$ISO_WEEK))  # force base-10 (avoid octal interpretation of e.g. "08")
```

If `WEEK_NUM` is **odd**, this is an off week. Send a push notification —
`biweekly-retro: off week (ISO week ${WEEK_NUM}), skipping` — and **exit cleanly with status 0**.
Do nothing else.

If `WEEK_NUM` is **even**, proceed. (Even-week anchor → every-other-Sunday cadence. Known minor
caveat: at a year boundary an ISO 52→1 or 53→1 transition can put two same-parity weeks adjacent,
nudging one cycle by a week. This is acceptable for a retrospective.)

---

## Step 1 — Define the analysis window

```bash
EJ="C:/Users/brown/Git/engineering-journal"
SCRATCH="C:/Users/brown/.claude/scratch"
WINDOW_START=$(date -d "${RUN_DATE} -28 days" +%Y-%m-%d)   # trailing 4 weeks, inclusive
mkdir -p "$SCRATCH"
```

The window is `[WINDOW_START, RUN_DATE]`. Composed journals are named `YYYY-MM-DD-<slug>.md` where
the leading 10 characters are the calendar date — compare that prefix against the window.

---

## Step 2 — Discover the journals in scope

Enumerate every project directory under `${EJ}/sessions/` (each subdirectory is a project, e.g.
`lifting-logbook`, `career-playbook`, `dev-env`, `meta`). For each project:

1. Read its `README.md` index first to orient (token efficiency — do not blind-glob large dirs).
2. Collect composed journal files whose leading `YYYY-MM-DD` falls within the window:

```bash
for proj in "$EJ"/sessions/*/; do
  name=$(basename "$proj")
  for f in "$proj"/20??-??-??-*.md; do
    [ -e "$f" ] || continue
    d=$(basename "$f" | cut -c1-10)
    if [[ "$d" > "$WINDOW_START" || "$d" == "$WINDOW_START" ]] && [[ ! "$d" > "$RUN_DATE" ]]; then
      echo "$name|$f"
    fi
  done
done
```

Exclude `README.md`, `*.stub.md`, and `*.manifest.jsonl` (the glob above already excludes them).

Build a per-project list of in-window journal paths. If **no** journals fall in the window across
all projects, send a push notification — `biweekly-retro: no journal activity in the trailing 4
weeks (${WINDOW_START}..${RUN_DATE}) — nothing to analyze` — and exit cleanly with status 0.

---

## Step 3 — Analyze each project in parallel

For each project that has in-window journals, spawn **one background subagent** (`Agent` tool,
`subagent_type: Explore`, `run_in_background: true`) in a **single message with all spawns together**
(no synchronous preflight agent). Give each a self-contained prompt naming the exact journal file
paths for that project and asking for a structured digest:

- Major work streams / features / fixes (with dates and PR numbers).
- Recurring themes and repeated problems.
- Incidents / recoveries (red main, reverts, repeated root-cause fixes).
- Rough feature-vs-process/infrastructure/firefighting balance.
- Signals of process friction or self-inflicted complexity.

If a subagent fails or returns nothing for a project, **do not abort the whole run** — note the gap
and continue with a partial report.

---

## Step 4 — Synthesize the retrospective (v2 structure)

Merge the per-project digests into one retrospective with **three parts**, each point anchored to
concrete dates / PR numbers / ADRs. This is the canonical structure — match it exactly:

**§1 — Global readout (patterns across repos).** The cross-cutting story: patterns that recur in
*multiple* repos (not per-project). Name strengths to keep and systemic problems. End with a
**Global action items** checklist — these route to dev-env in Step 6.

**§2 — Per-repo sections.** One subsection per repo *with activity in the window* (skip silent
repos). Each: a 2–4 line recap (work streams, friction, incidents) **and its own action-item
checklist** — these route to that repo in Step 6.

**§3 — Process-to-product ratio (tracked metric).** Estimate the share of effort on
process/tooling/automation vs. user-facing product. State the method (PR counts, themes) so runs are
comparable, and **show the trend** vs. the previous retro (read the prior `*-retro.md` file's §3 to
get the last value). A high ratio is fine if deliberate; a drifting one is the warning.

Tag each action item with the window(s) that surfaced it. Be specific and opinionated — the value is
in the meta-observations, not an exhaustive log.

---

## Step 5 — Write and PR the committed report

1. Ensure the retro folder exists; create a one-time README if missing:

```bash
RETRO_DIR="$EJ/sessions/meta/retro"
mkdir -p "$RETRO_DIR"
if [ ! -f "$RETRO_DIR/README.md" ]; then
  cat > "$RETRO_DIR/README.md" <<'EOF'
# Biweekly Retrospectives

Auto-generated by the `biweekly-retro` routine (dev-env `claude/routines/biweekly-retro/`).
Each `YYYY-MM-DD-retro.md` reflects on the trailing 4 weeks of engineering-journal activity:
§1 global cross-repo readout (+ global action items) → §2 per-repo sections (each with action
items) → §3 process-to-product ratio tracked over time. The routine files action-item issues in
the **correct repo** for each finding (cross-cutting → dev-env).
EOF
fi
```

2. Write the report to `${RETRO_DIR}/${RUN_DATE}-retro.md` in the **§1/§2/§3 structure from Step 4**
   (short title + the window it covers at the top).

3. Commit on a dedicated branch and open a PR to `main` (this repo squash-merges; do **not** commit
   to `main` directly, and do **not** auto-merge — auto-merge is disabled by ADR-031, the user
   reviews and merges):

```bash
cd "$EJ"
git checkout -b "retro/${RUN_DATE}" origin/main 2>/dev/null || git checkout "retro/${RUN_DATE}"
git add "sessions/meta/retro/${RUN_DATE}-retro.md" "sessions/meta/retro/README.md"
# This checkout is shared by every concurrent Claude Code session — the explicit `--` pathspec keeps
# this commit from sweeping in another session's already-staged files (see dev-env claude/CLAUDE.md →
# Engineering Journal → Stub file workflow → "Commit with an explicit pathspec").
git commit -m "[docs] Biweekly retro ${RUN_DATE} (trailing 4 weeks)" -- "sessions/meta/retro/${RUN_DATE}-retro.md" "sessions/meta/retro/README.md"
git push -u origin "retro/${RUN_DATE}"
```

4. Open the PR and capture its URL:

```bash
gh pr create --repo brownm09/engineering-journal \
  --base main --head "retro/${RUN_DATE}" \
  --title "Biweekly retro ${RUN_DATE}" \
  --body "Automated biweekly retrospective covering ${WINDOW_START}..${RUN_DATE}. Companion issue with the action checklist: <filled in Step 6>."
```

If any git/PR step fails, push-notify `biweekly-retro: report git/PR step failed — draft at
${RETRO_DIR}/${RUN_DATE}-retro.md` and continue to Step 6 if possible (the report file still exists
locally for recovery).

---

## Step 6 — File action-item issues in the correct repo (deduped)

File the action items **in the repo they belong to**, not all in one place. For each repo's §2
checklist, file one consolidated issue **in that repo**; the §1 global checklist goes to dev-env.

**Routing rule (where a finding's issue is filed):**
- **A per-repo §2 finding** → that repo's own tracker, when the repo has a GitHub remote with Issues
  enabled (lifting-logbook, career-playbook, dev-env, tech-leadership-reference, win11-init-tools,
  brownm09, job-search-agent, gas-lifting-logbook all qualify).
- **§1 global / cross-cutting findings** → **dev-env**.
- **engineering-journal / meta** findings → **dev-env** (engineering-journal declares no issue
  tracker by convention).
- **research-notes** (and any repo with no GitHub remote) → **dev-env**.

**Dedup guard (mandatory — keeps the biweekly cadence from re-filing the same items).** Before
filing in a repo, read its existing open `retro-action` issues and skip any finding already covered:

```bash
# existing open retro-action issue bodies for repo $R (empty if none / label absent)
gh issue list --repo "brownm09/${R}" --label retro-action --state open \
  --json number,title,body --jq '.[] | "#\(.number) \(.title)\n\(.body)"' 2>/dev/null
```

Only file findings **not** already represented there. Also skip findings already tracked by a
non-retro issue when the digest names that issue number (link it instead of re-filing). If a repo
has zero genuinely-new findings this run, **file nothing** for it.

**For each repo with new findings**, ensure the label exists then file the consolidated issue:

```bash
gh label create retro-action --repo "brownm09/${R}" --color 5319e7 \
  --description "Action item surfaced by a biweekly-retro run" 2>/dev/null || true
gh issue create --repo "brownm09/${R}" --label retro-action \
  --title "Biweekly retro ${RUN_DATE} — action items" \
  --body "$(cat <<EOF
Findings from the ${RUN_DATE} biweekly retrospective (window ${WINDOW_START}..${RUN_DATE}).
Report: <engineering-journal report PR URL from Step 5>

## Action items
- [ ] <finding> — windows: <Wx> — <1-line why>
- [ ] ...

_Generated by the \`biweekly-retro\` routine._
EOF
)"
```

The dev-env issue (global + meta + no-remote findings) follows the same shape with
`title "Biweekly retro ${RUN_DATE} — global / cross-cutting action items"`. Capture every issue URL.
Adding dev-env issues to the project board is left to the user (the routine runs without the
`project` gh scope).

---

## Step 7 — Report

Send a push notification summarizing the run:

```
biweekly-retro ${RUN_DATE} complete — <N> projects, <K> journals analyzed.
Process:product ≈ <ratio>. Top items: <2–3 headline improvements>.
Report PR: <url>   Issues filed: <repo#N, repo#N, ...>
```

Clean up any scratch files this run created.

---

## Constraints

- **Engineering-journal repo:** `C:/Users/brown/Git/engineering-journal`; sessions root `sessions/`,
  one subdirectory per project.
- **Window:** trailing 28 days, inclusive of `RUN_DATE`.
- **Cadence:** weekly Sunday trigger, even-ISO-week parity gate → biweekly.
- **Never** commit to `main`; open a PR. **Never** auto-merge (ADR-031). **Never** call
  `AskUserQuestion`.
- **Scratch dir:** `C:/Users/brown/.claude/scratch/` — all temp files; never `/tmp/`.
- **No `jq`** — use `node -e` if JSON parsing is needed.
- **Platform:** Windows 11, Git Bash syntax.
- This routine reads many journals and writes a reasoned synthesis — favor careful, opinionated
  analysis over breadth. The scheduler chooses the model; do the synthesis thoroughly regardless.
- **App-open caveat:** scheduled tasks run while the Claude app is open; if it was closed when the
  task was due, the run happens on next launch.

---

**Restorable live-copy imperative ([dev-env#703](https://github.com/brownm09/dev-env/issues/703) item 3, [dev-env#767](https://github.com/brownm09/dev-env/issues/767)).**
The execute-now / do-not-greet mitigation ([dev-env#698](https://github.com/brownm09/dev-env/issues/698))
is the **only** effective, model-agnostic guard against an autonomous scheduled run greeting instead of
executing — the frontmatter `model:` pin is confirmed **inert** (dev-env#703 item 2). It lives verbatim
only in the machine-local live copy (`~/.claude/scheduled-tasks/biweekly-retro/SKILL.md`, which is
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
