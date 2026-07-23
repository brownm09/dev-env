#!/usr/bin/env bash
# Self-test for claude/scripts/journal-compose-replay.sh (dev-env#890).
#
# Drives the REAL script against throwaway fixture repos built with mktemp -d.
# No network, no auth, no `gh`: origin/main is faked with `git update-ref
# refs/remotes/origin/main`, which is all the script actually reads.
#
# Each fixture reproduces the state Step 10.5 hands the script: a draft branch tip
# ($PREV), an origin/main that advanced independently since the merge base, and a
# worktree already switched onto a fresh recovery branch cut from origin/main.
#
# The regression this exists for is fixture A's README.md and fixture B's
# sessions/proj/README.md: before dev-env#890, BOTH replayed as a bare
# `git checkout $PREV -- <path>`, silently discarding whatever origin/main had
# added there (the 2026-07-20 journal's entry rows, in the real incident).
#
# Portable to Git Bash on Windows and Linux CI. Run from anywhere:
#   bash claude/scripts/tests/test-journal-compose-replay.sh

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$SCRIPT_DIR/../journal-compose-replay.sh"

PASS=0
FAIL=0
ok()  { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

echo "Testing $SCRIPT"
[ -f "$SCRIPT" ] || { echo "FATAL: script not found at $SCRIPT"; exit 1; }

TMPDIRS=()
cleanup() { for d in ${TMPDIRS+"${TMPDIRS[@]}"}; do rm -rf "$d"; done; }
trap cleanup EXIT

# Read one `KEY=value` line out of the script's stdout.
field() { printf '%s\n' "$1" | sed -n "s/^$2=//p"; }

# Assert helpers -------------------------------------------------------------
eq()       { [ "$2" = "$3" ] && ok "$1" || bad "$1 (expected '$3', got '$2')"; }
contains() { case "$2" in *"$3"*) ok "$1";; *) bad "$1 (expected '$3' within '$2')";; esac; }
missing()  { case "$2" in *"$3"*) bad "$1 ('$3' unexpectedly present in '$2')";; *) ok "$1";; esac; }
has_file() { [ -e "$1/$2" ] && ok "$3" || bad "$3 ($2 should exist)"; }
no_file()  { [ -e "$1/$2" ] && bad "$3 ($2 should be gone)" || ok "$3"; }
file_has() { grep -qF -- "$3" "$1/$2" && ok "$4" || bad "$4 ($2 should contain '$3')"; }
file_hasnt() { grep -qF -- "$3" "$1/$2" && bad "$4 ($2 should not contain '$3')" || ok "$4"; }

# No conflict markers may ever reach the work tree: `merge-file -p` writes to
# stdout and the result is only moved into place on a clean merge.
no_markers() {
  local wt=$1 label=$2 hits
  hits=$(grep -rlE '^(<<<<<<<|>>>>>>>)' "$wt" --exclude-dir=.git 2>/dev/null)
  [ -z "$hits" ] && ok "$label" || bad "$label (conflict markers in: $hits)"
}

new_repo() {
  local root wt
  root=$(mktemp -d) || exit 1
  TMPDIRS+=("$root")
  wt="$root/wt"
  git init -q -b work "$wt" || exit 1
  git -C "$wt" config user.email "test@example.com"
  git -C "$wt" config user.name  "Replay Test"
  # Pin the CRLF hazard on every platform, not just Windows: with autocrlf the
  # checked-out file is CRLF while `git show` emits the LF blob, so a 3-way merge
  # that mixes work-tree content with blob content sees every line as changed and
  # a trivially disjoint merge conflicts. The script reads all three sides as
  # blobs; this config is what makes the test able to tell.
  git -C "$wt" config core.autocrlf true
  printf '%s' "$wt"
}

commit_all() { git -C "$1" add -A && git -C "$1" commit -q -m "$2"; }

# ---------------------------------------------------------------------------
# Fixture A — every path resolves mechanically (expect exit 0)
# ---------------------------------------------------------------------------
echo
echo "Fixture A: safe replay, disjoint 3-way merge, deletions, shard restore"

WT=$(new_repo)
mkdir -p "$WT/sessions/proj" "$WT/sessions/other/open-prs"

# A 40-line README whose two edited regions are far apart, so the draft's and
# main's edits merge cleanly.
{ echo "# Journal index"; echo; echo "## TOP"; echo "TOP-base"; echo
  for i in $(seq 1 30); do echo "filler line $i"; done
  echo; echo "## BOTTOM"; echo "BOTTOM-base"; } > "$WT/README.md"
