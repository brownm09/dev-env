#!/usr/bin/env python3
"""Unit + integration tests for pre-tool-use-journal-shell-write-guard.py.

Two layers, both hermetic (Layer 2 spawns the real hook as a subprocess but
touches no real files or git repos -- this hook does no filesystem/git work
at all, unlike its two PreToolUse siblings):

  1. Pure-function tests of `journal_path_kind()` / `find_bash_redirect_targets()`
     / `find_tee_targets()` / `find_serializer_journal_mentions()` /
     `find_powershell_write_targets()` / `find_journal_shell_writes()` /
     `_is_overridden()` / `_might_write_journal_content()` -- no subprocess.

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


def test_might_write_journal_content_true_for_every_marker() -> str:
    """No false negatives: every command shape the real detectors can
    match must pass the pre-filter too, or main() would skip real work
    without ever reaching the accurate check."""
    cases = [
        "echo hi > sessions/dev-env/x.stub.md",
        "echo hi | tee sessions/dev-env/x.stub.md",
        "echo hi | Tee-Object sessions/dev-env/x.stub.md",
        "node -e \"fs.writeFileSync('sessions/dev-env/tiles/1.json')\"",
        "py -3 -c \"open('sessions/dev-env/tiles/1.json')\"",
        "Out-File sessions/dev-env/open-prs/1.json",
        "Set-Content -Path sessions/dev-env/tiles/1.json -Value x",
        "Add-Content sessions/dev-env/open-prs/1.json -Value x",
        "New-Item sessions/dev-env/tiles/1.json -Value x",
    ]
    for cmd in cases:
        if not jswg._might_write_journal_content(cmd):
            raise AssertionError(f"{cmd!r} must pass the pre-filter (a real detector can match it)")
    return "every real-detector-matchable command shape passes the pre-filter (no false negatives)"


def test_might_write_journal_content_false_for_ordinary_commands() -> str:
    """The overwhelmingly common case (no relevant construct at all) is
    correctly filtered out -- this is what the pre-filter exists to skip."""
    cases = [
        "git status",
        "git add sessions/dev-env/x.stub.md",
        "ls -la",
        "gh pr view 123 --json title,body",
    ]
    for cmd in cases:
        if jswg._might_write_journal_content(cmd):
            raise AssertionError(f"{cmd!r} (no relevant construct) should not pass the pre-filter")
    return "ordinary commands with no relevant construct correctly fail the pre-filter"


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


def test_find_bash_redirect_targets_escaped_apostrophe_in_prose() -> str:
    """THE single most important regression case in this suite -- dev-env#962
    review finding A1. An earlier version of the masking fix used a blind,
    context-free substitution that correctly caught this case but silently
    regressed the sibling case in the next test; this one alone would not
    have caught that regression, which is why both are pinned."""
    got = jswg.find_bash_redirect_targets("echo Claude\\'s > sessions/dev-env/2026-08-07_120000.stub.md")
    if got != [(">", "sessions/dev-env/2026-08-07_120000.stub.md")]:
        raise AssertionError(
            f"a backslash-escaped apostrophe in UNQUOTED prose (the standard Bash workaround for "
            f"embedding a literal apostrophe) must not defeat detection, got {got}"
        )
    return "echo Claude\\'s > <target> (escaped apostrophe in unquoted prose) -> target still found"


def test_find_bash_redirect_targets_canonical_apostrophe_idiom() -> str:
    """The standard Bash idiom for embedding an apostrophe INSIDE a
    single-quoted string: close the quote, backslash-escape a literal
    apostrophe, reopen the quote."""
    got = jswg.find_bash_redirect_targets("echo 'it'\\''s fine' > sessions/dev-env/x.stub.md")
    if got != [(">", "sessions/dev-env/x.stub.md")]:
        raise AssertionError(f"the 'it'\\''s fine' apostrophe-embedding idiom must not defeat detection, got {got}")
    return "echo 'it'\\''s fine' > <target> (canonical apostrophe-embedding idiom) -> target still found"


def test_find_bash_redirect_targets_trailing_backslash_before_close_quote_not_regressed() -> str:
    """The regression the blind-substitution version of this fix introduced
    (and this test catches): a single-quoted string that legitimately ENDS
    in a literal backslash character, immediately followed by its real
    closing quote (e.g. a Windows path). `_hookio`'s single-quote state has
    zero escape-awareness (correctly matching real Bash: a bare `'` always
    closes the span, backslash or not) -- a naive `\\'`-anywhere
    neutralization breaks this by preventing the span from ever closing."""
    got = jswg.find_bash_redirect_targets("echo 'C:\\dir\\' > sessions/dev-env/x.stub.md")
    if got != [(">", "sessions/dev-env/x.stub.md")]:
        raise AssertionError(
            f"a single-quoted string ending in a literal backslash before its real closing quote "
            f"must still correctly close the span and find the redirect target, got {got}"
        )
    return "echo 'C:\\dir\\' > <target> (trailing backslash before a real closing quote) -> not regressed"


def test_find_powershell_write_targets_sub_expression_read_not_a_target() -> str:
    """dev-env#962 review finding B1: a -Value sub-expression that READS a
    journal path (not writes to it) must not be misdetected as the write
    target -- only the -Path-bound value counts."""
    got = jswg.find_powershell_write_targets(
        "Set-Content -Path C:/scratch/backup.json -Value (Get-Content sessions/dev-env/tiles/961.json -Raw)"
    )
    if any("tiles" in t[1] for t in got):
        raise AssertionError(f"a -Value sub-expression merely READING a journal path must not be the target, got {got}")
    return "Set-Content -Path backup.json -Value (Get-Content <journal-path> -Raw) -> the read is not the target"


def test_find_powershell_write_targets_quoted_log_message_not_a_target() -> str:
    """dev-env#962 review finding A2: a positional (no -Path flag) log
    message that merely MENTIONS a journal path must not be misdetected as
    the write target -- the real target is the first positional argument."""
    got = jswg.find_powershell_write_targets('Set-Content log.txt "wrote sessions/dev-env/tiles/54.json"')
    if any("tiles" in t[1] for t in got):
        raise AssertionError(f"a quoted log message merely mentioning a journal path must not be the target, got {got}")
    return 'Set-Content log.txt "wrote <journal-path>" -> the mention is not the target'


def test_find_powershell_write_targets_out_file() -> str:
    # This function expects an already segment-isolated line -- pipe-splitting
    # happens one layer up, in find_journal_shell_writes() via
    # split_top_level(..., split_pipe=True) -- so the input here is the
    # post-split segment, matching test_find_journal_shell_writes_pipe_isolates_tee_object's
    # end-to-end coverage of the actual pre-pipe-portion + split combination.
    got = jswg.find_powershell_write_targets("Out-File sessions/dev-env/open-prs/54.json")
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


def test_find_powershell_write_targets_backslash_windows_path() -> str:
    """dev-env#962 review finding B3: an idiomatic Windows backslash path
    (as PowerShell tab-completion routinely produces) must not be
    destroyed by POSIX backslash-escape tokenization. All four path
    regexes explicitly accept `[/\\\\]`, so backslash support is intended."""
    got = jswg.find_powershell_write_targets(r"Set-Content -Path sessions\dev-env\tiles\961.json -Value x")
    if not any(t[1] == r"sessions\dev-env\tiles\961.json" for t in got):
        raise AssertionError(f"a backslash Windows path must survive tokenization intact, got {got}")
    return r"Set-Content -Path sessions\dev-env\tiles\961.json -> backslashes preserved, target detected"


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
    got = jswg.find_journal_shell_writes(cmd, "Bash")
    if len(got) != 1 or got[0]["target"] != "sessions/dev-env/open-prs/54.json":
        raise AssertionError(f"expected exactly one match among several segments, got {got}")
    return "a multi-segment command with one real match among several unrelated segments -> exactly one match"


def test_find_journal_shell_writes_pipe_isolates_tee_object() -> str:
    cmd = "Get-Content x.txt | Tee-Object -FilePath sessions/dev-env/tiles/54.json"
    got = jswg.find_journal_shell_writes(cmd, "PowerShell")
    if len(got) != 1 or got[0]["operator_or_cmdlet"] != "Tee-Object":
        raise AssertionError(f"pipe-isolated Tee-Object must be detected, got {got}")
    return "Get-Content x | Tee-Object -FilePath <path> -> detected (split_pipe isolates Tee-Object onto its own segment)"


def test_find_journal_shell_writes_heredoc_body_mention_not_triggered() -> str:
    cmd = 'git commit -m "$(cat <<\'EOF\'\nfixed sessions/dev-env/open-prs/54.json\nEOF\n)"'
    got = jswg.find_journal_shell_writes(cmd, "Bash")
    if got:
        raise AssertionError(f"a heredoc body merely mentioning a filename as prose must not trigger, got {got}")
    return "a heredoc BODY mentioning a journal filename as prose -> not mistaken for a real write"


def test_find_journal_shell_writes_multiline_quoted_argument_is_a_known_accepted_gap() -> str:
    """dev-env#962 review finding A6, deliberately left unfixed and pinned
    HERE as a known, accepted residual gap (ADR-129's own scope statement
    documents this choice explicitly) rather than silently working or
    silently regressing: a redirect that lands on a later physical line
    only because a PRECEDING QUOTED ARGUMENT on the same segment spans a
    raw embedded newline (`printf` with an embedded `\\n`, as a heredoc
    substitute) is invisible to every detector, all of which only ever
    inspect a segment's own first physical line by design (a heredoc/
    here-string BODY must never be mistaken for invocation syntax, and
    that convention cannot distinguish this narrower case from a real
    heredoc body without much deeper quote-state tracking). This is
    narrower and rarer than the fixed findings above -- it requires
    deliberately embedding a raw newline inside a single shell argument --
    and the override token remains available for the (rarer still)
    genuine case. Asserts one of exactly two ACCEPTABLE outcomes -- either
    the documented gap (no match) or a future improvement finding the
    correct target -- so this still fails loudly on any THIRD outcome
    (a wrong target, a crash, spurious extra matches), rather than
    silently accepting anything at all once a gap is declared open."""
    cmd = "printf '%s\\n' 'line one\nline two' > sessions/dev-env/x.stub.md"
    got = jswg.find_journal_shell_writes(cmd, "Bash")
    if got == []:
        return "multi-line quoted argument hiding a same-segment redirect -- known, accepted gap (ADR-129 scope statement)"
    if len(got) == 1 and got[0]["target"] == "sessions/dev-env/x.stub.md":
        return "multi-line quoted argument now detected -- gap closed, update this test's docstring/name"
    raise AssertionError(f"neither the documented gap ([]) nor a correct match -- a THIRD, unexpected outcome: {got}")


def test_find_journal_shell_writes_git_add_commit_push_not_triggered() -> str:
    cmds = [
        "git add sessions/dev-env/open-prs/54.json",
        'git commit -m "draft: 2026-08-07 session 1" -- sessions/dev-env/2026-08-07_120000.stub.md',
        "git push -u origin draft/2026-08-07",
    ]
    for cmd in cmds:
        got = jswg.find_journal_shell_writes(cmd, "Bash")
        if got:
            raise AssertionError(f"{cmd!r} must not trigger (no redirect/cmdlet), got {got}")
    return "git add / commit -m ... -- <path> / push referencing journal paths -> never triggered"


def test_find_journal_shell_writes_rm_remove_item_not_triggered() -> str:
    cmds = [
        ("Bash", 'rm -f "sessions/dev-env/open-prs/54.json"'),
        ("PowerShell", "Remove-Item sessions/dev-env/tiles/54.json"),
    ]
    for tool_name, cmd in cmds:
        got = jswg.find_journal_shell_writes(cmd, tool_name)
        if got:
            raise AssertionError(f"{cmd!r} (documented deletion mechanism) must not trigger, got {got}")
    return "rm -f / Remove-Item deleting a shard (the documented deletion mechanism) -> never triggered"


def test_find_journal_shell_writes_plain_reads_not_triggered() -> str:
    cmds = [
        ("Bash", "cat sessions/dev-env/2026-08-07_120000.stub.md"),
        ("Bash", "ls sessions/dev-env/open-prs/"),
        ("PowerShell", "Get-Content sessions/dev-env/tiles/54.json"),
    ]
    for tool_name, cmd in cmds:
        got = jswg.find_journal_shell_writes(cmd, tool_name)
        if got:
            raise AssertionError(f"a plain read ({cmd!r}) must not trigger, got {got}")
    return "cat / ls / Get-Content (plain reads) -> never triggered"


def test_find_journal_shell_writes_mkdir_new_item_directory_not_triggered() -> str:
    cmds = [
        ("Bash", 'mkdir -p "C:/Users/brown/Git/engineering-journal/sessions/dev-env/tiles"'),
        ("PowerShell", "New-Item -ItemType Directory -Force sessions/dev-env/tiles"),
    ]
    for tool_name, cmd in cmds:
        got = jswg.find_journal_shell_writes(cmd, tool_name)
        if got:
            raise AssertionError(f"directory scaffolding ({cmd!r}) must not trigger, got {got}")
    return "mkdir -p / New-Item -ItemType Directory (no -Value) -> never triggered (writes no content)"


def test_find_journal_shell_writes_powershell_cmdlet_gated_on_tool_name() -> str:
    """dev-env#962 review finding, two independent guards pinned separately
    so a regression in either alone still fails this test:
    (1) position-anchoring -- a Bash command whose argument merely
    CONTAINS a PowerShell-cmdlet-shaped word (a grep pattern, not at
    segment-start) must never be misdetected as an invocation, under
    EITHER tool_name; (2) tool_name gating -- the identical, genuinely
    cmdlet-first text must trigger only under tool_name=PowerShell, never
    under tool_name=Bash, even though position-anchoring alone would
    otherwise qualify it."""
    not_anchored = "rg Add-Content sessions/dev-env/tiles/961.json"
    for tool_name in ("Bash", "PowerShell"):
        got = jswg.find_journal_shell_writes(not_anchored, tool_name)
        if got:
            raise AssertionError(f"a cmdlet-shaped word not at segment-start must never trigger ({tool_name}), got {got}")

    anchored = "Add-Content sessions/dev-env/tiles/961.json"
    got_bash = jswg.find_journal_shell_writes(anchored, "Bash")
    if got_bash:
        raise AssertionError(f"a genuinely cmdlet-first line must still NOT trigger under tool_name=Bash, got {got_bash}")
    got_ps = jswg.find_journal_shell_writes(anchored, "PowerShell")
    if not got_ps:
        raise AssertionError("the same cmdlet-first line under tool_name=PowerShell should be detected")
    return "cmdlet-shaped word not at segment-start never triggers; a genuine cmdlet-first line triggers only under tool_name=PowerShell"


def test_find_journal_shell_writes_cross_repo_false_positive_not_triggered() -> str:
    """dev-env#962 review finding: *.manifest.jsonl and tiles/<digits>.json
    are established conventions outside this repo too (ML manifests, game
    asset pipelines) -- a target with no sessions/ component, issued from a
    cwd that isn't the engineering-journal checkout, must not block."""
    got = jswg.find_journal_shell_writes(
        "python gen.py > data/train.manifest.jsonl", "Bash", cwd="C:/Users/brown/Git/some-other-repo"
    )
    if got:
        raise AssertionError(f"an unrelated repo's own .manifest.jsonl file must not trigger, got {got}")
    return "an unrelated repo's same-shaped file (no sessions/ component, non-journal cwd) -> never triggered"


