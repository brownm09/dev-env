#!/bin/bash
# baseline-tests: capture and diff pre-existing test failures for the fix-on-touch policy.
#
# Usage:
#   baseline-tests snapshot   # run tests on the current branch, save failing tests as the baseline
#   baseline-tests diff       # run tests now, compare against the baseline, classify failures
#
# Reads test_command from .claude/hook-config.json (default: "npx jest --json --silent").
# Writes baseline JSON to C:/Users/brown/.claude/scratch/baseline_<repo>_<branch>.json.
#
# Snapshot format:
#   {
#     "repo": "<repo>", "branch": "<branch>", "captured_at": "<iso>",
#     "failures": [{"file": "...", "test_name": "...", "first_line": "...", "fingerprint": "<sha1>"}]
#   }
#
# Diff exit codes:
#   0 — no new failures (preexisting still allowed)
#   1 — new failures introduced on this branch (must be fixed before PR)
#   2 — script/usage error (no test command, no baseline, parse failure)
#
# See docs/adr/030-baseline-test-failure-policy.md for the policy rationale.

set -u

SCRATCH_DIR="C:/Users/brown/.claude/scratch"
DEFAULT_TEST_COMMAND="npx jest --json --silent"

err() { echo "[baseline-tests] $*" >&2; }

repo_name() {
  local toplevel
  toplevel=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
  basename "$toplevel"
}

branch_name_sanitized() {
  local b
  b=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || return 1
  echo "$b" | tr '/' '-'
}

baseline_path() {
  local repo="$1" branch="$2"
  echo "$SCRATCH_DIR/baseline_${repo}_${branch}.json"
}

read_test_command() {
  local cfg=".claude/hook-config.json"
  if [ ! -f "$cfg" ]; then
    echo "$DEFAULT_TEST_COMMAND"
    return 0
  fi
  CFG_PATH="$cfg" DEFAULT_TC="$DEFAULT_TEST_COMMAND" node -e '
    const fs = require("fs");
    try {
      const d = JSON.parse(fs.readFileSync(process.env.CFG_PATH, "utf8"));
      process.stdout.write(d.test_command || process.env.DEFAULT_TC);
    } catch (e) { process.stdout.write(process.env.DEFAULT_TC); }
  '
}

# Run the configured test command, capturing stdout to $1 and stderr to $1.err.
# On empty stdout, print the stderr tail and exit 2.
run_test_command() {
  local out="$1" tc="$2"
  err "Running: $tc"
  # Tests may exit non-zero — that's expected; keep going.
  eval "$tc" > "$out" 2> "$out.err" || true
  if [ ! -s "$out" ]; then
    err "Test command produced no stdout. Verify '$tc' emits Jest --json output."
    if [ -s "$out.err" ]; then
      err "Last 5 lines of stderr:"
      tail -n 5 "$out.err" | while IFS= read -r line; do err "  $line"; done
    fi
    rm -f "$out" "$out.err"
    return 2
  fi
  rm -f "$out.err"
  return 0
}

# Parse Jest --json stdout from $1 (raw output file), write failures JSON to $2 (out path).
# Uses repo/branch passed as $3/$4 for the metadata envelope.
parse_jest_to_failures() {
  local raw="$1" out="$2" repo="$3" branch="$4"
  RAW_PATH="$raw" OUT_PATH="$out" REPO="$repo" BRANCH="$branch" node -e '
    const fs = require("fs");
    const crypto = require("crypto");
    const raw = fs.readFileSync(process.env.RAW_PATH, "utf8");
    // Jest emits warnings on stderr only; stdout should be pure JSON when --json is set.
    // Strip any leading non-{ noise (npm/turbo banners) by finding the first "{".
    const start = raw.indexOf("{");
    if (start < 0) { console.error("No JSON object found in test output."); process.exit(3); }
    let parsed;
    try { parsed = JSON.parse(raw.slice(start)); }
    catch (e) { console.error("Failed to parse Jest JSON: " + e.message); process.exit(3); }
    const failures = [];
    for (const tr of (parsed.testResults || [])) {
      const file = tr.name || tr.testFilePath || "";
      for (const a of (tr.assertionResults || [])) {
        if (a.status === "failed") {
          const test_name = (a.ancestorTitles || []).concat([a.title || ""]).filter(Boolean).join(" > ");
          const first_line = ((a.failureMessages && a.failureMessages[0]) || "").split("\n")[0].trim();
          const fp_input = file + "::" + test_name + "::" + first_line;
          const fingerprint = crypto.createHash("sha1").update(fp_input).digest("hex");
          failures.push({ file, test_name, first_line, fingerprint });
        }
      }
    }
    const envelope = {
      repo: process.env.REPO,
      branch: process.env.BRANCH,
      captured_at: new Date().toISOString(),
      summary: {
        passed: parsed.numPassedTests || 0,
        failed: parsed.numFailedTests || 0,
        skipped: parsed.numPendingTests || 0,
        total: parsed.numTotalTests || 0,
      },
      failures,
    };
    fs.writeFileSync(process.env.OUT_PATH, JSON.stringify(envelope, null, 2) + "\n");
    console.log("Captured " + failures.length + " failing test(s).");
  '
}

touched_files() {
  # Files modified on the current branch vs origin/main, normalized to repo-relative POSIX paths.
  git diff --name-only origin/main -- . 2>/dev/null || true
}

