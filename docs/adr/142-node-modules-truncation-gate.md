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

**Two honesty notes about this corpus, both found in review rather than at calibration time.**

*The benign ceiling was contaminated, and is corrected here.* The first draft of this ADR justified
the 0.50 empty-shell floor against a "benign ceiling" of **21.3%** — which was
`reverent-kowalevski-79b384`'s `(empty + partial) / total`. That tree is this corpus's **known-bad**
reference. Sourcing a known-*good* ceiling from a known-*bad* tree is exactly the contamination
ADR-115's calibration rule exists to prevent, in an ADR that cites ADR-115. The corrected figure is
**15.0%** — `confident-mcnulty-ad4e52`, 50 of 334 empty with **zero** partials, every empty being an
optional platform dep npm skipped. Re-derived independently against the live corpus after the fix:
the highest empty-shell ratio among all trees the shipped code verdicts `ok` is 0.150, that same
tree. The floor's margin is therefore **3.3×**, not 2.3×, and
`test_benign_ceiling_is_not_sourced_from_a_known_bad_tree` now pins it so the mistake cannot recur
silently.

*The corpus is alive, so a named tree is a dated observation, not a fixture.* By the time review ran,
`reverent-kowalevski-79b384` had been reinstalled and scans clean; a different worktree
(`priceless-shamir`) had appeared carrying 326 empties and 8 partials. Every tree named in this ADR
is evidence of what was measured on 2026-08-27, not a reproducible reference — the reproducible
artifacts are the fixtures in `tests/test_worktree_npm_install.py`.

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
  0.50 sits at a 3.3× margin over the worst confirmed-benign tree measured (15.0%) — deliberately wide,
  because the arm only ever prints a sentence. Because neither arm describes anything the user can
  *act* on, the advisory is emitted once per worktree rather than once per session; re-printing an
  unactionable sentence at every session start is noise, not signal.

This asymmetry is the substance of the repair-vs-advisory decision dev-env#970 asked for. It is not
caution for its own sake: it is the difference between a signal that has a measured false-positive rate
and one that does not.

**The repair also refuses a tree git does not ignore.** The premise that an automatic `npm ci` is safe
(ADR-016/ADR-037) is that a worktree's `node_modules` is *regenerable*. A repo that deliberately vendors
its dependency tree breaks that premise — and there PARTIAL is not even evidence of damage, since a
vendored package may legitimately ship without its own `package.json`. So the repair runs
`git check-ignore -q node_modules` first and advises instead when the answer is no. This matters
because the PR widened what can trigger an unattended `npm ci` (which executes `preinstall`/`install`/
`postinstall` lifecycle scripts) from "the user made a fresh worktree" to "a directory in `node_modules`
has a particular shape" — see Consequences.

### 3. A live install suppresses the gate

Found during calibration, not reasoned about in advance: re-scanning the corpus a few minutes later
returned **different numbers**, because a concurrent `npm install` was extracting into one of the
worktrees. npm extracts each package to a sibling `.<name>-XXXXXXXX` directory and renames it into place
on completion, so a healthy in-flight install is *full* of directories with exactly the PARTIAL shape —
42 of them in the tree caught mid-flight, plus 8 genuinely half-populated packages.

So the staging directories become the **suppression** signal: any of them means an install is running,
and the audit returns without repairing or advising. `truncation_verdict()` gives `staging` precedence
over every other arm, including PARTIAL — it lives in the pure, tested layer rather than as a
short-circuit in the caller precisely because it is the most consequential branch in the change.

Verified that this costs no detection power: after filtering staging dirs, every known-bad tree still
fires on genuine partials (21 / 127 / 127 / 8 / 4 / 2).

**Two corrections from review, both in the safe direction.**

*Match an allowlist, not npm's staging shape.* The first implementation matched `-[A-Za-z0-9_-]{8}$`,
generalised from three samples on one npm version — so any change to npm's suffix length or alphabet
would silently reclassify every in-flight extraction as PARTIAL, pointing the **destructive** arm at
live installs. `is_staging_name()` now inverts it: a dot-entry is staging unless it is one of npm's
known bookkeeping names (`.bin`, `.cache`, `.package-lock.json`, `.prisma`, `.vite`, `.vite-temp`, …).
An unrecognised dot-entry now defers, which is the failure direction the original docstring already
claimed but the regex did not deliver.

*A staging entry must be a directory.* `is_staging_name()` is a pure name test, and the caller did not
check `is_dir()` — so a stray `.DS_Store` **file** counted as staging, deferring the audit and (because
the defer path deliberately skips its sentinel) re-running the full scan on every prompt forever, while
a genuine partial in the same tree went unrepaired. Reproduced live before the fix; pinned by
`test_scan_ignores_a_stray_dot_file`.

