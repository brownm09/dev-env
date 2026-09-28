#!/usr/bin/env python3
"""Unit tests for dev-env-doctor.py, the read-only install health check (dev-env#1114).

Exercises the doctor's pure decision helpers (`*_result` / `*_results`) against fixtures
only -- temp files, fake resolvers, injected `which`/`is_file` callables -- so nothing here
reads this machine's real ~/.claude, git config, clones or network. `collect()` and `_run()`
(the thin I/O layer around the helpers) are deliberately untested, matching the repo's
no-subprocess-mock convention (test_disk_space_check.py).

Two checks guard against the doctor itself going vacuous (ADR-144): the link list is pinned
against setup.sh's real arrays -- asserting the parse extracted something before comparing --
and the hook-command and compile checks must FAIL, not PASS, on zero inputs.

Usage:
    py -3 claude/scripts/tests/test_dev_env_doctor.py

Exit 0 = all pass.
"""

import importlib.util
import os
import re
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


# --------------------------------------------------------------------------- setup.sh parity


def test_linked_items_match_setup_sh():
    text = SETUP_SH.read_text(encoding="utf-8")
    file_m = re.search(r"^CLAUDE_FILE_LINKS=\(([^)]*)\)", text, re.M)
    dir_m = re.search(r"^CLAUDE_DIR_LINKS=\(([^)]*)\)", text, re.M)
    assert file_m and dir_m, "could not find CLAUDE_FILE_LINKS / CLAUDE_DIR_LINKS in setup.sh"
    file_links, dir_links = file_m.group(1).split(), dir_m.group(1).split()
    # Non-empty before comparing: an empty parse compared against an empty tuple would pass
    # vacuously (ADR-144).
    assert file_links and dir_links, f"empty parse: {file_links!r} {dir_links!r}"
    assert "claude/routines" in text, "setup.sh no longer links claude/routines"
    expected = tuple(file_links + dir_links + ["routines"])
    assert doc.LINKED_ITEMS == expected, f"doctor checks {doc.LINKED_ITEMS}, setup.sh links {expected}"
    return f"LINKED_ITEMS == {expected}"


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


# --------------------------------------------------------------------------- hook commands


def test_hook_commands_all_present():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp, "a.py"), Path(tmp, "b.py")
        a.write_text("", encoding="utf-8")
        b.write_text("", encoding="utf-8")
        settings = _settings(f"pyw -3 {a.as_posix()}", f"pyw -3 {b.as_posix()}", f"pyw -3 {a.as_posix()}")
        results = doc.hook_command_results(settings, Path("s.json"), Path(tmp), which=_always("found"))
    assert _statuses(results) == [PASS], results
    assert "3 commands, 2 distinct scripts" in results[0].detail, results[0].detail
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
    return results[1].detail


# --------------------------------------------------------------------------- links


def _link_fixture(repo_root, claude_home, bin_link):
    """A resolver under which every expected link resolves into repo_root."""
    mapping = {str(claude_home / n): str(repo_root / "claude" / n) for n in doc.LINKED_ITEMS}
    mapping[str(bin_link)] = str(repo_root / "bin")
    return mapping


def test_link_results_all_correct():
    repo, home, bin_link = Path("R:/repo"), Path("H:/home/.claude"), Path("H:/home/bin")
    mapping = _link_fixture(repo, home, bin_link)
    results = doc.link_results(home, bin_link, repo, resolve=lambda p: mapping.get(p, p), lexists=_always(True))
    assert _statuses(results) == [PASS], results
    assert "all 7" in results[0].detail, results[0].detail
    return results[0].detail


def test_link_results_reports_missing_real_and_elsewhere():
    repo, home, bin_link = Path("R:/repo"), Path("H:/home/.claude"), Path("H:/home/bin")
    mapping = _link_fixture(repo, home, bin_link)
    del mapping[str(home / "skills")]                     # resolves to itself: a real dir
    mapping[str(home / "hooks")] = "X:/somewhere/else"    # a link pointing elsewhere
    missing = str(home / "templates")
    results = doc.link_results(
        home, bin_link, repo, resolve=lambda p: mapping.get(p, p), lexists=lambda p: p != missing,
    )
    assert _statuses(results) == [FAIL], results
    detail = results[0].detail
    assert detail.startswith("3 of 7 wrong"), detail
    assert "templates is missing" in detail.replace("\\", "/"), detail
    assert "not a link" in detail and "X:/somewhere/else" in detail, detail
    return detail


# --------------------------------------------------------------------------- python sources


