#!/usr/bin/env bash
# test-get-project-item.sh — execution smoke test for get-project-item.sh.
#
# This is the catch that bash -n (syntax only) misses: it actually RUNS the
# script end-to-end and asserts behaviour, which is how dev-env#334 (a runtime
# path-resolution bug that parsed cleanly) would have been caught.
#
# Network-dependent: it calls `gh project item-list`. When gh is unauthenticated
# or offline it SKIPS (exit 0) rather than failing — so it is a local pre-PR
# check, not a hermetic CI gate. Run: bash claude/scripts/tests/test-get-project-item.sh
set -u

REPO_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
SCRIPT="$REPO_ROOT/claude/scripts/get-project-item.sh"
# Known, stable project #3 item used as the success fixture. If this issue is
# ever removed from the board, the test fails loudly (assertion), which is the
# correct signal — update KNOWN_ISSUE to any current project item number.
KNOWN_ISSUE=334
SCRATCH="C:/Users/brown/.claude/scratch"

PASS=0; FAIL=0
ok()  { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
skip() { echo "SKIP: $*"; exit 0; }

[ -f "$SCRIPT" ] || { echo "FATAL: script not found at $SCRIPT" >&2; exit 1; }

# Preflight: gh present, authenticated, and the project query reachable.
command -v gh >/dev/null 2>&1 || skip "gh CLI not installed"
gh auth status >/dev/null 2>&1 || skip "gh not authenticated (run: gh auth login)"
if ! gh project item-list 3 --owner brownm09 --limit 1 >/dev/null 2>&1; then
  skip "gh project query failed (offline, or missing 'project' scope: gh auth refresh -s project)"
fi

# Count via find (not `ls | wc`) so a non-alphanumeric name can't skew the count.
count_temps() { find "$SCRATCH" -maxdepth 1 -name 'tmp_project_item_*.json' 2>/dev/null | wc -l; }
before=$(count_temps)

# Test 1 — success path: resolves a known item to a PVTI_ node id.
if ITEM_ID=$(bash "$SCRIPT" "$KNOWN_ISSUE" 2>/dev/null); then
  case "$ITEM_ID" in
    PVTI_*) ok "resolved #$KNOWN_ISSUE -> $ITEM_ID" ;;
    *)      bad "expected a PVTI_ id for #$KNOWN_ISSUE, got: '$ITEM_ID'" ;;
  esac
else
  bad "script exited non-zero resolving known issue #$KNOWN_ISSUE (the #334 ENOENT regression)"
fi

# Test 2 — failure path: a non-existent issue exits 1 with the diagnostic.
err=$(bash "$SCRIPT" 99999999 2>&1 >/dev/null); rc=$?
if [ "$rc" -eq 1 ]; then ok "no-match exits 1"; else bad "no-match expected exit 1, got $rc"; fi
case "$err" in
  *"no item found"*) ok "no-match emits diagnostic to stderr" ;;
  *)                 bad "no-match stderr missing 'no item found': '$err'" ;;
esac

# Test 3 — cleanup: the trap removes the temp file on every path.
after=$(count_temps)
if [ "$after" -le "$before" ]; then ok "no leftover temp files (count $before -> $after)"
else bad "temp files leaked (count $before -> $after); trap cleanup failed"; fi

echo "Tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
