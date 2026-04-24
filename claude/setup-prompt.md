# Windows Dev-Env Bootstrap

Paste everything below the line as your opening message in a fresh Claude Code
session on the new Windows machine. Claude will walk through each step and
report results before moving on.

---

Your task is to bootstrap this Windows machine with the `brownm09/dev-env`
Claude Code configuration. Work through the steps below in order; report the
output of each step before moving to the next. Stop and surface any error
rather than continuing past it.

## 1. Check prerequisites

Run each command in Git Bash and report the output:

```bash
git --version
python3 --version
node --version
bash --version
```

If `python3` is not found: tell me to install Python 3 from https://python.org/downloads (tick "Add python.exe to PATH") and re-run `python3 --version` after.

If `git` / `bash` are not found: tell me to install Git for Windows from https://git-scm.com/download/win and open a new Git Bash window.

Do not proceed past step 1 until `git`, `python3`, and `bash` are all present.

## 2. Check Developer Mode

Symlinks (`mklink`) require either Developer Mode or an elevated shell.
Check whether Developer Mode is on:

```bash
reg.exe query "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock" /v AllowDevelopmentWithoutDevLicense 2>/dev/null || echo "key not found"
```

If the value is not `0x1`: warn me and remind me to enable Developer Mode in
Windows Settings → System → For Developers, then re-run this check before
continuing. Alternatively, confirm that Git Bash is running as Administrator.

## 3. Clone the repo

```bash
mkdir -p "$HOME/Git"
if [ -d "$HOME/Git/dev-env/.git" ]; then
  echo "Repo already present — pulling latest"
  git -C "$HOME/Git/dev-env" pull
else
  git clone https://github.com/brownm09/dev-env.git "$HOME/Git/dev-env"
fi
```

## 4. Run setup.sh

```bash
bash "$HOME/Git/dev-env/setup.sh"
```

Read all output carefully. Stop and report any WARNING lines before continuing.

## 5. Configure global git hooks path

Check for conflicts, then set:

```bash
SYSTEM_HOOKS="$(git config --system core.hooksPath 2>/dev/null || true)"
GLOBAL_HOOKS="$(git config --global core.hooksPath 2>/dev/null || true)"

echo "system hooks: ${SYSTEM_HOOKS:-<not set>}"
echo "global hooks: ${GLOBAL_HOOKS:-<not set>}"
```

If `system hooks` is set to something other than `~/.claude/hooks`: stop and
tell me — an enterprise policy may own it and overriding it could break things.

Otherwise, set the global hooks path:

```bash
git config --global core.hooksPath "$HOME/.claude/hooks"
git config --global core.hooksPath  # confirm
```

## 6. Verify

```bash
ls -la "$HOME/.claude/CLAUDE.md"
ls -la "$HOME/.claude/settings.json"
ls -la "$HOME/.claude/scripts/"
ls -la "$HOME/.claude/skills/"
ls -la "$HOME/.claude/hooks/"
ls -la "$HOME/.claude/scratch/"
ls -la "$HOME/bin/"
```

Report any missing file or broken link. Setup is complete when all seven paths
resolve without error.