echo "only-draft base" > "$WT/sessions/proj/only-draft.md"
echo "stub content"    > "$WT/sessions/proj/consumed-stub.md"
echo "doomed"          > "$WT/sessions/proj/both-deleted.md"
commit_all "$WT" base
BASE=$(git -C "$WT" rev-parse HEAD)

# Draft side: edits TOP, modifies/adds/deletes under sessions/proj/, and creates a
# shard under a project OUTSIDE the replay pathspec (the dev-env#787 shape).
git -C "$WT" checkout -q -b draftside
sed -i 's/^TOP-base$/TOP-draft-edit/' "$WT/README.md"
echo "only-draft CHANGED" > "$WT/sessions/proj/only-draft.md"
echo "composed journal"   > "$WT/sessions/proj/new-journal.md"
rm "$WT/sessions/proj/consumed-stub.md" "$WT/sessions/proj/both-deleted.md"
echo '{"pr":55}' > "$WT/sessions/other/open-prs/55.json"
commit_all "$WT" draft
PREV=$(git -C "$WT" rev-parse HEAD)

# origin/main side: edits BOTTOM only, and independently deletes both-deleted.md.
git -C "$WT" checkout -q -b mainside "$BASE"
sed -i 's/^BOTTOM-base$/BOTTOM-main-edit/' "$WT/README.md"
rm "$WT/sessions/proj/both-deleted.md"
commit_all "$WT" main
git -C "$WT" update-ref refs/remotes/origin/main HEAD

# The state Step 10.5 hands the script: recovery branch cut from origin/main.
git -C "$WT" checkout -q -b compose origin/main

OUT=$(bash "$SCRIPT" "$WT" "$PREV" "sessions/proj/" README.md 2>&1)
RC=$?

eq "exit 0 when every path resolves" "$RC" "0"
# 2 uncontested A|M checkouts + 1 uncontested D removal; the both-sides-deleted
# path is a no-op, not a replay, so it is deliberately not counted.
eq "REPLAY_SAFE counts the uncontested paths" "$(field "$OUT" REPLAY_SAFE)" "3"
eq "BOTH_CHANGED lists only the contested README" "$(field "$OUT" BOTH_CHANGED)" "README.md"
eq "AUTO_MERGED lists the cleanly merged README" "$(field "$OUT" AUTO_MERGED)" "README.md"
eq "MANUAL_RECONCILE empty" "$(field "$OUT" MANUAL_RECONCILE)" "none"

# 1. M, main untouched -> draft content wins (the common path, unchanged).
file_has "$WT" "sessions/proj/only-draft.md" "only-draft CHANGED" \
  "1. M with main untouched replays from \$PREV"
# 2. A, main untouched -> added.
has_file "$WT" "sessions/proj/new-journal.md" "2. A with main untouched is added"
# 3. M on both sides, disjoint -> auto-merged, BOTH edits survive.
file_has "$WT" README.md "TOP-draft-edit"  "3a. auto-merge keeps the draft's edit"
file_has "$WT" README.md "BOTTOM-main-edit" \
  "3b. auto-merge keeps origin/main's edit (the dev-env#890 regression)"
# 6. D, main untouched -> removed.
no_file "$WT" "sessions/proj/consumed-stub.md" "6. D with main untouched is removed"
# 7. D on both sides -> no-op, no error.
no_file "$WT" "sessions/proj/both-deleted.md" "7. D on both sides is a clean no-op"
# 9a. Shard present only on $PREV -> restored.
eq "9a. shard main never had is restored" \
  "$(field "$OUT" SHARD_INTEGRITY_RESTORED)" "sessions/other/open-prs/55.json"
has_file "$WT" "sessions/other/open-prs/55.json" "9a. restored shard is on disk"
eq "9a. nothing skipped" "$(field "$OUT" SHARD_RESTORE_SKIPPED)" "none"
# 10. No conflict markers anywhere.
no_markers "$WT" "10. fixture A leaves no conflict markers"

# Everything the script touched must be staged, so the caller's commit picks it up.
UNSTAGED=$(git -C "$WT" diff --name-only)
eq "all replayed changes are staged" "${UNSTAGED:-none}" "none"

# ---------------------------------------------------------------------------
# Fixture B — contested paths stop the recovery (expect exit 2)
# ---------------------------------------------------------------------------
echo
echo "Fixture B: overlapping merge, add/add, delete/modify, shard main deleted"

WT=$(new_repo)
mkdir -p "$WT/sessions/proj" "$WT/sessions/other/open-prs"

