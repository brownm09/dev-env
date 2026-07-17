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
#   - open findings, no disposition, via tool_name=PowerShell (dev-env#620) -> BLOCK (exit 2)
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

# Run the hook; echoes exit code. $1=command  $2=seam ("UNSET" to not set env)  $3=tool_name (default Bash).
run_gate() {
  local cmd="$1" seam="$2" tool="${3:-Bash}" stdin
  stdin=$(printf '{"tool_name":"%s","tool_input":{"command":%s},"cwd":"."}' \
            "$tool" "$(printf '%s' "$cmd" | json_str)")
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

echo "[2b] open findings, no disposition, via tool_name=PowerShell (dev-env#620) -> BLOCK"
# Proves the PowerShell PreToolUse extension reaches this gate too -- if
# tool_name filtering were still Bash-only, this would incorrectly exit 0.
J=$(canned "body with no disposition section" "$MARK_OPEN")
RC=$(run_gate "$MERGE_CMD" "$J" "PowerShell"); [ "$RC" = "2" ] && ok "exit 2 (blocked, tool_name=PowerShell)" || bad "expected 2, got $RC"; rm -f "$J"

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

echo "[6b] gh pr merge --help -> allow, never evaluated (dev-env#557)"
# No MERGE_GATE_TEST_JSON seam set at all -- if the guard did not fire, this
# would fall through to a LIVE gh pr view subprocess call (no seam = real gh).
# The guard must short-circuit before that ever happens, so this case proves
# the fix without needing any canned JSON.
RC=$(run_gate 'gh pr merge --help' "UNSET"); [ "$RC" = "0" ] && ok "exit 0 (never reached gh)" || bad "expected 0, got $RC"

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

echo "[8] --subject value with a quoted -R decoy does not hijack the repo (dev-env#634)"
OUT=$($PY - "$HOOK" <<'PYEOF'
import importlib.util, os, sys
p = sys.argv[1]
sys.path.insert(0, os.path.dirname(p))
spec = importlib.util.spec_from_file_location("mg", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(m._parse_merge_target('gh pr merge 42 --subject "see -R other/repo for context"'))
print(m._parse_merge_target('gh pr merge 42 --repo brownm09/dev-env --subject "see -R other/repo for context"'))
PYEOF
)
echo "$OUT" | grep -q "('42', None)" && ok "quoted -R decoy in --subject does not hijack repo (dev-env#634)" || bad "quoted decoy: $OUT"
echo "$OUT" | grep -q "('42', 'brownm09/dev-env')" && ok "real --repo flag survives alongside quoted decoy" || bad "real flag survives: $OUT"

echo "[9] a real --repo AFTER a quoted value containing &&/;/bare-& is no longer dropped (dev-env#660)"
OUT=$($PY - "$HOOK" <<'PYEOF'
import importlib.util, os, sys
p = sys.argv[1]
sys.path.insert(0, os.path.dirname(p))
spec = importlib.util.spec_from_file_location("mg", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
# Confirmed live bug before the fix: the tail's own end-boundary search ran
# against RAW (unmasked) text, so a && inside a quoted --subject value was
# mistaken for the real end of the invocation, truncating away --repo
# entirely -- returned ('42', None) instead of ('42', 'brownm09/dev-env').
print(m._parse_merge_target('gh pr merge 42 --subject "part1 && part2" --repo brownm09/dev-env'))
# A bare (undoubled) & is just as real a trigger for a naive split -- an
# ordinary commit subject like "R&D tracking" needs no deliberate crafting.
print(m._parse_merge_target('gh pr merge 42 --subject "R&D tracking" --repo brownm09/dev-env'))
# A chained sibling command after the merge must still be excluded --
# confirms the fix didn't just delete the boundary check.
print(m._parse_merge_target('gh pr merge 42 --repo brownm09/dev-env && rm -rf /'))
# Review finding on PR #668: a quoted && decoy AND a real trailing && chain
# combined in the SAME command -- the boundary-finder must pick the FIRST
# unmasked separator, not be shadowed by the earlier masked decoy.
print(m._parse_merge_target('gh pr merge 42 --subject "a && b" --repo brownm09/dev-env && rm -rf /'))
PYEOF
)
echo "$OUT" | grep -qF "('42', 'brownm09/dev-env')" && ok "real --repo after a quoted && value resolves correctly (dev-env#660)" || bad "&& truncation: $OUT"
echo "$OUT" | grep -c "('42', 'brownm09/dev-env')" | grep -q "^4$" && ok "bare-&, chained-command, and combined decoy+chain cases all still correct" || bad "expected 4 matching lines: $OUT"

echo "[10] a multi-line gh pr merge (backslash-newline line-continuations) is not truncated (dev-env#831)"
OUT=$($PY - "$HOOK" <<'PYEOF'
import importlib.util, os, sys
p = sys.argv[1]
sys.path.insert(0, os.path.dirname(p))
spec = importlib.util.spec_from_file_location("mg", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
# dev-env#831: _parse_merge_target now strips shell backslash-newline
# line-continuations before its &&|;|newline boundary split. Before the fix the
# split treated the continuation's newline as a real statement separator and
# truncated the tail at the first continuation -- so a --repo (or the PR number)
# sitting on a CONTINUED line was silently dropped, returning ('42', None)
# instead of ('42', 'brownm09/dev-env'). Each case below puts the consumed token
# AFTER a continuation, so each is discriminating (pre-fix returns the wrong value).
print(m._parse_merge_target('gh pr merge 42 --squash \\\n  --repo brownm09/dev-env \\\n  --subject "x"'))
# The PR number itself may sit on a continued line (continuation between the verb
# and its positional argument) and must survive the join.
print(m._parse_merge_target('gh pr merge \\\n  42 --repo brownm09/dev-env'))
# A real top-level && after the continuation must still be excluded: the fix
# strips only the continuation, it does not delete the boundary check.
print(m._parse_merge_target('gh pr merge 42 \\\n  --repo brownm09/dev-env && rm -rf /'))
PYEOF
)
echo "$OUT" | grep -c "('42', 'brownm09/dev-env')" | grep -q "^3$" && ok "multi-line merge: --repo/number on a continued line resolves; chained && still excluded (dev-env#831)" || bad "line-continuation truncation: $OUT"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
