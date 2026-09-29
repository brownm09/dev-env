#!/usr/bin/env python3
"""Unit tests for dev-env-doctor.py, the read-only install health check (dev-env#1114).

Exercises the doctor's pure decision helpers (`*_result` / `*_results`, `parse_ls_remote`,
`guarded`) against fixtures only -- temp files, fake resolvers, injected `which`/`is_file`
callables -- so nothing here reads this machine's real ~/.claude, git config, clones or
network. One case builds a real dangling junction (a symlink off Windows) in a temp directory,
because a dangling link passing is a property of the real realpath(), which a fake resolver
can't show. `collect()`, `_run()` and `_kill_tree()` (the thin I/O layer around the helpers)
are deliberately untested, matching the repo's no-subprocess-mock convention
(test_disk_space_check.py).

Several checks guard against the doctor itself going vacuous (ADR-144): the link lists are
pinned against setup.sh's real arrays -- asserting the parse extracted something before
comparing -- and the hook-command and compile checks must FAIL, not PASS, on zero inputs.

Usage:
    py -3 claude/scripts/tests/test_dev_env_doctor.py

Exit 0 = all pass.
"""

import importlib.util
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "dev-env-doctor.py"
SETUP_SH = REPO_ROOT / "setup.sh"

# The script imports _winsubp and _repo_scan (siblings in scripts/); make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename -- import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("dev_env_doctor", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
doc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doc)  # safe: main() is guarded by __main__

PASS, WARN, FAIL, INFO = doc.PASS, doc.WARN, doc.FAIL, doc.INFO


def _statuses(results):
    return [r.status for r in results]


