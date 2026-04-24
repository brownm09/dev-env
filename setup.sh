#!/usr/bin/env bash
# dev-env setup — run once per machine after cloning this repo.
#
# Usage (Windows, Git Bash):  bash setup.sh
# Usage (Linux/macOS):        bash setup.sh
#
# Windows: self-elevates via UAC if neither Administrator nor Developer Mode
# is detected. No manual elevation step required.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Windows setup
# ---------------------------------------------------------------------------
setup_windows() {
  echo "dev-env setup (Windows) from $REPO_DIR"
  echo ""

  # -- Elevation / Developer Mode check ------------------------------------
  # mklink (file symlink) and mklink /D (dir symlink) require either
  # Administrator or Developer Mode. mklink /J (junction) works without both.
  # Self-elevate via UAC so the user never has to think about it.

  is_admin()    { net.exe session &>/dev/null 2>&1; }
  has_dev_mode() {
    local val
    val="$(reg.exe query \
      "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock" \
      /v AllowDevelopmentWithoutDevLicense 2>/dev/null \
      | tr -d '\r' | grep -oP '0x\w+' || echo "0x0")"
    [[ "$val" == "0x1" ]]
  }

  if ! is_admin && ! has_dev_mode; then
    SCRIPT_WIN="$(cygpath -w "${BASH_SOURCE[0]}")"
    echo "Requires elevation (Administrator or Developer Mode)."
    echo "Triggering UAC prompt — setup will complete in a new window..."
    # -Wait keeps this process alive until the elevated one finishes.
    powershell.exe -NoProfile -Command \
      "Start-Process 'bash' -ArgumentList '\"$SCRIPT_WIN\"' -Verb RunAs -Wait"
    exit 0
  fi

  # -- Soft prerequisites --------------------------------------------------
  # These don't block setup but will cause hooks to fail at runtime.

  if ! cmd.exe /c "where bash >NUL 2>&1"; then
    echo "WARNING: bash.exe not on Windows PATH."
    echo "  Add Git Bash: C:\\Program Files\\Git\\usr\\bin"
    echo "  Claude Code hooks use 'bash -c ...' and won't fire until this is fixed."
    echo ""
  fi

  if ! python3 --version &>/dev/null; then
    echo "WARNING: python3 not found."
    echo "  Install from https://python.org/downloads/ (tick 'Add python.exe to PATH')."
    echo "  Hook scripts in claude/scripts/ won't run until this is fixed."
    echo ""
  fi

  # -- ~/.claude layout ----------------------------------------------------
  mkdir -p "$HOME/.claude"
  echo "Creating ~/.claude layout..."

  win_link "$REPO_DIR/claude/CLAUDE.md"    "$HOME/.claude/CLAUDE.md"    file
  echo "  Linked CLAUDE.md"

  win_link "$REPO_DIR/claude/settings.json" "$HOME/.claude/settings.json" file
  echo "  Linked settings.json"

  for subdir in scripts skills hooks; do
    win_link "$REPO_DIR/claude/$subdir" "$HOME/.claude/$subdir" dir
    echo "  Linked $subdir/"
  done

  mkdir -p "$HOME/.claude/scratch"
  echo "  Created scratch/"

  win_link "$REPO_DIR/bin" "$HOME/bin" junction
  echo "  Linked ~/bin/"

  set_hooks_path

  echo ""
  echo "Done. Open a new Git Bash window so ~/bin is on PATH."
}

# win_link <target> <link> <type: file|dir|junction>
win_link() {
  local src="$1" dst="$2" type="$3"
  local src_win dst_win flag

  src_win="$(cygpath -w "$src")"
  dst_win="$(cygpath -w "$dst")"

  case "$type" in
    file)     flag="" ;;
    dir)      flag="/D" ;;
    junction) flag="/J" ;;
  esac

  rm -f "$dst" 2>/dev/null || true
  if [ -d "$dst" ]; then
    cmd.exe /c "rmdir \"$dst_win\"" 2>/dev/null || rm -rf "$dst"
  fi

  cmd.exe /c "mklink $flag \"$dst_win\" \"$src_win\""
}

# ---------------------------------------------------------------------------
# Linux / macOS setup
# ---------------------------------------------------------------------------
setup_unix() {
  echo "dev-env setup ($(uname -s)) from $REPO_DIR"
  echo ""

  # settings.json contains Windows-specific absolute paths in hook commands.
  echo "NOTE: claude/settings.json has Windows paths in hook commands."
  echo "  Hooks will not fire correctly until those paths are updated for this OS."
  echo ""

  mkdir -p "$HOME/.claude"
  echo "Creating ~/.claude layout..."

  for item in CLAUDE.md settings.json; do
    ln -sf "$REPO_DIR/claude/$item" "$HOME/.claude/$item"
    echo "  Linked $item"
  done

  for subdir in scripts skills hooks; do
    ln -sf "$REPO_DIR/claude/$subdir" "$HOME/.claude/$subdir"
    echo "  Linked $subdir/"
  done

  mkdir -p "$HOME/.claude/scratch"
  echo "  Created scratch/"

  ln -sf "$REPO_DIR/bin" "$HOME/bin"
  echo "  Linked ~/bin/"

  set_hooks_path

  echo ""
  echo "Done. Reload your shell so ~/bin is on PATH (or open a new terminal)."
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

  git config --global core.hooksPath "$HOME/.claude/hooks"
  echo "  Set core.hooksPath -> ~/.claude/hooks"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
  MINGW*|CYGWIN*|MSYS*) setup_windows ;;
  Linux|Darwin)          setup_unix ;;
  *)
    echo "Unsupported OS: $OS" >&2
    exit 1 ;;
esac
