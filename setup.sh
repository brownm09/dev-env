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

echo "Done."
