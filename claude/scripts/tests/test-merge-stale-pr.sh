#!/usr/bin/env bash
# Self-test for claude/scripts/merge-stale-pr.sh (dev-env#463).
#
# Drives the REAL script against throwaway fixture repos (a bare "origin" +
# a working clone standing in for the shared engineering-journal checkout),
# with `gh` stubbed so no network/auth is ever touched. Focus: the Step 4
# orphaned-draft-file commit uses an explicit pathspec on both `git add` and
# `git commit` (dev-env#461) so it can never sweep in unrelated content
# already staged by a concurrent session in the same shared checkout
# (docs/adr/056-per-session-sharding-journal-companion-files.md -> Addendum).
#
# Note on scenario 1: once a concurrent session's file is left staged
# (uncommitted) after the Step 4 commit, the *following* `git rebase
# origin/main` in Step 5 will always refuse to run ("Your index contains
# uncommitted changes") — that is a plain git precondition, independent of
# whether Step 4's commit was scoped correctly. So scenario 1 intentionally
# does not assert the script's overall exit code; it asserts the git state
# left behind by Step 4 directly, which is what dev-env#463 asked for.
#
# Portable to Git Bash on Windows and Linux CI. Run from anywhere:
#   bash claude/scripts/tests/test-merge-stale-pr.sh

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$SCRIPT_DIR/../merge-stale-pr.sh"
PR_NUMBER="777"

PASS=0
FAIL=0
ok()  { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

echo "Testing $SCRIPT"
[ -f "$SCRIPT" ] || { echo "FATAL: script not found at $SCRIPT"; exit 1; }

# Write a stub `gh` into a fresh temp dir (OUTSIDE any repo) that answers
# `pr view ... --json headRefName --jq '.headRefName'` with $TEST_GH_BRANCH
# and no-ops `pr merge`. Every invocation is appended to $TEST_GH_LOG so
# tests can assert which gh subcommands actually ran. Echoes the bin dir;
# caller removes its parent.
make_stub_gh() {
  local bindir
  bindir="$(mktemp -d)/bin"
  mkdir -p "$bindir"
  cat > "$bindir/gh" <<'GH'
#!/bin/bash
LOG="${TEST_GH_LOG:-/dev/null}"
echo "gh $*" >> "$LOG"
case "${1:-} ${2:-}" in
  "pr view")
    echo "${TEST_GH_BRANCH:?TEST_GH_BRANCH not set for gh stub}"
    ;;
  "pr merge")
    : # no-op success; real gh would squash-merge and delete the remote branch
    ;;
  *)
    : # unrecognized call; succeed harmlessly rather than break the script
    ;;
esac
exit 0
GH
  chmod +x "$bindir/gh"
  echo "$bindir"
}

# Build a fixture repo pair: a bare "origin" and a working clone on a fresh
# draft branch, standing in for the shared engineering-journal checkout.
# Args: branch  [orphan-draft-file-relpath ...]
# Env:  SKIP_COMPOSED=1 omits the composed-journal file (forces the
#       interactive "not composed yet" prompt in Step 3).
# Echoes "<work> <bare> <pre_sha>".
make_fixture() {
  local branch="$1"; shift
  local bare work date_part pre_sha
  bare=$(mktemp -d)
  work=$(mktemp -d)
  git init -q --bare "$bare" >/dev/null 2>&1
  (
    cd "$work" || exit 1
    git init -q
    git config user.email t@t.test
    git config user.name test
    git config commit.gpgsign false
    git config pull.rebase false
    git remote add origin "$bare"

    git checkout -q -b main
    mkdir -p sessions/testproj
    echo "# journal repo" > README.md
    git add -A && git commit -qm "init main"
    git push -q origin main

    git checkout -q -b "$branch"
    date_part="${branch#draft/}"
    echo "session marker" > sessions/testproj/.session-marker
    if [[ "${SKIP_COMPOSED:-0}" != "1" ]]; then
      echo "composed journal" > "sessions/testproj/${date_part}-composed.md"
    fi
    for f in "$@"; do
      mkdir -p "$(dirname "$f")"
      echo "orphan draft" > "$f"
    done
    git add -A
    git commit -qm "session work for $date_part"
    git push -q -u origin "$branch"
  ) >/dev/null 2>&1
  pre_sha=$(git -C "$work" rev-parse HEAD)
  echo "$work $bare $pre_sha"
}

