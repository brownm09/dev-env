# Windows Dev-Env Bootstrap

Paste everything below the line as your opening message in a fresh Claude Code
session on the new Windows machine. Claude will clone the repo and run the
setup script.

---

Your task is to bootstrap this Windows machine with the `brownm09/dev-env`
Claude Code configuration. Work through the steps below in order; report the
result of each before moving on.

## 1. Verify Git Bash is available

```bash
git --version && bash --version
```

If `git` is not found, stop and tell me to install Git for Windows from
https://git-scm.com/download/win, then open a new Git Bash window.

## 2. Clone and run setup

```bash
mkdir -p "$HOME/Git"
if [ -d "$HOME/Git/dev-env/.git" ]; then
  git -C "$HOME/Git/dev-env" pull
else
  git clone https://github.com/brownm09/dev-env.git "$HOME/Git/dev-env"
fi
bash "$HOME/Git/dev-env/setup.sh"
```

`setup.sh` will:
- Self-elevate via UAC if Administrator privileges are needed
- Warn about any missing soft prerequisites (python3, bash on PATH)
- Create all `~/.claude/` symlinks and junctions
- Set `core.hooksPath` globally
- Create `~/.claude/scratch/`

Read all output. Stop and surface any WARNING or error before continuing.

## 3. Verify

```bash
ls -la "$HOME/.claude/CLAUDE.md"
ls -la "$HOME/.claude/settings.json"
ls -la "$HOME/.claude/scripts/"
ls -la "$HOME/.claude/skills/"
ls -la "$HOME/.claude/hooks/"
ls -la "$HOME/.claude/scratch/"
ls -la "$HOME/bin/"
git config --global core.hooksPath
```

Report any missing file, broken link, or unexpected hooks path. Setup is
complete when all seven paths resolve and `core.hooksPath` points to
`~/.claude/hooks`.
