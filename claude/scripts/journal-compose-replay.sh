#!/usr/bin/env bash
# journal-compose-replay.sh -- Step 10.5 conflict-recovery replay for /journal-compose.
#
# Invoked by claude/skills/journal-compose/SKILL.md Step 10.5 (both the
# single-project and the multi-project recovery block) AFTER the compose worktree
# has been switched onto a fresh `compose/YYYY-MM-DD` branch cut from origin/main:
#
#   PREV=$(git -C "$WT" rev-parse HEAD)              # the draft branch's tip
#   git -C "$WT" checkout -b compose/YYYY-MM-DD origin/main
#   bash journal-compose-replay.sh "$WT" "$PREV" "sessions/<project>/" README.md
#
# WHAT IT REPLAYS.  Everything the draft branch actually added, modified, or
# deleted since it diverged from origin/main (ADR-104's diff-and-replay), scoped
# to the given pathspecs.
#
# WHY THE PARTITION (dev-env#890).  ADR-104's replay applied `git checkout $PREV
# -- <path>` to every A|M path, which is last-write-wins: for a path BOTH sides
# changed, origin/main's version is discarded wholesale. During the 2026-07-21
# compose, origin/main had advanced by the 2026-07-20 compose (engineering-journal
# PR #181), which had edited the same 6 README sections -- replaying as written
# would have silently reverted the previous day's entry rows and Progress Summary
# updates in a commit that looks clean. The replay is correct for paths only the
# draft branch touched (the common case: composed journals, stubs, shards); it is
# wrong exactly for the paths that caused the conflict in the first place.
#
# So every path is first classified by whether origin/main ALSO changed it since
# the merge base. Only paths main left alone replay wholesale. A both-sides path
# gets a real 3-way merge; if that conflicts, main's content is left untouched on
# disk and the path is reported for manual reconciliation with exit 2 -- this
# script never resolves a conflict by guessing, and never leaves conflict markers
# in the work tree.
#
# It also owns the open-PR shard-integrity restore (dev-env#787), which the two
# SKILL.md copies previously duplicated verbatim as a `restore_missing_shards`
# shell function, under the same guard: a shard origin/main DELETED since the
# merge base was deleted by a concurrent session that verified the PR merged, so
# restoring it is the ADR-119 shard-resurrection failure in miniature.
#
# Exit status:
#   0  every path resolved mechanically -- caller may commit
#   2  manual reconciliation required (MANUAL_RECONCILE lists the paths) -- the
#      caller MUST NOT commit until those paths are reconciled by hand
#   1  usage or precondition error (nothing was changed), OR a git/cp failure
#      partway through the replay, in which case the tree is partially replayed.
#      That is harmless -- the recovery branch is disposable and nothing has been
#      pushed -- but re-cut it rather than committing what is there.
#
# Run: bash claude/scripts/journal-compose-replay.sh <WT> <PREV> <pathspec>...

# NOT `set -e`: `main_touched` and `git merge-file` both return non-zero as normal
# control flow (a "yes/no" answer and a conflict count). Mutating git calls carry
# an explicit `|| exit 1` instead.
set -uo pipefail

usage() {
  echo "usage: journal-compose-replay.sh <worktree> <prev-commit> <pathspec> [<pathspec> ...]" >&2
  exit 1
}

[ "$#" -ge 3 ] || usage
WT=$1
PREV=$2
shift 2

[ -d "$WT" ] || { echo "ERROR: worktree not found: $WT" >&2; exit 1; }

git -C "$WT" rev-parse --verify --quiet "$PREV^{commit}" >/dev/null || {
  echo "ERROR: not a commit in $WT: $PREV" >&2; exit 1; }
git -C "$WT" rev-parse --verify --quiet origin/main >/dev/null || {
  echo "ERROR: origin/main missing in $WT -- run 'git -C \"\$WT\" fetch origin main' first" >&2
  exit 1; }

