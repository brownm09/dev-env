---
name: biweekly-retro
description: Every other Sunday, synthesize a retrospective from the trailing 4 weeks of engineering-journal entries across all projects — patterns, friction, and recommended improvements — and emit a committed report plus an actionable GitHub issue.
schedule: "0 9 * * 0"
# Sunday 09:00 LOCAL time (the scheduled-tasks scheduler evaluates cron in local time, not UTC).
# Weekly trigger; the ISO-week parity gate in Step 0.5 makes the effective cadence biweekly
# (runs only on EVEN ISO week numbers → every other Sunday).
---

Synthesize a biweekly engineering retrospective from the engineering-journal. Run **fully
autonomously** — never call `AskUserQuestion`, never wait for input, never prompt for approval.

**Objective:** Every other Sunday, read the trailing 4 weeks of composed daily journal entries
across all projects, synthesize a retrospective (what went well, where to push back, recommended
improvements, with a tracked process-to-product ratio), and produce two durable artifacts: a
committed markdown report in the engineering-journal repo, and an actionable GitHub issue in the
dev-env repo. Report completion via push notification.

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

## Step 4 — Synthesize the retrospective

Merge the per-project digests into one retrospective with these sections, each point anchored to
concrete dates / PR numbers / ADRs:

1. **What the period looked like** — the arc of work across projects.
2. **What went well (keep doing)** — durable strengths worth reinforcing.
3. **Where to push back** — friction, self-inflicted complexity, recurring paper cuts, gaps.
4. **Recommended improvements** — a short, concrete, actionable list (this becomes the issue
   checklist in Step 6).
5. **Process-to-product ratio** — estimate the share of effort spent on process/tooling/automation
   vs. user-facing product, as a single tracked metric so trends are visible across runs. State the
   method (PR counts, themes) so successive runs are comparable.

Be specific and opinionated — the value is in the meta-observations, not an exhaustive log.

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
Each `YYYY-MM-DD-retro.md` reflects on the trailing 4 weeks of engineering-journal activity
across all projects: what went well, where to push back, recommended improvements, and the
process-to-product ratio tracked over time. The routine also opens a companion dev-env issue
with the run's recommended-improvement checklist.
EOF
fi
```

2. Write the report to `${RETRO_DIR}/${RUN_DATE}-retro.md` (front it with a short title and the
   window it covers).

3. Commit on a dedicated branch and open a PR to `main` (this repo squash-merges; do **not** commit
   to `main` directly, and do **not** auto-merge — auto-merge is disabled by ADR-031, the user
   reviews and merges):

```bash
cd "$EJ"
git checkout -b "retro/${RUN_DATE}" origin/main 2>/dev/null || git checkout "retro/${RUN_DATE}"
git add "sessions/meta/retro/${RUN_DATE}-retro.md" "sessions/meta/retro/README.md"
git commit -m "[docs] Biweekly retro ${RUN_DATE} (trailing 4 weeks)"
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

## Step 6 — Open the actionable issue (dev-env)

The engineering-journal repo has **no issue tracker** — process-improvement issues live in dev-env.
Create the issue from the **Recommended improvements** list (Step 4 item 4) as a checkbox list:

```bash
gh issue create --repo brownm09/dev-env \
  --title "Biweekly retro ${RUN_DATE} — action items" \
  --body "$(cat <<EOF
Recommended improvements from the ${RUN_DATE} biweekly retrospective (window ${WINDOW_START}..${RUN_DATE}).

Report: <engineering-journal report PR URL from Step 5>

## Action items
- [ ] <improvement 1>
- [ ] <improvement 2>
- [ ] ...

_Generated by the \`biweekly-retro\` routine._
EOF
)"
```

Capture the issue URL. (Adding it to the Dev Env project board is left to the user — the routine
runs without the \`project\` gh scope.)

---

## Step 7 — Report

Send a push notification summarizing the run:

```
biweekly-retro ${RUN_DATE} complete — <N> projects, <K> journals analyzed.
Process:product ≈ <ratio>. Top items: <2–3 headline improvements>.
Report PR: <url>   Issue: <url>
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
```
