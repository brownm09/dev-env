#!/usr/bin/env bash
# check-remote-read-hygiene.sh — lint for the dev-env#602 / #877 failure class.
#
# Bug class: a remote read of the form `git show <ref>:<path>` paired with
# `2>/dev/null`, used to decide whether a file EXISTS. On Windows/Git-Bash, MSYS
# path conversion rewrites the `<ref>:<path>` argument into a single Windows-style
# token — `origin/main:.gitignore` becomes `origin\main;.gitignore` — and git exits
# non-zero with `fatal: ambiguous argument`. The `2>/dev/null` swallows that
# `fatal:`, leaving empty output indistinguishable from "the file is absent" or
# "the pattern is not present". The failure mode is a check that reports CLEAN,
# not one that errors.
#
# The mangle is DETERMINISTIC, not intermittent: the trigger is a leading-dot path
# segment immediately after the `:`, independent of path depth (`origin/main:.gitignore`
# mangles; `origin/main:claude/skills/review/SKILL.md` does not). Quoting does not help.
# See dev-env#602, #877, ADR-117 (CLI Scripting Checklist item 5) and ADR-120.
#
# Rule: a non-comment line under claude/** must not pair a `git show <ref>:<path>`
# with `2>/dev/null`. Use `MSYS_NO_PATHCONV=1` and let stderr through, or read the
# blob over the API:
#   gh api "repos/<OWNER>/<REPO>/contents/<path>?ref=<ref>" -H "Accept: application/vnd.github.raw"
#
# Note this rule deliberately keys on the CO-OCCURRENCE of `git show` and
# `2>/dev/null` on one line, not on `git show <ref>:<path>` alone — the sanctioned
# `MSYS_NO_PATHCONV=1 git show ...` form carries no `2>/dev/null`, so it never trips.
#
# Comments are stripped before matching so a file may *document* the hazard (as
# claude/skills/review/SKILL.md and claude/CLAUDE.md both do at length) without
# tripping the lint. Markdown prose is not a comment, so documentation files spell
# the pattern out inside fenced/inline code or as separated tokens; a genuine
# offender is an executable-looking line, which is what this catches.
#
# The two dedicated test directories are EXCLUDED, for the reason ADR-116's
# anti-regression pass already established (Testing item 78): a gate asserting a
# pattern is absent necessarily contains that pattern — this script's own diagnostic
# `echo` lines are executable, not comments, so a self-scan flags it every time.
# Nothing under claude/scripts/tests or claude/hooks/tests performs a remote read to
# decide an absence, so the exclusion costs no real coverage; the target surface is
# claude/skills, claude/routines, claude/hooks and claude/scripts.
#
# Exit 0 = clean; exit 1 = at least one offender (offending file + lines printed
# to stderr). Run: bash claude/scripts/tests/check-remote-read-hygiene.sh
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$REPO_ROOT"

# Scan every tracked file under claude/ — skills and routines are Markdown, hooks
# and scripts are Python/bash, and the failure class is identical in all of them
# (a session executes what the file tells it to). Binary/vendored paths are excluded
# by using git's own tracked-file list rather than a filesystem walk.
#
# NOTE the pathspec uses git's `:(exclude)` magic rather than filtering after the
# fact. `git ls-files` lists TRACKED files only, so an as-yet-uncommitted script is
# invisible to it — which is precisely how this gate passed locally while failing in
# CI on its first run: it was still untracked when tested. Verify any change to this
# file with the file staged (`git add -N`), never from a clean working tree.
mapfile -t FILES < <(
  git ls-files -- 'claude/**' ':(exclude)claude/scripts/tests/**' ':(exclude)claude/hooks/tests/**' | sort -u
)

OFFENDERS=0
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue

  # Real line numbers preserved: grep -n on the file, then drop full-line comments
  # (`#` for sh/python, `>` for markdown blockquote prose) so a file may document
  # the hazard without being flagged.
  hits=$(grep -nE 'git[[:space:]]+show[^|;&]*2>[[:space:]]*/dev/null' "$f" \
           | grep -vE '^[0-9]+:[[:space:]]*[#>]' || true)

  if [ -n "$hits" ]; then
    OFFENDERS=$((OFFENDERS + 1))
    {
      echo "OFFENDER: $f"
      echo "  pairs 'git show' with 2>/dev/null (dev-env#602 / #877 class):"
      echo "$hits" | sed 's/^/    /'
      echo "  Fix: drop the 2>/dev/null and prefix MSYS_NO_PATHCONV=1, or read the blob via"
      echo "       gh api \"repos/<OWNER>/<REPO>/contents/<path>?ref=<ref>\" -H \"Accept: application/vnd.github.raw\""
      echo "       and classify by exit status + stderr text, never by stdout emptiness."
    } >&2
  fi
done

if [ "$OFFENDERS" -gt 0 ]; then
  echo "remote-read-hygiene: $OFFENDERS offending file(s) — see above." >&2
  exit 1
fi
echo "remote-read-hygiene: clean (${#FILES[@]} tracked files under claude/ scanned; no 'git show' paired with 2>/dev/null)."
