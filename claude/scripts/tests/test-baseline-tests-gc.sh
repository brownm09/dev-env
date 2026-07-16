#!/bin/bash
# Behavioral test for baseline-tests.sh's branch-existence-based gc (dev-env#778).
#
# A follow-up from dev-env#768/PR#777, which deliberately excluded
# baseline_<repo>_<branch>.json from its own age-based sentinel sweep --
# see sweep-scratch-debris.py's module docstring. This snapshot family is
# scoped to a BRANCH's lifetime, not a session/day, so cleanup must be
# keyed on branch existence (local ref or origin), never age.
#
# Sources the real script (guarded by the BASH_SOURCE check at its own
# tail -- sourcing only defines functions, nothing executes) to call
# branch_exists_locally / branch_exists_remotely / branch_is_gone /
# read_baseline_meta / cmd_gc directly against real throwaway git fixtures
# (a bare "origin" + a working clone, mirroring test-merge-stale-pr.sh's
# pattern) -- no mocking, no real network beyond the local filesystem
# transport git uses for a file-path remote. Also drives cmd_gc and
# cmd_snapshot as real subprocesses for the subcommand-dispatch and
# auto-gc-on-snapshot paths.
#
# core.hooksPath is set GLOBALLY on this machine (dev-env's own git hooks) --
# every fixture repo below explicitly overrides it back to an empty
# directory so a throwaway `git commit`/`git push` in a temp fixture never
# invokes a real dev-env hook (e.g. the pre-push lockfile-drift guard).
#
# Usage:
#   bash claude/scripts/tests/test-baseline-tests-gc.sh
#
# Exit 0 = all pass.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/claude/scripts/baseline-tests.sh"

TMPROOT=$(mktemp -d)
trap 'rm -rf "$TMPROOT"' EXIT

NOHOOKS_DIR="$TMPROOT/no-hooks"
mkdir -p "$NOHOOKS_DIR"

# Point SCRATCH_DIR somewhere inert before sourcing so the script's own
# `: "${SCRATCH_DIR:=...}"` default-assignment never resolves to the real
# machine scratch dir, even transiently. Each test below reassigns it to a
# fixture-specific path before calling any function that touches disk.
export SCRATCH_DIR="$TMPROOT/_unused_default_scratch"
# shellcheck source=/dev/null
source "$SCRIPT"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "PASS: $1"; }
fail() {
  FAIL=$((FAIL + 1))
  echo "FAIL: $1"
  shift
  for line in "$@"; do echo "      $line"; done
}

assert_kept() {
  local desc="$1" path="$2"
  if [ -f "$path" ]; then pass "$desc"; else fail "$desc" "expected $path to survive gc, but it was deleted"; fi
}

assert_removed() {
  local desc="$1" path="$2"
  if [ ! -f "$path" ]; then pass "$desc"; else fail "$desc" "expected $path to be removed by gc, but it still exists"; fi
}

# --- fixture builders --------------------------------------------------

# init_repo <path> [--bare]
init_repo() {
  local path="$1" bare="${2:-}"
  if [ "$bare" = "--bare" ]; then
    git init --quiet --bare --initial-branch=main "$path"
  else
    git init --quiet --initial-branch=main "$path"
    git -C "$path" config user.email "test@example.com"
    git -C "$path" config user.name "Test"
    git -C "$path" config commit.gpgsign false
  fi
  git -C "$path" config core.hooksPath "$NOHOOKS_DIR"
}

# make_fixture <root> -- creates <root>/origin.git (bare) and <root>/myrepo
# (a working clone with one commit on main, pushed to origin). Prints the
# working clone's path.
make_fixture() {
  local root="$1" origin="$1/origin.git" work="$1/myrepo"
  init_repo "$origin" --bare
  init_repo "$work"
  echo "x" > "$work/README.md"
  git -C "$work" add README.md
  git -C "$work" commit --quiet -m initial
  git -C "$work" remote add origin "$origin"
  git -C "$work" push --quiet -u origin main
  echo "$work"
}

