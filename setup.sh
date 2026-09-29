#!/usr/bin/env bash
# dev-env setup — run once per machine after cloning this repo; safe to re-run.
#
# Usage (Windows, Git Bash):  bash setup.sh
# Usage (Linux/macOS):        bash setup.sh
# Undo one run:               bash setup.sh --restore ~/.claude/backups/setup-<timestamp>
#
# Windows: creating the ~/.claude symlinks needs Developer Mode (Settings > System > For
# developers), the "Create symbolic links" right, or an elevated Git Bash. Setup creates a
# throwaway link first and stops with that instruction if it can't -- it never relaunches
# itself through UAC (ADR-041, dev-env#1114).
#
# Nothing is lost (ADR-079). Into ~/.claude/backups/setup-<timestamp>/ go: any real file or
# directory that sat where a link belongs (moved, not deleted); where a replaced link pointed,
# when it pointed anywhere but dev-env; the settings.json the seed changed; and the previous
# global core.hooksPath, "unset" included. --restore puts all of it back. Adding a second
# machine: docs/REFERENCE.md -> "Adding a Second Machine".

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Everything setup links from this repo (docs/adr/003-config-in-version-control.md), named
# once: setup_windows() and setup_unix() iterate these arrays, so the platforms can't diverge
# the way `templates` once did (dev-env#606); --restore takes its allow-list from them; and
# test_dev_env_doctor.py pins dev-env-doctor.py's LINKED_ITEMS / HOME_LINKS against them.
#
# settings.json is deliberately NOT a link (dev-env#1049, ADR-139). The Claude Code app
# writes ~/.claude/settings.json itself -- /config theme changes, `tui`, notification flags,
# the `autoMode` environment scan -- so symlinking it into the repo made the app dirty a
# tracked file, and git then refused to fast-forward the canonical checkout, forever. It is
# now a real, machine-local file seeded and kept current by seed_claude_settings() below.
CLAUDE_FILE_LINKS=(CLAUDE.md)                     # ~/.claude/<name> -> claude/<name>, file symlink
CLAUDE_DIR_LINKS=(scripts skills hooks templates) # ~/.claude/<name> -> claude/<name>, directory symlink
CLAUDE_JUNCTION_LINKS=(routines)                  # ~/.claude/<name> -> claude/<name>, junction on Windows
HOME_LINKS=(bin)                                  # ~/<name> -> <name>, junction on Windows

# Where this run puts anything it replaces. Computed once per run and created lazily: a
# re-run that changes nothing creates no backup directory at all.
SETUP_BACKUP_DIR="${SETUP_BACKUP_DIR:-$HOME/.claude/backups/setup-$(date +%Y%m%d-%H%M%S)}"

# settings_home -- the home directory the tracked hook commands are written for (the prefix
# of the first ~/.claude path in claude/settings.shared.json, e.g. C:/Users/brown), or
# nothing when no hook names one.
settings_home() {
  { grep -o -E '[A-Za-z]:/Users/[^/"]+/\.claude/' "$REPO_DIR/claude/settings.shared.json" || true; } \
    | sed -n '1{s#/\.claude/$##;p;}'
}

# settings_home_mismatch -- why the tracked hook commands can't run here (their scripts sit
# under another home), or nothing when they can. A hook whose script is missing exits 2,
# which blocks every prompt, so seeding them under another home would stop every session.
# dev-env#1113 makes the paths per-machine; until it lands, such a machine isn't seeded.
settings_home_mismatch() {
  local want have
  want="$(settings_home)"
  [ -n "$want" ] || return 0
  case "$(uname -s)" in
    MINGW*|CYGWIN*|MSYS*)
      have="$(cygpath -m "$HOME")"
      [ "${want,,}" = "${have,,}" ] && return 0 ;;
    *)
      have="$HOME"
      [ "$want" = "$have" ] && return 0 ;;
  esac
  echo "its hook commands run scripts under $want, but this home is $have"
}

