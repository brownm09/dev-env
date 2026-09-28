#!/usr/bin/env bash
# dev-env setup — run once per machine after cloning this repo; safe to re-run.
#
# Usage (Windows, Git Bash):  bash setup.sh
# Usage (Linux/macOS):        bash setup.sh
# Undo a run's replacements:  bash setup.sh --restore ~/.claude/backups/setup-<timestamp>
#
# Windows: creating the ~/.claude symlinks needs Developer Mode (Settings > System >
# For developers) or an elevated Git Bash. Without either, setup stops and says so --
# it no longer relaunches itself through UAC (ADR-041, dev-env#1114).
#
# Nothing is deleted: a real file or directory already sitting where a link belongs is
# moved to ~/.claude/backups/setup-<timestamp>/ first, and --restore copies it back
# (ADR-079). Adding a second machine: docs/REFERENCE.md -> "Adding a second machine".

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Files and directories linked from claude/ into ~/.claude/ on every platform
# (docs/adr/003-config-in-version-control.md). Shared by setup_windows() and
# setup_unix() so the two platforms' loops can't silently diverge the way
# `templates` did (dev-env#606) -- one list, iterated twice.
#
# settings.json is deliberately NOT in this list (dev-env#1049, ADR-139). The Claude
# Code app writes ~/.claude/settings.json itself -- /config theme changes, `tui`,
# notification flags, the `autoMode` environment scan -- so symlinking it into the repo
# made the app dirty a tracked file, and git then refused to fast-forward the canonical
# checkout, forever. It is now a real, machine-local file seeded and kept current by
# seed_claude_settings() below.
CLAUDE_FILE_LINKS=(CLAUDE.md)
CLAUDE_DIR_LINKS=(scripts skills hooks templates)

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

  if "${runner[@]}" "$REPO_DIR/claude/scripts/_settings_sync.py"; then
    echo "  Seeded settings.json (machine-local; see ADR-139)"
  else
    echo "  WARNING: seeding ~/.claude/settings.json failed. Re-run manually:"
    echo "    ${runner[*]} $REPO_DIR/claude/scripts/_settings_sync.py"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Windows setup
