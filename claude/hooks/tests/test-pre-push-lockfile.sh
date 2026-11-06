#!/bin/bash
# Self-test for the lockfile-drift guard (dev-env#308, ADR-036) and the resurrected-draft
# guard (dev-env#1205) in claude/hooks/pre-push.
#
# Drives the REAL hook against throwaway fixture repos with a stubbed `npm`, asserting:
#   - BLOCK (exit 1) on a drifted lockfile
#   - PASS  (exit 0) on an in-sync lockfile
#   - SKIP  (exit 0 + warning) when npm exits non-zero
#   - SKIP  (exit 0) when npm is absent from PATH
#   - the working tree is restored and no backup leaks into the repo on every path
#   - a repo-level .git/hooks/pre-push still fires (chaining) on the pass path
#   - the merge-base warning stays quiet on engineering-journal draft/YYYY-MM-DD pushes and
#     still fires on every other branch (dev-env#1272)
#   - a push to a past draft/<D> is blocked when the day merged from draft/<D> or
#     compose/<D>, and allowed when neither merged (resurrected-draft guard, dev-env#1205)
#
# Portable to Git Bash on Windows and Linux CI. Run from anywhere:
#   bash claude/hooks/tests/test-pre-push-lockfile.sh

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK="$SCRIPT_DIR/../pre-push"

PASS=0
FAIL=0
ok()   { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad()  { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

# Build a fixture git repo: commit C1 (in-sync), then C2 touching package.json.
# Echoes "<repo> <C1> <C2>".
make_fixture() {
  local repo
  repo=$(mktemp -d)
  (
    cd "$repo" || exit 1
    git init -q
    git config user.email t@t.test
    git config user.name test
    git config commit.gpgsign false
    printf '{\n  "name": "fix",\n  "version": "1.0.0",\n  "dependencies": { "left-pad": "^1.0.0" }\n}\n' > package.json
    printf '{\n  "name": "fix",\n  "lockfileVersion": 3,\n  "packages": {}\n}\n' > package-lock.json
    git add -A && git commit -qm c1
    # C2 changes package.json so the push range trips PKG_TOUCHED.
    printf '{\n  "name": "fix",\n  "version": "1.0.0",\n  "dependencies": { "left-pad": "^1.1.0" }\n}\n' > package.json
    git add -A && git commit -qm c2
  ) >/dev/null 2>&1 || return 1
  local c1 c2
  c2=$(git -C "$repo" rev-parse HEAD)
  c1=$(git -C "$repo" rev-parse HEAD~1)
  echo "$repo $c1 $c2"
}

# Write a stub `npm` into a fresh temp dir (OUTSIDE any repo) that behaves per
# $TEST_NPM_MODE (drift|fail|pass). Echoes the bin dir; caller removes its parent.
make_stub_npm() {
  local bindir
  bindir="$(mktemp -d)/bin"
  mkdir -p "$bindir"
  cat > "$bindir/npm" <<'NPM'
#!/bin/bash
# stub npm for the hook self-test; only `install --package-lock-only` matters.
case "${TEST_NPM_MODE:-pass}" in
  drift) printf '\n{"_drift":true}\n' >> package-lock.json; exit 0 ;;
  fail)  exit 7 ;;
  *)     exit 0 ;;   # pass: leave the lockfile untouched
esac
NPM
  chmod +x "$bindir/npm"
  echo "$bindir"
}

# Run the hook for an existing-branch push (remote=C1, local=C2).
# Args: repo c1 c2 path_override mode. Echoes the hook exit code.
run_hook() {
  local repo="$1" c1="$2" c2="$3" pathv="$4" mode="$5"
  (
    cd "$repo" || exit 99
    echo "refs/heads/main $c2 refs/heads/main $c1" | \
      PATH="$pathv" TEST_NPM_MODE="$mode" bash "$HOOK" origin "file://$repo" >/dev/null 2>&1
  )
  echo $?
}

