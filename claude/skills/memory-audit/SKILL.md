---
name: memory-audit
description: Reconcile agent memory against the version-controlled instructions and emit a table — per entry type, durable?, instruction home?, and a disposition (remain / promote / delete). Catches never-ported durables, stale notes, and index drift. Invoke as /memory-audit.
argument-hint: "(no arguments)"
allowed-tools: Read Grep Glob Bash AskUserQuestion Edit
---

You are auditing the active project's agent memory against the version-controlled
instructions, to surface what should **remain** as a cache, be **promoted** into the
instructions, or be **deleted** as stale. This is the audit-time complement to the
write-time rule in `claude/CLAUDE.md` § Durable Preferences & Memory and
[ADR-048](../../../docs/adr/048-memory-immortalization-issue-pairing.md) /
[ADR-038](../../../docs/adr/038-durable-preferences-documented-in-repo.md). It realizes
the reconciliation direction of [dev-env#363](https://github.com/brownm09/dev-env/issues/363).

The output is a **table for the user to review** — do not delete or promote anything
without confirmation (Step 5). Read-only by default.

## Step 0 — Locate the memory store

The active project's memory lives at
`~/.claude/projects/<project-dir>/memory/` with an index at `MEMORY.md`.
List the directory and read `MEMORY.md`:

```bash
ls -1 "C:/Users/brown/.claude/projects/<project-dir>/memory/"
```

(`<project-dir>` is the slugified project path Claude Code already uses for this
session — it is the directory whose `memory/` you were given at session start.)

## Step 1 — Read every memory file

For each `*.md` file other than `MEMORY.md`, read its frontmatter (`type:` —
`user` / `feedback` / `project` / `reference`) and body. Note any
"**Documented in repo (source of truth):**" line — the instruction home the
memory *claims*.

## Step 2 — Verify against CURRENT instructions, not a stale tree

> A memory is a point-in-time snapshot, and a worktree can be cut from an older
> `main`. Before trusting *or* doubting any claim, verify against current `origin/main`.

For the dev-env repo specifically:

```bash
git -C C:/Users/brown/Git/dev-env fetch origin --quiet
git -C C:/Users/brown/Git/dev-env log --oneline -1 origin/main
```

When a memory claims an instruction home (a `CLAUDE.md` section, an `ADR-NNN`,
a doc path), **confirm the cited file/section actually exists on `origin/main`**
(`git show origin/main:<path>` or grep the working tree only if it is on
`origin/main`). A claim that points at a non-existent ADR or section is **drift**,
not proof of immortalization. (This guard exists because the audit that created this
skill first produced a false "drift" finding from a stale worktree base.)

## Step 3 — Classify each entry

For each memory, determine three things:

1. **Durable?** A `user`/`feedback` rule, or a `project` entry encoding a
   cross-session rule/decision, is **durable**. Open-PR lists, in-flight working
   state, and other fast-changing notes are **transient** (exempt from the rule).
2. **Instruction home?** Does a real, current instruction (a `CLAUDE.md` section,
   project doc, or ADR) capture this durable rule? Confirm per Step 2.
3. **Drift / staleness signals:**
   - **Stale note** — cites PRs/issues now merged/closed, or a "next step" that has
     since shipped, or facts contradicted by current code.
   - **Never-ported durable** — durable, no instruction home, no tracking issue (or
     a *closed* issue whose work never landed). This is the forbidden memory-only
     state.
   - **Index drift** — the entry is missing from `MEMORY.md`, or its `MEMORY.md`
     line disagrees with the file it indexes.

## Step 4 — Emit the reconciliation table

Print one row per memory file:

```
| Memory file | Type | Durable? | Instruction home (verified) | Drift | Disposition |
|---|---|---|---|---|---|
```

Disposition is one of:

- **remain-as-cache** — durable and already immortalized in a current instruction;
  keep the memory as a recall aid. (Fix index drift if any.)
- **promote-to-instructions** — durable, no current instruction home → file an
  immortalization issue and port it into the appropriate `CLAUDE.md`/docs (per the
  write-time rule).
- **delete-stale** — transient-and-expired, or resolved/superseded; the repo (or
  engineering-journal `open-prs.jsonl`) is the authoritative source.

Also report any `MEMORY.md` index drift (missing entries, inconsistent lines).

## Step 5 — Confirm before acting

Present the table and ask the user which dispositions to apply (use
`AskUserQuestion` if there is more than one promote/delete decision). Then, only for
the confirmed items:

- **promote** → file the GitHub issue, port the rule into `CLAUDE.md`/docs, link the
  issue from the memory body and `MEMORY.md` (follow the write-time rule in
  `claude/CLAUDE.md`).
- **delete** → remove the memory file and its `MEMORY.md` line.
- **remain** → leave the file; only repair `MEMORY.md` index drift.

Memory files are machine-local (never committed); instruction edits and issues
follow the normal dev-env branch/PR workflow.

## Notes

- The write-time hook `memory-write-advisory.py` (ADR-048) catches *new* memory-only
  rules at the moment of writing; this skill catches the *backlog* and *drift* that
  accrue over time. They are complements.
- Keep the bar for **promote** at genuinely durable cross-session rules — the same
  bar the write-time rule uses. Do not promote transient context.
