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

# Files and directories linked from claude/ into ~/.claude/ on every platform
# (docs/adr/003-config-in-version-control.md). Shared by setup_windows() and
# setup_unix() so the two platforms' loops can't silently diverge the way
# `templates` did (dev-env#606) -- one list, iterated twice.
CLAUDE_FILE_LINKS=(CLAUDE.md settings.json)
CLAUDE_DIR_LINKS=(scripts skills hooks templates)

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

  if ! py -3 --version &>/dev/null; then
    echo "WARNING: 'py -3' not found (Windows Python Launcher)."
    echo "  Install from https://python.org/downloads/ (tick 'Install launcher for all users')."
    echo "  Hook scripts in claude/scripts/ won't run until this is fixed."
    echo "  Note: 'python3' on Windows usually resolves to the Microsoft Store stub — use 'py -3'."
    echo ""
  fi

  link_claude_windows

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

  win_link "$REPO_DIR/bin" "$HOME/bin" junction
  echo "  Linked ~/bin/"
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

  link_claude_unix

  set_hooks_path

  echo ""
  echo "Done. Reload your shell so ~/bin is on PATH (or open a new terminal)."
}

# link_claude_unix -- create/refresh the ~/.claude symlink layout and ~/bin
# from the shared CLAUDE_FILE_LINKS/CLAUDE_DIR_LINKS enumeration -- see
# claude/scripts/tests/test-setup-link-loop.sh.
link_claude_unix() {
  mkdir -p "$HOME/.claude"
  echo "Creating ~/.claude layout..."

  for item in "${CLAUDE_FILE_LINKS[@]}"; do
    ln -sf "$REPO_DIR/claude/$item" "$HOME/.claude/$item"
    echo "  Linked $item"
  done

  for subdir in "${CLAUDE_DIR_LINKS[@]}"; do
    ln -sf "$REPO_DIR/claude/$subdir" "$HOME/.claude/$subdir"
    echo "  Linked $subdir/"
  done

  # Read-only mirror so a routine can self-reference its own canonical source at
  # run time. Does NOT register scheduled tasks — the scheduled-tasks MCP tool owns
  # a separate, non-linked ~/.claude/scheduled-tasks/ directory. See ADR-003 amendment.
  ln -sf "$REPO_DIR/claude/routines" "$HOME/.claude/routines"
  echo "  Linked routines/"

  mkdir -p "$HOME/.claude/scratch"
  echo "  Created scratch/"

  ln -sf "$REPO_DIR/bin" "$HOME/bin"
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
  OS="$(uname -s)"
  case "$OS" in
    MINGW*|CYGWIN*|MSYS*) setup_windows ;;
    Linux|Darwin)          setup_unix ;;
    *)
      echo "Unsupported OS: $OS" >&2
      exit 1 ;;
  esac
fi
