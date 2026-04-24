#!/usr/bin/env bash
# dev-env setup — run once on each new machine after cloning this repo.
# Creates symlinks from well-known config locations into this repo.
#
# Requirements (Windows):
#   - Git for Windows (Git Bash)
#   - Developer Mode enabled in Windows Settings → System → For Developers
#     (allows mklink without elevation; alternative: run as Administrator)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Setting up dev-env from $REPO_DIR"
echo ""

# ---------------------------------------------------------------------------
# Prerequisite: bash.exe on Windows PATH
# Claude Code hook commands use "bash -c '...'" so that python3 (a Windows App
# Execution Alias symlink) resolves correctly regardless of which process spawns
# the hook. Verify that bash.exe is on the Windows PATH so non-interactive
# processes (like the Claude Code Desktop hook runner) can find it.
# ---------------------------------------------------------------------------
BASH_PATH="$(cmd.exe /c "where bash 2>NUL" 2>/dev/null | tr -d '\r' | head -1)"
if [ -z "$BASH_PATH" ]; then
  echo "WARNING: bash.exe is not on the Windows PATH."
  echo "  Claude Code hooks use 'bash -c ...' to invoke Python scripts."
  echo "  Add Git Bash to your Windows PATH, e.g.:"
  echo "    C:\\Program Files\\Git\\usr\\bin"
  echo "  Then re-run this script."
  echo ""
else
  echo "  bash found at: $BASH_PATH"
fi

# ---------------------------------------------------------------------------
# Prerequisite: python3
# Required by every hook script in claude/scripts/.
# ---------------------------------------------------------------------------
if ! python3 --version &>/dev/null; then
  echo ""
  echo "WARNING: python3 not found."
  echo "  Install Python 3 from https://python.org/downloads/ and ensure it is"
  echo "  on your PATH. Then re-run this script."
  echo ""
else
  echo "  python3 found: $(python3 --version 2>&1)"
fi

# ---------------------------------------------------------------------------
# Prerequisite: Developer Mode or Administrator
# mklink without /J requires Developer Mode or elevation.
# ---------------------------------------------------------------------------
DEV_MODE_KEY="HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced"
DEV_MODE_VAL="$(reg.exe query "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock" /v AllowDevelopmentWithoutDevLicense 2>/dev/null | grep -oP '0x\w+' || true)"
if [ "$DEV_MODE_VAL" != "0x1" ]; then
  IS_ADMIN="$(net.exe session 2>/dev/null && echo yes || echo no)"
  if [ "$IS_ADMIN" != "yes" ]; then
    echo ""
    echo "WARNING: Developer Mode is not enabled and this session is not elevated."
    echo "  Symlinks (mklink) require one of:"
    echo "    - Windows Settings → System → For Developers → Developer Mode ON"
    echo "    - Running Git Bash as Administrator"
    echo "  Directory junctions (mklink /J, /D) are not affected."
    echo "  CLAUDE.md and settings.json links may fail. Enable Developer Mode and re-run."
    echo ""
  fi
fi

echo ""
echo "Creating ~/.claude layout..."

# Claude Code global config dir
mkdir -p "$HOME/.claude"

# ---------------------------------------------------------------------------
# CLAUDE.md (file symlink)
# ---------------------------------------------------------------------------
WIN_TARGET="$(cygpath -w "$REPO_DIR/claude/CLAUDE.md")"
WIN_LINK="$(cygpath -w "$HOME/.claude/CLAUDE.md")"

rm -f "$HOME/.claude/CLAUDE.md"
cmd.exe /c "mklink \"$WIN_LINK\" \"$WIN_TARGET\""
echo "  Linked claude/CLAUDE.md -> ~/.claude/CLAUDE.md"

# ---------------------------------------------------------------------------
# settings.json (file symlink)
# ---------------------------------------------------------------------------
WIN_SETTINGS_TARGET="$(cygpath -w "$REPO_DIR/claude/settings.json")"
WIN_SETTINGS_LINK="$(cygpath -w "$HOME/.claude/settings.json")"

