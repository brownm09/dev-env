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
}
