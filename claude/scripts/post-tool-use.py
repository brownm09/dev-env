#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh issue create' or 'gh pr create'
and automatically adds the item to the configured GitHub project.

Detection matches only a top-level CLI invocation (via _hookio.scan_top_level),
not the string appearing inside commit messages, heredocs, grep patterns, or
other quoted arguments (dev-env#499).

Project opt-in: add .claude/hook-config.json to the project root. That file is
gitignored by dev-env's own convention (.gitignore ignores all of .claude/),
but that is a per-project choice, not a universal one -- e.g. lifting-logbook
deliberately tracks it in git (dev-env#527). Projects without the file at all
are silently skipped.

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

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
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
from _hookio import read_command_output, scan_top_level
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
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_config(cwd: str) -> dict | None:
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
        return _read_config(os.path.join(root, CONFIG_FILE))
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
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    output = read_command_output(data)
    exit_code = data.get("tool_response", {}).get("exitCode", 0)
    cwd = data.get("cwd", "")

    is_issue_create = is_issue_create_command(command)
    is_pr_create = is_pr_create_command(command)

    if not (is_issue_create or is_pr_create):
        sys.exit(0)

    # Don't process if the gh command itself failed
    if exit_code != 0:
        sys.exit(0)

    # Load project config — skip silently if not present
    config = load_config(cwd)
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
