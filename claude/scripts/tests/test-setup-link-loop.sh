#!/usr/bin/env bash
# Self-test for setup.sh's ~/.claude/ link-loop enumeration (dev-env#614), its
# backup-before-replace / --restore behavior, and its Windows preflight (dev-env#1114).
#
# Scenarios 1-3 drive ONLY the extracted link_claude_windows()/link_claude_unix()
# functions (see setup.sh), with win_link/ln stubbed, so they need no
# Administrator/Developer Mode privilege. What IS real: setup.sh is sourced
# unmodified, the link arrays are the actual ones it defines, and mkdir -p runs for real
# against a throwaway $HOME.
#
# Scenarios 4 onward run setup.sh's functions for real against a throwaway $HOME: the
# backup, link-record and --restore path, set_hooks_path, same_path, win_cmd, the symlink
# probe, the path and HOME guards, the dispatch guard, mklink failure reporting, and the
# settings.json capture. Directory links are junctions on Windows (no privilege needed) and
# symlinks elsewhere; global git config is redirected to a temp file via GIT_CONFIG_GLOBAL,
# with the system file ignored. Nothing touches the real ~/.claude or git config, and
# setup_windows() itself never runs.
#
# A case that can't run here (8.3 names on a volume without them; one needing a privilege
# this shell lacks) prints "skipped:" and is counted as skipped, never as passed. Not
# "SKIP:" -- the runner reads a leading "SKIP:" as a whole-file skip.
#
# Portable to Git Bash on Windows and Linux CI. Run from anywhere:
#   bash claude/scripts/tests/test-setup-link-loop.sh

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SETUP_SCRIPT="$SCRIPT_DIR/../../../setup.sh"
FAKE_REPO="/fake/repo"

PASS=0
SKIP=0
FAIL=0
ok()   { echo "  ok: $*"; PASS=$((PASS + 1)); }
skip() { echo "  skipped: $*"; SKIP=$((SKIP + 1)); }
bad()  { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

is_windows() {
  case "$(uname -s)" in
    MINGW*|CYGWIN*|MSYS*) return 0 ;;
  esac
  return 1
}

echo "Testing $SETUP_SCRIPT"
[ -f "$SETUP_SCRIPT" ] || { echo "FATAL: setup.sh not found at $SETUP_SCRIPT"; exit 1; }

# settings.json is deliberately absent: it is a real, machine-local file seeded by
# seed_claude_settings(), not a symlink into the repo (dev-env#1049, ADR-139).
expected_file_links="CLAUDE.md"
expected_dir_links="scripts skills hooks templates"
expected_junction_links="routines"
expected_home_links="bin"

# --- Scenario 1: the link arrays match the documented ADR-003 enumeration ---
echo "[1] setup.sh's link arrays match the expected enumeration"
ARRAYS_OUT=$(source "$SETUP_SCRIPT" && printf '%s\n' "${CLAUDE_FILE_LINKS[*]}" "${CLAUDE_DIR_LINKS[*]}" \
  "${CLAUDE_JUNCTION_LINKS[*]}" "${HOME_LINKS[*]}")
mapfile -t ARR <<< "$ARRAYS_OUT"
for pair in "CLAUDE_FILE_LINKS|${ARR[0]:-}|$expected_file_links" \
            "CLAUDE_DIR_LINKS|${ARR[1]:-}|$expected_dir_links" \
            "CLAUDE_JUNCTION_LINKS|${ARR[2]:-}|$expected_junction_links" \
            "HOME_LINKS|${ARR[3]:-}|$expected_home_links"; do
  IFS='|' read -r name actual expected <<< "$pair"
  if [ "$actual" = "$expected" ]; then
    ok "$name = ($expected)"
  else
    bad "$name = ($actual), expected ($expected)"
  fi
done

# --- Scenario 2: setup_windows()'s extracted link loop (win_link stubbed) ---
echo "[2] link_claude_windows() links exactly the expected set"
TMPHOME=$(mktemp -d)
LOGFILE=$(mktemp)
(
  export HOME="$TMPHOME"
  source "$SETUP_SCRIPT"
  REPO_DIR="$FAKE_REPO"
  win_link() { echo "$1|$2|$3" >> "$LOGFILE"; }
  # Stubbed so the loop test never shells out to python against $FAKE_REPO. Logging it
  # into the same file pins that the seed runs, and where in the sequence (ADR-139).
  seed_claude_settings() { echo "SEED" >> "$LOGFILE"; }
  link_claude_windows >/dev/null
)
RC=$?
[ "$RC" = "0" ] && ok "link_claude_windows exits 0" || bad "link_claude_windows exited $RC"