# write_baseline <scratch_dir> <repo> <branch> <filename>
write_baseline() {
  local scratch="$1" repo="$2" branch="$3" fname="$4"
  cat > "$scratch/$fname" <<JSON
{"repo": "$repo", "branch": "$branch", "captured_at": "2026-01-01T00:00:00.000Z", "summary": {"passed": 0, "failed": 0, "skipped": 0, "total": 0}, "failures": []}
JSON
}

# --- shared "main" fixture: one gc pass, several dispositions ----------
#
# Building one fixture and calling cmd_gc ONCE against six baseline files
# with different expected dispositions also proves gc handles a mixed batch
# correctly in a single pass, not just one file in isolation.

MAIN_WORK=""
MAIN_SCRATCH=""

setup_main_fixture() {
  local root="$TMPROOT/main"
  mkdir -p "$root"
  MAIN_WORK=$(make_fixture "$root")
  MAIN_SCRATCH="$root/scratch"
  mkdir -p "$MAIN_SCRATCH"

  # a) branch exists locally -> kept
  git -C "$MAIN_WORK" branch feature-local >/dev/null
  write_baseline "$MAIN_SCRATCH" "myrepo" "feature-local" "baseline_myrepo_feature-local.json"

  # b) branch exists only on origin (pushed, then local ref deleted) -> kept
  git -C "$MAIN_WORK" branch feature-remote-only >/dev/null
  git -C "$MAIN_WORK" push --quiet origin feature-remote-only
  git -C "$MAIN_WORK" branch -D feature-remote-only >/dev/null
  write_baseline "$MAIN_SCRATCH" "myrepo" "feature-remote-only" "baseline_myrepo_feature-remote-only.json"

  # c) branch never existed anywhere -> removed
  write_baseline "$MAIN_SCRATCH" "myrepo" "feature-long-gone" "baseline_myrepo_feature-long-gone.json"

  # d) a different repo's file sitting in the same scratch dir -> the glob
  #    never matches it, so it is never even scanned, regardless of its
  #    own branch's existence.
  write_baseline "$MAIN_SCRATCH" "otherrepo" "feature-long-gone" "baseline_otherrepo_feature-long-gone.json"

  # e) malformed JSON -> kept (never guess)
  echo "{not valid json" > "$MAIN_SCRATCH/baseline_myrepo_brokenfile.json"

  # f) well-formed JSON missing the branch field -> kept (never guess)
  echo '{"repo": "myrepo", "captured_at": "2026-01-01T00:00:00.000Z", "failures": []}' \
    > "$MAIN_SCRATCH/baseline_myrepo_nobranch.json"

  (
    cd "$MAIN_WORK" || exit 1
    SCRATCH_DIR="$MAIN_SCRATCH"
    cmd_gc
  ) > "$root/gc.log" 2>&1
}

test_kept_branch_exists_locally() {
  assert_kept "gc keeps a baseline whose branch exists locally" \
    "$MAIN_SCRATCH/baseline_myrepo_feature-local.json"
}

test_kept_branch_exists_on_origin_only() {
  assert_kept "gc keeps a baseline whose branch exists only on origin (deleted locally)" \
    "$MAIN_SCRATCH/baseline_myrepo_feature-remote-only.json"
}

test_removed_branch_gone_everywhere() {
  assert_removed "gc removes a baseline whose branch is gone locally and on origin" \
    "$MAIN_SCRATCH/baseline_myrepo_feature-long-gone.json"
}

test_kept_other_repo_file_never_scanned() {
  assert_kept "gc never scans a different repo's baseline file, regardless of its branch" \
    "$MAIN_SCRATCH/baseline_otherrepo_feature-long-gone.json"
}

test_kept_malformed_json() {
  assert_kept "gc keeps a malformed/unparseable baseline file" \
    "$MAIN_SCRATCH/baseline_myrepo_brokenfile.json"
}