def test_find_journal_shell_writes_sessions_component_self_sufficient() -> str:
    """A target that already names its own sessions/<project>/ directory
    is a genuine hazard regardless of cwd -- this is the common case and
    must not regress from the anchoring fix above."""
    got = jswg.find_journal_shell_writes("echo x > sessions/dev-env/tiles/54.json", "Bash")
    if not got:
        raise AssertionError("a target containing its own sessions/ component must still trigger with no cwd at all")
    return "a target with a genuine sessions/ path component triggers even with no cwd supplied"


def test_find_journal_shell_writes_relative_path_needs_journal_cwd() -> str:
    """A bare relative target (no sessions/ component -- the realistic
    shape when cwd is already inside sessions/<project>/) is a hazard only
    when cwd itself resolves under the engineering-journal checkout."""
    cmd = "echo x > tiles/54.json"
    got_journal_cwd = jswg.find_journal_shell_writes(cmd, "Bash", cwd="C:/Users/brown/Git/engineering-journal/sessions/dev-env")
    if not got_journal_cwd:
        raise AssertionError("a relative target from inside the engineering-journal checkout must trigger")
    got_other_cwd = jswg.find_journal_shell_writes(cmd, "Bash", cwd="C:/Users/brown/Git/some-game")
    if got_other_cwd:
        raise AssertionError(f"the identical relative target from an unrelated repo's cwd must not trigger, got {got_other_cwd}")
    got_no_cwd = jswg.find_journal_shell_writes(cmd, "Bash")
    if got_no_cwd:
        raise AssertionError(f"the identical relative target with no cwd evidence at all must not trigger, got {got_no_cwd}")
    return "a relative (no sessions/ component) target triggers only when cwd resolves under engineering-journal"