# Computed here rather than taken as an argument so this script also works on the
# route SKILL.md's push-failure rule describes (a pre-push merged-draft-branch
# rejection jumps straight to the recovery block and skips the merge-tree probe,
# which is the only place the skill itself computes MERGE_BASE). $PREV is the
# draft tip, so this is the same commit the probe's `merge-base HEAD origin/main`
# resolved before the branch switch.
MERGE_BASE=$(git -C "$WT" merge-base "$PREV" origin/main)
[ -n "$MERGE_BASE" ] || { echo "ERROR: could not compute merge base for $PREV..origin/main" >&2; exit 1; }

# Scratch dir per the global CLAUDE.md convention (never a project working tree).
# CI runs as a different user, where that fixed path does not exist -- fall back to
# a private temp dir, which the EXIT trap then removes along with the files in it.
# JOURNAL_COMPOSE_REPLAY_SCRATCH exists so the fallback branch below is reachable
# from the test suite on any machine; the real invocation never sets it.
SCRATCH="${JOURNAL_COMPOSE_REPLAY_SCRATCH:-C:/Users/brown/.claude/scratch}"
SCRATCH_IS_OURS=""
if [ ! -d "$SCRATCH" ]; then
  SCRATCH=$(mktemp -d) || exit 1
  SCRATCH_IS_OURS=1   # we created it, so the trap must remove it too
fi
BASE_TMP="$SCRATCH/compose-replay-base-$$"
PREV_TMP="$SCRATCH/compose-replay-prev-$$"
MAIN_TMP="$SCRATCH/compose-replay-main-$$"
MERGED_TMP="$SCRATCH/compose-replay-merged-$$"
cleanup() {
  rm -f "$BASE_TMP" "$PREV_TMP" "$MAIN_TMP" "$MERGED_TMP"
  # rmdir, not rm -rf: it removes the dir only if our four files were all that
  # was in it, so a surprise cannot be deleted silently.
  [ -n "$SCRATCH_IS_OURS" ] && rmdir "$SCRATCH" 2>/dev/null
  return 0
}
trap cleanup EXIT

# Did origin/main also change $1 since the two branches diverged? Non-empty output
# means yes -- the path is contested and must never be blind-overwritten.
main_touched() {
  [ -n "$(git -C "$WT" diff --name-only "$MERGE_BASE" origin/main -- "$1")" ]
}

# show_blob <ref> <path> <outfile> -- read a blob out of <ref> into <outfile>.
#
# The `<ref>:<path>` argument is the form MSYS path conversion mangles
# (dev-env#602 / #877, ADR-120), so it needs MSYS_NO_PATHCONV=1 -- but that guard
# is all-or-nothing per command, and applied to a `git -C "$WT" show ...` it also
# stops `-C`'s own path from being translated, so git cannot find the repo at all
# ("fatal: cannot change to '/tmp/...'"). Scoping the guard inside a subshell that
# `cd`s instead -- `cd` is a bash builtin and needs no translation -- protects the
# ref argument without touching how git locates the repo.
#
# stderr is deliberately NOT suppressed: a swallowed `fatal:` here would read
# exactly like an empty file, which is the whole dev-env#602 failure class.
show_blob() {
  ( cd "$WT" && MSYS_NO_PATHCONV=1 git show "$1:$2" ) > "$3"
}

SAFE=0
BOTH=""
AUTO=""
MANUAL=""
RESTORED=""
SKIPPED=""
# Append $2 to the space-separated accumulator named by $1 (nameref, not eval --
# a path is data and must never be re-parsed as shell).
add() { local -n _acc=$1; _acc="${_acc:+$_acc }$2"; }

# --no-renames keeps the status column to plain A/M/D: a rename surfaces as a D of
# the old path plus an A of the new one, both of which the loop handles correctly.
# Read into an array rather than a scratch file so nothing this step writes can
# ever be staged into the compose commit.
mapfile -t PLAN < <(git -C "$WT" diff --no-renames --name-status "$MERGE_BASE" "$PREV" -- "$@")