rm -f "$HOME/.claude/settings.json"
cmd.exe /c "mklink \"$WIN_SETTINGS_LINK\" \"$WIN_SETTINGS_TARGET\""
echo "  Linked claude/settings.json -> ~/.claude/settings.json"

# ---------------------------------------------------------------------------
# scripts/ (directory junction)
# ---------------------------------------------------------------------------
WIN_SCRIPTS_TARGET="$(cygpath -w "$REPO_DIR/claude/scripts")"
WIN_SCRIPTS_LINK="$(cygpath -w "$HOME/.claude/scripts")"

if [ -L "$HOME/.claude/scripts" ] || [ -d "$HOME/.claude/scripts" ]; then
  cmd.exe /c "rmdir \"$WIN_SCRIPTS_LINK\"" 2>/dev/null || rm -rf "$HOME/.claude/scripts"
fi
cmd.exe /c "mklink /D \"$WIN_SCRIPTS_LINK\" \"$WIN_SCRIPTS_TARGET\""
echo "  Linked claude/scripts/ -> ~/.claude/scripts/"

# ---------------------------------------------------------------------------
# skills/ (directory junction)
# ---------------------------------------------------------------------------
WIN_SKILLS_TARGET="$(cygpath -w "$REPO_DIR/claude/skills")"
WIN_SKILLS_LINK="$(cygpath -w "$HOME/.claude/skills")"

if [ -L "$HOME/.claude/skills" ] || [ -d "$HOME/.claude/skills" ]; then
  cmd.exe /c "rmdir \"$WIN_SKILLS_LINK\"" 2>/dev/null || rm -rf "$HOME/.claude/skills"
fi
cmd.exe /c "mklink /D \"$WIN_SKILLS_LINK\" \"$WIN_SKILLS_TARGET\""
echo "  Linked claude/skills/ -> ~/.claude/skills/"

# ---------------------------------------------------------------------------
# hooks/ (directory junction)
# ---------------------------------------------------------------------------
WIN_HOOKS_TARGET="$(cygpath -w "$REPO_DIR/claude/hooks")"
WIN_HOOKS_LINK="$(cygpath -w "$HOME/.claude/hooks")"

if [ -L "$HOME/.claude/hooks" ] || [ -d "$HOME/.claude/hooks" ]; then
  cmd.exe /c "rmdir \"$WIN_HOOKS_LINK\"" 2>/dev/null || rm -rf "$HOME/.claude/hooks"
fi
cmd.exe /c "mklink /D \"$WIN_HOOKS_LINK\" \"$WIN_HOOKS_TARGET\""
echo "  Linked claude/hooks/ -> ~/.claude/hooks/"

# ---------------------------------------------------------------------------
# scratch/ (machine-local temp dir; not version-controlled)
# ---------------------------------------------------------------------------
mkdir -p "$HOME/.claude/scratch"
echo "  Created ~/.claude/scratch/"

# ---------------------------------------------------------------------------
# ~/bin (directory junction to dev-env/bin/)
# ---------------------------------------------------------------------------
WIN_BIN_TARGET="$(cygpath -w "$REPO_DIR/bin")"
WIN_BIN_LINK="$(cygpath -w "$HOME/bin")"

if [ -L "$HOME/bin" ] || [ -d "$HOME/bin" ]; then
  cmd.exe /c "rmdir \"$WIN_BIN_LINK\"" 2>/dev/null || rm -rf "$HOME/bin"
fi
cmd.exe /c "mklink /J \"$WIN_BIN_LINK\" \"$WIN_BIN_TARGET\""
echo "  Linked bin/ -> ~/bin/"

echo ""
echo "Done. Next steps if not already complete:"
echo "  1. Set global git hooks path:"
echo "       git config --global core.hooksPath ~/.claude/hooks"
echo "  2. Reload your shell (or open a new Git Bash window) so ~/bin is on PATH."
