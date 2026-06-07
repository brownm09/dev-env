#!/usr/bin/env bash
# get-project-item.sh — resolve a GitHub Project item ID from an issue/PR number
#
# Usage: get-project-item.sh <issue-or-pr-number> [project-number] [owner]
#
# Outputs the project item node ID to stdout so callers can capture it:
#   ITEM_ID=$(bash get-project-item.sh 42)
#
# Defaults: project 3, owner brownm09 (dev-env project board).
# Override via arguments or PROJECT_NUMBER / PROJECT_OWNER env vars.
#
# Prerequisites: gh CLI authenticated with the 'project' scope.
#   Add once if needed: gh auth refresh -s project

set -euo pipefail

ISSUE_NUMBER="${1:-}"
if [[ -z "$ISSUE_NUMBER" ]]; then
  echo "Usage: get-project-item.sh <issue-or-pr-number> [project-number] [owner]" >&2
  exit 1
fi

PROJECT_NUMBER="${2:-${PROJECT_NUMBER:-3}}"
OWNER="${3:-${PROJECT_OWNER:-brownm09}}"

# Literal Windows-style path (not "${HOME}/...") so Git Bash and Node resolve it
# identically. Under Git Bash $HOME is the MSYS path /c/Users/brown; Node on Windows
# resolves a leading /c/... against the current drive (-> C:\c\Users\...), so the
# redirect-write and the node-read would target different files (dev-env#334). This
# matches the repo convention (DEFAULT_LOG in session-mode-report.py).
SCRATCH="C:/Users/brown/.claude/scratch"
TMPFILE="${SCRATCH}/tmp_project_item_$$.json"
trap 'rm -f "$TMPFILE"' EXIT

gh project item-list "$PROJECT_NUMBER" \
  --owner "$OWNER" \
  --format json \
  --limit 1000 > "$TMPFILE"

# Under `set -e` a non-zero exit from node (e.g. no matching item) aborts the script
# with that status; the EXIT trap above removes the temp file either way. (The former
# `EXIT_CODE=$?` guard here was dead code — set -e aborted before it could run.)
ITEM_ID=$(node -e "
  const d = JSON.parse(require('fs').readFileSync('$TMPFILE', 'utf8'));
  const item = d.items.find(i => i.content && i.content.number === $ISSUE_NUMBER);
  if (!item) { process.stderr.write('get-project-item: no item found for #$ISSUE_NUMBER\n'); process.exit(1); }
  console.log(item.id);
")

echo "$ITEM_ID"
