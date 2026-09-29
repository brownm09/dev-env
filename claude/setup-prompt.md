# Windows Dev-Env Bootstrap

Paste everything below the line as your opening message in a fresh Claude Code
session on the new Windows machine. Claude will clone the repos, run the setup
script and verify the install. The full runbook — what stays per machine, and
how to use two machines on the same day — is `docs/REFERENCE.md` → "Adding a
Second Machine" ([dev-env#1107](https://github.com/brownm09/dev-env/issues/1107)).

Before pasting, do the parts that need you: enable Developer Mode (Settings >
System > For developers); install Git for Windows, Python with the py launcher,
the GitHub CLI and nvm for Windows; and sign in with `gh auth login`.

---

Your task is to bootstrap this Windows machine with the `brownm09/dev-env`
Claude Code configuration. Work through the steps below in order and report the
result of each before moving on. Do not register any scheduled routines.

## 1. Check the profile path and prerequisites

```bash
echo "$USERPROFILE"
git --version && py -3 --version && gh --version && node --version
gh auth status
```

If the profile path is not `C:\Users\brown`, stop and tell me: dev-env's hook
commands name `C:/Users/brown/...` and would block every prompt until
dev-env#1113 lands. If a tool is missing or gh is not signed in, stop and tell
me what to install or run.

## 2. Wire gh into git

```bash
gh auth setup-git
gh auth refresh -s project
```

`gh auth refresh` may open a browser — tell me if it needs me.

## 3. Clone and run setup

```bash
mkdir -p "$HOME/Git"
for repo in dev-env engineering-journal; do
  if [ -d "$HOME/Git/$repo/.git" ]; then
    git -C "$HOME/Git/$repo" pull --ff-only
  else
    git clone "https://github.com/brownm09/$repo.git" "$HOME/Git/$repo"
  fi
done
bash "$HOME/Git/dev-env/setup.sh"
```

`setup.sh` will:
- Stop before changing anything, with the fix, if this shell can't create symlinks
  (Developer Mode, the "Create symbolic links" right, or an elevated shell), if Git Bash's
  `HOME` isn't the Windows profile, or if a path contains one of cmd.exe's special characters
- Move anything already at a link location into `~/.claude/backups/setup-<timestamp>/`, and
  record there where any replaced link pointed, if it pointed outside dev-env
- Create the `~/.claude/` links (`CLAUDE.md`, `scripts`, `skills`, `hooks`, `templates`, `routines`) and `~/bin`
- Seed `~/.claude/settings.json` (not on a profile other than `C:\Users\brown` — see step 1) and
  set `core.hooksPath` globally, saving the previous values in the same backup directory
- Finish by running `dev-env-doctor.py`, which checks the prerequisites (`py`, `pyw`, `gh`
  and its sign-in, `node`, git identity), and exit non-zero while it reports a FAIL

Read all output. Surface every WARNING, FAIL, "Backed up" and "Replacing link" line before
continuing. `bash setup.sh --restore <that backup directory>` undoes the run.

## 4. Finish and verify

Ask me to copy `.claude/hook-config.json` from `~/Git/dev-env/` on my other
machine into the same path here (it holds project-board IDs). Then:

```bash
py -3 ~/.claude/scripts/dev-env-doctor.py
```

Setup is complete when the doctor reports no FAIL lines. Report each remaining
WARN with what would fix it. Don't change git security settings (such as
`safe.directory`) or clone further repos without asking me.
