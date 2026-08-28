# ADR-142: Detect a Truncated `node_modules` by a Measured Property, and Let Only the Calibrated Signal Repair

**Date:** 2026-08-27
**Status:** Accepted
**Tags:** disk, worktrees, node_modules, hooks, npm-install, ENOSPC, truncation, detection-heuristic, instrument-calibration, measurement, known-bad-reference, repair-vs-advisory, adr-016, adr-037, adr-045, adr-115

---

## Context

A `node_modules` tree that **exists but is incomplete** is a recurring cross-repo hazard, and it was
un-gated. `worktree-npm-install.py` ([ADR-016](016-worktree-npm-auto-install.md), extended by
[ADR-045](045-pre-install-freespace-gate.md)) treated *presence* of the directory as its sentinel, so a
truncated tree exited the hook as "already installed."

Occurrences this gate is filed against (biweekly-retro 2026-08-08, dev-env#963 → dev-env#970):

- **cover-letter-runtime** — 4× on 08-04/05 ([dev-env#945](https://github.com/brownm09/dev-env/issues/945)).
  Two named packages: `@langchain/core` missing its own `package.json` entirely (`npm ls` reported it
  `invalid`), and `zod` missing `package.json`/`index.js`/`index.d.ts` at the package root while its
  subdirectories were present.
- **gas-lifting-logbook** — ~90% empty shells on 07-26.
- **lifting-logbook** — nearly every session (#721).

Each was re-diagnosed from scratch, because truncation never announces itself. It surfaces as a
confidently-misleading downstream error — `ERR_UNSUPPORTED_DIR_IMPORT` from an ESM resolver,
`MODULE_NOT_FOUND` deep in a load chain — exactly the read-past-the-top-line problem
[ADR-034](034-error-message-diligence.md) is about.

Note what dev-env#945 established and this ADR inherits: **the root cause is not confirmed.** ADR-045's
incident (dev-env#364) was genuine `C:` exhaustion, but #945 measured 30 GB free at diagnosis time.
Antivirus interference, a transient dip, and a concurrent-install race are all still live hypotheses.
This gate therefore detects and repairs the *state*; it does not claim a cause.

## Decision

### 1. The discriminator is chosen by measurement, not by plausibility

Calibrated 2026-08-27 against **all 48 real `node_modules` trees** under `C:/Users/brown/Git`. Two
designs that look obviously right were built, measured, and **rejected on their numbers**:

| Candidate | Measured result | Verdict |
|---|---|---|
| Audit `node_modules/.package-lock.json` (npm's own install receipt) — for each recorded entry that is not `link: true` and not an `os`/`cpu`-excluded optional, require the package dir + its `package.json` | Flags **12/48**. Inspection: essentially all false positives — the hidden lockfile records the tree from whenever npm last ran, so a branch that no longer has `apps/api-legacy` reads as truncated, and transitively-optional wasm fallbacks (`@emnapi/*`, `@napi-rs/wasm-runtime`, `@tybys/wasm-util`) read as missing. Cost up to **2.6 s** | Rejected — noisy *and* slow |
| Treat a missing `node_modules/.package-lock.json` as "the install never completed" | Fires on **16/48**, most of them healthy trees | Rejected — a diagnostic, not verdict-bearing |

What separates cleanly is narrower. Call a package directory **PARTIAL** when it is non-empty, is not a
symlink/junction, and lacks its own `package.json`:

| Tree class | n | PARTIAL |
|---|---|---|
| Known-good trees | 38 | **0** |
| lifting-logbook `fervent-bartik` / `keen-raman` / `lift-abbreviation` (~100% shell) | 3 | 127 each |
| cover-letter-runtime `reverent-kowalevski-79b384` | 1 | 21, including **`@langchain/core`** |
| gas-lifting-logbook `sharp-lumiere` / `serene-noyce` / `priceless-faraday` | 3 | 24 / 4 / 2 |

`@langchain/core` is the package **dev-env#945 itself named**, still truncated on disk at calibration
time. That makes this a genuine known-bad reference rather than a synthetic one — the calibration
requirement the global `## Experimental Rigor` rule states (instruments calibrated against known-good
*and* known-bad before scoring).

The two benign classes that defeated the naive scan are excluded **by construction, not by threshold**:

- **npm workspace links** (`@lifting-logbook/api` → `apps/api`) are Windows *junctions*. `islink()`
  returns False for a junction, so `os.path.isjunction` is checked too — the resolved target lives in
  the repo and says nothing about the install.
- **Skipped optional platform deps** (`@esbuild/linux-x64` and 40–70 siblings per tree) are *empty*
  directories. npm creates the directory and skips extraction when `os`/`cpu` do not match. Empty is a
  different class from partial, so no threshold is needed to tell them apart — and the *matching*
  platform's binary (`@esbuild/win32-x64`) is still checked, which is what keeps ADR-045's truncated-
  native-binary case in scope.

### 2. Only the calibrated signal may repair

- **PARTIAL ≥ 1 → repair.** Run `npm ci` **through the existing `_gate_install()`**, so ADR-045's
  reclamation ladder and 5 GB hard-floor refusal apply unchanged. Reinstalling onto a near-full disk is
  one of the ways the truncation plausibly got there, so the repair must not be able to re-create it.
  Without a `package-lock.json` there is no clean-slate `npm ci`, so that case advises instead.
- **Empty-shell ratio ≥ 0.50, or zero package dirs → advise only.** Both are suspicious shapes with **no
  confirmed positive of their own**: every broken tree in the corpus was already caught by PARTIAL, and
  a wholly empty `node_modules` is unrepresented in the corpus entirely. Per the global rule that *an
  uncalibrated check is a diagnostic, not verdict-bearing*, neither may trigger a destructive reinstall.
  0.50 sits at a 2.3× margin over the worst benign tree measured (21.3%) — deliberately wide, because
  the arm only ever prints a sentence.

This asymmetry is the substance of the repair-vs-advisory decision dev-env#970 asked for. It is not
caution for its own sake: it is the difference between a signal that has a measured false-positive rate
and one that does not.

### 3. A live install suppresses the gate

Found during calibration, not reasoned about in advance: re-scanning the corpus a few minutes later
returned **different numbers**, because a concurrent `npm install` was extracting into one of the
worktrees. npm extracts each package to a sibling `.<name>-XXXXXXXX` directory and renames it into place
on completion, so a healthy in-flight install is *full* of directories with exactly the PARTIAL shape —
42 of them in the tree caught mid-flight, plus 8 genuinely half-populated packages.

So the staging directories become the **suppression** signal: any of them means an install is running,
and the audit returns without repairing, advising, or setting its sentinel — the next prompt re-audits
once the install lands. `is_staging_name()` deliberately **over-matches** (inside an `@scope` directory
every child is a package, so any dot-entry there is bookkeeping; at the top level only the `-XXXXXXXX`
suffix qualifies, leaving `.bin`/`.cache`/`.vite-temp`/`.package-lock.json` alone). A false "staging"
reading suppresses the gate and costs nothing; a false "partial" reading would run `npm ci` over
somebody's running install.

Verified that this costs no detection power: after filtering staging dirs, every known-bad tree still
fires on genuine partials (21 / 127 / 127 / 8 / 4 / 2).

### 4. The audit runs once per session per worktree

The scan costs ~0.4 s on a typical tree and 1.65 s worst-measured, which is too much on every prompt and
nothing at all once. A sentinel keyed on `session_id` + a hash of the worktree path bounds it, following
`disk-space-check.py`'s once-per-session pattern. The sentinel is written **before** acting, so a repair
that fails cannot retry on every prompt for the rest of the session.

Scope: the **top-level tree only**. A workspace's own nested `node_modules` (`apps/api/node_modules/…`)
is not scanned — no calibration data covers that shape, and a root `npm ci` reinstalls the workspaces
anyway.

### 5. What this deliberately does not detect

**Intra-package file absence.** A package whose `package.json` is present but whose build output is not —
`std-env/dist/index.mjs` missing while `dist/index.cjs` is there — is a different layer, tracked by
[dev-env#242](https://github.com/brownm09/dev-env/issues/242) and its open PR
[#246](https://github.com/brownm09/dev-env/pull/246), which repairs by copying from the main checkout.
That PR is left open rather than absorbed or closed: its trigger (a skipped `postinstall`) and its repair
strategy are both different, and copying a package's files from the main checkout can mix versions when
the worktree's branch moved the lockfile. Both issues carry a cross-reference to this boundary.

## Consequences

- A truncated `node_modules` is now named as such on the first prompt in a worktree, instead of being
  re-diagnosed from a downstream ESM/resolver error. The dev-env#945 case repairs itself.
- The gate cannot fight ADR-045: a repair is a gated install, so a low-disk repair refuses rather than
  re-truncating.
- It cannot clobber a concurrent install, and it fails open on every measurement error — an unreadable
  tree returns `None` and is distinguishable from "measured, found nothing," so a permissions error
  cannot reach the advisory path.
- `worktree-npm-install.py` keeps a single install/report code path: the absent-tree install and the
  truncation repair share `_run_install()`, so the two report identically and only their reason differs.
- The empty-shell and empty-tree arms will stay advisory until something gives them a confirmed positive.
  If one ever fires on a tree that turns out to be genuinely broken, that observation is what would
  promote it — recorded here so a future session does not silently upgrade it without one.
- Two known gaps, stated rather than papered over: a **crashed** install that leaves staging directories
  behind permanently suppresses the gate in that worktree (fail-safe direction, but silent), and nested
  workspace `node_modules` trees are unscanned.
- The calibration is a snapshot of one machine on one date. It is reproducible — the corpus is every
  `node_modules` under `C:/Users/brown/Git` — but the ratio floor and the benign-ceiling figure the tests
  pin are properties of *these* repos' optional-dependency counts, not universal constants.

## References

- [ADR-016](016-worktree-npm-auto-install.md) — the auto-install hook this extends from
  absent-only to absent-or-truncated
- [ADR-045](045-pre-install-freespace-gate.md) — the free-space gate every repair routes through
- [ADR-037](037-worktree-disk-reclamation.md) — why a worktree's `node_modules` is regenerable, which is
  what makes an automatic `npm ci` a safe repair rather than a destructive one
- [ADR-115](115-experimental-rigor-protocol.md) — the calibrated-instrument rule that decides which
  signal may repair and which may only advise
- [ADR-034](034-error-message-diligence.md) — the misleading-downstream-error problem this removes a
  recurring instance of
- dev-env#970 (this gate), dev-env#963 (the retro queue that surfaced it), dev-env#945 / #721 (the
  recurrences and the `@langchain/core` known-bad reference), dev-env#364 (ADR-045's ENOSPC incident),
  dev-env#242 / PR #246 (the intra-package-file layer left out of scope)
- [npm hidden lockfile (`node_modules/.package-lock.json`)](https://docs.npmjs.com/cli/v10/configuring-npm/package-lock-json#hidden-lockfile)
  — npm's own documentation of the install receipt evaluated and rejected as the discriminator
