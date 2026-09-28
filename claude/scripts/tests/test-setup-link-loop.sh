#!/usr/bin/env bash
# Self-test for setup.sh's ~/.claude/ link-loop enumeration (dev-env#614) and its
# backup-before-replace / --restore behavior (dev-env#1114).
#
# Scenarios 1-3 drive ONLY the extracted link_claude_windows()/link_claude_unix()
# functions (see setup.sh), with win_link/ln stubbed, so they need no
# Administrator/Developer Mode privilege. What IS real: setup.sh is sourced
# unmodified, CLAUDE_FILE_LINKS/CLAUDE_DIR_LINKS are the actual arrays it defines,
# and mkdir -p runs for real against a throwaway $HOME.
#
# Scenarios 4-6 run prepare_link_target, restore_setup_backup and set_hooks_path for
# real against a throwaway $HOME. Directory links are junctions on Windows (no
# privilege needed) and symlinks elsewhere; global git config is redirected to a
# temp file via GIT_CONFIG_GLOBAL. Nothing touches the real ~/.claude or git config,
# and setup_windows()'s elevation gate is never invoked.
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

# settings.json is deliberately absent: it is a real, machine-local file seeded by
# seed_claude_settings(), not a symlink into the repo (dev-env#1049, ADR-139).
expected_file_links="CLAUDE.md"
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
# that was never built.
make_dir_link() {
  local out=""
  case "$(uname -s)" in
    MINGW*|CYGWIN*|MSYS*) out=$(cmd.exe /c "mklink /J \"$(cygpath -w "$2")\" \"$(cygpath -w "$1")\"" 2>&1) ;;
    *) out=$(ln -s "$1" "$2" 2>&1) ;;
  esac
  [ -L "$2" ] && return 0
  echo "FIXTURE: no link at $2 after creating it | mklink: $(printf '%s' "$out" | tr -d '\r' | tr '\n' ' ') | exists: $([ -e "$2" ] && echo yes || echo no) | fs: $(volume_fs "$(dirname "$2")")"
  return 1
}

# --- Scenario 4: prepare_link_target backs up or unlinks -- it never deletes ---
echo "[4] prepare_link_target removes existing links (never their targets) and backs up real items"
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

  # A link that already points at the target is removed too (the caller recreates it) --
  # and the directory it pointed at must survive untouched.
  if make_dir_link "$HOME/repo/claude/hooks" "$HOME/.claude/hooks"; then
    prepare_link_target "$HOME/repo/claude/hooks" "$HOME/.claude/hooks" >/dev/null
    echo "samelink_rc=$? link_gone=$({ [ -L "$HOME/.claude/hooks" ] || [ -e "$HOME/.claude/hooks" ]; } && echo no || echo yes) target_intact=$(cat "$HOME/repo/claude/hooks/from-repo.txt" 2>/dev/null) backups=$(ls "$SETUP_BACKUP_DIR" 2>/dev/null | tr '\n' ',')"
  fi

  if make_dir_link "$HOME/elsewhere" "$HOME/.claude/templates"; then
    prepare_link_target "$HOME/repo/claude/templates" "$HOME/.claude/templates" >/dev/null
    echo "stale_rc=$? stale_gone=$({ [ -L "$HOME/.claude/templates" ] || [ -e "$HOME/.claude/templates" ]; } && echo no || echo yes) target_intact=$(cat "$HOME/elsewhere/keep.txt" 2>/dev/null)"
  fi
)
echo "$OUT" | grep -q "empty_rc=0 backup_dir_exists=no" && ok "nothing there: proceed, no backup dir created" || bad "empty case: $OUT"
echo "$OUT" | grep -q "realdir_rc=0 dst_gone=yes saved=mine" && ok "real directory moved into the backup, contents intact" || bad "real-dir case: $OUT"
echo "$OUT" | grep -q "realfile_rc=0 saved_file=my notes" && ok "real file moved into the backup, contents intact" || bad "real-file case: $OUT"
# backups= lists what the run backed up: only the two real items, never the links.
echo "$OUT" | grep -q "samelink_rc=0 link_gone=yes target_intact=repo backups=CLAUDE.md,skills," \
  && ok "a link already pointing at the target is removed, not backed up; its target survives" || bad "same-target link case: $OUT"