# Run the real script. Args: workdir branch ghbin gh_log stdin(devnull|<text>)
# Echoes "<exit_code> <logfile>".
run_script() {
  local workdir="$1" branch="$2" ghbin="$3" ghlog="$4" stdin_reply="$5"
  local logfile rc
  logfile=$(mktemp)
  if [[ "$stdin_reply" == "devnull" ]]; then
    JOURNAL_REPO="$workdir" PATH="$ghbin:$PATH" \
      TEST_GH_BRANCH="$branch" TEST_GH_LOG="$ghlog" \
      bash "$SCRIPT" "$PR_NUMBER" >"$logfile" 2>&1 </dev/null
  else
    JOURNAL_REPO="$workdir" PATH="$ghbin:$PATH" \
      TEST_GH_BRANCH="$branch" TEST_GH_LOG="$ghlog" \
      bash "$SCRIPT" "$PR_NUMBER" >"$logfile" 2>&1 <<<"$stdin_reply"
  fi
  rc=$?
  echo "$rc $logfile"
}

commit_files() { git -C "$1" diff-tree --no-commit-id --name-only -r "$2" | sort; }
head_sha()     { git -C "$1" rev-parse HEAD; }
commit_msg()   { git -C "$1" log -1 --format=%s "$2"; }

cleanup() { rm -rf "$WORK" "$BARE" "$(dirname "$GHBIN")" "$LOGFILE" "$GHLOG" 2>/dev/null || true; }

# --- Scenario 1: concurrent-session staged file must not be swept into the ---
# --- Step 4 orphaned-draft commit (the core ask of dev-env#463)           ---
echo "[1] concurrent staged file is excluded from the orphaned-draft commit"
BRANCH="draft/2026-07-01"
DRAFT_FILE="sessions/testproj/2026-07-01_120000_draft.md"
read -r WORK BARE PRE_SHA <<<"$(make_fixture "$BRANCH" "$DRAFT_FILE")"
GHBIN=$(make_stub_gh)
GHLOG=$(mktemp)
LOGFILE=""

CONCURRENT_FILE="sessions/otherproj/2026-07-01_130000.manifest.jsonl"
mkdir -p "$WORK/$(dirname "$CONCURRENT_FILE")"
echo "concurrent session shard" > "$WORK/$CONCURRENT_FILE"
git -C "$WORK" add "$CONCURRENT_FILE"

read -r RC LOGFILE <<<"$(run_script "$WORK" "$BRANCH" "$GHBIN" "$GHLOG" devnull)"
echo "    (script exit $RC — a non-zero exit here is expected; see header note)"

NEW_SHA=$(head_sha "$WORK")
if [ "$NEW_SHA" != "$PRE_SHA" ]; then
  ok "Step 4 created a new commit"
  MSG=$(commit_msg "$WORK" "$NEW_SHA")
  case "$MSG" in
    "chore: remove orphaned draft files"*) ok "commit message matches: $MSG" ;;
    *) bad "unexpected commit message: $MSG" ;;
  esac
  FILES=$(commit_files "$WORK" "$NEW_SHA")
  if [ "$FILES" = "$DRAFT_FILE" ]; then
    ok "commit touches exactly the deleted draft file, nothing else"
  else
    bad "commit touched unexpected file set: [$FILES]"
  fi
else
  bad "no new commit was created for the orphaned draft file"
fi

if [ ! -e "$WORK/$DRAFT_FILE" ]; then
  ok "orphaned draft file removed from disk"
else
  bad "orphaned draft file still present on disk"
fi

STATUS=$(git -C "$WORK" status --porcelain -- "$CONCURRENT_FILE")
case "$STATUS" in
  "A  $CONCURRENT_FILE")
    ok "concurrent session's file remains staged, uncommitted"
    ;;
  *)
    bad "concurrent session's file has unexpected status: [$STATUS]"
    ;;
esac