cmd_snapshot() {
  local repo branch out tmp tc
  repo=$(repo_name) || { err "not inside a git repo"; exit 2; }
  branch=$(branch_name_sanitized) || { err "could not resolve branch"; exit 2; }
  out=$(baseline_path "$repo" "$branch")
  mkdir -p "$SCRATCH_DIR"
  tc=$(read_test_command)
  if [ -z "$tc" ]; then err "no test_command resolved"; exit 2; fi

  tmp="$SCRATCH_DIR/baseline_run_$$.json"
  run_test_command "$tmp" "$tc" || exit 2
  parse_jest_to_failures "$tmp" "$out" "$repo" "$branch" || { rm -f "$tmp"; exit 2; }
  rm -f "$tmp"
  err "Baseline written → $out"
}

cmd_diff() {
  local repo branch baseline tmp tc current touched_file rc
  repo=$(repo_name) || { err "not inside a git repo"; exit 2; }
  branch=$(branch_name_sanitized) || { err "could not resolve branch"; exit 2; }
  baseline=$(baseline_path "$repo" "$branch")
  if [ ! -f "$baseline" ]; then
    err "No baseline at $baseline — run 'baseline-tests snapshot' on the branch's starting commit first."
    exit 2
  fi
  tc=$(read_test_command)
  if [ -z "$tc" ]; then err "no test_command resolved"; exit 2; fi

  # Refresh origin/main so the touched-file set is computed against current main,
  # not a stale ref from when the worktree was last touched.
  git fetch --quiet origin main 2>/dev/null || git fetch --quiet origin 2>/dev/null || true

  tmp="$SCRATCH_DIR/baseline_diff_$$.json"
  run_test_command "$tmp" "$tc" || exit 2

  current="$SCRATCH_DIR/baseline_diff_current_$$.json"
  parse_jest_to_failures "$tmp" "$current" "$repo" "$branch" || { rm -f "$tmp" "$current"; exit 2; }
  rm -f "$tmp"

  touched_file="$SCRATCH_DIR/baseline_touched_$$.txt"
  touched_files > "$touched_file"

  # Classify and report.
  BASELINE_PATH="$baseline" CURRENT_PATH="$current" TOUCHED_PATH="$touched_file" node -e '
    const fs = require("fs");
    const baseline = JSON.parse(fs.readFileSync(process.env.BASELINE_PATH, "utf8"));
    const current = JSON.parse(fs.readFileSync(process.env.CURRENT_PATH, "utf8"));
    const touched = new Set(fs.readFileSync(process.env.TOUCHED_PATH, "utf8").trim().split("\n").filter(Boolean));
    const baseFps = new Set(baseline.failures.map(f => f.fingerprint));
    const curFps = new Set(current.failures.map(f => f.fingerprint));
    const groups = { "new": [], "preexisting-touched": [], "preexisting-untouched": [] };
    for (const f of current.failures) {
      if (!baseFps.has(f.fingerprint)) { groups["new"].push(f); continue; }
      const fileRel = f.file.replace(/\\/g, "/");
      // Treat the failure file as absolute if it starts with a Windows drive
      // letter (e.g. "C:/...") or a POSIX root ("/..."). Basename-only matching
      // is only safe in that case — relative paths from different directories
      // can share a basename without being the same file.
      const isAbs = /^[A-Za-z]:\//.test(fileRel) || fileRel.startsWith("/");
      const base = fileRel.split("/").pop();
      let hit = false;
      for (const t of touched) {
        if (fileRel === t || fileRel.endsWith("/" + t) || t.endsWith("/" + fileRel)) { hit = true; break; }
        if (isAbs && base && t.split("/").pop() === base) { hit = true; break; }
      }
      (hit ? groups["preexisting-touched"] : groups["preexisting-untouched"]).push(f);
    }
    const fixed = baseline.failures.filter(f => !curFps.has(f.fingerprint));

    function block(label, arr) {
      console.log("\n=== " + label + " (" + arr.length + ") ===");
      for (const f of arr) {
        console.log("  - " + f.file);
        console.log("      " + f.test_name);
        if (f.first_line) console.log("      " + f.first_line);
      }
    }
    block("NEW failures (block PR — must fix)", groups["new"]);
    block("PRE-EXISTING failures in touched files (fix-on-touch or file an issue)", groups["preexisting-touched"]);
    block("PRE-EXISTING failures in untouched files (note in PR body)", groups["preexisting-untouched"]);
    if (fixed.length) block("Failures fixed since baseline (kudos)", fixed);

    console.log("\nSummary: " + groups["new"].length + " new, " +
                groups["preexisting-touched"].length + " preexisting-touched, " +
                groups["preexisting-untouched"].length + " preexisting-untouched, " +
                fixed.length + " fixed.");

    process.exit(groups["new"].length > 0 ? 1 : 0);
  '
  rc=$?
  rm -f "$current" "$touched_file"
  exit $rc
}

main() {
  local sub="${1:-}"
  case "$sub" in
    snapshot) cmd_snapshot ;;
    diff) cmd_diff ;;
    -h|--help|"")
      cat <<EOF
Usage: baseline-tests <snapshot|diff>

  snapshot   Run tests on the current branch and save failing tests as the baseline.
  diff       Run tests now and classify failures vs the baseline (new / preexisting-touched / preexisting-untouched).

Reads .claude/hook-config.json field 'test_command' (default: '$DEFAULT_TEST_COMMAND').
Snapshot path: $SCRATCH_DIR/baseline_<repo>_<branch>.json
EOF
      ;;
    *) err "unknown subcommand: $sub"; exit 2 ;;
  esac
}

main "$@"