echo "$OUT" | grep -q "stale_rc=0 stale_gone=yes target_intact=keep" && ok "a stale link is removed and its target's contents survive" || bad "stale-link case: $OUT"
rm -rf "$TMPHOME"

# --- Scenario 5: restore_setup_backup copies originals back, and converges ---
echo "[5] restore_setup_backup undoes a run's replacements and is idempotent"
TMPHOME=$(mktemp -d)
OUT=$(
  export HOME="$TMPHOME"
  export SETUP_BACKUP_DIR="$TMPHOME/.claude/backups/setup-test"
  source "$SETUP_SCRIPT"
  set +e
  mkdir -p "$HOME/repo/claude/skills" "$HOME/.claude/skills"
  echo "repo" > "$HOME/repo/claude/skills/from-repo.txt"
  echo "mine" > "$HOME/.claude/skills/mine.txt"
  echo "my notes" > "$HOME/.claude/CLAUDE.md"
  prepare_link_target "$HOME/repo/claude/skills" "$HOME/.claude/skills" >/dev/null
  prepare_link_target "$HOME/repo/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md" >/dev/null
  # What setup would have linked in its place.
  make_dir_link "$HOME/repo/claude/skills" "$HOME/.claude/skills" && echo "linked_before=yes"

  restore_setup_backup "$SETUP_BACKUP_DIR" >/dev/null
  echo "rc1=$? skills_real=$([ -L "$HOME/.claude/skills" ] && echo no || echo yes) mine=$(cat "$HOME/.claude/skills/mine.txt" 2>/dev/null) notes=$(cat "$HOME/.claude/CLAUDE.md" 2>/dev/null) repo_intact=$(cat "$HOME/repo/claude/skills/from-repo.txt" 2>/dev/null) backup_kept=$([ -f "$SETUP_BACKUP_DIR/skills/mine.txt" ] && echo yes || echo no)"

  SECOND=$(restore_setup_backup "$SETUP_BACKUP_DIR")
  echo "rc2=$? skipped=$(echo "$SECOND" | grep -c Skipped)"
)
echo "$OUT" | grep -q "linked_before=yes" && echo "$OUT" | grep -q "rc1=0 skills_real=yes mine=mine notes=my notes repo_intact=repo backup_kept=yes" \
  && ok "originals restored over setup's link, the repo target untouched, the backup kept as the anchor" || bad "restore: $OUT"
echo "$OUT" | grep -q "rc2=0 skipped=2" && ok "a second restore converges (skips both, no error)" || bad "second restore: $OUT"
rm -rf "$TMPHOME"

# --- Scenario 6: set_hooks_path saves a different prior global value; --restore puts it back ---
echo "[6] set_hooks_path backs up a prior global core.hooksPath"
TMPHOME=$(mktemp -d)
OUT=$(
  export HOME="$TMPHOME"
  export GIT_CONFIG_GLOBAL="$TMPHOME/gitconfig"
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
)
echo "$OUT" | grep -q "saved=D:/prior/hooks now_ours=1" && ok "prior value saved before core.hooksPath is overwritten" || bad "save: $OUT"
echo "$OUT" | grep -q "rerun_backup=no" && ok "a re-run that finds its own value saves nothing" || bad "rerun: $OUT"
echo "$OUT" | grep -q "restored=D:/prior/hooks" && ok "--restore puts the prior global value back" || bad "restore hooksPath: $OUT"
rm -rf "$TMPHOME"

# --- Scenario 7: same_path sees one directory through its 8.3 short and long spellings ---
# same_path now only decides whether a prior global core.hooksPath is worth saving. It checks
# file identity first, so one existing directory reached through two spellings is the same
# path, and a different directory is not.
echo "[7] same_path matches an existing directory across 8.3 short/long spellings"
case "$(uname -s)" in
  MINGW*|CYGWIN*|MSYS*)
    TMPHOME=$(mktemp -d)
    LONGDIR="$TMPHOME/long-directory-name-for-8dot3"
    mkdir -p "$LONGDIR"
    SHORTDIR=$(cygpath -u "$(cygpath -d "$LONGDIR")")
    if [ "$(basename "$SHORTDIR")" = "$(basename "$LONGDIR")" ]; then
      ok "no 8.3 short names on this volume -- nothing to compare"
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
    rm -rf "$TMPHOME" ;;
  *)
    ok "8.3 short names are Windows-only -- nothing to compare" ;;
esac

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