def test_find_journal_shell_writes_node_e_continuation_line_write() -> str:
    """THE most important remaining regression case: dev-env#904's own
    incident -- the one that motivated this entire hook -- was exactly
    this shape, and it was NOT detected until this fix. The hazardous
    path is on a LATER physical line than the interpreter invocation,
    invisible to every detector that only inspects a segment's first
    line."""
    cmd = (
        "node -e \"\n"
        "const fs = require('fs');\n"
        "fs.writeFileSync('sessions/dev-env/tiles/961.json', JSON.stringify({}));\n"
        "\""
    )
    got = jswg.find_journal_shell_writes(cmd, "Bash")
    if not got or got[0]["mechanism"] != "serializer-invocation":
        raise AssertionError(f"a node -e write with the path on a continuation line must be detected, got {got}")
    return "node -e '...' with the write target on a LATER physical line -> detected (the dev-env#904 shape)"


def test_find_journal_shell_writes_py_c_continuation_line_write() -> str:
    """The retired tile-shard recipe's exact shape -- this repo's own `py
    -3 -c` convention (a separate -3 version flag, not fused as "py3")."""
    cmd = (
        "py -3 -c '\n"
        "import json\n"
        "json.dump({}, open(\"sessions/dev-env/tiles/961.json\", \"w\"))\n"
        "' <<'TILE_PROMPT_EOF'\n"
        "some prompt text\n"
        "TILE_PROMPT_EOF"
    )
    got = jswg.find_journal_shell_writes(cmd, "Bash")
    if not got or got[0]["mechanism"] != "serializer-invocation":
        raise AssertionError(f"a py -3 -c write with the path on a continuation line must be detected, got {got}")
    return "py -3 -c '...' (this repo's own retired tile-shard recipe) -> detected"