def _settings(*commands):
    """A settings.json-shaped dict wiring each command under UserPromptSubmit."""
    return {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": c} for c in commands]}]}}


def _always(value):
    return lambda _arg: value


def _dir_link(target: Path, link: Path) -> None:
    """A directory link needing no privilege: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        import _winapi  # CPython's own test suite builds junctions this way
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


# --------------------------------------------------------------------------- setup.sh parity


def _setup_array(text, name):
    m = re.search(rf"^{name}=\(([^)]*)\)", text, re.M)
    assert m, f"could not find {name}=( ... ) in setup.sh"
    items = m.group(1).split()
    # Non-empty before comparing: an empty parse compared against an empty tuple would pass
    # vacuously (ADR-144).
    assert items, f"{name} parsed empty"
    return items


def test_link_lists_match_setup_sh():
    text = SETUP_SH.read_text(encoding="utf-8")
    claude = (
        _setup_array(text, "CLAUDE_FILE_LINKS")
        + _setup_array(text, "CLAUDE_DIR_LINKS")
        + _setup_array(text, "CLAUDE_JUNCTION_LINKS")
    )
    home = _setup_array(text, "HOME_LINKS")
    assert doc.LINKED_ITEMS == tuple(claude), f"doctor checks {doc.LINKED_ITEMS}, setup.sh links {claude}"
    assert doc.HOME_LINKS == tuple(home), f"doctor checks ~/{doc.HOME_LINKS}, setup.sh links ~/{home}"
    return f"LINKED_ITEMS == {tuple(claude)}, HOME_LINKS == {tuple(home)}"


# --------------------------------------------------------------------------- parsing helpers


def test_script_path_from_command():
    f = doc.script_path_from_command
    assert f("pyw -3 C:/Users/me/.claude/scripts/a-b.py") == "C:/Users/me/.claude/scripts/a-b.py"
    assert f('pyw -3 "C:/Users/John Smith/.claude/scripts/x.py"  ') == "C:/Users/John Smith/.claude/scripts/x.py"
    assert f("bash C:/Users/me/.claude/hooks/pre-push") is None
    assert f("") is None
    return "plain, quoted-with-space, non-.py and empty commands"


def test_home_prefix():
    assert doc.home_prefix("C:/Users/brown/.claude/scripts/a.py") == "C:/Users/brown"
    assert doc.home_prefix("C:\\Users\\other\\.claude\\scripts\\a.py") == "C:/Users/other"
    assert doc.home_prefix("/opt/scripts/a.py") is None
    return "forward-slash, backslash, and no-.claude paths"


def test_same_path():
    assert doc.same_path("a/b/../c", "a/c")
    if os.name == "nt":
        assert doc.same_path("C:/Users/Me/x", "c:\\users\\me\\X"), "Windows paths compare case-insensitively"
    else:
        assert not doc.same_path("/a/B", "/a/b"), "POSIX paths compare case-sensitively"
    return f"normalizes '..' and separators; case rule for os.name={os.name}"


def test_parse_ls_remote():
    sha = "a" * 40
    ref = "refs/heads/draft/2026-09-29"
    assert doc.parse_ls_remote(f"{sha}\t{ref}\n", ref) == sha
    assert doc.parse_ls_remote("", ref) == ""
    assert doc.parse_ls_remote(f"{sha}\trefs/heads/draft/2026-09-28\n", ref) == "", "another ref is not ours"
    # stderr is kept apart now, but stray text on stdout must still never pose as a SHA.
    assert doc.parse_ls_remote("warning: redirecting to https://github.com/x/y.git/\n", ref) == ""
    assert doc.parse_ls_remote(f"nothex{'0' * 34}\t{ref}\n", ref) == ""
    return "the matching 40-hex line only; empty, other refs and warning text -> no branch"


# --------------------------------------------------------------------------- hook commands


def test_hook_commands_all_present():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp, "a.py"), Path(tmp, "b.py")
        a.write_text("", encoding="utf-8")
        b.write_text("", encoding="utf-8")
        settings = _settings(f"pyw -3 {a.as_posix()}", f"pyw -3 {b.as_posix()}", f"pyw -3 {a.as_posix()}")
        results = doc.hook_command_results(settings, Path("s.json"), Path(tmp), which=_always("found"))
    assert _statuses(results) == [PASS], results
    assert "3 commands checked, 2 distinct scripts" in results[0].detail, results[0].detail
    return results[0].detail


def test_hook_commands_missing_script_fails():
    with tempfile.TemporaryDirectory() as tmp:
        present = Path(tmp, "a.py")
        present.write_text("", encoding="utf-8")
        gone = Path(tmp, "gone.py")
        settings = _settings(f"pyw -3 {present.as_posix()}", f"pyw -3 {gone.as_posix()}")
        results = doc.hook_command_results(settings, Path("s.json"), Path(tmp), which=_always("found"))
    assert _statuses(results) == [FAIL], results
    assert "1 of 2" in results[0].detail and "gone.py" in results[0].detail, results[0].detail
    return results[0].detail


def test_hook_commands_zero_entries_fails_not_vacuous():
    results = doc.hook_command_results({"hooks": {}}, Path("s.json"), Path.home(), which=_always("found"))
    assert _statuses(results) == [FAIL] and "vacuously" in results[0].detail, results
    results = doc.hook_command_results({}, Path("s.json"), Path.home(), which=_always("found"))
    assert _statuses(results) == [FAIL], results
    return "empty hooks block and missing hooks key both FAIL"


def test_hook_commands_none_parsed_fails_not_vacuous():
    # Entries exist, but no script path can be read from any: the check read nothing, so it
    # must not report "all present" -- and the launcher is still checked.
    settings = _settings("pyw -3 C:/x/.claude/scripts/gone-a.py --mode x", "pyw -3 C:/x/.claude/scripts/gone-b.py > log")
    results = doc.hook_command_results(
        settings, Path("s.json"), Path("C:/x"), is_file=_always(False), which=_always(None),
    )
    assert _statuses(results) == [FAIL, FAIL], results
    assert "checked nothing" in results[0].detail, results[0].detail
    assert results[1].check == "hook launcher" and "'pyw'" in results[1].detail, results[1]
    return results[0].detail


def test_hook_commands_unreadable_settings_fails():
    results = doc.hook_command_results(None, Path("missing.json"), Path.home())
    assert _statuses(results) == [FAIL] and "missing.json" in results[0].detail, results
    return results[0].detail


def test_hook_commands_other_home_hint():
    settings = _settings("pyw -3 C:/Users/someone-else/.claude/scripts/a.py")
    results = doc.hook_command_results(
        settings, Path("s.json"), Path("C:/Users/me"), is_file=_always(False), which=_always("found"),
    )
    assert _statuses(results) == [FAIL], results
    assert "C:/Users/someone-else" in results[0].detail and "dev-env#1113" in results[0].detail, results[0].detail
    return results[0].detail


def test_hook_commands_same_home_no_hint():
    settings = _settings("pyw -3 C:/Users/me/.claude/scripts/a.py")
    results = doc.hook_command_results(
        settings, Path("s.json"), Path("C:/Users/me"), is_file=_always(False), which=_always("found"),
    )
    assert _statuses(results) == [FAIL] and "dev-env#1113" not in results[0].detail, results[0].detail
    return "no relocation hint when the scripts are under this machine's own home"


def test_hook_commands_launcher_missing_fails():
    settings = _settings("pyw -3 C:/x/.claude/scripts/a.py")
    results = doc.hook_command_results(
        settings, Path("s.json"), Path("C:/x"), is_file=_always(True), which=_always(None),
    )
    assert _statuses(results) == [PASS, FAIL], results
    assert results[1].check == "hook launcher" and "'pyw'" in results[1].detail, results[1]
    return results[1].detail


def test_hook_commands_unparsed_warns():
    settings = _settings("pyw -3 C:/x/.claude/scripts/a.py", "bash C:/x/.claude/hooks/pre-push")
    results = doc.hook_command_results(
        settings, Path("s.json"), Path("C:/x"), is_file=_always(True), which=_always("found"),
    )
    assert _statuses(results) == [PASS, WARN], results
    assert "1 commands checked" in results[0].detail, "the PASS line counts only checked commands"
    return results[1].detail


# --------------------------------------------------------------------------- links


def _link_fixture(repo_root, claude_home, home):
    """A resolver under which every expected link resolves into repo_root."""
    mapping = {str(claude_home / n): str(repo_root / "claude" / n) for n in doc.LINKED_ITEMS}
    mapping.update({str(home / n): str(repo_root / n) for n in doc.HOME_LINKS})
    return mapping


def test_link_results_all_correct():
    repo, claude_home, home = Path("R:/repo"), Path("H:/home/.claude"), Path("H:/home")
    mapping = _link_fixture(repo, claude_home, home)
    results = doc.link_results(
        claude_home, home, repo, resolve=lambda p: mapping.get(p, p), lexists=_always(True), exists=_always(True),
    )
    assert _statuses(results) == [PASS], results
    assert "all 7" in results[0].detail, results[0].detail
    return results[0].detail


def test_link_results_reports_missing_real_elsewhere_and_dangling():
    repo, claude_home, home = Path("R:/repo"), Path("H:/home/.claude"), Path("H:/home")
    mapping = _link_fixture(repo, claude_home, home)
    del mapping[str(claude_home / "skills")]                     # resolves to itself: a real dir
    mapping[str(claude_home / "hooks")] = "X:/somewhere/else"    # a link pointing elsewhere
    missing = str(claude_home / "templates")
    dangling = str(home / "bin")                                 # resolves correctly, but the target is gone
    results = doc.link_results(
        claude_home, home, repo, resolve=lambda p: mapping.get(p, p),
        lexists=lambda p: p != missing, exists=lambda p: p not in (missing, dangling),
    )
    assert _statuses(results) == [FAIL], results
    detail = results[0].detail.replace("\\", "/")
    assert detail.startswith("4 of 7 wrong"), detail
    assert "templates is missing" in detail, detail
    assert "not a link" in detail and "X:/somewhere/else" in detail, detail
    assert "bin dangles" in detail, detail
    return detail


def test_link_results_real_dangling_junction():
    # The live failure: realpath() of a dangling link returns its missing target, so a
    # comparison with the expected target alone reads it as correct.
    with tempfile.TemporaryDirectory() as tmp:
        repo, claude_home, home = Path(tmp, "repo"), Path(tmp, "home", ".claude"), Path(tmp, "home")
        (repo / "claude" / "templates").mkdir(parents=True)
        claude_home.mkdir(parents=True)
        link = claude_home / "templates"
        _dir_link(repo / "claude" / "templates", link)
        assert os.path.exists(link), "fixture: the link resolves before its target is removed"
        shutil.rmtree(repo / "claude" / "templates")
        assert os.path.lexists(link) and not os.path.exists(link), "fixture: the link now dangles"
        results = doc.link_results(claude_home, home, repo)
        if os.name == "nt":
            os.rmdir(link)  # remove the junction itself; its target is already gone
    detail = results[0].detail.replace("\\", "/")
    assert _statuses(results) == [FAIL] and "templates dangles" in detail, detail
    return "a real dangling link FAILs with 'dangles' (the other six are simply missing here)"


def test_checkout_results():
    exists = _always(True)
    same = doc.checkout_results(Path("C:/Git/dev-env"), Path("C:/Git/dev-env"), "main", is_dir=exists, resolve=lambda p: p)
    assert _statuses(same) == [PASS, PASS], same
    # ~/Git is a junction to D:\Git: the resolved checkout still matches the resolved expectation.
    via_junction = doc.checkout_results(
        Path("D:/Git/dev-env"), Path("C:/Users/me/Git/dev-env"), "main", is_dir=exists,
        resolve=lambda p: p.replace("C:/Users/me/Git", "D:/Git").replace("C:\\Users\\me\\Git", "D:\\Git"),
    )
    assert _statuses(via_junction) == [PASS, PASS], via_junction
    elsewhere = doc.checkout_results(Path("C:/old/dev-env"), Path("C:/Git/dev-env"), "main", is_dir=exists, resolve=lambda p: p)
    assert _statuses(elsewhere) == [WARN, PASS], elsewhere
    detached = doc.checkout_results(Path("C:/Git/dev-env"), Path("C:/Git/dev-env"), "", is_dir=exists, resolve=lambda p: p)
    assert _statuses(detached) == [PASS, WARN] and "detached HEAD" in detached[1].detail, detached
    unknown = doc.checkout_results(Path("C:/Git/dev-env"), Path("C:/Git/dev-env"), None, is_dir=exists, resolve=lambda p: p)
    assert _statuses(unknown) == [PASS, WARN] and "could not read" in unknown[1].detail, unknown
    gone = doc.checkout_results(Path("C:/moved/dev-env"), Path("C:/Git/dev-env"), None, is_dir=_always(False))
    assert _statuses(gone) == [FAIL] and "does not exist" in gone[0].detail, gone
    return "same/junctioned PASS; elsewhere WARN; detached or unreadable branch WARN; missing checkout FAIL"


# --------------------------------------------------------------------------- python sources


def test_compile_results():
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp, "clean.py")
        clean.write_text("x = 1\n", encoding="utf-8")
        bom = Path(tmp, "bom.py")
        bom.write_bytes(b"\xef\xbb\xbfx = 1\n")  # the interpreter accepts a UTF-8 BOM
        warn = Path(tmp, "warn.py")
        warn.write_text('s = "\\d"\n', encoding="utf-8")  # an invalid escape sequence
        bad = Path(tmp, "bad.py")
        bad.write_text("def (:\n", encoding="utf-8")
        nul = Path(tmp, "nul.py")
        nul.write_bytes(b"x = 1\x00\n")  # a ValueError, not a SyntaxError, on Python <= 3.11

        only_clean = doc.compile_results([clean, bom])
        assert _statuses(only_clean) == [PASS], only_clean
        # SyntaxWarning from Python 3.12, DeprecationWarning before: a WARN on either.
        with_warn = doc.compile_results([clean, warn])
        assert _statuses(with_warn) == [WARN] and "warn.py:1" in with_warn[0].detail, with_warn
        all_bad = doc.compile_results([clean, warn, bad, nul])
        assert _statuses(all_bad) == [FAIL, WARN], all_bad
        assert "bad.py" in all_bad[0].detail and "nul.py" in all_bad[0].detail, all_bad[0].detail
    empty = doc.compile_results([])
    assert _statuses(empty) == [FAIL] and "vacuously" in empty[0].detail, empty
    return f"clean and BOM PASS; invalid escape WARN on Python {sys.version_info[0]}.{sys.version_info[1]}; syntax error and NUL FAIL; no files FAIL"


# --------------------------------------------------------------------------- tools & auth


def test_tool_results():
    assert _statuses(doc.tool_results(which=_always("found"))) == [PASS]
    missing_gh = doc.tool_results(which=lambda n: None if n == "gh" else "found")
    assert _statuses(missing_gh) == [FAIL] and "gh (" in missing_gh[0].detail, missing_gh
    missing_node = doc.tool_results(which=lambda n: None if n == "node" else "found")
    assert _statuses(missing_node) == [PASS, WARN], missing_node
    return "all present PASS; gh missing FAIL; node missing WARN"


def test_gh_auth_result():
    ok = "github.com\n  - Token scopes: 'gist', 'project', 'read:org', 'repo', 'workflow'\n"
    no_project = "github.com\n  - Token scopes: 'gist', 'read:org', 'repo'\n"
    assert doc.gh_auth_result(None, None, "").status == INFO
    assert doc.gh_auth_result(1, None, "").status == FAIL, "no token: not signed in"
    offline = doc.gh_auth_result(0, None, "")
    assert offline.status == INFO and "--offline" in offline.detail, offline
    timed_out = doc.gh_auth_result(0, None, "", status_timed_out=True)
    assert timed_out.status == INFO and "timed out" in timed_out.detail, timed_out
    # Offline, gh auth status exits 1 and calls the token invalid: signed in, just unverified.
    unreachable = doc.gh_auth_result(0, 1, "The token in keyring is invalid")
    assert unreachable.status == WARN and "could not be reached" in unreachable.detail, unreachable
    assert doc.gh_auth_result(0, 0, ok).status == PASS
    assert doc.gh_auth_result(0, 0, no_project).status == WARN
    assert doc.gh_auth_result(0, 0, "Logged in to github.com").status == WARN
    return "no gh INFO; no token FAIL; offline/timeout INFO; status failure WARN (not FAIL); scopes PASS/WARN"


def test_credential_helper_result():
    gh = ["", "!'C:\\Program Files\\GitHub CLI\\gh.exe' auth git-credential"]
    assert doc.credential_helper_result(gh).status == PASS
    assert doc.credential_helper_result(["manager"]).status == WARN
    assert doc.credential_helper_result([]).status == WARN
    return "gh helper PASS; manager or none WARN"


def test_identity_result():
    assert doc.identity_result("Me", "me@example.com").status == PASS
    assert doc.identity_result("", "me@example.com").status == WARN
    assert doc.identity_result("Me", "").status == WARN
    return "both set PASS; either missing WARN"


# --------------------------------------------------------------------------- hooks paths


def test_global_hooks_result():
    here = _always(True)
    same = doc.global_hooks_result("C:/Users/me/.claude/hooks", Path("C:/Users/me/.claude/hooks"), resolve=lambda p: p, is_dir=here)
    assert same.status == PASS, same
    assert doc.global_hooks_result(None, Path("C:/h"), resolve=lambda p: p, is_dir=here).status == FAIL
    assert doc.global_hooks_result("", Path("C:/h"), resolve=lambda p: p, is_dir=here).status == FAIL
    other = doc.global_hooks_result("C:/elsewhere", Path("C:/h"), resolve=lambda p: p, is_dir=here)
    assert other.status == WARN, other
    # A dangling ~/.claude/hooks: realpath() still names the expected directory, but git runs nothing.
    dangling = doc.global_hooks_result("C:/h", Path("C:/h"), resolve=lambda p: p, is_dir=_always(False))
    assert dangling.status == FAIL and "does not exist" in dangling.detail, dangling
    return "matching PASS; unset or missing directory FAIL; different WARN"


def test_repo_hooks_results():
    effective = {
        "C:/Git/good": "C:/g/hooks",
        "C:/Git/overridden": "C:/Git/overridden/.git/hooks",
        "C:/Git/unreadable": None,
    }
    results = doc.repo_hooks_results(effective, "C:/g/hooks", resolve=lambda p: p)
    assert _statuses(results) == [PASS, WARN, WARN], results
    assert "1 clone(s)" in results[0].detail
    assert results[1].detail.startswith("overridden:") and "dev-env#1108" in results[1].detail, results[1]
    assert "unreadable" in results[2].detail and "safe.directory" in results[2].detail, results[2]
    assert _statuses(doc.repo_hooks_results({}, "C:/g/hooks")) == [INFO]
    return "global PASS; override WARN; unreadable WARN; none INFO"


# --------------------------------------------------------------------------- journal & config


def test_journal_results():
    j = Path("C:/Git/engineering-journal")
    today = "2026-09-28"
    missing = doc.journal_results(j, False, None, today, None, False, False)
    assert _statuses(missing) == [FAIL] and "git clone" in missing[0].detail, missing
    offline = doc.journal_results(j, True, "main", today, None, False, True)
    assert _statuses(offline) == [INFO, INFO], offline
    unreachable = doc.journal_results(j, True, "main", today, None, False, False)
    assert _statuses(unreachable) == [INFO, WARN], unreachable
    no_branch = doc.journal_results(j, True, "main", today, "", False, False)
    assert _statuses(no_branch) == [INFO, INFO], no_branch
    contained = doc.journal_results(j, True, f"draft/{today}", today, "a" * 40, True, False)
    assert _statuses(contained) == [INFO, PASS], contained
    unreadable = doc.journal_results(j, True, None, today, "", False, False)
    assert _statuses(unreadable) == [WARN, INFO] and "could not read" in unreadable[0].detail, unreadable
    detached = doc.journal_results(j, True, "", today, "", False, False)
    assert detached[0].detail == "detached HEAD", detached
    stale = doc.journal_results(j, True, "main", today, "a" * 40, False, False)
    assert _statuses(stale) == [INFO, WARN], stale
    detail = stale[1].detail
    assert f"checkout draft/{today} (no -b)" in detail and "never --force" in detail, detail
    # This machine's own draft/<today> has no upstream, so the advice names the remote branch.
    assert f"pull --no-rebase --no-edit origin draft/{today}" in detail, detail
    assert f"push -u origin draft/{today}" in detail, detail
    return "missing FAIL; unreadable branch WARN; offline/no-branch INFO; unreachable WARN; contained PASS; not contained WARN with explicit-remote advice"


def test_hook_config_result():
    assert doc.hook_config_result(Path("x/hook-config.json"), True).status == PASS
    absent = doc.hook_config_result(Path("x/hook-config.json"), False)
    assert absent.status == WARN and "copy it from your other machine" in absent.detail, absent
    return "present PASS; absent WARN"


def test_machine_role_result():
    none = doc.machine_role_result(None)
    assert none.status == INFO and "not a routine host" in none.detail, none
    assert "yes" in doc.machine_role_result(b'{"routine_host": true}').detail
    assert "yes" in doc.machine_role_result(b'\xef\xbb\xbf{"routine_host": true}').detail, "a UTF-8 BOM is fine"
    assert doc.machine_role_result(b'{"routine_host": false}').detail.startswith("no")
    assert doc.machine_role_result(b'{"routine_host": "true"}').detail.startswith("no"), "only JSON true counts"
    assert doc.machine_role_result(b"[]").detail.startswith("no")
    assert doc.machine_role_result(b"{").status == WARN
    utf16 = doc.machine_role_result('{"routine_host": true}'.encode("utf-16"))  # PowerShell 5.1's default
    assert utf16.status == WARN and "not UTF-8" in utf16.detail, utf16
    return "absent/false/non-bool/list -> not a host; true -> host; malformed or UTF-16 WARN"


def test_guarded():
    ok = [doc.Result(PASS, "x", "fine")]
    assert doc.guarded("x", lambda: ok) == ok

    def boom():
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    crashed = doc.guarded("routine host", boom)
    assert _statuses(crashed) == [FAIL] and crashed[0].check == "routine host", crashed
    assert "crashed" in crashed[0].detail and "UnicodeDecodeError" in crashed[0].detail, crashed
    return crashed[0].detail


def test_exit_code():
    ok = [doc.Result(PASS, "a", ""), doc.Result(WARN, "b", ""), doc.Result(INFO, "c", "")]
    assert doc.exit_code(ok) == 0
    assert doc.exit_code(ok + [doc.Result(FAIL, "d", "")]) == 1
    assert doc.exit_code([]) == 0
    return "FAIL present -> 1, else 0"


def main() -> int:
    tests = [
        ("LINKED_ITEMS / HOME_LINKS match setup.sh's link arrays", test_link_lists_match_setup_sh),
        ("script path extracted from hook commands", test_script_path_from_command),
        ("home prefix of a hook script path", test_home_prefix),
        ("same_path normalization and case rule", test_same_path),
        ("ls-remote parsed from stdout, SHA validated", test_parse_ls_remote),
        ("hook commands all present -> PASS", test_hook_commands_all_present),
        ("a missing hook script -> FAIL", test_hook_commands_missing_script_fails),
        ("zero hook commands -> FAIL, not a vacuous PASS", test_hook_commands_zero_entries_fails_not_vacuous),
        ("no readable script path -> FAIL, not a vacuous PASS", test_hook_commands_none_parsed_fails_not_vacuous),
        ("unreadable settings -> FAIL", test_hook_commands_unreadable_settings_fails),
        ("scripts under another home -> relocation hint", test_hook_commands_other_home_hint),
        ("scripts under this home -> no relocation hint", test_hook_commands_same_home_no_hint),
        ("hook launcher not on PATH -> FAIL", test_hook_commands_launcher_missing_fails),
        ("a command with no .py -> WARN", test_hook_commands_unparsed_warns),
        ("all links resolve into the repo -> PASS", test_link_results_all_correct),
        ("missing, real, misdirected and dangling links -> one FAIL", test_link_results_reports_missing_real_elsewhere_and_dangling),
        ("a real dangling junction -> FAIL", test_link_results_real_dangling_junction),
        ("dev-env checkout location, existence and branch", test_checkout_results),
        ("compile results by severity, across Python versions", test_compile_results),
        ("tool presence", test_tool_results),
        ("gh sign-in and scopes", test_gh_auth_result),
        ("gh credential helper", test_credential_helper_result),
        ("git identity", test_identity_result),
        ("global core.hooksPath", test_global_hooks_result),
        ("per-clone effective hooks dir", test_repo_hooks_results),
        ("journal clone and today's draft branch", test_journal_results),
        ("dev-env board config", test_hook_config_result),
        ("routine-host flag", test_machine_role_result),
        ("a crashing check becomes a FAIL line", test_guarded),
        ("exit code", test_exit_code),
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