EXPECTED_WIN=$(cat <<EOF
$FAKE_REPO/claude/CLAUDE.md|$TMPHOME/.claude/CLAUDE.md|file
$FAKE_REPO/claude/scripts|$TMPHOME/.claude/scripts|dir
$FAKE_REPO/claude/skills|$TMPHOME/.claude/skills|dir
$FAKE_REPO/claude/hooks|$TMPHOME/.claude/hooks|dir
$FAKE_REPO/claude/templates|$TMPHOME/.claude/templates|dir
$FAKE_REPO/claude/routines|$TMPHOME/.claude/routines|junction
SEED
$FAKE_REPO/bin|$TMPHOME/bin|junction
EOF
)
ACTUAL_WIN=$(cat "$LOGFILE" 2>/dev/null || true)
if [ "$ACTUAL_WIN" = "$EXPECTED_WIN" ]; then
  ok "win_link called for exactly the expected 7 targets plus the settings seed, in order"
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
  # Stubbed for the same reason as the Windows scenario above (ADR-139).
  seed_claude_settings() { echo "SEED" >> "$LOGFILE"; }
  link_claude_unix >/dev/null
)
RC=$?
[ "$RC" = "0" ] && ok "link_claude_unix exits 0" || bad "link_claude_unix exited $RC"

EXPECTED_UNIX=$(cat <<EOF
-sf $FAKE_REPO/claude/CLAUDE.md $TMPHOME/.claude/CLAUDE.md
-sf $FAKE_REPO/claude/scripts $TMPHOME/.claude/scripts
-sf $FAKE_REPO/claude/skills $TMPHOME/.claude/skills
-sf $FAKE_REPO/claude/hooks $TMPHOME/.claude/hooks
-sf $FAKE_REPO/claude/templates $TMPHOME/.claude/templates
-sf $FAKE_REPO/claude/routines $TMPHOME/.claude/routines
SEED
-sf $FAKE_REPO/bin $TMPHOME/bin
EOF
)
ACTUAL_UNIX=$(cat "$LOGFILE" 2>/dev/null || true)
if [ "$ACTUAL_UNIX" = "$EXPECTED_UNIX" ]; then
  ok "ln -sf called for exactly the expected 7 targets plus the settings seed, in order"
else
  bad "ln call log did not match expected:"
  echo "    --- expected ---"; echo "$EXPECTED_UNIX" | sed 's/^/    /'
  echo "    --- actual ---";   echo "$ACTUAL_UNIX"   | sed 's/^/    /'
fi

[ -d "$TMPHOME/.claude" ] && ok "~/.claude created for real" || bad "~/.claude was not created"
[ -d "$TMPHOME/.claude/scratch" ] && ok "~/.claude/scratch created for real" || bad "~/.claude/scratch was not created"
rm -rf "$TMPHOME" "$LOGFILE"

# The filesystem of the volume holding $1 (NTFS, ReFS, ext4, ...) -- diagnostics only.
volume_fs() {
  case "$(uname -s)" in
    MINGW*|CYGWIN*|MSYS*)
      powershell.exe -NoProfile -Command "[System.IO.DriveInfo]::new('$(cygpath -w "$1" | cut -c1-2)\\').DriveFormat" 2>/dev/null | tr -d '\r' ;;
    *)
      df -T "$1" 2>/dev/null | awk 'NR==2 {print $2}' ;;
  esac
}

# A directory link without needing privilege: a junction on Windows, a symlink elsewhere.
# When no link is there afterwards it prints a FIXTURE line -- what mklink said, whether the
# path exists, the volume's filesystem -- and fails, so no case below can pass on a fixture
# that was never built. (That line is how CI showed cmd.exe printing its interactive banner
# instead of running mklink -- the `/c` switch lost to path conversion.) On Windows it goes
# through setup.sh's own win_cmd, so every caller sources setup.sh first.
make_dir_link() {
  local out=""
  case "$(uname -s)" in
    MINGW*|CYGWIN*|MSYS*) out=$(win_cmd mklink /J "$(cygpath -w "$2")" "$(cygpath -w "$1")" 2>&1 </dev/null) ;;
    *) out=$(ln -s "$1" "$2" 2>&1) ;;
  esac
  [ -L "$2" ] && return 0
  echo "FIXTURE: no link at $2 after creating it | mklink: $(printf '%s' "$out" | tr -d '\r' | tr '\n' ' ') | exists: $([ -e "$2" ] && echo yes || echo no) | fs: $(volume_fs "$(dirname "$2")")"
  return 1
}