# seed_claude_settings -- materialize/refresh the real, machine-local
# ~/.claude/settings.json from the tracked claude/settings.shared.json. Idempotent:
# replaces only the keys dev-env owns (hooks, permissions), seeds absent defaults, and
# leaves every app-written key alone. Also migrates an existing repo symlink left by a
# pre-ADR-139 setup run. Same code path dev-env-sync.py runs on every prompt.
#
# Never fails the run: the symlinks above are the critical half of setup, and a seed that
# cannot complete (no python, an unreadable shared file) must warn with the exact manual
# command rather than abort setup with the links half-applied. `return 0` is explicit for
# the same reason -- these functions run under `set -e`.
seed_claude_settings() {
  local reason live="$HOME/.claude/settings.json" before=""
  reason="$(settings_home_mismatch)"
  if [ -n "$reason" ]; then
    echo "  WARNING: not seeding ~/.claude/settings.json: $reason."
    echo "  A hook whose script is missing blocks every prompt, so sessions here would not start."
    echo "  dev-env#1113 makes these paths per-machine; until it lands, this machine runs without dev-env's hooks."
    return 0
  fi

  # An array, not a string: "py -3" as a bare string would only work by relying on
  # unquoted word-splitting at the call site, which breaks the moment any element
  # contains a space. shellcheck would flag that (SC2086) -- but `run-shellcheck.sh`
  # (Testing item 7) SKIPs when shellcheck is not installed, so this file cannot count
  # on the lint catching it.
  local -a runner=()
  if command -v py >/dev/null 2>&1; then
    runner=(py -3)
  elif command -v python3 >/dev/null 2>&1; then
    runner=(python3)
  fi

  if [[ ${#runner[@]} -eq 0 ]]; then
    echo "  WARNING: no python found -- ~/.claude/settings.json not seeded."
    echo "  Run this once python is available:"
    echo "    py -3 $REPO_DIR/claude/scripts/_settings_sync.py"
    return 0
  fi

  # Copy the live file aside before the sync, and keep the copy only if the sync changed it.
  # --restore puts it back: restoring a real ~/.claude/scripts under settings that still run
  # dev-env's hooks from there would leave every hook's script missing (ADR-079 rules 1-2).
  if [ -f "$live" ] && [ ! -L "$live" ]; then
    if ! before="$(mktemp "$HOME/.claude/.settings-before-XXXXXX")" || ! cp -p "$live" "$before"; then
      echo "  WARNING: could not copy ~/.claude/settings.json aside first, so it was not seeded."
      [ -z "$before" ] || rm -f "$before"
      return 0
    fi
  fi

  if "${runner[@]}" "$REPO_DIR/claude/scripts/_settings_sync.py"; then
    echo "  Seeded settings.json (machine-local; see ADR-139)"
  else
    echo "  WARNING: seeding ~/.claude/settings.json failed. Re-run manually:"
    echo "    ${runner[*]} $REPO_DIR/claude/scripts/_settings_sync.py"
  fi

  if [ -n "$before" ]; then
    if cmp -s "$before" "$live"; then
      rm -f "$before"
    elif keep_in_backup "$before" settings.json; then
      echo "  Backed up the previous settings.json -> $SETUP_BACKUP_DIR/settings.json"
    else
      echo "  WARNING: could not file the previous settings.json in the backup; it is at $before"
    fi
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Windows setup
# ---------------------------------------------------------------------------
setup_windows() {
  echo "dev-env setup (Windows) from $REPO_DIR"
  echo ""

  preflight_windows || exit 1

  link_claude_windows

  set_hooks_path

  # The doctor owns the prerequisite list (py, pyw, gh and its auth, node, git identity, the
  # credential helper), so setup doesn't keep a second copy that drifts from it.
  echo ""
  echo "Verifying the install with dev-env-doctor (read-only)..."
  if ! command -v py >/dev/null 2>&1; then
    echo "Could not run the doctor: 'py' is not on PATH. Install Python with the py launcher"
    echo "(python.org, 'Install launcher for all users'), then run:"
    echo "  py -3 ~/.claude/scripts/dev-env-doctor.py"
    exit 1
  fi
  if ! py -3 "$REPO_DIR/claude/scripts/dev-env-doctor.py"; then
    echo ""
    echo "Setup made its changes, but the install isn't healthy yet. Fix the FAIL lines above,"
    echo "then re-run: py -3 ~/.claude/scripts/dev-env-doctor.py"
    exit 1
  fi

  echo ""
  echo "Done. Open a new Git Bash window so ~/bin is on PATH."
}

# preflight_windows -- everything that must hold before setup changes anything, each failing
# fast with its fix, so a run either proceeds or leaves no trace.
preflight_windows() {
  local p

  # Claude Code and dev-env's Python scripts find ~/.claude through the Windows profile
  # (USERPROFILE), never through Git Bash's HOME; a Windows-level HOME variable splits the two.
  if [ -z "${USERPROFILE:-}" ] || ! same_path "$(cygpath -u "$USERPROFILE")" "$HOME"; then
    echo "ERROR: Git Bash's HOME ($HOME) is not your Windows profile (${USERPROFILE:-unset})." >&2
    echo "  Claude Code reads ~/.claude from the profile, so links made under HOME would never be used." >&2
    echo "  Re-run with the profile as HOME:  HOME=\"\$(cygpath -u \"\$USERPROFILE\")\" bash setup.sh" >&2
    return 1
  fi

  for p in "$HOME" "$REPO_DIR"; do
    if ! cmd_safe_path "$p"; then
      echo "ERROR: setup can't link under $(cygpath -w "$p")." >&2
      echo "  cmd.exe, which creates the links, reads one of  & | < > ^ % ! , ; =  in that path as" >&2
      echo "  syntax rather than as part of the name. Clone dev-env to a path without them; a" >&2
      echo "  profile path with one of them isn't supported yet." >&2
      return 1
    fi
  done

  if ! symlink_probe; then
    echo "ERROR: this shell can't create symlinks, which the ~/.claude layout needs." >&2
    echo "  Enable Developer Mode (Settings > System > For developers > Developer Mode)," >&2
    echo "  or open Git Bash with 'Run as administrator' -- then re-run: bash setup.sh" >&2
    return 1
  fi
}

# symlink_probe -- can this shell create the symlinks setup needs? Try it rather than infer
# it: the "Create symbolic links" right grants it without Developer Mode or elevation, and
# the registry probe this replaced failed silently under Git Bash (dev-env#1114). On
# failure, prints mklink's own message.
symlink_probe() {
  local probe="$HOME/.claude/.setup-symlink-probe-$$-$RANDOM" out
  mkdir -p "$HOME/.claude"
  if ! out="$(win_cmd mklink /D "$(cygpath -w "$probe")" "$(cygpath -w "$REPO_DIR")" 2>&1 </dev/null)"; then
    printf '%s\n' "$out" | tr -d '\r' | sed '/^[[:space:]]*$/d; s/^/  mklink: /' >&2
    return 1
  fi
  remove_link "$probe" >/dev/null || echo "WARNING: could not remove the probe link $probe" >&2
  return 0
}

# cmd_safe_path <path> -- whether cmd.exe reads <path> as one literal argument. The MSYS
# runtime quotes an argument only when it holds a space or a double quote, so cmd sees
# & | < > ^ % ! as syntax and , ; = as separators; spaces and parentheses are fine.
cmd_safe_path() {
  local w="$1"
  if command -v cygpath >/dev/null 2>&1; then
    w="$(cygpath -w "$1")"
  fi
  case "$w" in
    *['&|<>^%!,;=']*) return 1 ;;
  esac
  return 0
}

# win_link <target> <link> <type: file|dir|junction>
# prepare_link_target first clears <link>: an existing link is removed (never its target),
# and a real file or directory is backed up, never deleted. Then mklink recreates it.
win_link() {
  local src="$1" dst="$2" type="$3" out
  local -a flag=()

  if ! prepare_link_target "$src" "$dst"; then
    echo "ERROR: could not clear $dst for linking -- nothing was replaced or deleted." >&2
    exit 1
  fi

  case "$type" in
    file)     ;;
    dir)      flag=(/D) ;;
    junction) flag=(/J) ;;
  esac

  if ! out="$(win_cmd mklink "${flag[@]}" "$(cygpath -w "$dst")" "$(cygpath -w "$src")" 2>&1 </dev/null)"; then
    link_failed "$dst" "$(printf '%s' "$out" | tr -d '\r')"
  fi

  # Read-back (ADR-079 rule 4): the new link must exist and resolve.
  if [ ! -e "$dst" ]; then
    link_failed "$dst" "mklink reported success, but the link does not resolve"
  fi
}

