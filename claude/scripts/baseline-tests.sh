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
  node -e "
    const fs = require('fs');
    try {
      const d = JSON.parse(fs.readFileSync('$cfg','utf8'));
      process.stdout.write(d.test_command || '$DEFAULT_TEST_COMMAND');
    } catch (e) { process.stdout.write('$DEFAULT_TEST_COMMAND'); }
  "
}

# Parse Jest --json stdout from $1 (raw output file), write failures JSON to $2 (out path).
# Uses repo/branch passed as $3/$4 for the metadata envelope.
parse_jest_to_failures() {
  local raw="$1" out="$2" repo="$3" branch="$4"
  node -e "
    const fs = require('fs');
    const crypto = require('crypto');
    const raw = fs.readFileSync('$raw','utf8');
    // Jest may emit warnings on stderr only, but stdout should be pure JSON when --json is set.
    // Strip any leading non-{ noise (npm/turbo banners) by finding the first '{'.
    const start = raw.indexOf('{');
    if (start < 0) { console.error('No JSON object found in test output.'); process.exit(3); }
    let parsed;
    try { parsed = JSON.parse(raw.slice(start)); }
    catch (e) { console.error('Failed to parse Jest JSON: ' + e.message); process.exit(3); }
    const failures = [];
    for (const tr of (parsed.testResults || [])) {
      const file = tr.name || tr.testFilePath || '';
      for (const a of (tr.assertionResults || [])) {
        if (a.status === 'failed') {
          const test_name = (a.ancestorTitles || []).concat([a.title || '']).filter(Boolean).join(' > ');
          const first_line = ((a.failureMessages && a.failureMessages[0]) || '').split('\n')[0].trim();
          const fp_input = file + '::' + test_name + '::' + first_line;
          const fingerprint = crypto.createHash('sha1').update(fp_input).digest('hex');
          failures.push({ file, test_name, first_line, fingerprint });
        }
      }
    }
    const envelope = {
      repo: '$repo',
      branch: '$branch',
      captured_at: new Date().toISOString(),
      summary: {
        passed: parsed.numPassedTests || 0,
        failed: parsed.numFailedTests || 0,
        skipped: parsed.numPendingTests || 0,
        total: parsed.numTotalTests || 0,
      },
      failures,
    };
    fs.writeFileSync('$out', JSON.stringify(envelope, null, 2) + '\n');
    console.log('Captured ' + failures.length + ' failing test(s).');
  "
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
  err "Running: $tc"
  # Run; ignore non-zero exit (tests may fail — that's the point).
  eval "$tc" > "$tmp" 2>/dev/null || true
  if [ ! -s "$tmp" ]; then
    err "Test command produced no stdout. Verify '$tc' emits Jest --json output."
    rm -f "$tmp"
    exit 2
  fi
  parse_jest_to_failures "$tmp" "$out" "$repo" "$branch" || { rm -f "$tmp"; exit 2; }
  rm -f "$tmp"
  err "Baseline written → $out"
}

cmd_diff() {
  local repo branch baseline tmp tc
  repo=$(repo_name) || { err "not inside a git repo"; exit 2; }
  branch=$(branch_name_sanitized) || { err "could not resolve branch"; exit 2; }
  baseline=$(baseline_path "$repo" "$branch")
  if [ ! -f "$baseline" ]; then
    err "No baseline at $baseline — run 'baseline-tests snapshot' on the branch's starting commit first."
    exit 2
  fi
  tc=$(read_test_command)
  if [ -z "$tc" ]; then err "no test_command resolved"; exit 2; fi

  tmp="$SCRATCH_DIR/baseline_diff_$$.json"
  err "Running: $tc"
  eval "$tc" > "$tmp" 2>/dev/null || true
  if [ ! -s "$tmp" ]; then
    err "Test command produced no stdout. Verify '$tc' emits Jest --json output."
    rm -f "$tmp"
    exit 2
  fi

  local current="$SCRATCH_DIR/baseline_diff_current_$$.json"
  parse_jest_to_failures "$tmp" "$current" "$repo" "$branch" || { rm -f "$tmp" "$current"; exit 2; }
  rm -f "$tmp"

  local touched_file="$SCRATCH_DIR/baseline_touched_$$.txt"
  touched_files > "$touched_file"

  # Classify and report.
  node -e "
    const fs = require('fs');
    const baseline = JSON.parse(fs.readFileSync('$baseline','utf8'));
    const current = JSON.parse(fs.readFileSync('$current','utf8'));
    const touched = new Set(fs.readFileSync('$touched_file','utf8').trim().split('\n').filter(Boolean));
    const baseFps = new Set(baseline.failures.map(f => f.fingerprint));
    const curFps = new Set(current.failures.map(f => f.fingerprint));
    const groups = { 'new': [], 'preexisting-touched': [], 'preexisting-untouched': [] };
    for (const f of current.failures) {
      if (!baseFps.has(f.fingerprint)) { groups['new'].push(f); continue; }
      // Match touched if any touched path ends with the failure's file basename, or equals the full path.
      const fileRel = f.file.replace(/\\\\/g,'/');
      let hit = false;
      for (const t of touched) {
        if (fileRel === t || fileRel.endsWith('/' + t) || t.endsWith('/' + fileRel)) { hit = true; break; }
        // Also try basename match for cases where Jest reports absolute path.
        const base = fileRel.split('/').pop();
        if (base && t.split('/').pop() === base && t.includes(base)) { hit = true; break; }
      }
      (hit ? groups['preexisting-touched'] : groups['preexisting-untouched']).push(f);
    }
    const fixed = baseline.failures.filter(f => !curFps.has(f.fingerprint));

    function block(label, arr) {
      console.log('\n=== ' + label + ' (' + arr.length + ') ===');
      for (const f of arr) {
        console.log('  - ' + f.file);
        console.log('      ' + f.test_name);
        if (f.first_line) console.log('      ' + f.first_line);
      }
    }
    block('NEW failures (block PR — must fix)', groups['new']);
    block('PRE-EXISTING failures in touched files (fix-on-touch or file an issue)', groups['preexisting-touched']);
    block('PRE-EXISTING failures in untouched files (note in PR body)', groups['preexisting-untouched']);
    if (fixed.length) block('Failures fixed since baseline (kudos)', fixed);

    console.log('\nSummary: ' + groups['new'].length + ' new, ' +
                groups['preexisting-touched'].length + ' preexisting-touched, ' +
                groups['preexisting-untouched'].length + ' preexisting-untouched, ' +
                fixed.length + ' fixed.');

    process.exit(groups['new'].length > 0 ? 1 : 0);
  "
  local rc=$?
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