def test_find_journal_shell_writes_serializer_not_triggered_without_journal_mention() -> str:
    """A node -e / py -3 -c invocation that never mentions a journal path
    at all -- or mentions one shaped like a journal path but with no
    sessions/ component and no journal cwd (task 14's anchoring still
    applies here too) -- must not trigger."""
    got_unrelated = jswg.find_journal_shell_writes('node -e "console.log(\'hello world\')"', "Bash")
    if got_unrelated:
        raise AssertionError(f"a node -e snippet mentioning nothing journal-shaped must not trigger, got {got_unrelated}")
    got_cross_repo = jswg.find_journal_shell_writes("node -e \"fs.writeFileSync('tiles/1.json', '{}')\"", "Bash")
    if got_cross_repo:
        raise AssertionError(f"a journal-shaped mention with no sessions/ component and no journal cwd must not trigger, got {got_cross_repo}")
    return "a serializer invocation with no journal-shaped mention, or one filtered by sessions/cwd-anchoring, does not trigger"


def test_find_journal_shell_writes_plain_node_script_not_serializer() -> str:
    """A plain `node build.js sessions/.../tiles/1.json` (no -e flag) is
    running a SCRIPT FILE, not passing an inline snippet -- it is not the
    retired serializer recipe shape and must not be misdetected as one
    (its argument is a real command-line word, already covered -- or not
    -- by the ordinary redirect/cmdlet detectors, not this one)."""
    got = jswg.find_journal_shell_writes("node build.js sessions/dev-env/tiles/1.json", "Bash")
    if got:
        raise AssertionError(f"a plain node script invocation (no -e) must not be treated as a serializer invocation, got {got}")
    return "node <script.js> <args> (no -e flag) -> not treated as a serializer invocation"


