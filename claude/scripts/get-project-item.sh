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
# even when `gh` is offline/unauthenticated). On a miss it asks GitHub for that one
# issue or PR's own board items (1 GraphQL point, against about 1,011 for a full
# `gh project item-list` of the 900-item board, dev-env#1246) and writes the result
# back into the cache so the next lookup for the same item is a hit. The cache key
# includes the project board (owner + number), not just the
# repo/number -- this repo alone already reconciles more than one real board
# (dev-env #3, lifting-logbook #2), and a key that omitted the board could return
# one board's answer for a lookup meant for another (a /review finding on this PR;
# must stay byte-identical to _gh_project.py's _cache_key format).
#
# Exit status: 0 printed the ID; 1 no such item on that board (or bad usage); 2 the
# lookup itself failed, for example on a rate limit, with gh's own message on stderr.
# gh exits 1 for nearly every failure, so this can't pass gh's code through.
#
# Prerequisites (fallback path only): gh CLI authenticated with the 'project' scope.
#   Add once if needed: gh auth refresh -s project

set -euo pipefail

ISSUE_NUMBER="${1:-}"
if [[ -z "$ISSUE_NUMBER" ]]; then
  echo "Usage: get-project-item.sh <issue-or-pr-number> [project-number] [owner]" >&2
  exit 1
fi
# Numeric-only: this value is later passed as a GraphQL Int (fallback path) and used
# to build the cache key. Rejecting non-numeric input here keeps anything else out of
# both, and every value node needs goes through process.env instead of being
# string-interpolated into the JS source (a /review finding on this file).
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
# two would target different files (dev-env#334). This
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

# Keep in step with post-pr-merge-project.py's _ITEM_LOOKUP_QUERY. includeArchived is
# false, against GitHub's default of true, because an archived item isn't one a caller
# should be moving. gh exits 1 on any GraphQL error but still prints the response body,
# so the exit code is kept for node to weigh rather than aborting here under `set -e`.
GH_RC=0
GH_RESPONSE=$(gh api graphql \
  -f query='query($owner: String!, $repo: String!, $number: Int!) {
    repository(owner: $owner, name: $repo) {
      issueOrPullRequest(number: $number) {
        ... on Issue { projectItems(first: 20, includeArchived: false) { nodes { id project { number owner { ... on User { login } ... on Organization { login } } } } } }
        ... on PullRequest { projectItems(first: 20, includeArchived: false) { nodes { id project { number owner { ... on User { login } ... on Organization { login } } } } } }
      }
    }
  }' \
  -f owner="$OWNER" \
  -f repo="$REPO" \
  -F number="$ISSUE_NUMBER") || GH_RC=$?

# Under `set -eo pipefail` a non-zero exit from node aborts the script with that status.
# The response reaches node on stdin, not in an env var, where Git Bash's path conversion
# could rewrite it. The wanted item is looked for first, because a response can carry
# it alongside an error about another board the token can't read. Without it, only a
# NOT_FOUND response means "no such item": any other gh failure (rate limit, auth,
# offline) exits 2 and leaves gh's own stderr as the diagnosis, so a caller can't mistake
# a failed lookup for an item that isn't there. The query is scoped to one repo, but an
# issue can sit on several boards, and a board number alone is ambiguous (brownm09 and
# merickvaughn each have a board 2), so a match needs both number and owner -- accepting
# any other would print the wrong ID and, via the write-back below, poison the shared
# cache for every later reader.
ITEM_ID=$(printf '%s' "$GH_RESPONSE" | GPI_GH_RC="$GH_RC" GPI_ISSUE_NUMBER="$ISSUE_NUMBER" GPI_REPO_SLUG="$REPO_SLUG" GPI_PROJECT_NUMBER="$PROJECT_NUMBER" GPI_OWNER_LOWER="$OWNER_LOWER" node -e '
  let d = {};
  try { d = JSON.parse(require("fs").readFileSync(0, "utf8")); } catch (e) { /* gh printed no JSON */ }
  const repository = d.data && d.data.repository;
  const target = repository && repository.issueOrPullRequest;
  const nodes = (target && target.projectItems && target.projectItems.nodes) || [];
  const wantProject = Number(process.env.GPI_PROJECT_NUMBER);
  const item = nodes.find(n => n && n.project
    && n.project.number === wantProject
    && n.project.owner
    && (n.project.owner.login || "").toLowerCase() === process.env.GPI_OWNER_LOWER);
  if (!item) {
    const errors = d.errors || [];
    const notFound = errors.length > 0 && errors.every(e => e.type === "NOT_FOUND");
    if (Number(process.env.GPI_GH_RC) !== 0 && !notFound) process.exit(2);
    process.stderr.write("get-project-item: no item found for #" + process.env.GPI_ISSUE_NUMBER
      + " in " + process.env.GPI_REPO_SLUG
      + " on board " + process.env.GPI_OWNER_LOWER + "/" + process.env.GPI_PROJECT_NUMBER + "\n");
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