# link_failed <link> <why> -- stop, and say where things are: whatever setup already moved
# aside is in the backup directory, and --restore puts it back.
link_failed() {
  echo "ERROR: could not create the link $1: $2" >&2
  if [ -d "$SETUP_BACKUP_DIR" ]; then
    echo "  What this run moved aside is in $SETUP_BACKUP_DIR" >&2
    echo "  To put it back: bash \"$REPO_DIR/setup.sh\" --restore \"$SETUP_BACKUP_DIR\"" >&2
  fi
  echo "  Or fix the cause above and re-run: bash setup.sh" >&2
  exit 1
}

# win_cmd <command> [args...] -- run one cmd.exe command (mklink, rmdir). Both rules below
# were learned from CI on Git for Windows 2.55 (dev-env#1114); 2.37 happened to tolerate
# breaking either one.
#   - Path conversion is off. Otherwise Git Bash rewrites cmd's lone `/c` switch into a
#     drive path, so cmd.exe starts an interactive shell, prints its banner, and runs
#     nothing (the #602 class). MSYS_NO_PATHCONV is Git for Windows' switch;
#     MSYS2_ARG_CONV_EXCL is upstream MSYS2's.
#   - Every argument is passed separately, never as one pre-quoted string. 2.55 escapes
#     embedded quotes as \", which cmd.exe does not understand ("The filename, directory
#     name, or volume label syntax is incorrect"); the runtime quotes an argument itself
#     when it holds a space. It does not quote cmd's own metacharacters, so callers pass
#     only paths cmd_safe_path accepts.
# Callers pass paths already in Windows form (cygpath -w).
win_cmd() {
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' cmd.exe /c "$@"
}