# ---------------------------------------------------------------------------
setup_windows() {
  echo "dev-env setup (Windows) from $REPO_DIR"
  echo ""

  # -- Elevation / Developer Mode check ------------------------------------
  # mklink (file symlink) and mklink /D (dir symlink) require either
  # Administrator or Developer Mode. mklink /J (junction) works without both.
  # Fail fast with the fix instead of relaunching through UAC: in an agent-driven or
  # non-interactive session the UAC dialog has no desktop to render against, so a
  # self-relaunch hangs or dies silently (ADR-041, dev-env#1114).

  is_admin()    { net.exe session &>/dev/null 2>&1; }
  has_dev_mode() {
    local val
    # MSYS_NO_PATHCONV=1: without it Git Bash rewrites the `/v` switch into a path, reg.exe
    # rejects the query ("Invalid syntax", hidden by 2>/dev/null), and Developer Mode never
    # registers -- the dev-env#602 class. ERE rather than `grep -P`, which refuses to run
    # outside a UTF-8 locale (agent sessions). Both fixed in dev-env#1114.
    val="$(MSYS_NO_PATHCONV=1 reg.exe query \
      "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock" \
      /v AllowDevelopmentWithoutDevLicense 2>/dev/null \
      | tr -d '\r' | grep -oE '0x[0-9a-fA-F]+' || echo "0x0")"
    [[ "$val" == "0x1" ]]
  }

  if ! is_admin && ! has_dev_mode; then
    echo "ERROR: creating the ~/.claude symlinks needs Developer Mode or an elevated shell." >&2
    echo "  Enable Developer Mode (Settings > System > For developers > Developer Mode)," >&2
    echo "  or open Git Bash with 'Run as administrator' -- then re-run: bash setup.sh" >&2
    exit 1
  fi

  # -- Soft prerequisites --------------------------------------------------
  # These don't block setup, but hooks or the workflow fail at runtime without them.
  # dev-env-doctor.py, run at the end, re-checks all of them.

  if ! cmd.exe /c "where bash >NUL 2>&1"; then
    echo "WARNING: bash.exe not on Windows PATH."
    echo "  Add Git Bash: C:\\Program Files\\Git\\usr\\bin"
    echo "  Claude Code's Bash tool and dev-env's *.sh scripts run under Git Bash."
    echo ""
  fi

  if ! py -3 --version &>/dev/null; then
    echo "WARNING: 'py -3' not found (Windows Python Launcher)."
    echo "  Install from https://python.org/downloads/ (tick 'Install launcher for all users')."
    echo "  Hook scripts in claude/scripts/ won't run until this is fixed."
    echo "  Note: 'python3' on Windows usually resolves to the Microsoft Store stub — use 'py -3'."
    echo ""
  fi

  if ! command -v pyw >/dev/null 2>&1; then
    echo "WARNING: 'pyw' (the windowless Python launcher) not found."
    echo "  Every hook command runs 'pyw -3 ...' (ADR-007) and fails without it."
    echo "  Reinstall Python from https://python.org/downloads/ with the launcher enabled."
    echo ""
  fi

  if ! command -v gh >/dev/null 2>&1; then
    echo "WARNING: GitHub CLI 'gh' not found. Install it from https://cli.github.com/, then run:"
    echo "  gh auth login && gh auth setup-git && gh auth refresh -s project"
    echo ""
  elif ! gh auth status >/dev/null 2>&1; then
    echo "WARNING: gh is not signed in. Run:"
    echo "  gh auth login && gh auth setup-git && gh auth refresh -s project"
    echo ""
  fi

  if ! command -v node >/dev/null 2>&1; then
    echo "WARNING: node not found. Install nvm for Windows, then: nvm install 20.11.1 && nvm use 20.11.1"
    echo ""
  fi

  if [ -z "$(git config --global user.email || true)" ]; then
    echo "WARNING: git identity not set. Run: git config --global user.name \"<name>\""
    echo "  and: git config --global user.email \"<email>\""
    echo ""
  fi

  link_claude_windows

  set_hooks_path

  echo ""
  echo "Verifying the install with dev-env-doctor (read-only)..."
  if command -v py >/dev/null 2>&1; then
    py -3 "$REPO_DIR/claude/scripts/dev-env-doctor.py" || \
      echo "Fix the FAIL lines above, then re-run: py -3 ~/.claude/scripts/dev-env-doctor.py"
  fi

  echo ""
  echo "Done. Open a new Git Bash window so ~/bin is on PATH."
}

# win_link <target> <link> <type: file|dir|junction>
# prepare_link_target first clears <link>: an existing link is removed (never its target)
# and a real file or directory is backed up, never deleted. Then mklink recreates it.
win_link() {
  local src="$1" dst="$2" type="$3"
  local src_win dst_win flag

  if ! prepare_link_target "$src" "$dst"; then
    echo "ERROR: could not clear $dst for linking -- nothing was replaced or deleted." >&2
    exit 1
  fi

  src_win="$(cygpath -w "$src")"
  dst_win="$(cygpath -w "$dst")"

  case "$type" in
    file)     flag="" ;;
    dir)      flag="/D" ;;
    junction) flag="/J" ;;
  esac

  cmd.exe /c "mklink $flag \"$dst_win\" \"$src_win\""

  # Read-back (ADR-079 rule 4): the new link must exist and resolve. Deliberately not a
  # comparison of where it points -- see prepare_link_target.
  if [ ! -e "$dst" ]; then
    echo "ERROR: $dst does not resolve after mklink." >&2
    exit 1
  fi
}

# Where this run moves anything real that a link would otherwise replace. Computed once per
# run and created lazily: a re-run over an existing layout only replaces links, so it
# creates no backup directory at all.
SETUP_BACKUP_DIR="${SETUP_BACKUP_DIR:-$HOME/.claude/backups/setup-$(date +%Y%m%d-%H%M%S)}"

