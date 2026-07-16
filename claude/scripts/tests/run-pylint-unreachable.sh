#!/usr/bin/env bash
# run-pylint-unreachable.sh — run pylint's unreachable-code check (W0101) over
# claude/scripts/*.py.
#
# Single-check gate: --disable=all --enable=unreachable runs ONLY pylint's
# dead-code-after-return/raise/continue/break detector (pylint's basic
# checker; a pure control-flow check, independent of type annotations) — not
# a general pylint rollout. mypy's --warn-unreachable was considered and
# rejected: it skips the bodies of untyped functions by default, and there is
# no clean way to run "just unreachable" without either fixing the whole tree
# to be mypy-clean or grep-filtering a wave of unrelated type errors. Ruff has
# no unreachable-code rule at all (astral-sh/ruff#970 still lists pylint's
# W0101 as unimplemented). See dev-env#815 / ADR-112 for the full comparison.
#
# Motivating bug: a trailing `return` in _resolve_worktree_scope()
# (pre-tool-use-worktree-path-check.py) could never execute and went
# undetected until noticed by hand during an unrelated refactor
# (dev-env#813 / PR #814). Nothing in this suite caught it.
#
# Skip-if-absent: pylint is a pip package, not a system binary. When missing,
# the check SKIPS (exit 0) with an install hint, matching run-shellcheck.sh's
# convention, so a fresh checkout never hard-fails on a missing dev
# dependency. CI installs it explicitly (see .github/workflows/hook-tests.yml)
# so it is NOT self-skipped there.
#   py -3 -m pip install "pylint==4.0.6"    # Windows local dev (ADR-007)
#   python -m pip install "pylint==4.0.6"   # CI / plain python on PATH
#
# Run: bash claude/scripts/tests/run-pylint-unreachable.sh
set -u

REPO_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$REPO_ROOT" || exit 1

# Resolve a working `python -m pylint` invocation. Mirrors run-shellcheck.sh's
# SHELLCHECK_BIN override. Tried in order: an explicit override, the Windows
# `py -3` launcher (ADR-007's local-dev convention — `python`/`python3` can
# resolve to the Microsoft Store stub there), then plain `python` (what CI's
# actions/setup-python puts first on PATH; `py` is not guaranteed present on
# the runner image).
resolve_pylint() {
  # shellcheck disable=SC2086  # intentional word-splitting: PYLINT_CMD may be a launcher + flags
  if [ -n "${PYLINT_CMD:-}" ] && $PYLINT_CMD --version >/dev/null 2>&1; then
    echo "$PYLINT_CMD"
    return 0
  fi
  if command -v py >/dev/null 2>&1 && py -3 -m pylint --version >/dev/null 2>&1; then
    echo "py -3 -m pylint"
    return 0
  fi
  if command -v python >/dev/null 2>&1 && python -m pylint --version >/dev/null 2>&1; then
    echo "python -m pylint"
    return 0
  fi
  return 1
}

PYLINT_INVOCATION=$(resolve_pylint) || {
  echo "SKIP: pylint not found (install: py -3 -m pip install \"pylint==4.0.6\", or python -m pip install \"pylint==4.0.6\")."
  exit 0
}

mapfile -t FILES < <(ls claude/scripts/*.py 2>/dev/null | sort)

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "run-pylint-unreachable: no Python files found under claude/scripts/." >&2
  exit 1
fi

echo "run-pylint-unreachable: scanning ${#FILES[@]} files with '$PYLINT_INVOCATION' (--disable=all --enable=unreachable)."

# shellcheck disable=SC2086  # intentional word-splitting: $PYLINT_INVOCATION is a launcher + flags
if $PYLINT_INVOCATION --disable=all --enable=unreachable --score=n "${FILES[@]}"; then
  echo "run-pylint-unreachable: PASS (0 unreachable-code findings)."
  exit 0
else
  echo "run-pylint-unreachable: FAIL — unreachable-code findings above must be fixed (dev-env#815 / ADR-112)." >&2
  exit 1
fi
