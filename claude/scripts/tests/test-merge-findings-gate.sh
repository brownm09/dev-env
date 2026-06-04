#!/bin/bash
# Behavioral test for pre-merge-findings-gate.py (ADR-028/ADR-039 merge gate).
#
# Drives the real hook with crafted stdin, using the hook's MERGE_GATE_TEST_JSON
# seam to supply canned `gh pr view --json` output, and asserts the gate's
# decision on each path:
#   - clean review (0 findings)            -> allow  (exit 0)
#   - open findings, no disposition        -> BLOCK  (exit 2)
#   - open findings, disposition recorded  -> allow  (exit 0)
#   - no /review marker on the PR          -> allow  (exit 0)
#   - gh failure                           -> allow  (exit 0, fail-open)
#   - command is not `gh pr merge`         -> allow  (exit 0)
#
# Run: bash claude/scripts/tests/test-merge-findings-gate.sh
set -u
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK="$SCRIPT_DIR/../pre-merge-findings-gate.py"
PASS=0
FAIL=0
ok()  { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

[ -f "$HOOK" ] || { echo "FATAL: hook not found at $HOOK"; exit 1; }
if command -v py >/dev/null 2>&1; then PY="py -3"; else PY="python3"; fi

# Encode a string as a JSON string literal (handles quotes/backslashes).
json_str() { $PY -c 'import json,sys; print(json.dumps(sys.stdin.read()), end="")'; }

# Write canned PR JSON (number/body/one comment) to a temp file; echo its path.
canned() { # body comment_body
  local f; f=$(mktemp)
  $PY -c 'import json,sys; print(json.dumps({"number":999,"body":sys.argv[1],"comments":[{"body":sys.argv[2]}]}))' \
    "$1" "$2" > "$f"
  echo "$f"
}

# Run the hook; echoes exit code. $1=command  $2=seam ("UNSET" to not set env).
run_gate() {
  local cmd="$1" seam="$2" stdin
  stdin=$(printf '{"tool_name":"Bash","tool_input":{"command":%s},"cwd":"."}' \
            "$(printf '%s' "$cmd" | json_str)")
  if [ "$seam" = "UNSET" ]; then
    printf '%s' "$stdin" | $PY "$HOOK" >/dev/null 2>&1
  else
    printf '%s' "$stdin" | MERGE_GATE_TEST_JSON="$seam" $PY "$HOOK" >/dev/null 2>&1
  fi
  echo $?
}

MARK_CLEAN="review done <!-- review-findings: blocking=0 non_blocking=0 -->"
MARK_OPEN="findings here <!-- review-findings: blocking=0 non_blocking=2 -->"
NO_MARK="a normal human comment with no marker"
MERGE_CMD='gh pr merge 999 --repo o/r --squash --delete-branch'

echo "Testing $HOOK"

echo "[1] clean review (0 findings) -> allow"
J=$(canned "some body" "$MARK_CLEAN")
RC=$(run_gate "$MERGE_CMD" "$J"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"; rm -f "$J"

echo "[2] open findings, no disposition -> BLOCK"
J=$(canned "body with no disposition section" "$MARK_OPEN")
RC=$(run_gate "$MERGE_CMD" "$J"); [ "$RC" = "2" ] && ok "exit 2 (blocked)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[3] open findings, disposition recorded -> allow"
J=$(canned "## Review findings disposition: all fixed" "$MARK_OPEN")
RC=$(run_gate "$MERGE_CMD" "$J"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"; rm -f "$J"

echo "[4] no /review marker -> allow"
J=$(canned "body" "$NO_MARK")
RC=$(run_gate "$MERGE_CMD" "$J"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"; rm -f "$J"

echo "[5] gh failure -> allow (fail-open)"
RC=$(run_gate "$MERGE_CMD" "FAIL"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"

echo "[6] non-merge command -> allow"
RC=$(run_gate 'gh pr view 999 --repo o/r' "UNSET"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"

echo "[7] --repo and --repo= forms parse to the right repo"
OUT=$($PY - "$HOOK" <<'PYEOF'
import importlib.util, os, sys
p = sys.argv[1]
sys.path.insert(0, os.path.dirname(p))
spec = importlib.util.spec_from_file_location("mg", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(m._parse_merge_target("gh pr merge 5 --repo o/r --squash"))
print(m._parse_merge_target("gh pr merge 7 --repo=a/b --admin"))
PYEOF
)
echo "$OUT" | grep -q "('5', 'o/r')" && ok "space form --repo parsed" || bad "space form: $OUT"
echo "$OUT" | grep -q "('7', 'a/b')" && ok "equals form --repo= parsed" || bad "equals form: $OUT"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