def test_find_journal_shell_writes_tee_append_flag_still_detected() -> str:
    """`tee -a <path>` (append mode) must be detected the same as bare
    `tee <path>` -- the -a flag must not be mistaken for a path token."""
    got = jswg.find_journal_shell_writes("echo '{}' | tee -a sessions/dev-env/x.stub.md", "Bash")
    if not got or got[0]["mechanism"] != "bash-tee":
        raise AssertionError(f"tee -a <journal-path> must be detected, got {got}")
    return "tee -a <path> (append mode) -> detected, -a flag not mistaken for the target"


def test_find_journal_shell_writes_tee_gated_on_tool_name() -> str:
    """tee detection is Bash-only, symmetric with the PowerShell-cmdlet
    gate -- a segment shaped like a tee invocation under tool_name=
    PowerShell must not trigger via the tee mechanism (PowerShell has its
    own Tee-Object, already covered separately)."""
    got = jswg.find_journal_shell_writes("tee sessions/dev-env/open-prs/54.json", "PowerShell")
    if any(m["mechanism"] == "bash-tee" for m in got):
        raise AssertionError(f"tee detection must not run under tool_name=PowerShell, got {got}")
    return "tee detection is gated on tool_name=Bash, mirroring the PowerShell-cmdlet gate"


def test_segment_has_bash_override() -> str:
    if not jswg._segment_has_bash_override("ALLOW_JOURNAL_SHELL_WRITE=1 echo '{...}' > sessions/dev-env/open-prs/54.json"):
        raise AssertionError("leading override token must be recognized")
    if jswg._segment_has_bash_override('git commit -m "ALLOW_JOURNAL_SHELL_WRITE=1 was mentioned here"'):
        raise AssertionError("override token merely mentioned inside a quoted string must NOT bypass")
    return "leading override token recognized; a quoted mention does not bypass"