if git -C "$WORK" show "${NEW_SHA}:${CONCURRENT_FILE}" >/dev/null 2>&1; then
  bad "concurrent session's file was swept into the orphaned-draft commit"
else
  ok "concurrent session's file is absent from the orphaned-draft commit tree"
fi
cleanup

# --- Scenario 2: no orphaned drafts -> Step 4 is skipped cleanly, no ---
# --- spurious commit, and the full script (rebase/push/merge) succeeds ---
echo "[2] no orphaned drafts -> clean skip, no spurious commit"
BRANCH="draft/2026-07-02"
read -r WORK BARE PRE_SHA <<<"$(make_fixture "$BRANCH")"
GHBIN=$(make_stub_gh)
GHLOG=$(mktemp)
LOGFILE=""

read -r RC LOGFILE <<<"$(run_script "$WORK" "$BRANCH" "$GHBIN" "$GHLOG" devnull)"
[ "$RC" = "0" ] && ok "script exits 0" || { bad "expected exit 0, got $RC"; cat "$LOGFILE"; }

NEW_SHA=$(head_sha "$WORK")
[ "$NEW_SHA" = "$PRE_SHA" ] && ok "no spurious commit created" || bad "unexpected new commit: $(commit_msg "$WORK" "$NEW_SHA")"

if grep -q "pr merge $PR_NUMBER" "$GHLOG"; then
  ok "gh pr merge was reached (full script ran to completion)"
else
  bad "gh pr merge was never called — script did not reach Step 6"
fi
cleanup

# --- Scenario 3: multiple orphaned drafts across directories -> commit ---
# --- includes all of them (guards the "${DRAFT_FILES[@]}" array handling) ---
echo "[3] multiple orphaned drafts across directories are all committed"
BRANCH="draft/2026-07-03"
DRAFT_A="sessions/testproj/2026-07-03_a_draft.md"
DRAFT_B="sessions/otherproj/2026-07-03_b_draft.md"
read -r WORK BARE PRE_SHA <<<"$(make_fixture "$BRANCH" "$DRAFT_A" "$DRAFT_B")"
GHBIN=$(make_stub_gh)
GHLOG=$(mktemp)
LOGFILE=""

read -r RC LOGFILE <<<"$(run_script "$WORK" "$BRANCH" "$GHBIN" "$GHLOG" devnull)"
[ "$RC" = "0" ] && ok "script exits 0" || { bad "expected exit 0, got $RC"; cat "$LOGFILE"; }

NEW_SHA=$(head_sha "$WORK")
EXPECTED=$(printf '%s\n%s' "$DRAFT_A" "$DRAFT_B" | sort)
FILES=$(commit_files "$WORK" "$NEW_SHA")
if [ "$FILES" = "$EXPECTED" ]; then
  ok "commit includes both orphaned draft files"
else
  bad "commit file set [$FILES] != expected [$EXPECTED]"
fi
cleanup

# --- Scenario 4: no composed journal file + user declines -> abort before ---
# --- any mutation (Step 3's interactive guard) ---
echo "[4] missing composed journal + user declines -> abort, no mutation"
BRANCH="draft/2026-07-04"
SKIP_COMPOSED=1
read -r WORK BARE PRE_SHA <<<"$(make_fixture "$BRANCH")"
unset SKIP_COMPOSED
GHBIN=$(make_stub_gh)
GHLOG=$(mktemp)
LOGFILE=""

read -r RC LOGFILE <<<"$(run_script "$WORK" "$BRANCH" "$GHBIN" "$GHLOG" $'n\n')"
[ "$RC" = "1" ] && ok "script exits 1" || { bad "expected exit 1, got $RC"; cat "$LOGFILE"; }
grep -q "Aborted." "$LOGFILE" && ok "prints Aborted." || bad "missing Aborted. message"

NEW_SHA=$(head_sha "$WORK")
[ "$NEW_SHA" = "$PRE_SHA" ] && ok "no commit created before abort" || bad "unexpected commit created before abort"

if grep -q "pr merge $PR_NUMBER" "$GHLOG"; then
  bad "gh pr merge was called despite the abort"
else
  ok "gh pr merge was never reached"
fi
cleanup

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
