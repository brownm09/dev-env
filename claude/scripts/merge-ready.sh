#!/usr/bin/env bash
# merge-ready.sh — list open PRs that are green + mergeable + waiting to merge.
# Usage: bash merge-ready.sh [owner/repo ...]   (defaults to brownm09/lifting-logbook)
# Requires: gh (authenticated), node. jq is intentionally NOT used (unavailable in this env).
set -euo pipefail

REPOS=("$@")
if [ ${#REPOS[@]} -eq 0 ]; then
  REPOS=("brownm09/lifting-logbook")
fi

# Scratch temp file — use the literal scratch dir, not $TMPDIR (Git Bash sets $TMPDIR to a
# Unix path that Node-on-Windows can't resolve; see the global temp-path rule / dev-env#334).
TMPFILE="C:/Users/brown/.claude/scratch/mergeready_$$.json"
trap 'rm -f "$TMPFILE"' EXIT

for REPO in "${REPOS[@]}"; do
  echo "================ $REPO ================"
  gh pr list --repo "$REPO" --state open \
    --json number,title,isDraft,mergeable,mergeStateStatus,statusCheckRollup,updatedAt > "$TMPFILE"
  node -e '
  const prs = JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));
  const sum = (roll=[]) => {
    const c = {ok:0,pending:0,fail:0};
    for (const x of roll) {
      const s = x.conclusion || x.status || "";
      if (["SUCCESS","NEUTRAL","SKIPPED"].includes(s)) c.ok++;
      else if (["FAILURE","ERROR","TIMED_OUT","CANCELLED","ACTION_REQUIRED","STARTUP_FAILURE"].includes(s)) c.fail++;
      else c.pending++;
    }
    return c;
  };
  const ready=[], waiting=[];
  for (const p of prs) {
    if (p.isDraft) continue;
    const c = sum(p.statusCheckRollup || []);
    const line = `#${p.number} ${p.title}  [${p.mergeStateStatus}] ${c.ok}✓ ${c.pending}… ${c.fail}✗`;
    if (p.mergeable==="MERGEABLE" && p.mergeStateStatus==="CLEAN" && c.fail===0 && c.pending===0) ready.push(line);
    else waiting.push(line);
  }
  console.log("  ✅ MERGE-READY (green + mergeable + nothing pending):");
  console.log(ready.length ? "    "+ready.join("\n    ") : "    (none)");
  console.log("  ⏳ open, not merge-ready:");
  console.log(waiting.length ? "    "+waiting.join("\n    ") : "    (none)");
  ' "$TMPFILE"
done

rm -f "$TMPFILE"