# same_path <a> <b> -- whether two paths name the same location. Used only to decide
# whether a prior global core.hooksPath is worth saving, where a wrong "different" costs
# nothing more than a redundant backup file.
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
# `rmdir` removes the junction without touching its target's contents. Returns 2 if the
# link is still there afterwards.
remove_link() {
  local dst="$1"
  rm -f "$dst" 2>/dev/null || true
  if [ -L "$dst" ]; then
    case "$(uname -s)" in
      MINGW*|CYGWIN*|MSYS*) cmd.exe /c "rmdir \"$(cygpath -w "$dst")\"" >/dev/null 2>&1 || true ;;
    esac
  fi
  if [ -L "$dst" ]; then
    echo "ERROR: could not remove the stale link at $dst" >&2
    return 2
  fi
  return 0
}

# prepare_link_target <target> <link> -- clear the way for (re)linking <link> to <target>.
#   0  the caller should create the link: nothing was there, an existing link was removed,
#      or a real file/directory was moved into $SETUP_BACKUP_DIR
#   2  could not clear it -- the caller must abort. A real item that cannot be captured
#      is never replaced: changing state you could not back up leaves no way back
#      (global "Back up before you mutate", ADR-079 rule 1).
#
# An existing link is removed whether or not it already points at <target>: a link is not
# data, and recreating it is exactly what setup did before dev-env#1114. Deciding "already
# correct" means comparing where a link points, and Git for Windows runtimes disagree about
# how they report that -- on the GitHub runner a correct junction came back as "different"
# and the check misfired. Removing and recreating needs no such comparison.
prepare_link_target() {
  local src="$1" dst="$2" saved
  : "$src"  # the target is the caller's to link; clearing <link> doesn't need it
  if [ -L "$dst" ]; then
    remove_link "$dst" || return 2
    return 0
  fi
  if [ -e "$dst" ]; then
    saved="$SETUP_BACKUP_DIR/$(basename "$dst")"
    mkdir -p "$SETUP_BACKUP_DIR" || return 2
    mv "$dst" "$saved" || return 2
    # Read-back (ADR-079 rule 4): the original is now in the backup, and gone from $dst.
    if [ -e "$dst" ] || [ ! -e "$saved" ]; then
      echo "ERROR: backing up $dst to $saved did not complete." >&2
      return 2
    fi
    echo "  Backed up existing $(basename "$dst") -> $saved"
  fi
  return 0
}