# --- Scenario 4: prepare_link_target backs up, records or unlinks -- it never deletes ---
echo "[4] prepare_link_target backs up real items, records foreign links, and never overwrites a backup"
TMPHOME=$(mktemp -d)
OUT=$(
  export HOME="$TMPHOME"
  source "$SETUP_SCRIPT"
  set +e
  mkdir -p "$HOME/repo/claude/skills" "$HOME/repo/claude/hooks" "$HOME/elsewhere" "$HOME/.claude"
  echo "keep" > "$HOME/elsewhere/keep.txt"
  echo "repo" > "$HOME/repo/claude/hooks/from-repo.txt"

  prepare_link_target "$HOME/repo/claude/skills" "$HOME/.claude/skills" >/dev/null
  echo "empty_rc=$? backup_dir_exists=$([ -d "$SETUP_BACKUP_DIR" ] && echo yes || echo no)"

  mkdir -p "$HOME/.claude/skills" && echo "mine" > "$HOME/.claude/skills/mine.txt"
  prepare_link_target "$HOME/repo/claude/skills" "$HOME/.claude/skills" >/dev/null
  echo "realdir_rc=$? dst_gone=$([ -e "$HOME/.claude/skills" ] && echo no || echo yes) saved=$(cat "$SETUP_BACKUP_DIR/skills/mine.txt" 2>/dev/null)"

  echo "my notes" > "$HOME/.claude/CLAUDE.md"
  prepare_link_target "$HOME/repo/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md" >/dev/null
  echo "realfile_rc=$? saved_file=$(cat "$SETUP_BACKUP_DIR/CLAUDE.md" 2>/dev/null)"

  # A link that already points at the target is setup's own: removed (the caller recreates
  # it), not recorded -- and the directory it pointed at survives untouched.
  if make_dir_link "$HOME/repo/claude/hooks" "$HOME/.claude/hooks"; then
    prepare_link_target "$HOME/repo/claude/hooks" "$HOME/.claude/hooks" >/dev/null
    echo "samelink_rc=$? link_gone=$({ [ -L "$HOME/.claude/hooks" ] || [ -e "$HOME/.claude/hooks" ]; } && echo no || echo yes) target_intact=$(cat "$HOME/repo/claude/hooks/from-repo.txt" 2>/dev/null) backups=$(ls "$SETUP_BACKUP_DIR" 2>/dev/null | tr '\n' ',')"
  fi

  # A link pointing anywhere else is configuration: where it pointed is recorded first.
  if make_dir_link "$HOME/elsewhere" "$HOME/.claude/templates"; then
    MSG=$(prepare_link_target "$HOME/repo/claude/templates" "$HOME/.claude/templates")
    echo "foreign_rc=$? foreign_gone=$({ [ -L "$HOME/.claude/templates" ] || [ -e "$HOME/.claude/templates" ]; } && echo no || echo yes) target_intact=$(cat "$HOME/elsewhere/keep.txt" 2>/dev/null) recorded=$([ "$(cat "$SETUP_BACKUP_DIR/templates.link" 2>/dev/null)" -ef "$HOME/elsewhere" ] && echo yes || echo no) said=$(echo "$MSG" | grep -c 'Replacing link templates')"
  fi

  # Written if absent: a second capture of the same name is refused, the original untouched.
  mkdir -p "$HOME/.claude/scripts" && echo "second" > "$HOME/.claude/scripts/s.txt"
  echo "earlier capture" > "$SETUP_BACKUP_DIR/scripts"
  prepare_link_target "$HOME/repo/claude/scripts" "$HOME/.claude/scripts" >/dev/null 2>&1
  echo "collision_rc=$? still_there=$(cat "$HOME/.claude/scripts/s.txt" 2>/dev/null) earlier=$(cat "$SETUP_BACKUP_DIR/scripts" 2>/dev/null)"
)
echo "$OUT" | grep -q "empty_rc=0 backup_dir_exists=no" && ok "nothing there: proceed, no backup dir created" || bad "empty case: $OUT"
echo "$OUT" | grep -q "realdir_rc=0 dst_gone=yes saved=mine" && ok "real directory moved into the backup, contents intact" || bad "real-dir case: $OUT"
echo "$OUT" | grep -q "realfile_rc=0 saved_file=my notes" && ok "real file moved into the backup, contents intact" || bad "real-file case: $OUT"
# backups= lists what the run backed up: only the two real items, never setup's own link.
echo "$OUT" | grep -q "samelink_rc=0 link_gone=yes target_intact=repo backups=CLAUDE.md,skills," \
  && ok "a link already pointing at the target is removed, not recorded; its target survives" || bad "same-target link case: $OUT"