# same_path <a> <b> -- whether two paths name the same location. Decides whether a prior
# global core.hooksPath is worth saving and whether HOME is the Windows profile.
#
# Identity first: when the path exists, `-ef` asks whether both names reach the same file
# on disk, whatever the spelling -- an 8.3 short name, /c/... vs C:/..., an MSYS mount
# such as /tmp. A path that doesn't exist has no identity, so spellings are compared
# instead: links resolved, then on Windows in C:/ form with long names,
# case-insensitively.
same_path() {
  if [ -e "$1" ] && [ "$1" -ef "$2" ]; then
    return 0
  fi
  local a b
  a="$(readlink -f "$1" 2>/dev/null || printf '%s' "$1")"
  b="$(readlink -f "$2" 2>/dev/null || printf '%s' "$2")"
  case "$(uname -s)" in
    MINGW*|CYGWIN*|MSYS*)
      a="$(cygpath -m -l "$a" 2>/dev/null || printf '%s' "$a")"
      b="$(cygpath -m -l "$b" 2>/dev/null || printf '%s' "$b")"
      [ "${a,,}" = "${b,,}" ] ;;
    *)
      [ "$a" = "$b" ] ;;
  esac
}

# remove_link <link> -- remove a symlink or junction itself, never what it points to.
# `rm -f` (no -r) unlinks a symlink; a Windows directory junction can survive it, and
# `rmdir` removes the junction without touching its target's contents -- but only through a
# path cmd.exe reads literally, or it acts on another path. Returns 2 if the link is still
# there afterwards.
remove_link() {
  local dst="$1"
  rm -f "$dst" 2>/dev/null || true
  if [ -L "$dst" ]; then
    case "$(uname -s)" in
      MINGW*|CYGWIN*|MSYS*)
        if cmd_safe_path "$dst"; then
          win_cmd rmdir "$(cygpath -w "$dst")" >/dev/null 2>&1 || true
        fi ;;
    esac
  fi
  if [ -L "$dst" ]; then
    echo "ERROR: could not remove the stale link at $dst" >&2
    return 2
  fi
  return 0
}

# save_backup_record <name> <text> -- write a small record into this run's backup directory:
# written if absent, never overwriting an earlier capture, and read back (ADR-079 rules 3-4).
save_backup_record() {
  local saved="$SETUP_BACKUP_DIR/$1"
  if [ -e "$saved" ] || [ -L "$saved" ]; then
    echo "ERROR: $saved already exists; not overwriting an earlier backup." >&2
    return 1
  fi
  mkdir -p "$SETUP_BACKUP_DIR" || return 1
  printf '%s\n' "$2" > "$saved" || return 1
  if [ "$(cat "$saved")" != "$2" ]; then
    echo "ERROR: $saved did not read back." >&2
    return 1
  fi
}

