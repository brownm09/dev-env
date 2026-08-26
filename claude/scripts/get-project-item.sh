#!/usr/bin/env bash
# get-project-item.sh — resolve a GitHub Project item ID from an issue/PR number
#
# Usage: get-project-item.sh <issue-or-pr-number> [project-number] [owner]
#
# Outputs the project item node ID to stdout so callers can capture it:
#   ITEM_ID=$(bash get-project-item.sh 42)
#
# Defaults: project 3, owner brownm09, repo dev-env (dev-env project board).
# Override project/owner via arguments or PROJECT_NUMBER / PROJECT_OWNER env vars;
# override repo via the PROJECT_REPO env var (no positional arg for it -- this board
# is dev-env-only in practice, so a 4th positional would be unused API surface).
#
# Checks a local item-ID cache first (dev-env#1057, ADR-141) -- populated by
# _gh_project.py's add_to_project() at creation time and backfilled wholesale by
# reconcile-project-board.py's sweep, so a cache hit costs zero `gh` calls (works
# even when `gh` is offline/unauthenticated). Falls back to the original full
# `gh project item-list --limit 1000` fetch-and-scan on a miss, exactly as before,
# and writes the result back into the cache so the next lookup for the same item
# is a hit.
#
# Prerequisites (fallback path only): gh CLI authenticated with the 'project' scope.
#   Add once if needed: gh auth refresh -s project

set -euo pipefail

ISSUE_NUMBER="${1:-}"
if [[ -z "$ISSUE_NUMBER" ]]; then
  echo "Usage: get-project-item.sh <issue-or-pr-number> [project-number] [owner]" >&2
  exit 1
fi

PROJECT_NUMBER="${2:-${PROJECT_NUMBER:-3}}"
OWNER="${3:-${PROJECT_OWNER:-brownm09}}"
REPO="${PROJECT_REPO:-dev-env}"

# Literal Windows-style path (not "${HOME}/...") so Git Bash and Node resolve it
# identically. Under Git Bash $HOME is the MSYS path /c/Users/brown; Node on Windows
# resolves a leading /c/... against the current drive (-> C:\c\Users\...), so the
# redirect-write and the node-read would target different files (dev-env#334). This
# matches the repo convention (DEFAULT_LOG in session-mode-report.py) and must stay
# byte-identical to _gh_project.py's CACHE_PATH so both sides read/write the same file.
SCRATCH="C:/Users/brown/.claude/scratch"
# PROJECT_ITEM_CACHE_PATH_OVERRIDE mirrors _gh_project.py's test-only override of the
# same name -- lets a test redirect the cache to a throwaway file instead of the real
# production cache. Unset in every real invocation, so the default is unchanged.
CACHE_FILE="${PROJECT_ITEM_CACHE_PATH_OVERRIDE:-${SCRATCH}/project-item-cache.json}"

# Cache check -- no `gh` call at all on a hit, so this succeeds even offline. A miss
# (key absent, file missing, or corrupt JSON) exits 1 from node without printing
# anything; being the left side of `&&` exempts it from `set -e`'s abort (the
# well-established "cmd_that_might_fail && only_on_success" idiom), so the script
# falls through to the existing fetch path below instead of aborting.
CACHED_ID=$(node -e "
  try {
    const cache = JSON.parse(require('fs').readFileSync('$CACHE_FILE', 'utf8'));
    const id = cache['$OWNER/$REPO#$ISSUE_NUMBER'];
    if (id) { console.log(id); process.exit(0); }
  } catch (e) { /* no cache file yet, or corrupt JSON -- fall through to fetch */ }
  process.exit(1);
") && { echo "$CACHED_ID"; exit 0; }

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

# Best-effort: populate the cache so the next lookup for this item is a hit. Same
# atomic tmp-file + rename idiom as _gh_project.py's write_item_cache_entry(). Never
# allowed to fail the script -- `|| true` -- a cache-write failure must not turn a
# successful lookup into an error.
node -e "
  const fs = require('fs');
  const path = require('path');
  const cacheFile = '$CACHE_FILE';
  let cache = {};
  try { cache = JSON.parse(fs.readFileSync(cacheFile, 'utf8')); } catch (e) { /* start fresh */ }
  cache['$OWNER/$REPO#$ISSUE_NUMBER'] = '$ITEM_ID';
  fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
  const tmp = cacheFile + '.' + process.pid + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cache, null, 2), 'utf8');
  fs.renameSync(tmp, cacheFile);
" || true

echo "$ITEM_ID"
