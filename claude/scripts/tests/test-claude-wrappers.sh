#!/usr/bin/env bash
# test-claude-wrappers.sh -- runs bin/claude and bin/claude.cmd against fixture
# install directories and asserts which claude.exe each one starts (dev-env#1142).
#
# Hermetic: LOCALAPPDATA points at a temp directory, and no real claude.exe runs, so
# no tokens are spent. The bash fixtures are shell scripts named claude.exe. A batch
# file can only start a real executable, so the cmd.exe fixtures are copies of
# cmd.exe, and probe.cmd makes the copy report its own path through %CMDCMDLINE%.
# The cmd.exe half is skipped where there is no cmd.exe; a skip is counted, not passed.
#
# Run: bash claude/scripts/tests/test-claude-wrappers.sh
set -u

REPO_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
WRAPPER="$REPO_ROOT/bin/claude"
WRAPPER_CMD="$REPO_ROOT/bin/claude.cmd"
CURRENT="Claude-Data/claude_pzs8sxrjxfjjc/claude-code"
OLDER="Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/claude-code"

PASS=0; FAIL=0; SKIP=0
ok()   { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad()  { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  skipped: $*"; SKIP=$((SKIP + 1)); }

TMPROOT=$(mktemp -d) || { echo "FATAL: mktemp -d failed"; exit 1; }
trap 'rm -rf "$TMPROOT"' EXIT

# A fresh LOCALAPPDATA with nothing installed.
new_root() { mktemp -d "$TMPROOT/root.XXXXXX"; }

HAS_CMD=false
if command -v cmd.exe >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then
  HAS_CMD=true
  REAL_CMD=$(cygpath -u "${COMSPEC:-C:/Windows/System32/cmd.exe}")
  PROBE_DIR="$TMPROOT/probe"
  mkdir -p "$PROBE_DIR"
  printf '@echo STARTED %%CMDCMDLINE%%\r\n@echo FIRST=%%~1\r\n@exit /b %%PROBE_EXIT%%\r\n' > "$PROBE_DIR/probe.cmd"
fi

# plant <root> <path under root>: install a fixture binary in both forms, each in its
# own tree under the root, because one file can't be both a script and a real executable.
plant() {
  local dir="$1/bash/$2"
  mkdir -p "$dir"
  cat > "$dir/claude.exe" <<'FAKE'
#!/usr/bin/env bash
echo "STARTED $0"
for arg in "$@"; do echo "ARG=$arg"; done
if [ -n "${FAKE_READ_STDIN:-}" ]; then echo "STDIN=$(cat)"; fi
if [ -n "${FAKE_DUMP_ENV:-}" ]; then env | cut -d= -f1 | LC_ALL=C sort; fi
exit "${FAKE_EXIT:-0}"
FAKE
  chmod +x "$dir/claude.exe"
  if [ "$HAS_CMD" = true ]; then
    dir="$1/cmd/$2"
    mkdir -p "$dir"
    cp "$REAL_CMD" "$dir/claude.exe"
  fi
}

# run_bash <root> [args...]: run bin/claude with LOCALAPPDATA at the root's bash tree.
run_bash() {
  local root="$1"; shift
  LOCALAPPDATA="$root/bash" bash "$WRAPPER" "$@" 2>"$TMPROOT/stderr"
}

# run_cmd <root> [args...]: run bin/claude.cmd with LOCALAPPDATA at the root's cmd tree.
# The default arguments make the fixture copy of cmd.exe run probe.cmd.
run_cmd() {
  local root="$1"; shift
  [ "$#" -gt 0 ] || set -- /c "$(cygpath -w "$PROBE_DIR/probe.cmd")"
  mkdir -p "$root/cmd"
  LOCALAPPDATA="$(cygpath -w "$root/cmd")" MSYS_NO_PATHCONV=1 \
    cmd.exe /c "$(cygpath -w "$WRAPPER_CMD")" "$@" 2>"$TMPROOT/stderr"
}

# Paths below come back in mixed slash styles, so compare them with slashes flattened.
flatten() { tr '\\' '/' | sed 's#//*#/#g'; }

# expect_pick <description> <root> <path that must have started>
expect_pick() {
  local desc="$1" root="$2" want="$3/claude.exe" out rc
  out=$(run_bash "$root"); rc=$?
  if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | flatten | grep -qF "$want"; then
    ok "bash: $desc"
  else
    bad "bash: $desc (rc=$rc, wanted $want, got: $out)"
  fi
  if [ "$HAS_CMD" != true ]; then
    skip "cmd: $desc (no cmd.exe here)"
    return
  fi
  out=$(run_cmd "$root"); rc=$?
  if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | flatten | grep -qF "$want"; then
    ok "cmd: $desc"
  else
    bad "cmd: $desc (rc=$rc, wanted $want, got: $out)"
  fi
}

echo "Which binary each layout resolves to"

root=$(new_root); plant "$root" "$CURRENT/2.1.317/a33aa5518497"
expect_pick "current location, hash layout" "$root" "$CURRENT/2.1.317/a33aa5518497"

root=$(new_root); plant "$root" "$CURRENT/2.1.200"
expect_pick "current location, flat layout" "$root" "$CURRENT/2.1.200"

root=$(new_root); plant "$root" "$OLDER/2.1.170"
expect_pick "older location, flat layout" "$root" "$OLDER/2.1.170"

root=$(new_root); plant "$root" "$OLDER/2.1.295/d2e29494ec83"
expect_pick "older location, hash layout" "$root" "$OLDER/2.1.295/d2e29494ec83"

root=$(new_root)
plant "$root" "$CURRENT/2.1.99/zzzz"
plant "$root" "$CURRENT/2.1.170/aaaa"
expect_pick "numeric sort: 2.1.170 beats 2.1.99, whatever the hash names" "$root" "$CURRENT/2.1.170/aaaa"

root=$(new_root)
plant "$root" "$CURRENT/2.1.99"
plant "$root" "$CURRENT/2.1.170/aaaa"
expect_pick "newer version in the hash layout beats older flat" "$root" "$CURRENT/2.1.170/aaaa"

root=$(new_root)
plant "$root" "$CURRENT/2.1.99/zzzz"
plant "$root" "$CURRENT/2.1.170"
expect_pick "newer flat version beats older in the hash layout" "$root" "$CURRENT/2.1.170"

root=$(new_root)
plant "$root" "$CURRENT/2.1.300"
plant "$root" "$OLDER/2.1.999"
expect_pick "current location wins even over a higher version in the older one" "$root" "$CURRENT/2.1.300"

root=$(new_root)
plant "$root" "$OLDER/2.1.170"
mkdir -p "$root/bash/$CURRENT/2.1.317/a33aa5518497" "$root/cmd/$CURRENT/2.1.317/a33aa5518497"
expect_pick "falls back when the current location holds no binary" "$root" "$OLDER/2.1.170"

root=$(new_root)
plant "$root" "$CURRENT/2.1.309/9078b9c1d466"
mkdir -p "$root/bash/$CURRENT/2.1.317/a33aa5518497" "$root/cmd/$CURRENT/2.1.317/a33aa5518497"
expect_pick "skips a newer version directory with no binary yet" "$root" "$CURRENT/2.1.309/9078b9c1d466"

root=$(new_root)
plant "$root" "$CURRENT/2.1.309"
plant "$root" "$CURRENT/temp"
plant "$root" "$CURRENT/9.9.9-staging"
expect_pick "ignores directories that aren't versions" "$root" "$CURRENT/2.1.309"

echo "Nothing installed"

root=$(new_root)
out=$(run_bash "$root"); rc=$?
err=$(cat "$TMPROOT/stderr")
if [ "$rc" -eq 1 ] && [ -z "$out" ]; then ok "bash: exit 1 with nothing on stdout"; else bad "bash: rc=$rc out=$out"; fi
case "$err" in
  *"could not find Claude Code binary"*"$CURRENT"*"$OLDER"*) ok "bash: stderr names both locations" ;;
  *) bad "bash: stderr was: $err" ;;