# keep_in_backup <path> <name> -- move <path> into this run's backup directory as <name>:
# written if absent (mv onto an existing file overwrites it, and onto a directory nests
# inside it), then read back (ADR-079 rules 3-4).
keep_in_backup() {
  local saved="$SETUP_BACKUP_DIR/$2"
  if [ -e "$saved" ] || [ -L "$saved" ]; then
    echo "ERROR: $saved already exists; not overwriting an earlier backup." >&2
    return 1
  fi
  mkdir -p "$SETUP_BACKUP_DIR" || return 1
  mv "$1" "$saved" || return 1
  if [ -e "$1" ] || [ ! -e "$saved" ]; then
    echo "ERROR: moving $1 to $saved did not complete." >&2
    return 1
  fi
}

# prepare_link_target <target> <link> -- clear the way for (re)linking <link> to <target>.
#   0  the caller should create the link: nothing was there, an existing link was removed,
#      or a real file/directory was moved into $SETUP_BACKUP_DIR
#   2  could not clear it -- the caller must abort. Nothing that can't be captured is ever
#      replaced: changing state you could not back up leaves no way back (global "Back up
#      before you mutate", ADR-079 rule 1).
prepare_link_target() {
  local src="$1" dst="$2" name prior
  name="$(basename "$dst")"
  if [ -L "$dst" ]; then
    # A link that already reaches <target> is setup's own; it's recreated without comment. One
    # that points anywhere else is configuration -- a dotfiles repo, a synced folder -- so where
    # it pointed is recorded first, and --restore recreates it. Identity (-ef) decides, not
    # spelling; a dangling link has no identity, so it is recorded too.
    if ! [ "$dst" -ef "$src" ]; then
      prior="$(readlink "$dst")" && [ -n "$prior" ] || return 2
      save_backup_record "$name.link" "$prior" || return 2
      echo "  Replacing link $name (it pointed at $prior; recorded for --restore)"
    fi
    remove_link "$dst" || return 2
    return 0
  fi
  if [ -e "$dst" ]; then
    keep_in_backup "$dst" "$name" || return 2
    echo "  Backed up existing $name -> $SETUP_BACKUP_DIR/$name"
  fi
  return 0
}

# backup_dst <name> -- where an entry of a setup backup directory goes back to. Fails for a
# name setup never writes, which is how --restore refuses a directory it didn't make.
backup_dst() {
  local base="${1%.link}" n
  case "$1" in
    git-global-core.hooksPath|git-global-core.hooksPath.unset)
      echo "the global git config"; return 0 ;;
    settings.json)
      echo "$HOME/.claude/settings.json"; return 0 ;;
  esac
  for n in "${CLAUDE_FILE_LINKS[@]}" "${CLAUDE_DIR_LINKS[@]}" "${CLAUDE_JUNCTION_LINKS[@]}"; do
    if [ "$base" = "$n" ]; then echo "$HOME/.claude/$n"; return 0; fi
  done
  for n in "${HOME_LINKS[@]}"; do
    if [ "$base" = "$n" ]; then echo "$HOME/$n"; return 0; fi
  done
  return 1
}

