#!/bin/bash
# Self-test for the lockfile-drift guard in claude/hooks/pre-push (dev-env#308, ADR-036).
#
# Drives the REAL hook against throwaway fixture repos with a stubbed `npm`, asserting:
#   - BLOCK (exit 1) on a drifted lockfile
#   - PASS  (exit 0) on an in-sync lockfile
#   - SKIP  (exit 0 + warning) when npm exits non-zero
#   - SKIP  (exit 0) when npm is absent from PATH
#   - the working tree is restored and no backup leaks into the repo on every path
#   - a repo-level .git/hooks/pre-push still fires (chaining) on the pass path
#
# Portable to Git Bash on Windows and Linux CI. Run from anywhere:
#   bash claude/hooks/tests/test-pre-push-lockfile.sh

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK="$SCRIPT_DIR/../pre-push"
ZERO="0000000000000000000000000000000000000000"

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
  NPM_DIR=$(dirname "$NPM_PATH")
  FINALPATH=$(printf '%s\n' $(echo "$PATH" | tr ':' '\n') | grep -v -F -x "$NPM_DIR" | paste -sd: -)
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

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
