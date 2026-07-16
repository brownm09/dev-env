#!/bin/bash
# baseline-tests: capture and diff pre-existing test failures for the fix-on-touch policy.
#
# Usage:
#   baseline-tests snapshot   # run tests on the current branch, save failing tests as the baseline
#   baseline-tests diff       # run tests now, compare against the baseline, classify failures
#   baseline-tests gc         # remove baseline snapshots for the current repo whose branch is gone
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
# `gc` sweeps baseline_<repo>_*.json files for the CURRENT repo (repo_name())
# whose recorded `branch` (read from the JSON envelope, never reverse-parsed
# from the filename) no longer exists locally or on origin. Branch-existence,
# not age -- a long-lived branch's snapshot can legitimately outlive any fixed
# age cutoff, and sweeping it by age would silently break the ADR-030
# fix-on-touch policy for that branch (dev-env#778, a follow-up from #768/
# PR#777, which deliberately excluded this family from its own age-based
# sweep -- see sweep-scratch-debris.py's module docstring). `snapshot` calls
# `gc` automatically (best-effort, never fails the snapshot) since writing a
# new baseline is the natural moment to sweep old ones for the same repo.
#
# See docs/adr/030-baseline-test-failure-policy.md for the policy rationale.

set -u

: "${SCRATCH_DIR:=C:/Users/brown/.claude/scratch}"
DEFAULT_TEST_COMMAND="npx jest --json --silent"

err() { echo "[baseline-tests] $*" >&2; }

repo_name() {
  local toplevel
  toplevel=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
  basename "$toplevel"
}

branch_name_raw() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null
}