for LINE in "${PLAN[@]}"; do
  case "$LINE" in
    *$'\t'*) ;;
    *) continue ;;
  esac
  STATUS=${LINE%%$'\t'*}
  FILEPATH=${LINE#*$'\t'}

  case "$STATUS" in
    A|M)
      if ! main_touched "$FILEPATH"; then
        git -C "$WT" checkout "$PREV" -- "$FILEPATH" || exit 1
        SAFE=$((SAFE + 1))
        continue
      fi
      add BOTH "$FILEPATH"
      # An A/A has no common ancestor to merge against -- never guess at one.
      #
      # All three sides are read as BLOBS, including origin/main's -- never the
      # work-tree file. `git show` emits stored content (LF), while the checked-out
      # file carries whatever the smudge filter produced; under the `core.autocrlf`
      # this machine runs, that is CRLF, so mixing the two makes every single line
      # differ and a trivially disjoint merge conflicts. The recovery branch was
      # just cut from origin/main and nothing has touched this path yet, so the blob
      # is also the authoritative content.
      #
      # `merge-file -p` writes the result to stdout, so a conflict cannot leave
      # markers in the work tree: on non-zero exit the merged output is discarded
      # and origin/main's file on disk is still untouched.
      if [ "$STATUS" = "M" ] \
        && show_blob "$MERGE_BASE" "$FILEPATH" "$BASE_TMP" \
        && show_blob "$PREV" "$FILEPATH" "$PREV_TMP" \
        && show_blob "origin/main" "$FILEPATH" "$MAIN_TMP" \
        && git merge-file -p \
             -L "draft (composed output)" -L "merge base" -L "origin/main" \
             "$PREV_TMP" "$BASE_TMP" "$MAIN_TMP" > "$MERGED_TMP"
      then
        cp "$MERGED_TMP" "$WT/$FILEPATH" || exit 1
        git -C "$WT" add -- "$FILEPATH" || exit 1
        add AUTO "$FILEPATH"
      else
        add MANUAL "$FILEPATH"
      fi
      ;;
    D)
      if [ ! -e "$WT/$FILEPATH" ]; then
        : # origin/main already deleted it too -- nothing to replay
      elif main_touched "$FILEPATH"; then
        # delete/modify: the draft branch dropped a file origin/main edited.
        add BOTH "$FILEPATH"
        add MANUAL "$FILEPATH"
      else
        git -C "$WT" rm --quiet -- "$FILEPATH" || exit 1
        SAFE=$((SAFE + 1))
      fi
      ;;
    *)
      echo "WARNING: unhandled diff status '$STATUS' for $FILEPATH -- inspect manually"
      ;;
  esac
done

# Open-PR shard integrity (dev-env#787): a shard present on $PREV but absent from
# the recovery branch is restored -- UNLESS origin/main is the reason it is absent.
mapfile -t SHARDS < <(
  git -C "$WT" ls-tree -r "$PREV" --name-only -- sessions/ | grep -E '/open-prs/[0-9]+\.json$' || true
)
for SHARD_PATH in "${SHARDS[@]}"; do
  [ -e "$WT/$SHARD_PATH" ] && continue
  if main_touched "$SHARD_PATH"; then
    add SKIPPED "$SHARD_PATH"   # origin/main deleted it deliberately -- respect that
    continue
  fi
  git -C "$WT" checkout "$PREV" -- "$SHARD_PATH" || exit 1
  add RESTORED "$SHARD_PATH"
done

echo "REPLAY_SAFE=$SAFE"
echo "BOTH_CHANGED=${BOTH:-none}"
echo "AUTO_MERGED=${AUTO:-none}"
echo "MANUAL_RECONCILE=${MANUAL:-none}"
echo "SHARD_INTEGRITY_RESTORED=${RESTORED:-none}"
echo "SHARD_RESTORE_SKIPPED=${SKIPPED:-none}"

if [ -n "$MANUAL" ]; then
  {
    echo "STOP: origin/main also changed the path(s) below since the merge base, and the"
    echo "      3-way merge could not resolve them. They still hold origin/main's content."
    echo "      Reconcile each by hand (re-apply the day's composed additions on top of"
    echo "      origin/main's version), 'git -C \"\$WT\" add' them, and only then commit:"
    for P in $MANUAL; do echo "        $P"; done
  } >&2
  exit 2
fi
exit 0