# The real dev-env#890 shape: a reverse-chronological entry table both composes
# insert a row at the head of.
{ echo "# Project journal"; echo; echo "| Date | Entry |"; echo "|---|---|";
  echo "| 2026-07-19 | earlier |"; } > "$WT/sessions/proj/README.md"
echo "main will edit this" > "$WT/sessions/proj/edited-by-main.md"
echo '{"pr":77}'           > "$WT/sessions/other/open-prs/77.json"
commit_all "$WT" base
BASE=$(git -C "$WT" rev-parse HEAD)

git -C "$WT" checkout -q -b draftside
sed -i 's/^|---|---|$/|---|---|\n| 2026-07-21 | draft row |/' "$WT/sessions/proj/README.md"
echo "draft version of the composed journal" > "$WT/sessions/proj/2026-07-21-slug.md"
rm "$WT/sessions/proj/edited-by-main.md"
commit_all "$WT" draft
PREV=$(git -C "$WT" rev-parse HEAD)

git -C "$WT" checkout -q -b mainside "$BASE"
sed -i 's/^|---|---|$/|---|---|\n| 2026-07-20 | main row |/' "$WT/sessions/proj/README.md"
echo "main version of the composed journal" > "$WT/sessions/proj/2026-07-21-slug.md"
echo "main edited it" > "$WT/sessions/proj/edited-by-main.md"
rm "$WT/sessions/other/open-prs/77.json"
commit_all "$WT" main
git -C "$WT" update-ref refs/remotes/origin/main HEAD

git -C "$WT" checkout -q -b compose origin/main

OUT=$(bash "$SCRIPT" "$WT" "$PREV" "sessions/proj/" README.md 2>&1)
RC=$?

eq "exit 2 when a path needs manual reconciliation" "$RC" "2"
MAN=$(field "$OUT" MANUAL_RECONCILE)

# 4. M on both sides, overlapping -> not merged, main's content intact.
contains "4a. overlapping M is listed for manual reconciliation" "$MAN" "sessions/proj/README.md"
file_has "$WT" "sessions/proj/README.md" "| 2026-07-20 | main row |" \
  "4b. origin/main's row survives (the dev-env#890 regression)"
file_hasnt "$WT" "sessions/proj/README.md" "draft row" \
  "4c. the draft's row is not force-applied over main's"
# 5. A on both sides -> no merge attempted (no common ancestor), main's copy kept.
contains "5a. add/add is listed for manual reconciliation" "$MAN" "sessions/proj/2026-07-21-slug.md"
file_has "$WT" "sessions/proj/2026-07-21-slug.md" "main version" \
  "5b. add/add leaves origin/main's file untouched"
missing "5c. add/add is never auto-merged" "$(field "$OUT" AUTO_MERGED)" "2026-07-21-slug"
# 8. D on draft, M on main -> not deleted.
contains "8a. delete/modify is listed for manual reconciliation" "$MAN" "sessions/proj/edited-by-main.md"
file_has "$WT" "sessions/proj/edited-by-main.md" "main edited it" \
  "8b. delete/modify does not blind-delete main's edit"
# 9b. Shard origin/main deleted is NOT resurrected.
eq "9b. shard main deleted is skipped, not restored" \
  "$(field "$OUT" SHARD_RESTORE_SKIPPED)" "sessions/other/open-prs/77.json"
no_file "$WT" "sessions/other/open-prs/77.json" "9b. skipped shard stays deleted"
eq "9b. nothing restored" "$(field "$OUT" SHARD_INTEGRITY_RESTORED)" "none"
# 10. No conflict markers anywhere.
no_markers "$WT" "10. fixture B leaves no conflict markers"

# ---------------------------------------------------------------------------
# Fixture C — precondition failures exit 1, never 0 or 2
# ---------------------------------------------------------------------------
echo
echo "Fixture C: preconditions"

bash "$SCRIPT" >/dev/null 2>&1
eq "no arguments exits 1" "$?" "1"

bash "$SCRIPT" "/nonexistent/worktree" HEAD "sessions/" >/dev/null 2>&1
eq "missing worktree exits 1" "$?" "1"

WT=$(new_repo)
echo x > "$WT/f.md"
commit_all "$WT" base
bash "$SCRIPT" "$WT" HEAD "sessions/" >/dev/null 2>&1
eq "absent origin/main exits 1" "$?" "1"

git -C "$WT" update-ref refs/remotes/origin/main HEAD
bash "$SCRIPT" "$WT" "not-a-commit" "sessions/" >/dev/null 2>&1
eq "bad \$PREV exits 1" "$?" "1"

echo
echo "----------------------------------------"
echo "passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