def test_compile_results():
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp, "clean.py")
        clean.write_text("x = 1\n", encoding="utf-8")
        warn = Path(tmp, "warn.py")
        warn.write_text('s = "\\d"\n', encoding="utf-8")  # an invalid escape sequence
        bad = Path(tmp, "bad.py")
        bad.write_text("def (:\n", encoding="utf-8")

        only_clean = doc.compile_results([clean])
        assert _statuses(only_clean) == [PASS], only_clean
        with_warn = doc.compile_results([clean, warn])
        assert _statuses(with_warn) == [WARN] and "warn.py:1" in with_warn[0].detail, with_warn
        all_three = doc.compile_results([clean, warn, bad])
        assert _statuses(all_three) == [FAIL, WARN] and "bad.py" in all_three[0].detail, all_three
    empty = doc.compile_results([])
    assert _statuses(empty) == [FAIL] and "vacuously" in empty[0].detail, empty
    return "clean -> PASS; invalid escape -> WARN; syntax error -> FAIL; no files -> FAIL"


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
    assert doc.gh_auth_result(None, "").status == INFO
    assert doc.gh_auth_result(1, "You are not logged into any GitHub hosts").status == FAIL
    assert doc.gh_auth_result(0, ok).status == PASS
    assert doc.gh_auth_result(0, no_project).status == WARN
    assert doc.gh_auth_result(0, "Logged in to github.com").status == WARN
    return "skipped INFO; signed out FAIL; project scope PASS; no project scope or unreadable scopes WARN"


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
    same = doc.global_hooks_result("C:/Users/me/.claude/hooks", Path("C:/Users/me/.claude/hooks"), resolve=lambda p: p)
    assert same.status == PASS, same
    assert doc.global_hooks_result(None, Path("C:/h"), resolve=lambda p: p).status == FAIL
    assert doc.global_hooks_result("", Path("C:/h"), resolve=lambda p: p).status == FAIL
    other = doc.global_hooks_result("C:/elsewhere", Path("C:/h"), resolve=lambda p: p)
    assert other.status == WARN, other
    return "matching PASS; unset FAIL; different WARN"


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
    contained = doc.journal_results(j, True, f"draft/{today}", today, "abc123", True, False)
    assert _statuses(contained) == [INFO, PASS], contained
    stale = doc.journal_results(j, True, "main", today, "abc123", False, False)
    assert _statuses(stale) == [INFO, WARN], stale
    detail = stale[1].detail
    assert f"checkout draft/{today} (no -b)" in detail and "never --force" in detail, detail
    return "missing FAIL; offline/no-branch INFO; unreachable WARN; contained PASS; not contained WARN"


def test_hook_config_result():
    assert doc.hook_config_result(Path("x/hook-config.json"), True).status == PASS
    absent = doc.hook_config_result(Path("x/hook-config.json"), False)
    assert absent.status == WARN and "copy it from your other machine" in absent.detail, absent
    return "present PASS; absent WARN"


def test_machine_role_result():
    none = doc.machine_role_result(None)
    assert none.status == INFO and "not a routine host" in none.detail, none
    assert "yes" in doc.machine_role_result('{"routine_host": true}').detail
    assert doc.machine_role_result('{"routine_host": false}').detail.startswith("no")
    assert doc.machine_role_result('{"routine_host": "true"}').detail.startswith("no"), "only JSON true counts"
    assert doc.machine_role_result("[]").detail.startswith("no")
    assert doc.machine_role_result("{").status == WARN
    return "absent/false/non-bool/list -> not a host; true -> host; malformed WARN"


def test_exit_code():
    ok = [doc.Result(PASS, "a", ""), doc.Result(WARN, "b", ""), doc.Result(INFO, "c", "")]
    assert doc.exit_code(ok) == 0
    assert doc.exit_code(ok + [doc.Result(FAIL, "d", "")]) == 1
    assert doc.exit_code([]) == 0
    return "FAIL present -> 1, else 0"


def main() -> int:
    tests = [
        ("LINKED_ITEMS matches setup.sh's link arrays", test_linked_items_match_setup_sh),
        ("script path extracted from hook commands", test_script_path_from_command),
        ("home prefix of a hook script path", test_home_prefix),
        ("same_path normalization and case rule", test_same_path),
        ("hook commands all present -> PASS", test_hook_commands_all_present),
        ("a missing hook script -> FAIL", test_hook_commands_missing_script_fails),
        ("zero hook commands -> FAIL, not a vacuous PASS", test_hook_commands_zero_entries_fails_not_vacuous),
        ("unreadable settings -> FAIL", test_hook_commands_unreadable_settings_fails),
        ("scripts under another home -> relocation hint", test_hook_commands_other_home_hint),
        ("scripts under this home -> no relocation hint", test_hook_commands_same_home_no_hint),
        ("hook launcher not on PATH -> FAIL", test_hook_commands_launcher_missing_fails),
        ("a command with no .py -> WARN", test_hook_commands_unparsed_warns),
        ("all links resolve into the repo -> PASS", test_link_results_all_correct),
        ("missing, real and misdirected links -> one FAIL", test_link_results_reports_missing_real_and_elsewhere),
        ("compile results by severity", test_compile_results),
        ("tool presence", test_tool_results),
        ("gh auth scopes", test_gh_auth_result),
        ("gh credential helper", test_credential_helper_result),
        ("git identity", test_identity_result),
        ("global core.hooksPath", test_global_hooks_result),
        ("per-clone effective hooks dir", test_repo_hooks_results),
        ("journal clone and today's draft branch", test_journal_results),
        ("dev-env board config", test_hook_config_result),
        ("routine-host flag", test_machine_role_result),
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
