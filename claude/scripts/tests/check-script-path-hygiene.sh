#!/usr/bin/env bash
# check-script-path-hygiene.sh — lint for the dev-env#334 failure class.
#
# Bug class: a scratch/temp path derived from $HOME is written by a Git Bash
# redirect (which maps the MSYS `/c/Users/...` to `C:\Users\...`) but then read
# back by Node-on-Windows, which resolves a leading `/c/...` against the current
# drive (`C:\c\Users\...`). The write and the read target different files →
# ENOENT on every invocation. See dev-env#334 and the fix in get-project-item.sh.
#
# Rule: a shell script that (a) invokes `node`, AND (b) assigns a path rooted at
# $HOME / ${HOME} on a NON-comment line, is at risk. Such scripts must use the
# literal Windows-style scratch path `C:/Users/brown/.claude/scratch` instead, so
# Git Bash and Node resolve it identically (matches DEFAULT_LOG in
# session-mode-report.py and the scratch-dir convention in CLAUDE.md).
#
# Comments are stripped before matching so a script may *document* the hazard
# (as get-project-item.sh does) without tripping the lint.
#
# Exit 0 = clean; exit 1 = at least one offender (offending file + lines printed
# to stderr). Run: bash claude/scripts/tests/check-script-path-hygiene.sh
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$REPO_ROOT"

# Shell files to scan: *.sh under scripts/ (+ tests) plus shebang-#!sh hook files.
mapfile -t FILES < <(
  {
    ls claude/scripts/*.sh claude/scripts/tests/*.sh claude/hooks/tests/*.sh 2>/dev/null
    for f in claude/hooks/*; do
      # `.*sh\b` (not `\bsh\b`): the interpreter basename ENDS in sh — matches
      # `#!/bin/sh`, `#!/usr/bin/env bash`, `zsh`. A leading `\b` would miss
      # `bash` (no word boundary inside "bash"), silently skipping bash hooks.
      [ -f "$f" ] && head -1 "$f" | grep -qE '^#!.*sh\b' && echo "$f"
    done
  } | sort -u
)

# grep the file for $1 with real line numbers, dropping full-line comments so a
# script that merely *documents* the hazard (mentions node or $HOME in a comment)
# is not flagged. Real file line numbers are preserved (grep -n on the file, then
# filter out `N:   # ...` comment lines) so offender output points at the true line.
OFFENDERS=0
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  code_lines() { grep -nE "$1" "$f" | grep -vE '^[0-9]+:[[:space:]]*#' || true; }
  # (a) does it actually call node (outside comments)?
  [ -n "$(code_lines '\bnode\b')" ] || continue
  # (b) does it assign a $HOME-rooted path (outside comments)?
  hits=$(code_lines '=[[:space:]]*"?'"'"'?\$\{?HOME\}?/')
  if [ -n "$hits" ]; then
    OFFENDERS=$((OFFENDERS + 1))
    {
      echo "OFFENDER: $f"
      echo "  invokes node AND builds a \$HOME-rooted path (dev-env#334 class):"
      echo "$hits" | sed 's/^/    /'
      echo "  Fix: use the literal path C:/Users/brown/.claude/scratch so Git Bash and Node agree."
    } >&2
  fi
done

if [ "$OFFENDERS" -gt 0 ]; then
  echo "path-hygiene: $OFFENDERS offending script(s) — see above." >&2
  exit 1
fi
echo "path-hygiene: clean (${#FILES[@]} shell files scanned; no \$HOME-rooted path passed to node)."
