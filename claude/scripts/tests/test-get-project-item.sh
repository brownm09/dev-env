#!/usr/bin/env bash
# test-get-project-item.sh — execution smoke test for get-project-item.sh.
#
# This is the catch that bash -n (syntax only) misses: it actually RUNS the
# script end-to-end and asserts behaviour, which is how dev-env#334 (a runtime
# path-resolution bug that parsed cleanly) would have been caught.
#
# Network-dependent for the live-lookup tests: they call `gh api graphql`. When gh is
# unauthenticated or offline the whole rest of the suite SKIPS (exit 0) rather than
# failing -- so that half is a local pre-PR check, not a hermetic CI gate. The
# Test 0 group is the exception: it is hermetic and runs BEFORE the network preflight
# -- the cache-hit case because proving it needs no `gh` call at all is the whole
# point (dev-env#1057, ADR-141), the rest because a stub `gh` on PATH can show which
# board's item gets picked and what a failed lookup does -- and per the `skip()` fix
# below, a Test 0 failure can never be silently discarded by that preflight even in
# an environment (like this repo's own CI) that never has a `gh` token to begin with.
#
# GraphQL cost of one run: 6 points (the preflight plus 5 lookups, each one query
# priced at 1 point). Each lookup used to force a full-board fetch, about 2,022
# points per run, which is what exhausted the shared budget on 2026-11-05 (dev-env#1238,
# dev-env#1246). Keep live lookups few: this file runs in every run-hook-tests.py pass.
#
# Run: bash claude/scripts/tests/test-get-project-item.sh
set -u

REPO_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
SCRIPT="$REPO_ROOT/claude/scripts/get-project-item.sh"
# Known, stable project #3 item used as the success fixture. If this issue is
# ever removed from the board, the test fails loudly (assertion), which is the
# correct signal — update KNOWN_ISSUE to any current project item number.
KNOWN_ISSUE=334
# Same idea for a PR on board 3, and for an issue that sits on a different owner's
# board 2 (lifting-logbook's board, owned by merickvaughn) and never on board 3.
KNOWN_PR=1254
OTHER_BOARD_REPO=lifting-logbook
OTHER_BOARD_ISSUE=644
OTHER_BOARD_NUMBER=2
OTHER_BOARD_OWNER=merickvaughn
SCRATCH="C:/Users/brown/.claude/scratch"