test_kept_missing_branch_field() {
  assert_kept "gc keeps a well-formed baseline file that is missing the branch field" \
    "$MAIN_SCRATCH/baseline_myrepo_nobranch.json"
}

# --- direct helper-function tests (reuse the main fixture) -------------

test_branch_exists_locally_true() {
  (cd "$MAIN_WORK" && branch_exists_locally feature-local)
  if [ $? -eq 0 ]; then
    pass "branch_exists_locally: true for an existing local branch"
  else
    fail "branch_exists_locally: true for an existing local branch" "expected exit 0"
  fi
}

test_branch_exists_locally_false() {
  (cd "$MAIN_WORK" && branch_exists_locally definitely-nope-branch)
  if [ $? -ne 0 ]; then
    pass "branch_exists_locally: false for a nonexistent local branch"
  else
    fail "branch_exists_locally: false for a nonexistent local branch" "expected non-zero exit"
  fi
}

test_branch_exists_remotely_confirmed_absent() {
  local rc
  (cd "$MAIN_WORK" && branch_exists_remotely definitely-nope-branch)
  rc=$?
  if [ "$rc" -eq 1 ]; then
    pass "branch_exists_remotely: returns 1 (confirmed absent) for a branch not on origin"
  else
    fail "branch_exists_remotely: returns 1 (confirmed absent) for a branch not on origin" "expected exit 1, got $rc"
  fi
}

test_branch_exists_remotely_confirmed_present() {
  local rc
  (cd "$MAIN_WORK" && branch_exists_remotely feature-remote-only)
  rc=$?
  if [ "$rc" -eq 0 ]; then
    pass "branch_exists_remotely: returns 0 (confirmed present) for a branch pushed to origin"
  else
    fail "branch_exists_remotely: returns 0 (confirmed present) for a branch pushed to origin" "expected exit 0, got $rc"
  fi
}

test_read_baseline_meta_parses_fields() {
  local meta expected
  meta=$(read_baseline_meta "$MAIN_SCRATCH/baseline_myrepo_feature-local.json")
  expected=$(printf 'myrepo\tfeature-local')
  if [ "$meta" = "$expected" ]; then
    pass "read_baseline_meta: parses repo/branch from a well-formed envelope"
  else
    fail "read_baseline_meta: parses repo/branch from a well-formed envelope" "got: '$meta'"
  fi
}

test_read_baseline_meta_empty_on_malformed() {
  local meta
  meta=$(read_baseline_meta "$MAIN_SCRATCH/baseline_myrepo_brokenfile.json")
  if [ -z "$meta" ]; then
    pass "read_baseline_meta: empty on unparseable JSON"
  else
    fail "read_baseline_meta: empty on unparseable JSON" "got: '$meta'"
  fi
}

# --- conservative-on-uncertainty: the remote check itself fails --------

test_kept_when_remote_check_itself_fails() {
  local root work scratch
  root="$TMPROOT/broken-origin"
  mkdir -p "$root"
  work="$root/repo2"
  init_repo "$work"
  echo "x" > "$work/README.md"
  git -C "$work" add README.md
  git -C "$work" commit --quiet -m initial
  git -C "$work" remote add origin "$root/does-not-exist.git"
  scratch="$root/scratch"
  mkdir -p "$scratch"
  write_baseline "$scratch" "repo2" "feature-unknown" "baseline_repo2_feature-unknown.json"

  (
    cd "$work" || exit 1
    SCRATCH_DIR="$scratch"
    cmd_gc
  ) > "$root/gc.log" 2>&1

  assert_kept "gc keeps a baseline when the remote existence check itself fails (unreachable origin)" \
    "$scratch/baseline_repo2_feature-unknown.json"
}

# --- subcommand dispatch + auto-gc-on-snapshot (real subprocesses) -----

