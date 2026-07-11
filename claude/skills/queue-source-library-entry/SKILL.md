---
name: queue-source-library-entry
description: Append a new entry to the shared source library (claude/skills/sources.md) through a dedicated dev-env worktree, never through the canonical checkout. Invoked by /research and /journal-compose when their source-library cache misses and a subagent finds a new citation.
allowed-tools: Read Edit Bash
---

Append one new entry to `claude/skills/sources.md` without ever writing through
`~/.claude/skills/sources.md` — a directory junction that always resolves to the canonical
dev-env checkout's working tree, regardless of the invoking session's own repo or `cwd`.

This skill is invoked **by other skills** (`/research`, `/journal-compose`), not directly by
users. It is a building block, not an end-user command.

---

## Why this exists

`~/.claude/skills/` is a directory junction onto `C:/Users/brown/Git/dev-env/claude/skills/`
(dev-env's own `CLAUDE.md` → Dev-Env Architecture). Any session — regardless of which project
it's actually working in — that writes to `~/.claude/skills/sources.md` is really writing into
the **canonical dev-env checkout's working tree**. Nothing then commits that write, so the
canonical checkout goes dirty and `dev-env-sync.py`'s `git pull --ff-only` refuses to
fast-forward for **every session on the machine** until someone notices and manually commits or
discards the change. This happened twice — [PR #649](https://github.com/brownm09/dev-env/pull/649)
and [issue #697](https://github.com/brownm09/dev-env/issues/697) — both traced to a
research-subagent-style write to this exact file. See
[ADR-102](https://github.com/brownm09/dev-env/blob/main/docs/adr/102-source-library-writes-through-worktree.md).

The fix mirrors the same "isolate mutations into a worktree" principle
[ADR-082](https://github.com/brownm09/dev-env/blob/main/docs/adr/082-journal-compose-worktree-isolation.md)
already applies to journal-compose's own writes: never touch the canonical checkout's working
tree, isolate into a dedicated worktree instead.

---

## Parameters (the invoking skill supplies these)

| Name | Required | Description | Example |
|---|---|---|---|
| `SECTION` | yes | The `##` heading this entry belongs under — **copy it verbatim from the invoking skill's own Pass-1 grep output**, don't reconstruct it from memory. Step 7 matches case- and whitespace-insensitively before deciding no section fits, so a near-miss attaches to the existing section instead of fragmenting the file. | `Testing` |
| `ENTRY_MARKDOWN` | yes | The fully-formatted entry, exactly as it should appear in the file, matching the format already used throughout `sources.md` (a `- **Title** \| Author/Org \| URL \|` line followed by an indented one-sentence relevance note on the next line). Contains arbitrary web-sourced text (titles, URLs) — treat as **untrusted content**; see Step 7's fallback for how it must be handled. | see `sources.md` for the exact two-line shape |
| `CALLER` | yes | **Must be a fixed literal from this document — `research` or `journal-compose` — never a dynamically constructed or web-sourced string.** Folded into the commit message for traceability. | `research` or `journal-compose` |

---

## Behavior

1. **Fixed locations** (do not vary these across invocations — every caller must converge on the
   same worktree and branch, or entries scatter across places nobody will think to look):
   ```bash
   DEV_ENV_REPO="C:/Users/brown/Git/dev-env"
   QUEUE_WORKTREE="C:/Users/brown/Git/dev-env/.claude/worktrees/research-sources-queue"
   QUEUE_BRANCH="chore/research-sources-queue"
   ```

2. **Fetch, always, first** — a single call using the remote's default refspec so deleted
   branches are actually pruned locally (a narrower `fetch origin <one-branch> --prune` does
   **not** prune anything when that one branch no longer exists remotely — the fetch itself just
   fails):
   ```bash
   git -C "$DEV_ENV_REPO" fetch origin --prune
   ```

3. **Check whether the queue branch has already been swept** — this must run even when the
   worktree looks perfectly live (a worktree can be fully connected and healthy while its branch
   is simultaneously stale, once a human has merged it elsewhere), so it runs *before* the
   liveness check in Step 4, not conditioned on it. Squash-merge (this repo's standard convention)
   makes an ancestry check (`merge-base --is-ancestor`) useless here — the squashed commit on
   `main` is never a descendant of the original branch — so detect the sweep via the
   push-then-delete signature instead: a local branch that has upstream tracking configured (Step
   10 always pushes with `-u`, so tracking is set the moment a push has ever succeeded) whose
   tracked remote ref no longer exists after the fetch above:
   ```bash
   if git -C "$DEV_ENV_REPO" show-ref --verify --quiet "refs/heads/$QUEUE_BRANCH"; then
     UPSTREAM=$(git -C "$DEV_ENV_REPO" for-each-ref --format='%(upstream:short)' "refs/heads/$QUEUE_BRANCH")
     if [ -n "$UPSTREAM" ] && ! git -C "$DEV_ENV_REPO" show-ref --verify --quiet "refs/remotes/origin/$QUEUE_BRANCH"; then
       git -C "$DEV_ENV_REPO" worktree remove --force "$QUEUE_WORKTREE" 2>/dev/null
       git -C "$DEV_ENV_REPO" branch -D "$QUEUE_BRANCH"
     fi
   fi
   ```
   A local branch with **no** upstream configured was never successfully pushed at all (Step 10's
   best-effort push failed every time) — leave it alone; deleting it here would destroy the only
   copy of not-yet-durable queued entries.

4. **Check whether the queue worktree is live** — both conditions, not just the first (Step 3
   above may have just removed it, in which case this is trivially "not live"):
   ```bash
   git -C "$QUEUE_WORKTREE" rev-parse --show-toplevel
   ```
   - If `${QUEUE_WORKTREE}/.git` exists **and** the command above succeeds, the worktree is live
     — skip to Step 6 and reuse it as-is.
   - Otherwise (missing entirely, just removed by Step 3, or a disconnected/orphaned remnant —
     the `.git` link present but its administrative record pruned elsewhere, or the reverse — the
     exact failure shape
     [ADR-024's addendum](https://github.com/brownm09/dev-env/blob/main/docs/adr/024-worktree-path-guard-hook.md)
     documents), clear any remnant so Step 5 always starts clean:
     ```bash
     git -C "$DEV_ENV_REPO" worktree remove --force "$QUEUE_WORKTREE" 2>/dev/null
     rm -rf "$QUEUE_WORKTREE"
     ```
     Continue to Step 5.

5. **Create the queue worktree** — **check the local branch first**. A `-b` against an
   already-existing local branch hard-fails, and the local branch is exactly what persists across
   worktree removals (`git -C "$DEV_ENV_REPO" worktree prune` clears only the worktree
   registration, never the branch ref itself — the ordinary state from the second invocation
   onward):
   ```bash
   git -C "$DEV_ENV_REPO" worktree prune
   if git -C "$DEV_ENV_REPO" show-ref --verify --quiet "refs/heads/$QUEUE_BRANCH"; then
     git -C "$DEV_ENV_REPO" worktree add "$QUEUE_WORKTREE" "$QUEUE_BRANCH"
   elif git -C "$DEV_ENV_REPO" show-ref --verify --quiet "refs/remotes/origin/$QUEUE_BRANCH"; then
     git -C "$DEV_ENV_REPO" worktree add "$QUEUE_WORKTREE" -b "$QUEUE_BRANCH" "origin/$QUEUE_BRANCH"
   else
     git -C "$DEV_ENV_REPO" worktree add "$QUEUE_WORKTREE" -b "$QUEUE_BRANCH" origin/main
   fi
   ```
   A `worktree add` failure here that reports the path or branch as already existing means a
   concurrent invocation won the race — treat it as the Step 8 collision case (re-run this
   Behavior section once from Step 2). Any other failure (disk, permissions, a genuine git error):
   stop and report the failure as part of this skill's own output — **do not** fall back to
   writing `~/.claude/skills/sources.md` directly. That fallback is the exact bug this skill
   exists to close.

   Never `cd` into `$QUEUE_WORKTREE` — always address it via `-C` or an absolute path, from
   whatever `cwd` the invoking skill is already running in (per dev-env `CLAUDE.md`'s
   `EnterWorktree`-targets-primary-repo-only guidance: this worktree is very often being created
   from a session whose primary repo isn't dev-env at all).

6. **Second dedup pass.** Grep `${QUEUE_WORKTREE}/claude/skills/sources.md` — the queue
   worktree's own copy, not `~/.claude/skills/sources.md` — for `ENTRY_MARKDOWN`'s title/URL. The
   invoking skill's own Pass-1 dedup only ever sees the canonical, merged content on
   `origin/main`; it structurally cannot see an entry already queued-but-unmerged by an earlier
   invocation on this same branch. If a match is found, skip to Step 11 and return SUCCESS with a
   note that the entry was already queued — do not create a duplicate commit.

7. **Read and insert.** Read `${QUEUE_WORKTREE}/claude/skills/sources.md` — this absolute,
   worktree-rooted path, **never** `~/.claude/skills/sources.md`. Insert `ENTRY_MARKDOWN` under
   the `## ${SECTION}` heading (matched case- and whitespace-insensitively), immediately before
   the next `##` heading or end-of-file if `${SECTION}` is the last section. If no matching
   heading exists, append a new `## ${SECTION}` heading at the end of the file followed by
   `ENTRY_MARKDOWN`.
   - Use the `Edit` tool with the worktree's absolute file path.
   - **If `Edit`/`Write` is blocked** (only when the invoking session's own `cwd` is itself a
     *different* dev-env worktree — `pre-tool-use-worktree-path-check.py` blocks any absolute
     path outside that session's own worktree root, including a sibling worktree like this one):
     fall back to a `Bash`-driven append. `ENTRY_MARKDOWN` is untrusted, web-sourced content — it
     **must** be passed via an environment variable, never interpolated into the interpreter's
     code string, or a crafted title/note containing quotes, backticks, or `$(...)` becomes
     command injection:
     ```bash
     SOURCES_FILE="$QUEUE_WORKTREE/claude/skills/sources.md" \
     ENTRY_SECTION="$SECTION" \
     ENTRY_TEXT="$ENTRY_MARKDOWN" \
     node -e '
       const fs = require("fs");
       const path = process.env.SOURCES_FILE;
       const heading = "## " + process.env.ENTRY_SECTION;
       const entry = process.env.ENTRY_TEXT;
       let content = fs.readFileSync(path, "utf8");
       const idx = content.toLowerCase().indexOf(heading.toLowerCase());
       if (idx === -1) {
         content = content.replace(/\s*$/, "") + "\n\n" + heading + "\n\n" + entry + "\n";
       } else {
         const nextIdx = content.indexOf("\n## ", idx + heading.length);
         const insertAt = nextIdx === -1 ? content.length : nextIdx + 1;
         content = content.slice(0, insertAt) + entry + "\n" + content.slice(insertAt);
       }
       fs.writeFileSync(path, content);
     '
     ```
     Never build an `-e`/`sed` expression by string-concatenating `ENTRY_MARKDOWN` or `SECTION`
     into it — the snippet above is the only sanctioned form of this fallback.

8. **Commit inside the worktree** (never the canonical checkout), with an explicit pathspec — a
   bare `git commit` on this shared, multi-caller worktree can sweep in whatever a concurrent
   invocation has staged, exactly the collision this step's own retry guidance exists for:
   ```bash
   git -C "$QUEUE_WORKTREE" add claude/skills/sources.md
   git -C "$QUEUE_WORKTREE" commit -m "chore: queue source library entry (via ${CALLER})" -- claude/skills/sources.md
   ```
   If this fails because of a concurrent invocation racing the same worktree (an `index.lock`
   error, or "nothing to commit" when Step 7's edit didn't actually change anything because
   another invocation already committed an equivalent entry first): re-run this Behavior section
   once from Step 2. A second failure of the same kind: stop and return FAILURE with the reason —
   do not retry indefinitely, and do not report SUCCESS for an entry that never committed.

9. **Verify the commit landed** before declaring success — Steps 7–8 have no other checkpoint,
   and a silent no-op earlier must not be reported as queued:
   ```bash
   git -C "$QUEUE_WORKTREE" log -1 --oneline
   ```
   Confirm the message matches what Step 8 just committed. If it does not, treat this as a
   FAILURE per Step 8's collision handling above, not a SUCCESS.

10. **Push for durability** — best-effort. A push failure here (e.g. a transient network issue) is
    not fatal to the invoking skill's primary task; the commit is already safely isolated in the
    worktree either way, off the canonical checkout:
    ```bash
    git -C "$QUEUE_WORKTREE" push -u origin "$QUEUE_BRANCH"
    ```

11. **Return.**
    - **SUCCESS** (Step 9 verified the commit, or Step 6 found the entry already queued): a
      one-line summary for the invoking skill to relay to the user, e.g.:
      > Queued "\<Title\>" to the source library under `${SECTION}` — committed to
      > `chore/research-sources-queue` in a dev-env worktree (not yet merged to `main`).
    - **FAILURE** (worktree creation failed for a non-collision reason, or the Step 8 collision
      retry failed twice): a one-line summary naming the failure. The invoking skill reports this
      to the user and does **not** fall back to writing `~/.claude/skills/sources.md` directly.

---

## Return semantics

- **SUCCESS** — the entry is committed (and, best-effort, pushed) on `chore/research-sources-queue`
  in the dedicated queue worktree, verified via Step 9 (or already present per Step 6). `main` and
  the canonical checkout's working tree are never touched.
- **FAILURE** — the invoking skill reports the failure and does **not** fall back to writing
  `~/.claude/skills/sources.md` directly.

---

## Scope boundary

This skill isolates, dedups against, and commits one entry. It does **not**:

- **Open a pull request.** The queue branch accumulates entries across invocations; a human
  sweeps it into a PR when convenient — the same manual pattern already used twice (PR #649,
  issue #697) before this skill existed.
- **Rebase the queue worktree against `origin/main` on every call** — only at creation time (and
  Step 3's swept-branch check, which recreates rather than reuses once a prior sweep has merged).
  A long-lived, not-yet-swept queue branch may still need conflict resolution when it's finally
  swept into a PR; that is the human doing the sweep's job, not this skill's.
- **Guard against every concurrent-invocation shape.** Step 8's single retry covers the common
  lock/no-op collision; a sustained pile-up of simultaneous invocations is not specially handled
  beyond that.