esac

if [ "$HAS_CMD" = true ]; then
  out=$(run_cmd "$root"); rc=$?
  err=$(flatten < "$TMPROOT/stderr")
  if [ "$rc" -eq 1 ] && [ -z "$out" ]; then ok "cmd: exit 1 with nothing on stdout"; else bad "cmd: rc=$rc out=$out"; fi
  case "$err" in
    *"could not find Claude Code binary"*"$CURRENT"*"$OLDER"*) ok "cmd: stderr names both locations" ;;
    *) bad "cmd: stderr was: $err" ;;
  esac
else
  skip "cmd: nothing installed (no cmd.exe here)"
fi

echo "The wrapper only finds and runs the binary"

root=$(new_root); plant "$root" "$CURRENT/2.1.317/a33aa5518497"

out=$(run_bash "$root" "two words" 'wow!' ''); rc=$?
if printf '%s\n' "$out" | grep -qxF 'ARG=two words' && printf '%s\n' "$out" | grep -qxF 'ARG=wow!' \
   && printf '%s\n' "$out" | grep -qxF 'ARG='; then
  ok "bash: arguments arrive intact"
else
  bad "bash: arguments (rc=$rc): $out"
fi

FAKE_EXIT=7 run_bash "$root" >/dev/null; rc=$?
if [ "$rc" -eq 7 ]; then ok "bash: binary's exit code comes back"; else bad "bash: exit code was $rc, wanted 7"; fi