tree_clean() { [ -z "$(git -C "$1" status --porcelain)" ]; }
no_stray_bak() { ! ls "$1"/package-lock.json.prepush.bak >/dev/null 2>&1; }

echo "Testing $HOOK"
[ -f "$HOOK" ] || { echo "FATAL: hook not found at $HOOK"; exit 1; }

# --- Scenario 1: in-sync -> PASS (exit 0), tree clean ---
echo "[1] in-sync lockfile -> PASS"
read -r REPO C1 C2 <<<"$(make_fixture)"
BIN=$(make_stub_npm "$REPO")
RC=$(run_hook "$REPO" "$C1" "$C2" "$BIN:$PATH" pass)
[ "$RC" = "0" ] && ok "exit 0" || bad "expected exit 0, got $RC"
tree_clean "$REPO" && ok "working tree clean" || bad "working tree dirty after pass"
no_stray_bak "$REPO" && ok "no stray backup" || bad "stray .prepush.bak left in repo"
rm -rf "$REPO" "$(dirname "$BIN")"

# --- Scenario 2: drift -> BLOCK (exit 1), tree restored ---
echo "[2] drifted lockfile -> BLOCK"
read -r REPO C1 C2 <<<"$(make_fixture)"
BIN=$(make_stub_npm "$REPO")
RC=$(run_hook "$REPO" "$C1" "$C2" "$BIN:$PATH" drift)
[ "$RC" = "1" ] && ok "exit 1 (push blocked)" || bad "expected exit 1, got $RC"
tree_clean "$REPO" && ok "working tree restored" || bad "lockfile not restored after block"
no_stray_bak "$REPO" && ok "no stray backup" || bad "stray .prepush.bak left in repo"
rm -rf "$REPO" "$(dirname "$BIN")"

# --- Scenario 3: npm exits non-zero -> SKIP (exit 0) ---
echo "[3] npm failure -> SKIP (no block)"
read -r REPO C1 C2 <<<"$(make_fixture)"
BIN=$(make_stub_npm "$REPO")
RC=$(run_hook "$REPO" "$C1" "$C2" "$BIN:$PATH" fail)
[ "$RC" = "0" ] && ok "exit 0 (not blocked on tooling failure)" || bad "expected exit 0, got $RC"
tree_clean "$REPO" && ok "working tree clean" || bad "working tree dirty after skip"
rm -rf "$REPO" "$(dirname "$BIN")"

# --- Scenario 4: npm absent -> SKIP (exit 0) ---
echo "[4] npm absent -> SKIP"
read -r REPO C1 C2 <<<"$(make_fixture)"
NPM_PATH=$(command -v npm 2>/dev/null || true)
SHIM=""
if [ -z "$NPM_PATH" ]; then
  FINALPATH="$PATH"   # npm already absent on this host
else
  # Drop EVERY PATH dir that holds an npm executable — npm can live in more than
  # one (here: an nvm4w shim dir AND a system "Program Files/nodejs" dir), so
  # removing only `dirname $(command -v npm)` leaves npm resolvable elsewhere and
  # the coverage check below fails. Iterate ':'-split entries so dirs with spaces
  # are matched exactly. (The previous `printf '%s\n' $(...)` form word-split on
  # spaces — SC2046 — and only "worked" by corrupting the spaced npm entry.)
  FINALPATH=$(echo "$PATH" | tr ':' '\n' | while IFS= read -r d; do
    [ -n "$d" ] || continue
    if [ -e "$d/npm" ] || [ -e "$d/npm.cmd" ] || [ -e "$d/npm.exe" ]; then continue; fi
    printf '%s\n' "$d"
  done | paste -sd: -)
  # If dropping npm's dir also dropped a tool the hook needs, shim that tool back in
  # (handles the pathological case where npm shares a directory with coreutils/git).
  SHIM=$(mktemp -d)
  for t in git grep cp rm diff mktemp date bash sed; do
    if ! PATH="$FINALPATH" command -v "$t" >/dev/null 2>&1; then
      src=$(command -v "$t" 2>/dev/null) && [ -n "$src" ] && ln -s "$src" "$SHIM/$t" 2>/dev/null
    fi
  done
  FINALPATH="$SHIM:$FINALPATH"
