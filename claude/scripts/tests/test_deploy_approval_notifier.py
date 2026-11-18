#!/usr/bin/env python3
"""Tests for deploy-approval-notifier.py and register-deploy-approval-notifier.ps1
(dev-env#1380, ADR-163).

Required when changing `deploy-approval-notifier.py` or `register-deploy-approval-notifier.ps1`.
Run: py -3 claude/scripts/tests/test_deploy_approval_notifier.py

Nothing here calls GitHub or shows a toast: a FakeGh answers `gh api` calls from
canned JSON and records every POST, and a recording toaster stands in for
PowerShell. `DEPLOY_NOTIFIER_DIR_OVERRIDE` and `DEPLOY_NOTIFIER_CONFIG` keep
state and config in a temp directory. It pins one toast per waiting production
run, skipping runs the user can't approve or that wait on another environment,
forgetting runs that stopped waiting, every refusal path of the Approve handler
(no POST), the approval payload, XML escaping and control-character stripping,
saving each nonce as soon as its toast shows, the one-time "can't reach GitHub"
alert, logging of a crash or an unreadable state file, and the registration
script's settings.

Scope gap: whether Windows shows the toast and routes its buttons can only be
seen on a real desktop. When changing the toast or the URL scheme, run
`py -3 claude/scripts/deploy-approval-notifier.py --test-toast` and check that
Review opens the run and Approve ends in a "Deploy not approved" toast.

Exit 0 = all pass.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.dom.minidom
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

_TMP = Path(tempfile.mkdtemp(prefix="deploy-notifier-test-"))
os.environ["DEPLOY_NOTIFIER_DIR_OVERRIDE"] = str(_TMP / "state")
os.environ["DEPLOY_NOTIFIER_CONFIG"] = str(_TMP / "config.json")

_spec = importlib.util.spec_from_file_location("deploy_approval_notifier", SCRIPTS / "deploy-approval-notifier.py")
notifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notifier)

REPO = "merickvaughn/lifting-logbook"


def _eq(actual, expected, message=""):
    assert actual == expected, f"{message} expected {expected!r}, got {actual!r}"


def _reset(repos=None):
    shutil.rmtree(_TMP / "state", ignore_errors=True)
    config = _TMP / "config.json"
    if repos is None:
        config.unlink(missing_ok=True)
    else:
        config.write_text(json.dumps({"repos": repos}), encoding="utf-8")


def _run(run_id, message="Fix the thing", sha="abcdef1234567"):
    return {"id": run_id, "head_sha": sha, "head_commit": {"message": message}, "display_title": message}


def _pending(name="production", env_id=111, can_approve=True):
    return [{"environment": {"id": env_id, "name": name}, "current_user_can_approve": can_approve}]


class FakeGh:
    def __init__(self, waiting=None, pending=None, post_rc=0, fail_paths=()):
        self.waiting = waiting or {}        # repo -> list of runs
        self.pending = pending or {}        # run_id -> pending_deployments JSON
        self.post_rc = post_rc
        self.fail_paths = fail_paths
        self.posts = []
        self.gets = []

    def __call__(self, args, input_text=None, env=None, timeout=60):
        if args[:2] != ["gh", "api"]:
            raise AssertionError(f"unexpected command {args}")
        if "-X" in args:
            self.posts.append((args[-3], json.loads(input_text)))
            return self.post_rc, "{}", "" if self.post_rc == 0 else "HTTP 422"
        path = args[2]
        self.gets.append(path)
        if any(p in path for p in self.fail_paths):
            return 1, "", "HTTP 502"
        if "/pending_deployments" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            return 0, json.dumps(self.pending.get(run_id, [])), ""
        if "status=waiting" in path:
            repo = path.split("repos/")[1].split("/actions")[0]
            return 0, json.dumps({"workflow_runs": self.waiting.get(repo, [])}), ""
        if "per_page=1" in path:
            return 0, json.dumps({"workflow_runs": [_run(42)]}), ""
        raise AssertionError(f"unexpected path {path}")


class Toasts:
    def __init__(self, ok=True):
        self.shown = []
        self.ok = ok

    def __call__(self, xml_text, runner=None):
        xml.dom.minidom.parseString(xml_text)  # every toast must be well-formed XML
        self.shown.append(xml_text)
        return self.ok


def _approve_uri(xml_text):
    doc = xml.dom.minidom.parseString(xml_text)
    for action in doc.getElementsByTagName("action"):
        if action.getAttribute("content") == "Approve":
            return action.getAttribute("arguments")
    raise AssertionError("no Approve button")


def _notify_one(run_id=7, pending=None):
    """Tick once with one waiting run; return (gh, toasts)."""
    gh = FakeGh(waiting={REPO: [_run(run_id)]}, pending={run_id: _pending() if pending is None else pending})
    toasts = Toasts()
    notifier.tick(runner=gh, toaster=toasts)
    return gh, toasts


# --- Tick -------------------------------------------------------------------


def test_waiting_production_run_toasts_once():
    _reset()
    gh, toasts = _notify_one()
    _eq(len(toasts.shown), 1, "first tick")
    assert "runs/7" in toasts.shown[0] and "scenario=\"reminder\"" in toasts.shown[0]
    assert _approve_uri(toasts.shown[0]).startswith(f"deploy-approve:{REPO}/7/")
    notifier.tick(runner=gh, toaster=toasts)
    _eq(len(toasts.shown), 1, "second tick must not re-toast")


def test_other_environment_or_no_permission_stays_quiet():
    for pending in (_pending(name="staging"), _pending(can_approve=False), []):
        _reset()
        _, toasts = _notify_one(pending=pending)
        _eq(toasts.shown, [], f"pending={pending}")
        _eq(notifier.load_state()["runs"], {}, "nothing recorded")


def test_failed_toast_is_retried_next_tick():
    _reset()
    gh = FakeGh(waiting={REPO: [_run(7)]}, pending={7: _pending()})
    notifier.tick(runner=gh, toaster=Toasts(ok=False))
    _eq(notifier.load_state()["runs"], {}, "a toast that failed isn't recorded")
    toasts = Toasts()
    notifier.tick(runner=gh, toaster=toasts)
    _eq(len(toasts.shown), 1, "retried")


def test_run_that_stopped_waiting_is_forgotten():
    _reset()
    gh, toasts = _notify_one()
    gh.waiting = {REPO: []}
    notifier.tick(runner=gh, toaster=toasts)
    _eq(notifier.load_state()["runs"], {}, "pruned")


def test_github_failure_keeps_state_and_continues():
    _reset(repos=[REPO, "other/repo"])
    gh, _ = _notify_one()
    gh.fail_paths = (f"repos/{REPO}/actions/runs?",)
    gh.waiting["other/repo"] = [_run(9)]
    gh.pending[9] = _pending(env_id=222)
    toasts = Toasts()
    notifier.tick(runner=gh, toaster=toasts)
    runs = notifier.load_state()["runs"]
    assert f"{REPO}#7" in runs, "a failed poll must not forget the repo's runs"
    assert "other/repo#9" in runs, "the other repo still polled"
    _eq(len(toasts.shown), 1)


def test_toast_escapes_commit_message():
    _reset()
    gh = FakeGh(waiting={REPO: [_run(7, message='Fix <b> & "quotes"\nsecond line')]}, pending={7: _pending()})
    toasts = Toasts()
    notifier.tick(runner=gh, toaster=toasts)
    doc = xml.dom.minidom.parseString(toasts.shown[0])
    texts = [t.firstChild.data for t in doc.getElementsByTagName("text")]
    _eq(texts[1], 'Fix <b> & "quotes" (abcdef1)', "body round-trips, first line only")


# --- Approve ----------------------------------------------------------------


def test_approve_posts_once_with_the_environment_id():
    _reset()
    gh, toasts = _notify_one(pending=_pending(env_id=555))
    uri = _approve_uri(toasts.shown[0])
    _eq(notifier.approve(uri, runner=gh, toaster=toasts), 0)
    _eq(gh.posts, [(f"repos/{REPO}/actions/runs/7/pending_deployments",
                    {"environment_ids": [555], "state": "approved", "comment": notifier.APPROVE_COMMENT})])
    assert "approved" in toasts.shown[-1]


def test_approve_accepts_trailing_slash_and_case():
    _reset()
    gh, toasts = _notify_one()
    uri = _approve_uri(toasts.shown[0]).replace("merickvaughn", "MerickVaughn") + "/"
    _eq(notifier.approve(uri, runner=gh, toaster=toasts), 0)
    _eq(len(gh.posts), 1)


def test_approve_refuses_wrong_nonce():
    _reset()
    gh, toasts = _notify_one()
    uri = _approve_uri(toasts.shown[0]).rsplit("/", 1)[0] + "/forged"
    _eq(notifier.approve(uri, runner=gh, toaster=toasts), 1)
    _eq(gh.posts, [], "no POST")
    assert "not approved" in toasts.shown[-1]


def test_approve_refuses_unwatched_repo():
    _reset()
    gh, toasts = _notify_one()
    uri = _approve_uri(toasts.shown[0]).replace(REPO, "evil/repo")
    _eq(notifier.approve(uri, runner=gh, toaster=toasts), 1)
    _eq(gh.posts, [], "no POST")


def test_approve_refuses_run_no_longer_waiting():
    _reset()
    gh, toasts = _notify_one()
    gh.pending[7] = []
    _eq(notifier.approve(_approve_uri(toasts.shown[0]), runner=gh, toaster=toasts), 1)
    _eq(gh.posts, [], "no POST")


def test_approve_refuses_malformed_uri():
    _reset()
    gh, toasts = _notify_one()
    for uri in ("deploy-approve:x", "deploy-approve:a/b/notanumber/n", "https://evil/a/b/1/n"):
        _eq(notifier.approve(uri, runner=gh, toaster=toasts), 1, uri)
    _eq(gh.posts, [], "no POST")


def test_approve_reports_a_failed_post():
    _reset()
    gh, toasts = _notify_one()
    gh.post_rc = 1
    _eq(notifier.approve(_approve_uri(toasts.shown[0]), runner=gh, toaster=toasts), 1)
    assert "Approval failed" in toasts.shown[-1]


def test_test_toast_nonce_is_refused():
    _reset()
    # Run 42 is remembered with a real nonce, so the refusal has to come from the nonce check.
    gh, toasts = _notify_one(run_id=42)
    _eq(notifier.test_toast(runner=gh, toaster=toasts), 0)
    _eq(notifier.approve(_approve_uri(toasts.shown[-1]), runner=gh, toaster=toasts), 1)
    _eq(gh.posts, [], "the test toast never approves")
    assert "doesn't match" in toasts.shown[-1]


def test_approve_github_failure_says_so_and_re_toasts():
    _reset()
    gh, toasts = _notify_one()
    uri = _approve_uri(toasts.shown[0])
    gh.fail_paths = ("/pending_deployments",)
    _eq(notifier.approve(uri, runner=gh, toaster=toasts), 1)
    _eq(gh.posts, [], "no POST")
    assert "Couldn't reach GitHub" in toasts.shown[-1], toasts.shown[-1]
    _eq(notifier.load_state()["runs"], {}, "the run is forgotten so it can be toasted again")
    gh.fail_paths = ()
    before = len(toasts.shown)
    notifier.tick(runner=gh, toaster=toasts)
    _eq(len(toasts.shown), before + 1, "next tick toasts the still-waiting run again")


def test_each_toast_is_saved_before_the_tick_goes_on():
    _reset()
    gh = FakeGh(waiting={REPO: [_run(7), _run(8)]}, pending={7: _pending(), 8: _pending()})

    class DiesOnSecond(Toasts):
        def __call__(self, xml_text, runner=None):
            if self.shown:
                raise RuntimeError("tick killed")
            return super().__call__(xml_text, runner)

    toasts = DiesOnSecond()
    try:
        notifier.tick(runner=gh, toaster=toasts)
    except RuntimeError:
        pass
    _eq(list(notifier.load_state()["runs"]), [f"{REPO}#7"], "the first toast's nonce survived")
    _eq(notifier.approve(_approve_uri(toasts.shown[0]), runner=gh, toaster=Toasts()), 0)


def test_unreachable_github_toasts_once_then_resets():
    _reset()
    gh = FakeGh(fail_paths=("status=waiting",))
    toasts = Toasts()
    for _ in range(notifier.FAILED_POLL_ALERT - 1):
        notifier.tick(runner=gh, toaster=toasts)
    _eq(toasts.shown, [], "quiet below the threshold")
    notifier.tick(runner=gh, toaster=toasts)
    _eq(len(toasts.shown), 1, "one alert at the threshold")
    assert "can't reach GitHub" in toasts.shown[0]
    notifier.tick(runner=gh, toaster=toasts)
    _eq(len(toasts.shown), 1, "no repeat while still failing")
    gh.fail_paths = ()
    notifier.tick(runner=gh, toaster=toasts)
    state = notifier.load_state()
    _eq((state["failed_polls"], state["alerted"]), (0, False), "reset on success")
    assert state.get("last_ok"), "last good poll recorded"


def test_control_characters_are_stripped_from_toasts():
    _reset()
    gh = FakeGh(waiting={REPO: [_run(7, message="bad\x01\x1bchars")]}, pending={7: _pending()})
    toasts = Toasts()
    notifier.tick(runner=gh, toaster=toasts)
    doc = xml.dom.minidom.parseString(toasts.shown[0])
    _eq(doc.getElementsByTagName("text")[1].firstChild.data, "badchars (abcdef1)")


def test_runs_of_unwatched_repos_are_forgotten():
    _reset(repos=[REPO, "other/repo"])
    gh = FakeGh(waiting={"other/repo": [_run(9)]}, pending={9: _pending()})
    notifier.tick(runner=gh, toaster=Toasts())
    assert "other/repo#9" in notifier.load_state()["runs"]
    _reset(repos=[REPO])
    notifier.save_state({"runs": {"other/repo#9": {"repo": "other/repo", "run_id": 9}}})
    notifier.tick(runner=FakeGh(), toaster=Toasts())
    _eq(notifier.load_state()["runs"], {})


def test_unreadable_state_is_logged():
    _reset()
    (_TMP / "state").mkdir(parents=True, exist_ok=True)
    (_TMP / "state" / "state.json").write_text("{torn", encoding="utf-8")
    _eq(notifier.load_state(), {"runs": {}})
    assert "state.json unreadable" in (_TMP / "state" / "notifier.log").read_text(encoding="utf-8")


def test_a_crash_is_logged_and_an_approve_click_is_told():
    _reset()

    def boom(argv):
        raise ValueError("unexpected")

    toasts = Toasts()
    _eq(notifier.guarded_main(["--approve", "deploy-approve:a/b/1/n"], entry=boom, toaster=toasts), 1)
    assert "crash: Traceback" in (_TMP / "state" / "notifier.log").read_text(encoding="utf-8")
    assert "internal error" in toasts.shown[-1]
    before = len(toasts.shown)
    _eq(notifier.guarded_main(["--tick"], entry=boom, toaster=toasts), 1)
    _eq(len(toasts.shown), before, "a crashed tick doesn't toast")


# --- Config and registration ------------------------------------------------


def test_bad_config_falls_back_to_defaults():
    _reset()
    (_TMP / "config.json").write_text("{not json", encoding="utf-8")
    _eq(notifier.watched_repos(), notifier.DEFAULT_REPOS)
    _reset(repos=["no-slash"])
    _eq(notifier.watched_repos(), notifier.DEFAULT_REPOS)


def test_register_script_parses_and_keeps_its_settings():
    path = SCRIPTS / "register-deploy-approval-notifier.ps1"
    parse = (
        "$e = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{path}', [ref]$null, [ref]$e); $e.Count"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", parse],
                          capture_output=True, text=True, check=False, timeout=120)
    _eq((proc.returncode, proc.stdout.strip()), (0, "0"), f"parse errors: {proc.stderr}")
    text = path.read_text(encoding="utf-8")
    # Code lines, not prose: the comment block mentions --tick and --approve too.
    for needle in ("-RunLevel Limited", "-LogonType Interactive", "-Hidden", "-MultipleInstances IgnoreNew",
                   "-AtLogOn", "-RepetitionInterval (New-TimeSpan -Minutes 2)",
                   "Get-Command pyw.exe", "$taskArgs = '-3 \"{0}\" --tick' -f $ScriptPath",
                   "-Execute $pyw -Argument $taskArgs", "--approve \"%1\"' -f $pyw, $ScriptPath",
                   "-Name 'URL Protocol'", "Export-ScheduledTask", "& reg.exe export",
                   "$task.Actions[0].Arguments -ne $taskArgs", "if (-not (Test-OurKey))"):
        assert needle in text, f"missing {needle}"
    for banned in ("-Verb RunAs", "-NoExit", "Start-Process"):
        assert banned not in text, f"ADR-041: {banned}"


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert tests, "no tests collected"
    failed = 0
    start = time.time()
    for name, func in tests:
        try:
            func()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001 -- report and keep going
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed ({time.time() - start:.1f}s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
