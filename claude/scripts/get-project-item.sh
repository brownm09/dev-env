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

SCRATCH="${HOME}/.claude/scratch"
TMPFILE="${SCRATCH}/tmp_project_item_$$.json"

gh project item-list "$PROJECT_NUMBER" \
  --owner "$OWNER" \
  --format json \
  --limit 1000 > "$TMPFILE"

ITEM_ID=$(node -e "
  const d = JSON.parse(require('fs').readFileSync('$TMPFILE', 'utf8'));
  const item = d.items.find(i => i.content && i.content.number === $ISSUE_NUMBER);
  if (!item) { process.stderr.write('get-project-item: no item found for #$ISSUE_NUMBER\n'); process.exit(1); }
  console.log(item.id);
")
EXIT_CODE=$?
rm -f "$TMPFILE"

if [[ $EXIT_CODE -ne 0 ]]; then
  exit 1
fi

echo "$ITEM_ID"