PASS=0; FAIL=0
ok()  { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
# A /review finding on this PR: the original skip() unconditionally `exit 0`'d,
# discarding any failure Test 0 (hermetic, runs BEFORE this can fire) already
# recorded -- and this repo's own CI workflow deliberately provides no `gh` token,
# so the network preflight below ALWAYS skips there, meaning a Test 0 regression
# would have shipped green in the one automated environment that runs this file.
# Refuse to skip once a real failure is on the books; report it instead.
skip() {
  if [ "$FAIL" -gt 0 ]; then
    echo "FAIL: cannot SKIP with $FAIL already-recorded failure(s) -- reporting failed, not skipped ($*)"
    echo "Tests: $PASS passed, $FAIL failed"
    exit 1
  fi
  echo "SKIP: $*"
  exit 0
}

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
command -v cygpath >/dev/null 2>&1 || { echo "FATAL: cygpath not found (required for winpath -- Git Bash should always provide it)" >&2; exit 1; }
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
# Left in MSYS form: this one goes on PATH, which bash reads, not node.
STUB_DIR=$(mktemp -d)
if [ -z "$CACHE_TMP" ] || [ -z "$EMPTY_GH_CONFIG" ] || [ -z "$STUB_DIR" ]; then
  echo "FATAL: winpath (cygpath/mktemp) produced an empty temp path -- refusing to install a cleanup trap over an empty/unset target" >&2
  exit 1
fi
trap 'rm -rf "$CACHE_TMP" "$EMPTY_GH_CONFIG" "$STUB_DIR"' EXIT
FAKE_ISSUE=99999001
FAKE_ITEM_ID="PVTI_faketest123"
# Key format must match _gh_project.py's _cache_key exactly: lower-cased
# "<project_owner>/<project_number>|<repo>#<number>" -- get-project-item.sh's
# own defaults are project 3, owner brownm09, repo dev-env.
FAKE_KEY="brownm09/3|brownm09/dev-env#$FAKE_ISSUE"
CACHE_OVERRIDE="$CACHE_TMP/cache.json"
printf '{"%s": "%s"}' "$FAKE_KEY" "$FAKE_ITEM_ID" > "$CACHE_OVERRIDE"

if hit_id=$(GH_CONFIG_DIR="$EMPTY_GH_CONFIG" PROJECT_ITEM_CACHE_PATH_OVERRIDE="$CACHE_OVERRIDE" bash "$SCRIPT" "$FAKE_ISSUE" 2>&1); then
  if [ "$hit_id" = "$FAKE_ITEM_ID" ]; then
    ok "cache hit resolves #$FAKE_ISSUE -> $FAKE_ITEM_ID with no gh call (GH_CONFIG_DIR points at an empty/unauthenticated dir)"
  else
    bad "cache hit returned unexpected value: '$hit_id' (expected $FAKE_ITEM_ID)"
  fi
else
  bad "cache-hit path failed -- likely fell through to an unauthenticated gh call: '$hit_id'"
fi

# --- Test 0b — non-numeric input is rejected before it ever reaches node/gh -----
err=$(bash "$SCRIPT" not-a-number 2>&1 >/dev/null); rc=$?
if [ "$rc" -eq 1 ]; then ok "non-numeric input exits 1"; else bad "non-numeric input expected exit 1, got $rc"; fi
case "$err" in
  *"must be a positive integer"*) ok "non-numeric input emits a clear diagnostic" ;;
  *) bad "non-numeric input stderr missing the expected diagnostic: '$err'" ;;
esac

# --- Test 0c-0g — the cache-miss lookup, against a stub `gh` on PATH ------------
#
# Hermetic, so these run in CI too. The stub prints a canned response and records its
# arguments, which also pins that a miss never reaches for the full board.
cat > "$STUB_DIR/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$GH_STUB_LOG"
printf '%s' "${GH_STUB_STDOUT:-}"
[ -z "${GH_STUB_STDERR:-}" ] || printf '%s\n' "$GH_STUB_STDERR" >&2
exit "${GH_STUB_RC:-0}"
STUB
chmod +x "$STUB_DIR/gh"
GH_STUB_LOG="$STUB_DIR/calls.log"
STUB_CACHE="$CACHE_TMP/stub-cache.json"

# Runs the script on a guaranteed cache miss against the stub, with the response in
# GH_STUB_STDOUT / GH_STUB_STDERR / GH_STUB_RC. Sets stub_out, stub_err and stub_rc.
run_stubbed() {
  rm -f "$STUB_CACHE"
  stub_out=$(PATH="$STUB_DIR:$PATH" GH_STUB_LOG="$GH_STUB_LOG" \
    GH_STUB_STDOUT="${GH_STUB_STDOUT:-}" GH_STUB_STDERR="${GH_STUB_STDERR:-}" GH_STUB_RC="${GH_STUB_RC:-0}" \
    PROJECT_ITEM_CACHE_PATH_OVERRIDE="$STUB_CACHE" bash "$SCRIPT" "$@" 2>"$STUB_DIR/stderr")
  stub_rc=$?
  stub_err=$(cat "$STUB_DIR/stderr")
}

# The same number on three boards: another owner's, another number, and the wanted one.
TWO_BOARDS='{"data":{"repository":{"issueOrPullRequest":{"projectItems":{"nodes":[
  {"id":"PVTI_otherowner","project":{"number":3,"owner":{"login":"someoneelse"}}},
  {"id":"PVTI_othernumber","project":{"number":2,"owner":{"login":"brownm09"}}},
  {"id":"PVTI_wanted","project":{"number":3,"owner":{"login":"BrownM09"}}}
]}}}}}'