def test_segment_is_ps_override_statement() -> str:
    if not jswg._segment_is_ps_override_statement("$env:ALLOW_JOURNAL_SHELL_WRITE=1"):
        raise AssertionError("a standalone $env: assignment must be recognized")
    if not jswg._segment_is_ps_override_statement(" $env:ALLOW_JOURNAL_SHELL_WRITE = '1' "):
        raise AssertionError("whitespace and a quoted '1' value must still be recognized")
    if jswg._segment_is_ps_override_statement("Write-Host \"$env:ALLOW_JOURNAL_SHELL_WRITE=1 was mentioned\""):
        raise AssertionError("a mention inside an unrelated statement must NOT be recognized as the assignment itself")
    return "a standalone $env:ALLOW_JOURNAL_SHELL_WRITE=1 (or ='1') assignment is recognized; a mention elsewhere is not"


def test_is_overridden_bash_scoped_to_matched_segment() -> str:
    """dev-env#962 review finding: an override on one segment must not
    exempt an unrelated hazard in a LATER segment of the same command."""
    segments = [
        "ALLOW_JOURNAL_SHELL_WRITE=1 echo a > ok.txt",
        " echo b > sessions/dev-env/x.stub.md",
    ]
    if not jswg._is_overridden(segments, 0):
        raise AssertionError("the overridden segment itself must report overridden")
    if jswg._is_overridden(segments, 1):
        raise AssertionError("an unrelated LATER segment must NOT be exempted by an earlier segment's Bash override")
    return "a Bash override on one segment does not exempt an unrelated later segment"


def test_is_overridden_powershell_env_assignment_applies_forward() -> str:
    """Unlike Bash's per-command VAR=1 prefix, a PowerShell $env: assignment
    is its own statement that applies to the REST of the script -- so a
    $env: override segment DOES exempt a later segment (unlike the Bash
    per-segment case above), matching real PowerShell semantics. A
    segment AFTER the write attempt must NOT retroactively exempt it."""
    segments = [
        "$env:ALLOW_JOURNAL_SHELL_WRITE=1",
        " Out-File sessions/dev-env/open-prs/54.json",
    ]
    if not jswg._is_overridden(segments, 1):
        raise AssertionError("a $env: override statement must exempt a later segment in the same command")

    segments_reversed = [
        " Out-File sessions/dev-env/open-prs/54.json",
        "$env:ALLOW_JOURNAL_SHELL_WRITE=1",
    ]
    if jswg._is_overridden(segments_reversed, 0):
        raise AssertionError("a $env: override statement AFTER the write attempt must not retroactively exempt it")
    return "a PowerShell $env: override statement exempts a later segment, never an earlier one"


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