# restore_setup_backup <backup-dir> -- undo one setup run's replacements. For each item the
# run captured, remove the link setup created in its place and copy the original back. It
# copies rather than moves, so the backup stays as the anchor and a repeated restore
# converges: an item whose destination is already a real file/dir is skipped (ADR-079
# rules 2-3). A saved global core.hooksPath is put back the same way.
restore_setup_backup() {
  local bdir="$1" item name dst prior
  if [ ! -d "$bdir" ]; then
    echo "ERROR: no backup directory at $bdir" >&2
    return 1
  fi
  for item in "$bdir"/*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    name="$(basename "$item")"
    if [ "$name" = "git-global-core.hooksPath" ]; then
      prior="$(cat "$item")"
      git config --global core.hooksPath "$prior"
      echo "  Restored global core.hooksPath -> $prior"
      continue
    fi
    if [ "$name" = "bin" ]; then
      dst="$HOME/bin"
    else
      dst="$HOME/.claude/$name"
    fi
    if [ -L "$dst" ]; then
      remove_link "$dst" || return 1
    fi
    if [ -e "$dst" ]; then
      echo "  Skipped $name (a real file or directory is already at $dst)"
      continue
    fi
    cp -a "$item" "$dst"
    echo "  Restored $name from $bdir"
  done
  return 0
}

# link_claude_windows -- create/refresh the ~/.claude junction/symlink layout
# and ~/bin from the shared CLAUDE_FILE_LINKS/CLAUDE_DIR_LINKS enumeration.
# Split out from setup_windows() so the enumeration is testable without the
# UAC elevation gate above -- see claude/scripts/tests/test-setup-link-loop.sh.
link_claude_windows() {
  mkdir -p "$HOME/.claude"
  echo "Creating ~/.claude layout..."

  for item in "${CLAUDE_FILE_LINKS[@]}"; do
    win_link "$REPO_DIR/claude/$item" "$HOME/.claude/$item" file
    echo "  Linked $item"
  done

  for subdir in "${CLAUDE_DIR_LINKS[@]}"; do
    win_link "$REPO_DIR/claude/$subdir" "$HOME/.claude/$subdir" dir
    echo "  Linked $subdir/"
  done

  # Read-only mirror so a routine can self-reference its own canonical source at
  # run time. Does NOT register scheduled tasks — the scheduled-tasks MCP tool owns
  # a separate, non-linked ~/.claude/scheduled-tasks/ directory. See ADR-003 amendment.
  win_link "$REPO_DIR/claude/routines" "$HOME/.claude/routines" junction
  echo "  Linked routines/ (junction)"

  mkdir -p "$HOME/.claude/scratch"
  echo "  Created scratch/"

  seed_claude_settings

  win_link "$REPO_DIR/bin" "$HOME/bin" junction
  echo "  Linked ~/bin/"
}

# ---------------------------------------------------------------------------
# Linux / macOS setup
# ---------------------------------------------------------------------------
setup_unix() {
  echo "dev-env setup ($(uname -s)) from $REPO_DIR"
  echo ""

  # settings.shared.json contains Windows-specific absolute paths in hook commands.
  echo "NOTE: claude/settings.shared.json has Windows paths in hook commands."
  echo "  Hooks will not fire correctly until those paths are updated for this OS."
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
  ln -sf "$src" "$dst"
}

# link_claude_unix -- create/refresh the ~/.claude symlink layout and ~/bin
# from the shared CLAUDE_FILE_LINKS/CLAUDE_DIR_LINKS enumeration -- see
# claude/scripts/tests/test-setup-link-loop.sh.
link_claude_unix() {
  mkdir -p "$HOME/.claude"
  echo "Creating ~/.claude layout..."

  for item in "${CLAUDE_FILE_LINKS[@]}"; do
    unix_link "$REPO_DIR/claude/$item" "$HOME/.claude/$item"
    echo "  Linked $item"
  done

  for subdir in "${CLAUDE_DIR_LINKS[@]}"; do
    unix_link "$REPO_DIR/claude/$subdir" "$HOME/.claude/$subdir"
    echo "  Linked $subdir/"
  done

  # Read-only mirror so a routine can self-reference its own canonical source at
  # run time. Does NOT register scheduled tasks — the scheduled-tasks MCP tool owns
  # a separate, non-linked ~/.claude/scheduled-tasks/ directory. See ADR-003 amendment.
  unix_link "$REPO_DIR/claude/routines" "$HOME/.claude/routines"
  echo "  Linked routines/"

  mkdir -p "$HOME/.claude/scratch"
  echo "  Created scratch/"

  seed_claude_settings

  unix_link "$REPO_DIR/bin" "$HOME/bin"
  echo "  Linked ~/bin/"
}

# ---------------------------------------------------------------------------
# Shared: configure global git hooks path
# ---------------------------------------------------------------------------
set_hooks_path() {
  local system_hooks
  system_hooks="$(git config --system core.hooksPath 2>/dev/null || true)"

  if [ -n "$system_hooks" ] && [ "$system_hooks" != "$HOME/.claude/hooks" ]; then
    echo ""
    echo "WARNING: system-level core.hooksPath already set to: $system_hooks"
    echo "  This may be enterprise-managed — skipping global hooks config."
    echo "  Set manually if safe: git config --global core.hooksPath ~/.claude/hooks"
    return
  fi

  # Save a different prior global value before overwriting it, so --restore can put it
  # back (ADR-079). same_path resolves both sides, since git stores this directory as
  # C:/... while Git Bash spells it /c/...
  local prior
  prior="$(git config --global core.hooksPath || true)"
  if [ -n "$prior" ] && ! same_path "$prior" "$HOME/.claude/hooks"; then
    mkdir -p "$SETUP_BACKUP_DIR"
    printf '%s\n' "$prior" > "$SETUP_BACKUP_DIR/git-global-core.hooksPath"
    echo "  Saved previous global core.hooksPath ($prior) to $SETUP_BACKUP_DIR/"
  fi

  git config --global core.hooksPath "$HOME/.claude/hooks"
  echo "  Set core.hooksPath -> ~/.claude/hooks"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
# Guarded so this file can be sourced by a test harness (which stubs win_link/
# ln and calls link_claude_windows/link_claude_unix directly) without
# executing OS detection or the elevation gate -- see
# claude/scripts/tests/test-setup-link-loop.sh.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
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