# restore_setup_backup <backup-dir> -- undo one setup run: put back every real file or
# directory it moved aside, every link it replaced, the settings.json its seed changed, and
# the global core.hooksPath (unsetting it if it was unset). Links it created where nothing
# was stay. The backup is never modified -- it stays the anchor -- and a repeated restore
# converges (ADR-079 rules 2-3). Exits non-zero if anything could not be put back.
restore_setup_backup() {
  local bdir="$1" item name dst found=0 rc=0
  if [ ! -d "$bdir" ]; then
    echo "ERROR: no backup directory at $bdir" >&2
    return 1
  fi

  # Refuse, before changing anything, a directory setup didn't write -- passing
  # ~/.claude/backups itself, one level up, is an easy tab-completion slip.
  for item in "$bdir"/*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    name="$(basename "$item")"
    if ! backup_dst "$name" >/dev/null; then
      echo "ERROR: $bdir is not one setup run's backup ($name is nothing setup backs up)." >&2
      echo "  Pass a directory like ~/.claude/backups/setup-<timestamp>." >&2
      return 1
    fi
    found=1
  done
  if [ "$found" = 0 ]; then
    echo "ERROR: $bdir holds nothing setup backed up." >&2
    return 1
  fi

  for item in "$bdir"/*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    name="$(basename "$item")"
    dst="$(backup_dst "$name")"
    case "$name" in
      git-global-core.hooksPath)       restore_hooks_path "$(cat "$item")" || rc=1 ;;
      git-global-core.hooksPath.unset) restore_hooks_path "" || rc=1 ;;
      settings.json)                   restore_settings "$item" "$dst" || rc=1 ;;
      *.link)                          restore_link "$(cat "$item")" "$dst" || rc=1 ;;
      *)                               restore_item "$item" "$dst" || rc=1 ;;
    esac
  done
  if [ "$rc" != 0 ]; then
    echo "Restore incomplete -- see above. The backup in $bdir is unchanged." >&2
  fi
  return "$rc"
}

# same_tree <a> <b> -- identical files or trees, comparing links as links (dangling ones too).
same_tree() {
  diff -rq --no-dereference "$1" "$2" >/dev/null
}

# copy_into_place <src> <dst> -- copy <src> to <dst> (absent, or a file to replace). The copy
# is staged beside <dst> and renamed into place, so an interrupted restore never leaves a
# partial <dst> that a later run would take for a finished one; links inside stay links
# (winsymlinks:nativestrict) as the backup's mv kept them, and it's read back afterwards.
copy_into_place() {
  local src="$1" dst="$2" stage
  stage="$(mktemp -d "$(dirname "$dst")/.setup-restore-XXXXXX")" || return 1
  if ! MSYS="${MSYS:+$MSYS }winsymlinks:nativestrict" cp -a "$src" "$stage/item"; then
    echo "ERROR: could not copy $src back; $dst is unchanged. (A link inside it needs the same" >&2
    echo "  symlink right as setup: Developer Mode or an elevated shell.)" >&2
    rm -rf "$stage"
    return 1
  fi
  if ! mv "$stage/item" "$dst"; then
    echo "ERROR: could not move the restored copy into place at $dst" >&2
    rm -rf "$stage"
    return 1
  fi
  rmdir "$stage"
  if ! same_tree "$src" "$dst"; then
    echo "ERROR: $dst does not match $src after restoring it" >&2
    return 1
  fi
}

# restore_item <backup-item> <dst> -- put back one real file or directory setup moved aside.
restore_item() {
  local item="$1" dst="$2" name
  name="$(basename "$dst")"
  if [ -L "$dst" ]; then
    remove_link "$dst" || return 1
  fi
  if [ -e "$dst" ]; then
    if same_tree "$item" "$dst"; then
      echo "  Already restored $name"
      return 0
    fi
    echo "  Skipped $name: $dst holds something else, and restore never overwrites it" >&2
    return 1
  fi
  copy_into_place "$item" "$dst" || return 1
  echo "  Restored $name"
}

# make_link <target> <link> -- recreate a link the way setup makes them: on Windows a
# junction for a directory (or a target that no longer exists), a file symlink for a file.
make_link() {
  local target="$1" dst="$2"
  local -a flag=(/J)
  case "$(uname -s)" in
    MINGW*|CYGWIN*|MSYS*)
      if ! cmd_safe_path "$target" || ! cmd_safe_path "$dst"; then
        echo "ERROR: can't recreate $dst -> $target through cmd.exe (a special character in the path); recreate it by hand" >&2
        return 1
      fi
      if [ -f "$target" ]; then
        flag=()
      fi
      win_cmd mklink "${flag[@]}" "$(cygpath -w "$dst")" "$(cygpath -w "$target")" >/dev/null </dev/null || return 1 ;;
    *)
      ln -s "$target" "$dst" || return 1 ;;
  esac
  [ -L "$dst" ]
}

# restore_link <target> <link> -- put back a link setup replaced, from its recorded target.
restore_link() {
  local target="$1" dst="$2" name
  name="$(basename "$dst")"
  if [ -z "$target" ]; then
    echo "ERROR: the record for $name is empty" >&2
    return 1
  fi
  # Already back: the same recorded text, or -- whatever the spelling -- the same place.
  if [ -L "$dst" ] && { [ "$(readlink "$dst")" = "$target" ] || [ "$dst" -ef "$target" ]; }; then
    echo "  Already restored link $name"
    return 0
  fi
  if [ -L "$dst" ]; then
    remove_link "$dst" || return 1
  elif [ -e "$dst" ]; then
    echo "  Skipped link $name: a real file or directory is at $dst, and restore never overwrites one" >&2
    return 1
  fi
  make_link "$target" "$dst" || return 1
  echo "  Restored link $name -> $target"
}

# restore_settings <captured> <live> -- put back the settings.json this run's seed changed,
# after saving the current one alongside the other backups.
restore_settings() {
  local saved="$1" live="$2" keep
  if [ -f "$live" ] && cmp -s "$saved" "$live"; then
    echo "  Already restored settings.json"
    return 0
  fi
  if [ -e "$live" ]; then
    keep="$HOME/.claude/backups/pre-restore-$(date +%Y%m%d-%H%M%S)-settings.json"
    mkdir -p "$HOME/.claude/backups"
    if [ -e "$keep" ] || ! cp -p "$live" "$keep"; then
      echo "ERROR: could not save the current settings.json to $keep, so it was left in place" >&2
      return 1
    fi
    echo "  Saved the current settings.json -> $keep"
  fi
  copy_into_place "$saved" "$live" || return 1
  echo "  Restored settings.json"
}

# restore_hooks_path <value> -- put the global core.hooksPath back; "" means it was unset.
restore_hooks_path() {
  local now
  now="$(git config --global core.hooksPath || true)"
  if [ -z "$1" ]; then
    if [ -n "$now" ]; then
      git config --global --unset core.hooksPath || return 1
    fi
    if [ -n "$(git config --global core.hooksPath || true)" ]; then
      echo "ERROR: the global core.hooksPath is still set" >&2
      return 1
    fi
    echo "  Unset the global core.hooksPath (it was unset before setup)"
    return 0
  fi
  if [ "$now" != "$1" ]; then
    git config --global core.hooksPath "$1" || return 1
  fi
  if [ "$(git config --global core.hooksPath || true)" != "$1" ]; then
    echo "ERROR: the global core.hooksPath did not read back as $1" >&2
    return 1
  fi
  echo "  Restored global core.hooksPath -> $1"
}

# link_claude_windows -- create/refresh the ~/.claude junction/symlink layout and ~/bin from
# the shared link arrays. Split out from setup_windows() so the enumeration is testable
# without its preflight -- see claude/scripts/tests/test-setup-link-loop.sh.
link_claude_windows() {
  local item
  mkdir -p "$HOME/.claude"
  echo "Creating ~/.claude layout..."

  for item in "${CLAUDE_FILE_LINKS[@]}"; do
    win_link "$REPO_DIR/claude/$item" "$HOME/.claude/$item" file
    echo "  Linked $item"
  done

  for item in "${CLAUDE_DIR_LINKS[@]}"; do
    win_link "$REPO_DIR/claude/$item" "$HOME/.claude/$item" dir
    echo "  Linked $item/"
  done

  # Read-only mirror so a routine can self-reference its own canonical source at
  # run time. Does NOT register scheduled tasks — the scheduled-tasks MCP tool owns
  # a separate, non-linked ~/.claude/scheduled-tasks/ directory. See ADR-003 amendment.
  for item in "${CLAUDE_JUNCTION_LINKS[@]}"; do
    win_link "$REPO_DIR/claude/$item" "$HOME/.claude/$item" junction
    echo "  Linked $item/ (junction)"
  done

  mkdir -p "$HOME/.claude/scratch"
  echo "  Created scratch/"

  seed_claude_settings

  for item in "${HOME_LINKS[@]}"; do
    win_link "$REPO_DIR/$item" "$HOME/$item" junction
    echo "  Linked ~/$item/"
  done
}

# ---------------------------------------------------------------------------
# Linux / macOS setup
# ---------------------------------------------------------------------------
setup_unix() {
  echo "dev-env setup ($(uname -s)) from $REPO_DIR"
  echo ""

  link_claude_unix

  set_hooks_path

  echo ""
  echo "Done. Reload your shell so ~/bin is on PATH (or open a new terminal)."
}

# unix_link <target> <link> -- the POSIX counterpart of win_link: the same prepare step,
# then `ln -sf`. Clearing first also keeps a re-run from creating a nested link *inside* an
# existing directory link, which a bare `ln -sf` onto a symlinked directory does.
unix_link() {
  local src="$1" dst="$2"
  if ! prepare_link_target "$src" "$dst"; then
    echo "ERROR: could not clear $dst for linking -- nothing was replaced or deleted." >&2
    exit 1
  fi
  ln -sf "$src" "$dst" || link_failed "$dst" "ln -s failed"
}

# link_claude_unix -- create/refresh the ~/.claude symlink layout and ~/bin from the shared
# link arrays -- see claude/scripts/tests/test-setup-link-loop.sh.
link_claude_unix() {
  local item
  mkdir -p "$HOME/.claude"
  echo "Creating ~/.claude layout..."

  for item in "${CLAUDE_FILE_LINKS[@]}"; do
    unix_link "$REPO_DIR/claude/$item" "$HOME/.claude/$item"
    echo "  Linked $item"
  done

  for item in "${CLAUDE_DIR_LINKS[@]}"; do
    unix_link "$REPO_DIR/claude/$item" "$HOME/.claude/$item"
    echo "  Linked $item/"
  done

  # Read-only mirror so a routine can self-reference its own canonical source at
  # run time. Does NOT register scheduled tasks — the scheduled-tasks MCP tool owns
  # a separate, non-linked ~/.claude/scheduled-tasks/ directory. See ADR-003 amendment.
  for item in "${CLAUDE_JUNCTION_LINKS[@]}"; do
    unix_link "$REPO_DIR/claude/$item" "$HOME/.claude/$item"
    echo "  Linked $item/"
  done

  mkdir -p "$HOME/.claude/scratch"
  echo "  Created scratch/"

  seed_claude_settings

  for item in "${HOME_LINKS[@]}"; do
    unix_link "$REPO_DIR/$item" "$HOME/$item"
    echo "  Linked ~/$item/"
  done
}

# ---------------------------------------------------------------------------
# Shared: configure global git hooks path
# ---------------------------------------------------------------------------
set_hooks_path() {
  local hooks="$HOME/.claude/hooks" system_hooks prior prior_path
  # Store the C:/ form explicitly. Git for Windows reads a /c/... value as a path under its
  # own install directory, and only MSYS path conversion -- off whenever MSYS_NO_PATHCONV is
  # exported -- would otherwise translate it on the way in.
  if command -v cygpath >/dev/null 2>&1; then
    hooks="$(cygpath -m "$hooks")"
  fi

  system_hooks="$(git config --system --type=path core.hooksPath || true)"
  if [ -n "$system_hooks" ] && ! same_path "$system_hooks" "$hooks"; then
    echo ""
    echo "WARNING: system-level core.hooksPath already set to: $system_hooks"
    echo "  This may be enterprise-managed — skipping global hooks config."
    echo "  Set manually if safe: git config --global core.hooksPath \"$hooks\""
    return 0
  fi

  # Record the previous global value before overwriting it, so --restore can put it back --
  # "unset", the usual starting state, included (ADR-079 rules 1-2). The expanded form (~ and
  # all) is compared; the raw value is saved, so a restore writes back exactly what was there.
  prior="$(git config --global core.hooksPath || true)"
  prior_path="$(git config --global --type=path core.hooksPath || true)"
  if [ -z "$prior" ]; then
    save_backup_record git-global-core.hooksPath.unset "" || return 1
    echo "  Recorded that the global core.hooksPath was unset (--restore unsets it again)"
  elif ! same_path "$prior_path" "$hooks"; then
    save_backup_record git-global-core.hooksPath "$prior" || return 1
    echo "  Saved the previous global core.hooksPath ($prior) to $SETUP_BACKUP_DIR/"
  fi

  git config --global core.hooksPath "$hooks"
  if [ "$(git config --global core.hooksPath || true)" != "$hooks" ]; then
    echo "ERROR: the global core.hooksPath did not read back as $hooks" >&2
    return 1
  fi
  echo "  Set core.hooksPath -> $hooks"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
# Only when run, never when sourced: the test harness sources this file to call its
# functions. `return` succeeds only in a sourced file -- unlike comparing BASH_SOURCE with
# $0, which `bash -c 'source "$0"' setup.sh` satisfies, running the full setup (dev-env#1114).
if ! (return 0 2>/dev/null); then
  if [[ "${1:-}" == "--restore" ]]; then
    restore_setup_backup "${2:?usage: bash setup.sh --restore <backup-dir>}"
    exit $?
  fi
  OS="$(uname -s)"
  case "$OS" in
    MINGW*|CYGWIN*|MSYS*) setup_windows ;;
    Linux|Darwin)          setup_unix ;;
    *)
      echo "Unsupported OS: $OS" >&2
      exit 1 ;;
  esac
fi
