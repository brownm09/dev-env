#!/usr/bin/env bash
# dev-env setup — run once on each new machine after cloning this repo.
# Creates symlinks from well-known config locations into this repo.
#
# Requires: Git Bash on Windows with Developer Mode enabled (for mklink without elevation).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Setting up dev-env from $REPO_DIR"

# Claude Code hook commands use "bash -c '...'" so that python3 (a Windows App
# Execution Alias symlink) resolves correctly regardless of which process spawns
# the hook. Verify that bash.exe is on the Windows PATH so non-interactive
# processes (like the Claude Code Desktop hook runner) can find it.
BASH_PATH="$(cmd.exe /c "where bash 2>NUL" 2>/dev/null | tr -d '\r' | head -1)"
if [ -z "$BASH_PATH" ]; then
  echo ""
  echo "WARNING: bash.exe is not on the Windows PATH."
  echo "  Claude Code hooks use 'bash -c ...' to invoke Python scripts."
  echo "  Add Git Bash to your Windows PATH, e.g.:"
  echo "    C:\\Program Files\\Git\\usr\\bin"
  echo "  Then re-run this script."
  echo ""
else
  echo "  bash found at: $BASH_PATH"
fi

# Claude Code global config
mkdir -p "$HOME/.claude"

# Git Bash ln -sf creates a copy, not a native Windows symlink, unless
# MSYS=winsymlinks:nativestrict is set. Use cmd.exe mklink instead.
WIN_TARGET="$(cygpath -w "$REPO_DIR/claude/CLAUDE.md")"
WIN_LINK="$(cygpath -w "$HOME/.claude/CLAUDE.md")"

rm -f "$HOME/.claude/CLAUDE.md"
cmd.exe /c "mklink \"$WIN_LINK\" \"$WIN_TARGET\""
echo "  Linked claude/CLAUDE.md -> ~/.claude/CLAUDE.md"

# Claude Code scripts — symlink the whole directory
WIN_SCRIPTS_TARGET="$(cygpath -w "$REPO_DIR/claude/scripts")"
WIN_SCRIPTS_LINK="$(cygpath -w "$HOME/.claude/scripts")"

# Remove existing directory or symlink before creating new one
if [ -L "$HOME/.claude/scripts" ] || [ -d "$HOME/.claude/scripts" ]; then
  cmd.exe /c "rmdir \"$WIN_SCRIPTS_LINK\"" 2>/dev/null || rm -rf "$HOME/.claude/scripts"
fi
cmd.exe /c "mklink /D \"$WIN_SCRIPTS_LINK\" \"$WIN_SCRIPTS_TARGET\""
echo "  Linked claude/scripts/ -> ~/.claude/scripts/"

# Claude Code skills — symlink the whole directory
WIN_SKILLS_TARGET="$(cygpath -w "$REPO_DIR/claude/skills")"
WIN_SKILLS_LINK="$(cygpath -w "$HOME/.claude/skills")"

if [ -L "$HOME/.claude/skills" ] || [ -d "$HOME/.claude/skills" ]; then
  cmd.exe /c "rmdir \"$WIN_SKILLS_LINK\"" 2>/dev/null || rm -rf "$HOME/.claude/skills"
fi
cmd.exe /c "mklink /D \"$WIN_SKILLS_LINK\" \"$WIN_SKILLS_TARGET\""
echo "  Linked claude/skills/ -> ~/.claude/skills/"

# Claude Code global settings
WIN_SETTINGS_TARGET="$(cygpath -w "$REPO_DIR/claude/settings.json")"
WIN_SETTINGS_LINK="$(cygpath -w "$HOME/.claude/settings.json")"

rm -f "$HOME/.claude/settings.json"
cmd.exe /c "mklink \"$WIN_SETTINGS_LINK\" \"$WIN_SETTINGS_TARGET\""
echo "  Linked claude/settings.json -> ~/.claude/settings.json"

# ~/bin — personal CLI wrappers (junction to dev-env/bin/)
WIN_BIN_TARGET="$(cygpath -w "$REPO_DIR/bin")"
WIN_BIN_LINK="$(cygpath -w "$HOME/bin")"

if [ -L "$HOME/bin" ] || [ -d "$HOME/bin" ]; then
  cmd.exe /c "rmdir \"$WIN_BIN_LINK\"" 2>/dev/null || rm -rf "$HOME/bin"
fi
cmd.exe /c "mklink /J \"$WIN_BIN_LINK\" \"$WIN_BIN_TARGET\""
echo "  Linked bin/ -> ~/bin/"

echo "Done."
