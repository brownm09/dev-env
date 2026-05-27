#!/bin/bash
# new-branch: create a branch always rooted at origin/main
#
# Usage (after sourcing):  new-branch <branch-name>
#
# In squash-merge repos, cutting from a branch that has already been merged
# leaves the new branch rooted at a commit that no longer exists on main.
# This function always checks out from origin/main, warning if HEAD has diverged.

new-branch() {
  local branch_name="$1"
  if [ -z "$branch_name" ]; then
    echo "Usage: new-branch <branch-name>" >&2
    return 1
  fi

  git fetch origin

  local merge_base main_tip
  merge_base=$(git merge-base HEAD origin/main)
  main_tip=$(git rev-parse origin/main)

  if [ "$merge_base" != "$main_tip" ]; then
    echo "WARNING: HEAD is not based on origin/main tip." >&2
    echo "  merge-base : ${merge_base:0:8}" >&2
    echo "  origin/main: ${main_tip:0:8}" >&2
    echo "Creating branch from origin/main (recommended). Ctrl-C to cancel." >&2
    sleep 2
  fi

  git checkout -b "$branch_name" origin/main

  # Optional: snapshot pre-existing test failures so the fix-on-touch policy
  # (ADR-030) can distinguish "introduced on this branch" from "inherited".
  # Opt-in per repo via `.claude/hook-config.json` field
  # `"baseline_test_failure_tracking": true`. Bypass for one invocation by
  # setting BASELINE_TESTS_SKIP=1 (e.g., when the test suite is too slow today).
  if [ "${BASELINE_TESTS_SKIP:-0}" = "1" ]; then
    echo "BASELINE_TESTS_SKIP=1 — skipping baseline snapshot." >&2
  elif [ -f .claude/hook-config.json ]; then
    local enabled
    enabled=$(node -e '
      try {
        const d = JSON.parse(require("fs").readFileSync(".claude/hook-config.json", "utf8"));
        process.stdout.write(d.baseline_test_failure_tracking === true ? "1" : "0");
      } catch (e) { process.stdout.write("0"); }
    ' 2>/dev/null)
    if [ "$enabled" = "1" ]; then
      if command -v baseline-tests >/dev/null 2>&1; then
        echo "Capturing pre-existing test failure baseline (ADR-030)..." >&2
        baseline-tests snapshot || echo "WARNING: baseline snapshot failed; fix-on-touch diff will be unavailable." >&2
      elif [ -x ~/.claude/scripts/baseline-tests.sh ]; then
        echo "Capturing pre-existing test failure baseline (ADR-030)..." >&2
        bash ~/.claude/scripts/baseline-tests.sh snapshot || echo "WARNING: baseline snapshot failed." >&2
      else
        echo "WARNING: baseline_test_failure_tracking is enabled but baseline-tests is not on PATH." >&2
      fi
    fi
  fi
}
