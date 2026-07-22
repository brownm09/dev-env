# ADR-116: Single-Source the Orphaned-Worktree Recovery Recipe (and Correct It)

**Date:** 2026-07-22
**Status:** Accepted
**Tags:** worktrees, orphan-recovery, hooks, pre-tool-use, shared-module, dry, documentation, parity-gate, enforcement, correction, error-message-diligence, adr-024, adr-066, adr-071, adr-114
**Issue:** [dev-env#862](https://github.com/brownm09/dev-env/issues/862)
**Supersedes (recipe only):** the recovery recipe named in [ADR-024](024-worktree-path-guard-hook.md)'s
2026-06-06 addendum and in [ADR-066](066-worktree-session-safety-rules.md) step 3

---

## Context

An **orphaned worktree** is a worktree-shaped directory git no longer resolves to itself — its `.git`
link file is missing, or the `gitdir:` target that link points at was pruned away. git from inside it
silently walks up and resolves to the **canonical** repo, so every write lands on the wrong tree.
[ADR-024](024-worktree-path-guard-hook.md)'s liveness guard (dev-env#328) converts that silent
corruption into a hard block: `pre-tool-use-worktree-path-check.py` exits 2 and prints a recovery recipe.

That message is not documentation. It is the **only** instruction the session can act on: the hook blocks
`Write`, `Edit`, and `NotebookEdit` from that cwd, so the session cannot edit its way out and has nothing
else to go on. A wrong recipe there is not a stale doc — it is a dead end at the worst moment.

The recipe existed in **three** hand-maintained copies: the hook's inline message, the
`docs/REFERENCE.md` "Worktree deregistration recovery" runbook, and the REFERENCE.md hooks-table row.
They drifted, exactly as duplicated operational knowledge does:

- [dev-env#751](https://github.com/brownm09/dev-env/issues/751) established that `git worktree add
  --force` does **not** work when the orphan directory still has content, and corrected the **runbook**.
- It never touched the **hook message** or the table row. The disproven recipe therefore stayed live on
  the highest-stakes surface for another six weeks, and was hit again on 2026-07-22 while recovering an
  orphaned worktree in career-playbook (career-playbook #823 / PR #826) — [dev-env#862](https://github.com/brownm09/dev-env/issues/862).

dev-env#862 also reported a second gap #751's proposed remedy did not cover: its `rm -rf` step fails when
the orphan is the blocked session's **own cwd** (`Device or resource busy`), which is the common case.

## Decision

**One definition, rendered everywhere, mechanically pinned.**

1. `claude/scripts/_worktree_recovery.py` holds the recipe as data — `RECOVERY_STEPS`, an ordered tuple
   of `RecoveryStep(command, note)` using `<canonical>` / `<orphan>` / `<branch>` placeholders — plus
   `recovery_commands()` and `recovery_recipe()`. Pure, no I/O, ASCII-only (the reason crosses Claude
   Code's cp1252 exit-2 stderr pipe; hook authoring rules 4/5). Same shape and import convention as
   `_worktree_canon` / `_repo_target` / `_hookout`, and like them it gets **no** hooks/utilities table
   row — library modules are documented in prose next to their consumer.
2. `pre-tool-use-worktree-path-check.py` renders that module instead of carrying its own copy.
3. The REFERENCE.md runbook is **pinned** to the module by `tests/test_worktree_recovery.py` (Testing
   item 78): the runbook fence's *runnable* lines must **equal** `RECOVERY_STEPS`' commands, in order,
   modulo a named `RUNBOOK_ONLY_COMMANDS` allowlist. The hooks-table row now points at the runbook
   rather than restating commands.
4. Reintroduction of the disproven form is blocked by two complementary passes, neither claimed to be
   exhaustive: an **AST pass** over `claude/scripts/*.py` non-docstring string literals (which is what
   distinguishes a *prescription* from the *explanations* this ADR, the module, and the hook all
   contain), and a **text pass** over the runnable fenced lines of the live docs, skills, and routines
   (which covers the surfaces the AST pass cannot reach). Known gaps, stated rather than papered over:
   the AST pass does not see `+`/`%`/`.format()`/f-string-variable/`bytes`/`join()` constructions, and
   neither pass scans `claude/scripts/tests/` — a test asserting the form is *absent* necessarily
   contains it. `docs/adr/` is exempt from both by design.

**And the recipe itself is corrected**, re-derived from evidence rather than inherited (below).

### The recipe

Work top to bottom; stop as soon as the worktree is live again.

| # | Command | Why |
|---|---|---|
| 1 | `git -C "<canonical>" worktree repair "<orphan>"` | Non-destructive, **preserves uncommitted work** — always try first |
| 2 | `git -C "<orphan>" rev-parse --show-toplevel` | Verification, not a fix — **the only reliable signal**; prints the worktree path ⇒ recovered |
| 3 | `git -C "<canonical>" worktree prune` | Clears the stale registration |
| 4 | `git -C "<canonical>" worktree add "<orphan>" <branch>` | Plain add — **not** `--force`/`-f` |
| — | `cp -r "<orphan>" "<orphan>.salvage"` | **Conditional trailer**, only if step 4 says `already exists`: capture before destroying |
| — | `find "<orphan>" -mindepth 1 -delete` | **Irreversible**; empties **in place**, then repeat step 4 |

The last two are deliberately **not** numbered. Rendering a destructive step inside a list introduced
by "run in order" invites a stressed reader to execute it unconditionally, which destroys exactly the
uncommitted work step 1 exists to preserve. `recovery_recipe()` emits them in a separate trailer, and
the invariant is pinned on a `destructive` flag rather than list position.

### Evidence (git 2.37.1.windows.1, throwaway fixtures, 2026-07-22)

The whole point of this ADR is that an unverified recipe here costs real recovery time, so every claim
above was run against real repos rather than reasoned about:

| Scenario | Result |
|---|---|
| admin dir survives, `.git` link deleted → `worktree repair <path>` | **Recovers**, uncommitted files preserved, worktree back on its branch — **but exits 1** printing `error: unable to locate repository; .git file broken` |
| `.git` link survives, admin dir deleted → `worktree repair <path>` | **Exits 1**, printing `error: unable to locate repository; .git file does not reference a repository`; repairs nothing (the *bare* `git worktree repair`, a different command, exits 0 here and also repairs nothing) |
| both sides gone → `worktree repair <path>` | Cannot repair — nothing left to relink; **exits 1 with the byte-identical `.git file broken` message the SUCCESS case prints** |
| empty dir, still registered → plain `add` | `fatal: … missing but already registered worktree` |
| empty dir, still registered → `add --force` | **Succeeds** — the narrow case that made the old recipe look right |
| **non-empty** dir → plain `add` | `fatal: '<path>' already exists` |
| **non-empty** dir → `add --force` / `add -f` | `fatal: '<path>' already exists` — identical; the flag is irrelevant here |
| non-empty → `prune` → empty in place → plain `add` | **Succeeds**, branch restored |
| `find <dir> -mindepth 1 -delete` with cwd == dir | Succeeds; the directory itself survives so the shell keeps a valid cwd |
| `rev-parse --show-toplevel` on a **sibling-convention** orphan | `fatal: not a git repository (or any of the parent directories): .git` — outside any repo to walk up to |
| `rev-parse --show-toplevel`, admin dir deleted | `fatal: not a git repository: <canonical>/.git/worktrees/<name>` |
| `find … -delete` in **PowerShell** | `find` resolves to `C:\windows\system32\find.exe`; `FIND: Parameter format not correct`, exit 2, nothing deleted |

Four findings are load-bearing and none of them were in the prior guidance:

- **`worktree repair`'s exit code and message cannot tell success from failure.** It exits 1 on
  success *and* on both failure shapes, and the both-sides-gone failure prints the **byte-identical**
  `.git file broken` text the success case does. So there is no diagnostic in step 1 at all — which is
  precisely why step 2 is a separate, mandatory verification rather than a courtesy. This is the global
  `CLAUDE.md` **Error Message Diligence** rule in its sharpest form: the same words understate success
  in one case and overstate it in another. (An earlier draft of this ADR asserted the admin-dir-deleted
  shape "exits 0 and does nothing" — that is true only of the *bare* `git worktree repair`, not of the
  path-argument form this recipe actually ships. Corrected after review.)
- **Step 1 cannot recover the both-sides-gone shape**, so in that shape the destructive step is the
  *only* forward path and the orphan's uncommitted work exists nowhere else. That is what makes the
  salvage copy mandatory rather than decorative — and it is where the superseded runbook's
  inspect-before-you-delete gate, which an earlier draft of this PR dropped, actually earned its place
  (global `CLAUDE.md` → Code Quality → *Back up before you mutate*).
- **Step 2 has a third outcome.** A sibling-convention orphan (`<repo>-worktrees/<name>`, dev-env#760)
  sits outside any repo, so `rev-parse` errors rather than printing either expected path. The note now
  reads "anything else → continue" instead of enumerating two outcomes.
- **`--force` is irrelevant to a non-empty target.** git evaluates `file_exists(path) &&
  !is_empty_dir(path)` and dies *before* consulting `--force`; the flag overrides only the
  stale-registration and branch-checked-out-elsewhere safeguards. This both confirms #751 and explains
  why the original recipe was ever written: it genuinely fixes the **empty**-but-registered case.
- **dev-env#862's own report is partly corrected here.** It states that after `prune`, a plain `add`
  "tolerates the leftover junk". It does not — it dies `already exists` exactly like `--force`. The
  directory must be emptied (step 5). The issue is right about the *conclusion* (`--force` is wrong,
  `rm -rf` is wrong) and wrong about the *mechanism*, so the recipe encodes the verified mechanism.

## Judgment calls

**Correct the recipe, not just the plumbing.** Single-sourcing a wrong recipe would have shipped the same
dead end from one place instead of three. The Step-0 matrix ran before any text was written.

**`worktree repair` first, destructive step last.** The prior recipe went straight to re-creating the
worktree, silently discarding uncommitted work in the orphan. `repair` recovers it intact whenever the
admin dir survived. The ordering is pinned by a test, because a future reorder would cost data silently.

**Empty in place; never `rm -rf` the directory.** Removal was observed to succeed in a clean subshell, so
`Device or resource busy` is handle-dependent rather than universal — but emptying in place works in
*both* cases and keeps the shell's cwd valid, so there is no scenario where removal is the better
instruction. The recipe states the one that always works.

**No `git checkout main` step.** The old runbook opened with it "to free the branch". `prune` already
frees it, and that command is now hard-blocked by `pre-tool-use-canonical-mutate-guard.py`
([ADR-071](071-canonical-checkout-mutate-guard-hook.md)) as a `-C` redirect of a mutating verb at a
canonical root — so the old runbook's first line could not be followed as written. `repair` / `prune` /
`add` are not blocked; the sequence needs no `ALLOW_CANONICAL_MUTATE=1` override.

**The anti-regression gate is AST-based, not grep-based.** A plain text scan flags the very prose that
*warns* against `--force` — this ADR, the module docstring, and the hook comment all discuss it at
length. The gate inspects non-docstring string literals (comments never reach the AST), so it catches a
*prescription* and ignores an *explanation*. `docs/adr/` is exempt entirely: ADR-024's addendum
legitimately records what was believed correct in June, and rewriting history to satisfy a lint would
destroy the record this ADR depends on.

**Parity gate over a doc, following ADR-114.** The two-file `## Testing` split established the pattern
(item 76): where one fact must live in two files, a cheap offline test asserts they agree. During
authoring the section scanner was fence-blind and matched the runbook's own `# 1. …` shell comments as
headings, truncating the section to an empty code block — the parity check passed **vacuously**. It was
caught by explicitly printing what the gate compared, and the scanner is now fence-aware. A parity gate
that can pass on nothing is worse than none, so item 78's entry in `docs/TESTING.md` flags `_section()`
as safety-critical.

## Consequences

- The hook message and the runbook can no longer disagree **about the commands they both carry**: one
  is rendered from `RECOVERY_STEPS`, the other is pinned to it by equality over runnable lines. The
  hooks-table row no longer restates commands at all. The gate is not a proof of global consistency —
  `RUNBOOK_ONLY_COMMANDS` is an explicit, named escape hatch (`npm install` today), and prose in either
  surface is unconstrained.
- A blocked session gets a recipe that works from inside the orphan, tries the work-preserving step
  first, and warns about the exit-1-on-success trap it would otherwise misread.
- Changing the recipe means changing `_worktree_recovery.py` and the runbook in the same PR — item 78
  fails otherwise. That is the intended cost.
- The hook gains one pure module-level import. Its declared fail direction is **unchanged**: it blocks
  with exit 2 but fails **open** on its own crash (`except Exception: sys.exit(0)`), per hook authoring
  rule 5 and `test_hook_safe_exit_guard.py`'s `FAIL_CLOSED` set, which lists only
  `pre-auto-merge-checkpoint-gate.py` and `pre-tool-use-journal-compose-force-guard.py`.

## References

- [dev-env#862](https://github.com/brownm09/dev-env/issues/862) — this issue
- [dev-env#751](https://github.com/brownm09/dev-env/issues/751) — disproved `--force`; fixed the runbook only
- [dev-env#328](https://github.com/brownm09/dev-env/issues/328) — the original orphaned-worktree incident
- [ADR-024](024-worktree-path-guard-hook.md) — the guard hook and its liveness addendum
- [ADR-066](066-worktree-session-safety-rules.md) — worktree session safety rules and the runbook's home
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — why `git -C <canonical> checkout` is blocked
- [ADR-114](114-slim-testing-section-index.md) — the two-file parity-gate precedent
- [git `worktree` documentation](https://git-scm.com/docs/git-worktree) — `add`, `prune`, `repair` semantics
