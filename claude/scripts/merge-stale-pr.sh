#!/usr/bin/env bash
# merge-stale-pr.sh — remediate a stale engineering-journal draft PR
#
# Usage: merge-stale-pr.sh <pr-number>
#
# Steps:
#   1. Look up the branch from the PR
#   2. Check out the branch, pull latest
#   3. Warn if no composed journal file exists on the branch
#   4. Delete orphaned *_draft.md files if present
#   5. Rebase against main (resolve README conflicts by keeping branch version)
#   6. Merge PR with squash, delete remote branch
#
# Prerequisites: gh CLI authenticated, run from the engineering-journal repo root
# or set JOURNAL_REPO env var.

set -euo pipefail

PR_NUMBER="${1:-}"
if [[ -z "$PR_NUMBER" ]]; then
  echo "Usage: merge-stale-pr.sh <pr-number>" >&2
  exit 1
fi

JOURNAL_REPO="${JOURNAL_REPO:-$HOME/Git/engineering-journal}"

if [[ ! -d "$JOURNAL_REPO/.git" ]]; then
  echo "Error: $JOURNAL_REPO is not a git repository." >&2
  echo "Set JOURNAL_REPO env var to point at the engineering-journal repo." >&2
  exit 1
fi

cd "$JOURNAL_REPO"

# ── Step 1: resolve branch from PR ────────────────────────────────────────────
echo "==> Fetching PR #${PR_NUMBER} details..."
BRANCH=$(gh pr view "$PR_NUMBER" --json headRefName --jq '.headRefName' 2>/dev/null)
if [[ -z "$BRANCH" ]]; then
  echo "Error: could not resolve branch for PR #${PR_NUMBER}." >&2
  exit 1
fi
echo "    Branch: $BRANCH"

# ── Step 2: check out branch and pull ─────────────────────────────────────────
echo "==> Checking out $BRANCH..."
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

# ── Step 3: check for composed journal ────────────────────────────────────────
DATE_PART="${BRANCH#draft/}"  # e.g. "2026-04-19"
COMPOSED_FILE=$(ls sessions/**/"${DATE_PART}"*.md 2>/dev/null | grep -v '_draft\.md' | grep -v '\.stub\.md' | head -1 || true)

if [[ -z "$COMPOSED_FILE" ]]; then
  echo "WARNING: No composed journal file found for $DATE_PART." >&2
  echo "         The PR may not have been composed yet. Proceed anyway? [y/N]" >&2
  read -r REPLY
  if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    echo "Aborted." >&2
    exit 1
  fi
else
  echo "    Composed journal: $COMPOSED_FILE"
fi

# ── Step 4: delete orphaned _draft.md files ───────────────────────────────────
DRAFTS=$(find sessions -name "*_draft.md" 2>/dev/null || true)
if [[ -n "$DRAFTS" ]]; then
  echo "==> Deleting orphaned draft files:"
  echo "$DRAFTS" | while IFS= read -r f; do
    echo "    $f"
    rm -f "$f"
  done
  git add -u
  git commit -m "chore: remove orphaned draft files from $DATE_PART" || true
fi

# ── Step 5: rebase against main ───────────────────────────────────────────────
echo "==> Rebasing against main..."
git fetch origin main

# Attempt rebase; on conflict, try auto-resolution for README files
if ! git rebase origin/main; then
  CONFLICTS=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
  echo "    Conflicts detected: $CONFLICTS"

  ALL_RESOLVABLE=true
  while IFS= read -r f; do
    if [[ "$f" == *README* ]]; then
      echo "    Resolving $f — keeping branch (ours) version..."
      git checkout --theirs "$f"
      git add "$f"
    else
      echo "    Cannot auto-resolve: $f" >&2
      ALL_RESOLVABLE=false
    fi
  done <<< "$CONFLICTS"

  if ! $ALL_RESOLVABLE; then
    echo "Error: non-README conflicts remain. Resolve manually and re-run." >&2
    git rebase --abort
    exit 1
  fi

  git rebase --continue --no-edit
fi

echo "==> Pushing rebased branch..."
git push --force-with-lease origin "$BRANCH"

# ── Step 6: merge PR (squash) and delete branch ───────────────────────────────
echo "==> Merging PR #${PR_NUMBER} (squash)..."
gh pr merge "$PR_NUMBER" --squash --delete-branch

echo ""
echo "Done. PR #${PR_NUMBER} merged and branch $BRANCH deleted."