# Test 0c — picks the item on the wanted board, by number AND owner (case-insensitive).
GH_STUB_STDOUT="$TWO_BOARDS" GH_STUB_RC=0 GH_STUB_STDERR="" run_stubbed 42
if [ "$stub_rc" -eq 0 ] && [ "$stub_out" = "PVTI_wanted" ]; then
  ok "cache miss picks the item on the wanted board out of several"
else
  bad "expected PVTI_wanted with exit 0, got '$stub_out' exit $stub_rc: $stub_err"
fi
case "$(cat "$GH_STUB_LOG")" in
  *"project item-list"*) bad "cache miss still fetched the whole board: $(cat "$GH_STUB_LOG")" ;;
  *"api graphql"*)       ok "cache miss asks GitHub for the one item, not the whole board" ;;
  *)                     bad "stub never saw the expected 'api graphql' call: $(cat "$GH_STUB_LOG")" ;;
esac

# Test 0d — on the same number under another owner only: no match, and nothing cached.
ONLY_OTHER_OWNER='{"data":{"repository":{"issueOrPullRequest":{"projectItems":{"nodes":[
  {"id":"PVTI_otherowner","project":{"number":3,"owner":{"login":"someoneelse"}}}
]}}}}}'
GH_STUB_STDOUT="$ONLY_OTHER_OWNER" GH_STUB_RC=0 GH_STUB_STDERR="" run_stubbed 42
if [ "$stub_rc" -eq 1 ] && [ -z "$stub_out" ]; then
  ok "an item only on another owner's same-numbered board exits 1 with no ID"
else
  bad "expected exit 1 and no output for another owner's board, got '$stub_out' exit $stub_rc"
fi
case "$stub_err" in
  *"no item found"*) ok "another owner's board reports 'no item found'" ;;
  *)                 bad "another owner's board stderr missing 'no item found': '$stub_err'" ;;
esac
if [ -e "$STUB_CACHE" ]; then bad "a no-match lookup wrote to the cache: $(cat "$STUB_CACHE")"
else ok "a no-match lookup writes nothing to the cache"; fi

# Test 0e — gh's real NOT_FOUND shape (exit 1, body on stdout) is a clean no-match.
NOT_FOUND='{"data":{"repository":{"issueOrPullRequest":null}},"errors":[{"type":"NOT_FOUND","message":"Could not resolve to an issue or pull request with the number of 42."}]}'
GH_STUB_STDOUT="$NOT_FOUND" GH_STUB_RC=1 GH_STUB_STDERR="gh: Could not resolve to an issue or pull request with the number of 42." run_stubbed 42
if [ "$stub_rc" -eq 1 ]; then ok "NOT_FOUND exits 1"; else bad "NOT_FOUND expected exit 1, got $stub_rc"; fi
case "$stub_err" in
  *"no item found"*) ok "NOT_FOUND reports 'no item found'" ;;
  *)                 bad "NOT_FOUND stderr missing 'no item found': '$stub_err'" ;;
esac

# Test 0f — any other gh failure is not "no item found": gh's own exit code and message
# survive, so a caller can tell a failed lookup from an absent item. Two shapes: a
# GraphQL rate-limit body on stdout, and no stdout at all (offline).
RATE_LIMITED='{"errors":[{"type":"RATE_LIMITED","code":"graphql_rate_limit","message":"API rate limit already exceeded for user ID 1."}]}'
for body in "$RATE_LIMITED" ""; do
  GH_STUB_STDOUT="$body" GH_STUB_RC=4 GH_STUB_STDERR="gh: simulated failure" run_stubbed 42
  if [ "$stub_rc" -eq 4 ]; then ok "a non-NOT_FOUND failure keeps gh's exit code (body: '${body:0:20}')"
  else bad "expected gh's exit code 4, got $stub_rc (body: '${body:0:20}')"; fi
  case "$stub_err" in
    *"no item found"*)      bad "a failed lookup was reported as 'no item found': '$stub_err'" ;;
    *"simulated failure"*)  ok "a non-NOT_FOUND failure leaves gh's own message as the diagnosis" ;;
    *)                      bad "gh's own message was lost: '$stub_err'" ;;
  esac
