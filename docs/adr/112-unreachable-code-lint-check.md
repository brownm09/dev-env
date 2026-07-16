# ADR 112 — Unreachable-Code Lint Check for Hook Scripts (pylint `unreachable` / W0101)

**Date:** 2026-07-16
**Status:** Accepted
**Tags:** hooks, testing, ci, lint, pylint, mypy, ruff, unreachable-code, dead-code, quality, workflow, tool-evaluation

---

## Context

[dev-env#813](https://github.com/brownm09/dev-env/issues/813) / [PR #814](https://github.com/brownm09/dev-env/pull/814)
removed a trailing `return` statement in `_resolve_worktree_scope()`
(`claude/scripts/pre-tool-use-worktree-path-check.py`) that could never execute — the line
above it always returned first, and the dead line referenced a variable (`matched`) that is
`None` on that code path. It was noticed only by hand, during an unrelated refactor
(dev-env#780 / PR #810).

Nothing in the existing verification suite (`py -3 claude/scripts/run-hook-tests.py`, the
73-item `## Testing` list, or the CI workflow) catches this class of bug. `ast.parse` (item 1)
and `bash -n`-equivalent checks only catch syntax errors — a dead-but-syntactically-valid
statement parses fine. The rest of the suite is behavior-level unit tests, which by
construction only exercise reachable code paths; a test suite proving a line is unreachable
would be a strange test to write on purpose. Filed as dev-env#815 (a non-blocking
`[maintainability]` finding on PR #814's review) to close that gap with a static-analysis
check instead of relying on it being noticed by hand again.

## Decision

**1. Tool choice: pylint's `unreachable` (W0101) check, run in isolation.**

Three candidates were evaluated, as the issue directed:

- **Ruff — rejected, no such rule exists.** Ruff tracks parity with pylint's message set;
  [astral-sh/ruff#970](https://github.com/astral-sh/ruff/issues/970), the tracking issue for
  pylint-rule parity, still lists `unreachable`/W0101 as unimplemented. There is nothing to
  wire in.
- **mypy `--warn-unreachable` — rejected.** Per the
  [mypy command-line docs](https://mypy.readthedocs.io/en/stable/command_line.html), this flag
  "report[s] an error whenever it encounters code determined to be unreachable or redundant
  after performing type analysis" — but by default mypy does not type-check the bodies of
  functions with no type annotations at all, so unreachable code inside a fully-untyped
  function would go undetected unless `--check-untyped-defs` is also passed. More decisively,
  mypy has no first-class way to report *only* unreachable-code findings: `--warn-unreachable`
  runs on top of a full type-analysis pass, and this 68-file tree has never been mypy-checked
  before. Adopting it meaningfully would mean either (a) bringing the whole tree to
  mypy-clean first — an open-ended, unscoped undertaking with unknown size — or (b)
  grep-filtering broad mypy output down to `[unreachable]`-tagged lines only, silently
  discarding every other error mypy finds. Both are a much bigger and riskier lift than this
  issue's stated scope ("this is a repo-wide tooling addition... deliberately out of scope for
  the one-line dead-code deletion").
- **pylint `unreachable` (W0101) — chosen.** Per
  [pylint's message docs](https://pylint.readthedocs.io/en/stable/user_guide/messages/warning/unreachable.html),
  this check is implemented in pylint's basic checker as a pure control-flow analysis — code
  positioned after a `return`/`raise`/`continue`/`break` that unconditionally exits — with no
  dependency on type annotations at all. Pylint has a first-class, officially documented
  mechanism for running exactly one check in isolation: `--disable=all --enable=unreachable`
  (per [pylint's message-control docs](https://pylint.readthedocs.io/en/stable/user_guide/messages/message_control.html)).
  This is a precise, low-risk fit for the issue's scope — it flags exactly the #813 class of
  bug and nothing else, with no risk of surfacing an unrelated wave of type-checking debt.

**2. New gate: `claude/scripts/tests/run-pylint-unreachable.sh`.**

Mirrors `run-shellcheck.sh`'s established shape (an external-tool lint gate, self-skipping
when the tool is absent):

- Scoped to `claude/scripts/*.py` (68 files) — the flat top-level directory named in the
  issue, not recursively into `claude/scripts/tests/`. A deliberate initial-scope decision to
  match what was asked; widening to test files is a natural, low-risk future extension (same
  tool, same check, just a wider glob) but not done here.
- Resolves a working `pylint` invocation itself (`py -3 -m pylint` first, matching this
  machine's [ADR-007](007-hook-command-invocation.md) local-dev convention where `python`/
  `python3` can resolve to the Microsoft Store stub; falls back to plain `python -m pylint`,
  matching what CI's `actions/setup-python` puts first on PATH) — with a `PYLINT_CMD`
  override, mirroring `run-shellcheck.sh`'s `SHELLCHECK_BIN` override.
- **SKIPs (exit 0) with an install hint when pylint is absent** — same convention as
  `run-shellcheck.sh`, so a fresh checkout without pylint installed never hard-fails.
- Auto-discovered by `run-hook-tests.py`'s existing `discover_bash_tests()` glob — no runner
  change needed.

**3. CI wiring: pylint is installed explicitly, unlike shellcheck.**

Shellcheck self-skips in CI too (`choco install` needs elevation the hosted runner doesn't
grant). Pylint is a plain pip package needing no elevation, so
`.github/workflows/hook-tests.yml` gains a `python -m pip install "pylint==4.0.6"` step. This
is the difference that actually satisfies the issue's goal — catching this bug class **in
CI**, not only when a contributor happens to have pylint installed locally. Version-pinned
(4.0.6, the latest stable release as of this writing) for reproducibility, matching the
SHA-pinned `actions/checkout` / `actions/setup-python` steps already in this workflow.

**4. First-pass scan: zero additional latent instances.**

Ran the new check against the current `claude/scripts/*.py` tree: clean (exit 0, no
findings) — the #813 instance, already fixed in PR #814, was the only one. Verified the
check itself is genuinely functioning (not a silent no-op) with a synthetic smoke test: a
throwaway file with a deliberate `return` followed by a dead statement correctly produces
`W0101: Unreachable code (unreachable)` at exit code 4 (pylint's warning-category bitmask
bit). No scope-guard decision about fixing/filing additional findings was needed.

## Considered alternatives

- **mypy `--warn-unreachable`, grep-filtered to `[unreachable]` lines only.** Rejected —
  works, but is a hack layered over a tool doing much more than asked, with no first-class
  support for the narrow use case; pylint's `--disable=all --enable=unreachable` does the same
  job as a supported, documented invocation.
- **Widen scope to `claude/scripts/tests/*.py` and `claude/hooks/` in this PR.** Deferred —
  the issue's literal ask is `claude/scripts/*.py`; `claude/hooks/` contains only a shell
  `pre-push` script (no Python to check) as of this writing. Extending the glob to
  `claude/scripts/tests/*.py` is a one-line follow-up if wanted, not done here to keep this
  PR's diff matched to its stated scope.
- **A dedicated `test_*.py` wrapper instead of a bash gate.** Rejected — pylint is an external
  tool invoked as a subprocess, architecturally identical to shellcheck, not local pure logic
  needing offline unit tests. The bash-gate-with-self-skip shape is the established precedent
  for exactly this case (item 7 / `run-shellcheck.sh`), and it is auto-discovered identically
  by `run-hook-tests.py` regardless of extension.
- **Leave pylint unpinned (`pip install pylint`).** Rejected — this workflow already
  SHA-pins its GitHub Actions specifically for supply-chain/reproducibility reasons; an
  unpinned pip install would be the one unpinned dependency in an otherwise pinned workflow,
  and a future pylint major version could change unreachable-detection behavior under CI's
  feet with no local signal.

## Consequences

- First Python lint/static-analysis dependency in this repo's history — `pylint` now appears
  in CI (`.github/workflows/hook-tests.yml`) and as an optional local dev tool.
- CI gains one new step (`pip install`) and one new gate; adds a few seconds to the
  `hook-tests` job, well within its 30-minute timeout.
- Local contributors without pylint installed see the gate self-skip (matching the
  shellcheck experience) — CI is the actual enforcement backstop, same division of labor as
  shellcheck's local-optional/CI-self-skip split, except this gate does NOT self-skip in CI.
- dev-env `CLAUDE.md` `## Testing` gains item 75; `docs/REFERENCE.md`'s Script verification
  suite table gains a row.
- Future broader lint adoption (style, unused-import, or other pylint/ruff/mypy categories)
  is a separate, larger decision with its own scope and rationale — not implied or
  pre-approved by this ADR.
- The pinned version (`pylint==4.0.6`) is duplicated across five places — the CI workflow,
  this file's own comments/references, the bash gate's install-hint strings, the `CLAUDE.md`
  item, and the `docs/REFERENCE.md` row — with no single source of truth, and pylint's
  transitive dependencies (astroid, platformdirs, etc.) are left unpinned. A future version
  bump should `grep -rn "pylint==4.0.6"` across the repo rather than assuming the CI workflow
  is the only place it lives; not worth a shared-version-file mechanism for one pinned dev
  dependency (review finding on PR #818).

## References

- [dev-env#815](https://github.com/brownm09/dev-env/issues/815) (this change),
  [dev-env#813](https://github.com/brownm09/dev-env/issues/813) /
  [PR #814](https://github.com/brownm09/dev-env/pull/814) (the motivating incident).
- Pylint `unreachable` / W0101:
  <https://pylint.readthedocs.io/en/stable/user_guide/messages/warning/unreachable.html>
- Pylint messages control (`--disable=all --enable=<check>` recipe):
  <https://pylint.readthedocs.io/en/stable/user_guide/messages/message_control.html>
- mypy `--warn-unreachable` / `--check-untyped-defs`:
  <https://mypy.readthedocs.io/en/stable/command_line.html>
- Ruff / pylint parity tracking (W0101 unimplemented):
  <https://github.com/astral-sh/ruff/issues/970>
- [ADR-007](007-hook-command-invocation.md) — `py -3` launcher convention this gate's
  interpreter-resolution fallback mirrors.
- `claude/scripts/tests/run-pylint-unreachable.sh`, `.github/workflows/hook-tests.yml`.
