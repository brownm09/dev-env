#!/usr/bin/env python3
"""Windows notification for GitHub Actions runs waiting on a production
environment approval, with a one-click Approve button. See ADR-163 and
dev-env#1380.

Purpose: scheduled-task payload that shows one toast per run waiting on a
`production` environment the signed-in `gh` user can approve, and the handler
for that toast's Approve button.

  --tick          Poll each watched repo over REST, toast new waiting runs,
                  and forget runs that stopped waiting. After FAILED_POLL_ALERT
                  ticks in a row that couldn't reach GitHub, toast once to say so.
  --approve URI   Handle deploy-approve:<owner>/<repo>/<run-id>/<nonce>. Approves
                  only when the repo is watched, the nonce matches the toast this
                  script showed, and the run still waits on production.
  --status        Print watched repos, remembered runs, the last good poll and
                  the recent log. Changes nothing.
  --test-toast    Toast the latest run of the first watched repo with a nonce
                  that matches nothing, so Approve exercises the URL scheme and
                  ends in a "refused" toast without approving anything.

Registered by register-deploy-approval-notifier.ps1 (a scheduled task for
--tick and the `deploy-approve` URL scheme for --approve). Watched repos come
from ~/.claude/deploy-approval-notifier.json ({"repos": ["owner/name"]}),
defaulting to DEFAULT_REPOS. State and notifier.log live in
~/.claude/deploy-approval-notifier/, outside scratch, because the state holds
the nonce every Approve button needs.
"""
import argparse
import contextlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import traceback
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _winsubp  # noqa: E402,F401 -- no console flash, UTF-8 decoding for gh/powershell

DEFAULT_REPOS = ["merickvaughn/lifting-logbook"]
ENVIRONMENT = "production"
SCHEME = "deploy-approve"
APPROVE_COMMENT = "Approved from Windows notification (deploy-approval-notifier)"
DIR_OVERRIDE_ENV = "DEPLOY_NOTIFIER_DIR_OVERRIDE"
CONFIG_OVERRIDE_ENV = "DEPLOY_NOTIFIER_CONFIG"
LOG_MAX_BYTES = 256 * 1024
GH_TIMEOUT = 20          # Seconds per gh call, so a tick stays well inside the task's 5-minute limit.
FAILED_POLL_ALERT = 5    # Ticks in a row (2 minutes apart) that couldn't reach GitHub before one toast says so.
RUN_FAILED = -1

_URI = re.compile(
    r"^deploy-approve:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(\d+)/([A-Za-z0-9_-]+)/?$"
)
# Characters XML 1.0 forbids; LoadXml rejects a toast containing any of them.
_XML_INVALID = re.compile("[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]")