done

# Test 0g — an unknown-to-any-board response with no errors is a clean no-match too.
EMPTY_NODES='{"data":{"repository":{"issueOrPullRequest":{"projectItems":{"nodes":[]}}}}}'
GH_STUB_STDOUT="$EMPTY_NODES" GH_STUB_RC=0 GH_STUB_STDERR="" run_stubbed 42
if [ "$stub_rc" -eq 1 ]; then ok "an issue on no board at all exits 1"
else bad "an issue on no board expected exit 1, got $stub_rc"; fi

# --- Network-dependent tests below: preflight gh present/authenticated/reachable ---
command -v gh >/dev/null 2>&1 || skip "gh CLI not installed"
gh auth status >/dev/null 2>&1 || skip "gh not authenticated (run: gh auth login)"
if ! gh api graphql -f query='{user(login:"brownm09"){projectV2(number:3){number}}}' >/dev/null 2>&1; then
  skip "gh project query failed (offline, or missing 'project' scope: gh auth refresh -s project)"
fi

# Count via find (not `ls | wc`) so a non-alphanumeric name can't skew the count.
count_temps() { find "$SCRATCH" -maxdepth 1 -name 'tmp_project_item_*.json' 2>/dev/null | wc -l; }
before=$(count_temps)

# Test 1 — success path: resolves a known item to a PVTI_ node id. Isolated from
# the real production cache via PROJECT_ITEM_CACHE_PATH_OVERRIDE (a fresh, empty
# tmp file) so this always exercises the live lookup, not a leftover cache entry
# from a prior run.
FALLBACK_CACHE="$CACHE_TMP/fallback-cache.json"
if ITEM_ID=$(PROJECT_ITEM_CACHE_PATH_OVERRIDE="$FALLBACK_CACHE" bash "$SCRIPT" "$KNOWN_ISSUE" 2>/dev/null); then
  case "$ITEM_ID" in
    PVTI_*) ok "resolved #$KNOWN_ISSUE -> $ITEM_ID" ;;
    *)      bad "expected a PVTI_ id for #$KNOWN_ISSUE, got: '$ITEM_ID'" ;;
  esac
else
  bad "script exited non-zero resolving known issue #$KNOWN_ISSUE (the #334 ENOENT regression)"
fi

# Test 1b — the fallback path populates the cache under the EXACT expected key (not
# just "the item ID appears somewhere in the file", which a /review finding noted
# would pass even if the write landed under a wrong owner/repo/project key -- the
# specific defect class the cache-scoping fixes above exist to prevent -- and which
# degrades unsafely to a false pass via `grep -q ""` if $ITEM_ID were ever empty).
EXPECTED_KEY="brownm09/3|brownm09/dev-env#$KNOWN_ISSUE"
if [ -n "$ITEM_ID" ] && [ -s "$FALLBACK_CACHE" ] \
   && GPI_TEST_CACHE="$FALLBACK_CACHE" GPI_TEST_KEY="$EXPECTED_KEY" GPI_TEST_ID="$ITEM_ID" node -e '
     const cache = JSON.parse(require("fs").readFileSync(process.env.GPI_TEST_CACHE, "utf8"));
     process.exit(cache[process.env.GPI_TEST_KEY] === process.env.GPI_TEST_ID ? 0 : 1);
   '; then
  ok "fallback fetch populated the cache under the exact key $EXPECTED_KEY -> $ITEM_ID"
else
  bad "fallback fetch did not populate FALLBACK_CACHE under the expected key $EXPECTED_KEY -> $ITEM_ID"
fi