echo "$OUT" | grep -q "foreign_rc=0 foreign_gone=yes target_intact=keep recorded=yes said=1" \
  && ok "a link pointing elsewhere is recorded, announced and removed; its target survives" || bad "foreign-link case: $OUT"
echo "$OUT" | grep -q "collision_rc=2 still_there=second earlier=earlier capture" \
  && ok "an existing backup of the same name is never overwritten; the item stays put" || bad "collision case: $OUT"
rm -rf "$TMPHOME"

# --- Scenario 5: restore_setup_backup undoes a run, converges, and refuses what it didn't make ---
echo "[5] restore_setup_backup puts items and links back, converges, and refuses a foreign directory"
TMPHOME=$(mktemp -d)
OUT=$(
  export HOME="$TMPHOME"
  export SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-test"
  source "$SETUP_SCRIPT"
  set +e
  mkdir -p "$HOME/repo/claude/skills" "$HOME/repo/claude/templates" "$HOME/.claude/skills" "$HOME/elsewhere"
  echo "repo" > "$HOME/repo/claude/skills/from-repo.txt"
  echo "mine" > "$HOME/.claude/skills/mine.txt"
  echo "my notes" > "$HOME/.claude/CLAUDE.md"
  echo "keep" > "$HOME/elsewhere/keep.txt"
  make_dir_link "$HOME/elsewhere" "$HOME/.claude/templates" || exit 0
  prepare_link_target "$HOME/repo/claude/skills" "$HOME/.claude/skills" >/dev/null
  prepare_link_target "$HOME/repo/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md" >/dev/null
  prepare_link_target "$HOME/repo/claude/templates" "$HOME/.claude/templates" >/dev/null
  # What setup would have linked in their place.
  make_dir_link "$HOME/repo/claude/skills" "$HOME/.claude/skills" && make_dir_link "$HOME/repo/claude/templates" "$HOME/.claude/templates" && echo "linked_before=yes"

  restore_setup_backup "$SETUP_BACKUP_DIR" >/dev/null
  echo "rc1=$? skills_real=$([ -L "$HOME/.claude/skills" ] && echo no || echo yes) mine=$(cat "$HOME/.claude/skills/mine.txt" 2>/dev/null) notes=$(cat "$HOME/.claude/CLAUDE.md" 2>/dev/null) repo_intact=$(cat "$HOME/repo/claude/skills/from-repo.txt" 2>/dev/null) backup_kept=$([ -f "$SETUP_BACKUP_DIR/skills/mine.txt" ] && echo yes || echo no) link_back=$([ -L "$HOME/.claude/templates" ] && [ "$HOME/.claude/templates" -ef "$HOME/elsewhere" ] && echo yes || echo no) staging_left=$(ls -A "$HOME/.claude" | grep -c setup-restore)"

  SECOND=$(restore_setup_backup "$SETUP_BACKUP_DIR" 2>&1)
  echo "rc2=$? already=$(echo "$SECOND" | grep -c 'Already restored')"

  # Something else where an item was restored: reported and left alone, and the exit says so.
  echo "changed" > "$HOME/.claude/CLAUDE.md"
  THIRD=$(restore_setup_backup "$SETUP_BACKUP_DIR" 2>&1)
  echo "rc3=$? skipped=$(echo "$THIRD" | grep -c 'Skipped CLAUDE.md') kept=$(cat "$HOME/.claude/CLAUDE.md")"

  # One level up -- ~/.claude/backups itself -- is refused before anything changes.
  WRONG=$(restore_setup_backup "$HOME/.claude/backups" 2>&1)
  echo "wrong_rc=$? wrong_said=$(echo "$WRONG" | grep -c 'is not one setup run') copied=$([ -e "$HOME/.claude/setup-test" ] && echo yes || echo no)"
  mkdir -p "$HOME/empty-backup"
  EMPTY=$(restore_setup_backup "$HOME/empty-backup" 2>&1)
  echo "empty_rc=$? empty_said=$(echo "$EMPTY" | grep -c 'holds nothing')"
)
echo "$OUT" | grep -q "linked_before=yes" && echo "$OUT" | grep -q "rc1=0 skills_real=yes mine=mine notes=my notes repo_intact=repo backup_kept=yes link_back=yes staging_left=0" \
  && ok "originals and the recorded link restored over setup's links; repo untouched; backup kept as the anchor" || bad "restore: $OUT"
