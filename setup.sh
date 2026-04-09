#!/usr/bin/env bash
# dev-env setup — run once on each new machine after cloning this repo.
# Creates symlinks from well-known config locations into this repo.
#
# Requires: Git Bash on Windows with Developer Mode enabled (for mklink without elevation).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Setting up dev-env from $REPO_DIR"

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

echo "Done."
