# dev-env Test Suite — Per-Item Behavioral Detail

Moved verbatim from the root `CLAUDE.md` `## Testing` section (2026-07-17, ADR-114) so the
always-loaded instruction file carries only the index. **Item numbers here match the index
in [`CLAUDE.md` → `## Testing`](../CLAUDE.md) one-to-one** — keep the two in sync: a new
test adds an item in both files in the same PR (numbering is append-only and
collision-checked at merge time by `pre-merge-numbering-check.py`, ADR-074).

For a one-line navigational map of the test directory, see
[`claude/scripts/tests/README.md`](../claude/scripts/tests/README.md). To run everything:
`py -3 claude/scripts/run-hook-tests.py` (from the repo root).

## Items

1. **Hook-script syntax check** — run from the repo root to verify all hook scripts parse:

   ```bash
   py -3 -c "import ast,sys; [ast.parse(open(f,encoding='utf-8').read(),f) for f in sys.argv[1:]]" claude/scripts/*.py
   ```

   `ast.parse` is used instead of `py_compile` because the latter writes `.pyc` files into
   `claude/scripts/__pycache__/` as a side effect (see [dev-env#276](https://github.com/brownm09/dev-env/issues/276));
   neither `-B` nor `PYTHONDONTWRITEBYTECODE=1` suppresses that. On Windows, `python3` resolves
   to the Microsoft Store stub — use `py -3` (the Windows Python Launcher), see [ADR-007](adr/007-hook-command-invocation.md).

2. **`pyw -3` stdio verification** (Windows-only — confirms `pythonw.exe` honors parent-supplied
   pipes, the invariant ADR-007's 2026-06-01 decision relies on):

   ```bash
   py -3 claude/scripts/tests/test_pyw_stdio.py
   ```

3. **Pre-push hook self-test** — required when changing `claude/hooks/pre-push`. Drives the real
   hook against throwaway fixture repos with a stubbed `npm` and asserts the lockfile-drift guard's
   BLOCK / PASS / SKIP paths, working-tree restoration, and repo-hook chaining (see [ADR-036](adr/036-lockfile-drift-prevention.md)):

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
   ([dev-env#474](https://github.com/brownm09/dev-env/issues/474), ADR-049/ADR-050 amendment). Also
   (PR5 of dev-env#717, [dev-env#736](https://github.com/brownm09/dev-env/issues/736)) pins
   `status_label()` (renamed from `status_emoji`) and the `format_snapshot()` static template as
   `.isascii()`, plus — the real end-to-end guarantee — that
   `ascii_sanitize(format_snapshot(<non-ASCII action>))` is `.isascii()` (the action column
   interpolates arbitrary Unicode, so wire-safety lives in `emit_block`, not `format_snapshot`): the
   emoji and `≤` were ASCII-ified (OVER/NEAR/OK tokens, `<=`) and all four emissions moved onto
   `_hookout.emit_block` (exit-2 stderr, `ascii_sanitize` backstop + exit-code-safe `finally`), so a
   cp1252 encode crash can no longer flip exit 2→0 and silently drop the snapshot (the #670 pattern);
   `status_label`/`format_snapshot` were previously unexercised. The
   live usage API call is not covered (the repo avoids urllib mocks).

   ```bash
   py -3 claude/scripts/tests/test_usage_snapshot.py
   ```

9. **worktree-npm-install gate test** — required when changing `claude/scripts/worktree-npm-install.py`.
   Exercises the pure `install_decision()` helper offline (no disk, no network, no npm): pins the
   `proceed` / `reclaim-first` / `abort` decisions and the 10 GB / 5 GB threshold boundaries that gate a
   low-space install against silent ENOSPC truncation ([ADR-045](adr/045-pre-install-freespace-gate.md)).
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
    [ADR-050 Amendment 9](adr/050-shared-hookio-sibling-hook-fixes.md)) does not. Also (PR5 of
    dev-env#717, [dev-env#736](https://github.com/brownm09/dev-env/issues/736)) pins the `RECLAIM_MSG`
    constant (content + `.isascii()`): the post-merge status now emits via
    `_hookout.emit_advisory(audience="user")` (a systemMessage toast) rather than a raw exit-0 stderr
    print (invisible on PostToolUse); the channel itself is enforced by the output-contract gate
    (item 61). The detached reclaim spawn is not covered (it shells out).

    ```bash
    py -3 claude/scripts/tests/test_post_pr_merge_reclaim.py
    ```

11. **memory-write-advisory test** — required when changing `claude/scripts/memory-write-advisory.py`.
    Exercises the pure `should_advise_memory_write()` predicate offline (no stdin, no Claude session):
    pins that a durable memory write with no immortalization link advises, while a write that already
    cites an issue/ADR/`CLAUDE.md`, the `MEMORY.md` index, a non-`memory/` path, a non-`.md` file, or the
    `Edit` tool stays silent ([ADR-048](adr/048-memory-immortalization-issue-pairing.md)). The stdin
    plumbing and exit-2 emission are not covered (pure-helper convention).

    ```bash
    py -3 claude/scripts/tests/test_memory_write_advisory.py
    ```

12. **post-tool-use test** — required when changing `claude/scripts/post-tool-use.py`. Exercises the pure
    `read_command_output()`, `extract_github_url()`, `canonical_root_from_worktree()`, and
    `_canonical_root_from_common_dir()` helpers offline: pins that the real `stdout`-shaped Bash payload yields
    a non-empty output (the pre-fix `output` read was `""` — the [#377](https://github.com/brownm09/dev-env/issues/377)
    silent no-op), that the legacy `output` field still works, that the de-silenced no-URL path distinguishes a
    different-repo miss from a genuine empty ([ADR-049](adr/049-hook-payload-output-field.md)), that a
    Claude-managed worktree cwd whose `hook-config.json` is absent (a project that gitignores it, dev-env's own
    convention — not every project's, e.g. lifting-logbook tracks it) resolves the canonical checkout's
    config — verified end-to-end via a hermetic temp dir — and that the sibling-worktree `git --git-common-dir`
    output resolves to the canonical root ([ADR-052](adr/052-worktree-config-canonical-fallback.md)). Also
    exercises `is_issue_create_command()` / `is_pr_create_command()` (built on the shared
    `_hookio.scan_top_level`, [ADR-050 Amendment 5](adr/050-shared-hookio-sibling-hook-fixes.md)): the four
    dev-env#499 false-positive reproductions (heredoc-embedded commit body, quoted commit message, grep pattern
    argument, `--text` field value) for both PR- and issue-create, plus subshell/quote/cd-prefix/chained cases;
    and a `subprocess`-driven end-to-end pair (`_run_hook`, mirroring `test_worktree_path_check.py`'s pattern)
    pinning that the pre-existing `exit_code != 0` gate still short-circuits immediately after the detection
    swap, without either branch invoking a live `gh` call. Also exercises `is_issue_create_help_only()` /
    `is_pr_create_help_only()` (thin wrappers over the shared `_hookio.is_help_only()`,
    [ADR-050 Amendment 15](adr/050-shared-hookio-sibling-hook-fixes.md); dev-env#636 — the identical
    `--help` false-positive `is_merge_help_only` closes for `gh pr merge`, reproduced here for
    `gh issue create` / `gh pr create`): bare `--help`/`-h`, a real create, no invocation at all, and —
    via three `main()` end-to-end subprocess cases — that `gh issue create --help` and `gh pr create
    --help` each exit 0 silently, and that a help-only issue-create chained with a REAL pr-create still
    reaches the exit-2 "no GitHub URL found" path **and names the PR specifically** (`"PR created"`, not
    `"Issue created"`) — proving `main()` downgrades each create-flag independently rather than bailing
    out wholesale. Also exercises the dev-env#527 / [ADR-076](adr/076-live-fetch-project-hook-single-select-options.md)
    live-fetch of `single_select` field options: the pure `_parse_live_options()` response parser (valid, empty-options,
    null-node, and malformed-JSON shapes), the pure `_resolve_required_fields()` legacy-config normalization shared
    between rendering and live-fetch target discovery, `fetch_live_required_field_options()`'s field-selection logic
    (single_select-with-field_id only) via an injected fake `fetch_fn`, and `format_reminder()`'s three rendering
    branches — live data used and labeled, a failed fetch falling back to cached data labeled as possibly stale, and
    the no-`live_options`-passed case matching pre-#527 output exactly. The live `gh project item-add` call, the
    `subprocess.run` in `canonical_root_via_git()`, and the live `gh api graphql` call in `fetch_live_field_options()`
    are not covered (the repo avoids subprocess mocks) — the last was instead verified once by hand against dev-env's
    own live Impact field during development of the #527 fix. Also (dev-env#838) exercises
    `extract_repo_flag`'s migration onto the shared `_repo_target` resolver: its `--repo`
    extraction now delegates to `issue_create_args`/`create_args` + the (widened)
    `repo_from_flag` instead of a private `_REPO_FLAG_RE` over `split_top_level` segments — new
    sabotage-verified cases pin that a `--repo` on a backslash-continued `gh pr create` /
    `gh issue create` line resolves (missed by the old segment loop) and that a decoy
    `--repo`/URL buried in a quoted `--title`/`--body` value does not hijack the real trailing
    flag (captured by the old unmasked regex); the pre-existing dev-env#544 URL/host-prefix
    cases now pass unchanged through the widened shared `repo_from_flag`. See
    [ADR-111 Amendment 1](adr/111-shared-repo-target-resolution.md).

    ```bash
    py -3 claude/scripts/tests/test_post_tool_use.py
    ```

13. **_hookio shared-read test** — required when changing `claude/scripts/_hookio.py`. Exercises the pure
    `read_command_output()` and merge-marker helpers (`output_has_merge_marker` / `merge_pr_number_from_output`) offline (no network, no gh): pins that the real `stdout`/`stderr`-shaped
    Bash payload yields the command output (the pre-#380 `output` read was always `""`), that stdout and
    stderr are joined, that the legacy `output` field is still honored as a fallback, and that a missing /
    empty / `None` / non-dict `tool_response` yields `""` without raising. Also exercises the pure
    `should_confirm_via_gh(exit_code, output)` predicate: only a non-zero exit code with no marker present
    is worth a live `gh pr view` confirmation (dev-env#489, [ADR-050 amendment 3](adr/050-shared-hookio-sibling-hook-fixes.md));
    a clean exit or a marker already found never pays the network call. Also exercises `effective_merge_dir()`
    (cd-chain / cwd resolution, [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md)) and
    `scan_top_level()` — the stack-based top-level-statement parser shared with `pr-merge-reminder.py` and
    `post-tool-use.py` ([ADR-050 Amendment 5](adr/050-shared-hookio-sibling-hook-fixes.md)): anchored-match
    semantics, non-splitting inside single/double quotes, `$()` subshells, and heredoc bodies, and correct
    splitting on `&&`, `;`, `||`, and newline. Also exercises `is_merge_help_only()` ([ADR-050 Amendment 13](adr/050-shared-hookio-sibling-hook-fixes.md);
    dev-env#557 — a `gh pr merge --help` invocation must never be mistaken for a real merge attempt): bare
    `--help`/`-h`, a real merge, no merge invocation at all, chained help-then-real and two-chained-help
    cases, heredoc/quoted-argument mentions that must not affect a real invocation elsewhere, and
    case-insensitivity. Also exercises `is_help_only(command, invocation_re)` — the generalized core
    `is_merge_help_only` now wraps ([ADR-050 Amendment 15](adr/050-shared-hookio-sibling-hook-fixes.md);
    dev-env#636) — directly, with a non-merge `invocation_re`, proving the extraction is genuinely generic
    rather than merge-specific (the real second/third caller, `post-tool-use.py`'s
    `is_issue_create_help_only()` / `is_pr_create_help_only()`, is covered in item 12 above). `_hookio` is
    imported by all five PostToolUse Bash hooks plus `pr-merge-reminder.py`
    ([ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md)). `confirm_merge_via_gh()`
    itself is not covered (it shells out to `gh pr view`). Also exercises `is_absolute_path()`
    ([ADR-050 Amendment 22](adr/050-shared-hookio-sibling-hook-fixes.md); dev-env#732 — the
    Python 3.13 `ntpath.isabs` regression, where a driveless-rooted path like `/Git/dev-env` now
    reads as non-absolute): forward-slash / backslash / UNC rooted, drive-absolute, and
    relative/empty inputs, plus a **3.13 simulation** that patches `os.path.isabs` to return `False`
    (as 3.13 does for a driveless-rooted path) and asserts the leading-separator short-circuit still
    classifies the target absolute — the only way to pin the 3.13 fix on this 3.12 runtime, where the
    `startswith` and `isabs` branches are otherwise indistinguishable for `/x`. The `effective_merge_dir`
    relative-path assertion is made version-agnostic via `is_absolute_path` in the same change, and
    `_effective_push_dir` / `_blockable_redirect_root` (items 28 / 33) reuse the shared helper. Also
    (dev-env#831) exercises the new `strip_line_continuations(command)` helper — the shell
    backslash-newline line-join hoisted here so the four `gh pr merge` boundary-finders share one
    strip: pins that a backslash+LF continuation is joined, a bare newline and a backslash+CRLF are
    both left intact (the LF-only rule — bash does not treat backslash+CRLF as a continuation), a
    lone backslash is untouched, multiple continuations all join, and a realistic multi-line
    `gh pr merge` collapses to one logical line with `--repo` preserved.

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
    `extract_pr_number_from_command`'s own positional-number match runs against a
    `mask_quoted_spans`-masked copy of `args` (dev-env#650, ADR-050 Amendment 19), so a `--subject`/`--body`
    value containing a bare decoy number ("resolves 42 items") can't be mistaken for the real merged PR
    number when the command names no real positional number; its `_PR_URL_RE` fallback runs against a
    `mask_prose_flag_values`-masked copy of `args` instead (dev-env#664, ADR-050 Amendment 21), so a
    `--subject`/`--body` value containing a URL-shaped decoy can't false-match either — mirroring
    `extract_repo_from_command`'s own `_PR_URL_REPO_RE` fix immediately below (dev-env#634, Amendment 17).
    `main()` skips the whole operation when the parsed repo doesn't match cwd's config — cwd's
    project-board fields don't apply to a different repo regardless of which PR's body gets fetched —
    but that gate itself is not separately unit-tested, consistent with this file's pure-helper-only
    convention. Also exercises the new `_merge_args()` helper's quote-aware args-region BOUNDARY
    (dev-env#660, ADR-050 Amendment 20 — distinct from, and layered underneath, Amendments 15/17's
    already-existing quote-aware SEARCH within that region): a `--repo` flag or positional PR number
    placed after a `--subject`/`--body` value containing a bare `&`/`|` or a doubled `&&` (confirmed
    live before the fix: an ordinary subject like `"R&D tracking"` already silently dropped a later
    `--repo`, no deliberately-crafted string needed) is no longer truncated away, while a genuine
    top-level `&&` chaining a real sibling command still correctly bounds the region. The live `gh`
    calls (`get_pr_body` / `find_project_item` / `move_to_done` / `confirm_merge_via_gh`) are not covered
    ([ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md),
    [ADR-067](adr/067-scope-merge-keyed-hooks-to-target-repo.md)). Also (dev-env#831) pins that
    both extractors resolve a `--repo` / PR-number sitting on a continued line of a multi-line
    `gh pr merge` — the shared `_repo_target.merge_args` now strips backslash-newline
    line-continuations (via `_hookio.strip_line_continuations`) before bounding the args region.

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
    [ADR-058 amendment](adr/058-worktree-squatting-main-detection-correction.md)); a feature branch
    (or squatting worktree) checked out gets the original fetch-into-ref, unchanged. Also exercises the
    pure-string resolution paths of `extract_repo()`'s `--repo`/`-R` flag check (`-R` shorthand added in
    dev-env#616) and its GitHub-URL parse. Also (PR5 of dev-env#717,
    [dev-env#736](https://github.com/brownm09/dev-env/issues/736)) exercises the pure message builders
    `format_pull_message()` (success / already-up-to-date / git-fail / timeout / exception) and
    `format_park_message()` (parked / checkout-raised / branch-already-exists), plus the `plan_advisory()`
    channel decision — a park message means the model's own cwd branch changed underneath it, so it routes
    to `_hookout.emit_block` (exit-2 stderr, model-visible), while routine pull status alone routes to
    `_hookout.emit_advisory(audience="user")` (a systemMessage toast), and nothing-to-say returns `None`;
    `pull_main`/`park_worktree_off_main` now return their message and `main()` dispatches once. The
    `pull_main` / `park_worktree_off_main` / `list_worktrees` git calls and `extract_repo`'s git-remote
    subprocess fallback are not covered
    ([ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), incl. Amendment 9 for the command-shape
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
    ([ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), incl. Amendment 10 for the command-shape
    anchoring). Also exercises the pure `references_engineering_journal()` predicate (dev-env#539): a
    `cd <path>` or `git -C <path>` naming the engineering-journal repo (either the hyphenated or
    underscored spelling) matches, while the same text mentioned only inside a heredoc body, a quoted
    argument, or a `$()` subshell does not — anchored the same `scan_top_level` way as
    `is_git_push_command()`, but keyed on the `cd`/`-C` directory-argument prefix rather than a CLI verb,
    since the target text is itself an argument *value*, never the start of its own statement
    ([ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md), incl. Amendment 12 for the command-shape
    anchoring). Also exercises the now-pure `most_recent_commit_has_stub(files)` (previously a git call,
    intentionally untested; the git call now lives only in the new `head_commit_files()` wrapper),
    `manifest_path_for_stub()`'s stub-to-manifest path derivation, and `head_commit_has_unresolved_pr(repo,
    files)` (dev-env#651, [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) Amendment 1)
    against tmp-dir manifest fixtures: a `prs_opened` PR absent from `prs_closed` blocks the reminder; the
    dev-env PR #633 shape (an unresolved PR recorded by an earlier commit, not the triggering commit's own
    diff) is pinned directly; a still-live stub with no manifest yet, an unreadable manifest, or an
    unparseable line all conservatively block; and a `.stub.md` path this commit deleted (no longer on disk)
    is skipped rather than treated as missing. Reuses `_journal_schema.parse_manifest_text` / the new
    `has_unresolved_open_pr()` (item 41) rather than re-parsing manifests locally.

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
    windows ([ADR-051](adr/051-worktree-liveness-guard.md)). The git-driven prune/reclaim loops are
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
    (`-R` shorthand added in dev-env#616), that its bare positional-number match ignores a decoy number
    inside a `--subject`/`--body` value while a real number still resolves alongside one (dev-env#650,
    ADR-050 Amendment 19), that `should_emit` stays silent whenever any PostToolUse
    attachment is present (the healthy session), and that the advisory is ASCII/cp1252-encodable so it
    can't vanish under Claude Code's
    cp1252-piped hook stdout ([ADR-053](adr/053-posttooluse-hooks-inert-in-background-sessions.md),
    [ADR-055](adr/055-reliable-event-inert-posttooluse-advisory.md)). Also exercises `_devenv_merge_pr`'s
    quote-aware args-region BOUNDARY (dev-env#660, ADR-050 Amendment 20): a `--repo` flag or PR number placed
    after a `--subject`/`--body` value containing a bare `&`/doubled `&&` is no longer silently dropped (the
    args region's own end-boundary search, distinct from Amendments 15/17's already-fixed search WITHIN that
    region, previously had no quote-awareness of its own), while a genuine chained `&&` command still
    correctly bounds it. `iter_bash_calls`,
    `load_records`, and `_result_text` are imported from `_hookutil`
    ([ADR-090](adr/090-shared-transcript-readers-hookutil.md)) and reached via module-attribute
    indirection, so this suite pins the advisory-specific behavior unchanged. Since dev-env#740
    ([ADR-103](adr/103-shared-hookout-emitter.md)) the advisory is delivered on **exit-2 stderr** (a
    Stop hook's exit-0 stdout is invisible; ADR-091) rather than the former invisible exit-0 stdout
    `print()`, so a behavioral layer now drives the real hook end-to-end over stdin via subprocess
    (HOME/USERPROFILE isolated to a temp dir so the sentinel + transcript-locate resolve under the tmp
    scratch, mirroring item 50): an inert session blocks (exit 2, advisory on stderr, **empty stdout**);
    a `stop_hook_active` continuation and a healthy session (a PostToolUse attachment present) each exit
    0; and the per-session sentinel suppresses a second fire — proving the advisory fires at most once and
    that `mark_resolved` ran on the blocking Stop, *after* the emission (the dev-env#629 retry-safety
    ordering). The remaining `main()` I/O beyond those end-to-end runs is not separately unit-tested
    (pure-helper convention). Also (dev-env#831) pins that `_devenv_merge_pr` resolves a multi-line
    `gh pr merge` whose `--repo`/PR-number is on a continued line, via the shared
    `_repo_target.merge_args` backslash-newline line-continuation strip.

    ```bash
    py -3 claude/scripts/tests/test_posttooluse_inert_advisory.py
    ```

19. **reconcile-open-prs test** — required when changing `claude/scripts/reconcile-open-prs.py`. Exercises
    the pure helpers and the per-PR shard reconciler offline (tmp dirs + an injected `state_fn`, no `gh`):
    pins `should_remove` (MERGED/CLOSED remove; OPEN/None/unknown keep), `shard_pr_number` /
    `repo_from_url` / `entry_repo_and_pr` parsing, and the [ADR-056](adr/056-per-session-sharding-journal-companion-files.md)
    structural guarantee that `reconcile_shard_dir` unlinks only the merged shard and leaves the surviving
    shard **byte-identical** (the no-clobber property), removes an emptied `open-prs/` dir, never auto-deletes
    malformed/non-numeric shards, and that the legacy `reconcile_file` still drains `open-prs.jsonl`. Also
    exercises `find_dirty_open_pr_paths` (dev-env#578, [ADR-082 Addendum](adr/082-journal-compose-worktree-isolation.md)):
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
    ([ADR-056](adr/056-per-session-sharding-journal-companion-files.md)). `get_journal_project` (a git
    call) and the systemMessage emission are not covered.

    ```bash
    py -3 claude/scripts/tests/test_post_compact.py
    ```

21. **journal-shards shared-reader test** — required when changing `claude/scripts/_journal_shards.py`.
    Exercises the pure shard/legacy readers offline (tmp dirs, no network, no `gh`): pins `shard_pr_number`
    parsing, that `iter_pr_shards` returns `(path, entry)` pairs numerically sorted (PR 2 before 10) with
    non-numeric-named, unparseable, non-UTF-8, and non-object shards skipped and a missing/non-dir path
    yielding `[]`, that a pr-less dict shard is still returned (the reader stays content-agnostic — the
    consumer dedups), and that `read_legacy_entries` drains one JSON object per line while skipping
    blank/malformed/non-dict lines and a missing or non-UTF-8 file. Both readers originally caught only
    `(json.JSONDecodeError, OSError)` / `OSError` around their `read_text(encoding="utf-8")` call, letting
    a non-UTF-8 file's `UnicodeDecodeError` (a `ValueError` subclass, not an `OSError`) escape uncaught —
    widened to also catch it (dev-env#804, the same fix shape as item 42's `_bash_state.read_state` and
    item 56's `dev-env-sync.py` precedents from dev-env#801/#800). The two consuming hooks
    (`reconcile-open-prs.py`, `post-compact.py`) import these helpers as one source of truth
    ([ADR-057](adr/057-shared-journal-shard-reader.md)).

    [ADR-118](adr/118-tile-persistence-shards.md) added tile shards
    (`sessions/<project>/tiles/<issue-number>.json`) on the identical numeric-filename layout, so the
    enumeration generalised to `iter_numeric_shards` with `iter_pr_shards` / `iter_tile_shards` as named
    delegations and `shard_pr_number` retained as an alias of a generic `shard_number` (the advisory hook
    imports it by the old name). Added coverage pins: `shard_number` parses any `<N>.json` and agrees with
    `shard_pr_number` on every input (a diverged alias would desync the write-advisory's
    filename-vs-field cross-check from the reader that enumerates it); `iter_tile_shards` returns
    `(path, parsed_entry)` with the spawn payload surviving the round-trip, sorts by *issue* number
    numerically (issue numbers span the same wide range as PR numbers, so ADR-057's lexical-sort bug would
    bite identically), and yields `[]` for a missing `tiles/` dir (the common pre-first-tile case, since
    the reconcile hook calls it unconditionally per project). The load-bearing one is
    `test_all_shard_readers_are_one_implementation`: it asserts all three entry points return *identical*
    results over a directory containing valid, non-numeric, unparseable, and non-dict shards — so it fails
    if anyone later re-specialises an entry point and reintroduces the exact two-divergent-copies drift
    this module was extracted to end.

    ```bash
    py -3 claude/scripts/tests/test_journal_shards.py
    ```

22. **worktree-topology test** — required when changing `claude/scripts/_worktree_topology.py` or the
    squat-detection paths in `prune-merged-worktrees.py` / `post-pr-merge-pull.py` / `dev-env-sync.py` /
    `journal-canonical-guard.py`.
    Exercises the pure topology + decision helpers offline (no git, no network, no disk; paths need not
    exist): pins `parse_worktree_porcelain` (path/branch/detached/`refs/heads/` stripping), `canonical_worktree`
    (first entry), `park_branch_for` (`claude/<basename>`, Windows + POSIX spellings), `main_squatter`
    (a non-canonical worktree on `main`, and `None` when the canonical holds `main` or the ref is free),
    `diagnose_main_topology` (healthy / squat / canonical-off-main-no-squatter), `canonical_sync_action`
    (`warn-squatter` / `return-canonical` / `warn-dirty` / `on-main` — what `dev-env-sync` does), and
    `merge_park_target` (parks a repo's own worktree left on `main`; `None` for the canonical / not-on-main /
    empty / **cross-repo** cwd-not-a-worktree-of-the-merged-repo / Windows-vs-POSIX spelling — what
    `post-pr-merge-pull` does). Also exercises two dev-env#619/dev-env#630 additions: `resolve_current_branch`
    (a non-zero `git symbolic-ref` returncode -> the `"<detached>"` sentinel instead of the silent early exit
    `dev-env-sync.py` used to take; returncode 0 -> stripped stdout) — including a dedicated regression test
    that threads `"<detached>"` through the **full** `diagnose_main_topology` -> `canonical_sync_action`
    pipeline (both a hand-built topology and one parsed from a real detached-canonical worktree list), not
    just the isolated helper, confirming it lands on `return-canonical`/`warn-dirty` and never
    `warn-squatter`/`on-main` — and `is_hijacked_branch` (the dev-env#630 hijack signature: `"<detached>"` or
    a `claude/*`-prefixed branch; `main`/`draft/YYYY-MM-DD`/any other named branch/empty/`None` all read as
    NOT hijacked, the last two without raising — what lets `journal-canonical-guard.py` leave
    engineering-journal's legitimately-many-branched canonical alone except for the actual hijack signature).
    `prune`'s park, and `journal-canonical-guard.py`'s own orchestration (subprocess calls, the TOCTOU
    re-check against a fresh worktree-list read, the actual git mutation), are exercised end-to-end by
    `--dry-run` / a throwaway-repo run in the PR, not here (they shell out to git)
    ([ADR-058](adr/058-worktree-squatting-main-detection-correction.md) incl. 2026-07-09 amendment,
    [ADR-093](adr/093-journal-canonical-hijack-guard.md)). Also pins the dev-env#747/ADR-105
    additions: `DRAFT_BRANCH_RE` (draft/YYYY-MM-DD and the `-recovery` suffix match; malformed dates,
    other suffixes, and other branches don't), `non_canonical_worktrees_matching` (finds every
    non-canonical squatter of a pattern, not just the first; the canonical itself is never flagged
    even when it legitimately holds the pattern), and `pattern_squat_action` (`warn-live` /
    `park-and-remove` / `park-only`, keyed on the caller-supplied `live`/`dirty`/`fully_pushed`
    booleans — see [ADR-105](adr/105-draft-branch-worktree-squat-guard.md)).

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
    ([ADR-060](adr/060-post-merge-tile-checkpoint-hook.md);
    [ADR-050 Amendment 9](adr/050-shared-hookio-sibling-hook-fixes.md)).

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
    plumbing and exit-2 emission are not covered (pure-helper convention). ([ADR-061](adr/061-pre-merge-message-queue.md))

    ```bash
    py -3 claude/scripts/tests/test_pre_merge_message_check.py
    ```

25. **manifest field validator test** — required when changing `claude/scripts/validate-manifest.py`.
    Since dev-env #556 / [ADR-081](adr/081-write-time-journal-shard-validation-hook.md), the
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
    ([dev-env#545](https://github.com/brownm09/dev-env/issues/545)). Also exercises the dev-env#747/
    ADR-105 draft-branch-squat wiring via the same `subprocess.run` mocking, now against a
    call-recording dispatcher (`_make_dispatch_draft_squat`) so each case asserts not just the
    `(pruned, skipped)` counts — identical for park-and-remove vs. park-only — but whether `git
    worktree remove` was actually invoked (a `/review` finding: the original two tests asserted
    only the counts, so a regression that stopped removing entirely still passed): a non-canonical
    worktree squatting an engineering-journal `draft/YYYY-MM-DD` branch is parked AND removed
    (remove call recorded) when idle, clean, and fully pushed, or parked ONLY (worktree left
    untouched, remove NEVER called) when dirty — mirroring the real `stub-829-165612` (park+remove)
    / `stub-823-120134` (park-only) disposition from the live 2026-07-12 incident. Two more cases
    cover `git worktree remove` failing (non-zero exit) or timing out after a successful park: both
    must still count the item as pruned (the branch was freed independently of the removal) while
    flagging it skipped for manual retry — a deliberate divergence from the generic merged-worktree
    timeout path above, which skips only. The
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
    scratch directory is absent, that its default `ext` stays `.flag` (backward compatible with
    every pre-existing caller, which passes only `prefix`) and that an overridden `ext` (e.g. `.txt`)
    sweeps only that suffix (dev-env#768 — the generalization `session-mode-prompt.py`'s `.txt`
    markers needed; see item 70), that an empty `prefix` with `ext=".tmp"` sweeps every `.tmp` under
    the dir while sparing live `.ts` ledgers (dev-env#802 — the heartbeat-orphan sweep
    `hook-liveness-check.py` relies on; see item 67), and that `find_transcript` returns the matching path (or `None`)
    including when the JSONL is nested under a project subdirectory. Also exercises the
    transcript-record readers ([ADR-090](adr/090-shared-transcript-readers-hookutil.md)):
    `_content_items` (list content vs the non-dict-rec / non-dict-message / non-list-content guard
    cases -> `[]`), `_result_text` (string / list-joined / `toolUseResult` stdout+stderr fallback /
    empty -> `''`), `iter_bash_calls` (id-pairing returning `(command, output, cwd)`, out-of-order
    parallel results not crossing, default `cwd=''`, and non-dict/non-Bash/orphan records -> `[]`
    without raising), `_parse_records` (keeps only JSON objects, dropping blank/malformed/non-object
    lines), and `load_records` (reads a JSONL file to its object records). Imported by
    `posttooluse-inert-advisory.py`, `stop-tile-enumeration-gate.py`, `reconcile-open-prs.py`, and
    `token-tracker.py` ([ADR-064](adr/064-shared-hookutil-sentinel-transcript-locate.md),
    [ADR-090](adr/090-shared-transcript-readers-hookutil.md)). Also exercises the bounded tail
    reader ([dev-env#679](https://github.com/brownm09/dev-env/issues/679),
    [ADR-090 Amendment 1](adr/090-shared-transcript-readers-hookutil.md)): `_record_from_line`
    (a valid dict line, and blank/malformed/non-object lines -> `None`) and `iter_records_reverse`
    (most-recent-first order; a property check that results match `reversed(load_records(...))`
    across 9 chunk sizes from 1 byte to 4096, forcing lines to be reassembled across many chunk
    boundaries; multi-byte UTF-8 characters decoding correctly even when a chunk boundary lands
    inside one; a file with no trailing newline; blank/malformed lines skipped; an empty file;
    `FileNotFoundError` on a missing path; `ValueError` on a non-positive `chunk_size`; and —
    the one test in this file that mocks, following `test_prune_merged_worktrees.py`'s precedent,
    since the property isn't otherwise observable from pure inputs/outputs — a `builtins.open`
    read-call counter proving a ~4000-line file yields its single matching tail record after just
    one chunk read, never touching the unread remainder, with its own `>= 1` floor assertion so the
    test can't vacuously pass if a future refactor stops routing reads through `open()`). Also
    exercises, via a dedicated timing-based regression test (`chunk_size` far smaller than a single
    ~2MB line, forcing ~15600 chunk reads across it, generous 5s bound), that a single line spanning
    many chunks parses in `O(line length)`, not the `O(line length^2 / chunk_size)` an adversarial
    `/review` pass on this same PR caught in the first implementation (a `chunk + tail` buffer
    re-concatenated on every chunk read) — the exact shape that bites `idle-refresher.py`'s live
    caller, since the record immediately before whatever it's scanning past is often the
    transcript's newest entry (the user's just-submitted prompt), whose size a large paste puts
    directly under the user's control. Used by `idle-refresher.py`, which needs only the last
    assistant record's timestamp and would otherwise pay a full parse on every prompt submit.
    Also exercises `record_heartbeat(hook_name, heartbeat_dir=None)`
    ([ADR-106](adr/106-hook-heartbeat-liveness-ledger.md); dev-env#745, PR8 of #717): a real
    tmp-dir round trip pins that it writes a parseable, current Unix timestamp to
    `heartbeat_dir / f"{hook_name}.ts"`, creates the directory (and parents) if absent, overwrites
    with an updated timestamp on a second call, leaves no leftover tmp file (the atomic
    `os.replace` swap), swallows errors when the target directory can't be created (a plain file
    occupying the path), and that the default `heartbeat_dir` is `SCRATCH / "hook-heartbeat"`. This
    is the writer side, called as the first statement of `main()` by all 41 currently-wired hooks
    plus `hook-liveness-check.py` itself — see item 67 for the reader side. Also exercises the
    user-text extraction helpers `_user_message_texts(rec)` and `_is_synthetic_user(rec)` (dev-env#710):
    the bare-string / list-of-`{"type":"text"}` extraction with its `[]` guards for a non-user / non-dict
    / malformed record and missing-`text`→`""` coercion, and that `_is_synthetic_user` flags
    `isMeta`/`isCompactSummary` while returning `False` (not raising) on a non-dict — the guard that lets
    `stop-tile-enumeration-gate.py`'s `skip_override` front it ahead of `_user_message_texts`'s own dict
    check. Promoted here from `stop-journal-stub-checkpoint.py` and shared with that tile gate, which
    previously inlined the same extraction (the transcript-parsing drift ADR-090 hoists readers here to
    prevent).

    ```bash
    py -3 claude/scripts/tests/test_hookutil.py
    ```

28. **pr-merge-reminder test** — required when changing `claude/scripts/pr-merge-reminder.py`.
    Exercises the pure command predicates (`is_pr_create_command` / `is_pr_merge_command` /
    `is_git_push_command` — now built on the shared `_hookio.scan_top_level`,
    [ADR-050 Amendment 5](adr/050-shared-hookio-sibling-hook-fixes.md)), the `_create_shard_step` /
    `_is_successful_merge_call` helpers, and the
    [ADR-065](adr/065-scope-push-reminder-to-target-repo.md) push-scoping behavior offline
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
    back to `effective_merge_dir` when absent. Also exercises `_effective_create_repo` (dev-env#646, ADR-050
    Amendment 18) — the `is_create` branch's own counterpart, gaining the identical `--repo`/`-R` flag-first
    precedence, but falling back to plain cwd (no cd-chain dir) when absent, since that branch never had one.
    The live `_open_pr_for_cwd` and `confirm_merge_via_gh` subprocess boundaries are not covered (the repo
    avoids subprocess mocks). `_effective_push_dir` now decides absoluteness via the shared `is_absolute_path`
    ([ADR-050 Amendment 22](adr/050-shared-hookio-sibling-hook-fixes.md); dev-env#732), and this suite's
    relative-push-dir assertion is version-agnostic accordingly (`os.path.isabs` on a driveless
    `\base\sub\repo` is `False` on Python 3.13+, which would otherwise break the assertion independently of the
    fix).

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
    ([ADR-068](adr/068-reconcile-project-board-orphan-issues.md)).

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
    cross-referenced ([ADR-037](adr/037-worktree-disk-reclamation.md)).

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
    pure-helper convention elsewhere in this list). [ADR-072](adr/072-shared-repo-scan-module.md)

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
    ([ADR-024](adr/024-worktree-path-guard-hook.md)).

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
    ([ADR-071](adr/071-canonical-checkout-mutate-guard-hook.md)).

    Also exercises `is_mutating_gh_segment()` ([ADR-071 Amendment 1](adr/071-canonical-checkout-mutate-guard-hook.md);
    dev-env#558): `gh pr merge --delete-branch`/`-d` (any flag order, other flags present) is classified as
    mutating — it checks out the base branch and deletes the local branch locally, the same silent-HEAD-thrash
    harm model reached through a `gh` invocation instead of a `git` verb — while a bare `gh pr merge` or
    `gh pr merge --squash` (no delete-branch flag, remote-API-only) stays classified as safe; the same
    heredoc-mention and anchored-override-prefix guarantees apply to the `gh` classifier as to the `git` one.
    End-to-end: `gh pr merge --delete-branch`/`-d` from a canonical (non-worktree) checkout is blocked (exit 2)
    with the matched command named in the reason; the same command from a worktree-pattern cwd is allowed
    (out of scope, unchanged); a bare `gh pr merge`/`--squash` is allowed from a canonical root; and the
    override token bypasses the block. `_blockable_redirect_root`'s `-C`/`--git-dir`/`--work-tree` redirect
    resolution now decides absoluteness via the shared `is_absolute_path`
    ([ADR-050 Amendment 22](adr/050-shared-hookio-sibling-hook-fixes.md); dev-env#732 — a forward-slash
    redirect target like `git -C /repo` would otherwise fail-open the guard on Python 3.13+); the pure
    resolution semantic is pinned by `is_absolute_path`'s own tests (item 13), so this file needs no new case
    for it. Also (dev-env#514) pins `_ALWAYS_MUTATING_VERBS` — the extracted single-source frozenset of
    unconditionally-mutating verbs `is_mutating_segment()` reads — by exact contents, that the four
    conditional verbs (checkout/stash/branch/pull) stay OUT of it (they keep their own guarded branches),
    and that each always-verb still classifies mutating end-to-end. The worktree-path regexes this hook
    needed are now single-sourced in `_worktree_canon.py` (dev-env#510): `_worktree_root_from_cwd()`
    delegates to `_worktree_canon.worktree_root_from_path()` and `_is_confirmed_worktree_root()`'s fail-open
    backstop to `_worktree_canon.is_worktree_path()`; both keep their existing tests
    (`test_worktree_root_from_cwd_*`, `test_is_confirmed_worktree_root_*`) green unchanged (the shared
    functions return the same result for the absolute paths this hook passes), with the equivalence itself
    pinned in `test_worktree_canon.py` (item 35).

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
    preserves. dev-env#510 adds coverage for the three SSOT functions this module now single-sources for
    the two PreToolUse worktree guards: `match_worktree()` (exposing `group(1)`=canonical root and
    `group(0)`=worktree root), `worktree_root_from_path()`, and `is_worktree_path()` — with two equivalence
    pins that the latter two return exactly what `pre-tool-use-canonical-mutate-guard.py`'s former local
    `_NESTED_WORKTREE_ROOT_RE`/`_SIBLING_WORKTREE_ROOT_RE`/`_WORKTREE_RE` produced for the ABSOLUTE paths
    that hook ever passes (a git-resolved `--show-toplevel`), plus the sole theoretical divergence (a
    marker at the very start of a RELATIVE path, unreachable as a resolved toplevel) documented as a pinned
    boundary. [ADR-073](adr/073-shared-worktree-canon-gh-project-modules.md)

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
    scripts that all already import `_winsubp`) ([ADR-007 → 2026-07-02 follow-up](adr/007-hook-command-invocation.md)).

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
    documented gap ([ADR-074](adr/074-pre-merge-numbering-collision-check.md); dev-env#516).

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
    `--repo=` both parse to the right repo in `_parse_merge_target`. Also pins `_parse_merge_target`'s
    quote-aware args-region BOUNDARY (dev-env#660, ADR-050 Amendment 20): a real `--repo` flag placed
    after a `--subject` value containing a doubled `&&` or an ordinary bare `&` (e.g. `"R&D tracking"`)
    is no longer silently dropped by the tail's own end-boundary search — previously unprotected by
    Amendment 17's `mask_quoted_spans(tail).split()` fix, which only made the search WITHIN an
    already-bounded tail quote-aware, not the boundary-finding step itself — while a genuine chained
    `&&` command still correctly bounds it. The shell test file pre-dates
    this list — added here (alongside the new pure-helper file) to close the gap where it existed but
    was never cross-referenced ([ADR-028](adr/028-all-findings-merge-gate.md), [ADR-039](adr/039-merge-gate-findings-enforcement.md)).
    A step `[10]` in the shell test (dev-env#831) pins that `_parse_merge_target` resolves a
    multi-line `gh pr merge` whose `--repo`/PR-number sits on a backslash-newline-continued line
    (its inline `re.split` now strips continuations first via `_hookio.strip_line_continuations`),
    while a real top-level `&&` after the continuation is still excluded.

    ```bash
    py -3 claude/scripts/tests/test_pre_merge_findings_gate.py
    bash claude/scripts/tests/test-merge-findings-gate.sh
    ```

39. **Crude command-substring-check regression test** — required when adding or changing any
    `claude/scripts/*.py` file. AST-based (not grep-based, so a comment or docstring merely
    *mentioning* the pattern is structurally invisible to it, not just filtered — see
    [ADR-050](adr/050-shared-hookio-sibling-hook-fixes.md) Amendment 11) repo-wide gate
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
    them (dev-env #556, [ADR-081](adr/081-write-time-journal-shard-validation-hook.md)).
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
    without duplicating the rule ([ADR-081](adr/081-write-time-journal-shard-validation-hook.md)).
    Also exercises the new `has_unresolved_open_pr()` (dev-env#651,
    [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md) Amendment 1): a `prs_opened`
    PR number absent from `prs_closed` is unresolved (True); a matching PR compared across int/str type
    mismatch, an already-resolved or never-opened entry, and a non-dict or non-list-valued entry all
    return the documented conservative result. This is now a third consumer of this module, alongside
    `validate-manifest.py` (item 25) and `journal-shard-write-advisory.py` (item 40) —
    `stub-push-archive-reminder.py` (item 16).

    [ADR-118](adr/118-tile-persistence-shards.md) added a third schema, `TILE_REQUIRED_FIELDS` /
    `missing_tile_fields()`, for tile shards (`sessions/<project>/tiles/<issue-number>.json`). Coverage
    mirrors the open-PR set (field order, all-present, one missing, a missing subset in schema order,
    non-dict and empty entries) plus three tile-specific pins: a missing `prompt` is flagged (the field
    without which the shard cannot reconstruct a chip, which is its entire reason to exist — a shard
    missing only this one would otherwise pass a shallow eyeball check and fail silently at re-spawn
    time); a stray `task_id` does not mask a genuinely missing required field (ADR-094's rejected
    "task_id record only" alternative, kept rejected); and `test_tile_and_open_pr_schemas_are_distinct`
    asserts the two schemas are **not** interchangeable — both are all-required single-object shards on
    the same numeric-filename layout sharing `url`/`stub`, so a copy-paste validating a tile against
    `OPEN_PR_REQUIRED_FIELDS` would "pass" on the overlap while silently ignoring `prompt`/`cwd`.

    ```bash
    py -3 claude/scripts/tests/test_journal_schema.py
    ```

42. **`_bash_state` shared-module test** — required when changing `claude/scripts/_bash_state.py`.
    Exercises every pure helper offline (tmp dirs via an injected `scratch=` parameter, mirroring
    `_hookutil.py`'s test convention — no real `~/.claude/scratch`): `state_path` correctness with
    and without an override; a `write_state`/`read_state` round-trip; `read_state` returning `None`
    on a missing file, malformed JSON, a JSON array (non-dict), or a non-UTF-8 file (`UnicodeDecodeError`, a `ValueError` subclass — dev-env#801) rather than raising; `write_state`
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
    fields rendering a `<unknown>` placeholder instead of raising. Also exercises the new
    `state_age_seconds()` (dev-env#682, [ADR-101](adr/101-bash-drift-check-every-call.md)):
    a missing state file returns `None`; a file `os.utime`'d ~90s into the past reports an age
    within a few seconds of 90; a file `os.utime`'d into the future returns a negative age rather
    than raising; and the same file-where-directory-expected fixture `write_state`'s own
    unwritable-scratch test uses returns `None` rather than raising. Also exercises the new
    `drift_warning_for()` (dev-env#682 `/review` — extracted after the three-call
    `current_repo_state`/`read_state`/`format_drift_warning` sequence was found duplicated
    verbatim across all four checkpoint hooks, the same class of divergence risk this module's
    own `current_repo_state()` consolidation was originally meant to close): run against this
    repo's own real checkout (via `REPO_ROOT`, a real git repo, so the test needs no `git`
    mocking) with an injected `scratch` dir — an empty `session_id` still resolves a real
    `(repo_root, branch)` but never returns a warning; a recorded state written to match the real
    current state returns no warning; a recorded state written to deliberately differ fires one.
    Backs `post-tool-use-cwd-track.py`'s state writes and the drift check in
    `pre-commit-branch-check.py` / `pre-pr-create-check.py` / `pre-merge-branch-check.py`
    ([ADR-085](adr/085-bash-repo-branch-drift-detection.md); dev-env#573), plus the
    elapsed-time gate in `pre-bash-drift-check.py` (item 59 below) — all four now call
    `drift_warning_for()` rather than re-composing the sequence by hand. `current_repo_state()`
    — the single combined `git rev-parse --show-toplevel --abbrev-ref HEAD` call shared by all five
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
    itself is `_bash_state.drift_warning_for()` (shared with the other three checkpoint hooks — see
    item 42), not a function local to this file. This test file pre-dates this list — added here
    alongside the drift-check change ([ADR-085](adr/085-bash-repo-branch-drift-detection.md)).

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
    is not backfilled per the Test Coverage Gate, [ADR-022](adr/022-test-coverage-gate-before-pr.md)
    — only the new behavior is tested); the repo/branch lookup itself is
    `_bash_state.drift_warning_for()` (shared with the other three checkpoint hooks — see item 42).
    ([ADR-085](adr/085-bash-repo-branch-drift-detection.md))

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
    display placeholders. The repo/branch lookup itself is `_bash_state.drift_warning_for()`
    (shared with the other three checkpoint hooks — see item 42), matching item 43's identical scope
    decision for the sibling commit-time hook. ([ADR-085](adr/085-bash-repo-branch-drift-detection.md))

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
    themselves and pipe the output in ([ADR-086](adr/086-journal-compose-liveness-guard.md)).

    ```bash
    py -3 claude/scripts/tests/test_check_journal_compose_liveness.py
    ```

47. **disk-space-check test** — required when changing `claude/scripts/disk-space-check.py`.
    Exercises the pure `classify_free_space(free_gb, warn_gb, act_gb)` helper extracted from
    `main()`'s inline threshold if/elif (dev-env#592,
    [ADR-087](adr/087-pretooluse-disk-space-check.md)): pins the three bands (ample space is
    `"ok"`, mid-range is `"warn"`, low space is `"act"`) and both threshold boundaries as inclusive
    on the healthier side (`free_gb == WARN_GB` is `"ok"`, `free_gb == ACT_GB` is `"warn"`, never
    `"act"`) — the exact behavior the inline if/elif already had, now pinned rather than only
    implicit. This hook is now registered under both `UserPromptSubmit` and `PreToolUse(Bash)`,
    reusing the same script and the same per-session marker-file gate for both (ADR-087); the
    `disk_usage` syscall, marker-file I/O, and the detached `reclaim-worktree-disk.py` spawn are not
    covered here (pure-helper convention, matching item 9's `install_decision()` precedent). Also
    exercises the extracted `should_cleanup_sentinels(hook_event_name)` predicate (dev-env#768): the
    sentinel sweep now added to `main()` (this hook never swept its own markers) is gated to `True`
    only for `"UserPromptSubmit"` — `"PreToolUse"`/`"Stop"`/empty are all `False` — so the
    scratch/ directory scan doesn't run on every single Bash call under this hook's `PreToolUse`
    registration.

    ```bash
    py -3 claude/scripts/tests/test_disk_space_check.py
    ```

48. **stop-tile-enumeration-gate test** — required when changing
    `claude/scripts/stop-tile-enumeration-gate.py`. Two layers, mirroring this hook family's
    established split ([ADR-088](adr/088-state-keyed-tile-enumeration-gate.md); dev-env#599),
    now covering **three independent triggers** that share the same enumeration/skip-override
    machinery (each with its OWN per-trigger sentinel as of
    [ADR-097](adr/097-per-trigger-tile-gate-sentinels.md); dev-env#677 — see that paragraph at
    the end of this item): the merged-PR trigger (below), the dangling-created-issue trigger
    ([ADR-092](adr/092-dangling-issue-tile-enumeration-gate.md); dev-env#638), and the
    tiles-spawned-without-a-table trigger ([ADR-094](adr/094-tile-tables-and-issue-per-tile.md)
    addendum; dev-env#656).
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
    false-fire (A4). `_target_pr`'s own bare positional-number fallback now runs against a
    `mask_quoted_spans`-masked copy of its input (dev-env#650, ADR-050 Amendment 19), so a
    `--subject`/`--body` value containing a decoy number can't be mistaken for the real target PR
    number — covered by direct tests (its own `_PR_URL_RE` check deliberately stays unmasked, an
    already-documented out-of-scope gap) plus an integration-level `session_merged_prs` case via the
    auto-merge acted-on/observed correlation path. A behavioral layer drives the real hook end-to-end over stdin via subprocess against
    a synthetic transcript, with HOME/USERPROFILE pointed at a temp dir so the per-trigger sentinels
    (ADR-097) are isolated: pins merged-no-enum -> exit 2 with the reason on stderr and empty stdout,
    merged+enum and no-merge -> exit 0, the `stop_hook_active` loop guard -> exit 0, and that the
    merged-PR trigger's own sentinel suppresses a second fire of THAT trigger. `main()`'s
    stdin/sentinel plumbing beyond the end-to-end runs is not separately unit-tested (pure-helper
    convention). The transcript-record readers
    (`load_records` / `_parse_records` / `iter_bash_calls` / `_result_text` / `_content_items`) now
    live in `_hookutil` ([ADR-090](adr/090-shared-transcript-readers-hookutil.md)) — the gate
    imports the three it uses (`_content_items`, `_parse_records`, and the shared `iter_bash_calls`,
    aliased) and wraps the last in a thin 2-tuple adapter (it never needs `cwd`), so
    `session_merged_prs` and these tests are unchanged by that extraction.

    The dangling-created-issue trigger (ADR-092) adds its own pure-helper coverage, fully independent
    of the merged-PR path above: `session_created_issues` (issue-number/repo extraction from a
    `gh issue create`'s output URL; that `gh issue create --help` yields nothing, since `--help`
    prints no issue URL — the same false-positive shape dev-env#636/ADR-050 Amendment 16 closes for
    `post-tool-use.py`, naturally inert here rather than needing its own guard; heredoc-anchoring).
    `session_resolved_issue_numbers` across GitHub's three documented auto-close keyword stems
    (Close/Fix/Resolve, present and past tense, case-insensitive —
    [GitHub's linking-a-pull-request-to-an-issue doc](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/linking-a-pull-request-to-an-issue)),
    scoped to each `gh pr create` / `gh pr edit` segment's own text (including its heredoc body, this
    repo's own `--body "$(cat <<'EOF' ...)"` idiom) so an unrelated Closes-style mention on a different
    chained segment can't leak in; that a Closes-keyword mention in a PR that never merged does NOT
    resolve the issue (no auto-close without a merge); `gh pr edit <N>`'s Closes keyword resolving a
    PR merged this session (the "attach the keyword after creation" flow, both bare-number and
    PR-URL target forms); and explicit `gh issue close N` resolution, **including the URL form**
    (`gh issue close <url>` — the bare-positional-only lookup originally missed this, since a URL's
    issue number is preceded by `/`, never whitespace; review of PR #639, confirmed independently by
    two reviewers). `_closed_issue_number`'s own bare positional-number fallback (the same compiled
    `_POS_NUM_RE` object `_target_pr` uses) now runs against a `mask_quoted_spans`-masked copy of its
    input too (dev-env#650, ADR-050 Amendment 19 — found by grepping this file for the pattern, not
    named in the issue itself), covered by direct tests plus an integration-level
    `session_resolved_issue_numbers` case. `session_unresolved_created_issues` (created minus resolved) and
    `evaluate_issues()`'s full composition (fire / enum-resolved / skip-resolved / no-issue no-op /
    **created-and-resolved-with-no-enum-needed also sets the sentinel** (review of PR #639 — distinct
    from "nothing created," else a create-then-close session with no merge anywhere never sets the
    sentinel and re-pays the full scan every subsequent turn) / lowest-issue-number determinism / the
    shared #700 bare-assertion rejection) mirror `evaluate()`'s exact shape as a fully independent
    sibling — zero modification to `evaluate()` or any of its 39 pre-existing tests, all of which pass
    unmodified against the extended file. Also covers `format_issue_reminder` (cp1252-encodability)
    and the combined-trigger cases: a merged PR and a dangling issue in the same session fire
    independently when unenumerated, and one recorded enumeration satisfies both. The behavioral layer
    gains seven end-to-end subprocess cases mirroring the merged-PR e2e pattern exactly: dangling
    blocks on stderr; enum, skip-override, explicit-close, and merge-resolution each allow; a combined
    merged-PR-plus-dangling-issue session emits both reminders in one exit-2 write; and the sentinel
    suppresses a second fire. The pre-filter also reuses the real `_ISSUE_CREATE_STMT_RE` detection
    regex (`.search()`) rather than a hand-written substring, so it can't drift from what the detector
    actually matches (a literal single-space `"issue create"` check would miss a tab/multi-space
    invocation — review of PR #639, confirmed independently by two reviewers).

    The tiles-spawned-without-a-table trigger (ADR-094 addendum, dev-env#656) adds a third fully
    independent pure-helper suite, mirroring the shape of the two above: `session_spawned_tiles`
    (a real `spawn_task` tool_use vs. none) and `table_marker_present` (the line-anchored
    `^#{1,6}\s*tiles\s+spawned\s+this\s+session` heading regex — case- and heading-level-insensitive,
    but requires the marker to start its own line so a mid-sentence mention of the phrase, or the
    same text merely echoed inside a user/tool_result record, does not false-satisfy it — only
    `assistant` `text` items are scanned). `evaluate_tile_table`'s composition (fire / resolved-by-marker /
    resolved-by-skip / no-op-without-a-spawn) and `format_table_reminder` (cp1252-encodability) mirror
    `evaluate`/`evaluate_issues`'s existing shape as a third sibling. Also pins the key interaction this
    trigger introduces: a spawned tile satisfies `enumeration_recorded` and so silently resolves
    triggers (1)/(2), but does **not** by itself satisfy trigger (3) — a session that merges a PR,
    spawns a tile, and never emits the table sees (1) resolve while (3) still fires; the reverse (a
    merged PR with no spawn at all) leaves trigger (3) a no-op, since there is nothing to table. The
    pre-filter gains a third OR-branch: a bare substring check for `"spawn_task"` (deliberately not
    `scan_top_level`-anchored, since it is a pre-filter substring check on JSON-recorded tool-call
    data, not a command-shape check). Matches `_SPAWN_TASK_RE`'s own deliberately namespace-agnostic
    "any namespacing hits" philosophy rather than the exact fully-qualified tool name
    `mcp__ccd_session__spawn_task` an earlier version hardcoded — that narrower check was a STRICT
    SUBSET of what the real detector (`session_spawned_tiles`, which `enumeration_recorded` now also
    delegates to — dev-env#674 review) can match, so a spawn recorded under any other MCP namespace
    would satisfy the detector but be silently skipped by the pre-filter, defeating the detector's own
    namespace-robustness (empirically not currently triggerable — all recorded spawns use the standard
    prefix — but a real, fixable soundness gap independently flagged by two review passes). The bare
    substring costs a few extra full-transcript reparses in sessions that merely mention "spawn_task"
    in prose (empirically ~8x in one real no-tile transcript) — a bounded, accepted perf cost matching
    the pre-existing "merged" pre-filter branch's own tradeoff. A dedicated regression test spawns a
    tile under a fabricated non-standard namespace and confirms both the pure detector and the full
    end-to-end hook still catch it. The behavioral layer gains six end-to-end subprocess cases mirroring
    the existing pattern: spawn-with-no-table blocks on stderr naming the exact heading; spawn-with-table
    and spawn-with-skip each allow; a combined merged-PR + dangling-issue + spawn session (no table)
    blocks naming **only** the table trigger (the spawn silently resolves the other two); the sentinel
    suppresses a second fire; and the other-namespace spawn still blocks. Two **pre-existing** e2e tests
    (`test_e2e_merged_with_enum_allows`, `test_e2e_dangling_issue_with_enum_allows`) were extended to
    also emit the table heading alongside their bare `spawn_task` tile, since a bare spawn alone no
    longer reaches exit 0 once trigger (3) exists — their original intent (proving triggers (1)/(2)
    resolve on enumeration) is preserved by making the session genuinely fully compliant rather than by
    weakening the new trigger.

    **Per-trigger sentinels** ([ADR-097](adr/097-per-trigger-tile-gate-sentinels.md); dev-env#677)
    replace the single sentinel all three triggers previously shared: before this fix, whichever
    trigger fired or resolved FIRST suppressed evaluating the other two for the rest of the session,
    including one whose own condition (e.g. a tile spawned in a later, separate turn) hadn't even
    happened yet when the sentinel was set — found during the PR #674 review that landed trigger (3).
    `main()` now checks three independent sentinel files (`-pr-`/`-issue-`/`-table-` suffixes on the
    same `SENTINEL_PREFIX`) and only skips reading the transcript at all when **all three** are
    already set; otherwise it evaluates only the still-open triggers, marking each independently. The
    cheap pre-filter (the `"merged"` / `gh issue create` / `spawn_task` substring/regex check, run
    after the transcript is read but before the full JSON parse) is likewise gated per trigger — a
    review pass on this PR found that an unqualified combined check would force a full reparse on
    every remaining Stop of the session once trigger 1 had ever fired, since `"merged"` never
    disappears from a transcript once written; each clause is now `already_done[trigger] or <original
    check>`, restoring the parse-skipping fast path the instant every trigger with a live signal in the
    transcript is either resolved or genuinely absent. No pure evaluator changed, so all 112
    pre-existing tests (including the three "sentinel suppresses a second fire" e2e tests, traced by
    hand) pass **unmodified**. Four new tests cover the fix directly: a two-turn simulation reproducing
    the dev-env#677 bug itself (trigger 1 fires in turn 1; a tile spawned with no table in turn 2 must
    still fire trigger 3 — it would wrongly stay silent under the pre-fix code); that a partial
    (merge-only) session sets ONLY the `pr-` sentinel file on disk, not `issue-`/`table-`; that a
    fully-compliant session sets all three sentinel files and a second Stop with the same transcript
    stays allowed; and a three-turn sequence proving trigger 1's stale `"merged"` text does not block
    detection of trigger 3 arising two turns later.

    **A fourth trigger, deferral-question** ([ADR-109](adr/109-tile-gate-deferral-question-trigger.md);
    dev-env#772), catches a session asking the user a scheduling/permission question about a known
    follow-up instead of tiling it (e.g. "let me know if you want me to start it now") — the motivating
    incident was the next PR in a multi-PR initiative (ADR-059) asked about instead of tiled, even
    though the plain-language tile-now rule already existed and other genuine follow-ups WERE correctly
    tiled in that same turn. That last detail is exactly why `evaluate_deferral` deliberately does NOT
    accept `enumeration_recorded` as resolution the way triggers 1/2 do — only `skip_override` — since
    the first draft, which did reuse `enumeration_recorded`, would have been silently resolved by those
    unrelated tiles and never fired for the incident it exists to catch (walked through by hand against
    the real incident, not caught by `/review`; see the ADR's own "rejected first draft" section). Also
    unlike triggers 1-3, this one is a bounded natural-language phrase match
    (`_DEFERRAL_QUESTION_RES`), not an objectively verifiable fact, so a fire here does NOT block via
    exit 2 — it rides `_hookout.emit_advisory("Stop", ..., audience="user")` (a systemMessage, exit 0),
    and is silently skipped in favor of the harder block whenever a blocking trigger ALSO fires in the
    same turn (only one exit code exists per invocation). 19 initial tests: phrase detection (including
    that an unrelated design question like "should I use approach A or B" does NOT match, and that a user
    record / tool_result containing the phrase is correctly ignored — assistant-text-only scope);
    `evaluate_deferral`'s composition, including the regression pin for the rejected first draft (an
    unrelated `spawn_task` elsewhere in the session must NOT resolve this trigger); and 5 e2e cases
    covering the motivating-incident shape end-to-end (exit 0 + systemMessage), the
    blocking-trigger-wins precedence case, a clean session emitting no systemMessage at all, the
    "should i" phrasing after an issue-create, and sentinel suppression. Also sharpens the
    command-keyed `post-merge-tile-checkpoint.py`'s existing blocking reminder (ADR-060) to explicitly
    name this same anti-pattern — that hook's own test file only exercises its merge-detection
    predicate, not message text, so no test change was needed there.

    **`/review` on PR #776 found 3 findings, all fixed, +2 more tests (137 total)** — see the ADR's own
    "Review hardening" section for full detail. The headline one: two independent subagents both caught
    that the shared sentinel-marking loop set trigger 4's FIRED sentinel whenever `fire_defer` was true,
    even on a turn where a co-firing blocking trigger (1-3) preempted the advisory via an early
    `sys.exit(2)` — silently and permanently suppressing the advisory for the rest of the session, the
    opposite of what this ADR and the module docstring explicitly promise. Fixed by marking trigger 4's
    FIRED sentinel only at its actual point of emission (unreachable when a blocking trigger fired first),
    not in the shared pre-emission loop. Also fixed: a dead regex-alternation branch (`_DEFERRAL_QUESTION_RES[0]`
    required an unnatural space before the apostrophe in "'d like", so the real contraction "you'd like" —
    one of the two phrasings the ADR documents as targeted — never matched); and the pre-filter's 4th
    clause forcing a full reparse on every Stop in ANY session containing the common phrases "should i "/
    "want me to", regardless of whether a merge or issue-create ever happened (fixed by adding the same
    scoping check `evaluate_deferral` itself already requires — a safe superset, confirmed no dedicated
    test needed since it's a pure internal-optimization with no externally observable behavior change).

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
    [ADR-003](adr/003-config-in-version-control.md)'s table, and that
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
    Two layers, mirroring this hook family's split ([ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md);
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
    repo); a no-flag run with a *planted* stale (uncomposed, pre-today) stub in the tmp journal repo
    delivers the Checks 2-3 advisory as a `{"systemMessage": ...}` JSON object on **stdout** (exit 0) with
    empty stderr — the re-pin of the corrected non-blocking channel (dev-env#740,
    [ADR-103](adr/103-shared-hookout-emitter.md): a Stop hook's exit-0 stdout is invisible, so the
    former plain-`print()` surfaced nothing, and Checks 2-3 now ride the `_hookout` systemMessage channel);
    a present flag + `stop_hook_active:true` exits 0 (loop guard, no re-block) and **preserves** the
    flag (never consumed without delivery). `main()`'s advisory branches (stale-draft / unmerged-branch /
    orphan cleanup) shell out to git and are not separately unit-tested (pure-helper convention) — the
    end-to-end runs exercise their fail-closed path and, with the planted stub, the systemMessage delivery.

    ```bash
    py -3 claude/scripts/tests/test_journal_stop_check.py
    ```

51. **idle-refresher test** — required when changing `claude/scripts/idle-refresher.py`. Exercises the
    pure helpers offline (no stdin, network, gh, or disk — fixture-only, matching item 47's precedent):
    `parse_iso_to_epoch` (the transcript `Z` / `+00:00` / naive / microsecond timestamp forms agree, and
    bad/`None`/non-str input yields `None`); `last_activity_epoch` (anchors on the **last** assistant
    record's timestamp — deliberately not "the last record of any type," which would be the just-submitted
    user prompt appended around submit time and always read as gap ~0 — and returns `None` when no assistant
    turn exists, which doubles as the first-prompt-of-session skip); `compute_gap_seconds`; `should_refresh`
    (the strict-`>` threshold boundary: `== thresh` does not fire, `> thresh` does, `None` never does);
    `load_threshold_minutes` via `tempfile.TemporaryDirectory` (the `idle_refresher_minutes` override
    honored, and the missing-config / absent-key / malformed-JSON fallbacks to the 60-min default);
    `is_automated_prompt` (the XML-prefixed skip, incl. leading whitespace and the lowercase-initial-only
    match); `humanize_gap`; and the ASCII/cp1252-encodability of the injected `additionalContext` cue (per
    `test_posttooluse_inert_advisory.py`'s precedent, so it can't vanish under Claude Code's cp1252-piped
    hook stdout). Since [dev-env#679](https://github.com/brownm09/dev-env/issues/679)
    ([ADR-090 Amendment 1](adr/090-shared-transcript-readers-hookutil.md)),
    `last_activity_epoch` takes an already most-recent-first iterable (no internal `reversed()`) so a live
    caller can feed it a lazy, bounded generator instead of a fully materialized list — the three fixtures
    above are written newest-first accordingly, and a dedicated test
    (`test_last_activity_epoch_consumes_lazily`) proves the laziness itself with a hand-rolled generator
    that raises `AssertionError` if pulled past its first match. `main()`'s stdin plumbing and the live
    transcript read (through the already-tested `_hookutil.iter_records_reverse` / `find_transcript`) are
    not covered (pure-helper convention) — exercised instead by the manual end-to-end smoke run in the PR
    ([ADR-095](adr/095-session-boundary-summaries-and-idle-refresher.md); dev-env#655).

    ```bash
    py -3 claude/scripts/tests/test_idle_refresher.py
    ```

52. **pre-auto-merge-checkpoint-gate test** — required when changing
    `claude/scripts/pre-auto-merge-checkpoint-gate.py`. Two layers, mirroring this hook family's
    established split ([ADR-083](adr/083-auto-merge-checkpoint-gate.md); dev-env#574). A
    pure-helper Python test exercises the flag-parsing, marker-parsing, freshness-comparison, and
    qualifying-comment-selection logic offline (no disk, no network, no gh): `wants_auto_merge`'s
    shlex-based tokenization of the merge tail — bare `--auto`, `--auto=true`, the falsy
    `--auto=false`/`0`/`no` set (plain and quoted), no `--auto` at all, `--disable-auto` (turns OFF
    a pending auto-merge, never in scope), survival across a `cd`-chain, `--auto` in an earlier
    unrelated chain segment or inside an unrelated flag's value (e.g. `--body`) correctly NOT
    detected, a shell-quoted `--auto` (quotes stripped before `gh` sees argv) still detected — a
    real pre-fix bypass a plain whitespace regex missed — and an unparseable tail (`shlex.split`
    raising on an unterminated quote) defaulting to *wanting* `--auto`, failing toward the stricter
    gate rather than letting an unparseable command through ungated. `_merge_tail` itself (the
    shared tail-extraction `wants_auto_merge` tokenizes) is separately pinned for its dev-env#660 /
    [ADR-050 Amendment 20](adr/050-shared-hookio-sibling-hook-fixes.md) boundary-masking fix:
    the end-boundary search now runs against a `mask_quoted_spans`-masked copy first, so a quoted
    `&&`/`||`/`;`/newline inside a `--subject`/`--body` value (e.g. `--subject "part1 && part2"
    --auto`) no longer truncates the tail before a real trailing `--auto` is seen — one test pins
    the full, untruncated tail and that the trailing `--auto` is now found by direct parse rather
    than the `shlex.split`-`ValueError` fallback it accidentally reached pre-fix; one confirms a
    genuinely real trailing `&&`-chained command still correctly bounds the tail (the fix must not
    simply widen the boundary unconditionally); and one — a review finding on PR #668 — combines a
    quoted decoy separator with a real trailing chain in the same command, pinning that the
    boundary-finder picks the FIRST unmasked separator rather than being shadowed by the earlier
    masked decoy. The `gh pr merge --auto --help` composition with the reused
    `_hookio.is_merge_help_only` confirms the hook's check order (`is_pr_merge_command` →
    `wants_auto_merge` → `is_merge_help_only`) exits 0 before any live lookup even though
    `wants_auto_merge` itself reports `True`. The `premerge-checkpoints` marker regex is pinned
    against valid values, the deliberate third `missing` literal (in neither `_VALID_ADR_WARRANT`
    nor `_VALID_DOC_RECONCILIATION`, so an unresolved gap is visibly recorded rather than blank
    while still failing validity), an absent marker, and a marker alongside the sibling gate's
    `review-findings` marker in the same comment body; `_is_stale`'s ISO-8601 string comparison
    (fresh, stale, and the equal-timestamps-counts-as-fresh boundary); and `_qualifying_comment`'s
    single-comment-carries-both-markers requirement — two comments each carrying only one marker
    must not combine, the one comment carrying both is found, most-recent-qualifying-comment wins,
    marker order within a comment doesn't matter, an empty comment list, and `_last_match` binding
    to the LAST marker occurrence within one comment's body rather than the first, so a comment
    quoting an earlier stale marker for context ahead of its own current one resolves correctly (a
    gap inherited from the sibling gate's identical `.search()`-per-comment pattern, fixed here
    specifically because this hook's fail-closed, no-override design raises the stakes). Also
    sanity-checks the reused `is_pr_merge_command` import. `main()`'s stdin/exit-2 plumbing and the
    live `gh pr view` call (`_fetch_pr_json`, reused from `pre-merge-findings-gate.py` and already
    covered by that file's own suite — item 38 above) are not covered (pure-helper convention,
    matching `test_pre_merge_findings_gate.py`'s own scope note). A behavioral shell test drives
    the real hook end-to-end over stdin via the `MERGE_GATE_TEST_JSON` seam (no live `gh`, no
    network): the no-`--auto`/explicit-`--auto=false` paths never touch `gh` at all; allow on
    clean-review-plus-complete-checkpoints-plus-fresh and on open-findings-with-recorded-
    disposition; BLOCK on open findings with no disposition, no comment carrying the
    `review-findings` marker, no comment carrying the `premerge-checkpoints` marker, an incomplete
    checkpoints marker, a stale marker, and a `gh` failure — the last confirming this hook's
    deliberately **flipped, fail-closed** default versus the sibling gate's fail-open one (ADR-083
    Decision point 3); a `commits` array at or above the 100-entry suspect-truncation threshold
    also BLOCKs (`gh pr view`'s `commits` field is a paginated connection with no documented page
    size, so `commits[-1]` can't be trusted as the true head commit past that size); and a
    shell-quoted `--auto` is still gated and passed while `--auto` appearing only as prose inside a
    `--body` value is correctly NOT gated at all (plain merge, never touches `gh`), alongside the
    `--auto --help` and non-merge-command allow paths. This shell layer pre-dates the dev-env#660
    boundary-masking fix and is unchanged by it — no case combines a quoted separator with the
    merge tail. Two crash-path cases added for dev-env#718 (Phase A of the hook-reliability
    initiative dev-env#717) pin the new fail-CLOSED crash guards: `[16]` a `main()` crash after the
    `--auto` trigger (a malformed `gh` response whose `commits[-1]` is not a dict raises
    `AttributeError`, caught by the new `__main__` guard) and `[17]` a corrupted sibling import (a
    temp-dir copy of the hook whose `pre-merge-findings-gate.py` raises on `exec_module`, caught by
    the new module-level dependency-load guard), both asserting exit 2 where the pre-#718 unguarded
    `exec_module` / un-`try`-wrapped `main()` exited 1 = fail-OPEN = silently ungated `--auto`. Four
    pure-helper cases and one e2e case (`[19]`) added for dev-env#823 pin the multi-line-command
    fix: a plain (non-`--auto`) `gh pr merge --squash` written with shell backslash-newline
    line-continuations must not be misdetected as `--auto` (before the fix, `_merge_tail`'s
    `\n`-split truncated the tail at the first continuation, leaving a dangling backslash that made
    `wants_auto_merge`'s `shlex.split` raise `ValueError` and fall through to its fail-closed
    `return True` — blocking a plain merge; incident: PR #820, 2026-07-16), while a genuine
    `--auto` on the merge line OR a continued line is still detected (no bypass introduced),
    `_merge_tail` joins continuations rather than truncating (no surviving backslash-newline, full
    argument set preserved), and the e2e `[19]` proves the full `main()` flow exits 0 without ever
    reaching `gh` — a sabotage-then-reconfirm check confirmed exit 2 (the #820 regression) once the
    `re.sub` join is reverted, so the case genuinely discriminates. 37 pure-helper tests and 20
    behavioral cases, both suites green as of 2026-07-17. (dev-env#831 retrofitted `_merge_tail`'s
    inline continuation strip to call the shared `_hookio.strip_line_continuations` helper —
    behaviour-identical, so these suites are unchanged and stay green.)

    ```bash
    py -3 claude/scripts/tests/test_pre_auto_merge_checkpoint_gate.py
    bash claude/scripts/tests/test-auto-merge-checkpoint-gate.sh
    ```

53. **`_journal_compose_force` shared-module test** — required when changing
    `claude/scripts/_journal_compose_force.py`. Exercises every pure helper offline: `resolve_force()`
    (a bare `--force`, with a date before/after, absent, `--forceful`/`--force-push` correctly NOT
    matched, empty string, `None`-safe); `marker_dir()`/`marker_path_for()` (default path, the
    `JOURNAL_COMPOSE_FORCE_MARKER_DIR` env override, filename shape); `build_marker()` (shape,
    bool-coercion, `None` raw-args handling); `write_marker()`/`read_marker()` (a real tmp-file round
    trip — the only impure surface, matching `test_hookutil.py`'s convention — overwrite-on-rerun,
    missing file, malformed JSON, non-dict JSON all returning `None`); and `is_marker_fresh()` (a
    recent marker, the exact `MAX_MARKER_AGE_SECONDS` boundary inclusive, one second past it, a
    future-timestamped marker treated as not fresh rather than trusted, malformed/missing
    `resolved_at`, a non-dict marker, and a custom `max_age_seconds` override). `now`/`resolved_at`
    are always explicit `datetime` values passed by the test, never the real clock. Extended during
    `/review` on PR #671 with a timezone-aware `resolved_at` (an ordinary `.isoformat()` shape that
    previously raised an uncaught `TypeError` on naive-minus-aware subtraction — outside the
    `except ValueError` — propagating out of the guard hook's `main()` and failing OPEN instead of
    closed; must now resolve to "not fresh") and a simulated-concurrent-writer case for
    `write_marker()` (a monkeypatched `os.getpid()` proving one writer's in-progress, not-yet-renamed
    temp file survives an independent writer's own write-and-rename, now that the temp filename is
    per-PID rather than a single fixed `path + ".tmp"`)
    ([ADR-096](adr/096-journal-compose-mechanical-force-guard.md); dev-env#631). Also exercises
    the new `cleanup_stale_markers()` (dev-env#768): removes a marker older than
    `MARKER_CLEANUP_MAX_AGE_DAYS` (default 30) while keeping a fresh one, ignores files that don't
    match the `journal-compose-force-*.json` glob, does not raise when the marker directory is
    absent, and honors a custom `max_age_days` override. Age-based sweeping is safe here — unlike
    e.g. a branch-lifetime-scoped baseline snapshot — because a marker is only ever consulted on the
    same calendar day it was written; any earlier date's marker has zero remaining utility the
    moment that day ends. Extended for dev-env#806 with
    `test_cleanup_stale_markers_tmp_orphan_swept_by_tmp_cleanup()`: a stale (just past
    `MARKER_CLEANUP_MAX_AGE_DAYS`) orphaned `journal-compose-force-<date>.json.<pid>.tmp` — left
    behind if `write_marker()`'s `os.replace` ever fails, e.g. a rare Windows sharing violation — is
    swept by the new second `cleanup_stale_sentinels(..., ext=".tmp")` call `cleanup_stale_markers()`
    now makes, while a fresh in-flight `.tmp` and the live `.json` marker are both spared. Driven
    through the real `cleanup_stale_markers()` end-to-end via `_with_marker_dir` (the function has no
    `scratch=` param of its own) rather than by calling `_hookutil.cleanup_stale_sentinels` directly,
    so the pre-existing `.json` sweep and the new `.tmp` sweep are proven not to interfere with each
    other in one call. Mirrors the identical fix in `dev-env-sync.py` (dev-env#797/PR #800, item 56)
    and `hook-liveness-check.py` (dev-env#802/PR #805, item 67).

    ```bash
    py -3 claude/scripts/tests/test_journal_compose_force.py
    ```

54. **journal-compose-force-resolve end-to-end test** — required when changing
    `claude/scripts/journal-compose-force-resolve.py`. Drives the real script as a subprocess
    (mirroring `test_canonical_mutate_guard.py`'s `_run_hook` pattern) with
    `JOURNAL_COMPOSE_FORCE_MARKER_DIR` redirected at a disposable temp dir: pins the printed
    `FORCE=true`/`FORCE=false` line for a `--force`-bearing argument, a force-less argument, a bare
    `--force`, and no argument at all; that the marker written to disk carries the expected schema
    (`force`, `raw_arguments`); that a second invocation overwrites the first day's marker rather than
    merging with it; and that a nonexistent marker directory is created rather than erroring. The
    script's own logic (`resolve_force`, `build_marker`, `write_marker`) is unit-tested directly in
    item 53 above — this file exercises only the CLI-glue layer (argv handling, stdout format,
    on-disk effect). Also exercises the new `cleanup_stale_markers()` wiring (dev-env#768): a stale
    marker from an earlier calendar date, planted in the redirected marker dir before invocation, is
    swept by the same run that writes today's own marker — proving the sweep call and the write
    don't race each other (today's fresh write always survives its own run's cleanup pass).
    ([ADR-096](adr/096-journal-compose-mechanical-force-guard.md); dev-env#631)

    ```bash
    py -3 claude/scripts/tests/test_journal_compose_force_resolve.py
    ```

55. **journal-compose-force-guard test** — required when changing
    `claude/scripts/pre-tool-use-journal-compose-force-guard.py`. Two layers, mirroring this hook
    family's established split. Pure command-classification tests exercise
    `segment_targets_today_compose()`/`command_targets_today_compose()` offline against every real
    `journal-compose` `SKILL.md` command shape: `worktree add` matching via a today-dated `-C` value
    and via the positional ref, a non-today date correctly not matching, a `-C`-scoped commit/push
    matching, **the key regression this hook exists to avoid** — a commit message merely *mentioning*
    "draft/2026-07-09" or "compose-2026-07-09" as prose (this repo's own commits legitimately discuss
    this pattern) never matching, whether via `-m`, `--message`, or `--message=` — a bare push with no
    date reference not matching, read-only `status`/`diff` never gated, `branch -D` cleanup
    (out of scope by design) never gated, `worktree remove` matching loosely (harmless — the marker
    already exists by the time cleanup runs), a heredoc-body-only mention not matching, the
    `draft/<date>-recovery` suffix still extracting the base date, an env-prefixed git invocation
    still classified correctly, and the `--git-dir=`/chained-segment forms. A behavioral layer drives
    the real hook end-to-end over stdin via subprocess (today-dated fixtures built from
    `datetime.date.today()` at test-run time, never hardcoded, so the suite is deterministic
    regardless of which day it runs), with `JOURNAL_COMPOSE_FORCE_MARKER_DIR` redirected at a
    disposable temp dir: no marker blocks (reason on stderr, empty stdout); a fresh `force=true`
    marker allows; `force=false` blocks; a **stale** `force=true` marker (past
    `MAX_MARKER_AGE_SECONDS`) blocks; a **corrupt** marker file **fails closed** (the deliberate
    reversal of this hook family's usual fail-open convention); a non-today date allows regardless of
    marker state (this hook's trigger condition never reaches it); and malformed JSON / empty stdin /
    a non-Bash tool / a missing `command` / non-dict JSON all fail open. Extended during `/review` on
    PR #671 with four fixes and their regression coverage: a `-c <name>=<value>` git-level flag (e.g.
    `-c core.hooksPath=x`) no longer lets its config value get mistaken for the verb, silently
    escaping the gate entirely (pure-classification cases plus an end-to-end no-marker-still-blocks
    case); the `git commit -am "..."` combined-short-flag idiom and the glued `-m<value>`/`-F<value>`
    (no space) forms are now excluded from the date scan exactly like the separate-token
    `-m`/`--message` forms (plus a `-ma` glued-value edge case pinning it resolves to a message value,
    per real git/getopt semantics, rather than misparsing); a cheap `today not in command`
    substring pre-filter now short-circuits the interpreted `split_top_level` parse for the
    overwhelming majority of Bash calls, since this hook is globally registered and runs on every one
    (pinned via a dateless-command case, deliberately placed in `command_targets_today_compose` rather
    than `segment_targets_today_compose` so the prose-exclusion tests still exercise the real parse
    path); and an end-to-end case pinning that a timezone-aware marker blocks cleanly (exit 2, no
    traceback on stderr) rather than crashing open (see item 53's `_journal_compose_force` entry for
    the underlying `is_marker_fresh()` fix). Extended for dev-env#718 (Phase A of the
    hook-reliability initiative dev-env#717) with a crash-path e2e case
    (`test_e2e_crash_after_trigger_fails_closed`): the new `__main__` crash guard converts an
    unexpected `main()` crash on a matched same-day compose target into a fail-CLOSED exit 2 (it was
    exit 1 = fail-OPEN before). Because the gate is otherwise fully defensive (`read_marker` /
    `is_marker_fresh` swallow their own exceptions), this is driven by a small env-gated
    (`JOURNAL_COMPOSE_FORCE_GUARD_TEST_CRASH`), production-inert fault-injection seam in `main()`;
    `_run_hook` now pops that var from the inherited env so only the opt-in test trips it. The gate's
    *top-level imports* deliberately stay fail-OPEN (it runs on every Bash call, so a broken
    `_hookio` must not exit 2 and block all Bash).
    ([ADR-096](adr/096-journal-compose-mechanical-force-guard.md); dev-env#631, dev-env#718)

    ```bash
    py -3 claude/scripts/tests/test_pre_tool_use_journal_compose_force_guard.py
    ```

56. **dev-env-sync test** — required when changing `claude/scripts/dev-env-sync.py`. Exercises
    the pure message-formatting helpers offline (no subprocess, no network, no git): `_plural()`
    singular/plural; `_count_from()` parsing a clean `git rev-list --count` result and failing
    open to `0` on a non-zero returncode or non-digit stdout (this diagnostic is advisory only,
    never load-bearing); `format_sync_note()` short-SHA truncation and pluralization;
    `format_pull_failure_message()` preserving git's own named-conflicting-file text alongside
    the new behind-count clause; `format_diverged_message()` showing both the ahead and behind
    counts for a true fork, and — a review finding on this same PR — using distinct,
    non-contradictory wording ("is ahead of origin/main", not "has diverged") when `behind == 0`
    (local merely has commits origin/main doesn't, e.g. a commit landed directly on the
    canonical) rather than always claiming divergence; and `format_pulled_message()` — matching
    pre/post commit counts print no extra note, a genuine mismatch (the pre-pull `behind_count`
    disagreeing with the actual post-pull `git log` range — the central ambiguity in
    dev-env#694's unreproduced "Pulled 0 commits" report) prints an explicit "a concurrent
    process likely moved origin/main mid-pull" note, a `behind_count == 0` case is instead
    labeled "could not be measured" rather than misattributed to a concurrent process (another
    review finding — `base == local` and `local != remote` are already established at the call
    site, so a real measurement is always >= 1, meaning 0 can only be `_count_from`'s fail-open
    sentinel), the local/remote short SHAs now actually appear in the success message (a third
    review finding — the parameters were previously accepted but never used, contradicting this
    PR's own stated behavior), and the existing >5-lines-pulled truncation trailer is unchanged.
    Also pins that every formatter's output is `.encode("cp1252")`-safe (not the stricter
    `.isascii()`, since the pre-existing em dash is non-ASCII but cp1252-safe) — a fourth review
    finding, since this PR's entire premise is fixing a cp1252 failure and the original test
    suite had no assertion that would catch a regression. Also (dev-env#797,
    [ADR-110](adr/110-escalate-persistent-dev-env-sync-ff-failures.md)) exercises the
    persistent-ff-failure escalation helpers offline: `parse_blocking_files()` (git's two
    `--ff-only`-abort stderr shapes — a dirty tracked file and an untracked-file conflict —
    multiple files in order, and the no-tab-lines / empty cases -> `[]`); `format_duration()`
    (the under-a-minute / `Xm` / `Xh` / `Xh Ym` renderings and negative-clamp);
    `record_failure()` (a fresh run from `None`, an ongoing run preserving `first_failure_at`
    while incrementing the count, and the malformed-prev / valid-timestamp-corrupt-count
    recovery paths, including `bool`-rejection so a JSON `true` can't masquerade as a
    timestamp/count); `should_escalate()` (the count and time boundaries in isolation, and the
    key robustness property that the time arm still fires when the count was lost to a
    concurrent-write race); `format_escalated_pull_failure_message()` (naming the
    consecutive-prompt count, duration, commits-behind count + SHAs, blocking file path(s), the
    STALE-tooling blast radius, and still echoing git's own diagnostic; singular
    prompt/commit; graceful degradation when git names no files; and `.isascii()`-safety for the
    stdout channel); and the best-effort scratch-state I/O (`read`/`write`/`clear_failure_state`)
    against a `tempfile.TemporaryDirectory()` — a write/read round-trip, no `.tmp` orphan left by
    the atomic `os.replace` swap, `clear` deleting (and a double-`clear` being a no-op), and
    malformed / non-dict JSON both reading back as `None`. The PR #800 `/review` (two independent
    opus reviewers) added coverage for its fixes: `build_failure_response()` — the escalate-vs-plain
    decision extracted from `main()` into a pure helper — is unit-tested for the fresh/plain,
    count-arm, and time-arm cases (so the state machine is no longer exercised only through the
    git-shelling `main()`); the escalated message's `behind == 0` guard (renders "unmeasured number
    of commits", never the "0 commits behind … STALE" self-contradiction PR #701 fixed in the sibling
    formatters); the `_hookout.ascii_sanitize` of the echoed `git_stderr` in BOTH failure formatters
    (a non-cp1252 stderr no longer `UnicodeEncodeError`s the advisory away — asserted `.isascii()` for
    the all-ASCII escalated message and `.encode("cp1252")` for the one-off message, which carries a
    pre-existing cp1252-safe em dash); `read_failure_state`'s `(OSError, ValueError)` catch (a non-UTF-8
    state file reads back as `None`); the per-PID-tmp write isolation (monkeypatched `os.getpid`,
    mirroring `test_journal_compose_force.py`); and the `.tmp` orphan sweep (a stale orphan reaped,
    a fresh in-flight one spared). `main()`'s remaining glue (`read` → `build_failure_response` →
    `write` + `print`, the `clear_failure_state` calls, and the `_hookutil.cleanup_stale_sentinels`
    backstop sweeps) is not covered (pure-helper convention). The git/subprocess orchestration
    (fetch, rev-parse, merge-base, the actual pull, and the off-main worktree-topology diagnosis
    reused from `_worktree_topology.py`) is not covered here — it has no local pure logic beyond
    the formatters above, matching this repo's established convention for topology-diagnosing
    orchestration scripts (items 22/26/30; PR #661's own note that this file previously had
    "zero local pure logic"). ([ADR-098](adr/098-dev-env-sync-advisories-to-stdout.md),
    [ADR-110](adr/110-escalate-persistent-dev-env-sync-ff-failures.md);
    dev-env#694, dev-env#797)

    ```bash
    py -3 claude/scripts/tests/test_dev_env_sync.py
    ```

57. **journal-canonical-guard stdout-routing test** — required when changing
    `claude/scripts/journal-canonical-guard.py`. End-to-end only (no pure-function layer — this
    fix adds no new logic, only a stream-routing change); drives the real hook via subprocess
    against a disposable throwaway git repo, using the `JOURNAL_CANONICAL_GUARD_REPO_PATH` test
    seam PR #661 already built in. Pins: a missing repo path and a canonical already on `main`
    are both silent no-ops (no output at all); a canonical on a legitimate non-hijacked branch
    (e.g. `draft/2026-07-10`, the common case per the documented Stub file workflow) is left
    untouched; **the core regression proof** — a hijacked-and-dirty canonical's warning, and a
    hijacked-canonical-blocked-by-a-squatter warning, both land on **stdout with stderr
    empty** (dev-env#699 — previously both were on stderr, invisible to Claude on this
    always-exit-0 `UserPromptSubmit` hook); and the already-working auto-return success message
    stays on stdout (regression pin). Two warning paths (worktree-list-unreadable,
    auto-return-checkout-failed) are deliberately not exercised — see the test file's own
    docstring for why each is fragile to construct reliably, matching this repo's established
    convention for hard-to-construct git-failure-injection paths (items 22/26/30). This is a
    deliberate, narrow departure from ADR-093's "no dedicated test file" precedent for this
    file — that decision was reasoned about topology-*decision* correctness (unaffected, still
    covered by item 22's `test_worktree_topology.py`), not the orthogonal stream-*routing* axis
    this test suite covers. ([ADR-099](adr/099-journal-canonical-guard-advisories-to-stdout.md);
    dev-env#699)

    ```bash
    py -3 claude/scripts/tests/test_journal_canonical_guard.py
    ```

58. **stop-journal-stub-checkpoint test** — required when changing
    `claude/scripts/stop-journal-stub-checkpoint.py`. Two layers, mirroring this hook family's
    established split. Pure-helper tests exercise the detection/decision core offline (no stdin,
    network, gh, or disk): `report_intent` (report-group and verify/deploy-group keywords in a
    genuine user prompt; the text-item content form; and the false-positive guards — a keyword in
    assistant text, a tool_result, an `isMeta`/`isCompactSummary` record, or a `<command-name>`
    slash-command wrapper never counts); `substantive_tool_count` (counts each of the nine
    substantive tools, ignores `TodoWrite`/`spawn_task`/text, counts parallel tool_use in one
    record, and the 4-vs-5 threshold boundary); `opened_or_merged_pr` (anchored `gh pr create`/
    `merge` via the shared `_hookio.scan_top_level`, with heredoc-body / `$()`-subshell mentions, a
    `--help`-only invocation, and `gh pr checks`/`view` all excluded); `wrote_stub` (a Write/Edit
    `*.stub.md` file_path, a backslash path, a non-stub `.md` non-match, and a Bash `git add
    ...stub.md` reference — the Bash side uses a `re.search`, not a `"..." in command` check, so this
    file stays clear of item 39's AST gate); `is_review_only_session` (the `<command-name>/review`
    wrapper, not prose mentioning `/review`); `skip_override` (a genuine user "skip journal"/"no
    stub", and that a tool_result or `isCompactSummary` mention does NOT waive); `evaluate` (the
    FIRE case `(True, False)`; each terminal exemption — stub written / PR opened / skip / `/review`
    — resolving to `(False, True)`; and the two non-terminal no-ops — no intent, or intent but
    count < 5 — as `(False, False)`); `format_reminder` (`.isascii()` + `.encode("cp1252")` so the
    exit-2 stderr text can't vanish under Claude Code's cp1252 hook-output pipe on Windows, plus the
    `[journal-stub-checkpoint]` prefix and the dismissal text); and that malformed/non-dict records
    spliced around the FIRE fixture still yield `(True, False)` without raising. A behavioral layer
    drives the real hook end-to-end over stdin via subprocess with HOME/USERPROFILE pointed at a
    temp dir (sentinel isolated from the real `~/.claude/scratch`, mirroring items 48/50): the FIRE
    case blocks (exit 2, reminder on **stderr**, **empty stdout** — a Stop hook's exit-0 stdout is
    not added to Claude's context, [ADR-091](adr/091-journal-stop-check-archive-reminder-blocking.md));
    wrote-stub / opened-PR / `/review` / skip / no-intent (pre-filter fast-exit) /
    intent-below-threshold all exit 0; `stop_hook_active=true` exits 0 (loop guard); and running the
    FIRE fixture twice under one `session_id` fires once then is suppressed by the sentinel.
    `main()`'s stdin/sentinel plumbing beyond the end-to-end runs is not separately unit-tested
    (pure-helper convention). Extended during `/review` on PR #706 with the narrowed-keyword
    non-fires (`analytics`, bare `deploy`) and the `check the deploy` still-fires case, the
    write-scoped stub-Bash detection (an `ls`/`cat` read of a `*.stub.md` does not count; a
    `git add`/redirect write does), the `/review-*` hyphen boundary, the non-ASCII-cwd cp1252
    sanitization, and the empty-stdin / non-dict-payload fail-open e2e paths (64 tests total).
    ([ADR-100](adr/100-stop-journal-stub-checkpoint-hook.md); dev-env#702)

    ```bash
    py -3 claude/scripts/tests/test_stop_journal_stub_checkpoint.py
    ```

59. **pre-bash-drift-check test** — required when changing `claude/scripts/pre-bash-drift-check.py`.
    Exercises the pure `should_check_drift(age_seconds, min_gap)` gate offline: `None` age (no
    prior state file yet) -> `False`; an age below the threshold -> `False`; the exact boundary
    (`age == min_gap`) -> `False` (strict `>`, the cheaper "skip" side owns the boundary, matching
    `disk-space-check.py`'s `classify_free_space` convention — item 47); an age above the
    threshold -> `True`; and a negative age (future/skewed mtime) -> `False`. Also pins
    `build_message()`'s `[bash-drift-check]` tag wrapping. `format_drift_warning` itself is not
    re-tested here — already fully covered by `test_bash_state.py` (item 42), which also covers
    the new `state_age_seconds()` and `drift_warning_for()` helpers this hook's gate and `main()`
    read/call. `main()`'s stdin plumbing and `drift_warning_for()`'s underlying git subprocess call
    are not covered (pure-helper convention, matching `pre-commit-branch-check.py` /
    `pre-pr-create-check.py` / `pre-merge-branch-check.py`'s own test files — items 43-45).
    ([ADR-101](adr/101-bash-drift-check-every-call.md); dev-env#682)

    ```bash
    py -3 claude/scripts/tests/test_pre_bash_drift_check.py
    ```

60. **_hookout emitter test** — required when changing `claude/scripts/_hookout.py`. Exercises the
    pure surface offline (no stdin, network, gh, or disk): the `plan_emission` channel-routing
    matrix across every (audience x event-class x blocking) cell, `ascii_sanitize`, the exit-0 JSON
    shape, and the `.isascii()` wire-safety guarantee. Pins that a `model` advisory on a context
    event (UserPromptSubmit / SessionStart / UserPromptExpansion) routes to
    `hookSpecificOutput.additionalContext` (exit 0), a `model` advisory with `blocking=True` (on a
    context event AND on a non-context event) routes to exit-2 stderr, a `user` advisory routes to
    `systemMessage` (exit 0) on any event, and `both` on a context event carries both keys in one
    JSON object; that the undeliverable classes raise `ValueError` — a non-blocking `model` advisory
    on a non-context event (there is no non-blocking model channel there), `user`+`blocking`, `both`
    wherever its model half isn't deliverable, an invalid audience, and `event=None` non-blocking.
    Pins `ascii_sanitize`'s punctuation/operator map (dashes, curly quotes, ellipsis, arrows,
    comparison ops, bullet, middle dot, and the no-break space spelled `chr(0xA0)` so the source is
    unambiguous), the emoji/unmapped `?` backstop, C0-control/DEL neutralization (except
    newline/tab, so an ANSI/ESC or carriage-return sequence can't reach the raw stderr stream),
    None/non-str coercion, and that the result is ALWAYS `.isascii()`; that exit-0 stdout is valid
    JSON and stays `.isascii()` even when the
    advisory text carries Unicode (ensure_ascii escaping) while `json.loads` restores the original
    content; and that exit-2 stderr is `.isascii()` (ascii_sanitize applied). The `emit_advisory` /
    `emit_block` deliverers are exercised in-process (redirecting stdout/stderr to `io.StringIO` and
    catching the `SystemExit` they raise — still offline, no subprocess): each writes the planned
    stream and exits with the planned code, an undeliverable `emit_advisory` propagates the
    `ValueError` rather than reaching a stream write, and `_deliver` still delivers the exit code
    even when the stream write raises `OSError` (closed-pipe resilience — a block's exit code is
    load-bearing and must survive a broken pipe).
    ([ADR-103](adr/103-shared-hookout-emitter.md); dev-env#719)

    ```bash
    py -3 claude/scripts/tests/test_hookout.py
    ```

61. **hook output-contract + ASCII-literal gate** — required when changing
    `claude/scripts/tests/test_hook_output_contract.py` or the shared
    `claude/scripts/tests/_hook_wiring.py` (the settings.json parser all three PR3 gates —
    items 61/62/63 — share; run all three when changing it). AST gate over every wired hook
    (via `_hook_wiring`, cross-referencing each script's event class against the SSOT
    `_hookout.STDOUT_MODEL_VISIBLE_EVENTS`, ADR-103) for four invisible-emission shapes: **A**
    stderr write whose governing exit is 0 (invisible everywhere); **B** *bare* stdout write whose
    governing exit is 0 on a hook wired only to non-context events (model-invisible there — a
    `json.dumps(...)`-wrapped write is not B; Check D inspects its payload keys instead); **C** stdout
    write whose governing exit is 2 (dropped — exit 2 ignores stdout, NOT json.dumps-exempt), now also
    firing **cross-function one level**: a stdout emission in a helper whose direct call site's
    continuation reaches `sys.exit(2)` is dropped by the caller's exit 2 (`analyze_dropped_by_caller`,
    dev-env#727); **D** a `json.dumps({"hookSpecificOutput": {"additionalContext": ...}})` stdout write,
    governing exit 0, on a non-context-only hook — model-invisible there (additionalContext is honored
    only on the context events), while a `json.dumps({"systemMessage": ...})` write stays exempt (the
    user channel, any event). D is the structured-channel refinement of Check B's former blanket json
    exemption (dev-env#727). Plus an ASCII-literal lint: a non-`.isascii()` string literal passed
    directly to a raw-stream call (json.dumps-exempt via `ensure_ascii`). Governing exit is a documented
    reaching approximation (forward-then-ascending scan; a `return`/scope-end -> exit 0; compound
    statements scanned over are pass-through — can only over-flag into the allowlist, never miss an
    emission co-located with its `sys.exit(2)`, incl. the if/else-branch-then-exit-2 shape by ascent).
    Pins the reaching self-tests (stderr->exit0/exit2, if/else ascent, bare-print fall-through,
    `print(file=...)` stream classification, the `json.dumps` systemMessage exemption, the
    `ensure_ascii=False` non-exemption), the payload-channel classifier
    (systemMessage/additionalContext/both-keys/dynamic/no-known-keys), the pure `_classify_emission`
    (A/B/C/D plus the exempt cells), and the cross-function drop-by-caller pass (helper-stdout + exit-2
    caller flagged; exit-0 caller / helper-stderr / helper-own-exit-2 / two-hops not flagged). Two-sided
    allowlists (the `test_no_crude_command_substring_checks.py` mechanism): `_OUTPUT_CONTRACT_ALLOWLIST`
    keyed by `(script, check)` (**empty** as of PR6/dev-env#740 — PR5 swept the PostToolUse hooks and PR6
    the Stop-family hooks, so every output-contract offender is now migrated onto `_hookout`; a new entry
    the gate reports is a genuine regression), `_NONASCII_EMISSION_ALLOWLIST` keyed by script (**empty**
    as of PR7/dev-env#743 — `dev-env-sync.py`'s 4 em-dash print() calls were hand-fixed to plain ASCII
    hyphens, the last remaining offender; token-tracker's 2 lines were cleared in PR6 when the per-turn
    echoes carrying them were dropped; a new entry the gate reports is a genuine regression). Documented limitations: the ASCII lint and Check D both see only literals
    *direct* in the emission call (usage-snapshot's emoji reach stderr via a variable, flagged only via
    an incidental em-dash — PR5's own `.isascii()` self-pin covers the emoji; a json payload built from
    a variable classifies as "unknown" and is exempt), and the cross-function Check C pass is one level
    and matches only a bare-`Name` call site (a two-hop or `mod.helper()` call is not traced). No wired
    hook hits Check D or the cross-function C today (both latent).
    ([ADR-103](adr/103-shared-hookout-emitter.md); dev-env#720, dev-env#727, dev-env#740)

    ```bash
    py -3 claude/scripts/tests/test_hook_output_contract.py
    ```

62. **hook safe-exit structural gate** — required when changing
    `claude/scripts/tests/test_hook_safe_exit_guard.py` (or `_hook_wiring.py`, item 61). Structural AST
    gate: every wired hook has a top-level `if __name__ == "__main__":` block with a
    `try: ... except Exception|<bare>:` handler that deterministically `sys.exit(N)`s (a literal, or a
    one-level call to a module-level helper that does — e.g. pre-auto-merge-checkpoint-gate's
    `_fail_closed`), and that N equals the hook's declared fail direction in `FAIL_CLOSED` (exit 2 for
    the two ADR-083/ADR-096 fail-closed gates, exit 0 for every other wired hook — including the
    *blocking* gates that exit 2 to block but fail OPEN on their own crash). An `except SystemExit: raise`
    pass-through is correctly ignored (not mistaken for the fail-direction handler). Pins the guard-shape
    self-tests (guarded exit 0/2, exit-2-via-helper, unguarded bare `main()` / `sys.exit(main())` / no
    `__main__` block / handler-without-exit, wrong-direction reported faithfully) and that `FAIL_CLOSED`
    names only wired scripts. Two-sided `_UNGUARDED_ALLOWLIST` (**empty** as of PR7/dev-env#743 — the last
    14 offenders (awake-blocker, idle-refresher, multi-worktree-alert, pre-commit-branch-check,
    pre-merge-branch-check, pre-merge-findings-gate, pre-merge-message-check, pre-merge-numbering-check,
    pre-pr-create-check, pre-tool-use-canonical-mutate-guard, pre-tool-use-worktree-path-check,
    session-mode-prompt, token-tracker, turn-count-hook) were all guarded; a stale entry, i.e. a
    now-guarded script, fails too, and a new unguarded hook not in the allowlist also fails). A
    guarded hook whose crash-exit contradicts its declared direction is a hard failure, never
    allowlist-able. Does NOT verify the fail-closed gates' module-level dependency-load guard (rule 5) —
    that surface is pinned by items 52/55. ([ADR-103](adr/103-shared-hookout-emitter.md);
    dev-env#720)

    ```bash
    py -3 claude/scripts/tests/test_hook_safe_exit_guard.py
    ```

63. **settings-hook wiring lint** — required when changing
    `claude/scripts/tests/test_settings_hook_wiring.py` (or `_hook_wiring.py`, item 61) or the `hooks`
    block of `claude/settings.json`. For every `(event, matcher, hook)` entry: the command resolves to a
    `<name>.py` that exists in `claude/scripts/`, and it carries an explicit integer `timeout` (seconds)
    at or above its budget floor — `usage-snapshot.py` -> 90, a hook importing `_winsubp` (the
    subprocess-hook marker, authoring rule 4) -> 30, pure-Python -> 10 (a FLOOR, `>=`, so a longer
    timeout is allowed). Pins `min_timeout`'s three tiers and `script_from_command`. The `pyw -3`
    invocation invariant (authoring rule 3) is deliberately not re-checked here — `test_pyw_stdio.py`'s
    `test_all_settings_hooks_use_pyw_and_resolve_to_repo` (item 2) already gates it; the resolution check
    overlaps that test's resolution half by design (it is the precondition for the `_winsubp`-based
    budget). Iterates entries generically, so a new event/matcher group (e.g. PR9's PowerShell mirror) is
    covered with no change beyond any new script's budget classification.
    ([ADR-103](adr/103-shared-hookout-emitter.md); dev-env#720)

    ```bash
    py -3 claude/scripts/tests/test_settings_hook_wiring.py
    ```

64. **run-hook-tests runner test** — required when changing `claude/scripts/run-hook-tests.py`.
    Exercises the runner's pure helpers offline (tempfile fixtures; no subprocess, no network, no
    real test execution): `discover_python_tests` / `discover_bash_tests` (the `test_*.py` / `*.sh`
    glob with `test_`-prefix and leading-`_` filtering, multi-directory union, and missing-directory
    tolerance), `runner_skip_reason` / `SKIP_TESTS` (the deliberately single-entry runner-skip list —
    `test_pyw_stdio.py`, a real-`pyw` Windows-subsystem stdio probe a non-interactive CI runner can't
    host — pinned so any future runner-skip is a test-visible change), `_command_for` (the interpreter
    argv for a `.py` vs `.sh` file, plus the bash-missing → `None` and non-test-suffix → `None`
    cases), `classify_result` (the pass / self-skip / fail mapping, including a non-zero exit
    overriding a `SKIP:` marker so a bash gate that prints `SKIP:` but still fails is a real failure),
    and `suite_discovery_error` (dev-env#730 review — the zero-Python-test floor guard that turns a
    broken `REPO_ROOT`/glob into a loud failure instead of a silent green). The `_run_one`
    bash-missing branch (which returns a skip *without* shelling out) is covered too. The runner's
    `main` and the subprocess-spawning path of `_run_one` (which shell out to run the real suite) are
    not covered — the
    runner's end-to-end acceptance test is the first green run of the `hook-tests` CI workflow
    (`.github/workflows/hook-tests.yml`, `windows-latest`, `pull_request`), which is also how the
    whole suite is now gated on every PR ([ADR-103](adr/103-shared-hookout-emitter.md);
    dev-env#721).

    ```bash
    py -3 claude/scripts/tests/test_run_hook_tests.py
    ```

65. **token-tracker test** — required when changing `claude/scripts/token-tracker.py`. Exercises the
    pure helpers offline (no stdin, network, gh, or disk): `format_locate_error(session_id)` — the
    user-facing "could not locate the transcript" diagnostic, now delivered via
    `_hookout.emit_advisory(audience="user")` as a systemMessage (exit 0) because a Stop hook's exit-0
    stderr is invisible (dev-env#740, [ADR-103](adr/103-shared-hookout-emitter.md)) — pinned
    `.isascii()` + `.encode("cp1252")` (so it can't vanish under Claude Code's cp1252 hook-output pipe on
    Windows), names the hook + the session id + "not recorded", and stays ASCII for an empty session id;
    `should_advise_locate_failure(session_id, scratch=tmp)` — the once-per-session guard added in the
    PR6 review (dev-env#740): a Stop hook fires every turn-end, so a persistently unlocatable transcript
    would re-toast every turn without it; pins advise-once-then-suppress, per-session (not global)
    keying, and the empty-session-id "always advise" fallback (can't dedupe -> fail toward visible);
    plus `get_pricing` (known models incl. a dated/suffixed id via substring match, and the default
    fallback for an unknown/empty model) and `compute_cost` (the four-bucket per-million arithmetic, and
    `$0` for empty usage). Also (dev-env#804) `read_token_log_lines(path)` — extracted from `main()`'s
    previously-inline, unguarded `TOKEN_LOG.read_text(encoding="utf-8")` (which let a non-UTF-8 log's
    `UnicodeDecodeError` escape uncaught, silently dropping the current session's entire summary) so the
    degrade-to-fresh-log behavior is unit-testable: a missing, unreadable (`OSError`, incl. a directory
    path), or non-UTF-8 (`UnicodeDecodeError`) log all resolve to `[]`, and a real log round-trips its
    lines unchanged (same fix shape as item 42's `_bash_state.read_state`, dev-env#801); and
    `_count_turns` / `aggregate_session` — a happy-path smoke test each (pinning that the shared
    `_iter_file_lines_safely(path)` generator refactor didn't change normal behavior) plus the matching
    resilience case: a file of only invalid UTF-8 bytes degrades to the zero/None defaults instead of
    raising, so a corrupted subagent or session transcript no longer crashes `main()` before it can
    record anything (dev-env#804 — these two functions had no prior test coverage; kept to a smoke test
    plus the new behavior rather than backfilling exhaustive pre-existing-behavior coverage). A `/review`
    finding on PR #808 found the two functions had each pasted an identical try/except-around-open()+
    for-loop scaffold; both now consume the shared `_iter_file_lines_safely` generator instead, exercised
    only indirectly through these same two functions' tests (it has no behavior of its own beyond what
    its callers already prove). A second `/review` finding on the same PR: `main()`'s existing-session
    dedup now writes `TOKEN_LOG` fresh (instead of appending) whenever `read_token_log_lines` returns `[]`
    — covering a missing, empty, *or* corrupted log, which it can't distinguish — so a corrupted log
    actually gets replaced instead of accreting unreadable growth forever; this branch lives entirely
    inside `main()`'s untested I/O and has no dedicated test, consistent with the convention below. Added
    in PR6 (the file had no test before): the migration moved the transcript-locate diagnostic onto the
    systemMessage channel and **dropped** the two per-turn stdout status echoes (invisible on a Stop hook,
    and a systemMessage in their place would be per-turn toast spam), which also cleared token-tracker's
    `_OUTPUT_CONTRACT_ALLOWLIST` entries (A + B) and its `_NONASCII_EMISSION_ALLOWLIST` entry (the
    `…`/`—` lived only in the dropped echoes) — verified by item 61's gate. `main()`'s remaining I/O
    (stdin parse, transcript locate, the log write/rewrite, the emission) is not covered (pure-helper
    convention); the emission channel is pinned by items 60/61 (`test_hookout.py` / the output-contract
    gate).

    ```bash
    py -3 claude/scripts/tests/test_token_tracker.py
    ```

66. **journal-draft-worktree-guard test** — required when changing
    `claude/scripts/pre-tool-use-journal-draft-worktree-guard.py`. Two layers, mirroring item 33's
    (`canonical-mutate-guard`) convention: pure-function tests of `find_worktree_add_blocks()` (a
    bare `git worktree add <path> ... draft/YYYY-MM-DD` blocks unconditionally, no git subprocess
    needed and — unlike `find_checkout_candidates()` below — a leading `cd` provides NO exemption
    here, since this function needs no cwd at all; `-b <newbranch>` naming a *different* branch with
    `draft/YYYY-MM-DD` as a mere commit-ish startpoint is correctly NOT blocked, while `-b
    draft/YYYY-MM-DD` itself IS; `--detach` is never a candidate (a detached HEAD holds no branch
    ref); a mention inside a commit-message heredoc body does not trigger) and
    `find_checkout_candidates()` (ambient/`-C`-redirected `checkout`/`switch` of a
    `draft/YYYY-MM-DD`-shaped branch is a candidate with its redirect dirs captured, resolution
    deferred to `main()`; a `cd` anywhere DOES take this extractor's candidates out of scope, since
    resolving a target genuinely requires a real cwd; `checkout <branch> -- <path>` — a file
    restore, not a branch switch — is not a candidate, but a *trailing* `--` with nothing after it
    still switches branches and IS a candidate; a non-draft branch is not a candidate);
    `_worktree_add_target()`'s `-b`-present-vs-absent token scan (including a dangling `-b` with no
    value not raising) and its `--detach` short-circuit; and `_has_override()` (a leading
    `ALLOW_JOURNAL_DRAFT_WORKTREE=1` bypasses; the same text merely quoted inside a commit message
    does not). Plus end-to-end `main()` tests via subprocess against real throwaway git repos: `git
    worktree add` onto a draft branch blocked (exit 2) from any repo, including when preceded by an
    unrelated `cd`; a `-C` redirect at the (env-overridden) journal canonical, with the path
    rendered in **forward slashes** — matching the documented production convention, and load-
    bearing: `_tokenize()`'s `shlex.split(posix=True)` silently strips every backslash from a
    Windows-rendered path, so a test built via `str(Path(...))` would pass regardless of whether the
    canonical-matching logic is even correct — allowed (exit 0), the one legitimate case; the
    identical checkout with no redirect from a non-canonical cwd blocked (exit 2), naming the
    resolved (wrong) target; the override token bypassing the block; a non-draft `checkout main`
    allowed regardless of cwd; and malformed JSON / missing cwd / non-Bash `tool_name` / non-object
    JSON all failing open. A relative `-C` redirect resolving against the *command's* cwd (not the
    hook script's own process cwd) — the identical fix `_blockable_redirect_root` needed in item
    33's hook (dev-env#576/PR#584) — was verified manually against a real target during development
    and is exercised by the redirect test above; the resolution loop also shares a `toplevel_cache`
    across candidates within one command, the same memoization item 33's hook uses. Five gaps in
    this list (the `cd`-bypass on `find_worktree_add_blocks`, the tautological forward-slash-vs-
    backslash `-C` test, the trailing-`--`-still-switches case, the `--detach` false-positive, and
    the park-and-remove/park-only tests in item 26 below not actually verifying `git worktree
    remove` was invoked) were found by `/review` on the PR that introduced this hook and fixed
    before merge — each independently confirmed via a sabotage-then-reconfirm check (break the
    logic, watch the test fail, restore, watch it pass), not just re-read. See
    [ADR-105](adr/105-draft-branch-worktree-squat-guard.md).

    ```bash
    py -3 claude/scripts/tests/test_journal_draft_worktree_guard.py
    ```

67. **hook-liveness-check test** — required when changing `claude/scripts/hook-liveness-check.py`.
    Exercises the pure helpers offline (tmp dirs for heartbeat files, hand-built settings.json
    fixtures — never the live `claude/settings.json`, so this suite's pass/fail doesn't drift with
    production wiring changes; [ADR-106](adr/106-hook-heartbeat-liveness-ledger.md);
    dev-env#745, PR8 of #717): `hook_name_from_command` (script-basename-minus-`.py` extraction,
    matching the literal string every hook passes to `_hookutil.record_heartbeat` — trailing
    whitespace tolerated, a non-`.py` command and empty/`None` input both -> `None`);
    `wired_hook_events` (a settings.json fixture mapping each wired hook name to the union of
    events it's registered under; multiple matcher groups under one event — e.g.
    `journal-shard-write-advisory`'s real Bash/Write/Edit tripling — dedupe to a single event
    membership; a non-`.py` command contributes nothing; malformed structure at any level — a
    non-list event group, a non-dict group, non-dict hook entries, a non-dict top-level `hooks`
    key — degrades to skipping that piece rather than raising); `exempt_hooks` (a hook wired
    *exclusively* to `PostCompact` and/or `Notification` is exempt; a hook also wired to any other
    event — e.g. `awake-blocker`'s real `Notification`+`UserPromptSubmit`+`Stop` shape — is NOT
    exempt even though `Notification` is among its events; a hand-built empty event set is not
    exempt either, guarding the vacuous-subset case); `stale_hooks` (fresh/missing/old/malformed-
    content decisions against injected heartbeat files and a caller-supplied `now` — never the real
    clock; the boundary is inclusive on the healthy side, exactly `cadence_days` old is NOT stale,
    one second past it IS; an exempt hook is never flagged even with no heartbeat file at all;
    output sorted by hook name); `_age_desc` and `format_warning` (`.isascii()`-safe; names the
    stale hook(s); shows `"never recorded"` for `last_seen=None` and a one-decimal day count for a
    real stale timestamp; a whole-number `cadence_days` renders `"7 days"`, not `"7.0 days"`).

    Extended same-PR (`/review` findings on PR #752, recorded in
    [ADR-106's 2026-07-13 addendum](adr/106-hook-heartbeat-liveness-ledger.md)) with
    `format_self_check_failure` (`.isascii()`-safe; names the specific failure reason and the
    parsed hook count; text distinct from `format_warning`'s so the two failure classes are never
    conflated) and a cross-check test, `test_wired_hook_events_agrees_with_hook_wiring_module`, that
    runs `wired_hook_events()` and `tests/_hook_wiring.wired_script_events()` against the **real**
    `claude/settings.json` and asserts agreement (modulo the `.py`-suffix convention) — a regression
    guard against the two deliberately-not-yet-consolidated parsers (see the ADR's Settings-parsing
    scope decision) silently drifting apart. `main()`'s stdin plumbing IS now covered: a behavioral
    layer drives the real hook end-to-end over stdin via subprocess, with HOME/USERPROFILE isolated
    to a temp dir (mirroring `test_stop_journal_stub_checkpoint.py`'s pattern, so
    `_hookutil.HEARTBEAT_DIR` and the once-per-session sentinel never touch the real
    `~/.claude/scratch/`) and `SETTINGS_PATH` overridden via the `HOOK_LIVENESS_SETTINGS_PATH` test
    seam. Seven cases: the healthy no-stale-hooks path is silent (own heartbeat just recorded, exit 0,
    empty stdout/stderr); a stale non-exempt hook (no heartbeat file at all) emits a model-visible
    `additionalContext` warning naming it; an unreadable/malformed settings.json emits the
    self-check-failure advisory rather than a silent exit; a settings.json missing this hook's own
    wiring emits the self-check-failure advisory (the review's core finding — this hook's own
    heartbeat previously stayed fresh even while this exact failure silently no-op'd); and the
    once-per-session debounce silences an identical second call in the same session while a
    different `session_id` still fires; and (dev-env#802) a >30-day-old orphaned heartbeat `.tmp`
    (`<hook>.ts.<pid>.tmp`) planted in the temp `HEARTBEAT_DIR` is swept by the new
    `cleanup_stale_sentinels("", scratch=HEARTBEAT_DIR, ext=".tmp")` call in `main()`, while a fresh
    in-flight `.tmp` and the live `.ts` ledger are spared. The writer side
    (`_hookutil.record_heartbeat`) is covered in
    item 27 above; the structural enforcement that every wired hook (including this one) actually
    calls it correctly is item 68 below.

    ```bash
    py -3 claude/scripts/tests/test_hook_liveness_check.py
    ```

68. **hook-heartbeat-guard structural gate** — required when changing which hooks are wired in
    `claude/settings.json`, or any wired hook's `main()`. Mirrors
    `test_hook_safe_exit_guard.py` / `test_hook_output_contract.py`'s established AST-gate pattern
    ([ADR-106](adr/106-hook-heartbeat-liveness-ledger.md)'s 2026-07-13 addendum;
    dev-env#745, PR8 of #717 `/review`): asserts every currently-wired hook (discovered via
    `tests/_hook_wiring.py`, so the checked set self-updates with `claude/settings.json` — never a
    hardcoded list) calls `_hookutil.record_heartbeat("<own-name>")` as the literal first statement
    of its own `main()` (after any docstring). Unlike its two precedent gates, ships with an
    **empty allowlist from day one** — PR8 made every wired hook compliant in the same change that
    introduced the gate, so there is no pre-existing debt to migrate; a newly wired hook must be
    compliant from its first commit. Pure-fixture self-tests pin the detector
    (`classify_heartbeat`) against: compliant (first statement, and first statement after a
    docstring); no `main()`; an empty `main()` (only `pass`); a docstring-only `main()`; the call
    present but not first; the call naming a *different* hook; a non-literal (variable) name
    argument (documented scope limitation — this is exactly the shape the gate itself caught in
    `hook-liveness-check.py`'s own first draft, which used a module-level `OWN_HOOK_NAME` variable
    instead of the literal, fixed immediately); the unqualified `from _hookutil import
    record_heartbeat` call shape (also out of scope by design); and an unrelated first call. The
    repo-wide gate then asserts zero non-compliant wired hooks against the real
    `claude/settings.json` + `claude/scripts/*.py`.

    ```bash
    py -3 claude/scripts/tests/test_hook_heartbeat_guard.py
    ```

    ```bash
    py -3 claude/scripts/tests/test_hook_liveness_check.py
    ```

69. **journal-onboard-check sentinel test** — required when changing
    `claude/scripts/journal-onboard-check.py`. Drives the real hook end-to-end over stdin via
    subprocess, with HOME/USERPROFILE pointed at a temp dir (mirroring items 48/50/58's pattern):
    pins that a new session writes `scratch/journal_onboard_<session_id>.flag` via the now-shared
    `SENTINEL_PREFIX` + `_hookutil.sentinel_path()`/`cleanup_stale_sentinels()` convention — replacing
    a hand-rolled path that was never swept (dev-env#768; this single prefix accounted for 986 files
    at the 2026-07-10 hook-reliability assessment); that a repeat invocation for an already-acked
    session is a no-op; that a >30-day-old flag from an unrelated session is swept while a fresh one
    is kept; and that a missing `session_id` or malformed stdin JSON both fail open (exit 0). The
    `get_repo_name()` git subprocess and journal-home-detection logic (unchanged by this fix) are not
    exercised here.

    ```bash
    py -3 claude/scripts/tests/test_journal_onboard_check.py
    ```

70. **session-mode-prompt sentinel test** — required when changing
    `claude/scripts/session-mode-prompt.py`. Drives the real hook end-to-end over stdin via
    subprocess, with HOME/USERPROFILE pointed at a temp dir: pins that a new session writes
    `scratch/session_mode_ack_<session_id>.txt` and emits the `additionalContext` reminder; that a
    second prompt in an already-acked session passes through silently (no output); that a
    >30-day-old marker from an unrelated session is swept via
    `_hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX, ext=".txt")` — the generalized `ext` parameter
    this dev-env#768 fix added (see item 27) — while a fresh marker is kept; and that an automated
    (XML-tagged) prompt is still suppressed after adding the cleanup call. `MARKER_DIR`/`LOG_PATH`
    were changed from a hardcoded absolute-path string to a `Path.home()`-derived one in the same fix
    (identical real-world value, but now honors HOME/USERPROFILE) — the hardcoded string had silently
    defeated this test's own HOME-redirection isolation on first write, caught while authoring this
    test file and cleaned up before landing (no stray files left on the real machine).

    ```bash
    py -3 claude/scripts/tests/test_session_mode_prompt.py
    ```

71. **sweep-scratch-debris test** — required when changing `claude/scripts/sweep-scratch-debris.py`.
    Exercises `find_stale()`/`sweep()` offline against real `tempfile.TemporaryDirectory()` fixtures
    (no real `~/.claude/scratch`): pins that `KNOWN_PATTERNS` is a non-empty list of well-formed
    `(prefix, ext)` pairs; a structural safety net asserting none of the module docstring's
    documented "deliberately excluded" filenames (`awake.lock`, a per-hook `hook-heartbeat/*.ts`,
    `token-sessions.jsonl`, a `baseline_<repo>_<branch>.json`) match any `KNOWN_PATTERNS` glob, so a
    future edit can't accidentally widen a prefix to catch one of them; `find_stale()`'s
    prefix/ext/age filtering; `sweep(apply=False)` reporting counts/bytes without deleting;
    `sweep(apply=True)` deleting only stale matches across multiple families independently in one
    pass; and no crash when the scratch directory is absent. This is the one-time/on-demand utility
    used to force-clear the existing backlog (dev-env#768 — 513 files / 1,303 bytes removed across
    `journal_onboard_*`/`session_mode_ack_*`/`disk_space_check_*` at merge time; the 11 other
    already-self-cleaning families reported zero backlog, confirming they were already compliant)
    rather than waiting for each hook's own next natural invocation to self-clean.

    ```bash
    py -3 claude/scripts/tests/test_sweep_scratch_debris.py
    ```

72. **baseline-tests gc test** — required when changing `claude/scripts/baseline-tests.sh`.
    Drives the real script end-to-end against throwaway git fixtures (a bare "origin" + a working
    clone, mirroring item 34's `merge-stale-pr.sh` pattern) via both sourced-function calls
    (`branch_name_raw`, `branch_exists_locally`, `branch_exists_remotely`, `branch_is_gone`,
    `read_baseline_meta_batch`, `cmd_gc` — the file is now sourcing-safe via a `BASH_SOURCE` guard
    at its own tail instead of an unconditional `main "$@"`) and real subprocess invocations
    (`baseline-tests gc`, `baseline-tests snapshot`). Pins the branch-existence-based cleanup this
    test file exists for (dev-env#778, a follow-up from dev-env#768/PR#777, which deliberately
    excluded this snapshot family from its own age-based sweep — see `sweep-scratch-debris.py`'s
    module docstring, item 71 above): a baseline is kept when its branch still exists locally, kept
    when it exists only on `origin` (pushed, then deleted locally — the remote check queries the
    real remote via `git ls-remote`, not a stale local remote-tracking ref), removed only when
    confirmed gone in both places, and — the conservative-on-uncertainty guarantee this design
    centers on — kept whenever the remote check itself fails (an unreachable `origin`), since
    `branch_exists_remotely` returns a three-way result (exists / confirmed-absent / check-failed)
    and only confirmed-absent counts as gone. Also pins that `gc` scans only the CURRENT repo's own
    `baseline_<repo>_*.json` files (a different repo's file sitting in the same scratch dir is
    never even touched, by construction of the glob) and never guesses on malformed JSON, a
    well-formed envelope missing the `branch` field, or a file whose NAME matches the current
    repo's glob but whose own envelope content names a different repo (a hand-edited/corrupted
    file) — all three are kept, matching this codebase's convention elsewhere for irreversible-ish
    cleanup (e.g. item 71's `sweep()` never counting a failed unlink as removed). `cmd_snapshot` is
    proven to auto-invoke `gc` on its way out (a fake `test_command` emits minimal valid Jest
    `--json` output so no real jest/npx install is needed) — sweeping a stale, unrelated
    gone-branch baseline in the same run that writes the new one, without touching the baseline
    snapshot just wrote for the branch actually checked out. Every fixture repo explicitly
    overrides `core.hooksPath` to an empty directory, since this machine sets it globally and a
    throwaway fixture's `commit`/`push` would otherwise invoke dev-env's own real git hooks (e.g.
    the pre-push lockfile-drift guard).

    **`/review` on the PR that introduced this item found two ship-blocking correctness bugs and a
    reliability hang risk, all fixed before merge (see [ADR-030](adr/030-baseline-test-failure-policy.md)'s
    2026-07-16 amendment for full detail) — this test file's coverage grew specifically to pin
    each one:**
    - `cmd_snapshot` originally stored the *sanitized* branch name (`branch_name_sanitized()`,
      slashes replaced with dashes for the filename) in the JSON envelope's `branch` field, but
      `cmd_gc` compares that field against *real* git refs — which never contain the sanitized
      form. Since this repo's own branch-naming convention (`feat/`, `fix/`, `config/`, `chore/`,
      `draft/`) always contains a slash, `gc` would delete the just-written baseline on nearly
      every real invocation, silently (`cmd_gc || true`). Every other fixture branch name in this
      file is deliberately slash-free, so nothing else exercises this. Fixed by adding
      `branch_name_raw()` and storing *that* (not the sanitized form) in the envelope; the
      sanitized form is now used only for the filename. `test_kept_branch_with_slash_in_name` (a
      direct `cmd_gc` call) and `test_snapshot_with_slashed_branch_name_survives_its_own_gc` (the
      full `git checkout -b feat/...` → `snapshot` pipeline) both pin the fix — the second is the
      exact end-to-end shape the review caught and is the more important of the two.
    - `branch_exists_remotely` originally passed a bare branch name straight to `git ls-remote
      --heads origin`, which git matches as a `*/<name>` suffix — a sibling branch merely ending in
      `/<name>` (e.g. `topic/foo` when checking `foo`) falsely reported "present". Fixed by
      extracting just the ref-name column from the output and requiring an exact match against
      `refs/heads/<branch>`. `test_branch_exists_remotely_suffix_sibling_not_false_positive` pins
      the original bug's shape; `test_branch_exists_remotely_prefix_sibling_not_false_positive`
      (a sibling sharing only a string *prefix*, e.g. `foo-bar` vs `foo`) pins that the fix's own
      exact-match approach doesn't introduce the mirror-image false positive a naive
      substring-based fix would have.
    - The `git ls-remote` call had no timeout and did nothing to suppress interactive credential
      prompts — since it now runs on `cmd_snapshot`'s hot path (every `new-branch` in an opted-in
      repo), an unreachable `origin` or a stale credential cache could hang branch creation
      indefinitely (this machine's own git credential helper GUI is documented elsewhere in this
      repo to hang non-interactively). Fixed with `GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never
      timeout 10`. Not separately unit-tested (a real hang is not practical to construct in a
      test); `test_kept_when_remote_check_itself_fails` continues to cover the resulting "check
      failed → keep" behavior once a failure occurs, by whatever means.
    - Two Windows-specific fixes surfaced *while writing* the new tests, not by `/review`: (1) the
      performance fix batching `read_baseline_meta`'s per-file `node -e` spawn into one
      `read_baseline_meta_batch` call initially piped bash-side POSIX-style paths (e.g.
      `/tmp/tmp.XXXX/...` from a test's own `mktemp -d`) to `node` over stdin — unlike an argv or
      environment variable, Git Bash's MSYS2 layer does not auto-translate a POSIX path handed to a
      native Windows binary over a pipe, so `node.exe` silently misread every path (including
      well-formed files) as a nonexistent literal path and reported them all as unparseable. Fixed
      by normalizing every path through `cygpath -m` (a single extra subprocess call for the whole
      batch, not one per file) before the pipe. (2) `read_baseline_meta_batch` echoes back
      whatever path form it was fed *after* that normalization, so
      `test_read_baseline_meta_batch_ok_and_skip_together`'s expected value has to be built from
      `cygpath`'s own output, not the original bash-constructed path string, or the test fails
      against otherwise-correct behavior.

    ```bash
    bash claude/scripts/tests/test-baseline-tests-gc.sh
    ```

73. **`_repo_target` shared-module test** — required when changing `claude/scripts/_repo_target.py`
    or the six hooks that delegate to it (`post-pr-merge-project.py`, `pr-merge-reminder.py`,
    `posttooluse-inert-advisory.py`, `post-pr-merge-pull.py`, `stop-tile-enumeration-gate.py`,
    `post-tool-use.py`).
    Exercises the pure resolver offline (no I/O — the module is pure): `repo_from_flag` (the
    `--repo`/`-R` flag in both `=` and space forms — the `=` form three of the five copies
    silently missed, dev-env#482 — strict GitHub slug, case preservation, the `(?<!\S)`
    standalone-token lookbehind rejecting a mid-word `-R`, the internal `mask_quoted_spans` that
    blinds a `--subject` "-R other/repo" decoy, and a non-slug value -> None); `merge_args` /
    `create_args` (the quote-aware statement-bounding, incl. the dev-env#667 chained create+merge
    cross-contamination case in both statement orders — each resolves its OWN invocation's flag —
    and the dev-env#660 quoted-`&&`/bare-`&` no-early-truncation boundary while a genuine chained
    `&&` still bounds it); `repo_from_pr_url` / `pr_number_from_pr_url` / `iter_pr_urls` and
    `issue_number_from_issue_url` / `iter_issue_urls` (scheme-agnostic, strict slug,
    multiple-in-order, and the deliberate **no-internal-masking** contract — the caller decides
    masking, so a URL inside a quoted `--subject` is still matched here); and `positional_number`
    (the bare token, the internal `mask_quoted_spans` decoy safety of dev-env#650, and that a digit
    inside a URL / a `--flag=12` value / a hyphenated branch name is not a standalone token). The
    six consumers each delegate to this module and keep their own suites (items 12/14/15/18/28/48)
    green, with per-bug regression cases added for #482 (equals form + chained-flag scoping), #569
    (`load_config` scoped via `effective_merge_dir`), #667 (chained create+merge), and #685
    (tile-gate URL fallbacks masked with `mask_prose_flag_values`). See
    [ADR-111](adr/111-shared-repo-target-resolution.md) — the module ends the per-site ADR-050
    amendment treadmill (Amendments 14/15/17/18/19/20/21) for the repo-flag/URL/number concern.
    Also (dev-env#831) pins that `merge_args`/`create_args` keep a `--repo` / PR-number on a
    continued line of a multi-line invocation — `_invocation_args` now strips backslash-newline
    continuations (via `_hookio.strip_line_continuations`) before the `[^\n…]` region regex — while
    a real top-level `&&` after a continuation still bounds the region. Also (dev-env#838, ADR-111
    Amendment 1) pins the sixth-consumer additions: `repo_from_flag` normalizing a full-URL /
    `github.com/`-host-prefixed / `www.` `--repo` value to the bare `owner/repo` (the optional
    non-capturing host prefix that folds in `post-tool-use.py`'s former `_REPO_HOST_PREFIX_RE` and
    fixes the same latent `github.com/owner` mis-capture in the other five), and `issue_create_args`
    — the `gh issue create` counterpart of `create_args` — with basic, chained-sibling-not-leaked,
    and continuation cases.

    ```bash
    py -3 claude/scripts/tests/test_repo_target.py
    ```

74. **session-mode-report test** — required when changing `claude/scripts/session-mode-report.py`.
    Exercises the pure `parse_log()` / `build_rows()` / `filter_rows()` helpers offline against a
    synthetic JSONL fixture (a real tempfile, no live `~/.claude/scratch` log): pins that a
    multi-prompt session's startup mode resolves to its **earliest** `ts` entry rather than
    whichever line appears first in the file — the fixture deliberately writes the later
    (`bypassPermissions`) prompt before the earlier (`plan`) one, so this also proves the
    lexicographic-`ts` comparison, not file order, drives the selection; that a malformed
    (non-JSON) line and a line with no `session_id` are both counted in `stats` rather than
    raising; `build_rows()`'s classification (an `automated_suppressed`/`<scheduled-task>` entry is
    `"automated"` and never flagged even though it started in `bypassPermissions`) and flagging (an
    interactive session starting in anything other than `plan` IS flagged; one starting in `plan`
    is not); and `filter_rows()`'s three flags (`--non-plan-only`, `--interactive-only`,
    `--since`) against the same fixture. A behavioral layer drives the real `main()` against
    fixture files: a clean fixture exits 0; a prompt containing an em-dash, smart quotes, and an
    emoji also exits 0 without a `UnicodeEncodeError` (pins `_force_utf8_streams()`'s stdout/stderr
    reconfiguration — a *redirected* Windows stdout falls back to the narrow cp1252 locale encoding
    rather than the console's native UTF-16 path, and this report echoes `prompt_prefix` verbatim);
    and a missing log path returns non-zero with the error on stderr. Read-only, stdlib-only report
    utility — no `_winsubp` import, no live git/gh call to mock. Not a wired hook (no
    `settings.json` entry): it is the on-demand report a maintainer runs against
    `session-mode-prompt.py`'s append-only log (item 70 above) to audit which sessions started off
    the `defaultMode: "plan"` setting — see ADR-027 background, named in the script's own
    docstring.

    ```bash
    py -3 claude/scripts/tests/test_session_mode_report.py
    ```

75. **Unreachable-code lint (pylint `unreachable` / W0101)** — required before any PR touching
    `claude/scripts/*.py` or `claude/scripts/tests/*.py`. `run-pylint-unreachable.sh` runs pylint
    with `--disable=all --enable=unreachable`, a single-check invocation covering ONLY pylint's
    dead-code-after-`return`/`raise`/`continue`/`break` detector (a pure control-flow check,
    independent of type annotations) — catching the dev-env#813 class of bug (a trailing `return` in
    `_resolve_worktree_scope()` that could never execute, only noticed by hand during an unrelated
    refactor) that nothing else in this suite caught. mypy's `--warn-unreachable` was evaluated and
    rejected: it skips the bodies of untyped functions by default, and there is no clean way to run
    "just unreachable" without either bringing the whole tree to mypy-clean or grep-filtering a wave
    of unrelated type errors. Ruff has no unreachable-code rule at all
    ([astral-sh/ruff#970](https://github.com/astral-sh/ruff/issues/970) still lists pylint's W0101 as
    unimplemented). SKIPs (exit 0) with an install hint when pylint is absent, matching
    `run-shellcheck.sh`'s convention — but unlike shellcheck, CI installs `pylint==4.0.6` explicitly
    (see `.github/workflows/hook-tests.yml`), so it is NOT self-skipped there. Auto-discovered by
    `run-hook-tests.py`, so it runs as part of the whole-suite command too. Originally scoped to
    `claude/scripts/*.py` only (dev-env#815 / PR #818), with widening to
    `claude/scripts/tests/*.py` explicitly deferred as a follow-up in ADR-112's "Considered
    alternatives"; dev-env#821 does that widening — same tool, same single check, wider glob
    (68 → 134 files scanned), zero new findings surfaced. See
    [ADR-112](adr/112-unreachable-code-lint-check.md).

    ```bash
    bash claude/scripts/tests/run-pylint-unreachable.sh
    ```

76. **Testing-index parity gate** — required when changing the `## Testing` index in `CLAUDE.md` or `docs/TESTING.md`; asserts both files carry identical, contiguous item numbers and titles (the ADR-114 two-file sync rule). Pure offline file parse (no subprocess/network/git); auto-discovered by `run-hook-tests.py`, so it gates CI on every PR. Exists because the merge-time numbering-collision gate (ADR-074) reads only the CLAUDE.md index against `origin/main` — it never opens `docs/TESTING.md` — so cross-file drift (an item added to one file but not the other, or one number titling different tests) had no mechanical guard. Added from `/review` findings on PR #855.

    ```bash
    py -3 claude/scripts/tests/test_testing_index_parity.py
    ```

77. **experiment-verdict-gate test** — required when changing `claude/scripts/stop-experiment-verdict-gate.py` (ADR-115). Pins the Stop hook that nudges when a session states a process-experiment conclusion with no `/experiment-audit` run. Verdict-language detection: each of the four operative idioms fires ("the spike failed", "experiment was a success", "adopt the challenger", "challenger outperformed ..."), while a bare unit-test "the test failed", meta-discussion that keeps words between the experiment noun and the outcome word, and — the load-bearing rigor-docs guarantee — verdict wording confined to a `Write`/`Edit` `tool_use` input (a session authoring an ADR/report) all do NOT fire. Also pins the suppressors (the `[experiment-audit]` marker in assistant text, a `/experiment-audit` command wrapper, and the "skip experiment audit" user override — a `tool_result`/compact-summary mention of the phrase does not waive), the `evaluate()` composition, the cp1252-encodable reminder, and the end-to-end behavior (fire → exit 2 on stderr with empty stdout; audit-ran / skip / no-verdict / docs-editing / `stop_hook_active` → exit 0; the once-per-session sentinel suppresses a second fire; empty/non-dict stdin → exit 0 fail-open). Structural compliance (heartbeat, safe-exit fail-open, output-contract channel, settings wiring) rides the shared PR3/PR8 gates (items 61/62/63/68), which auto-discover the hook once it is wired in `claude/settings.json`.

    ```bash
    py -3 claude/scripts/tests/test_stop_experiment_verdict_gate.py
    ```