# Test 1c — a second invocation for the same issue, same isolated cache, now hits
# with zero `gh` calls (the fallback's own write-back actually gets read back) --
# proven the same way Test 0 is, via an unauthenticated GH_CONFIG_DIR.
if [ -n "$ITEM_ID" ]; then
  if second_id=$(GH_CONFIG_DIR="$EMPTY_GH_CONFIG" PROJECT_ITEM_CACHE_PATH_OVERRIDE="$FALLBACK_CACHE" bash "$SCRIPT" "$KNOWN_ISSUE" 2>&1) \
     && [ "$second_id" = "$ITEM_ID" ]; then
    ok "second lookup for #$KNOWN_ISSUE hits the now-populated cache with no gh call ($second_id)"
  else
    bad "second lookup for #$KNOWN_ISSUE did not hit the cache the first lookup populated (got: '${second_id:-}')"
  fi
fi

# Test 1d — a PR resolves the same way an issue does.
if PR_ITEM_ID=$(PROJECT_ITEM_CACHE_PATH_OVERRIDE="$FALLBACK_CACHE" bash "$SCRIPT" "$KNOWN_PR" 2>/dev/null); then
  case "$PR_ITEM_ID" in
    PVTI_*) ok "resolved PR #$KNOWN_PR -> $PR_ITEM_ID" ;;
    *)      bad "expected a PVTI_ id for PR #$KNOWN_PR, got: '$PR_ITEM_ID'" ;;
  esac
else
  bad "script exited non-zero resolving known PR #$KNOWN_PR"
fi

# Test 2 — failure path: a non-existent issue exits 1 with the diagnostic.
err=$(PROJECT_ITEM_CACHE_PATH_OVERRIDE="$FALLBACK_CACHE" bash "$SCRIPT" 99999999 2>&1 >/dev/null); rc=$?
if [ "$rc" -eq 1 ]; then ok "no-match exits 1"; else bad "no-match expected exit 1, got $rc"; fi
case "$err" in
  *"no item found"*) ok "no-match emits diagnostic to stderr" ;;
  *)                 bad "no-match stderr missing 'no item found': '$err'" ;;
esac

# Test 2b — an issue on another board only is a no-match for board 3. The control after
# it keeps this from passing vacuously: the same issue does resolve on its real board.
err=$(PROJECT_REPO="$OTHER_BOARD_REPO" PROJECT_ITEM_CACHE_PATH_OVERRIDE="$FALLBACK_CACHE" bash "$SCRIPT" "$OTHER_BOARD_ISSUE" 2>&1 >/dev/null); rc=$?
if [ "$rc" -eq 1 ]; then ok "an issue on another board only exits 1 for board 3"
else bad "an issue on another board only expected exit 1, got $rc"; fi
case "$err" in
  *"no item found"*) ok "an issue on another board only reports 'no item found'" ;;
  *)                 bad "another-board stderr missing 'no item found': '$err'" ;;
esac
if OTHER_ID=$(PROJECT_REPO="$OTHER_BOARD_REPO" PROJECT_ITEM_CACHE_PATH_OVERRIDE="$FALLBACK_CACHE" bash "$SCRIPT" "$OTHER_BOARD_ISSUE" "$OTHER_BOARD_NUMBER" "$OTHER_BOARD_OWNER" 2>/dev/null); then
  case "$OTHER_ID" in
    PVTI_*) ok "control: #$OTHER_BOARD_ISSUE resolves on its real board -> $OTHER_ID" ;;
    *)      bad "control: expected a PVTI_ id on the real board, got: '$OTHER_ID'" ;;
  esac
else
  bad "control: #$OTHER_BOARD_ISSUE did not resolve on $OTHER_BOARD_OWNER/$OTHER_BOARD_NUMBER"
fi

# Test 3 — cleanup: the trap removes the temp file on every path.
after=$(count_temps)
if [ "$after" -le "$before" ]; then ok "no leftover temp files (count $before -> $after)"
else bad "temp files leaked (count $before -> $after); trap cleanup failed"; fi

echo "Tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
