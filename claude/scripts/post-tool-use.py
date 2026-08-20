#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh issue create' or 'gh pr create'
and automatically adds the item to the configured GitHub project.

Detection matches only a top-level CLI invocation (via _hookio.scan_top_level),
not the string appearing inside commit messages, heredocs, grep patterns, or
other quoted arguments (dev-env#499). A --help/-h-only invocation (e.g. `gh
issue create --help`, run per this repo's own CLI Scripting Checklist) is
excluded too (dev-env#636) -- see is_issue_create_help_only / is_pr_create_help_only.

Project opt-in: add .claude/hook-config.json to the project root. That file is
gitignored by dev-env's own convention (.gitignore ignores all of .claude/),
but that is a per-project choice, not a universal one -- e.g. lifting-logbook
deliberately tracks it in git (dev-env#527). Projects without the file at all
are silently skipped.

Cross-repo resolution (dev-env#542): when the invoking session's own cwd has
no resolvable hook-config.json but the gh command names an explicit
`--repo owner/name` for a DIFFERENT, locally-checked-out repo, load_config
looks for that repo as a sibling checkout and uses its config instead --
otherwise a `gh issue create --repo <other-repo>` filed from an unrelated
project's session always silently no-ops, since cwd never matches the
command's actual target. See extract_repo_flag / _sibling_repo_config.

hook-config.json schema:
  {
    "project_number":  "2",
    "project_owner":   "brownm09",
    "project_node_id": "PVT_kwHOAjEKvM4BTuEF",
    "epic_field_id":   "PVTSSF_...",
    "epic_options": {
      "<name>": "<option-id>",
      ...
    },
    "milestones": ["v0.1 — Foundation", ...]
  }

Also fires for the PowerShell tool (dev-env#763): registered under both the
Bash and PowerShell PostToolUse matchers in settings.json, since PowerShell is
an equally sanctioned way to run `gh issue create`/`gh pr create` in this
environment.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",  # or "PowerShell"
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "..."},  # NOT "output" — ADR-049
    "session_id": "...",
    "cwd": "..."
  }

`required_fields` entries of type `single_select` are refreshed at reminder
time via a live `gh api graphql` fetch of that field's current options
(dev-env#527, ADR-076) -- the cached `options` map above is used only as a
fallback when the live fetch fails (network, auth, timeout), and the printed
reminder labels which source it used so staleness is visible instead of
silent, the way it drifted undetected in lifting-logbook#628.

Exit 0  — not a relevant command, no config, or gh command itself failed; silent
Exit 2  — item added (or failed to add); structured reminder emitted via stderr
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

from _gh_project import add_to_project
from _hookio import (
    is_help_only,
    read_command,
    read_command_output,
    read_cwd,
    read_exit_code,
    scan_top_level,
)
import _hookutil
from _repo_target import create_args, issue_create_args, repo_from_flag
from _worktree_canon import canonical_root_from_worktree

CONFIG_FILE = ".claude/hook-config.json"

# Matches the start of a statement token against `gh issue create` or
# `gh pr create` (dev-env#499). The check functions below anchor via
# .match(), and scan_top_level only ever calls them on top-level statements —
# never a substring buried in a heredoc body, a quoted commit message, a grep
# pattern, or a --text field value (the false-positive class fixed here; the
# previous unanchored re.search(r"\bgh\s+issue\s+create\b", command) /
# re.search(r"\bgh\s+pr\s+create\b", command) matched the pattern ANYWHERE in
# the raw command string).
_ISSUE_CREATE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+issue\s+create\b")
_PR_CREATE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+create\b")


def _check_issue_create_stmt(token: str) -> bool:
    return bool(_ISSUE_CREATE_RE.match(token.lstrip()))


def _check_pr_create_stmt(token: str) -> bool:
    return bool(_PR_CREATE_RE.match(token.lstrip()))


def is_issue_create_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh issue create`."""
    return scan_top_level(command, _check_issue_create_stmt)


def is_pr_create_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh pr create`."""
    return scan_top_level(command, _check_pr_create_stmt)


def is_issue_create_help_only(command: str) -> bool:
    """True iff every top-level `gh issue create` segment in *command* is a
    --help/-h invocation (dev-env#636) -- e.g. `gh issue create --help`, run
    per this repo's own CLI Scripting Checklist ("run <command> --help first
    to confirm flag names"), textually matches `is_issue_create_command` but
    creates nothing. Mirrors `_hookio.is_merge_help_only`'s fix for the
    identical `gh pr merge --help` false-positive (dev-env#557); reuses
    `_ISSUE_CREATE_RE` so this stays byte-for-byte consistent with
    `is_issue_create_command`'s own matching."""
    return is_help_only(command, _ISSUE_CREATE_RE)


def is_pr_create_help_only(command: str) -> bool:
    """True iff every top-level `gh pr create` segment in *command* is a
    --help/-h invocation (dev-env#636). See `is_issue_create_help_only`."""
    return is_help_only(command, _PR_CREATE_RE)


def extract_repo_flag(command: str) -> str | None:
    """Return the `--repo`/`-R` value from *command*'s top-level `gh issue
    create` / `gh pr create` statement, normalized to `owner/name`, or None
    if absent (dev-env#542).

    Delegates to the shared `_repo_target` resolver (dev-env#838, ADR-111):
    `issue_create_args` / `create_args` bound the extraction to the create
    invocation's own argument region -- statement-scoped (never a heredoc body,
    quoted string, or $() subshell elsewhere; dev-env#499) and continuation-safe
    (a `--repo` on a backslash-newline-continued line is joined first via
    `strip_line_continuations`; dev-env#831) -- then `repo_from_flag` extracts and
    normalizes the flag from that region: it masks quoted-value decoys
    (`mask_quoted_spans`) so a `--repo` buried in a quoted `--title`/`--body`
    value can't hijack the match, and strips any full-URL / `github.com/` host
    prefix down to `owner/name` (the former private `_REPO_HOST_PREFIX_RE`, folded
    into the shared resolver -- dev-env#544). Issue-create is checked first,
    mirroring main()'s own `item_type = "Issue" if is_issue_create` precedence for
    a command that somehow chains both.

    Conservative by design: the caller (`_sibling_repo_config`) only ever trusts
    an extracted value that a real sibling checkout's own hook-config.json
    independently confirms (and `_read_config` never raises on a malformed derived
    path -- dev-env#544 review), so any misparse here degrades to today's silent
    skip, never a wrong add or a crash.
    """
    for args in (issue_create_args(command), create_args(command)):
        if args is not None:
            repo = repo_from_flag(args)
            if repo:
                return repo
    return None


def _canonical_root_from_common_dir(cwd: str, common: str) -> str | None:
    """Resolve the canonical repo root from a `git rev-parse --git-common-dir`
    result. `common` may be absolute or relative to `cwd`; it is the canonical
    checkout's `.git` dir, so its parent is the root. Returns None when the output
    is empty or does not name a `.git` dir. Pure — no I/O, offline-testable."""
    common = (common or "").strip()
    if not common:
        return None
    common_abs = common if os.path.isabs(common) else os.path.join(cwd, common)
    common_norm = os.path.normpath(common_abs)
    if os.path.basename(common_norm).lower() != ".git":
        return None
    return os.path.dirname(common_norm)


def canonical_root_via_git(cwd: str) -> str | None:
    """Canonical repo root for a *sibling* worktree (e.g. `dev-env-188`, which the
    path regex above cannot derive) via `git rev-parse --git-common-dir`. Returns
    None on any git failure so load_config degrades to a silent skip rather than
    raising. Only the `subprocess.run` call is untested (repo convention,
    cf. add_to_project); the pure resolution is `_canonical_root_from_common_dir`."""
    try:
        # text=True decodes as UTF-8 (not the Windows cp1252 default) via the
        # _winsubp patch imported above — dev-env#503.
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return _canonical_root_from_common_dir(cwd, result.stdout)


def _read_config(path: str) -> dict | None:
    """Load and parse *path* as JSON, or None on any failure to do so --
    missing file, a malformed path (reserved characters, a null byte -- can
    reach here via _sibling_repo_config's best-effort *name* derivation,
    dev-env#544 review), or valid JSON that isn't a dict. `OSError` subsumes
    `FileNotFoundError`; `ValueError` subsumes `json.JSONDecodeError` -- both
    listed for clarity. Never raises."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _sibling_repo_config(own_root: str, repo_flag: str) -> dict | None:
    """Best-effort cross-repo hook-config.json lookup (dev-env#542).

    *own_root* is this session's own canonical checkout root; *repo_flag* is
    an `owner/name` parsed off an explicit `gh issue/pr create --repo` flag
    naming a DIFFERENT repo. Looks for `name` as a sibling checkout under the
    same parent directory this whole fleet already uses -- the convention
    reconcile-project-board.py's `--scan-dir C:/Users/brown/Git` invocation
    and _repo_scan.find_git_repos already rely on.

    Never a directory-name guess alone: only returned when the sibling's own
    hook-config.json self-reports a `repo` field matching *repo_flag*
    exactly (case-insensitive). A same-named-but-unrelated sibling directory,
    a sibling with no config, or no sibling checkout at all all yield None --
    the same silent-skip behavior as before this function existed, never a
    wrong-project add.
    """
    name = repo_flag.rsplit("/", 1)[-1]
    if not name or not own_root:
        return None
    # own_root can be the raw, unnormalized cwd (the `root or cwd` fallback in
    # load_config, when canonical_root_via_git failed) -- a trailing separator
    # there makes os.path.dirname return own_root itself instead of its
    # parent, silently searching one level too deep (dev-env#544 review).
    own_root = os.path.normpath(own_root)
    sibling_root = os.path.join(os.path.dirname(own_root), name)
    if os.path.normpath(sibling_root) == own_root:
        return None
    cfg = _read_config(os.path.join(sibling_root, CONFIG_FILE))
    if cfg is None:
        return None
    if str(cfg.get("repo", "")).strip().lower() != repo_flag.strip().lower():
        return None
    return cfg


def load_config(cwd: str, command: str | None = None) -> dict | None:
    """Load hook-config.json for the project, or None.

    In projects that gitignore the config (dev-env's own convention -- not a
    universal one, see the module docstring), it lives only in the canonical
    checkout: `git worktree add` never checks out a gitignored file, and the
    harness copies it into Claude-managed worktrees only inconsistently
    (dev-env #378). Read the cwd-local copy first; when it is absent in a
    worktree, fall back to the canonical checkout's copy so worktree sessions
    behave like main-checkout sessions. A project that tracks the config in
    git (e.g. lifting-logbook) never hits the fallback: `git worktree add`
    checks out tracked files normally, so the cwd-local read on the first
    line already finds it.

    *command*, when given, enables one more resolution attempt after the cwd
    / worktree branches above all miss: `gh issue create --repo owner/name`
    (or `gh pr create`) may name a DIFFERENT repo than this session's own cwd
    (dev-env#542) -- a common cross-repo filing pattern this function
    otherwise has no way to route correctly, since cwd is the *session's*
    repo, not the command's target. See `_sibling_repo_config` for the
    resolution + verification strategy.
    """
    cfg = _read_config(os.path.join(cwd, CONFIG_FILE))
    if cfg is not None:
        return cfg
    # Claude-managed worktree (`<root>/.claude/worktrees/<name>`): pure, no subprocess.
    root = canonical_root_from_worktree(cwd)
    if root:
        cfg = _read_config(os.path.join(root, CONFIG_FILE))
        if cfg is not None:
            return cfg
    # Sibling worktree (`<root>-<suffix>`): resolve the canonical root via git.
    root = canonical_root_via_git(cwd)
    if root and os.path.normpath(root) != os.path.normpath(cwd):
        cfg = _read_config(os.path.join(root, CONFIG_FILE))
        if cfg is not None:
            return cfg
    # Cross-repo (dev-env#542): only reached when this session's own cwd (and
    # its worktree/sibling-worktree fallbacks) resolved NO config above -- a
    # session whose cwd already has its own hook-config.json keeps that one
    # and never attempts this branch (dev-env#544 review). The gh command
    # names an explicit --repo/-R target different from this session's own
    # repo; look for it as a sibling checkout -- see _sibling_repo_config.
    if command:
        repo_flag = extract_repo_flag(command)
        if repo_flag:
            cfg = _sibling_repo_config(root or cwd, repo_flag)
            if cfg is not None:
                return cfg
    return None


def extract_github_url(output: str, repo: str | None = None) -> str | None:
    """Return the last GitHub URL found in command output, or None.

    If repo is provided (e.g. 'owner/name'), only return a URL that contains
    that repo path — prevents cross-repo false positives when cwd belongs to
    a different project than the one being created.
    """
    pattern = (
        rf"https://github\.com/{re.escape(repo)}/" if repo
        else r"https://github\.com/"
    )
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if re.search(pattern, line):
            # Extract just the URL in case the line has surrounding text
            match = re.search(r"https://github\.com/\S+", line)
            if match:
                return match.group(0).rstrip(".")
    return None


# GraphQL query for a ProjectV2SingleSelectField's current options
# (https://docs.github.com/en/graphql/reference/objects#projectv2singleselectfield).
# `id` is supplied as a `-f` variable by the caller, never string-interpolated
# into the query, so a field_id value cannot inject additional query structure.
_FIELD_OPTIONS_QUERY = """
query($id: ID!) {
  node(id: $id) {
    ... on ProjectV2SingleSelectField {
      options { id name }
    }
  }
}
"""


def _parse_live_options(raw: str) -> dict[str, str] | None:
    """Parse a `gh api graphql` response for `_FIELD_OPTIONS_QUERY` into
    {name: id}. None on any malformed shape -- non-JSON, wrong/missing node
    type, absent keys -- never raises. Pure, no I/O."""
    try:
        options = json.loads(raw)["data"]["node"]["options"]
        return {opt["name"]: opt["id"] for opt in options}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def fetch_live_field_options(field_id: str, *, timeout: int = 10) -> dict[str, str] | None:
    """Live current {name: id} options for a ProjectV2SingleSelectField via
    `gh api graphql` (https://cli.github.com/manual/gh_api), or None on any
    failure: missing field_id, no `gh` binary, timeout, non-zero exit, a
    malformed response, or non-UTF-8 output. Never raises.

    UnicodeDecodeError is caught alongside the process-level failures even
    though `encoding="utf-8"` is passed explicitly: per _winsubp.py's
    contract, the shared CREATE_NO_WINDOW/errors="replace" patch only
    defaults `errors=` onto a text-mode call that supplies no `encoding=` of
    its own -- an explicit `encoding=` (as here) opts out of that safety net,
    so this call is on its own for decode failures. Catching it here (rather
    than letting it propagate into fetch_live_required_field_options' loop,
    which has no per-field try/except) keeps one field's malformed response
    from silently dropping every other field's live data in the same
    reminder -- the caller relies on this function's "never raises" promise
    to isolate failures per-field, not per-reminder (dev-env#527 review).

    Not unit-tested: shells out to `gh`, matching the add_to_project /
    canonical_root_via_git convention (repo avoids subprocess mocks). The
    pure parse is `_parse_live_options`."""
    if not field_id:
        return None
    try:
        result = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={_FIELD_OPTIONS_QUERY}", "-f", f"id={field_id}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, UnicodeDecodeError):
        return None
    if result.returncode != 0:
        return None
    return _parse_live_options(result.stdout)


def _resolve_required_fields(config: dict) -> list[dict]:
    """Return config's required_fields, normalizing the legacy epic_field_id /
    milestones keys into the same shape when required_fields is absent
    (ADR-023's backward-compat rule). Pure, no I/O -- shared by
    format_reminder (rendering) and fetch_live_required_field_options
    (live-fetch target discovery) so the two rules can never diverge."""
    required_fields = list(config.get("required_fields", []))

    # Backward compat: convert old epic_field_id / milestones shape
    if not required_fields:
        if config.get("epic_field_id"):
            opts = config.get("epic_options", {})
            required_fields.append({
                "name": "Epic",
                "field_id": config["epic_field_id"],
                "type": "single_select",
                "options": opts,
            })
        if config.get("milestones"):
            required_fields.append({
                "name": "Milestone",
                "type": "milestone",
                "options_list": config["milestones"],
            })

    return required_fields


def fetch_live_required_field_options(
    required_fields: list[dict],
    *,
    fetch_fn=fetch_live_field_options,
) -> dict[str, dict[str, str] | None]:
    """Attempt a live options fetch for every single_select field that has a
    field_id. Returns {field_id: {name: id}} on a successful fetch or
    {field_id: None} on failure -- one independent attempt per field, so one
    field's failure never affects another's. `fetch_fn` defaults to the real
    live call; injectable so the field-selection logic (which fields get
    attempted, keyed correctly) is unit-testable without a real subprocess.

    Fetches run serially, each bounded by fetch_fn's own timeout (10s for the
    real fetch_live_field_options) -- cost is multiplicative in the number of
    single_select fields, not just bounded by a single field's timeout. Both
    projects onboarded onto this hook today (lifting-logbook's Epic,
    dev-env's own Impact) have exactly one such field, so this is currently
    a non-issue in practice; a future config with several single_select
    fields and a hung `gh` call would block this synchronous PostToolUse
    hook for field_count * timeout seconds before the reminder prints."""
    live: dict[str, dict[str, str] | None] = {}
    for field in required_fields:
        if field.get("type") != "single_select":
            continue
        field_id = field.get("field_id")
        if not field_id:
            continue
        live[field_id] = fetch_fn(field_id)
    return live


def format_reminder(
    item_type: str,
    url: str,
    item_id: str,
    config: dict,
    *,
    live_options: dict[str, dict[str, str] | None] | None = None,
    required_fields: list[dict] | None = None,
) -> str:
    """live_options, when provided, maps field_id -> live {name: id} (or None
    for a field whose live fetch was attempted and failed). A field_id absent
    from live_options (including when live_options itself is None -- no fetch
    was attempted) renders exactly as before this parameter existed: the
    cached config['options'], unlabeled. This keeps every existing call site
    and the default byte-identical to pre-live-fetch output.

    required_fields, when provided, is rendered directly instead of being
    re-derived from config via _resolve_required_fields. main() resolves
    required_fields once (to pick live-fetch targets for
    fetch_live_required_field_options) and threads that same list in here,
    so a reminder's live-fetch pass and its rendering pass are guaranteed to
    agree by construction rather than by calling a pure-but-independent
    function twice (dev-env#527 review). Defaults to None, which re-derives
    from config exactly as this function always did before the parameter
    existed -- every pre-existing call site is unaffected."""
    lines = [
        f"[project-hook] {item_type} added to project.",
        f"  URL:     {url}",
        f"  Item ID: {item_id}",
    ]

    if required_fields is None:
        required_fields = _resolve_required_fields(config)

    for field in required_fields:
        name = field.get("name", "Field")
        field_id = field.get("field_id", "")
        ftype = field.get("type", "text")
        hint = field.get("hint", "")
        hint_str = f" ({hint})" if hint else ""

        lines.append("")
        lines.append(f"  Set {name}{hint_str}:")

        project_node_id = config.get("project_node_id", "<project-node-id>")
        if ftype == "single_select":
            lines += [
                f"    gh project item-edit \\",
                f"      --project-id {project_node_id} \\",
                f"      --id {item_id} \\",
                f"      --field-id {field_id} \\",
                f"      --single-select-option-id <option-id>",
            ]
            cached_opts = field.get("options", {})
            if live_options is not None and field_id in live_options:
                live_result = live_options[field_id]
                if live_result is not None:
                    opts, freshness = live_result, " (live)"
                else:
                    opts, freshness = cached_opts, " (cached — live fetch failed; may be stale)"
            else:
                opts, freshness = cached_opts, ""
            if opts:
                lines.append(f"  {name} options{freshness}:")
                for opt_name, opt_id in opts.items():
                    lines.append(f"      {opt_name}: {opt_id}")
        elif ftype == "text":
            lines += [
                f"    gh project item-edit \\",
                f"      --project-id {project_node_id} \\",
                f"      --id {item_id} \\",
                f"      --field-id {field_id} \\",
                f"      --text \"<{name.lower()}>\"",
            ]
        elif ftype == "milestone":
            opts_list = field.get("options_list", [])
            opts_str = (
                ", ".join(f'"{m}"' for m in opts_list) if opts_list else "<milestone>"
            )
            lines.append(f"    gh issue edit <N> --milestone {opts_str}")

    return "\n".join(lines)


def main() -> None:
    _hookutil.record_heartbeat("post-tool-use")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        # A valid-JSON-but-non-dict top-level payload (a list, string, number,
        # or null) would otherwise crash the very next line (dev-env#1031/
        # #1033, mirroring usage-snapshot.py's dev-env#1028 post-review fix).
        sys.exit(0)

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    # dev-env#1031/#1033: read_command()/read_cwd()/read_exit_code() never
    # raise on a present-but-non-dict tool_input/cwd/tool_response
    # (dev-env#1028's payload shape) -- the pre-fix unguarded chains crashed
    # here, silently caught by the __main__ safe-exit guard below (which
    # loses only this project-board add, an advisory side effect with other
    # backstops -- see ADR-050 Amendment 27 for why pre-merge-findings-gate.py,
    # a blocking merge gate, was fixed first and separately on fail-open
    # severity grounds). default=0 here (not the -1 most sibling hooks use)
    # matches this file's own pre-fix literal -- verified per-file, not
    # copy-pasted, since a wrong default would reintroduce the dev-env#557
    # misattribution bug (see read_exit_code's own docstring).
    #
    # Documented, accepted trade-off (ADR-050 Amendment 28 post-review finding
    # 6): read_exit_code() ALSO coerces a present-but-non-int-coercible
    # exitCode (e.g. null) to `default`, not just a genuinely MISSING one --
    # the pre-fix `.get("exitCode", default)` only substituted the default on
    # a missing key, so a present `exitCode: null` returned the raw `None`
    # unchanged (crashing downstream, not silently treated as 0). Because
    # `default=0` here, that coercion now makes a malformed-but-present
    # exitCode indistinguishable from a genuinely successful (0) one -- the
    # `if exit_code != 0: sys.exit(0)` gate below no longer skips for that
    # narrow case. Accepted rather than special-cased: narrower and less
    # confirmed than dev-env#1028's own top-level shape (no observed
    # incident for this specific sub-field malformation), and the four
    # `-1`-default sibling files are unaffected (both `None` and `-1` equally
    # satisfy `!= 0`). See test_exit_code_coercion_pins_accepted_tradeoff in
    # test_post_tool_use.py for the pinned, executable proof of this exact
    # behavior.
    command = read_command(data)
    output = read_command_output(data)
    exit_code = read_exit_code(data, default=0)
    cwd = read_cwd(data)

    is_issue_create = is_issue_create_command(command)
    is_pr_create = is_pr_create_command(command)

    # A --help/-h invocation can never actually create anything (dev-env#636,
    # mirrors _hookio.is_merge_help_only's dev-env#557 fix) -- downgrade each
    # create-flag independently so a real create chained with an unrelated
    # help-only invocation of the OTHER type is still processed correctly.
    if is_issue_create and is_issue_create_help_only(command):
        is_issue_create = False
    if is_pr_create and is_pr_create_help_only(command):
        is_pr_create = False

    if not (is_issue_create or is_pr_create):
        sys.exit(0)

    # Don't process if the gh command itself failed
    if exit_code != 0:
        sys.exit(0)

    # Load project config — skip silently if not present
    config = load_config(cwd, command)
    if config is None:
        sys.exit(0)

    item_type = "Issue" if is_issue_create else "PR"
    repo = config.get("repo")  # e.g. "owner/repo-name"

    url = extract_github_url(output, repo)
    if not url:
        # A configured repo filter can legitimately miss: the command may have
        # created the item in a *different* repo than this cwd's project. Stay
        # silent only in that case — some GitHub URL is present, just not ours.
        # If a successful create produced no GitHub URL at all, that is the
        # symptom of a real failure (e.g. reading the wrong payload field — the
        # bug behind #377) and must surface rather than be swallowed silently.
        if repo and extract_github_url(output, None):
            sys.exit(0)
        print(
            f"[project-hook] {item_type} created but no GitHub URL found in output.\n"
            f"  Add to project manually:\n"
            f"    gh project item-add {config['project_number']} "
            f"--owner {config['project_owner']} --url <url>",
            file=sys.stderr,
        )
        sys.exit(2)

    item_id, _ = add_to_project(url, config["project_number"], config["project_owner"])

    if item_id:
        required_fields = _resolve_required_fields(config)
        live_options = fetch_live_required_field_options(required_fields)
        print(
            format_reminder(
                item_type, url, item_id, config,
                live_options=live_options, required_fields=required_fields,
            ),
            file=sys.stderr,
        )
    else:
        print(
            f"[project-hook] {item_type} created but auto-add to project failed.\n"
            f"  URL: {url}\n"
            f"  Add manually:\n"
            f"    gh project item-add {config['project_number']} "
            f"--owner {config['project_owner']} --url {url}",
            file=sys.stderr,
        )

    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