test_gc_subcommand_dispatch() {
  local root work scratch out rc
  root="$TMPROOT/dispatch"
  mkdir -p "$root"
  work=$(make_fixture "$root")
  scratch="$root/scratch"
  mkdir -p "$scratch"
  write_baseline "$scratch" "myrepo" "feature-long-gone" "baseline_myrepo_feature-long-gone.json"

  out=$(cd "$work" && SCRATCH_DIR="$scratch" bash "$SCRIPT" gc 2>&1)
  rc=$?

  if [ "$rc" -eq 0 ] && [ ! -f "$scratch/baseline_myrepo_feature-long-gone.json" ] \
     && printf '%s' "$out" | grep -q '\[baseline-tests\] gc:'; then
    pass "baseline-tests gc (subcommand dispatch): removes a gone-branch baseline, exits 0, logs via err()"
  else
    fail "baseline-tests gc (subcommand dispatch): removes a gone-branch baseline, exits 0, logs via err()" \
      "rc=$rc" "output: $out"
  fi
}

test_snapshot_auto_invokes_gc() {
  local root work scratch out rc new_baseline
  root="$TMPROOT/autogc"
  mkdir -p "$root"
  work=$(make_fixture "$root")
  scratch="$root/scratch"
  mkdir -p "$scratch"

  # A fake test_command emitting minimal valid Jest --json output (no
  # failures) so cmd_snapshot succeeds without a real jest/npx install.
  mkdir -p "$work/.claude"
  cat > "$work/.claude/hook-config.json" <<'JSON'
{"test_command": "echo '{\"testResults\":[],\"numPassedTests\":0,\"numFailedTests\":0,\"numPendingTests\":0,\"numTotalTests\":0}'"}
JSON

  # A real feature branch (so branch_name_sanitized() != "main"), plus a
  # stale baseline for a DIFFERENT, already-gone branch -- proving
  # snapshot's own auto-gc call sweeps it without touching the baseline
  # snapshot is simultaneously writing for the branch actually checked out.
  git -C "$work" checkout --quiet -b feature-under-test
  write_baseline "$scratch" "myrepo" "feature-long-gone" "baseline_myrepo_feature-long-gone.json"

  out=$(cd "$work" && SCRATCH_DIR="$scratch" bash "$SCRIPT" snapshot 2>&1)
  rc=$?
  new_baseline="$scratch/baseline_myrepo_feature-under-test.json"

  if [ "$rc" -eq 0 ] && [ -f "$new_baseline" ] && [ ! -f "$scratch/baseline_myrepo_feature-long-gone.json" ]; then
    pass "snapshot writes its own baseline and auto-gc sweeps a gone-branch baseline in the same run"
  else
    fail "snapshot writes its own baseline and auto-gc sweeps a gone-branch baseline in the same run" \
      "rc=$rc" \
      "new_baseline_exists=$([ -f "$new_baseline" ] && echo yes || echo no)" \
      "stale_still_present=$([ -f "$scratch/baseline_myrepo_feature-long-gone.json" ] && echo yes || echo no)" \
      "output: $out"
  fi
}

main() {
  setup_main_fixture

  test_kept_branch_exists_locally
  test_kept_branch_exists_on_origin_only
  test_removed_branch_gone_everywhere
  test_kept_other_repo_file_never_scanned
  test_kept_malformed_json
  test_kept_missing_branch_field
  test_branch_exists_locally_true
  test_branch_exists_locally_false
  test_branch_exists_remotely_confirmed_absent
  test_branch_exists_remotely_confirmed_present
  test_read_baseline_meta_parses_fields
  test_read_baseline_meta_empty_on_malformed
  test_kept_when_remote_check_itself_fails
  test_gc_subcommand_dispatch
  test_snapshot_auto_invokes_gc

  echo
  echo "Tests: $PASS passed, 0 skipped, $FAIL failed"
  [ "$FAIL" -eq 0 ]
}

main
