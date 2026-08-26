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
# is a hit. The cache key includes the project board (owner + number), not just the
# repo/number -- this repo alone already reconciles more than one real board
# (dev-env #3, lifting-logbook #2), and a key that omitted the board could return
# one board's answer for a lookup meant for another (a /review finding on this PR;
# must stay byte-identical to _gh_project.py's _cache_key format).
#
# Prerequisites (fallback path only): gh CLI authenticated with the 'project' scope.
#   Add once if needed: gh auth refresh -s project

set -euo pipefail

ISSUE_NUMBER="${1:-}"
if [[ -z "$ISSUE_NUMBER" ]]; then
  echo "Usage: get-project-item.sh <issue-or-pr-number> [project-number] [owner]" >&2
  exit 1
fi
# Numeric-only: this value is later embedded in a `content.number === N` comparison
# (fallback path) and used to build the cache key. Rejecting non-numeric input here,
# rather than letting it reach node as an unquoted expression or a JS-string
# fragment, closes an injection-shaped hole a /review finding raised for this file's
# interpolated node snippets (also fixed below by passing values through
# process.env instead of string-interpolating them into the JS source).
if [[ ! "$ISSUE_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "get-project-item: <issue-or-pr-number> must be a positive integer, got: '$ISSUE_NUMBER'" >&2
  exit 1
fi

PROJECT_NUMBER="${2:-${PROJECT_NUMBER:-3}}"
OWNER="${3:-${PROJECT_OWNER:-brownm09}}"
REPO="${PROJECT_REPO:-dev-env}"
REPO_SLUG="${OWNER}/${REPO}"

# Literal Windows-style path (not "${HOME}/...") so Git Bash and Node resolve it
# identically. Under Git Bash $HOME is the MSYS path /c/Users/brown; Node on Windows
# resolves a leading /c/... against the current drive (-> C:\c\Users\...), so the
# redirect-write and the node-read would target different files (dev-env#334). This
# matches the repo convention (DEFAULT_LOG in session-mode-report.py) and must stay
# byte-identical to _gh_project.py's CACHE_PATH so both sides read/write the same
# file. PROJECT_ITEM_CACHE_PATH_OVERRIDE mirrors _gh_project.py's test-only override
# of the same name -- lets a test redirect the cache to a throwaway file instead of
# the real production cache. A test-supplied override may arrive as a Windows-style
# backslash path (e.g. Python's tempfile on this OS); normalize to forward slashes
# so it round-trips through a JS single-quoted-string-free process.env read exactly
# like the literal default does (a /review finding: an un-normalized backslash path
# reaching JS source text as a string literal is itself an escape-sequence hazard --
# one more reason every value below goes through process.env, not interpolation).
SCRATCH="C:/Users/brown/.claude/scratch"
CACHE_FILE="${PROJECT_ITEM_CACHE_PATH_OVERRIDE:-${SCRATCH}/project-item-cache.json}"
CACHE_FILE="${CACHE_FILE//'\'/'/'}"

# Cache key must match _gh_project.py's _cache_key(project_owner, project_number,
# repo, number) exactly: f"{project_owner.lower()}/{project_number}|{repo.lower()}#{number}".
OWNER_LOWER="${OWNER,,}"
REPO_SLUG_LOWER="${REPO_SLUG,,}"
CACHE_KEY="${OWNER_LOWER}/${PROJECT_NUMBER}|${REPO_SLUG_LOWER}#${ISSUE_NUMBER}"

# Cache check -- no `gh` call at all on a hit, so this succeeds even offline. Every
# value node needs travels via process.env, never interpolated into the JS source
# text (the injection-shaped hole noted above) -- a env-var name collision with
# something in the caller's environment is not a realistic concern for these
# GPI_-prefixed, script-local names. A miss (key absent, file missing, or corrupt
# JSON) exits 1 from node without printing anything; being the left side of `&&`
# exempts it from `set -e`'s abort (the well-established "cmd_that_might_fail &&
# only_on_success" idiom), so the script falls through to the existing fetch path
# below instead of aborting.
CACHED_ID=$(GPI_CACHE_FILE="$CACHE_FILE" GPI_CACHE_KEY="$CACHE_KEY" node -e '
  try {
    const cache = JSON.parse(require("fs").readFileSync(process.env.GPI_CACHE_FILE, "utf8"));
    const id = cache[process.env.GPI_CACHE_KEY];
    if (typeof id === "string" && id) { console.log(id); process.exit(0); }
  } catch (e) { /* no cache file yet, or corrupt JSON -- fall through to fetch */ }
  process.exit(1);
') && { echo "$CACHED_ID"; exit 0; }

TMPFILE="${SCRATCH}/tmp_project_item_$$.json"
trap 'rm -f "$TMPFILE"' EXIT

gh project item-list "$PROJECT_NUMBER" \
  --owner "$OWNER" \
  --format json \
  --limit 1000 > "$TMPFILE"

# Under `set -e` a non-zero exit from node (e.g. no matching item) aborts the script
# with that status; the EXIT trap above removes the temp file either way. Filtered
# by content.repository === REPO_SLUG before accepting a match -- an unfiltered
# number-only match can return a DIFFERENT repo's item on a board that carries more
# than one (this board already does: dev-env #3 also lists lifting-logbook's items
# under --scan-dir reconciliation), which would both print the wrong ID here and,
# via the write-back below, poison the shared cache for every later reader (a
# /review finding on this PR -- the same class of bug post-pr-merge-project.py's
# fallback scan needed fixing for).
ITEM_ID=$(GPI_TMPFILE="$TMPFILE" GPI_ISSUE_NUMBER="$ISSUE_NUMBER" GPI_REPO_SLUG="$REPO_SLUG" node -e '
  const d = JSON.parse(require("fs").readFileSync(process.env.GPI_TMPFILE, "utf8"));
  const wantNumber = Number(process.env.GPI_ISSUE_NUMBER);
  const wantRepo = process.env.GPI_REPO_SLUG;
  const item = d.items.find(i => i.content
    && i.content.number === wantNumber
    && i.content.repository === wantRepo);
  if (!item) {
    process.stderr.write("get-project-item: no item found for #" + wantNumber + " in " + wantRepo + "\n");
    process.exit(1);
  }
  console.log(item.id);
')

# Best-effort: populate the cache so the next lookup for this item is a hit. Same
# atomic tmp-file + rename idiom as _gh_project.py's write_item_cache_entry() --
# including cleaning up the tmp file on a failed write, not just a successful one,
# so a rare mid-write failure can't leave an orphaned `*.<pid>.tmp` that nothing
# ever revisits (a /review finding; sweep-scratch-debris.py's KNOWN_PATTERNS now
# also names this file family so a leaked one is swept regardless). Never allowed
# to fail the script -- `|| true` -- a cache-write failure must not turn a
# successful lookup into an error.
GPI_CACHE_FILE="$CACHE_FILE" GPI_CACHE_KEY="$CACHE_KEY" GPI_ITEM_ID="$ITEM_ID" node -e '
  const fs = require("fs"), path = require("path");
  const cacheFile = process.env.GPI_CACHE_FILE;
  let cache = {};
  try { cache = JSON.parse(fs.readFileSync(cacheFile, "utf8")); } catch (e) { /* start fresh */ }
  cache[process.env.GPI_CACHE_KEY] = process.env.GPI_ITEM_ID;
  fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
  const tmp = cacheFile + "." + process.pid + ".tmp";
  try {
    fs.writeFileSync(tmp, JSON.stringify(cache, null, 2), "utf8");
    fs.renameSync(tmp, cacheFile);
  } catch (e) {
    try { fs.unlinkSync(tmp); } catch (e2) { /* nothing left to clean up */ }
  }
' || true

echo "$ITEM_ID"