# PowerShell's AUMID, as docker-watchdog.py uses: an unpackaged script has none of its own.
_TOAST_SCRIPT = r"""
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime]
$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml($env:DEPLOY_NOTIFIER_TOAST_XML)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""


# --- Plumbing ---------------------------------------------------------------


def state_dir() -> Path:
    override = os.environ.get(DIR_OVERRIDE_ENV)
    return Path(override) if override else Path.home() / ".claude" / "deploy-approval-notifier"


def config_path() -> Path:
    override = os.environ.get(CONFIG_OVERRIDE_ENV)
    return Path(override) if override else Path.home() / ".claude" / "deploy-approval-notifier.json"


def log(message: str) -> None:
    """Best effort: a logging failure never changes what the notifier does."""
    with contextlib.suppress(OSError):
        state_dir().mkdir(parents=True, exist_ok=True)
        path = state_dir() / "notifier.log"
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, str(path) + ".1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")


def run(args: list, input_text: "str | None" = None, env: "dict | None" = None,
        timeout: float = GH_TIMEOUT) -> "tuple[int, str, str]":
    """Run a command. A timeout or a failure to launch returns RUN_FAILED."""
    try:
        proc = subprocess.run(args, input=input_text, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return RUN_FAILED, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def watched_repos() -> "list[str]":
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return list(DEFAULT_REPOS)
    except (OSError, ValueError) as exc:
        log(f"config unreadable ({exc}); using defaults")
        return list(DEFAULT_REPOS)
    repos = data.get("repos") if isinstance(data, dict) else None
    if not isinstance(repos, list) or not all(isinstance(r, str) and "/" in r for r in repos):
        log("config has no valid 'repos' list; using defaults")
        return list(DEFAULT_REPOS)
    return repos


def load_state() -> dict:
    path = state_dir() / "state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"runs": {}}
    except (OSError, ValueError) as exc:
        log(f"state.json unreadable ({exc}); starting empty, so open Approve buttons will be refused")
        return {"runs": {}}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
        log("state.json has no 'runs' map; starting empty")
        return {"runs": {}}
    return data


def save_state(state: dict) -> bool:
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        path = state_dir() / "state.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log(f"could not save state.json: {exc}")
        return False
    return True


def gh_json(runner, path: str) -> "tuple[object, str | None]":
    """GET a REST path through `gh api`. Returns (parsed JSON, None), or (None, error)."""
    code, out, err = runner(["gh", "api", path])
    if code != 0:
        error = f"gh api {path} failed rc={code}: {err.strip()[:300]}"
        log(error)
        return None, error
    try:
        return json.loads(out), None
    except ValueError:
        log(f"gh api {path} returned non-JSON")
        return None, f"gh api {path} returned non-JSON"


def run_key(repo: str, run_id: int) -> str:
    return f"{repo.lower()}#{run_id}"


def run_url(repo: str, run_id: int) -> str:
    return f"https://github.com/{repo}/actions/runs/{run_id}"


# --- Toasts -----------------------------------------------------------------


def _text(value: str) -> str:
    return escape(_XML_INVALID.sub("", value))


def toast_xml(title: str, body: str, launch: "str | None" = None,
              actions: "list[tuple[str, str]] | None" = None, reminder: bool = False) -> str:
    """Build toast XML. `actions` are (label, protocol URI) buttons."""
    attrs = ""
    if launch:
        attrs += f" activationType=\"protocol\" launch={quoteattr(launch)}"
    if reminder:
        attrs += " scenario=\"reminder\""
    parts = [f"<toast{attrs}>", "<visual><binding template=\"ToastGeneric\">",
             f"<text>{_text(title)}</text>", f"<text>{_text(body)}</text>",
             "</binding></visual>"]
    if actions or reminder:
        parts.append("<actions>")
        for label, uri in actions or []:
            parts.append(f"<action content={quoteattr(label)} activationType=\"protocol\" "
                         f"arguments={quoteattr(uri)}/>")
        if reminder:
            # A reminder stays on screen until a button is pressed, so give it a way out.
            parts.append("<action content=\"Dismiss\" activationType=\"system\" arguments=\"dismiss\"/>")
        parts.append("</actions>")
    parts.append("</toast>")
    return "".join(parts)


def show_toast(xml: str, runner=run) -> bool:
    env = dict(os.environ, DEPLOY_NOTIFIER_TOAST_XML=xml)
    code, _, err = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_SCRIPT],
                          env=env, timeout=30)
    if code != 0:
        log(f"toast failed rc={code}: {err.strip()[:300]}")
    return code == 0


def waiting_toast(repo: str, run: dict, nonce: str) -> str:
    run_id = run["id"]
    title = f"Production deploy waiting: {repo}"
    message = ((run.get("head_commit") or {}).get("message") or run.get("display_title") or "").splitlines()
    first_line = message[0] if message else ""
    body = f"{first_line} ({(run.get('head_sha') or '')[:7]})".strip()
    url = run_url(repo, run_id)
    return toast_xml(title, body, launch=url, reminder=True,
                     actions=[("Review", url), ("Approve", f"{SCHEME}:{repo}/{run_id}/{nonce}")])


# --- Tick -------------------------------------------------------------------


def approvable_environment(pending) -> "int | None":
    """The production environment ID the current user can approve, or None."""
    if not isinstance(pending, list):
        return None
    for entry in pending:
        environment = (entry or {}).get("environment") or {}
        if environment.get("name") == ENVIRONMENT and entry.get("current_user_can_approve"):
            return environment.get("id")
    return None


def record_poll_result(state: dict, error: "str | None", toaster, runner) -> None:
    """Track consecutive failed ticks, and toast once when GitHub has been unreachable a while."""
    if error is None:
        if state.get("failed_polls"):
            log(f"GitHub reachable again after {state['failed_polls']} failed tick(s)")
        state["failed_polls"] = 0
        state["alerted"] = False
        state["last_ok"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return
    state["failed_polls"] = state.get("failed_polls", 0) + 1
    if state["failed_polls"] >= FAILED_POLL_ALERT and not state.get("alerted"):
        if toaster(toast_xml("Deploy notifier can't reach GitHub",
                             f"No waiting deploy will be shown until this clears. {error[:150]}"), runner):
            state["alerted"] = True


def tick(runner=run, toaster=show_toast) -> int:
    state = load_state()
    runs = state["runs"]
    watched = watched_repos()
    watched_lower = {r.lower() for r in watched}
    for key in [k for k, v in runs.items() if str(v.get("repo", "")).lower() not in watched_lower]:
        del runs[key]
        log(f"forgot {key} (repo no longer watched)")
    tick_error = None
    for repo in watched:
        listing, error = gh_json(runner, f"repos/{repo}/actions/runs?status=waiting&per_page=30")
        if listing is None or not isinstance(listing, dict):
            tick_error = error or f"unexpected listing for {repo}"
            continue  # Keep what we know about this repo; next tick retries.
        waiting = set()
        for run_info in listing.get("workflow_runs") or []:
            run_id = run_info.get("id")
            if not isinstance(run_id, int):
                continue
            key = run_key(repo, run_id)
            waiting.add(key)
            if key in runs:
                continue
            pending, error = gh_json(runner, f"repos/{repo}/actions/runs/{run_id}/pending_deployments")
            if pending is None:
                tick_error = error
                continue
            env_id = approvable_environment(pending)
            if env_id is None:
                continue
            nonce = secrets.token_urlsafe(16)
            if toaster(waiting_toast(repo, run_info, nonce), runner):
                runs[key] = {"repo": repo, "run_id": run_id, "environment_id": env_id,
                             "nonce": nonce, "notified_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                save_state(state)  # Now, so the toast's Approve works even if this tick dies later.
                log(f"toasted {key}")
        for key in [k for k, v in runs.items() if str(v.get("repo", "")).lower() == repo.lower()]:
            if key not in waiting:
                del runs[key]
                log(f"forgot {key} (no longer waiting)")
    record_poll_result(state, tick_error, toaster, runner)
    save_state(state)
    return 0


# --- Approve ----------------------------------------------------------------


def refuse(reason: str, url: "str | None", toaster, runner) -> int:
    log(f"approve refused: {reason}")
    toaster(toast_xml("Deploy not approved", reason, launch=url), runner)
    return 1


def approve(uri: str, runner=run, toaster=show_toast) -> int:
    match = _URI.match(uri.strip())
    if not match:
        return refuse("The Approve link was malformed.", None, toaster, runner)
    owner, name, run_id_text, nonce = match.groups()
    run_id = int(run_id_text)
    watched = {r.lower(): r for r in watched_repos()}
    repo = watched.get(f"{owner}/{name}".lower())
    if repo is None:
        return refuse(f"{owner}/{name} isn't a watched repo.", None, toaster, runner)
    url = run_url(repo, run_id)
    state = load_state()
    key = run_key(repo, run_id)
    record = state["runs"].get(key)
    if not record or not hmac.compare_digest(str(record.get("nonce", "")), nonce):
        return refuse("This button doesn't match a notification on record. Open the run to approve it.",
                      url, toaster, runner)
    pending, _ = gh_json(runner, f"repos/{repo}/actions/runs/{run_id}/pending_deployments")
    if pending is None:
        # Forget the run so the next tick shows a fresh notification if it's still waiting.
        del state["runs"][key]
        save_state(state)
        return refuse("Couldn't reach GitHub to check the run (see notifier.log). If it's still waiting, "
                      "a new notification follows within a few minutes, or open the run.", url, toaster, runner)
    env_id = approvable_environment(pending)
    if env_id is None:
        return refuse("The run is no longer waiting on production, or you can't approve it.",
                      url, toaster, runner)
    payload = json.dumps({"environment_ids": [env_id], "state": "approved", "comment": APPROVE_COMMENT})
    code, _, err = runner(["gh", "api", "-X", "POST",
                           f"repos/{repo}/actions/runs/{run_id}/pending_deployments", "--input", "-"],
                          input_text=payload)
    if code != 0:
        log(f"approve POST failed for {repo}#{run_id} rc={code}: {err.strip()[:300]}")
        toaster(toast_xml("Approval failed", f"GitHub refused the approval: {err.strip()[:150]}",
                          launch=url), runner)
        return 1
    log(f"approved {repo}#{run_id} environment {env_id}")
    toaster(toast_xml("Production deploy approved", f"{repo} run {run_id} is deploying.", launch=url), runner)
    return 0


# --- Status and test toast --------------------------------------------------


def status() -> int:
    print("Watched repos:", ", ".join(watched_repos()))
    print(f"Config: {config_path()}")
    state = load_state()
    print(f"Last good poll: {state.get('last_ok', 'never')}; failed ticks in a row: {state.get('failed_polls', 0)}")
    runs = state["runs"]
    print(f"Remembered runs ({len(runs)}):")
    for key, record in sorted(runs.items()):
        print(f"  {key}  environment {record.get('environment_id')}  toasted {record.get('notified_at')}")
    log_path = state_dir() / "notifier.log"
    print(f"Log: {log_path}")
    with contextlib.suppress(OSError):
        for line in log_path.read_text(encoding="utf-8").splitlines()[-15:]:
            print("  " + line)
    return 0


def test_toast(runner=run, toaster=show_toast) -> int:
    repo = watched_repos()[0]
    listing, error = gh_json(runner, f"repos/{repo}/actions/runs?per_page=1")
    if error is not None:
        print(f"Couldn't list runs for {repo}: {error}")
        return 1
    latest = (listing or {}).get("workflow_runs") or [] if isinstance(listing, dict) else []
    if not latest:
        print(f"No runs found for {repo}.")
        return 1
    ok = toaster(waiting_toast(repo, latest[0], "test-nonce-matches-nothing"), runner)
    print(f"Test toast for {run_url(repo, latest[0]['id'])}: {'shown' if ok else 'FAILED (see log)'}")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tick", action="store_true")
    group.add_argument("--approve", metavar="URI")
    group.add_argument("--status", action="store_true")
    group.add_argument("--test-toast", action="store_true")
    args = parser.parse_args(argv)
    if args.tick:
        return tick()
    if args.approve is not None:
        return approve(args.approve)
    if args.test_toast:
        return test_toast()
    return status()


def guarded_main(argv=None, entry=main, toaster=show_toast) -> int:
    """pyw has no console, so an uncaught error would vanish: log it, and tell an Approve click."""
    try:
        return entry(argv)
    except Exception:  # noqa: BLE001 -- last-resort report; there's no console to show it
        log("crash: " + traceback.format_exc().strip().replace("\n", " | "))
        if "--approve" in (argv if argv is not None else sys.argv):
            toaster(toast_xml("Deploy not approved", "The notifier hit an internal error (see notifier.log). "
                              "Open the run to approve it."))
        return 1


if __name__ == "__main__":
    sys.exit(guarded_main())