### 3a. A repair must not race an install — including one of its own

`npm ci` **removes** `node_modules` before rebuilding it. So a repair opens a window in which the tree
is *absent* — and `main()`'s absent-tree branch, which has no sentinel and no staging check, would take
that as its cue and start a **second concurrent `npm ci` in the same directory**. Reachable inside one
session: the hook is wired with a 30 s budget, `npm ci` on a large monorepo takes longer, the hook is
killed, npm continues orphaned (Windows does not reap the grandchildren), and the next prompt lands in
the window. Two concurrent installs in one directory is itself one of the root causes §Context lists
for the truncation this ADR exists to repair — so shipping it inside the repair would have been
self-defeating.

`acquire_install_lock()` / `release_install_lock()` make the two paths mutually exclusive, keyed per
worktree in the scratch directory. A hook killed mid-install cannot release its own lock, so a lock
older than 15 minutes is reclaimed rather than honoured forever.

The underlying 30 s-hook-vs-300 s-install mismatch is **pre-existing** (ADR-016 already conceded
installs take 30–120 s) and is not resolved here; what this ADR fixes is the newly-introduced hazard
that a *killed repair* leaves a half-deleted tree a second install can then race. Filed for the
general case rather than widened into this change.

### 4. The audit runs once per session per worktree

The scan costs ~0.4 s on a typical tree and 1.65 s worst-measured, which is too much on every prompt and
nothing at all once. A sentinel keyed on `session_id` + a hash of the worktree path bounds it, following
`disk-space-check.py`'s once-per-session pattern. The sentinel is written **before** acting, so a repair
that fails cannot retry on every prompt for the rest of the session.

Three details that only look like details:

- **A sentinel that cannot be written means "do not act", not "act anyway".** The original code
  swallowed the write failure and continued. The write most plausibly fails on a **full disk** — exactly
  the condition this hook exists for — and continuing there would re-run `npm ci`, and with it
  `_gate_install`'s synchronous reclamation ladder (which deletes *other* worktrees' `node_modules`), on
  every prompt. For a destructive action, failing open has to mean declining to act.
- **The deferral is bounded.** The defer path skips the audit sentinel on purpose, so the re-audit can
  happen once the install lands. Unbounded, a *crashed* install leaves the worktree paying a full scan on
  every prompt forever, silently. A separate worktree-keyed defer marker re-checked by age caps that at
  one scan per 10 minutes.
- **`session_id` absent is date-bounded, not constant and not unique.** A shared `"nosession"` constant
  would collide across sessions and, against the 30-day sentinel sweep, turn "once per session" into
  "once per month" for that worktree — so one failed repair would disable the gate for a month. A
  *unique* fallback has the opposite failure: a full scan every prompt. The local date bounds the blind
  window to a day without reintroducing the per-prompt cost. `session_id` is now also documented in the
  module's stdin-shape block, which previously did not mention the field the sentinel key depends on.

`cleanup_stale_sentinels()` runs **after** the early returns, not before. Above them it globbed the whole
scratch directory on every prompt for the life of every worktree — measured at 20–24 ms against ~8,500
files, a real regression against ADR-016's "already-installed worktrees pay only three `Path.exists()`
checks per prompt".

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
- **What can trigger an unattended `npm ci` is now broader, and that is the real cost of this change.**
  `npm ci` executes `preinstall`/`install`/`postinstall` lifecycle scripts from the repo and from every
  package in the lockfile. Before this ADR a worktree whose `node_modules` was present was inert; now a
  single non-empty package directory lacking its own `package.json` is enough, evaluated automatically
  on the first prompt of a session. That shape can be produced by something other than truncation — an
  interrupted third-party installer, a partial copy, or the antivirus interference §Context lists as a
  live hypothesis. The `git check-ignore` gate narrows this to trees git already treats as disposable,
  and the install lock stops it from stacking; neither makes the trigger surface as narrow as it was.
  Recorded explicitly because the decision above is argued on *detection accuracy* (PARTIAL's 0/38), and
  detection accuracy is a different question from what the detection is permitted to execute.
- Two known gaps, stated rather than papered over: a **crashed** install that leaves staging directories
  behind suppresses the gate in that worktree until they are cleared (fail-safe direction, but silent —
  now costing one scan per 10 minutes rather than one per prompt), and nested workspace `node_modules`
  trees are unscanned.
- The pre-existing 30 s-hook / 300 s-install mismatch is **not** fixed here; a killed repair can still
  leave a partially-rebuilt tree. The install lock means the next prompt will not race it, and the
  audit sentinel means it will not be re-attempted this session — so the failure is bounded, not
  eliminated. Tracked separately.
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
