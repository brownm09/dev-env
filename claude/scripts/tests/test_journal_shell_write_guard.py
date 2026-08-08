#!/usr/bin/env python3
"""Unit + integration tests for pre-tool-use-journal-shell-write-guard.py.

Two layers, both hermetic (Layer 2 spawns the real hook as a subprocess but
touches no real files or git repos -- this hook does no filesystem/git work
at all, unlike its two PreToolUse siblings):

  1. Pure-function tests of `journal_path_kind()` / `find_bash_redirect_targets()`
     / `find_powershell_write_targets()` / `find_journal_shell_writes()` /
     `_has_override()` -- no subprocess.

     The single most important case in this layer is
     `test_find_bash_redirect_targets_heredoc_declaration_line`: it pins the
     hook's load-bearing `_mask_first_line_quotes` fix (see that function's
     and the module's own docstrings). Manually verified while writing this
     hook: swapping `_mask_first_line_quotes`'s body for a direct
     `mask_quoted_spans(first_line)` call (no `<<`-neutralization) makes this
     one test fail -- `_find_heredoc_end` then treats the rest of the
     first-line-truncated string as an unterminated heredoc declaration and
     masks the redirect target right along with it, so the target is lost.

  2. End-to-end main() via subprocess -- drives the real hook over stdin,
     asserting exit codes and (since this hook uses `_hookout.emit_block`,
     which writes plain ASCII-sanitized text, not JSON) plain-text `in
     proc.stderr` checks -- matching the current convention confirmed
     against `test_skill_file_size_guard.py`, not the older
     `json.loads(proc.stderr)["reason"]` shape some earlier sibling hooks
     use. Covers: a Bash redirect blocked for each of the four path kinds;
     the heredoc-declaration-line shape end-to-end; PowerShell `Out-File`
     and `New-Item -Value` blocked; `git add`/`commit -- <path>`/`push`,
     `rm`/`Remove-Item` (the documented shard-deletion mechanism),
     plain reads, and directory-only scaffolding all allowed; the override
     token bypasses; a payload with no `cwd` at all still works (this hook,
     unlike its two siblings, needs no cwd/git resolution); malformed input
     fails open.

Usage:
    py -3 claude/scripts/tests/test_journal_shell_write_guard.py

Exit 0 = all pass.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "pre-tool-use-journal-shell-write-guard.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("journal_shell_write_guard", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jswg = _load_module()

# --------------------------------------------------------------------------
# Layer 1: pure-function tests
# --------------------------------------------------------------------------


def test_journal_path_kind_stub() -> str:
    if jswg.journal_path_kind("sessions/dev-env/2026-08-07_120000.stub.md") != "stub":
        raise AssertionError("a *.stub.md path must classify as 'stub'")
    return "*.stub.md classifies as 'stub'"


def test_journal_path_kind_manifest() -> str:
    if jswg.journal_path_kind("sessions/dev-env/2026-08-07_120000.manifest.jsonl") != "manifest":
        raise AssertionError("a *.manifest.jsonl path must classify as 'manifest'")
    return "*.manifest.jsonl classifies as 'manifest'"


def test_journal_path_kind_open_pr() -> str:
    if jswg.journal_path_kind("sessions/dev-env/open-prs/54.json") != "open-pr":
        raise AssertionError("an open-prs/<digits>.json path must classify as 'open-pr'")
    return "open-prs/<digits>.json classifies as 'open-pr'"


def test_journal_path_kind_tile() -> str:
    if jswg.journal_path_kind("sessions/dev-env/tiles/961.json") != "tile":
        raise AssertionError("a tiles/<digits>.json path must classify as 'tile'")
    return "tiles/<digits>.json classifies as 'tile'"


def test_journal_path_kind_none_for_unrelated() -> str:
    for p in ("README.md", "sessions/dev-env/reports/x.md", "foo.json", "package.json"):
        if jswg.journal_path_kind(p) is not None:
            raise AssertionError(f"{p!r} must not classify as any journal-file kind")
    return "unrelated paths (README.md, reports/x.md, foo.json) classify as None"


def test_journal_path_kind_requires_separator() -> str:
    if jswg.journal_path_kind("mytiles/54.json") is not None:
        raise AssertionError("'mytiles/54.json' must not match -- 'tiles' needs its own path component")
    return "'mytiles/54.json' (no real separator before 'tiles') does not match"


def test_journal_path_kind_open_pr_tile_require_numeric_stem() -> str:
    if jswg.journal_path_kind("open-prs/foo.json") is not None:
        raise AssertionError("open-prs/foo.json (non-numeric stem) must not match")
    if jswg.journal_path_kind("tiles/bar.json") is not None:
        raise AssertionError("tiles/bar.json (non-numeric stem) must not match")
    return "open-prs/ and tiles/ require a numeric filename stem"


def test_find_bash_redirect_targets_basic() -> str:
    got = jswg.find_bash_redirect_targets("echo '{...}' > sessions/dev-env/open-prs/54.json")
    if got != [(">", "sessions/dev-env/open-prs/54.json")]:
        raise AssertionError(f"expected one > redirect to the open-pr path, got {got}")
    return "echo '{...}' > <path> -> one (>, target) pair"


def test_find_bash_redirect_targets_append_operator() -> str:
    got = jswg.find_bash_redirect_targets("echo hi >> sessions/dev-env/open-prs/54.json")
    if got != [(">>", "sessions/dev-env/open-prs/54.json")]:
        raise AssertionError(f"expected a distinct >> operator, got {got}")
    return ">> is detected distinctly from >"


def test_find_bash_redirect_targets_quoted_target_unquoted_on_read() -> str:
    got = jswg.find_bash_redirect_targets('echo hi > "sessions/dev-env/open-prs/54.json"')
    if got != [(">", "sessions/dev-env/open-prs/54.json")]:
        raise AssertionError(f"expected the quoted target read WITHOUT its quotes, got {got}")
    return "a quoted redirect target is read with its quotes stripped"


def test_find_bash_redirect_targets_ignores_quoted_gt_in_prose() -> str:
    got = jswg.find_bash_redirect_targets('echo "score: 5 > 3" > sessions/dev-env/open-prs/54.json')
    if got != [(">", "sessions/dev-env/open-prs/54.json")]:
        raise AssertionError(f"a '>' inside quoted prose must not be treated as an operator, got {got}")
    return "a '>' inside a quoted string is ignored; only the real trailing redirect is found"


def test_find_bash_redirect_targets_heredoc_declaration_line() -> str:
    """THE critical regression case -- see module docstring."""
    got = jswg.find_bash_redirect_targets("cat <<'EOF' > sessions/dev-env/2026-08-07_120000.stub.md")
    if got != [(">", "sessions/dev-env/2026-08-07_120000.stub.md")]:
        raise AssertionError(
            f"the classic `cat <<'EOF' > file` shape must still find its redirect target, got {got} "
            "-- if this fails, _mask_first_line_quotes's <<-neutralization regressed"
        )
    return "cat <<'EOF' > <target> (heredoc opener + same-line redirect) -> target still found"


def test_find_bash_redirect_targets_no_operator() -> str:
    got = jswg.find_bash_redirect_targets("git add sessions/dev-env/open-prs/54.json")
    if got:
        raise AssertionError(f"a command with no redirect operator must yield [], got {got}")
    return "no '>' anywhere -> []"


def test_find_powershell_write_targets_out_file() -> str:
    got = jswg.find_powershell_write_targets("'{...}' | Out-File sessions/dev-env/open-prs/54.json")
    if not any(t[1] == "sessions/dev-env/open-prs/54.json" and t[0] == "Out-File" for t in got):
        raise AssertionError(f"expected Out-File to be detected with its target, got {got}")
    return "Out-File <path> -> detected with its target"


def test_find_powershell_write_targets_set_content() -> str:
    got = jswg.find_powershell_write_targets("Set-Content -Path sessions/dev-env/tiles/54.json -Value '{...}'")
    if not any(t[0] == "Set-Content" and t[1] == "sessions/dev-env/tiles/54.json" for t in got):
        raise AssertionError(f"expected Set-Content -Path <target> detected, got {got}")
    return "Set-Content -Path <path> -Value ... -> detected with its -Path target"


def test_find_powershell_write_targets_add_content() -> str:
    got = jswg.find_powershell_write_targets("Add-Content sessions/dev-env/open-prs/54.json -Value '{...}'")
    if not any(t[0] == "Add-Content" and t[1] == "sessions/dev-env/open-prs/54.json" for t in got):
        raise AssertionError(f"expected Add-Content detected, got {got}")
    return "Add-Content <path> -Value ... -> detected"


def test_find_powershell_write_targets_tee_object() -> str:
    got = jswg.find_powershell_write_targets("Tee-Object -FilePath sessions/dev-env/tiles/54.json")
    if not any(t[0] == "Tee-Object" and t[1] == "sessions/dev-env/tiles/54.json" for t in got):
        raise AssertionError(f"expected Tee-Object detected, got {got}")
    return "Tee-Object -FilePath <path> -> detected"


def test_find_powershell_write_targets_value_argument_not_matched_as_target() -> str:
    got = jswg.find_powershell_write_targets(
        "Set-Content -Path sessions/dev-env/tiles/54.json -Value 'see tiles/99.json for context'"
    )
    targets = [t[1] for t in got]
    if "sessions/dev-env/tiles/54.json" not in targets:
        raise AssertionError(f"the real -Path target must still be found, got {got}")
    if any("99" in t for t in targets):
        raise AssertionError(f"a path-shaped word INSIDE -Value's own argument must not be a target, got {got}")
    return "-Value's own argument (even if path-shaped) is never itself treated as a write target"


def test_find_powershell_write_targets_new_item_requires_value() -> str:
    got_bare = jswg.find_powershell_write_targets("New-Item -ItemType Directory sessions/dev-env/tiles")
    if got_bare:
        raise AssertionError(f"bare New-Item with no -Value must not match, got {got_bare}")
    got_value = jswg.find_powershell_write_targets("New-Item sessions/dev-env/tiles/54.json -Value '{...}'")
    if not any(t[1] == "sessions/dev-env/tiles/54.json" for t in got_value):
        raise AssertionError(f"New-Item WITH -Value must match, got {got_value}")
    return "New-Item only counts as a write when -Value is also present"


def test_find_powershell_write_targets_cmdlet_name_in_quoted_string_not_triggered() -> str:
    got = jswg.find_powershell_write_targets("git commit -m 'mentions Out-File sessions/dev-env/tiles/54.json'")
    if got:
        raise AssertionError(f"a cmdlet name only inside a quoted string must not be a genuine invocation, got {got}")
    return "a cmdlet name appearing only inside a quoted argument is not treated as a real invocation"


def test_find_journal_shell_writes_combines_across_segments() -> str:
    cmd = "mkdir -p foo && echo hi > bar.txt && echo '{...}' > sessions/dev-env/open-prs/54.json"
    got = jswg.find_journal_shell_writes(cmd)
    if len(got) != 1 or got[0]["target"] != "sessions/dev-env/open-prs/54.json":
        raise AssertionError(f"expected exactly one match among several segments, got {got}")
    return "a multi-segment command with one real match among several unrelated segments -> exactly one match"


def test_find_journal_shell_writes_pipe_isolates_tee_object() -> str:
    cmd = "Get-Content x.txt | Tee-Object -FilePath sessions/dev-env/tiles/54.json"
    got = jswg.find_journal_shell_writes(cmd)
    if len(got) != 1 or got[0]["operator_or_cmdlet"] != "Tee-Object":
        raise AssertionError(f"pipe-isolated Tee-Object must be detected, got {got}")
    return "Get-Content x | Tee-Object -FilePath <path> -> detected (split_pipe isolates Tee-Object onto its own segment)"


def test_find_journal_shell_writes_heredoc_body_mention_not_triggered() -> str:
    cmd = 'git commit -m "$(cat <<\'EOF\'\nfixed sessions/dev-env/open-prs/54.json\nEOF\n)"'
    got = jswg.find_journal_shell_writes(cmd)
    if got:
        raise AssertionError(f"a heredoc body merely mentioning a filename as prose must not trigger, got {got}")
    return "a heredoc BODY mentioning a journal filename as prose -> not mistaken for a real write"


def test_find_journal_shell_writes_git_add_commit_push_not_triggered() -> str:
    cmds = [
        "git add sessions/dev-env/open-prs/54.json",
        'git commit -m "draft: 2026-08-07 session 1" -- sessions/dev-env/2026-08-07_120000.stub.md',
        "git push -u origin draft/2026-08-07",
    ]
    for cmd in cmds:
        got = jswg.find_journal_shell_writes(cmd)
        if got:
            raise AssertionError(f"{cmd!r} must not trigger (no redirect/cmdlet), got {got}")
    return "git add / commit -m ... -- <path> / push referencing journal paths -> never triggered"


def test_find_journal_shell_writes_rm_remove_item_not_triggered() -> str:
    cmds = [
        'rm -f "sessions/dev-env/open-prs/54.json"',
        "Remove-Item sessions/dev-env/tiles/54.json",
    ]
    for cmd in cmds:
        got = jswg.find_journal_shell_writes(cmd)
        if got:
            raise AssertionError(f"{cmd!r} (documented deletion mechanism) must not trigger, got {got}")
    return "rm -f / Remove-Item deleting a shard (the documented deletion mechanism) -> never triggered"


def test_find_journal_shell_writes_plain_reads_not_triggered() -> str:
    cmds = [
        "cat sessions/dev-env/2026-08-07_120000.stub.md",
        "ls sessions/dev-env/open-prs/",
        "Get-Content sessions/dev-env/tiles/54.json",
    ]
    for cmd in cmds:
        got = jswg.find_journal_shell_writes(cmd)
        if got:
            raise AssertionError(f"a plain read ({cmd!r}) must not trigger, got {got}")
    return "cat / ls / Get-Content (plain reads) -> never triggered"


def test_find_journal_shell_writes_mkdir_new_item_directory_not_triggered() -> str:
    cmds = [
        'mkdir -p "C:/Users/brown/Git/engineering-journal/sessions/dev-env/tiles"',
        "New-Item -ItemType Directory -Force sessions/dev-env/tiles",
    ]
    for cmd in cmds:
        got = jswg.find_journal_shell_writes(cmd)
        if got:
            raise AssertionError(f"directory scaffolding ({cmd!r}) must not trigger, got {got}")
    return "mkdir -p / New-Item -ItemType Directory (no -Value) -> never triggered (writes no content)"


def test_has_override() -> str:
    if not jswg._has_override("ALLOW_JOURNAL_SHELL_WRITE=1 echo '{...}' > sessions/dev-env/open-prs/54.json"):
        raise AssertionError("leading override token must be recognized")
    if jswg._has_override('git commit -m "ALLOW_JOURNAL_SHELL_WRITE=1 was mentioned here"'):
        raise AssertionError("override token merely mentioned inside a quoted string must NOT bypass")
    return "leading override token recognized; a quoted mention does not bypass"


# --------------------------------------------------------------------------
# Layer 2: end-to-end subprocess tests
# --------------------------------------------------------------------------


def _run_hook(payload) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=json.dumps(payload) if not isinstance(payload, str) else payload,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_main_blocks_bash_redirect_to_each_kind() -> str:
    targets = {
        "stub": "sessions/dev-env/2026-08-07_120000.stub.md",
        "manifest": "sessions/dev-env/2026-08-07_120000.manifest.jsonl",
        "open-pr": "sessions/dev-env/open-prs/54.json",
        "tile": "sessions/dev-env/tiles/961.json",
    }
    for kind, target in targets.items():
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"echo '{{...}}' > {target}"},
            "cwd": "C:/Users/brown/Git/engineering-journal",
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(f"{kind}: expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}")
        if proc.stdout != "":
            raise AssertionError(f"{kind}: nothing should reach stdout on a block, got {proc.stdout!r}")
        if "journal-shell-write-guard" not in proc.stderr or "ALLOW_JOURNAL_SHELL_WRITE=1" not in proc.stderr:
            raise AssertionError(f"{kind}: block reason missing expected markers: {proc.stderr!r}")
        if target not in proc.stderr:
            raise AssertionError(f"{kind}: block reason should name the target: {proc.stderr!r}")
    return "a Bash redirect to each of the four journal-path kinds is blocked (exit 2), empty stdout, reason on stderr"


def test_main_blocks_bash_heredoc_declaration_line_to_stub() -> str:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": "cat <<'EOF' > sessions/dev-env/2026-08-07_120000.stub.md\n## Session\ncontent\nEOF"
        },
        "cwd": "C:/Users/brown/Git/engineering-journal",
    }
    proc = _run_hook(payload)
    if proc.returncode != 2:
        raise AssertionError(f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}")
    if "2026-08-07_120000.stub.md" not in proc.stderr:
        raise AssertionError(f"block reason should name the stub target: {proc.stderr!r}")
    return "cat <<'EOF' > <stub-path> ... EOF blocked end-to-end (the exact reported failure shape)"


def test_main_blocks_powershell_out_file() -> str:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "PowerShell",
        "tool_input": {"command": "'{...}' | Out-File sessions/dev-env/open-prs/54.json"},
        "cwd": "C:/Users/brown/Git/engineering-journal",
    }
    proc = _run_hook(payload)
    if proc.returncode != 2:
        raise AssertionError(f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}")
    return "PowerShell Out-File to an open-PR shard path blocked (exit 2)"


def test_main_blocks_powershell_new_item_value() -> str:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "PowerShell",
        "tool_input": {"command": "New-Item sessions/dev-env/tiles/961.json -Value '{...}'"},
        "cwd": "C:/Users/brown/Git/engineering-journal",
    }
    proc = _run_hook(payload)
    if proc.returncode != 2:
        raise AssertionError(f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}")
    return "PowerShell New-Item -Value to a tile shard path blocked (exit 2)"


def test_main_allows_git_add_commit_push() -> str:
    for cmd in (
        "git add sessions/dev-env/2026-08-07_120000.stub.md",
        'git commit -m "draft: 2026-08-07 session 1" -- sessions/dev-env/2026-08-07_120000.stub.md',
        "git push -u origin draft/2026-08-07",
    ):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "cwd": "C:/Users/brown/Git/engineering-journal",
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(f"{cmd!r} must be allowed, got exit {proc.returncode}. stderr={proc.stderr!r}")
    return "git add / commit -- <path> / push referencing journal paths -> allowed (exit 0)"


def test_main_allows_rm_and_remove_item() -> str:
    for tool_name, cmd in (
        ("Bash", 'rm -f "sessions/dev-env/open-prs/54.json"'),
        ("PowerShell", "Remove-Item sessions/dev-env/tiles/961.json"),
    ):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"command": cmd},
            "cwd": "C:/Users/brown/Git/engineering-journal",
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(f"{cmd!r} (documented deletion) must be allowed, got exit {proc.returncode}")
    return "rm -f / Remove-Item deleting a shard -> allowed (exit 0) -- the documented deletion mechanism"


def test_main_allows_plain_reads() -> str:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cat sessions/dev-env/2026-08-07_120000.stub.md"},
        "cwd": "C:/Users/brown/Git/engineering-journal",
    }
    proc = _run_hook(payload)
    if proc.returncode != 0:
        raise AssertionError(f"a plain read must be allowed, got exit {proc.returncode}. stderr={proc.stderr!r}")
    return "cat <stub-path> (plain read) -> allowed (exit 0)"


def test_main_allows_mkdir_and_new_item_directory_scaffolding() -> str:
    for tool_name, cmd in (
        ("Bash", 'mkdir -p "C:/Users/brown/Git/engineering-journal/sessions/dev-env/tiles"'),
        ("PowerShell", "New-Item -ItemType Directory -Force sessions/dev-env/tiles"),
    ):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"command": cmd},
            "cwd": "C:/Users/brown/Git/engineering-journal",
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(f"{cmd!r} (directory scaffolding) must be allowed, got exit {proc.returncode}")
    return "mkdir -p / New-Item -ItemType Directory (no -Value) scaffolding -> allowed (exit 0)"


def test_main_override_bypasses_block() -> str:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": "ALLOW_JOURNAL_SHELL_WRITE=1 echo '{...}' > sessions/dev-env/open-prs/54.json"
        },
        "cwd": "C:/Users/brown/Git/engineering-journal",
    }
    proc = _run_hook(payload)
    if proc.returncode != 0:
        raise AssertionError(f"override must bypass the block, got exit {proc.returncode}. stderr={proc.stderr!r}")
    return "ALLOW_JOURNAL_SHELL_WRITE=1 prefix bypasses the block (exit 0)"


def test_main_no_cwd_in_payload_still_works() -> str:
    # Unlike its two PreToolUse siblings, this hook needs no cwd/git resolution
    # at all -- pure text detection -- so a payload omitting cwd entirely must
    # still correctly block.
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo '{...}' > sessions/dev-env/open-prs/54.json"},
    }
    proc = _run_hook(payload)
    if proc.returncode != 2:
        raise AssertionError(f"a payload with no cwd at all must still block, got exit {proc.returncode}")
    return "a payload omitting cwd entirely still blocks correctly (this hook needs no cwd)"


def test_main_fails_open_on_malformed_input() -> str:
    cases = [
        ("", "empty stdin"),
        ("not json", "malformed JSON"),
        (json.dumps({"tool_name": "Bash"}), "missing tool_input"),
        (json.dumps({"tool_name": "Write", "tool_input": {"command": "echo hi > sessions/x/open-prs/1.json"}}), "non-Bash/PowerShell tool_name"),
        (json.dumps([]), "valid JSON but not an object"),
    ]
    for raw, desc in cases:
        proc = _run_hook(raw)
        if proc.returncode != 0:
            raise AssertionError(f"{desc} must fail open (exit 0), got {proc.returncode}. stderr={proc.stderr!r}")
    return "empty/malformed JSON, missing tool_input, non-Bash/PowerShell tool_name, non-object JSON all fail open"


def main() -> int:
    tests = [
        ("journal_path_kind: stub", test_journal_path_kind_stub),
        ("journal_path_kind: manifest", test_journal_path_kind_manifest),
        ("journal_path_kind: open-pr", test_journal_path_kind_open_pr),
        ("journal_path_kind: tile", test_journal_path_kind_tile),
        ("journal_path_kind: unrelated paths -> None", test_journal_path_kind_none_for_unrelated),
        ("journal_path_kind: requires real path separator", test_journal_path_kind_requires_separator),
        ("journal_path_kind: open-pr/tile require numeric stem", test_journal_path_kind_open_pr_tile_require_numeric_stem),
        ("find_bash_redirect_targets: basic", test_find_bash_redirect_targets_basic),
        ("find_bash_redirect_targets: >> vs >", test_find_bash_redirect_targets_append_operator),
        ("find_bash_redirect_targets: quoted target unquoted on read", test_find_bash_redirect_targets_quoted_target_unquoted_on_read),
        ("find_bash_redirect_targets: ignores quoted > in prose", test_find_bash_redirect_targets_ignores_quoted_gt_in_prose),
        ("find_bash_redirect_targets: heredoc declaration line (CRITICAL)", test_find_bash_redirect_targets_heredoc_declaration_line),
        ("find_bash_redirect_targets: no operator -> []", test_find_bash_redirect_targets_no_operator),
        ("find_powershell_write_targets: Out-File", test_find_powershell_write_targets_out_file),
        ("find_powershell_write_targets: Set-Content", test_find_powershell_write_targets_set_content),
        ("find_powershell_write_targets: Add-Content", test_find_powershell_write_targets_add_content),
        ("find_powershell_write_targets: Tee-Object", test_find_powershell_write_targets_tee_object),
        ("find_powershell_write_targets: -Value argument not a target", test_find_powershell_write_targets_value_argument_not_matched_as_target),
        ("find_powershell_write_targets: New-Item requires -Value", test_find_powershell_write_targets_new_item_requires_value),
        ("find_powershell_write_targets: cmdlet name in quoted string not triggered", test_find_powershell_write_targets_cmdlet_name_in_quoted_string_not_triggered),
        ("find_journal_shell_writes: combines across segments", test_find_journal_shell_writes_combines_across_segments),
        ("find_journal_shell_writes: pipe isolates Tee-Object", test_find_journal_shell_writes_pipe_isolates_tee_object),
        ("find_journal_shell_writes: heredoc body mention not triggered", test_find_journal_shell_writes_heredoc_body_mention_not_triggered),
        ("find_journal_shell_writes: git add/commit/push not triggered", test_find_journal_shell_writes_git_add_commit_push_not_triggered),
        ("find_journal_shell_writes: rm/Remove-Item not triggered", test_find_journal_shell_writes_rm_remove_item_not_triggered),
        ("find_journal_shell_writes: plain reads not triggered", test_find_journal_shell_writes_plain_reads_not_triggered),
        ("find_journal_shell_writes: mkdir/New-Item directory not triggered", test_find_journal_shell_writes_mkdir_new_item_directory_not_triggered),
        ("_has_override: leading vs quoted mention", test_has_override),
        ("main(): blocks Bash redirect to each of the four kinds", test_main_blocks_bash_redirect_to_each_kind),
        ("main(): blocks heredoc-declaration-line shape to stub", test_main_blocks_bash_heredoc_declaration_line_to_stub),
        ("main(): blocks PowerShell Out-File", test_main_blocks_powershell_out_file),
        ("main(): blocks PowerShell New-Item -Value", test_main_blocks_powershell_new_item_value),
        ("main(): allows git add/commit/push", test_main_allows_git_add_commit_push),
        ("main(): allows rm/Remove-Item deletion", test_main_allows_rm_and_remove_item),
        ("main(): allows plain reads", test_main_allows_plain_reads),
        ("main(): allows mkdir/New-Item directory scaffolding", test_main_allows_mkdir_and_new_item_directory_scaffolding),
        ("main(): override bypasses block", test_main_override_bypasses_block),
        ("main(): no cwd in payload still works", test_main_no_cwd_in_payload_still_works),
        ("main(): fails open on malformed input", test_main_fails_open_on_malformed_input),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
