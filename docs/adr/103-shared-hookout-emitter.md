# ADR-103: Shared `_hookout` Emitter — One Encoding of the Hook Output-Contract Channel Table

**Date:** 2026-07-10
**Status:** Accepted
**Tags:** hooks, output-contract, stdout, stderr, exit-code, systemMessage, additionalContext, claude-facing, cp1252, shared-module, dry, enforcement, migration, adr-027, adr-091, adr-098, adr-099, adr-100

---

## Context

Claude Code delivers a hook's output to the **model** or the **user** through different
physical channels depending on the hook event and the exit code. The contract (per the
[Claude Code hooks reference](https://code.claude.com/docs/en/hooks), the same primary source
[ADR-091](091-journal-stop-check-archive-reminder-blocking.md) and
[ADR-098](098-dev-env-sync-advisories-to-stdout.md) already quote):

- `stderr` on exit 0 is **invisible everywhere** — no event surfaces it.
- Plain `stdout` on exit 0 is **model-visible only on the context events**
  (`UserPromptSubmit`, `UserPromptExpansion`, `SessionStart`); on `PreToolUse` / `PostToolUse`
  / `Stop` it is transcript-only, i.e. invisible to the model.
- `exit 2` feeds **stderr to the model** on any event (blocking the prompt / tool / stop).
- `{"systemMessage": …}` JSON on exit 0 is **user-visible** on any event.

Getting the mapping wrong is silent by construction — an advisory routed to the wrong stream
simply never appears, with no error. That exact failure has been fixed one hook at a time, and
the pace of rediscovery is the point:

- [ADR-091](091-journal-stop-check-archive-reminder-blocking.md) moved `journal-stop-check.py`'s
  archive reminder from exit-0 stdout to exit-2 stderr (a `Stop` hook forwards *neither* stream
  on exit 0).
- [ADR-098](098-dev-env-sync-advisories-to-stdout.md) / **PR #701** moved every `dev-env-sync.py`
  warning from stderr to stdout (a `UserPromptSubmit` hook *does* forward exit-0 stdout) — a
  drift that hid a fast-forward-pull failure for 36+ hours and 21+ commits.
- [ADR-099](099-journal-canonical-guard-advisories-to-stdout.md) / **PR #705** did the
  mirror-image move for `journal-canonical-guard.py` — the identical defect in a sibling file,
  filed only because ADR-098 explicitly scoped it out.
- [ADR-100](100-stop-journal-stub-checkpoint-hook.md) built a *new* `Stop` hook already
  contract-correct by design (exit-2 stderr, `stop_hook_active` guard) — the fifth encoding of
  the same channel facts in as many ADRs.

ADR-098 and ADR-099 landed **during the assessment session that produced this initiative's
plan** — a live demonstration that per-site fixes keep arriving faster than they can be
generalized. Each re-derived the same contract from the primary source, and each left the
next hook to rediscover it. PR1 of this initiative (**dev-env#718**) added authoring
invariant **#5** ("declared fail direction — advisory hooks fail *open*, blocking gates fail
*closed*") to [`docs/REFERENCE.md` → Authoring rules](../REFERENCE.md#authoring-rules), and its
own caveat — *"ASCII-sanitize any crash-reason text (rule 4's cp1252 concern applies to exit-2
stderr too)"* — is one more per-site restatement of a rule that ought to live in code, not prose.

The root cause is architectural: there is no shared emitter, so the channel table is
copy-encoded (correctly or otherwise) in every hook, and `journal-stop-check.py:231` even
**commented the wrong belief** with a test that *pinned* it. This ADR closes that by encoding
the table exactly once. It is the **foundation PR (PR2)** of the hook-reliability initiative
([dev-env#717](https://github.com/brownm09/dev-env/issues/717) top-level,
[dev-env#719](https://github.com/brownm09/dev-env/issues/719) this sub-issue); the enforcement
gates (PR3) and the per-site migrations (PRs 5–7) that consume it are described under
*Enforcement & migration* below.

## Decision

Add `claude/scripts/_hookout.py`, a non-invoked sibling library module (the same shape as
[`_hookio.py`](050-shared-hookio-sibling-hook-fixes.md) for PostToolUse Bash hooks and
[`_hookutil.py`](064-shared-hookutil-sentinel-transcript-locate.md) for the Stop /
UserPromptSubmit family), depending only on the standard library. It encodes the channel table
**once** and exposes:

```python
emit_advisory(event, text, *, audience="model"|"user"|"both", blocking=False)  # -> exits
emit_block(text)                                                                 # -> exits 2
ascii_sanitize(text) -> str
plan_emission(event, text, *, audience, blocking) -> Emission                    # pure core
STDOUT_MODEL_VISIBLE_EVENTS   # frozenset: UserPromptSubmit / SessionStart / UserPromptExpansion
```

### The channel table, encoded once

`plan_emission` is the pure routing core; the deliverers (`emit_advisory` / `emit_block`) call
it and then perform the write + `sys.exit`. Given `(event, audience, blocking)` it returns an
`Emission(stdout, stderr, exit_code)`:

- **`blocking=True`** → exit-2 stderr, event-independent (the one model-visible channel on every
  event). This is `emit_block`'s path.
- **non-blocking, `audience="model"`, context event** → `{"hookSpecificOutput":
  {"hookEventName": event, "additionalContext": text}}` on stdout, exit 0.
- **non-blocking, `audience="user"`, any event** → `{"systemMessage": text}` on stdout, exit 0.
- **non-blocking, `audience="both"`, context event** → one JSON object carrying **both** keys,
  exit 0.

### Undeliverable requests raise, rather than emit into the void

The contract has a hard corollary: **there is no non-blocking, model-visible channel on
`PreToolUse` / `PostToolUse` / `Stop`.** `plan_emission` refuses such a request with a
`ValueError` that names the two honest options (`blocking=True` for exit-2 stderr, or
`audience="user"` for a systemMessage toast), rather than silently emitting nothing. It also
raises for `audience="user"` + `blocking=True` (a block reaches the model via stderr, not the
user — systemMessage needs exit 0) and for `audience="both"` anywhere its model half isn't
deliverable. Every real call site passes `(event, audience, blocking)` as **literals**, so a
`ValueError` surfaces in the hook's own test, never at runtime — it is a compile-time-shaped
guard against the exact "wrote an advisory that can never be seen" bug this initiative exists to
kill, and a hook's own fail-open `except Exception: sys.exit(0)` catches any stray propagation
anyway.

### Wire safety: `ensure_ascii` for JSON, `ascii_sanitize` for raw streams

Claude Code decodes hook output through the Windows **cp1252** codepage; a non-cp1252 byte on a
raw stream raises `UnicodeEncodeError` at print time and the whole advisory is lost (the
vanishing-output class `posttooluse-inert-advisory.py` / `idle-refresher.py` / ADR-091 / ADR-098
each guard against per-hook today). `_hookout` splits the concern by channel:

- **JSON channels** use `json.dumps(payload, ensure_ascii=True)` — non-ASCII is escaped to
  `\uXXXX`, so the emitted stdout bytes are pure ASCII on the wire while the parser restores the
  original content. JSON-channel text is therefore **not** garbled by sanitization.
- **Raw streams** (exit-2 stderr) run through `ascii_sanitize`, which maps common Unicode
  punctuation/operators to readable ASCII (dashes → `-`, curly quotes → straight, `…` → `...`,
  arrows, `≤`/`≥`/`≠`, bullet, middle dot, no-break space) and replaces anything still non-ASCII
  (emoji, rare symbols) with `?`. Guaranteeing `.isascii()` guarantees cp1252-encodability (ASCII
  ⊂ cp1252). `ascii_sanitize` is deliberately **domain-agnostic**: a caller wanting a *semantic*
  ASCII rendering of a status glyph (e.g. `🔴` → `OVER`, the usage-snapshot case in PR5) does that
  mapping itself; the helper only guarantees the bytes are safe without mangling ordinary
  typography.

### Delivery owns the exit code

`emit_advisory` / `emit_block` perform the write **and** `sys.exit` with the matching code. This
is deliberate: the channel *is* coupled to the exit code (a systemMessage requires exit 0; a
block requires exit 2), so "emit, then exit with the matching code" is the only correct
sequence, and folding the exit into the emitter removes the "wrote the block reason but forgot
to exit 2" bug class. `sys.exit` raises `SystemExit` (a `BaseException`, not `Exception`), so a
hook's `try: main() except Exception: sys.exit(0)` safe-exit guard (authoring rule 2) does not
swallow a deliberate exit-2 block. Hooks needing custom control — emitting several times, or
exiting under their own logic — call the pure `plan_emission` and manage delivery themselves.

## Enforcement & migration

This ADR is the anchor for the initiative's foundation; the delivery contract above is enforced
and adopted across the following PRs (a foundation-then-gates-then-migrations sequence):

- **PR3 (`feat/hook-output-contract-gates`)** adds the mechanical enforcement: an AST
  output-contract test (flagging `stderr`-reaching-exit-0, bare `print` reaching exit 0 on
  PreToolUse/PostToolUse/Stop, and stdout on an exit-2 path), an ASCII-literal lint
  (`json.dumps`-exempt), a safe-exit structural test, and a settings-wiring lint. Each gate lands
  green with every current offender in a **two-sided allowlist** (the `_KNOWN_EXCEPTIONS`
  mechanism from `test_no_crude_command_substring_checks.py`: a stale entry fails the suite too).
  `_hookout` adoption is what shrinks those allowlists.
- **PR4 (`config/ci-hook-test-suite`)** makes the gates *run*. `claude/scripts/run-hook-tests.py`
  discovers and runs the whole suite — every `claude/scripts/tests/test_*.py` plus the bash gates in
  `claude/scripts/tests/` and `claude/hooks/tests/` — and `.github/workflows/hook-tests.yml` executes
  it on `windows-latest` for every `pull_request`. This turns PR3's three contract/wiring gates (and
  the ~60 tests around them) into an external gate on every PR, rather than one that depends on an
  author remembering the local `## Testing` commands — the same "no signal that the safety system
  itself broke" gap this initiative targets, one level up. The runner glob-discovers, so a new test
  file is gated automatically; it runner-skips only `test_pyw_stdio.py` (a real-`pyw`
  Windows-subsystem stdio probe a headless runner can't host faithfully, documented in-place) and
  treats a bash gate's own `SKIP:` self-exit (no shellcheck, no authenticated gh) as non-failing.
  `windows-latest` is faithful to the real `py -3` / Git Bash / cp1252 runtime
  ([ADR-007](007-hook-command-invocation.md)); `pull_request`-only (never on `main` pushes) follows
  the lockfile-gate scoping lesson (dev-env `CLAUDE.md` → Dependency and lockfile policy). The
  runner's own pure helpers are unit-tested (`tests/test_run_hook_tests.py`); its end-to-end
  acceptance test is the first green CI run.
- **PRs 5–7 (migrations)** move the live per-site offenders onto `_hookout`, each deleting its
  allowlist entries: PR5 the PostToolUse advisory channels (`post-pr-merge-pull` park →
  `emit_block`, status → `emit_advisory(..., audience="user")`; `post-pr-merge-reclaim`;
  `usage-snapshot` ASCII), PR6 the Stop-hook channels (`token-tracker`, `journal-stop-check`
  checks 2–3, `posttooluse-inert-advisory` → `emit_block` + `stop_hook_active` guard), PR7 the
  safe-exit sweep plus the opportunistic `dev-env-sync` / `journal-canonical-guard`
  (UserPromptSubmit) migrations. The `#701`/`#705` pace is exactly why the allowlist is
  two-sided: a concurrently-landed per-site fix just turns an entry stale, and removing it is a
  one-line follow-up the suite itself demands.

**Verification note for the migrations.** Three contract cells have no prior in-repo exercise —
`systemMessage` on `PostToolUse`/`Stop` (the `audience="user"` path PR5/PR6 first route there),
`additionalContext` on `UserPromptExpansion`, and the combined both-keys object. Each is asserted
by the documented common-field contract (`systemMessage` is an event-agnostic top-level field per
the [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)), but the migration that
first routes a user advisory to a `PostToolUse`/`Stop` event should do a one-time live confirmation
that the toast renders — a wrong channel belief on an as-yet-unexercised cell is exactly the
silent-invisibility class this module exists to close.

**Migration hazard (author-facing, encoded in the emitter's docstrings).** The
raises-rather-than-vanishes property holds only for a *static* `(event, audience, blocking)` triple.
An `audience="model"` call with a **dynamic** event (e.g. `emit_advisory(data["hook_event_name"],
…, audience="model")`) that resolved to a non-context event would raise `ValueError` on every fire,
which the calling hook's fail-open guard swallows — reintroducing the silent-vanishing and passing
*green* in an end-to-end `main()` test. `emit_advisory`'s docstring carries an explicit note to pass
the event as a literal on that path; `audience="user"` and `emit_block` are event-independent and
immune.

PR2 itself is **purely additive** — a new module + its tests + docs. It changes no existing
hook's behavior; nothing imports `_hookout` yet. That keeps the foundation independently
reviewable and lets the migrations land incrementally against a stable contract.

## Consequences

- The output-contract channel table is defined in exactly one place. A future hook author calls
  `emit_advisory` / `emit_block` and cannot route an advisory to a silently-invisible channel —
  the impossible combinations raise instead.
- The cp1252 wire-safety rule (authoring rule 4's stderr concern, ADR-091/098's ASCII-only
  convention) is enforced in code (`ensure_ascii` + `ascii_sanitize`) rather than restated as
  prose in each hook.
- `ValueError` on an undeliverable `(event, audience, blocking)` is a development-time signal.
  Because every call site passes literals, it never fires in production; if a future dynamic
  caller ever passed a runtime-derived triple, the hook's own fail-open guard would catch it (an
  advisory vanishing — the pre-existing failure mode, not a new regression).
- One more non-invoked library module in `claude/scripts/` (no table row, matching
  `_hookio`/`_hookutil`/`_journal_shards`/`_repo_scan`), documented in the Hooks prose of README
  and REFERENCE.
- No behavior change until PRs 5–7 adopt it; the enforcement gates (PR3) are what make adoption
  mechanically pressured rather than aspirational.

## Alternatives considered

- **A prose "channel table" in REFERENCE.md instead of a shared emitter.** Rejected — the table
  already *exists* as prose (in three ADRs, the CLAUDE.md `## Observability` section, and
  authoring invariant #5) and hooks still got it wrong, because prose is exactly as missable as
  the bug it documents (the ADR-050 Amendment 11 lesson: a written "keep these in sync" note is
  as missable as the drift it guards against). Code that *refuses* the wrong channel is the only
  durable form.
- **Silently degrade an undeliverable model-visible advisory to `systemMessage`** (deliver
  *something*) instead of raising. Rejected for `audience="model"` — the author asked for the
  model specifically; silently delivering to the user only reintroduces a soft version of the
  invisibility bug (the model still doesn't see it, and nothing says so). `audience="both"` on a
  non-context event raises for the same reason: its model half is genuinely undeliverable
  non-blocking, and the author should choose `blocking=True` or accept `audience="user"`
  explicitly.
- **Auto-escalate a non-blocking model advisory to exit 2 on non-context events.** Rejected —
  turning an advisory into a tool/stop *block* without the author asking is a surprising and
  potentially disruptive side effect (it halts the tool call or stop). Blocking is a deliberate
  choice the caller must make (`blocking=True`), which is also where the `stop_hook_active`
  loop-guard responsibility belongs.
- **Have `emit_advisory` return the exit code for the caller to `sys.exit`, rather than exiting
  itself.** Rejected as the default — it reopens the "wrote the reason, forgot to exit 2" gap.
  The pure `plan_emission` is provided for the rare hook that genuinely needs to manage its own
  delivery/exit.
- **Bake status-glyph semantics (`🔴`→`OVER`, `✅`→`OK`) into `ascii_sanitize`.** Rejected — those
  are caller-specific meanings, not a universal typographic transform; folding them in would make
  the sanitizer's output surprising and couple it to one hook's vocabulary. The caller passes
  already-ASCII semantic text; `ascii_sanitize` is the defensive backstop, not a translator.

## References

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the exit-code /
  stdout-vs-stderr / per-event-type semantics this module encodes.
- [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md) — the base
  stderr-for-blocking / stdout-`additionalContext`-for-context-injection contract.
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) — `_hookio.py`, the sibling-module precedent
  for the PostToolUse Bash family.
- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — `_hookutil.py`, the sibling
  module for the Stop / UserPromptSubmit family.
- [ADR-091](091-journal-stop-check-archive-reminder-blocking.md),
  [ADR-098](098-dev-env-sync-advisories-to-stdout.md),
  [ADR-099](099-journal-canonical-guard-advisories-to-stdout.md),
  [ADR-100](100-stop-journal-stub-checkpoint-hook.md) — the per-site channel fixes this module
  generalizes (PRs #701 and #705 landed ADR-098 / ADR-099 during this initiative's own planning).
- [`docs/REFERENCE.md` → Authoring rules](../REFERENCE.md#authoring-rules) — invariant #5
  (declared fail direction, added in PR1 / dev-env#718), whose cp1252 caveat this module encodes.
- [dev-env#717](https://github.com/brownm09/dev-env/issues/717) — the top-level
  hook-reliability initiative issue.
- [dev-env#719](https://github.com/brownm09/dev-env/issues/719) — this sub-issue (PR2).
- [dev-env#721](https://github.com/brownm09/dev-env/issues/721) — the CI-runner sub-issue (PR4),
  recorded in *Enforcement & migration* above.