fi
# Coverage is only meaningful if npm is genuinely gone AND the hook's tools resolve.
# A construction failure is a loud FAIL, never a silent skip (dev-env#313).
if PATH="$FINALPATH" command -v npm >/dev/null 2>&1 || ! PATH="$FINALPATH" command -v git >/dev/null 2>&1; then
  bad "could not construct an npm-absent environment (npm still on PATH or git missing)"
else
  RC=$(run_hook "$REPO" "$C1" "$C2" "$FINALPATH" pass)
  [ "$RC" = "0" ] && ok "exit 0 (guard skipped cleanly, npm absent)" || bad "expected exit 0, got $RC"
  tree_clean "$REPO" && ok "working tree clean" || bad "working tree dirty"
fi
rm -rf "$REPO" "${SHIM:-/nonexistent}"

# --- Scenario 5: repo-level pre-push chaining fires on pass ---
echo "[5] chaining to repo-level .git/hooks/pre-push"
read -r REPO C1 C2 <<<"$(make_fixture)"
BIN=$(make_stub_npm "$REPO")
SENTINEL="$REPO/.chained"
cat > "$REPO/.git/hooks/pre-push" <<CHAIN
#!/bin/bash
touch "$SENTINEL"
exit 0
CHAIN
chmod +x "$REPO/.git/hooks/pre-push"
RC=$(run_hook "$REPO" "$C1" "$C2" "$BIN:$PATH" pass)
[ "$RC" = "0" ] && ok "exit 0" || bad "expected exit 0, got $RC"
[ -f "$SENTINEL" ] && ok "repo-level hook was invoked" || bad "repo-level hook did not fire"
rm -rf "$REPO" "$(dirname "$BIN")"

