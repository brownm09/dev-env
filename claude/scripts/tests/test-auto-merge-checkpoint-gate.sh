#!/bin/bash
# Behavioral test for pre-auto-merge-checkpoint-gate.py (ADR-083 auto-merge checkpoint gate).
#
# Drives the real hook with crafted stdin, reusing the hook's (imported) MERGE_GATE_TEST_JSON
# seam to supply canned `gh pr view --json` output, and asserts the gate's decision on each path
# named in ADR-083's Follow-up item 3:
#   - no --auto at all                                       -> allow  (exit 0)
#   - explicit --auto=false                                  -> allow  (exit 0, no gh call at all)
#   - --auto, clean review + complete checkpoints + fresh    -> allow  (exit 0)
#   - --auto, open findings + disposition recorded           -> allow  (exit 0)
#   - --auto, open findings + no disposition                 -> BLOCK  (exit 2)
#   - --auto, no comment carries the review-findings marker  -> BLOCK  (exit 2)
#   - --auto, no comment carries the premerge-checkpoints marker -> BLOCK (exit 2)
#   - --auto, checkpoints marker present but incomplete       -> BLOCK  (exit 2)
#   - --auto, marker stale (head commit postdates it)        -> BLOCK  (exit 2)
#   - --auto, gh failure                                      -> BLOCK  (exit 2 -- flipped vs. the
#                                                                 sibling gate's fail-open)
#   - gh pr merge --auto --help                               -> allow  (exit 0, never reaches gh)
#   - non-merge command                                       -> allow  (exit 0)
#   - --auto, main() crashes after the trigger (malformed gh JSON: commits[-1] is not a dict)
#                                                             -> BLOCK  (exit 2 via the __main__
#                                                                crash guard; was exit 1 = fail-OPEN
#                                                                before dev-env#718)
#   - broken sibling dependency (corrupt pre-merge-findings-gate.py loaded via exec_module)
#                                                             -> BLOCK  (exit 2 via the module-level
#                                                                import guard; was exit 1 before #718)
#   - --auto via tool_name=PowerShell (dev-env#620): open findings/no disposition -> BLOCK,
#     clean review + complete checkpoints -> allow (proves the PowerShell PreToolUse
#     extension reaches this gate, not just a settings.json wiring assumption)
#
# Run: bash claude/scripts/tests/test-auto-merge-checkpoint-gate.sh
set -u
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK="$SCRIPT_DIR/../pre-auto-merge-checkpoint-gate.py"
PASS=0
FAIL=0
ok()  { echo "  ok: $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

[ -f "$HOOK" ] || { echo "FATAL: hook not found at $HOOK"; exit 1; }
if command -v py >/dev/null 2>&1; then PY="py -3"; else PY="python3"; fi

# Encode a string as a JSON string literal (handles quotes/backslashes).
json_str() { $PY -c 'import json,sys; print(json.dumps(sys.stdin.read()), end="")'; }

# Write canned PR JSON (number/body/one comment/one commit) to a temp file; echo its path.
# $1=pr_body  $2=comment_body  $3=comment_createdAt  $4=head_committedAt
canned() {
  local f; f=$(mktemp)
  $PY -c 'import json,sys
pr_body, comment_body, comment_created, head_committed = sys.argv[1:5]
print(json.dumps({
    "number": 999,
    "body": pr_body,
    "comments": [{"body": comment_body, "createdAt": comment_created}],
    "commits": [{"committedDate": head_committed}],
}))' "$1" "$2" "$3" "$4" > "$f"
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

FRESH_CREATED="2026-07-05T10:00:00Z"
FRESH_HEAD="2026-07-05T09:00:00Z"    # comment postdates head commit -- fresh
STALE_HEAD="2026-07-05T11:00:00Z"    # head commit postdates comment -- stale

MARK_CLEAN="<!-- review-findings: blocking=0 non_blocking=0 -->"
MARK_OPEN="<!-- review-findings: blocking=0 non_blocking=2 -->"
CHECKPOINTS_OK="<!-- premerge-checkpoints: adr_warrant=written doc_reconciliation=updated -->"
CHECKPOINTS_MISSING="<!-- premerge-checkpoints: adr_warrant=missing doc_reconciliation=updated -->"
NO_MARK="a normal human comment with no marker at all"

AUTO_CMD='gh pr merge 999 --repo o/r --auto --squash'
PLAIN_CMD='gh pr merge 999 --repo o/r --squash --delete-branch'

echo "Testing $HOOK"

echo "[1] no --auto at all -> allow, never touches gh"
RC=$(run_gate "$PLAIN_CMD" "UNSET"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"

echo "[2] explicit --auto=false -> allow, never touches gh"
RC=$(run_gate 'gh pr merge 999 --auto=false --squash' "UNSET"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"

echo "[3] --auto, clean review + complete checkpoints + fresh -> allow"
J=$(canned "some body" "$MARK_CLEAN $CHECKPOINTS_OK" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"; rm -f "$J"

echo "[4] --auto, open findings + disposition recorded -> allow"
J=$(canned "## Review findings disposition: all fixed" "$MARK_OPEN $CHECKPOINTS_OK" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"; rm -f "$J"

echo "[5] --auto, open findings + NO disposition -> BLOCK"
J=$(canned "body with no disposition section" "$MARK_OPEN $CHECKPOINTS_OK" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "2" ] && ok "exit 2 (blocked)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[6] --auto, no comment carries the review-findings marker -> BLOCK"
J=$(canned "some body" "$NO_MARK" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "2" ] && ok "exit 2 (blocked)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[7] --auto, review-findings clean but NO premerge-checkpoints marker at all -> BLOCK"
J=$(canned "some body" "$MARK_CLEAN" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "2" ] && ok "exit 2 (blocked)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[8] --auto, premerge-checkpoints marker present but incomplete (adr_warrant=missing) -> BLOCK"
J=$(canned "some body" "$MARK_CLEAN $CHECKPOINTS_MISSING" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "2" ] && ok "exit 2 (blocked)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[9] --auto, stale marker (head commit postdates the qualifying comment) -> BLOCK"
J=$(canned "some body" "$MARK_CLEAN $CHECKPOINTS_OK" "$FRESH_CREATED" "$STALE_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "2" ] && ok "exit 2 (blocked)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[10] --auto, gh failure -> BLOCK (flipped default vs. the sibling gate's fail-open)"
RC=$(run_gate "$AUTO_CMD" "FAIL"); [ "$RC" = "2" ] && ok "exit 2 (blocked)" || bad "expected 2, got $RC"

echo "[11] gh pr merge --auto --help -> allow, never reaches gh"
RC=$(run_gate 'gh pr merge --auto --help' "UNSET"); [ "$RC" = "0" ] && ok "exit 0 (never reached gh)" || bad "expected 0, got $RC"

echo "[12] non-merge command -> allow"
RC=$(run_gate 'gh pr view 999 --repo o/r' "UNSET"); [ "$RC" = "0" ] && ok "exit 0" || bad "expected 0, got $RC"

echo "[13] --auto, commits array at/above the suspect-truncation page size -> BLOCK"
J=$($PY -c 'import json,sys,tempfile
data = {
    "number": 999,
    "body": "some body",
    "comments": [{"body": sys.argv[1], "createdAt": sys.argv[2]}],
    "commits": [{"committedDate": sys.argv[3]} for _ in range(100)],
}
f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json")
json.dump(data, f)
f.close()
print(f.name)' "$MARK_CLEAN $CHECKPOINTS_OK" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "2" ] && ok "exit 2 (blocked)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[14] --auto, quoted --auto flag (shell-stripped) -> still detected and evaluated"
J=$(canned "some body" "$MARK_CLEAN $CHECKPOINTS_OK" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate 'gh pr merge 999 --repo o/r --squash "--auto"' "$J"); [ "$RC" = "0" ] && ok "exit 0 (quoted --auto correctly gated and passed)" || bad "expected 0, got $RC"; rm -f "$J"

echo "[15] --auto mentioned only as prose inside --body value -> NOT gated (plain merge)"
RC=$(run_gate 'gh pr merge 999 --repo o/r --squash --body "please --auto this"' "UNSET"); [ "$RC" = "0" ] && ok "exit 0 (never touches gh -- correctly not treated as --auto)" || bad "expected 0, got $RC"

echo "[16] --auto, main() crashes after the trigger (commits[-1] not a dict) -> BLOCK via __main__ crash guard"
# canned() always emits a dict-shaped commit; build a malformed one inline so main()'s
# commits[-1].get(...) raises AttributeError -- proving the __main__ guard converts a runtime crash
# into a fail-CLOSED exit 2 (it exited 1 = fail-OPEN before dev-env#718). Clean markers so the
# earlier disposition/checkpoint checks pass and execution actually reaches the commits access.
J=$($PY -c 'import json,sys,tempfile
data = {
    "number": 999,
    "body": "some body",
    "comments": [{"body": sys.argv[1], "createdAt": sys.argv[2]}],
    "commits": ["not-a-dict"],
}
f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json")
json.dump(data, f); f.close(); print(f.name)' "$MARK_CLEAN $CHECKPOINTS_OK" "$FRESH_CREATED")
RC=$(run_gate "$AUTO_CMD" "$J"); [ "$RC" = "2" ] && ok "exit 2 (crash -> fail closed)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[17b] --auto via tool_name=PowerShell (dev-env#620), open findings + NO disposition -> BLOCK"
# Proves the PowerShell PreToolUse extension reaches this gate too -- if tool_name filtering
# were still Bash-only, this would incorrectly exit 0 (fail-open no-op) instead of blocking.
J=$(canned "body with no disposition section" "$MARK_OPEN $CHECKPOINTS_OK" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J" "PowerShell"); [ "$RC" = "2" ] && ok "exit 2 (blocked, tool_name=PowerShell)" || bad "expected 2, got $RC"; rm -f "$J"

echo "[17c] --auto via tool_name=PowerShell, clean review + complete checkpoints + fresh -> allow"
J=$(canned "some body" "$MARK_CLEAN $CHECKPOINTS_OK" "$FRESH_CREATED" "$FRESH_HEAD")
RC=$(run_gate "$AUTO_CMD" "$J" "PowerShell"); [ "$RC" = "0" ] && ok "exit 0 (allowed, tool_name=PowerShell)" || bad "expected 0, got $RC"; rm -f "$J"

echo "[18] broken sibling dependency (corrupt pre-merge-findings-gate.py) -> BLOCK via module-level import guard"
# Run a copy of the hook in a temp dir whose pre-merge-findings-gate.py raises on import, so the
# module-level exec_module fails. Before #718 this raised uncaught -> exit 1 = fail-OPEN; the import
# guard now fails CLOSED (exit 2). The crash fires at import, before stdin is even read, so the
# command content is irrelevant -- a real --auto merge is used only for realism.
TMPD=$(mktemp -d)
cp "$HOOK" "$TMPD/"
cp "$SCRIPT_DIR"/../_*.py "$TMPD/"   # all shared _*.py siblings the hook loads at import
printf 'raise RuntimeError("boom from broken sibling")\n' > "$TMPD/pre-merge-findings-gate.py"
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 999 --auto"},"cwd":"."}' \
  | $PY "$TMPD/pre-auto-merge-checkpoint-gate.py" >/dev/null 2>&1
RC=$?; [ "$RC" = "2" ] && ok "exit 2 (import crash -> fail closed)" || bad "expected 2, got $RC"
rm -rf "$TMPD"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