def test_main_powershell_env_override_bypasses_block() -> str:
    """dev-env#962 review finding: the documented override token has no
    PowerShell equivalent -- $env:ALLOW_JOURNAL_SHELL_WRITE=1 must now
    work end-to-end for a PowerShell-exclusive mechanism (Out-File)."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "PowerShell",
        "tool_input": {
            "command": "$env:ALLOW_JOURNAL_SHELL_WRITE=1; 'x' | Out-File sessions/dev-env/open-prs/54.json"
        },
        "cwd": "C:/Users/brown/Git/engineering-journal",
    }
    proc = _run_hook(payload)
    if proc.returncode != 0:
        raise AssertionError(f"$env: override must bypass a PowerShell-only block, got exit {proc.returncode}. stderr={proc.stderr!r}")
    return "$env:ALLOW_JOURNAL_SHELL_WRITE=1; <PowerShell write> bypasses the block end-to-end (exit 0)"


def test_main_override_does_not_exempt_unrelated_later_segment() -> str:
    """dev-env#962 review finding: an override on one segment must not
    exempt an unrelated hazard in a later, different segment -- the
    command must still block (exit 2) on the unrelated segment."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": "ALLOW_JOURNAL_SHELL_WRITE=1 echo a > ok.txt && echo b > sessions/dev-env/x.stub.md"
        },
        "cwd": "C:/Users/brown/Git/engineering-journal",
    }
    proc = _run_hook(payload)
    if proc.returncode != 2:
        raise AssertionError(f"an override on an unrelated earlier segment must not bypass a later segment's hazard, got exit {proc.returncode}")
    if "x.stub.md" not in proc.stderr:
        raise AssertionError(f"the block should name the actually-unoverridden target: {proc.stderr!r}")
    return "an override on one segment does not exempt an unrelated hazard in a later segment (still blocks, exit 2)"


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
        ("_might_write_journal_content: true for every detector marker (no false negatives)", test_might_write_journal_content_true_for_every_marker),
        ("_might_write_journal_content: false for ordinary commands", test_might_write_journal_content_false_for_ordinary_commands),
        ("find_bash_redirect_targets: basic", test_find_bash_redirect_targets_basic),
        ("find_bash_redirect_targets: >> vs >", test_find_bash_redirect_targets_append_operator),
        ("find_bash_redirect_targets: quoted target unquoted on read", test_find_bash_redirect_targets_quoted_target_unquoted_on_read),
        ("find_bash_redirect_targets: ignores quoted > in prose", test_find_bash_redirect_targets_ignores_quoted_gt_in_prose),
        ("find_bash_redirect_targets: heredoc declaration line (CRITICAL)", test_find_bash_redirect_targets_heredoc_declaration_line),
        ("find_bash_redirect_targets: no operator -> []", test_find_bash_redirect_targets_no_operator),
        ("find_bash_redirect_targets: escaped apostrophe in prose (CRITICAL)", test_find_bash_redirect_targets_escaped_apostrophe_in_prose),
        ("find_bash_redirect_targets: canonical apostrophe-embedding idiom", test_find_bash_redirect_targets_canonical_apostrophe_idiom),
        ("find_bash_redirect_targets: trailing backslash before close-quote not regressed", test_find_bash_redirect_targets_trailing_backslash_before_close_quote_not_regressed),
        ("find_powershell_write_targets: sub-expression read not a target", test_find_powershell_write_targets_sub_expression_read_not_a_target),
        ("find_powershell_write_targets: quoted log message not a target", test_find_powershell_write_targets_quoted_log_message_not_a_target),
        ("find_powershell_write_targets: Out-File", test_find_powershell_write_targets_out_file),
        ("find_powershell_write_targets: Set-Content", test_find_powershell_write_targets_set_content),
        ("find_powershell_write_targets: Add-Content", test_find_powershell_write_targets_add_content),
        ("find_powershell_write_targets: Tee-Object", test_find_powershell_write_targets_tee_object),
        ("find_powershell_write_targets: backslash Windows path preserved", test_find_powershell_write_targets_backslash_windows_path),
        ("find_powershell_write_targets: -Value argument not a target", test_find_powershell_write_targets_value_argument_not_matched_as_target),
        ("find_powershell_write_targets: New-Item requires -Value", test_find_powershell_write_targets_new_item_requires_value),
        ("find_powershell_write_targets: cmdlet name in quoted string not triggered", test_find_powershell_write_targets_cmdlet_name_in_quoted_string_not_triggered),
        ("find_journal_shell_writes: combines across segments", test_find_journal_shell_writes_combines_across_segments),
        ("find_journal_shell_writes: pipe isolates Tee-Object", test_find_journal_shell_writes_pipe_isolates_tee_object),
        ("find_journal_shell_writes: heredoc body mention not triggered", test_find_journal_shell_writes_heredoc_body_mention_not_triggered),
        ("find_journal_shell_writes: multi-line quoted argument is a known accepted gap (A6)", test_find_journal_shell_writes_multiline_quoted_argument_is_a_known_accepted_gap),
        ("find_journal_shell_writes: git add/commit/push not triggered", test_find_journal_shell_writes_git_add_commit_push_not_triggered),
        ("find_journal_shell_writes: rm/Remove-Item not triggered", test_find_journal_shell_writes_rm_remove_item_not_triggered),
        ("find_journal_shell_writes: plain reads not triggered", test_find_journal_shell_writes_plain_reads_not_triggered),
        ("find_journal_shell_writes: mkdir/New-Item directory not triggered", test_find_journal_shell_writes_mkdir_new_item_directory_not_triggered),
        ("find_journal_shell_writes: PowerShell cmdlet gated on tool_name + anchored to segment-start", test_find_journal_shell_writes_powershell_cmdlet_gated_on_tool_name),
        ("find_journal_shell_writes: cross-repo false positive not triggered", test_find_journal_shell_writes_cross_repo_false_positive_not_triggered),
        ("find_journal_shell_writes: sessions/ component self-sufficient", test_find_journal_shell_writes_sessions_component_self_sufficient),
        ("find_journal_shell_writes: relative path needs journal cwd", test_find_journal_shell_writes_relative_path_needs_journal_cwd),
        ("find_journal_shell_writes: node -e continuation-line write (CRITICAL, dev-env#904 shape)", test_find_journal_shell_writes_node_e_continuation_line_write),
        ("find_journal_shell_writes: py -3 -c continuation-line write", test_find_journal_shell_writes_py_c_continuation_line_write),
        ("find_journal_shell_writes: serializer not triggered without journal mention", test_find_journal_shell_writes_serializer_not_triggered_without_journal_mention),
        ("find_journal_shell_writes: plain node script not a serializer invocation", test_find_journal_shell_writes_plain_node_script_not_serializer),
        ("find_journal_shell_writes: tee -a append flag still detected", test_find_journal_shell_writes_tee_append_flag_still_detected),
        ("find_journal_shell_writes: tee gated on tool_name=Bash", test_find_journal_shell_writes_tee_gated_on_tool_name),
        ("_segment_has_bash_override: leading vs quoted mention", test_segment_has_bash_override),
        ("_segment_is_ps_override_statement: standalone $env: assignment recognized", test_segment_is_ps_override_statement),
        ("_is_overridden: Bash override scoped to matched segment", test_is_overridden_bash_scoped_to_matched_segment),
        ("_is_overridden: PowerShell $env: assignment applies forward only", test_is_overridden_powershell_env_assignment_applies_forward),
        ("main(): blocks Bash redirect to each of the four kinds", test_main_blocks_bash_redirect_to_each_kind),
        ("main(): blocks heredoc-declaration-line shape to stub", test_main_blocks_bash_heredoc_declaration_line_to_stub),
        ("main(): blocks PowerShell Out-File", test_main_blocks_powershell_out_file),
        ("main(): blocks PowerShell New-Item -Value", test_main_blocks_powershell_new_item_value),
        ("main(): allows git add/commit/push", test_main_allows_git_add_commit_push),
        ("main(): allows rm/Remove-Item deletion", test_main_allows_rm_and_remove_item),
        ("main(): allows plain reads", test_main_allows_plain_reads),
        ("main(): allows mkdir/New-Item directory scaffolding", test_main_allows_mkdir_and_new_item_directory_scaffolding),
        ("main(): override bypasses block", test_main_override_bypasses_block),
        ("main(): PowerShell $env: override bypasses block", test_main_powershell_env_override_bypasses_block),
        ("main(): override does not exempt unrelated later segment", test_main_override_does_not_exempt_unrelated_later_segment),
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