# --- Scenarios 6-8: resurrected-draft guard (dev-env#1205) ---
# A stub `gh` answers `pr list --head <head>` with a merged PR only for
# $TEST_GH_MERGED_HEAD, and logs every head it was asked about.
make_stub_gh() {
  cat > "$1/gh" <<'GH'
#!/bin/bash
head=""
while [ $# -gt 0 ]; do
  [ "$1" = "--head" ] && head="$2"
  shift
done
echo "$head" >> "$TEST_GH_LOG"
if [ "$head" = "${TEST_GH_MERGED_HEAD:-}" ]; then echo '[{"number":1}]'; else echo '[]'; fi
GH
  chmod +x "$1/gh"
}

# Push a past-dated draft branch from a fixture whose origin is the journal repo.
# Args: merged_head. Sets RC, HOOK_ERR to the hook's stderr, and GH_LOG to the heads the stub saw.
run_draft_push() {
  local repo c1 c2 bin
  read -r repo c1 c2 <<<"$(make_fixture)"
  git -C "$repo" remote add origin https://example.test/engineering-journal.git
  bin=$(make_stub_npm "$repo")
  make_stub_gh "$bin"
  local log="$repo/.gh-heads"
  (
    cd "$repo" || exit 99
    echo "refs/heads/draft/2020-01-02 $c2 refs/heads/draft/2020-01-02 $c1" | \
      PATH="$bin:$PATH" TEST_NPM_MODE=pass TEST_GH_LOG="$log" TEST_GH_MERGED_HEAD="$1" \
      bash "$HOOK" origin "file://$repo" >/dev/null 2>"$repo/.hook-stderr"
  )
  RC=$?
  HOOK_ERR=$(cat "$repo/.hook-stderr" 2>/dev/null)
  GH_LOG=$(cat "$log" 2>/dev/null)
  rm -rf "$repo" "$(dirname "$bin")"
}

echo "[6] day merged only from compose/<D> -> BLOCK"
run_draft_push compose/2020-01-02
[ "$RC" = "1" ] && ok "exit 1 (push blocked)" || bad "expected exit 1, got $RC"
echo "$HOOK_ERR" | grep -q "already has a merged PR" && ok "names the merged PR" || bad "refusal message missing"

echo "[7] day merged only from draft/<D> -> BLOCK"
run_draft_push draft/2020-01-02
[ "$RC" = "1" ] && ok "exit 1 (push blocked)" || bad "expected exit 1, got $RC"
echo "$HOOK_ERR" | grep -q "already has a merged PR" && ok "names the merged PR" || bad "refusal message missing"

echo "[8] no merged PR at either head -> PASS"
run_draft_push none
[ "$RC" = "0" ] && ok "exit 0" || bad "expected exit 0, got $RC"
# The allow is only meaningful if the guard really asked about both heads.
echo "$GH_LOG" | grep -qx draft/2020-01-02 && ok "looked up draft head" || bad "draft head not looked up"
echo "$GH_LOG" | grep -qx compose/2020-01-02 && ok "looked up compose head" || bad "compose head not looked up"

# --- Scenarios 9-12: merge-base warning scope (dev-env#1272) ---
# Push a branch from a fixture whose origin/main has moved past the branch's base.
# Args: branch origin_url. Sets RC and HOOK_ERR to the hook's stderr.
run_behind_main_push() {
  local repo c1 c2 bin moved
  read -r repo c1 c2 <<<"$(make_fixture)"
  git -C "$repo" remote add origin "$2"
  moved=$(git -C "$repo" commit-tree "$c1^{tree}" -p "$c1" -m main-moved)
  git -C "$repo" update-ref refs/remotes/origin/main "$moved"
  bin=$(make_stub_npm "$repo")
  make_stub_gh "$bin"
  (
    cd "$repo" || exit 99
    echo "refs/heads/$1 $c2 refs/heads/$1 $c1" | \
      PATH="$bin:$PATH" TEST_NPM_MODE=pass TEST_GH_LOG="$repo/.gh-heads" TEST_GH_MERGED_HEAD=none \
      bash "$HOOK" origin "file://$repo" >/dev/null 2>"$repo/.hook-stderr"
  )
  RC=$?
  HOOK_ERR=$(cat "$repo/.hook-stderr" 2>/dev/null)
  rm -rf "$repo" "$(dirname "$bin")"
}

JOURNAL_URL=https://example.test/engineering-journal.git
WARNING="merge base"

echo "[9] journal draft for today, behind origin/main -> no warning"
run_behind_main_push "draft/$(date +%Y-%m-%d)" "$JOURNAL_URL"
[ "$RC" = "0" ] && ok "exit 0" || bad "expected exit 0, got $RC"
echo "$HOOK_ERR" | grep -q "$WARNING" && bad "merge-base warning printed: $HOOK_ERR" || ok "no merge-base warning"

echo "[10] journal draft for a past unmerged day, behind origin/main -> no warning"
run_behind_main_push draft/2020-01-02 "$JOURNAL_URL"
[ "$RC" = "0" ] && ok "exit 0" || bad "expected exit 0, got $RC"
echo "$HOOK_ERR" | grep -q "$WARNING" && bad "merge-base warning printed: $HOOK_ERR" || ok "no merge-base warning"

# Same fixture as 9-10: these two prove "no warning" above is not a vacuous pass.
echo "[11] journal non-draft branch, behind origin/main -> warning"
run_behind_main_push fix/something "$JOURNAL_URL"
[ "$RC" = "0" ] && ok "exit 0 (warning does not block)" || bad "expected exit 0, got $RC"
echo "$HOOK_ERR" | grep -q "$WARNING" && ok "merge-base warning printed" || bad "merge-base warning missing"

echo "[12] draft-named branch outside the journal repo, behind origin/main -> warning"
run_behind_main_push draft/2020-01-02 https://example.test/other-repo.git
[ "$RC" = "0" ] && ok "exit 0 (warning does not block)" || bad "expected exit 0, got $RC"
echo "$HOOK_ERR" | grep -q "$WARNING" && ok "merge-base warning printed" || bad "merge-base warning missing"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
