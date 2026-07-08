#!/usr/bin/env bash
# Self-test for setup.sh's ~/.claude/ link-loop enumeration (dev-env#614).
#
# setup.sh's win_link/ln calls and the UAC elevation gate in setup_windows()
# are never invoked here -- this drives ONLY the extracted
# link_claude_windows()/link_claude_unix() functions (see setup.sh), with
# win_link/ln stubbed, so the test needs no Administrator/Developer Mode
# privilege and never touches a real ~/.claude or global git config. What IS
# real: setup.sh is sourced unmodified, CLAUDE_FILE_LINKS/CLAUDE_DIR_LINKS are
# the actual arrays it defines, and mkdir -p runs for real against a
# throwaway $HOME.
#
# Portable to Git Bash on Windows and Linux CI. Run from anywhere:
#   bash claude/scripts/tests/test-setup-link-loop.sh

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SETUP_SCRIPT="$SCRIPT_DIR/../../../setup.sh"
FAKE_REPO="/fake/repo"

PASS=0
FAIL=0
ok()  { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

echo "Testing $SETUP_SCRIPT"
[ -f "$SETUP_SCRIPT" ] || { echo "FATAL: setup.sh not found at $SETUP_SCRIPT"; exit 1; }

expected_file_links="CLAUDE.md settings.json"
expected_dir_links="scripts skills hooks templates"

# --- Scenario 1: shared arrays match the documented ADR-003 enumeration ---
echo "[1] CLAUDE_FILE_LINKS / CLAUDE_DIR_LINKS match the expected enumeration"
ARRAYS_OUT=$(source "$SETUP_SCRIPT" && printf '%s\n' "${CLAUDE_FILE_LINKS[*]}" "${CLAUDE_DIR_LINKS[*]}")
mapfile -t ARR <<< "$ARRAYS_OUT"
FILE_LINKS_ACTUAL="${ARR[0]:-}"
DIR_LINKS_ACTUAL="${ARR[1]:-}"

if [ "$FILE_LINKS_ACTUAL" = "$expected_file_links" ]; then
  ok "CLAUDE_FILE_LINKS = ($expected_file_links)"
else
  bad "CLAUDE_FILE_LINKS = ($FILE_LINKS_ACTUAL), expected ($expected_file_links)"
fi
if [ "$DIR_LINKS_ACTUAL" = "$expected_dir_links" ]; then
  ok "CLAUDE_DIR_LINKS = ($expected_dir_links)"
else
  bad "CLAUDE_DIR_LINKS = ($DIR_LINKS_ACTUAL), expected ($expected_dir_links)"
fi

# --- Scenario 2: setup_windows()'s extracted link loop (win_link stubbed) ---
echo "[2] link_claude_windows() links exactly the expected set"
TMPHOME=$(mktemp -d)
LOGFILE=$(mktemp)
(
  export HOME="$TMPHOME"
  source "$SETUP_SCRIPT"
  REPO_DIR="$FAKE_REPO"
  win_link() { echo "$1|$2|$3" >> "$LOGFILE"; }
  link_claude_windows >/dev/null
)
RC=$?
[ "$RC" = "0" ] && ok "link_claude_windows exits 0" || bad "link_claude_windows exited $RC"

EXPECTED_WIN=$(cat <<EOF
$FAKE_REPO/claude/CLAUDE.md|$TMPHOME/.claude/CLAUDE.md|file
$FAKE_REPO/claude/settings.json|$TMPHOME/.claude/settings.json|file
$FAKE_REPO/claude/scripts|$TMPHOME/.claude/scripts|dir
$FAKE_REPO/claude/skills|$TMPHOME/.claude/skills|dir
$FAKE_REPO/claude/hooks|$TMPHOME/.claude/hooks|dir
$FAKE_REPO/claude/templates|$TMPHOME/.claude/templates|dir
$FAKE_REPO/claude/routines|$TMPHOME/.claude/routines|junction
$FAKE_REPO/bin|$TMPHOME/bin|junction
EOF
)
ACTUAL_WIN=$(cat "$LOGFILE" 2>/dev/null || true)
if [ "$ACTUAL_WIN" = "$EXPECTED_WIN" ]; then
  ok "win_link called for exactly the expected 8 targets, in order"
else
  bad "win_link call log did not match expected:"
  echo "    --- expected ---"; echo "$EXPECTED_WIN" | sed 's/^/    /'
  echo "    --- actual ---";   echo "$ACTUAL_WIN"   | sed 's/^/    /'
fi

[ -d "$TMPHOME/.claude" ] && ok "~/.claude created for real" || bad "~/.claude was not created"
[ -d "$TMPHOME/.claude/scratch" ] && ok "~/.claude/scratch created for real" || bad "~/.claude/scratch was not created"
rm -rf "$TMPHOME" "$LOGFILE"

# --- Scenario 3: setup_unix()'s extracted link loop (ln stubbed) ---
echo "[3] link_claude_unix() links exactly the expected set"
TMPHOME=$(mktemp -d)
LOGFILE=$(mktemp)
(
  export HOME="$TMPHOME"
  source "$SETUP_SCRIPT"
  REPO_DIR="$FAKE_REPO"
  ln() { echo "$*" >> "$LOGFILE"; }
  link_claude_unix >/dev/null
)
RC=$?
[ "$RC" = "0" ] && ok "link_claude_unix exits 0" || bad "link_claude_unix exited $RC"

EXPECTED_UNIX=$(cat <<EOF
-sf $FAKE_REPO/claude/CLAUDE.md $TMPHOME/.claude/CLAUDE.md
-sf $FAKE_REPO/claude/settings.json $TMPHOME/.claude/settings.json
-sf $FAKE_REPO/claude/scripts $TMPHOME/.claude/scripts
-sf $FAKE_REPO/claude/skills $TMPHOME/.claude/skills
-sf $FAKE_REPO/claude/hooks $TMPHOME/.claude/hooks
-sf $FAKE_REPO/claude/templates $TMPHOME/.claude/templates
-sf $FAKE_REPO/claude/routines $TMPHOME/.claude/routines
-sf $FAKE_REPO/bin $TMPHOME/bin
EOF
)
ACTUAL_UNIX=$(cat "$LOGFILE" 2>/dev/null || true)
if [ "$ACTUAL_UNIX" = "$EXPECTED_UNIX" ]; then
  ok "ln -sf called for exactly the expected 8 targets, in order"
else
  bad "ln call log did not match expected:"
  echo "    --- expected ---"; echo "$EXPECTED_UNIX" | sed 's/^/    /'
  echo "    --- actual ---";   echo "$ACTUAL_UNIX"   | sed 's/^/    /'
fi

[ -d "$TMPHOME/.claude" ] && ok "~/.claude created for real" || bad "~/.claude was not created"
[ -d "$TMPHOME/.claude/scratch" ] && ok "~/.claude/scratch created for real" || bad "~/.claude/scratch was not created"
rm -rf "$TMPHOME" "$LOGFILE"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
