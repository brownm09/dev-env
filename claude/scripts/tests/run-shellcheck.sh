#!/usr/bin/env bash
# run-shellcheck.sh — run shellcheck over the repo's shell scripts and hooks.
#
# Blocking gate at --severity=error: the current tree is error-clean (verified
# 2026-06-07, shellcheck 0.10.0), so any newly introduced hard shell bug
# (undefined-behaviour quoting, unreachable code, bad redirections) fails the
# check. Warnings/info are pre-existing style/hygiene and are printed advisorily
# (non-blocking) — they are tracked separately, not gated here.
#
# Skip-if-absent: shellcheck is not installed by default on this machine and
# cannot be installed without elevation (choco needs admin). When it is missing
# the check SKIPS (exit 0) with an install hint rather than failing, so it never
# blocks a PR on a tool the environment lacks. To enable the gate locally:
#   choco install shellcheck      # from an elevated shell, OR
#   download the portable zip from https://github.com/koalaman/shellcheck/releases
#     and put shellcheck(.exe) on PATH (or set SHELLCHECK_BIN to its path).
#
# Run: bash claude/scripts/tests/run-shellcheck.sh
set -u

REPO_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$REPO_ROOT" || exit 1

SC="${SHELLCHECK_BIN:-shellcheck}"
if ! command -v "$SC" >/dev/null 2>&1; then
  echo "SKIP: shellcheck not found (set SHELLCHECK_BIN or install: choco install shellcheck)."
  echo "      Portable: https://github.com/koalaman/shellcheck/releases"
  exit 0
fi

# Enumerate shell files: *.sh under scripts/ (+ test dirs) plus #!sh hook files.
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

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "run-shellcheck: no shell files found." >&2
  exit 1
fi

echo "run-shellcheck: scanning ${#FILES[@]} shell files with $("$SC" --version | awk '/version:/{print $2}')."

# Advisory pass (warnings + info) — informational, never blocks.
echo "--- advisory (warning/info, non-blocking) ---"
"$SC" --severity=info "${FILES[@]}" || true

# Blocking pass (errors only).
echo "--- blocking (error severity) ---"
if "$SC" --severity=error "${FILES[@]}"; then
  echo "run-shellcheck: PASS (0 error-severity findings)."
  exit 0
else
  echo "run-shellcheck: FAIL — error-severity shellcheck findings above must be fixed." >&2
  exit 1
fi