out=$(echo piped | FAKE_READ_STDIN=1 run_bash "$root"); rc=$?
if printf '%s\n' "$out" | grep -qxF 'STDIN=piped'; then ok "bash: binary gets the caller's stdin"; else bad "bash: stdin (rc=$rc): $out"; fi

# The environment is what authentication reads, so it must arrive unchanged: same
# names as starting the binary directly, and a credential variable passes through.
exe="$root/bash/$CURRENT/2.1.317/a33aa5518497/claude.exe"
direct=$(LOCALAPPDATA="$root/bash" FAKE_DUMP_ENV=1 bash "$exe" | grep -v '^STARTED\|^_$')
wrapped=$(FAKE_DUMP_ENV=1 run_bash "$root" | grep -v '^STARTED\|^_$')
if [ -n "$direct" ] && [ "$direct" = "$wrapped" ]; then
  ok "bash: binary sees the same environment variable names as when started directly"
else
  bad "bash: environment differs: $(diff <(printf '%s\n' "$direct") <(printf '%s\n' "$wrapped"))"
fi

if [ "$HAS_CMD" = true ]; then
  out=$(run_cmd "$root" /c "$(cygpath -w "$PROBE_DIR/probe.cmd")" "two words"); rc=$?
  if printf '%s\n' "$out" | tr -d '\r' | grep -qxF 'FIRST=two words'; then
    ok "cmd: arguments arrive intact"
  else
    bad "cmd: arguments (rc=$rc): $out"
  fi

  out=$(run_cmd "$root" /c "echo wow!"); rc=$?
  if printf '%s\n' "$out" | tr -d '\r' | grep -qxF 'wow!'; then
    ok "cmd: a ! in the arguments survives"
  else
    bad "cmd: ! in arguments (rc=$rc): $out"
  fi

  PROBE_EXIT=7 run_cmd "$root" >/dev/null; rc=$?
  if [ "$rc" -eq 7 ]; then ok "cmd: binary's exit code comes back"; else bad "cmd: exit code was $rc, wanted 7"; fi

  # Same check as bash: `set` from the binary, against what it prints started directly.
  printf '@set\r\n' > "$PROBE_DIR/set.cmd"
  names() { tr -d '\r' | cut -d= -f1 | LC_ALL=C sort; }
  set_cmd=$(cygpath -w "$PROBE_DIR/set.cmd")
  exe=$(cygpath -w "$root/cmd/$CURRENT/2.1.317/a33aa5518497/claude.exe")
  direct=$(LOCALAPPDATA="$(cygpath -w "$root/cmd")" MSYS_NO_PATHCONV=1 cmd.exe /c "$exe" /c "$set_cmd" | names)
  wrapped=$(run_cmd "$root" /c "$set_cmd" | names)
  if [ -n "$direct" ] && [ "$direct" = "$wrapped" ]; then
    ok "cmd: binary sees the same environment variable names as when started directly"
  else
    bad "cmd: environment differs: $(diff <(printf '%s\n' "$direct") <(printf '%s\n' "$wrapped"))"
  fi
else
  skip "cmd: pass-through (no cmd.exe here)"
fi

echo "Line endings"

# cmd.exe can miss a label in an LF-only batch file, and a CRLF shebang breaks bash.
if [ "$(grep -c $'\r$' "$WRAPPER_CMD")" -eq "$(wc -l < "$WRAPPER_CMD")" ]; then
  ok "claude.cmd is CRLF"
else
  bad "claude.cmd has lines without CRLF"
fi
if grep -q $'\r' "$WRAPPER"; then bad "bin/claude has a CR"; else ok "bin/claude is LF"; fi

echo ""
echo "Results: $PASS passed, $SKIP skipped, $FAIL failed"
[ "$FAIL" -eq 0 ]
