#!/usr/bin/env bash
# dev-env setup — run once on each new machine after cloning this repo.
# Creates symlinks from well-known config locations into this repo.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Setting up dev-env from $REPO_DIR"

# Claude Code global config
mkdir -p "$HOME/.claude"
ln -sf "$REPO_DIR/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
echo "  Linked claude/CLAUDE.md -> ~/.claude/CLAUDE.md"

echo "Done."
