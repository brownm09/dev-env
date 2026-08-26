#!/usr/bin/env bash
# test-get-project-item.sh — execution smoke test for get-project-item.sh.
#
# This is the catch that bash -n (syntax only) misses: it actually RUNS the
# script end-to-end and asserts behaviour, which is how dev-env#334 (a runtime
# path-resolution bug that parsed cleanly) would have been caught.
#
# Network-dependent for the item-list-fetch tests: they call `gh project item-list`.
# When gh is unauthenticated or offline the whole rest of the suite SKIPS (exit 0)
# rather than failing -- so it is a local pre-PR check, not a hermetic CI gate.
# The cache-hit test (Test 0) is the one exception: it is hermetic and runs BEFORE
# the network preflight, since proving it needs no `gh` call at all is the whole
# point (dev-env#1057, ADR-141).
#
# Run: bash claude/scripts/tests/test-get-project-item.sh
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

# get-project-item.sh embeds its cache-path env var directly into `node -e` source
# text, not as a standalone argument -- so MSYS's automatic Unix-path-to-Windows
# argument translation (which only rewrites an ARGUMENT that IS a bare Unix path,
# not a path embedded inside a larger string) never kicks in, exactly like the
# script's own header comment describes for dev-env#334. A raw `mktemp -d` result
# (MSYS-style `/tmp/tmp.XXXXX`) fed to that env var therefore resolves differently
# in bash (bash's own MSYS translation) than in node (drive-letter-prepend, e.g.
# `/tmp/...` -> `C:\tmp\...`, NOT where bash actually wrote the file) -- so every
# temp path used below is converted to genuine Windows form first, matching the
# convention the script itself follows.
winpath() { cygpath -w "$1" | tr '\\' '/'; }

# --- Test 0 — cache hit: hermetic, no `gh` call needed, runs unconditionally ----
#
# Proven, not just asserted: GH_CONFIG_DIR is pointed at an empty temp directory,
# so `gh` has no credentials and any real API call it made would fail immediately
# on auth. If the cache-hit branch in get-project-item.sh ever regressed into
# still calling `gh` on a hit, the script would fall through to that call, it
# would fail under the empty config, and the whole script would abort non-zero
# under `set -e` -- so a passing result here is real evidence the cache short-
# circuited before any `gh` invocation, not just a lucky offline/online coincidence.
CACHE_TMP=$(winpath "$(mktemp -d)")
EMPTY_GH_CONFIG=$(winpath "$(mktemp -d)")
trap 'rm -rf "$CACHE_TMP" "$EMPTY_GH_CONFIG"' EXIT
FAKE_ISSUE=99999001
FAKE_ITEM_ID="PVTI_faketest123"
CACHE_OVERRIDE="$CACHE_TMP/cache.json"
printf '{"brownm09/dev-env#%s": "%s"}' "$FAKE_ISSUE" "$FAKE_ITEM_ID" > "$CACHE_OVERRIDE"

if hit_id=$(GH_CONFIG_DIR="$EMPTY_GH_CONFIG" PROJECT_ITEM_CACHE_PATH_OVERRIDE="$CACHE_OVERRIDE" bash "$SCRIPT" "$FAKE_ISSUE" 2>&1); then
  if [ "$hit_id" = "$FAKE_ITEM_ID" ]; then
    ok "cache hit resolves #$FAKE_ISSUE -> $FAKE_ITEM_ID with no gh call (GH_CONFIG_DIR points at an empty/unauthenticated dir)"
  else
    bad "cache hit returned unexpected value: '$hit_id' (expected $FAKE_ITEM_ID)"
  fi
else
  bad "cache-hit path failed -- likely fell through to an unauthenticated gh call: '$hit_id'"
fi

# --- Network-dependent tests below: preflight gh present/authenticated/reachable ---
command -v gh >/dev/null 2>&1 || skip "gh CLI not installed"
gh auth status >/dev/null 2>&1 || skip "gh not authenticated (run: gh auth login)"
if ! gh project item-list 3 --owner brownm09 --limit 1 >/dev/null 2>&1; then
  skip "gh project query failed (offline, or missing 'project' scope: gh auth refresh -s project)"
fi

# Count via find (not `ls | wc`) so a non-alphanumeric name can't skew the count.
count_temps() { find "$SCRATCH" -maxdepth 1 -name 'tmp_project_item_*.json' 2>/dev/null | wc -l; }
before=$(count_temps)

# Test 1 — success path: resolves a known item to a PVTI_ node id. Isolated from
# the real production cache via PROJECT_ITEM_CACHE_PATH_OVERRIDE (a fresh, empty
# tmp file) so this always exercises the fetch-and-scan fallback, not a leftover
# cache entry from a prior run.
FALLBACK_CACHE="$CACHE_TMP/fallback-cache.json"
if ITEM_ID=$(PROJECT_ITEM_CACHE_PATH_OVERRIDE="$FALLBACK_CACHE" bash "$SCRIPT" "$KNOWN_ISSUE" 2>/dev/null); then
  case "$ITEM_ID" in
    PVTI_*) ok "resolved #$KNOWN_ISSUE -> $ITEM_ID" ;;
    *)      bad "expected a PVTI_ id for #$KNOWN_ISSUE, got: '$ITEM_ID'" ;;
  esac
else
  bad "script exited non-zero resolving known issue #$KNOWN_ISSUE (the #334 ENOENT regression)"
fi

# Test 1b — the fallback path populates the cache: a second invocation for the
# same issue, still isolated to FALLBACK_CACHE, must now hit with zero `gh` calls.
if [ -s "$FALLBACK_CACHE" ] && grep -q "$ITEM_ID" "$FALLBACK_CACHE" 2>/dev/null; then
  ok "fallback fetch populated the cache with #$KNOWN_ISSUE -> $ITEM_ID"
else
  bad "fallback fetch did not populate FALLBACK_CACHE as expected"
fi

# Test 2 — failure path: a non-existent issue exits 1 with the diagnostic.
err=$(PROJECT_ITEM_CACHE_PATH_OVERRIDE="$FALLBACK_CACHE" bash "$SCRIPT" 99999999 2>&1 >/dev/null); rc=$?
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
