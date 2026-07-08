# dev-env — Project Instructions

The global Claude Code configuration lives in [`claude/CLAUDE.md`](claude/CLAUDE.md),
symlinked to `~/.claude/CLAUDE.md`. All workflow rules, hook invariants, model selection
guidelines, and journal conventions are defined there and apply to every project.

## Reference Documentation

| Doc | Purpose |
|---|---|
| [README.md](README.md) | Quick-reference tables for skills, hooks, and routines |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Detailed descriptions, invocation syntax, config options, and ADR links |
| [docs/adr/](docs/adr/) | Design decisions behind rules in `claude/CLAUDE.md` |

## Testing

This is the canonical, complete set of dev-env verification commands. The global "Test
before PR" rule in [`claude/CLAUDE.md`](claude/CLAUDE.md) defers to this section.

Item numbers below (and `docs/adr/INDEX.md`'s ADR numbers) are checked for collisions against
`origin/main` immediately before every `gh pr merge`, by `pre-merge-numbering-check.py`
([ADR-074](docs/adr/074-pre-merge-numbering-collision-check.md); dev-env#516) — concurrent PRs
routinely pick the same "next number" from a stale snapshot, and the collision is only ever
visible at merge time. If blocked, `git fetch origin main && git rebase origin/main`, renumber
the colliding item(s) to the next free number, and re-run `gh pr merge`.

1. **Hook-script syntax check** — run from the repo root to verify all hook scripts parse:

   ```bash
   py -3 -c "import ast,sys; [ast.parse(open(f,encoding='utf-8').read(),f) for f in sys.argv[1:]]" claude/scripts/*.py
   ```

   `ast.parse` is used instead of `py_compile` because the latter writes `.pyc` files into
   `claude/scripts/__pycache__/` as a side effect (see [dev-env#276](https://github.com/brownm09/dev-env/issues/276));
   neither `-B` nor `PYTHONDONTWRITEBYTECODE=1` suppresses that. On Windows, `python3` resolves
   to the Microsoft Store stub — use `py -3` (the Windows Python Launcher), see [ADR-007](docs/adr/007-hook-command-invocation.md).

2. **`pyw -3` stdio verification** (Windows-only — confirms `pythonw.exe` honors parent-supplied
   pipes, the invariant ADR-007's 2026-06-01 decision relies on):

   ```bash
   py -3 claude/scripts/tests/test_pyw_stdio.py
   ```

3. **Pre-push hook self-test** — required when changing `claude/hooks/pre-push`. Drives the real
   hook against throwaway fixture repos with a stubbed `npm` and asserts the lockfile-drift guard's
   BLOCK / PASS / SKIP paths, working-tree restoration, and repo-hook chaining (see [ADR-036](docs/adr/036-lockfile-drift-prevention.md)):

   ```bash
   bash claude/hooks/tests/test-pre-push-lockfile.sh
   ```

4. **Docs-only guard** — for docs-only changes to `claude/CLAUDE.md`: run
   `grep -n 'date -u' claude/CLAUDE.md` and confirm every match is in an internal operational
   artifact context (lock files, log timestamps) — not in stub filename or branch name descriptions.

5. **Script path-hygiene lint** — required when adding or changing any `claude/scripts/*.sh` or
   `claude/hooks/*` shell script. Flags the [dev-env#334](https://github.com/brownm09/dev-env/issues/334)
   failure class: a `$HOME`-rooted scratch/temp path passed to `node` (Git Bash writes
   `/c/Users/...` → `C:\Users\...`, but Node-on-Windows re-resolves the same string to
   `C:\c\Users\...` → ENOENT). Scripts must use the literal `C:/Users/brown/.claude/scratch`
   instead. Hermetic; comments mentioning `$HOME` are ignored.

   ```bash
   bash claude/scripts/tests/check-script-path-hygiene.sh
   ```

6. **`get-project-item.sh` smoke test** — required when changing `claude/scripts/get-project-item.sh`.
   Actually *runs* the script (which `bash -n` cannot — #334 parsed cleanly yet failed at runtime):
   asserts a known issue resolves to a `PVTI_` id, the no-match path exits 1, and the temp file is
   cleaned up. Network-dependent — SKIPs (exit 0) when `gh` is unauthenticated/offline, so it is a
   local pre-PR check, not a CI gate.

   ```bash
   bash claude/scripts/tests/test-get-project-item.sh
   ```

7. **shellcheck gate** — recommended when changing any shell script. Blocking at `--severity=error`
   (the tree is error-clean as of 2026-06-07); pre-existing warnings/info are printed advisorily and
   not gated. SKIPs (exit 0) with an install hint when `shellcheck` is absent — it is not installed by
   default here and `choco install shellcheck` needs an elevated shell, so set `SHELLCHECK_BIN` to a
   [portable binary](https://github.com/koalaman/shellcheck/releases) to run it locally.

   ```bash
   bash claude/scripts/tests/run-shellcheck.sh
   ```

8. **usage-snapshot classifier test** — required when changing `claude/scripts/usage-snapshot.py`.
   Exercises the pure `classify_token()` helper offline (no network, no credentials file): pins the
   `no_expiry` / `ok` / `expiring` / `expired` states and the expiry boundary, asserting an expired
   token now yields a user-facing advisory rather than the silent skip it did before
   [#355](https://github.com/brownm09/dev-env/issues/355). Also exercises the pure
   `merge_confirmed(command, output)` predicate offline: pins that a worktree-merge payload (gh's
   success marker present, exit code non-zero) confirms despite the exit code, while a queued
   `--auto` (no marker yet) and a non-merge command do not — the exit-code-only gate this replaced
   silently dropped the snapshot on every worktree merge
   ([dev-env#474](https://github.com/brownm09/dev-env/issues/474), ADR-049/ADR-050 amendment). The
   live usage API call is not covered (the repo avoids urllib mocks).

   ```bash
   py -3 claude/scripts/tests/test_usage_snapshot.py
   ```

9. **worktree-npm-install gate test** — required when changing `claude/scripts/worktree-npm-install.py`.
   Exercises the pure `install_decision()` helper offline (no disk, no network, no npm): pins the
   `proceed` / `reclaim-first` / `abort` decisions and the 10 GB / 5 GB threshold boundaries that gate a
   low-space install against silent ENOSPC truncation ([ADR-045](docs/adr/045-pre-install-freespace-gate.md)).
   The synchronous reclamation ladder and the real install are not covered (they shell out; the repo
   avoids subprocess mocks).

   ```bash
   py -3 claude/scripts/tests/test_worktree_npm_install.py
   ```

10. **post-pr-merge-reclaim test** — required when changing `claude/scripts/post-pr-merge-reclaim.py`.
    Exercises the pure `is_successful_merge()` predicate offline: a `gh pr merge` whose output carries
    gh's success marker triggers reclamation, regardless of exit code; a non-merge command, a genuinely
    failed merge, an exit-0 non-merge invocation like `gh pr merge --help` (dev-env#485), or `gh pr merge`
    text mentioned only inside a heredoc body, a quoted argument, or a `$()` subshell (dev-env#529, the
    command-shape check is `scan_top_level`-anchored, not a raw substring test —
    [ADR-050 Amendment 9](docs/adr/050-shared-hookio-sibling-hook-fixes.md)) does not. The detached reclaim
    spawn is not covered (it shells out).

    ```bash
    py -3 claude/scripts/tests/test_post_pr_merge_reclaim.py
    ```

11. **memory-write-advisory test** — required when changing `claude/scripts/memory-write-advisory.py`.
    Exercises the pure `should_advise_memory_write()` predicate offline (no stdin, no Claude session):
    pins that a durable memory write with no immortalization link advises, while a write that already
    cites an issue/ADR/`CLAUDE.md`, the `MEMORY.md` index, a non-`memory/` path, a non-`.md` file, or the
    `Edit` tool stays silent ([ADR-048](docs/adr/048-memory-immortalization-issue-pairing.md)). The stdin
    plumbing and exit-2 emission are not covered (pure-helper convention).

    ```bash
    py -3 claude/scripts/tests/test_memory_write_advisory.py
    ```

12. **post-tool-use test** — required when changing `claude/scripts/post-tool-use.py`. Exercises the pure
    `read_command_output()`, `extract_github_url()`, `canonical_root_from_worktree()`, and
    `_canonical_root_from_common_dir()` helpers offline: pins that the real `stdout`-shaped Bash payload yields
    a non-empty output (the pre-fix `output` read was `""` — the [#377](https://github.com/brownm09/dev-env/issues/377)
    silent no-op), that the legacy `output` field still works, that the de-silenced no-URL path distinguishes a
    different-repo miss from a genuine empty ([ADR-049](docs/adr/049-hook-payload-output-field.md)), that a
    Claude-managed worktree cwd whose `hook-config.json` is absent (a project that gitignores it, dev-env's own
    convention — not every project's, e.g. lifting-logbook tracks it) resolves the canonical checkout's
    config — verified end-to-end via a hermetic temp dir — and that the sibling-worktree `git --git-common-dir`
    output resolves to the canonical root ([ADR-052](docs/adr/052-worktree-config-canonical-fallback.md)). Also
    exercises `is_issue_create_command()` / `is_pr_create_command()` (built on the shared
    `_hookio.scan_top_level`, [ADR-050 Amendment 5](docs/adr/050-shared-hookio-sibling-hook-fixes.md)): the four
    dev-env#499 false-positive reproductions (heredoc-embedded commit body, quoted commit message, grep pattern
    argument, `--text` field value) for both PR- and issue-create, plus subshell/quote/cd-prefix/chained cases;
    and a `subprocess`-driven end-to-end pair (`_run_hook`, mirroring `test_worktree_path_check.py`'s pattern)
    pinning that the pre-existing `exit_code != 0` gate still short-circuits immediately after the detection
    swap, without either branch invoking a live `gh` call. Also exercises `is_issue_create_help_only()` /
    `is_pr_create_help_only()` (thin wrappers over the shared `_hookio.is_help_only()`,
    [ADR-050 Amendment 15](docs/adr/050-shared-hookio-sibling-hook-fixes.md); dev-env#636 — the identical
    `--help` false-positive `is_merge_help_only` closes for `gh pr merge`, reproduced here for
    `gh issue create` / `gh pr create`): bare `--help`/`-h`, a real create, no invocation at all, and —
    via two `main()` end-to-end subprocess cases — that `gh issue/pr create --help` now exits 0 silently
    and that a help-only issue-create chained with a REAL pr-create still reaches the exit-2 "no GitHub
    URL found" path for the real create (proving `main()` downgrades each create-flag independently
    rather than bailing out wholesale). Also exercises the dev-env#527 / [ADR-076](docs/adr/076-live-fetch-project-hook-single-select-options.md)
    live-fetch of `single_select` field options: the pure `_parse_live_options()` response parser (valid, empty-options,
    null-node, and malformed-JSON shapes), the pure `_resolve_required_fields()` legacy-config normalization shared
    between rendering and live-fetch target discovery, `fetch_live_required_field_options()`'s field-selection logic
    (single_select-with-field_id only) via an injected fake `fetch_fn`, and `format_reminder()`'s three rendering
    branches — live data used and labeled, a failed fetch falling back to cached data labeled as possibly stale, and
    the no-`live_options`-passed case matching pre-#527 output exactly. The live `gh project item-add` call, the
    `subprocess.run` in `canonical_root_via_git()`, and the live `gh api graphql` call in `fetch_live_field_options()`
    are not covered (the repo avoids subprocess mocks) — the last was instead verified once by hand against dev-env's
    own live Impact field during development of the #527 fix.

    ```bash
    py -3 claude/scripts/tests/test_post_tool_use.py
    ```

13. **_hookio shared-read test** — required when changing `claude/scripts/_hookio.py`. Exercises the pure
    `read_command_output()` and merge-marker helpers (`output_has_merge_marker` / `merge_pr_number_from_output`) offline (no network, no gh): pins that the real `stdout`/`stderr`-shaped
    Bash payload yields the command output (the pre-#380 `output` read was always `""`), that stdout and
    stderr are joined, that the legacy `output` field is still honored as a fallback, and that a missing /
    empty / `None` / non-dict `tool_response` yields `""` without raising. Also exercises the pure
    `should_confirm_via_gh(exit_code, output)` predicate: only a non-zero exit code with no marker present
    is worth a live `gh pr view` confirmation (dev-env#489, [ADR-050 amendment 3](docs/adr/050-shared-hookio-sibling-hook-fixes.md));
    a clean exit or a marker already found never pays the network call. Also exercises `effective_merge_dir()`
    (cd-chain / cwd resolution, [ADR-067](docs/adr/067-scope-merge-keyed-hooks-to-target-repo.md)) and
    `scan_top_level()` — the stack-based top-level-statement parser shared with `pr-merge-reminder.py` and
    `post-tool-use.py` ([ADR-050 Amendment 5](docs/adr/050-shared-hookio-sibling-hook-fixes.md)): anchored-match
    semantics, non-splitting inside single/double quotes, `$()` subshells, and heredoc bodies, and correct
    splitting on `&&`, `;`, `||`, and newline. Also exercises `is_merge_help_only()` ([ADR-050 Amendment 13](docs/adr/050-shared-hookio-sibling-hook-fixes.md);
    dev-env#557 — a `gh pr merge --help` invocation must never be mistaken for a real merge attempt): bare
    `--help`/`-h`, a real merge, no merge invocation at all, chained help-then-real and two-chained-help
    cases, heredoc/quoted-argument mentions that must not affect a real invocation elsewhere, and
    case-insensitivity. Also exercises `is_help_only(command, invocation_re)` — the generalized core
    `is_merge_help_only` now wraps ([ADR-050 Amendment 15](docs/adr/050-shared-hookio-sibling-hook-fixes.md);
    dev-env#636) — directly, with a non-merge `invocation_re`, proving the extraction is genuinely generic
    rather than merge-specific (the real second/third caller, `post-tool-use.py`'s
    `is_issue_create_help_only()` / `is_pr_create_help_only()`, is covered in item 12 above). `_hookio` is
    imported by all five PostToolUse Bash hooks plus `pr-merge-reminder.py`
    ([ADR-050](docs/adr/050-shared-hookio-sibling-hook-fixes.md)). `confirm_merge_via_gh()`
    itself is not covered (it shells out to `gh pr view`).

    ```bash
    py -3 claude/scripts/tests/test_hookio.py
    ```

14. **post-pr-merge-project test** — required when changing `claude/scripts/post-pr-merge-project.py`.
    Exercises the pure `extract_pr_number_from_command()`, `extract_pr_number()`, `merge_succeeded()`, and
    `extract_repo_from_command()` helpers offline: pins command-based extraction (`gh pr merge 380` / a
    `/pull/380` URL / bare form → `None`), output-marker extraction (`Squashed and merged pull request #N`,
    the cross-repo `owner/repo#N` variant, and the legacy `/pull/N` URL), the `--auto`-safe merge gate (a
    queued `--auto` or a failed merge yields no completed-merge number and `merge_succeeded` is `False`),
    and the dev-env#559 repo resolution (a PR URL's owner/repo parsed from the merge command, scoped to
    the same merge-args region as the PR-number extraction so a chained sibling command can't hijack it;
    a bare number or bare form both yield `None`, falling back to cwd's config; an explicit `--repo` flag
    is checked first and wins over a URL mentioned in a `--subject`/`--body` value — review finding on
    PR #572 — mirroring `_effective_merge_repo`'s flag-first precedence in `pr-merge-reminder.py`), and
    also recognizes gh's `-R` shorthand for `--repo` (dev-env#616 — the shorthand previously fell through
    to `None` and silently resolved against cwd's own config instead of the command's actual target repo).
    `main()` skips the whole operation when the parsed repo doesn't match cwd's config — cwd's
    project-board fields don't apply to a different repo regardless of which PR's body gets fetched —
    but that gate itself is not separately unit-tested, consistent with this file's pure-helper-only
    convention. The live `gh` calls
    (`get_pr_body` / `find_project_item` / `move_to_done` / `confirm_merge_via_gh`) are not covered
    ([ADR-050](docs/adr/050-shared-hookio-sibling-hook-fixes.md),
    [ADR-067](docs/adr/067-scope-merge-keyed-hooks-to-target-repo.md)).

    ```bash
    py -3 claude/scripts/tests/test_post_pr_merge_project.py
    ```

15. **post-pr-merge-pull test** — required when changing `claude/scripts/post-pr-merge-pull.py`. Exercises
    the pure `is_successful_merge()` predicate offline: a `gh pr merge` whose output carries gh's success
    marker triggers the local-`main` fast-forward regardless of exit code (worktree merges exit non-zero but
    print the marker — issue #275); a non-merge command, a genuinely failed merge, an exit-0 non-merge
    invocation like `gh pr merge --help` (dev-env#485), or `gh pr merge` text mentioned only inside a
    heredoc body, a quoted argument, or a `$()` subshell (dev-env#529 — the command-shape check is
    `scan_top_level`-anchored, not a raw substring test) does not. Also exercises the pure `pull_command()`
    predicate: a canonical checked out on `main` gets a plain `pull --ff-only` (the fetch-into-ref trick
    fails with 'refusing to fetch into branch ... checked out' there — dev-env#488,
    [ADR-058 amendment](docs/adr/058-worktree-squatting-main-detection-correction.md)); a feature branch
    (or squatting worktree) checked out gets the original fetch-into-ref, unchanged. Also exercises the
    pure-string resolution paths of `extract_repo()`'s `--repo`/`-R` flag check (`-R` shorthand added in
    dev-env#616) and its GitHub-URL parse. The `pull_main` / `list_worktrees` git calls and `extract_repo`'s
    git-remote subprocess fallback are not covered
    ([ADR-050](docs/adr/050-shared-hookio-sibling-hook-fixes.md), incl. Amendment 9 for the command-shape
    anchoring).

    ```bash
    py -3 claude/scripts/tests/test_post_pr_merge_pull.py
    ```

16. **stub-push-archive-reminder test** — required when changing
    `claude/scripts/stub-push-archive-reminder.py`. Exercises the pure `has_push_error()` guard offline: a
    successful push output arms the archive reminder, while an `error:` / `fatal:` line (case-insensitive)
    blocks it. The pre-#380 read of the legacy `output` field was always empty, so this guard was a no-op.
    Also exercises the pure `is_git_push_command()` predicate (dev-env#532): a bare top-level `git push`
    matches, while `git push` text mentioned only inside a heredoc body, a quoted argument, or a `$()`
    subshell does not — the command-shape check is `scan_top_level`-anchored, not a raw substring test,
    mirroring `pr-merge-reminder.py`'s identical `is_git_push_command()`
    ([ADR-050](docs/adr/050-shared-hookio-sibling-hook-fixes.md), incl. Amendment 10 for the command-shape
    anchoring). Also exercises the pure `references_engineering_journal()` predicate (dev-env#539): a
    `cd <path>` or `git -C <path>` naming the engineering-journal repo (either the hyphenated or
    underscored spelling) matches, while the same text mentioned only inside a heredoc body, a quoted
    argument, or a `$()` subshell does not — anchored the same `scan_top_level` way as
    `is_git_push_command()`, but keyed on the `cd`/`-C` directory-argument prefix rather than a CLI verb,
    since the target text is itself an argument *value*, never the start of its own statement
    ([ADR-050](docs/adr/050-shared-hookio-sibling-hook-fixes.md), incl. Amendment 12 for the command-shape
    anchoring).

    ```bash
    py -3 claude/scripts/tests/test_stub_push_archive_reminder.py
    ```

17. **worktree-liveness guard test** — required when changing `claude/scripts/_worktree_liveness.py` or the
    prune/reclaim scripts' use of it. Exercises the pure `encode_project_slug()` / `is_recent()` and the
    filesystem `transcript_dir_for()` / `newest_jsonl_mtime()` / `worktree_session_is_live()` helpers
    offline (tmp projects root + `os.utime`, no live session): pins the slug encoding (`:` `\` `/` `.` all
    become `-`), the recency boundary (incl. unknown and future mtimes), exact-slug and basename-suffix
    transcript-dir resolution, recursive newest-`.jsonl` mtime (incl. `subagents/`), the live/stale/no-session
    verdicts that stop prune/reclaim from severing an active session, and the prune=24h / reclaim=6h default
    windows ([ADR-051](docs/adr/051-worktree-liveness-guard.md)). The git-driven prune/reclaim loops are
    exercised by `--dry-run` in the PR, not here.

    ```bash
    py -3 claude/scripts/tests/test_worktree_liveness.py
    ```

18. **posttooluse-inert-advisory test** — required when changing
    `claude/scripts/posttooluse-inert-advisory.py`. Exercises the pure transcript-scanning helpers offline
    (synthetic records; no stdin, network, gh, or disk): pins that an `attachment` record with
    `hookEvent == "PostToolUse"` (both the exit-0 `hook_success` and exit-2 `hook_blocking_error` shapes)
    proves PostToolUse fired, that `iter_bash_calls` pairs each Bash command with its result by `tool_use_id`
    (so parallel calls don't cross), that `detect_board_actions` triggers only on dev-env (project #3)
    creates/merges and ignores other-repo URLs, URL-less creates, hard-merge-failures, and bare merges from a
    non-dev-env cwd, that `_devenv_merge_pr`'s `--repo`/`-R` flag check resolves both forms identically
    (`-R` shorthand added in dev-env#616), that `should_emit` stays silent whenever any PostToolUse
    attachment is present (the healthy session), and that the advisory is ASCII/cp1252-encodable so it
    can't vanish under Claude Code's
    cp1252-piped hook stdout ([ADR-053](docs/adr/053-posttooluse-hooks-inert-in-background-sessions.md),
    [ADR-055](docs/adr/055-reliable-event-inert-posttooluse-advisory.md)). `iter_bash_calls`,
    `load_records`, and `_result_text` are imported from `_hookutil`
    ([ADR-090](docs/adr/090-shared-transcript-readers-hookutil.md)) and reached via module-attribute
    indirection, so this suite pins the advisory-specific behavior unchanged. The `main()` I/O (stdin,
    transcript locate, sentinel) is not covered (pure-helper convention).

    ```bash
    py -3 claude/scripts/tests/test_posttooluse_inert_advisory.py
    ```

19. **reconcile-open-prs test** — required when changing `claude/scripts/reconcile-open-prs.py`. Exercises
    the pure helpers and the per-PR shard reconciler offline (tmp dirs + an injected `state_fn`, no `gh`):
    pins `should_remove` (MERGED/CLOSED remove; OPEN/None/unknown keep), `shard_pr_number` /
    `repo_from_url` / `entry_repo_and_pr` parsing, and the [ADR-056](docs/adr/056-per-session-sharding-journal-companion-files.md)
    structural guarantee that `reconcile_shard_dir` unlinks only the merged shard and leaves the surviving
    shard **byte-identical** (the no-clobber property), removes an emptied `open-prs/` dir, never auto-deletes
    malformed/non-numeric shards, and that the legacy `reconcile_file` still drains `open-prs.jsonl`. Also
    exercises `find_dirty_open_pr_paths` (dev-env#578, [ADR-082 Addendum](docs/adr/082-journal-compose-worktree-isolation.md)):
    the pure `git status --porcelain` line filter that surfaces any currently-uncommitted
    `sessions/*/open-prs*` change (this session's own fresh unlinks, or a prior session's
    never-committed ones) — pins the porcelain `XY <path>` slicing, that shard/legacy-file paths are
    kept while unrelated paths (stub files, script edits) are dropped, backslash-to-forward-slash
    path normalization, rename lines (`old -> new`) resolving to just the destination path (review
    finding on PR #581 — nothing in this hook renames a shard, but a rename from elsewhere no longer
    produces an unaddable combined string), and graceful handling of empty/short input. The live
    `gh pr view` boundary (`check_pr_state`) and the `git status --porcelain` boundary
    (`dirty_open_pr_status_lines`) are
    not covered (the repo avoids subprocess mocks).

    ```bash
    py -3 claude/scripts/tests/test_reconcile_open_prs.py
    ```

20. **post-compact open-PR reader test** — required when changing `claude/scripts/post-compact.py`. Exercises
    the pure `read_open_pr_entries()` offline (tmp dirs, no `git`): pins that per-PR shards `open-prs/<N>.json`
    and the legacy `open-prs.jsonl` are unioned and deduped by PR number (shard wins), that shards sort
    numerically (not lexically), that a malformed shard is skipped without dropping valid ones, and that a
    record with no `pr` is skipped (no None-key collapse, no downstream KeyError)
    ([ADR-056](docs/adr/056-per-session-sharding-journal-companion-files.md)). `get_journal_project` (a git
    call) and the systemMessage emission are not covered.

    ```bash
    py -3 claude/scripts/tests/test_post_compact.py
    ```

21. **journal-shards shared-reader test** — required when changing `claude/scripts/_journal_shards.py`.
    Exercises the pure shard/legacy readers offline (tmp dirs, no network, no `gh`): pins `shard_pr_number`
    parsing, that `iter_pr_shards` returns `(path, entry)` pairs numerically sorted (PR 2 before 10) with
    non-numeric-named, unparseable, and non-object shards skipped and a missing/non-dir path yielding `[]`,
    that a pr-less dict shard is still returned (the reader stays content-agnostic — the consumer dedups),
    and that `read_legacy_entries` drains one JSON object per line while skipping blank/malformed/non-dict
    lines and a missing file. The two consuming hooks (`reconcile-open-prs.py`, `post-compact.py`) import
    these helpers as one source of truth ([ADR-057](docs/adr/057-shared-journal-shard-reader.md)).

    ```bash
    py -3 claude/scripts/tests/test_journal_shards.py
    ```

22. **worktree-topology test** — required when changing `claude/scripts/_worktree_topology.py` or the
    squat-detection paths in `prune-merged-worktrees.py` / `post-pr-merge-pull.py` / `dev-env-sync.py`.
    Exercises the pure topology + decision helpers offline (no git, no network, no disk; paths need not
    exist): pins `parse_worktree_porcelain` (path/branch/detached/`refs/heads/` stripping), `canonical_worktree`
    (first entry), `park_branch_for` (`claude/<basename>`, Windows + POSIX spellings), `main_squatter`
    (a non-canonical worktree on `main`, and `None` when the canonical holds `main` or the ref is free),
    `diagnose_main_topology` (healthy / squat / canonical-off-main-no-squatter), `canonical_sync_action`
    (`warn-squatter` / `return-canonical` / `warn-dirty` / `on-main` — what `dev-env-sync` does), and
    `merge_park_target` (parks a repo's own worktree left on `main`; `None` for the canonical / not-on-main /
    empty / **cross-repo** cwd-not-a-worktree-of-the-merged-repo / Windows-vs-POSIX spelling — what
    `post-pr-merge-pull` does). `prune`'s park is exercised
    end-to-end by `--dry-run` / a throwaway-repo run in the PR, not here (it shells out to git)
    ([ADR-058](docs/adr/058-worktree-squatting-main-detection-correction.md)).

    ```bash
    py -3 claude/scripts/tests/test_worktree_topology.py
    ```

23. **post-merge-tile-checkpoint test** — required when changing
    `claude/scripts/post-merge-tile-checkpoint.py`. Exercises the pure
    `is_successful_merge()` predicate offline (no subprocess, no network, no disk):
    pins that a successful merge (gh's stdout success marker present, regardless of exit
    code) triggers the tile checkpoint reminder, while a non-merge command, a genuinely
    failed merge (no success marker), an exit-0 non-merge invocation like
    `gh pr merge --help` (dev-env#485), or `gh pr merge` text mentioned only inside a
    heredoc body, a quoted argument, or a `$()` subshell (dev-env#529 — the command-shape
    check is `scan_top_level`-anchored, not a raw substring test) does not
    ([ADR-060](docs/adr/060-post-merge-tile-checkpoint-hook.md);
    [ADR-050 Amendment 9](docs/adr/050-shared-hookio-sibling-hook-fixes.md)).

    ```bash
    py -3 claude/scripts/tests/test_post_merge_tile_checkpoint.py
    ```

24. **pre-merge-message-check test** — required when changing `claude/scripts/pre-merge-message-check.py`.
    Exercises the pure `is_pr_merge_command` predicate (built on the shared `_hookio.scan_top_level`,
    matching `pre-merge-numbering-check.py`'s identically-named predicate — dev-env#519) and the
    `_read_queue()` helper offline (tmp files for queue content; no network, no gh): pins that
    detection fires on a bare / flagged / chained / `cd`-chained `gh pr merge` invocation and stays
    silent on a non-merge command or a `gh pr merge` mentioned only inside a heredoc body
    (dev-env#499), that `_read_queue` returns content verbatim, returns `""` for an empty or
    whitespace-only file, and returns `""` when the queue file is absent (fail-open). The stdin
    plumbing and exit-2 emission are not covered (pure-helper convention). ([ADR-061](docs/adr/061-pre-merge-message-queue.md))

    ```bash
    py -3 claude/scripts/tests/test_pre_merge_message_check.py
    ```

25. **manifest field validator test** — required when changing `claude/scripts/validate-manifest.py`.
    Since dev-env #556 / [ADR-081](docs/adr/081-write-time-journal-shard-validation-hook.md), the
    pure helpers this test exercises (`missing_required_fields()`, `find_entries_missing_fields()`,
    `parse_manifest_text()`) live in `claude/scripts/_journal_schema.py` and are re-imported by
    `validate-manifest.py` — this test still exercises them unchanged, through that re-export. Run
    this test, and item 41's, when changing `_journal_schema.py`. Pins that a fully-valid entry (all
    five required fields) reports no missing fields; that a single absent field is returned in
    canonical schema order; that a non-dict entry (list or scalar) is treated as missing every required
    field; that `find_entries_missing_fields` skips valid entries and preserves input order; and that
    `parse_manifest_text` handles blank-line skipping, ADR-056 single-object shards, legacy multi-line
    manifests, invalid JSON, and JSON non-objects — all returning `(lineno, None)` so callers can
    report parse errors with a file-and-line reference. The `main()` I/O (argv, file reads, exit code)
    is not covered (pure-helper convention). Converts the silent-skip class from #423 (missing field
    found mid-compose, hand-patched) into a visible up-front gate in `/journal-compose` Step 0.7.

    ```bash
    py -3 claude/scripts/tests/test_validate_manifest.py
    ```

26. **prune-merged-worktrees test** — required when changing `claude/scripts/prune-merged-worktrees.py`.
    Exercises `prune_one()` via subprocess mocking (subprocess.run mocking, unlike the other offline
    pure-helper tests): pins that a `subprocess.TimeoutExpired` from a slow `git worktree remove` is caught, the timed-out
    worktree is counted as skipped, and the prune loop continues rather than propagating the exception
    and aborting the scan ([dev-env#350](https://github.com/brownm09/dev-env/issues/350)). Also exercises
    the ADR-075 ephemeral-diff prunability signal offline: `files_are_all_ephemeral()` (matches-all,
    one-mismatch, the empty-patterns opt-in gate, and empty-files all resolve correctly) and
    `load_ephemeral_patterns()` against a real tmp `.claude/hook-config.json` (reads a configured
    list; fails open to `[]` on a missing file, an unreadable file (`OSError`, e.g. a transient
    `PermissionError` — a review finding, since the original code only caught `FileNotFoundError`
    and an uncaught exception here would abort every remaining repo in a `--scan-dir` run), missing
    key, malformed JSON, a non-list value, a non-string element, or an invalid regex — the last
    case also prints a `WARNING:` line, captured via `redirect_stdout`). Also exercises the
    ADR-078 `--include-named` opt-in via `prune_one()`'s `include_named` parameter: a merged,
    clean, non-`claude/*` branch worktree is skipped via the unchanged prefix-guard reason when
    `include_named=False` (the default — regression proof), and pruned via the exact same
    `is_merged()`/`is_dirty()` path `claude/*` branches already use when `include_named=True`
    ([dev-env#545](https://github.com/brownm09/dev-env/issues/545)). The
    merge-detection and worktree-list steps are not covered here — they are exercised end-to-end by
    `--dry-run` in the PR.

    ```bash
    py -3 claude/scripts/tests/test_prune_merged_worktrees.py
    ```

27. **_hookutil shared-helper test** — required when changing `claude/scripts/_hookutil.py`.
    Exercises the pure `cleanup_stale_sentinels(prefix)`, `sentinel_path(prefix, session_id)`,
    and `find_transcript(session_id)` helpers offline (injected tmp dirs, no real
    `~/.claude/scratch` or `~/.claude/projects`): pins sentinel-path correctness, that
    `cleanup_stale_sentinels` removes flags older than `MAX_AGE_DAYS` matching the given prefix
    while leaving fresh ones and files with other prefixes intact, that it does not raise when the
    scratch directory is absent, and that `find_transcript` returns the matching path (or `None`)
    including when the JSONL is nested under a project subdirectory. Also exercises the
    transcript-record readers ([ADR-090](docs/adr/090-shared-transcript-readers-hookutil.md)):
    `_content_items` (list content vs the non-dict-rec / non-dict-message / non-list-content guard
    cases -> `[]`), `_result_text` (string / list-joined / `toolUseResult` stdout+stderr fallback /
    empty -> `''`), `iter_bash_calls` (id-pairing returning `(command, output, cwd)`, out-of-order
    parallel results not crossing, default `cwd=''`, and non-dict/non-Bash/orphan records -> `[]`
    without raising), `_parse_records` (keeps only JSON objects, dropping blank/malformed/non-object
    lines), and `load_records` (reads a JSONL file to its object records). Imported by
    `posttooluse-inert-advisory.py`, `stop-tile-enumeration-gate.py`, `reconcile-open-prs.py`, and
    `token-tracker.py` ([ADR-064](docs/adr/064-shared-hookutil-sentinel-transcript-locate.md),
    [ADR-090](docs/adr/090-shared-transcript-readers-hookutil.md)).

    ```bash
    py -3 claude/scripts/tests/test_hookutil.py
    ```

28. **pr-merge-reminder test** — required when changing `claude/scripts/pr-merge-reminder.py`.
    Exercises the pure command predicates (`is_pr_create_command` / `is_pr_merge_command` /
    `is_git_push_command` — now built on the shared `_hookio.scan_top_level`,
    [ADR-050 Amendment 5](docs/adr/050-shared-hookio-sibling-hook-fixes.md)), the `_create_shard_step` /
    `_is_successful_merge_call` helpers, and the
    [ADR-065](docs/adr/065-scope-push-reminder-to-target-repo.md) push-scoping behavior offline
    (no network/gh): pins that `_effective_push_dir` redirects the open-PR lookup to a
    `cd <repo> && git push` target — so a cross-repo push is evaluated against THAT repo, not the
    session cwd — and falls back to cwd for a bare push; and that `_is_successful_merge_call` gates
    solely on gh's success marker, not the exit code, so an exit-0 non-merge invocation like
    `gh pr merge --help` no longer fires the reminder (dev-env#485). Also exercises `_build_messages`'s
    `live_confirmed: bool | None` parameter (dev-env#504 — the live `gh pr view` fallback for when the
    marker itself is lost, dev-env#489): `True` overrides a marker-less `merge_ok` to fire the merge
    reminder, `False` leaves it unfired, and the default `None` (every pre-existing call in this suite)
    reproduces the exact pre-#504 marker-only behavior — `main()`, not `_build_messages`, decides
    whether to attempt the live call, so this suite never shells out. Also exercises `_effective_merge_repo`'s
    `--repo`/`-R` flag check (`-R` shorthand added in dev-env#616) overriding cwd and any cd-chain, falling
    back to `effective_merge_dir` when absent. The live `_open_pr_for_cwd` and
    `confirm_merge_via_gh` subprocess boundaries are not covered (the repo avoids subprocess mocks).

    ```bash
    py -3 claude/scripts/tests/test_pr_merge_reminder.py
    ```

29. **reconcile-project-board test** — required when changing `claude/scripts/reconcile-project-board.py`.
    Exercises the pure helpers offline (no network/gh/disk): pins `canonical_repo_root` (a Claude-managed
    worktree path resolves to the canonical checkout where the machine-local hook-config lives), `field_key`
    (field name -> item-list JSON key), `board_issue_numbers` (ignores PRs / cross-repo / number-less items),
    the `compute_orphans` set difference (open issues minus board issues — the #434/#435/#436 case),
    `item_missing_fields` / `board_items_missing_fields` (a field is missing unless it holds a non-empty
    value; only OPEN same-repo issues are surfaced), `looks_like_scope_error`, and `render_report` —
    including the **no-guessing contract** (it emits `gh project item-edit` commands but never assigns a
    field value), the dry-run wording, and the machine-readable `RESULT:` line the routine reads. The `gh`
    boundary (`fetch_open_issues` / `fetch_board_items` / `add_to_project`) is not mocked (repo convention) —
    it is exercised by the `--dry-run` integration run in the PR
    ([ADR-068](docs/adr/068-reconcile-project-board-orphan-issues.md)).

    ```bash
    py -3 claude/scripts/tests/test_reconcile_project_board.py
    ```

30. **reclaim-worktree-disk test** — required when changing `claude/scripts/reclaim-worktree-disk.py`.
    Exercises the pure decision/discovery helpers offline (real tmp-dir fixtures, no mocking): pins the
    `is_idle_eligible` merged/commits-ahead decision table, the `.claude/worktrees/` membership gate
    (`is_claude_managed_worktree`), `find_reclaim_dirs` (discovers top-level and nested-workspace
    `node_modules`/`.turbo`, does not descend into a found reclaim dir, skips `.git`), `dir_size_bytes`,
    and `reclaim_worktree` (dry-run reports without deleting; a real run deletes only the reclaim dirs,
    preserving source files and `.git`). The git-dependent eligibility guards (dirty check, primary/cwd
    exclusion, merge detection) are exercised end-to-end by `--dry-run` in the PR, not here. This test
    file pre-dates this list — added here to close the gap where it existed but was never
    cross-referenced ([ADR-037](docs/adr/037-worktree-disk-reclamation.md)).

    ```bash
    py -3 claude/scripts/tests/test_reclaim_worktree_disk.py
    ```

31. **_repo_scan shared-module test** — required when changing `claude/scripts/_repo_scan.py` or the
    `--scan-dir` discovery path in `prune-merged-worktrees.py` / `reclaim-worktree-disk.py` /
    `reconcile-project-board.py`. Exercises the pure `find_git_repos(scan_dir)` helper offline (real
    `tempfile.TemporaryDirectory()` fixtures, no mocking): pins that a primary repo (`.git` directory) is
    discovered while a worktree (`.git` file), a plain non-repo directory, and a bare file are all
    excluded; case-insensitive sort order; a nonexistent `scan_dir` returning `None` (distinct from
    `[]`); and an empty-but-readable `scan_dir` returning `[]`. `PermissionError` shares the same
    `except` tuple as the tested `FileNotFoundError` path and is not separately exercised (mirrors the
    pure-helper convention elsewhere in this list). [ADR-072](docs/adr/072-shared-repo-scan-module.md)

    ```bash
    py -3 claude/scripts/tests/test_repo_scan.py
    ```

32. **worktree-path-check test** — required when changing `claude/scripts/pre-tool-use-worktree-path-check.py`.
    Exercises the pure `_worktree_is_live()` decision table offline (stubbed `path_exists` / `git_toplevel`
    helpers, no real git): pins the six liveness combinations — a live worktree, a missing `.git` link, git
    resolving up to the canonical root, git resolving to an unrelated path, a failed git call treated as live
    rather than false-blocking, and a live match differing only by case/separator — plus that the `.git`-link
    check short-circuits before git is ever spawned. End-to-end `main()` tests drive the real hook over stdin:
    an Edit from an orphaned worktree cwd (missing `.git` link) is blocked with the orphan recovery recipe; a
    Write whose absolute path escapes a *live* worktree to the canonical root is blocked with the escape
    recovery recipe (ADR-024's primary, most-documented scenario, and a code path the orphan test doesn't
    reach); and a call from a non-worktree cwd is a no-op. Both block scenarios assert the reason lands on
    stderr with empty stdout — Claude Code discards a PreToolUse hook's stdout on exit code 2, so a reason
    printed there would be silently invisible to the model even though the block itself still worked
    ([dev-env#469](https://github.com/brownm09/dev-env/issues/469)). This test file pre-dates this list —
    added here to close the gap where it existed but was never cross-referenced
    ([ADR-024](docs/adr/024-worktree-path-guard-hook.md)).

    ```bash
    py -3 claude/scripts/tests/test_worktree_path_check.py
    ```

33. **canonical-mutate-guard test** — required when changing `claude/scripts/pre-tool-use-canonical-mutate-guard.py`.
    Exercises the pure `classify()` / `is_mutating_segment()` helpers offline: pins the full mutating-verb
    matrix (checkout / `checkout -b`, switch, commit, merge, rebase, reset, cherry-pick, revert, stash
    pop/apply — including flag-prefixed forms like `git -c gc.auto=0 stash pop` — `branch -d`/`-D`/`--delete`,
    and bare pull) against the explicitly-allowed read-only surface (status, log, diff, show, fetch,
    `branch --show-current`, rev-parse, ls-tree, blame, `remote -v`, plain branch, stash list/show,
    `checkout -- <path>`, `pull --ff-only`, and non-git commands); the segment-split/anchor behavior so a
    mutating verb merely mentioned inside a heredoc body does not trigger (the career-playbook
    [#442](https://github.com/brownm09/career-playbook/issues/442) lesson); the redirect handling where a
    `cd <path>` takes the *whole* command out of scope (including env-prefixed forms) while a
    `git -C <path>` / `--git-dir=<path>` / `--work-tree=<path>` target is captured by `_parse_git_prefix`
    and resolved against the canonical-root check (dev-env#576, ADR-071 Amendment 2 — with the
    engineering-journal checkout a temporary `_REDIRECT_TARGET_ALLOWLIST` carve-out); and the override
    token's anchored-prefix requirement
    (`ALLOW_CANONICAL_MUTATE=1` as a genuine leading prefix bypasses, while the same string merely mentioned
    inside a commit message argument does not). End-to-end `main()` tests drive the real hook over stdin
    against a real throwaway `git init` repo: a mutating command from a canonical (non-worktree) checkout —
    the concurrent-session HEAD-thrashing collision the hook exists to prevent
    ([dev-env#453](https://github.com/brownm09/dev-env/issues/453)) — is blocked with the reason on stderr
    and empty stdout; read-only commands and `pull --ff-only` are allowed while bare `pull` is blocked; any
    command from a worktree-pattern cwd is allowed (out of scope — ADR-024's hook covers that surface); the
    override token bypasses the block; and a non-git cwd, malformed JSON, non-dict JSON (`[]`, `"x"`, `123`,
    `null`), missing/empty `cwd`, and a non-Bash `tool_name` all fail open. A `-C`/`--git-dir`/`--work-tree`
    redirect *into* a canonical root from elsewhere IS now covered and tested (dev-env#576, ADR-071
    Amendment 2 — e.g. `git -C <canonical> checkout` from a worktree cwd blocked, `git -C <journal>` allowed
    by the carve-out, `git --work-tree=<canonical> commit` blocked); the sole remaining documented v1 gap is
    a bare `cd` *into* a canonical root, a deliberate scope limitation
    ([ADR-071](docs/adr/071-canonical-checkout-mutate-guard-hook.md)).

    Also exercises `is_mutating_gh_segment()` ([ADR-071 Amendment 1](docs/adr/071-canonical-checkout-mutate-guard-hook.md);
    dev-env#558): `gh pr merge --delete-branch`/`-d` (any flag order, other flags present) is classified as
    mutating — it checks out the base branch and deletes the local branch locally, the same silent-HEAD-thrash
    harm model reached through a `gh` invocation instead of a `git` verb — while a bare `gh pr merge` or
    `gh pr merge --squash` (no delete-branch flag, remote-API-only) stays classified as safe; the same
    heredoc-mention and anchored-override-prefix guarantees apply to the `gh` classifier as to the `git` one.
    End-to-end: `gh pr merge --delete-branch`/`-d` from a canonical (non-worktree) checkout is blocked (exit 2)
    with the matched command named in the reason; the same command from a worktree-pattern cwd is allowed
    (out of scope, unchanged); a bare `gh pr merge`/`--squash` is allowed from a canonical root; and the
    override token bypasses the block.

    ```bash
    py -3 claude/scripts/tests/test_canonical_mutate_guard.py
    ```

34. **merge-stale-pr self-test** — required when changing `claude/scripts/merge-stale-pr.sh`. Drives the
    REAL script against throwaway fixture repos (a bare "origin" + a working clone standing in for the
    shared engineering-journal checkout) with `gh` stubbed — no network, no auth. Asserts the Step 4
    orphaned-draft-file commit touches only the files it just deleted, never a file already staged by a
    simulated concurrent session in the same shared checkout (the explicit-pathspec `git add --` / `git
    commit --` safety fix from [dev-env#461](https://github.com/brownm09/dev-env/pull/461)); that a clean
    branch with no orphaned drafts skips Step 4 without a spurious commit and runs to completion; that
    multiple orphaned drafts across different directories are all included in one commit (guards the
    `"${DRAFT_FILES[@]}"` array handling); and that a missing composed-journal file plus a declined prompt
    aborts before any mutation. Rebase and push (Step 5) run for real against the fixture remote; only
    `gh pr view` (Step 1) and `gh pr merge` (Step 6) are stubbed.

    ```bash
    bash claude/scripts/tests/test-merge-stale-pr.sh
    ```

35. **worktree-canon shared-module test** — required when changing `claude/scripts/_worktree_canon.py`.
    Exercises the pure `canonical_root_from_worktree()` / `canonical_repo_root()` helpers offline (no
    I/O): pins the shared `_WORKTREE_RE` match (forward-slash, backslash, a cwd nested below the
    worktree name, POSIX paths) side by side with each function's own no-match contract —
    `canonical_root_from_worktree` returns `None` (post-tool-use.py's `load_config()` uses this to know
    whether to fall through to the sibling-worktree git fallback, which is not shared here), while
    `canonical_repo_root` returns the input unchanged (reconcile-project-board.py's `default_repo_root()`
    always has a real path and would crash `os.path.join` on `None`) — including on a sibling worktree
    path (e.g. `dev-env-188`, outside `.claude/worktrees/`, so the regex misses it by design) and on
    empty/`None` input. `post-tool-use.py`'s and `reconcile-project-board.py`'s own test files continue
    to exercise the same functions unchanged, through the module-attribute indirection `from X import Y`
    preserves. [ADR-073](docs/adr/073-shared-worktree-canon-gh-project-modules.md)

    ```bash
    py -3 claude/scripts/tests/test_worktree_canon.py
    ```

36. **`_winsubp` shared-module test** — required when changing `claude/scripts/_winsubp.py`. Exercises the
    pure `_apply_windows_subprocess_defaults(kwargs)` helper offline (a plain dict in, no subprocess spawn,
    no mock): pins the `CREATE_NO_WINDOW` constant value, that `creationflags` is OR-merged (not overwritten)
    with any existing value (the dev-env#297 behavior, now covered for the first time), that
    `encoding="utf-8", errors="replace"` is defaulted onto a call requesting text mode (`text=True`, the
    legacy `universal_newlines=True`, or `errors=` alone — Popen enters text mode whenever any of
    `encoding`/`errors`/`text`/`universal_newlines` is truthy) with no `encoding=` of its own, that
    binary-mode calls are left untouched, that an explicit `encoding=` (or `errors=`) from the caller is
    never overridden, and that the function mutates and returns the *same* dict object — an invariant the
    patched `Popen.__init__` relies on since it doesn't reassign the return value. A second check in
    `claude/scripts/tests/test_pyw_stdio.py` reproduces the exact dev-env#503 crash end-to-end under a
    real `pyw -3` child: byte `0x9d` (unmapped in cp1252, the Windows default codepage) is written by a
    grandchild process and asserted to decode as U+FFFD rather than being silently lost (the reader-thread
    exception does not propagate to the caller — see that test's docstring for the mechanism). Originally
    motivated by a crash reading `gh project item-add`'s output in `post-tool-use.py`'s `add_to_project` —
    independently fixed by PR #507's extraction of that function into `_gh_project.py` (explicit
    `encoding="utf-8"`) before this fix merged. This module's ongoing value is the repo-wide default for
    every *other* subprocess-using script's text-mode call that doesn't set its own encoding (e.g.
    `canonical_root_via_git` in `post-tool-use.py`, `confirm_merge_via_gh` in `_hookio.py`, and ~20 further
    scripts that all already import `_winsubp`) ([ADR-007 → 2026-07-02 follow-up](docs/adr/007-hook-command-invocation.md)).

    ```bash
    py -3 claude/scripts/tests/test_winsubp.py
    py -3 claude/scripts/tests/test_pyw_stdio.py
    ```

37. **numbering-collision check test** — required when changing
    `claude/scripts/pre-merge-numbering-check.py`. Two layers. Pure-helper tests exercise
    `extract_section`, `extract_testing_numbers`, `extract_adr_numbers`, `is_dev_env_repo`,
    `find_new_collisions`, `find_gaps`, `find_new_gaps`, `format_block_message`, and
    `is_pr_merge_command` offline (no disk, no network, no subprocess): pins that a number this
    branch newly claims (absent at the merge-base) which origin/main has also claimed is a
    collision, while an edit to an *existing* item is never flagged even when branch and main's
    text for it diverge; pins the CLAUDE.md Testing-section and docs/adr/INDEX.md table extractors
    against realistic samples (an indented code block inside an item, and the ADR table's
    header/separator rows, must not be mistaken for list entries); pins the dev-env repo-scope
    check against https, ssh, and other-repo origin URLs; pins that a gap already present on
    `origin/main` alone is never re-advised (only a gap this branch's own numbers create or
    extend is), so a long-standing legitimate hole doesn't nag on every future merge; and pins
    `is_pr_merge_command` (built on the shared `_hookio.scan_top_level`, matching
    `pr-merge-reminder.py`'s identically-named predicate) against a bare invocation, a
    `cd`-chained one, and a `gh pr merge` mentioned only inside a heredoc body, which must NOT
    match (dev-env#499). A second, end-to-end layer drives the real `main()` over stdin via
    subprocess against real throwaway git repos (a bare "origin" plus independent clones,
    mirroring `test_canonical_mutate_guard.py`'s Layer 2 pattern): a non-dev-env repo and a
    non-merge command are both a silent no-op, and a genuine cross-branch collision — discovered
    only because the hook's own `git fetch` pulls in a competing branch's already-pushed commit —
    blocks the merge (exit 2) with the reason on stderr and empty stdout. Unlike
    `pre-merge-message-check.py` (whose `main()` never shells out to git and has no such layer),
    this hook's git/subprocess orchestration is exercised directly rather than left as a
    documented gap ([ADR-074](docs/adr/074-pre-merge-numbering-collision-check.md); dev-env#516).

    ```bash
    py -3 claude/scripts/tests/test_pre_merge_numbering_check.py
    ```

38. **pre-merge-findings-gate test** — required when changing `claude/scripts/pre-merge-findings-gate.py`.
    Two layers, mirroring this hook family's established split. A pure-helper Python test exercises
    `is_pr_merge_command` offline (built on the shared `_hookio.scan_top_level`, matching
    `pre-merge-numbering-check.py`'s and `pre-merge-message-check.py`'s identically-named predicate —
    dev-env#519): pins a bare, `&&`-chained, and `cd`-chained top-level `gh pr merge` match, and that
    a non-merge command or a `gh pr merge` mentioned only inside a heredoc body (dev-env#499) does not match, so
    the hook's live `gh pr view` call is never paid for a command that never actually merges. A
    behavioral shell test drives the real hook end-to-end via the `MERGE_GATE_TEST_JSON` seam (no
    live `gh`, no network): pins the clean-review / open-findings-blocked / disposition-recorded /
    no-review-marker / gh-failure-fail-open / non-merge-command decision paths, and that `--repo` and
    `--repo=` both parse to the right repo in `_parse_merge_target`. The shell test file pre-dates
    this list — added here (alongside the new pure-helper file) to close the gap where it existed but
    was never cross-referenced ([ADR-028](docs/adr/028-all-findings-merge-gate.md), [ADR-039](docs/adr/039-merge-gate-findings-enforcement.md)).

    ```bash
    py -3 claude/scripts/tests/test_pre_merge_findings_gate.py
    bash claude/scripts/tests/test-merge-findings-gate.sh
    ```

39. **Crude command-substring-check regression test** — required when adding or changing any
    `claude/scripts/*.py` file. AST-based (not grep-based, so a comment or docstring merely
    *mentioning* the pattern is structurally invisible to it, not just filtered — see
    [ADR-050](docs/adr/050-shared-hookio-sibling-hook-fixes.md) Amendment 11) repo-wide gate
    against the recurring `if "<cli-invocation>" not in command: return False` false-positive
    class (Amendments 5/6/9/10/12): a raw substring test matches literal text inside a heredoc body,
    a quoted argument, or a `$()` subshell as if it were a real top-level statement. Generalizes
    beyond the ADR's three originally-named literals (`gh pr merge` / `gh pr create` / `git push`)
    to flag *any* string-literal `in`/`not in` check against a variable named `command` — this is
    what caught two previously-untracked checks of the identical shape in
    `stub-push-archive-reminder.py` (dev-env#534), since converged onto `scan_top_level` (dev-env#539,
    Amendment 12), that neither Amendment 9's nor Amendment 10's own three-literal grep would have
    found. A small, auditable `_KNOWN_EXCEPTIONS` allowlist (matched on file + literal text, not line
    number) covers pre-existing, not-yet-fixed debt — currently empty, since Amendment 12 converged
    the last two tracked entries — and the test fails on both a new unlisted offense and a stale
    listed exception whose underlying check has since been fixed, so the allowlist can't silently rot.

    ```bash
    py -3 claude/scripts/tests/test_no_crude_command_substring_checks.py
    ```

40. **journal-shard-write-advisory test** — required when changing
    `claude/scripts/journal-shard-write-advisory.py`. This is the write-time PostToolUse
    (Write/Edit/Bash) hook that validates engineering-journal shards against the schema at the
    moment they're written, rather than waiting for the next day's compose gate (item 25) to catch
    them (dev-env #556, [ADR-081](docs/adr/081-write-time-journal-shard-validation-hook.md)).
    Exercises every pure helper offline: `classify_shard_path` (canonical/backslash/Git-Bash forms,
    a shard nested under a Claude-managed journal worktree, the `engineering_journal` underscore
    spelling, both shard kinds, and the non-matches — legacy `open-prs.jsonl`, a non-journal repo, a
    journal path missing `sessions`, `.stub.md`); `extract_candidate_tokens` (the redirect / heredoc /
    `node -e`-quoted-string / multi-path `git add` / `rm -f` command shapes, the legacy-file
    non-match, and the 20-item cap); `extract_base_dirs` / `resolve_candidates` (cwd first, harvested
    `git -C`/`cd` directories, the constant journal-repo fallback, all via an injected `isfile` so
    they run fully offline); `validate_shard_bytes` (a healthy shard; missing fields in schema order;
    a BOM reported alongside missing fields, not instead of them; the open-PR non-numeric-filename
    and stem-vs-embedded-`pr` mismatch checks — reusing `_journal_shards.shard_pr_number`, computed
    once against the real path to avoid a `Path.stem` double-strip bug a naive re-derivation from the
    stem string would hit; an empty shard; non-JSON-object content); `format_advisory` (multi-file
    aggregation, both schema templates present, and an `.isascii()` + `.encode("cp1252")` pin per
    `test_posttooluse_inert_advisory.py`'s precedent); and `candidate_paths` (Write/Edit passthrough,
    Bash harvest, other tools and missing `tool_input` yielding nothing). `collect_problems` is
    exercised against real `tempfile.TemporaryDirectory()` fixtures (its only impure surface is the
    filesystem, matching `test_hookutil.py`) including the size-cap skip. `main()`'s stdin plumbing is
    not covered (pure-helper convention). The token-harvest regexes are deliberately **not**
    `_hookio.scan_top_level`-anchored — this hook validates on-disk data, not command intent, so a
    path merely mentioned inside a heredoc/quoted argument/subshell is harmless to check — which also
    means item 39's AST gate does not apply to this file's `re.findall` calls (confirmed: they are not
    `Constant-str in/not in Name('command')` Compare nodes).

    ```bash
    py -3 claude/scripts/tests/test_journal_shard_write_advisory.py
    ```

41. **`_journal_schema` shared-module test** — required when changing
    `claude/scripts/_journal_schema.py`. Exercises every export offline: the moved
    `missing_required_fields` / `find_entries_missing_fields` / `parse_manifest_text` (parity with
    the coverage item 25 already pinned pre-extraction); `missing_open_pr_fields` against
    `OPEN_PR_REQUIRED_FIELDS` (all-present, one missing, and the `summary`-instead-of-`topic` shape
    from the 2026-07-02 meta-shard incident); and `decode_shard_bytes` (plain UTF-8; a UTF-8 BOM,
    text returned past the BOM with the problem named rather than surfacing as an opaque line-1 JSON
    parse failure; UTF-16 LE/BE BOMs; invalid UTF-8; empty bytes). This module has no `main()` — it
    is the shared schema/validation core for both `validate-manifest.py` (item 25) and
    `journal-shard-write-advisory.py` (item 40), so a schema change here is validated by both gates
    without duplicating the rule ([ADR-081](docs/adr/081-write-time-journal-shard-validation-hook.md)).

    ```bash
    py -3 claude/scripts/tests/test_journal_schema.py
    ```

42. **`_bash_state` shared-module test** — required when changing `claude/scripts/_bash_state.py`.
    Exercises every pure helper offline (tmp dirs via an injected `scratch=` parameter, mirroring
    `_hookutil.py`'s test convention — no real `~/.claude/scratch`): `state_path` correctness with
    and without an override; a `write_state`/`read_state` round-trip; `read_state` returning `None`
    on a missing file, malformed JSON, or a JSON array (non-dict) rather than raising; `write_state`
    swallowing an `OSError` when the scratch path is unwritable (a file occupying where a directory
    is expected); `cleanup_stale_state` removing files older than `MAX_AGE_DAYS` while keeping fresh
    ones and files that don't match the `bash_state_*.json` glob, and not raising when the scratch
    dir is absent (mirrors `_hookutil.cleanup_stale_sentinels` — this module was the only
    per-session-file producer in the codebase without this, caught in `/review`); and
    `format_drift_warning`'s six decision cases — `None` recorded state, both current values `None`
    (the checkpoint's own git read failed/timed out — nothing to meaningfully compare, and firing
    here would show an unchanged cwd on both the "was" and "now" lines, another `/review` catch), an
    unchanged `(repo_root, branch)` pair (including a same-repo `cwd`-only change, which must **not**
    fire — the key precision property this module exists for), a `repo_root` change (the
    worktree-silently-replaced-by-canonical-root incident shape), a branch-only change with the same
    `repo_root` (the same-repo-branch-reverted incident shape), and a recorded state with `None`
    fields rendering a `<unknown>` placeholder instead of raising. Backs
    `post-tool-use-cwd-track.py`'s state writes and the drift check in
    `pre-commit-branch-check.py` / `pre-pr-create-check.py` / `pre-merge-branch-check.py`
    ([ADR-085](docs/adr/085-bash-repo-branch-drift-detection.md); dev-env#573). `current_repo_state()`
    — the single combined `git rev-parse --show-toplevel --abbrev-ref HEAD` call shared by all four
    consuming files (extracted here after three near-duplicate copies existed briefly and one already
    diverged in its failure-mode return value) — shells out and is not covered here (pure-helper
    convention).

    ```bash
    py -3 claude/scripts/tests/test_bash_state.py
    ```

43. **pre-commit-branch-check test** — required when changing `claude/scripts/pre-commit-branch-check.py`.
    Exercises the pure `is_git_commit_command()` detector (bare and `&&`-chained `git commit`
    matches; an unrelated git command does not) and the new `build_message()` formatter added for
    the dev-env#573 drift-warning integration: unchanged pre-existing output when there is no drift,
    the drift warning appended on its own line when present, and a `None` branch (detached HEAD /
    git failure) rendering a display placeholder rather than the raw `None`. The repo/branch lookup
    itself is `_bash_state.current_repo_state()` (shared with the other two checkpoint hooks — see
    item 42), not a function local to this file. This test file pre-dates this list — added here
    alongside the drift-check change ([ADR-085](docs/adr/085-bash-repo-branch-drift-detection.md)).

    ```bash
    py -3 claude/scripts/tests/test_pre_commit_branch_check.py
    ```

44. **pre-pr-create-check test** — required when changing `claude/scripts/pre-pr-create-check.py`.
    Exercises the new `build_checklist()` formatter added for the dev-env#573 branch-display and
    drift-warning integration: the numbered checklist plus a branch/repo display line always
    present, `None` branch/repo_root rendering display placeholders, the drift warning appended
    after the branch-display line when present, and — the fragility this test specifically guards
    against — that `baseline_line`'s pre-existing hardcoded "4." numbering and `doc_warning`'s
    relative order are unchanged, since the new branch/drift content is inserted *between* the
    numbered checklist and those two conditionally-numbered lines rather than into the numbered
    sequence itself. `_doc_reconciliation_warning()` and `_baseline_advisory()` shell out to git /
    read files and are not covered (pure-helper convention; this file's pre-existing untested logic
    is not backfilled per the Test Coverage Gate, [ADR-022](docs/adr/022-test-coverage-gate-before-pr.md)
    — only the new behavior is tested); the repo/branch lookup itself is
    `_bash_state.current_repo_state()` (shared with the other two checkpoint hooks — see item 42).
    ([ADR-085](docs/adr/085-bash-repo-branch-drift-detection.md))

    ```bash
    py -3 claude/scripts/tests/test_pre_pr_create_check.py
    ```

45. **pre-merge-branch-check test** — required when changing `claude/scripts/pre-merge-branch-check.py`.
    Exercises `is_pr_merge_command()` (built on the shared `_hookio.scan_top_level`, matching the
    identically-named predicate in `pre-merge-message-check.py` / `pre-merge-numbering-check.py` —
    dev-env#519): a bare and a `cd`-chained `gh pr merge` match, a `gh pr merge` mentioned only
    inside a heredoc body does not (dev-env#499), and an unrelated `gh` command does not. Also
    exercises `build_message()`: no drift is a single line with no warning appended, a drift warning
    appends on its own line after the branch-display text, and `None` branch/repo_root render
    display placeholders. The repo/branch lookup itself is `_bash_state.current_repo_state()`
    (shared with the other two checkpoint hooks — see item 42), matching item 43's identical scope
    decision for the sibling commit-time hook. ([ADR-085](docs/adr/085-bash-repo-branch-drift-detection.md))

    ```bash
    py -3 claude/scripts/tests/test_pre_merge_branch_check.py
    ```

46. **check-journal-compose-liveness test** — required when changing
    `claude/scripts/check-journal-compose-liveness.py`. Exercises the pure
    `has_uncommitted_target_date_changes()` and `format_abort_message()` helpers offline — no
    subprocess, no git, no filesystem: pins that an untracked (`??`) or modified (` M`) stub/manifest
    shard for the target date is dirty, that a different date or a non-date-named path (e.g. an
    open-PR shard) is clean, that a renamed path (`OLD -> NEW`) checks the destination side, that
    blank lines and multi-line mixed output are handled correctly, that a date-marker match without
    the shard suffix is clean, that `main()` rejects a malformed date (including an unsubstituted
    `YYYY-MM-DD` placeholder left in `journal-compose/SKILL.md`) with exit 2, and that the abort
    message is ASCII (and therefore cp1252-safe), matching the encoding-safety convention pinned
    elsewhere in this repo's advisory-emitting scripts (items 18, 40). The script itself stays pure
    I/O (porcelain text via stdin, not a live `git status` call) so this test needs no subprocess
    mocking — the two callers (`journal-compose-with-retry.sh`'s primary check and
    `journal-compose/SKILL.md` Step 0.6's defense-in-depth check) both run `git status --porcelain`
    themselves and pipe the output in ([ADR-086](docs/adr/086-journal-compose-liveness-guard.md)).

    ```bash
    py -3 claude/scripts/tests/test_check_journal_compose_liveness.py
    ```

47. **disk-space-check test** — required when changing `claude/scripts/disk-space-check.py`.
    Exercises the pure `classify_free_space(free_gb, warn_gb, act_gb)` helper extracted from
    `main()`'s inline threshold if/elif (dev-env#592,
    [ADR-087](docs/adr/087-pretooluse-disk-space-check.md)): pins the three bands (ample space is
    `"ok"`, mid-range is `"warn"`, low space is `"act"`) and both threshold boundaries as inclusive
    on the healthier side (`free_gb == WARN_GB` is `"ok"`, `free_gb == ACT_GB` is `"warn"`, never
    `"act"`) — the exact behavior the inline if/elif already had, now pinned rather than only
    implicit. This hook is now registered under both `UserPromptSubmit` and `PreToolUse(Bash)`,
    reusing the same script and the same per-session marker-file gate for both (ADR-087); the
    `disk_usage` syscall, marker-file I/O, and the detached `reclaim-worktree-disk.py` spawn are not
    covered here (pure-helper convention, matching item 9's `install_decision()` precedent).

    ```bash
    py -3 claude/scripts/tests/test_disk_space_check.py
    ```

48. **stop-tile-enumeration-gate test** — required when changing
    `claude/scripts/stop-tile-enumeration-gate.py`. Two layers, mirroring this hook family's
    established split ([ADR-088](docs/adr/088-state-keyed-tile-enumeration-gate.md); dev-env#599).
    Pure-helper tests exercise the detection/decision core offline (no stdin, network, gh, or disk):
    `session_merged_prs` across all three merge paths — a `gh pr merge` success marker, a
    `gh api .../pulls/N/merge` with `"merged":true`, and the auto-merge case (a `--auto` enqueue or a
    `gh pr create`, then a later `gh pr view` MERGED state, correlated with a PR the session acted on) — plus the
    non-matches that must NOT count: a `gh pr view` MERGED for a PR the session never acted on (an
    unrelated old PR merely inspected), a queued `--auto` alone with no MERGED confirmation, a
    `gh pr merge --help` (no marker, no PR — dev-env#485 shape), and a `gh pr merge` mentioned only
    inside a heredoc body or `$()` subshell (the dev-env#499 `split_top_level` anchoring class);
    `enumeration_recorded` (a `spawn_task` tool call; the prescribed "Follow-ups considered" /
    "-> not tiled, because" / "-> tiled" text, both ASCII `->` and Unicode `→`) INCLUDING the
    bare-"no follow-ups" rejection that is the lifting-logbook#700 skip; `skip_override` (a genuine
    user "skip tiles" / "no tiles" / "don't spawn tiles", and that the phrase inside a `tool_result`
    does NOT waive); the `evaluate()` composition (fire / resolved / no-op, lowest-PR determinism);
    `iter_bash_calls` id-pairing (parallel calls don't cross); and the reminder's ASCII/cp1252
    encodability. It also pins the PR #604 review regressions: the observed PR number preferring the JSON
    `"number"` (A1), that a compact-summary / `isMeta` record restating "skip tiles" does NOT waive the
    gate (A2), that malformed / non-dict records neither raise nor silently disable it (A3), and that the
    auto-merge correlation is `(repo, number)`-aware so a same-numbered PR in another repo does not
    false-fire (A4). A behavioral layer drives the real hook end-to-end over stdin via subprocess against
    a synthetic transcript, with HOME/USERPROFILE pointed at a temp dir so the once-per-session
    sentinel is isolated: pins merged-no-enum -> exit 2 with the reason on stderr and empty stdout,
    merged+enum and no-merge -> exit 0, the `stop_hook_active` loop guard -> exit 0, and that the
    sentinel suppresses a second fire. `main()`'s stdin/sentinel plumbing beyond the end-to-end runs
    is not separately unit-tested (pure-helper convention). The transcript-record readers
    (`load_records` / `_parse_records` / `iter_bash_calls` / `_result_text` / `_content_items`) now
    live in `_hookutil` ([ADR-090](docs/adr/090-shared-transcript-readers-hookutil.md)) — the gate
    imports the three it uses (`_content_items`, `_parse_records`, and the shared `iter_bash_calls`,
    aliased) and wraps the last in a thin 2-tuple adapter (it never needs `cwd`), so
    `session_merged_prs` and these tests are unchanged by that extraction.

    ```bash
    py -3 claude/scripts/tests/test_stop_tile_enumeration_gate.py
    ```

49. **setup-link-loop test** — required when changing `setup.sh`'s `CLAUDE_FILE_LINKS` /
    `CLAUDE_DIR_LINKS` arrays or its `link_claude_windows()` / `link_claude_unix()` functions.
    Sources `setup.sh` unmodified — a guard around the OS-dispatch block at the bottom makes this
    safe, since sourcing only defines functions/arrays without executing anything — and exercises
    the extracted link functions with `win_link`/`ln` stubbed to a call log, so the test needs no
    Administrator/Developer Mode privilege and never touches a real `~/.claude` or global git
    config: pins the shared `CLAUDE_FILE_LINKS` (`CLAUDE.md`, `settings.json`) and
    `CLAUDE_DIR_LINKS` (`scripts`, `skills`, `hooks`, `templates`) enumeration against
    [ADR-003](docs/adr/003-config-in-version-control.md)'s table, and that
    `link_claude_windows()` / `link_claude_unix()` each call their link primitive for exactly the
    expected 8 targets (the two arrays, plus the separately-linked `routines` junction and
    `~/bin`) in order, against a real throwaway `$HOME` — so the unstubbed `mkdir -p` calls are
    verified for real too. `setup_windows()`'s UAC elevation gate, the soft-prereq warnings,
    `set_hooks_path()`'s global `git config` mutation, and `win_link`'s actual `cygpath`/`mklink`
    invocation are out of scope by design ([dev-env#614](https://github.com/brownm09/dev-env/issues/614)).

    ```bash
    bash claude/scripts/tests/test-setup-link-loop.sh
    ```

50. **journal-stop-check test** — required when changing `claude/scripts/journal-stop-check.py`.
    Two layers, mirroring this hook family's split ([ADR-091](docs/adr/091-journal-stop-check-archive-reminder-blocking.md);
    dev-env#622). Pure/fixture-helper tests exercise the changed surface offline: `archive_reminder_message()`
    is ASCII/cp1252-encodable (so the exit-2 stderr text cannot vanish under Claude Code's cp1252
    hook-output pipe on Windows — mirroring items 18/40) and names the `ccd_session_mgmt__archive_session`
    MCP tool + the `list_sessions` lookup; `parse_stop_hook_active()` returns True only when the payload
    sets the flag and False on false / missing / empty / malformed / non-dict stdin (so a parse hiccup
    never suppresses a genuine first Stop); and `consume_stub_pushed_sentinel(sentinel=tmp)` returns the
    reminder and deletes a present flag (the consume-on-read one-shot guard for the exit-2 block), returns
    None on a second read, and None on an absent flag. A behavioral layer drives the real hook end-to-end
    over stdin via subprocess with HOME/USERPROFILE pointed at a temp dir (SENTINEL isolated from the real
    `~/.claude/scratch`, mirroring item 48): a present flag + `stop_hook_active:false` blocks the stop
    (exit 2, reminder on **stderr**, **empty stdout** — Claude Code shows a Stop hook's stderr on exit 2,
    not stdout) and consumes the flag; no flag exits 0 (advisory path, fail-closed against the tmp journal
    repo); a present flag + `stop_hook_active:true` exits 0 (loop guard, no re-block) and **preserves** the
    flag (never consumed without delivery). `main()`'s advisory branches (stale-draft / unmerged-branch /
    orphan cleanup) shell out to git and are not separately unit-tested (pure-helper convention) — the
    end-to-end no-flag run exercises their fail-closed path.

    ```bash
    py -3 claude/scripts/tests/test_journal_stop_check.py
    ```

## Observability

dev-env has **no long-running runtime to instrument** — it is a configuration repo whose
"runtime" is short-lived hook scripts and skills invoked by Claude Code. There is no
application logger, no log aggregation, and no traces. This section exists to satisfy the
global per-project `## Observability` requirement and to tell the *Plan-then-optimize → Pass 3*
Observability dimension what to verify here instead.

Hooks and scripts observe the Claude Code hook contract rather than a logging stack:

- **Diagnostics go to stderr; exit codes carry meaning.** Blocking hooks emit to stderr and
  use per-session marker files; non-blocking advisories exit 0. See
  [ADR-027](docs/adr/027-userpromptsubmit-blocking-hook-conventions.md) and
  [ADR-007](docs/adr/007-hook-command-invocation.md) for the invocation and output model.
- **The equivalent of "is it observable / correct at its boundaries" is the verification
  suite in `## Testing` above** — the hook-script syntax check, the `pyw -3` stdio test, and
  the pre-push self-test. A change to a hook or script must keep those green.

What the Pass 3 Observability dimension should verify for a dev-env change: any new or changed
hook/script routes diagnostics to stderr (not stdout, which Claude Code consumes), chooses its
exit code deliberately (0 = advisory, non-zero = blocking), and is covered by the relevant
`## Testing` self-test. Pure docs/config changes (like this one) answer "N/A — no runtime."

## Documentation Maintenance

When a PR modifies any of the paths below, update the listed reference docs **in the same PR**.

| Change | Required updates |
|---|---|
| Add / remove / rename a skill in `claude/skills/` | Skills table in `README.md` + `docs/REFERENCE.md` Skills section |
| Add / remove / rename an event-driven hook script in `claude/hooks/` (or a script in `claude/scripts/` that fires on a Claude Code hook event) | Hooks table in `README.md` + `docs/REFERENCE.md` Hooks section |
| Add / remove / rename a utility/on-demand script in `claude/scripts/` (no hook event — invoked manually or from a skill) | Utilities table (On-demand scripts) in `docs/REFERENCE.md` |
| Add / remove / rename a routine in `claude/routines/` | Routines table in `README.md` + `docs/REFERENCE.md` Routines section |
| Change `hook-config.json` schema (new field, removed field, type change) | Configuration subsection in `docs/REFERENCE.md` |
| Change a skill's invocation syntax or options | Skill entry in `docs/REFERENCE.md` |
| Rename or move any file linked in `README.md` or `docs/REFERENCE.md` | Update the link in both files |

## Dev-Env Architecture

`~/.claude/` is split between two categories. Treat them differently.

**Owned by `brownm09/dev-env` — symlinked, version-controlled:**

| Path | dev-env source |
|---|---|
| `~/.claude/CLAUDE.md` | `claude/CLAUDE.md` |
| `~/.claude/scripts/` | `claude/scripts/` (directory junction) |
| `~/.claude/skills/` | `claude/skills/` (directory junction) |
| `~/.claude/hooks/` | `claude/hooks/` (directory junction) |
| `~/.claude/routines/` | `claude/routines/` (directory junction) |
| `~/.claude/templates/` | `claude/templates/` (directory junction) |
| `~/.claude/settings.json` | `claude/settings.json` |

**Machine-local only — never commit:**

`scratch/`, `projects/`, `sessions/`, `backups/`, `ide/`, `plans/`, `shell-snapshots/`

**Rule:** Any addition or modification to a dev-env-owned artifact — new hook script, new skill, settings change, CLAUDE.md edit — must be committed to `brownm09/dev-env` via branch and PR before the session ends. Do not leave global tooling as untracked files.

**Rule:** The canonical dev-env worktree (`~/Git/dev-env`) must stay on `main` at all times. All dev-env changes go through a separate worktree (use `EnterWorktree` or `git worktree add`). Reason: `~/.claude/settings.json` and `~/.claude/scripts/` are symlinked/junctioned to the canonical worktree's working tree — checking out a feature branch there makes newly merged hooks and scripts invisible until the worktree returns to main. `dev-env-sync` will warn on every prompt when this rule is violated.

**Routines note:** `~/.claude/routines/` is a read-only junction mirror of `claude/routines/` — it exists so a routine can read its own canonical source at run time, not so the scheduler auto-registers it. The `scheduled-tasks` MCP tool never reads or writes through it: that tool owns a **separate, real, non-linked** directory, `~/.claude/scheduled-tasks/`, with one subdirectory per *registered* task (reusable routines and one-off/manual tasks side by side — deliberately not a version-controlled mirror, since one-offs have no business in git). Authoring or editing `claude/routines/<name>/SKILL.md` and merging it does **not** update a live task. After merging, separately call `create_scheduled_task` / `update_scheduled_task` (`scheduled-tasks` MCP tool) so the live prompt matches. Prefer the self-referencing pattern `weekly-memory-audit` already uses: have the live prompt read its own canonical `claude/routines/<name>/SKILL.md` at run time and follow it when present, falling back to an embedded copy — this keeps the live task self-healing against future drift instead of silently diverging, as `daily-journal-compose` did until [dev-env#464](https://github.com/brownm09/dev-env/issues/464). See [ADR-003 amendment](docs/adr/003-config-in-version-control.md) and [dev-env#344](https://github.com/brownm09/dev-env/issues/344).

**Routine authoring — sync-to-main preamble.** Any routine that reads repo-resident files (a skill, a context file, a queue file) at run time must invoke the `sync-routine-worktree` skill as Step 0, before reading any of those files. Scheduled tasks fire into Claude-managed worktrees whose branches were cut from whatever `main` was at worktree creation; without an explicit sync the routine reads stale files or aborts because a recently-merged file is missing on the worktree branch. The sync skill handles fetch, branch-class-aware sync (Claude-managed worktree / `main` / other), file existence verification, and abort-with-push-notification on conflict — routines pass `REPO`, `VERIFY_FILE`, and `PREFIX` and treat the return as a guard. See `claude/skills/sync-routine-worktree/SKILL.md` and `claude/routines/nightly-cover-letters/SKILL.md` for the canonical pattern. Rationale: `docs/adr/013-sync-routine-worktree-skill.md`.

**Doc-reconciliation checkpoint** (three moments, same as ADR-warrant): (1) immediately after a plan is approved; (2) immediately after `gh pr create` returns; (3) immediately before `gh pr merge`. At each checkpoint, ask: does this change add, remove, rename, or modify the behavior of a skill, hook, script, or routine? If yes, verify that `README.md` and the Documentation Maintenance table above are satisfied in this PR. **If warranted updates are missing, add them before merging.** Rationale: `docs/adr/019-doc-reconciliation-enforcement.md`.

**Downstream artifacts that name specific dev-env skills/hooks/routines** (update in the same PR as a rename or retirement):

- `tech-leadership-reference/ai-adoption/ai-adoption-readiness-framework.md` — Appendix C names `/propose`, `/review`, `/journal-compose`, `/research`, and the `prune-stale-worktrees` and nightly journal compose routines as live-state evidence.

**Repo path:** `C:/Users/brown/Git/dev-env`

## GitHub Project

All new dev-env issues must be added to the **Dev Env** project and given an Impact rating and Why description before work begins. The general single-select option-mutation hazard that applies to **every** project is documented in the global `claude/CLAUDE.md` → Dev-Env & Project Boards section; the dev-env-specific IDs and procedures are below.

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

**Workflow — automated via PostToolUse hook:** After `gh issue create` succeeds, `post-tool-use.py` adds the issue to project #3 and exits code 2, printing the exact `gh project item-edit` commands to set Impact and Why. **Run those commands immediately — before any file edits.**

**Fallback (if the hook did not fire or the item-add failed):** run the three steps manually. Requires project scope — add once if needed: `gh auth refresh -s project`. A *wholesale* non-fire — no `[project-hook]` output at all after `gh issue create`, and `spawn_task` chips also not rendering — most often means the session was launched as a background task / via `spawn_task`, where **every** PostToolUse hook is silently inert ([ADR-053](docs/adr/053-posttooluse-hooks-inert-in-background-sessions.md)); these manual steps are the recovery.

```bash
# 1. Add issue to project, capture item ID
TMPFILE="C:/Users/brown/.claude/scratch/tmp_item_$$.json"
gh project item-add 3 --owner brownm09 --url <issue-url> --format json > "$TMPFILE"
ITEM_ID=$(node -e "const d=JSON.parse(require('fs').readFileSync('$TMPFILE','utf8')); console.log(d.id);")
rm -f "$TMPFILE"

# 2. Set Impact   (08de2558=High  6320e8a6=Medium  d8a85c2f=Low)
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkNc --single-select-option-id <option-id>

# 3. Set Why (one sentence — the cost of not fixing it)
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTF_lAHOAjEKvM4BWKFezhRgkN0 --text "<why this matters>"
```

To look up an item ID by issue number `<N>` (e.g., to move status in a later session):

```bash
TMPFILE="C:/Users/brown/.claude/scratch/tmp_item_$$.json"
gh project item-list 3 --owner brownm09 --format json --limit 1000 > "$TMPFILE"
ITEM_ID=$(node -e "
  const d=JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));
  const item=d.items.find(i=>i.content&&i.content.number===<N>);
  console.log(item.id);
")
rm -f "$TMPFILE"
```

**Move status** — set the Status field (`PVTSSF_lAHOAjEKvM4BWKFezhRgkMY`) to In Progress (`47fc9ee4`) when work begins, Done (`98236657`) after the PR merges:

```bash
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkMY --single-select-option-id <status-option-id>
```