echo "$OUT" | grep -q "rc2=0 already=3" && ok "a second restore converges (all three already back, exit 0)" || bad "second restore: $OUT"
echo "$OUT" | grep -q "rc3=1 skipped=1 kept=changed" && ok "a different item in the way is reported and never overwritten (exit 1)" || bad "changed destination: $OUT"
echo "$OUT" | grep -q "wrong_rc=1 wrong_said=1 copied=no" && ok "a directory setup didn't write is refused, changing nothing" || bad "foreign directory: $OUT"
echo "$OUT" | grep -q "empty_rc=1 empty_said=1" && ok "an empty directory is refused" || bad "empty directory: $OUT"
rm -rf "$TMPHOME"

# --- Scenario 6: set_hooks_path records the prior global value -- unset included --, and restore puts it back ---
echo "[6] set_hooks_path records a prior global core.hooksPath, or that it was unset"
TMPHOME=$(mktemp -d)
OUT=$(
  export HOME="$TMPHOME"
  # C:/ form on Windows: with MSYS_NO_PATHCONV exported (the last case below) Git Bash stops
  # translating environment variables too, and native git can't open a /tmp/... path.
  if is_windows; then export GIT_CONFIG_GLOBAL="$(cygpath -m "$TMPHOME/gitconfig")"; else export GIT_CONFIG_GLOBAL="$TMPHOME/gitconfig"; fi
  export GIT_CONFIG_NOSYSTEM=1   # a managed machine's system-level value must not change the outcome
  export SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-test"
  source "$SETUP_SCRIPT"
  set +e
  mkdir -p "$HOME/.claude"
  git config --global core.hooksPath "D:/prior/hooks"
  set_hooks_path >/dev/null
  echo "saved=$(cat "$SETUP_BACKUP_DIR/git-global-core.hooksPath" 2>/dev/null) now_ours=$(git config --global core.hooksPath | grep -c '/.claude/hooks$')"

  SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-rerun"
  set_hooks_path >/dev/null
  echo "rerun_backup=$([ -e "$SETUP_BACKUP_DIR" ] && echo yes || echo no)"

  restore_setup_backup "$TMPHOME/.claude/backups/setup-test" >/dev/null
  echo "restored=$(git config --global core.hooksPath)"

  git config --global --unset core.hooksPath
  SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-unset"
  set_hooks_path >/dev/null
  echo "unset_marker=$([ -f "$SETUP_BACKUP_DIR/git-global-core.hooksPath.unset" ] && echo yes || echo no) set_now=$(git config --global core.hooksPath | grep -c '/.claude/hooks$')"
  restore_setup_backup "$SETUP_BACKUP_DIR" >/dev/null
  echo "after_unset_restore=[$(git config --global core.hooksPath)]"

  # Exported MSYS_NO_PATHCONV (a Docker-on-Git-Bash habit) must not change what git stores.
  (export MSYS_NO_PATHCONV=1; SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-noconv"; set_hooks_path >/dev/null)
  echo "stored=$(git config --global core.hooksPath)"
)
echo "$OUT" | grep -q "saved=D:/prior/hooks now_ours=1" && ok "prior value saved before core.hooksPath is overwritten" || bad "save: $OUT"
echo "$OUT" | grep -q "rerun_backup=no" && ok "a re-run that finds its own value saves nothing" || bad "rerun: $OUT"
echo "$OUT" | grep -q "restored=D:/prior/hooks" && ok "--restore puts the prior global value back" || bad "restore hooksPath: $OUT"
echo "$OUT" | grep -q "unset_marker=yes set_now=1" && ok "an unset prior value is recorded as unset" || bad "unset marker: $OUT"
echo "$OUT" | grep -q "after_unset_restore=\[\]" && ok "--restore unsets core.hooksPath again" || bad "unset restore: $OUT"
if is_windows; then
  echo "$OUT" | grep -qE "stored=[A-Za-z]:/.*/\.claude/hooks$" && ok "stored in C:/ form even with MSYS_NO_PATHCONV exported" || bad "stored form: $OUT"
else
  echo "$OUT" | grep -q "stored=$TMPHOME/.claude/hooks" && ok "stored as \$HOME/.claude/hooks" || bad "stored form: $OUT"
fi
rm -rf "$TMPHOME"

# --- Scenario 7: same_path sees one directory through its 8.3 short and long spellings ---
# It checks file identity first, so one existing directory reached through two spellings is
# the same path, and a different directory is not. A volume with 8.3 names disabled (CI's
# D:\a\_temp) has nothing to compare -- a skip, not a pass.
echo "[7] same_path matches an existing directory across 8.3 short/long spellings"
if is_windows; then
  TMPHOME=$(mktemp -d)
  LONGDIR="$TMPHOME/long-directory-name-for-8dot3"
  mkdir -p "$LONGDIR"
  SHORTDIR=$(cygpath -u "$(cygpath -d "$LONGDIR")")
  if [ "$(basename "$SHORTDIR")" = "$(basename "$LONGDIR")" ]; then
    skip "no 8.3 short names on this volume ($(volume_fs "$TMPHOME")) -- nothing to compare"
  else
    OUT=$(
      source "$SETUP_SCRIPT"
      set +e
      same_path "$SHORTDIR" "$LONGDIR" && echo "same=yes" || echo "same=no"
      same_path "$SHORTDIR" "$TMPHOME" && echo "other=yes" || echo "other=no"
    )
    echo "$OUT" | grep -q "same=yes" && echo "$OUT" | grep -q "other=no" \
      && ok "$(basename "$SHORTDIR") and $(basename "$LONGDIR") are one directory; its parent is not" \
      || bad "8.3 spelling: $OUT"
  fi
  rm -rf "$TMPHOME"
else
  skip "8.3 short names are Windows-only"
fi

# --- Scenario 8: win_cmd runs its command, and hands mklink a path with a space intact ---
# Regression pins for dev-env#1114's CI failures on Git for Windows 2.55, where local 2.37
# passed. (1) A bare `cmd.exe /c ...` lost its /c to path conversion, so cmd printed its
# banner and ran nothing. (2) With /c fixed, one pre-quoted command string had its embedded
# quotes escaped as \", which cmd.exe rejects as bad filename syntax. stdin is /dev/null so a
# regression exits at once instead of waiting on a prompt.
echo "[8] win_cmd runs a cmd.exe command, and passes a path with a space intact"
if is_windows; then
  OUT=$(source "$SETUP_SCRIPT"; win_cmd echo win-cmd-ran 2>&1 </dev/null)
  if echo "$OUT" | grep -q "win-cmd-ran" && ! echo "$OUT" | grep -q "Microsoft Windows \[Version"; then
    ok "cmd.exe ran the command it was given"
  else
    bad "win_cmd: $(printf '%s' "$OUT" | tr -d '\r' | tr '\n' ' ')"
  fi

  TMPHOME=$(mktemp -d)
  mkdir -p "$TMPHOME/dir with space/target"
  OUT=$(source "$SETUP_SCRIPT"; win_cmd mklink /J "$(cygpath -w "$TMPHOME/dir with space/link")" "$(cygpath -w "$TMPHOME/dir with space/target")" 2>&1 </dev/null)
  if [ -L "$TMPHOME/dir with space/link" ] && [ -d "$TMPHOME/dir with space/link" ]; then
    ok "mklink got a path containing a space intact"
  else
    bad "win_cmd with a space in the path: $(printf '%s' "$OUT" | tr -d '\r' | tr '\n' ' ')"
  fi
  rm -rf "$TMPHOME"
else
  skip "cmd.exe is Windows-only"
fi

# --- Scenario 9: the symlink probe tries the privilege instead of inferring it ---
# It replaced a Developer Mode registry probe that failed silently under Git Bash; a user
# holding the "Create symbolic links" right passes it without Developer Mode or elevation.
echo "[9] symlink_probe creates and removes a real link, and fails with mklink's message when refused"
if is_windows; then
  TMPHOME=$(mktemp -d)
  OUT=$(
    export HOME="$TMPHOME"
    source "$SETUP_SCRIPT"
    set +e
    symlink_probe 2>&1
    echo "probe_rc=$? residue=$(ls -A "$HOME/.claude" | grep -c setup-symlink-probe)"
    win_cmd() { echo "You do not have sufficient privilege to perform this operation."; return 1; }
    symlink_probe 2>&1
    echo "denied_rc=$?"
  )
  if echo "$OUT" | grep -q "probe_rc=0"; then
    echo "$OUT" | grep -q "probe_rc=0 residue=0" && ok "the probe link was created and removed again" || bad "probe residue: $OUT"
  else
    skip "this shell can't create symlinks ($(echo "$OUT" | grep -m1 'mklink:' | tr -d '\r')), so only the refusal is checked"
  fi
  echo "$OUT" | grep -q "denied_rc=1" && echo "$OUT" | grep -q "mklink: You do not have sufficient privilege" \
    && ok "a refused mklink fails the probe and shows mklink's own message" || bad "refusal: $OUT"
  rm -rf "$TMPHOME"
else
  skip "the symlink probe is Windows-only"
fi

# --- Scenario 10: cmd_safe_path keeps ordinary paths and refuses cmd.exe's syntax characters ---
# Calibration: three paths that must pass (a space, parentheses, the usual clone) and seven
# that must not -- each character cmd.exe reads as syntax or as an argument separator when
# the MSYS runtime leaves the argument unquoted.
echo "[10] cmd_safe_path accepts spaces and parentheses, and refuses & ^ % ! , ; ="
OUT=$(
  source "$SETUP_SCRIPT"
  set +e
  for p in "/c/Users/John Smith/x" "/c/src/dev-env (copy)" "/c/Users/brown/Git/dev-env"; do
    cmd_safe_path "$p" || echo "refused-good: $p"
  done
  for p in "/c/R&D/x" "/c/a^b" "/c/100%/x" "/c/wow!/x" "/c/a,b" "/c/a;b" "/c/a=b"; do
    cmd_safe_path "$p" && echo "accepted-bad: $p"
  done
  echo "done"
)
if echo "$OUT" | grep -q "^done$" && ! echo "$OUT" | grep -qE "refused-good|accepted-bad"; then
  ok "3 ordinary paths accepted, 7 with cmd syntax characters refused"
else
  bad "cmd_safe_path: $OUT"
fi

# --- Scenario 11: sourcing setup.sh never runs it, whatever $0 is ---
# `bash -c 'source "$0"' setup.sh` satisfied the old BASH_SOURCE == $0 guard and ran the full
# setup. Safe even if the guard regresses: dispatch would try to restore a nonexistent
# directory, fail, and never print sourced-ok.
echo "[11] sourcing setup.sh -- even as \$0 -- defines its functions without running it"
OUT=$(bash -c 'source "$0" --restore /nonexistent-dir-for-setup-test; echo sourced-ok' "$SETUP_SCRIPT" 2>&1)
if [ "$OUT" = "sourced-ok" ]; then
  ok "bash -c 'source \"\$0\"' setup.sh only sourced it"
else
  bad "sourcing ran something: $OUT"
fi

# --- Scenario 12: the HOME and home-path guards ---
echo "[12] setup refuses a HOME that isn't the profile, and doesn't seed hooks written for another home"
TMPHOME=$(mktemp -d)
if is_windows; then
  OUT=$(
    mkdir -p "$TMPHOME/a" "$TMPHOME/b"
    export HOME="$TMPHOME/a" USERPROFILE="$(cygpath -w "$TMPHOME/b")"
    source "$SETUP_SCRIPT"
    set +e
    preflight_windows 2>&1
    echo "split_rc=$?"
    symlink_probe() { return 0; }   # past the HOME check, the probe is scenario 9's job
    export USERPROFILE="$(cygpath -w "$TMPHOME/a")"
    preflight_windows 2>&1
    echo "same_rc=$?"
  )
  echo "$OUT" | grep -q "split_rc=1" && echo "$OUT" | grep -q "is not your Windows profile" \
    && ok "a HOME that isn't the Windows profile is refused before anything changes" || bad "HOME split: $OUT"
  echo "$OUT" | grep -q "same_rc=0" && ok "HOME and the profile spelled differently (/c/... vs C:\\...) pass" || bad "HOME match: $OUT"
else
  skip "the Windows profile check is Windows-only"
fi
OUT=$(
  export HOME="$TMPHOME/home"
  mkdir -p "$HOME"
  source "$SETUP_SCRIPT"
  set +e
  REPO_DIR="$TMPHOME/fake-repo"
  mkdir -p "$REPO_DIR/claude"
  printf '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "pyw -3 C:/Users/someone-else/.claude/scripts/x.py"}]}]}}\n' > "$REPO_DIR/claude/settings.shared.json"
  mkdir -p "$HOME/.claude"
  seed_claude_settings
  echo "seed_rc=$? seeded=$([ -e "$HOME/.claude/settings.json" ] && echo yes || echo no)"
  # The same file on a machine whose home is the one it names: no objection.
  if is_windows; then HOME=/c/Users/someone-else; else HOME=C:/Users/someone-else; fi
  echo "match=[$(settings_home_mismatch)]"
)
echo "$OUT" | grep -q "WARNING: not seeding" && echo "$OUT" | grep -q "seed_rc=0 seeded=no" \
  && ok "hooks written for another home are not seeded, and setup carries on" || bad "home mismatch: $OUT"
echo "$OUT" | grep -q "match=\[\]" && ok "hooks written for this home raise no objection" || bad "home match: $OUT"
rm -rf "$TMPHOME"

# --- Scenario 13: a failed mklink names the link, the backup and the --restore command ---
echo "[13] win_link reports a failed mklink with the backup directory and the --restore command"
TMPHOME=$(mktemp -d)
OUT=$(
  export HOME="$TMPHOME"
  export SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-test"
  source "$SETUP_SCRIPT"
  set +e
  mkdir -p "$HOME/.claude/skills" && echo "mine" > "$HOME/.claude/skills/mine.txt"
  win_cmd() { echo "The system cannot find the path specified."; return 1; }
  cygpath() { printf '%s\n' "$2"; }   # win_link's paths only reach the stub above
  ( win_link "$HOME/repo/claude/skills" "$HOME/.claude/skills" dir ) 2>&1
  echo "rc=$? kept=$(cat "$SETUP_BACKUP_DIR/skills/mine.txt" 2>/dev/null)"
)
echo "$OUT" | grep -q "rc=1 kept=mine" && echo "$OUT" | grep -q "could not create the link" \
  && echo "$OUT" | grep -q "The system cannot find the path specified" && echo "$OUT" | grep -q -- "--restore" \
  && ok "the failure names the link and mklink's reason, points at --restore, and the original is in the backup" \
  || bad "mklink failure: $OUT"
rm -rf "$TMPHOME"

# --- Scenario 14: the seed's previous settings.json is captured, and --restore puts it back ---
echo "[14] a settings.json the seed changed is captured; an unchanged one isn't; --restore puts it back"
TMPHOME=$(mktemp -d)
OUT=$(
  export HOME="$TMPHOME"
  export SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-test"
  source "$SETUP_SCRIPT"
  set +e
  mkdir -p "$HOME/.claude"
  echo '{"theme": "mine"}' > "$HOME/.claude/settings.json"
  settings_home_mismatch() { :; }   # this scenario is about the capture, not the home guard
  py() { echo '{"theme": "mine", "hooks": {}}' > "$HOME/.claude/settings.json"; }   # stands in for _settings_sync
  seed_claude_settings >/dev/null
  echo "captured=$(cat "$SETUP_BACKUP_DIR/settings.json" 2>/dev/null)"

  SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-rerun"
  py() { :; }   # a sync with nothing to change
  seed_claude_settings >/dev/null
  echo "rerun_backup=$([ -e "$SETUP_BACKUP_DIR" ] && echo yes || echo no) leftovers=$(ls -A "$HOME/.claude" | grep -c settings-before)"

  restore_setup_backup "$TMPHOME/.claude/backups/setup-test" >/dev/null
  echo "rc=$? live=$(cat "$HOME/.claude/settings.json") kept_current=$(cat "$HOME"/.claude/backups/pre-restore-*-settings.json 2>/dev/null | grep -c hooks)"
)
echo "$OUT" | grep -q 'captured={"theme": "mine"}' && ok "the settings.json the seed changed is captured first" || bad "capture: $OUT"
echo "$OUT" | grep -q "rerun_backup=no leftovers=0" && ok "a seed that changes nothing captures nothing and leaves no temp file" || bad "unchanged re-run: $OUT"
echo "$OUT" | grep -q 'rc=0 live={"theme": "mine"} kept_current=1' \
  && ok "--restore puts it back, after saving the current one" || bad "settings restore: $OUT"
rm -rf "$TMPHOME"

# --- Scenario 15: restore keeps a link nested inside a backed-up directory a link ---
# The backup's mv keeps nested junctions and symlinks as links; Git Bash's default cp -a
# would turn them into deep copies that stop tracking their source.
echo "[15] restore keeps links nested inside a backed-up directory as links"
TMPHOME=$(mktemp -d)
OUT=$(
  export HOME="$TMPHOME"
  export SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-test"
  source "$SETUP_SCRIPT"
  set +e
  if is_windows && ! symlink_probe >/dev/null 2>&1; then
    echo "no-privilege"
    exit 0
  fi
  mkdir -p "$HOME/shared" "$HOME/.claude/skills"
  echo "shared" > "$HOME/shared/s.txt"
  make_dir_link "$HOME/shared" "$HOME/.claude/skills/linked" || exit 0
  prepare_link_target "$HOME/repo/claude/skills" "$HOME/.claude/skills" >/dev/null
  restore_setup_backup "$SETUP_BACKUP_DIR" >/dev/null 2>&1
  echo "rc=$? nested_link=$([ -L "$HOME/.claude/skills/linked" ] && echo yes || echo no) content=$(cat "$HOME/.claude/skills/linked/s.txt" 2>/dev/null)"
)
if echo "$OUT" | grep -q "no-privilege"; then
  skip "this shell can't create symlinks, which a restored nested link needs"
else
  echo "$OUT" | grep -q "rc=0 nested_link=yes content=shared" \
    && ok "a nested link came back as a link, still reaching its source" || bad "nested link: $OUT"
fi
rm -rf "$TMPHOME"

echo ""
echo "Results: $PASS passed, $SKIP skipped, $FAIL failed"
[ "$FAIL" -eq 0 ]
