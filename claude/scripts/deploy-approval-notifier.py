#!/usr/bin/env python3
"""Windows notification for GitHub Actions runs waiting on a production
environment approval, with a one-click Approve button. See ADR-163 and
dev-env#1380.

Purpose: scheduled-task payload that shows one toast per run waiting on a
`production` environment the signed-in `gh` user can approve, and the handler
for that toast's Approve button.

  --tick          Poll each watched repo over REST, toast new waiting runs,
                  and forget runs that stopped waiting.
  --approve URI   Handle deploy-approve:<owner>/<repo>/<run-id>/<nonce>. Approves
                  only when the repo is watched, the nonce matches the toast this
                  script showed, and the run still waits on production.
  --status        Print watched repos, remembered runs and the recent log.
                  Changes nothing.
  --test-toast    Toast the latest run of the first watched repo with a nonce
                  that matches nothing, so Approve exercises the URL scheme and
                  ends in a "refused" toast without approving anything.

Registered by register-deploy-approval-notifier.ps1 (a scheduled task for
--tick and the `deploy-approve` URL scheme for --approve). Watched repos come
from ~/.claude/deploy-approval-notifier.json ({"repos": ["owner/name"]}),
defaulting to DEFAULT_REPOS. State and notifier.log live in
~/.claude/scratch/deploy-approval-notifier/.
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
RUN_FAILED = -1

_URI = re.compile(
    r"^deploy-approve:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(\d+)/([A-Za-z0-9_-]+)/?$"
)

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
    return Path(override) if override else Path.home() / ".claude" / "scratch" / "deploy-approval-notifier"


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
        timeout: float = 60) -> "tuple[int, str, str]":
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
    try:
        data = json.loads((state_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"runs": {}}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
        return {"runs": {}}
    return data


def save_state(state: dict) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    path = state_dir() / "state.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def gh_json(runner, path: str):
    """GET a REST path through `gh api`. Returns parsed JSON, or None on failure."""
    code, out, err = runner(["gh", "api", path])
    if code != 0:
        log(f"gh api {path} failed rc={code}: {err.strip()[:300]}")
        return None
    try:
        return json.loads(out)
    except ValueError:
        log(f"gh api {path} returned non-JSON")
        return None


def run_key(repo: str, run_id: int) -> str:
    return f"{repo.lower()}#{run_id}"


def run_url(repo: str, run_id: int) -> str:
    return f"https://github.com/{repo}/actions/runs/{run_id}"


# --- Toasts -----------------------------------------------------------------


def toast_xml(title: str, body: str, launch: "str | None" = None,
              actions: "list[tuple[str, str]] | None" = None, reminder: bool = False) -> str:
    """Build toast XML. `actions` are (label, protocol URI) buttons."""
    attrs = ""
    if launch:
        attrs += f" activationType=\"protocol\" launch={quoteattr(launch)}"
    if reminder:
        attrs += " scenario=\"reminder\""
    parts = [f"<toast{attrs}>", "<visual><binding template=\"ToastGeneric\">",
             f"<text>{escape(title)}</text>", f"<text>{escape(body)}</text>",
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


def tick(runner=run, toaster=show_toast) -> int:
    state = load_state()
    runs = state["runs"]
    shown = 0
    for repo in watched_repos():
        listing = gh_json(runner, f"repos/{repo}/actions/runs?status=waiting&per_page=30")
        if listing is None:
            continue  # Keep what we know about this repo; next tick retries.
        waiting = set()
        for run_info in listing.get("workflow_runs") or []:
            run_id = run_info.get("id")
            key = run_key(repo, run_id)
            waiting.add(key)
            if key in runs:
                continue
            pending = gh_json(runner, f"repos/{repo}/actions/runs/{run_id}/pending_deployments")
            env_id = approvable_environment(pending)
            if env_id is None:
                continue
            nonce = secrets.token_urlsafe(16)
            if toaster(waiting_toast(repo, run_info, nonce), runner):
                runs[key] = {"repo": repo, "run_id": run_id, "environment_id": env_id,
                             "nonce": nonce, "notified_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                shown += 1
                log(f"toasted {key}")
        for key in [k for k, v in runs.items() if v.get("repo", "").lower() == repo.lower()]:
            if key not in waiting:
                del runs[key]
                log(f"forgot {key} (no longer waiting)")
    save_state(state)
    log(f"tick ok: {shown} new")
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
    record = load_state()["runs"].get(run_key(repo, run_id))
    if not record or not hmac.compare_digest(str(record.get("nonce", "")), nonce):
        return refuse("This button doesn't match a notification on record. Open the run to approve it.",
                      url, toaster, runner)
    pending = gh_json(runner, f"repos/{repo}/actions/runs/{run_id}/pending_deployments")
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
    runs = load_state()["runs"]
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
    listing = gh_json(runner, f"repos/{repo}/actions/runs?per_page=1")
    latest = (listing or {}).get("workflow_runs") or []
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


if __name__ == "__main__":
    sys.exit(main())