branch_name_sanitized() {
  local b
  b=$(branch_name_raw) || return 1
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

# True (0) iff $1 is a local branch ref in the current repo.
branch_exists_locally() {
  git rev-parse --verify --quiet "refs/heads/$1" >/dev/null 2>&1
}

# Checks whether $1 exists as a branch on origin.
#   0 — confirmed present on origin
#   1 — confirmed absent on origin (ls-remote ran fine, no exact-matching ref)
#   2 — check itself failed (no network, no origin remote, auth failure,
#       timed out, ...)
# Exit code 2 is deliberately distinct from 1: the caller must never treat a
# failed check as "confirmed absent" -- that would delete a snapshot for a
# branch that might still be very much alive.
#
# GIT_TERMINAL_PROMPT=0 / GCM_INTERACTIVE=never plus a bounded `timeout`
# keep an unreachable or auth-prompting origin from hanging this call --
# this runs on cmd_snapshot's hot path (every `new-branch` in an opted-in
# repo), and this machine's own git credential helper GUI is documented
# elsewhere in this repo to hang non-interactively. A stuck prompt or a
# black-holed connection must fail into the "check failed" (keep) branch
# within a bounded time, not block branch creation indefinitely.
#
# `git ls-remote`'s own pattern matching treats a bare branch name as a
# "*/<name>" suffix match, so querying "foo" would also return a sibling
# branch "topic/foo" on the server side. Rather than depend on that
# matching behavior, the exact-match filter below re-checks the returned
# ref name column against "refs/heads/<branch>" verbatim, so a same-suffix
# or same-prefix sibling branch can never be mistaken for the one asked
# about.
branch_exists_remotely() {
  local branch="$1" out
  out=$(GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never timeout 10 git ls-remote --heads origin "$branch" 2>/dev/null) || return 2
  printf '%s\n' "$out" | cut -f2 | grep -qxF "refs/heads/$branch"
}

# True (0) only when $1 is confirmed gone both locally and on origin. Any
# uncertainty (the remote check itself failed) keeps the snapshot -- fail
# toward not deleting, matching this codebase's convention elsewhere for
# irreversible-ish cleanup (e.g. sweep-scratch-debris.py never counts a
# failed unlink as removed).
branch_is_gone() {
  local branch="$1" remote_rc
  branch_exists_locally "$branch" && return 1
  branch_exists_remotely "$branch"
  remote_rc=$?
  [ "$remote_rc" -eq 1 ]
}

# Prints "<status>\t<repo>\t<branch>\t<path>" for each file argument, one
# line per file, in the same order the files were given -- status is "ok"
# (repo/branch parsed successfully; repo/branch columns populated) or "skip"
# (unparseable JSON or missing repo/branch field; repo/branch columns
# empty). Reads content, never reverse-parses the filename --
# branch_name_sanitized() replaces "/" with "-", so a filename fragment
# alone cannot be trusted to recover the original branch name.
#
# Batched into a single `node` invocation rather than one per file: cmd_gc
# re-parses every baseline whose branch is still live on every run (those
# are never deleted, so unlike a one-off sweep this cost does not shrink
# over time), and each `node` process start on Windows is not free.
#
# `cygpath -m` normalizes every path first. Unlike an argv or environment
# variable, Git Bash's MSYS2 layer does NOT auto-translate a POSIX-style
# path (e.g. "/tmp/foo") handed to a native Windows binary over a pipe --
# node.exe would instead misread it as the literal, nonexistent
# "C:\tmp\foo" and every file would silently come back "skip". This is a
# single extra subprocess call for the whole batch, not one per file, so it
# does not reintroduce the per-file cost this batching exists to avoid.
read_baseline_meta_batch() {
  cygpath -m -- "$@" | node -e '
    const fs = require("fs");
    const readline = require("readline");
    const rl = readline.createInterface({ input: process.stdin, terminal: false });
    rl.on("line", (path) => {
      if (!path) return;
      try {
        const d = JSON.parse(fs.readFileSync(path, "utf8"));
        if (typeof d.repo === "string" && d.repo && typeof d.branch === "string" && d.branch) {
          process.stdout.write("ok\t" + d.repo + "\t" + d.branch + "\t" + path + "\n");
          return;
        }
      } catch (e) { /* fall through to skip */ }
      process.stdout.write("skip\t\t\t" + path + "\n");
    });
  '
}

cmd_gc() {
  local repo files=() removed=0 kept=0 skipped=0 status frepo branch f prior_nullglob=0
  repo=$(repo_name) || { err "gc: not inside a git repo"; return 1; }
  mkdir -p "$SCRATCH_DIR"

  shopt -q nullglob && prior_nullglob=1
  shopt -s nullglob
  files=("$SCRATCH_DIR"/baseline_"${repo}"_*.json)
  [ "$prior_nullglob" -eq 0 ] && shopt -u nullglob

  if [ ${#files[@]} -gt 0 ]; then
    while IFS=$'\t' read -r status frepo branch f; do
      case "$status" in
        skip)
          err "gc: skip (unparseable or missing repo/branch): $f"
          skipped=$((skipped + 1))
          ;;
        ok)
          if [ "$frepo" != "$repo" ]; then
            # Defensive: matched the filename glob but the envelope's own
            # repo field disagrees (e.g. hand-edited or corrupted) -- never
            # guess.
            kept=$((kept + 1))
          elif branch_is_gone "$branch"; then
            rm -f -- "$f"
            err "gc: removed (branch gone: $branch): $f"
            removed=$((removed + 1))
          else
            kept=$((kept + 1))
          fi
          ;;
      esac
    done < <(read_baseline_meta_batch "${files[@]}")
  fi

  # Stay quiet on the common no-op case (every baseline's branch is still
  # live) -- this now runs on every `new-branch`, and routine "nothing to
  # do" bookkeeping would otherwise bury a genuinely notable skip/removal
  # in per-branch chatter.
  if [ "$removed" -gt 0 ] || [ "$skipped" -gt 0 ]; then
    err "gc: removed $removed, kept $kept, skipped $skipped (repo=$repo)"
  fi
}

cmd_snapshot() {
  local repo branch_raw branch out tmp tc
  repo=$(repo_name) || { err "not inside a git repo"; exit 2; }
  branch_raw=$(branch_name_raw) || { err "could not resolve branch"; exit 2; }
  branch=$(echo "$branch_raw" | tr '/' '-')
  out=$(baseline_path "$repo" "$branch")
  mkdir -p "$SCRATCH_DIR"
  tc=$(read_test_command)
  if [ -z "$tc" ]; then err "no test_command resolved"; exit 2; fi

  tmp="$SCRATCH_DIR/baseline_run_$$.json"
  run_test_command "$tmp" "$tc" || exit 2
  # The envelope's branch field stores the RAW (unsanitized) name -- cmd_gc
  # compares it against real git refs, which never contain the sanitized
  # form. Only the filename (baseline_path, above) needs sanitizing, since
  # a literal "/" there would resolve to a nested (and likely missing) path.
  parse_jest_to_failures "$tmp" "$out" "$repo" "$branch_raw" || { rm -f "$tmp"; exit 2; }
  rm -f "$tmp"
  err "Baseline written → $out"

  # Best-effort: writing a new baseline is a natural moment to sweep old
  # ones for this repo. Never let a gc hiccup fail the snapshot itself.
  cmd_gc || true
}

cmd_diff() {
  local repo branch_raw branch baseline tmp tc current touched_file rc
  repo=$(repo_name) || { err "not inside a git repo"; exit 2; }
  branch_raw=$(branch_name_raw) || { err "could not resolve branch"; exit 2; }
  branch=$(echo "$branch_raw" | tr '/' '-')
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
  # Consistency with cmd_snapshot: the envelope's branch field is always the
  # raw (unsanitized) name, even though this particular envelope is a
  # throwaway temp file never read by cmd_gc.
  parse_jest_to_failures "$tmp" "$current" "$repo" "$branch_raw" || { rm -f "$tmp" "$current"; exit 2; }
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
    gc) cmd_gc ;;
    -h|--help|"")
      cat <<EOF
Usage: baseline-tests <snapshot|diff|gc>

  snapshot   Run tests on the current branch and save failing tests as the baseline.
             Also sweeps stale baselines for this repo (see 'gc') on the way out.
  diff       Run tests now and classify failures vs the baseline (new / preexisting-touched / preexisting-untouched).
  gc         Remove baseline_<repo>_*.json files for this repo whose branch no longer
             exists locally or on origin. Never deletes on an inconclusive remote check.

Reads .claude/hook-config.json field 'test_command' (default: '$DEFAULT_TEST_COMMAND').
Snapshot path: $SCRATCH_DIR/baseline_<repo>_<branch>.json
EOF
      ;;
    *) err "unknown subcommand: $sub"; exit 2 ;;
  esac
}

# Guard so this file can be sourced (e.g. by its own test suite, to call the
# functions above directly against fixtures) without immediately executing
# main with the sourcing script's own argv.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
