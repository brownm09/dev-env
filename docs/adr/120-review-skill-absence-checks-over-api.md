# ADR-120: The `/review` Skill Reads Remote Files Over the API, and Classifies Absence by Exit Status

**Date:** 2026-07-22
**Status:** Accepted
**Tags:** skills, review, absence-claims, false-absent, msys, windows, git, gh-api, exit-status, doc-reconciliation, correction, adr-004, adr-011, adr-117

---

## Context

[ADR-117](117-absence-claims-need-absolute-paths.md) folded four false-absent mechanisms into one
CLI Scripting Checklist rule in the global `claude/CLAUDE.md`, one of which is **suppressed
failure**: never pair an absence check with `2>/dev/null`, because MSYS path-conversion can mangle
`git show <ref>:<path>` and the redirect then hides the `fatal:`, leaving empty output that reads
exactly like "not present." ADR-117 recorded that
[dev-env#602](https://github.com/brownm09/dev-env/issues/602) was only **partially** addressed —
the rule reached the checklist, but not the one place in this repo that actually executes the
pattern.

That place is the `/review` skill, which carried three live instances, all deciding an absence:

| Site | Command | The absence it decided |
|---|---|---|
| `SKILL.md:80-81` (Step 2b) | `git show origin/<headRefName>:.claude/CLAUDE.md 2>/dev/null \|\| git show origin/<headRefName>:CLAUDE.md 2>/dev/null` | "does this repo have a Documentation Maintenance table?" |
| `SKILL.md:120` (Step 2c) | `git show origin/<headRefName>:<dir>/README.md 2>/dev/null` | "does a README exist at this ancestor directory?" |

All three branched on **stdout emptiness alone**. The `||` chain is the worst of them: a mangle on
the first branch falls through to a second that gives no signal it ever ran, so the skill concludes
"no Documentation Maintenance table" and **silently skips the entire doc-reconciliation gate**. The
failure mode is a review that reports *clean*, not one that errors — the same shape ADR-117 was
written about, reached through the same mechanism.

### The mangle is deterministic, and the trigger is narrower than #602 assumed

#602 described the mangling as *intermittent* and hypothesised the trigger was "multiple
`/`-separated segments," explicitly flagging that the exact rule was never confirmed. Both halves
are wrong. Measured against `origin/main` in this repo while writing this ADR:

| Argument | Result |
|---|---|
| `origin/main:docs/adr/INDEX.md` | works |
| `origin/main:claude/skills/review/SKILL.md` | works — four segments deep |
| `origin/main:CLAUDE.md` | works |
| `origin/main:.github/workflows/hook-tests.yml` | **mangles** |
| `origin/main:.gitignore` | **mangles** — a single segment |

The trigger is a **leading-dot path segment immediately after the `:`**; path depth is irrelevant.
It reproduces on **every** invocation, quoted or unquoted. This is consistent with MSYS2 treating a
`:`-joined argument as a POSIX `PATH`-style list and converting it when a segment looks
path-shaped, though the precise heuristic is not documented — the observed rule above is what this
ADR relies on, not an inferred mechanism.

"Deterministic" changes the severity materially. These were not sites that *might* fail:

- **Step 2b could never detect a `.claude/CLAUDE.md` on Windows.** That is a first-class supported
  layout — `/journal-onboard` offers to create exactly that file. For a repo whose CLAUDE.md lives
  there with no root copy, branch 1 mangled every time, branch 2 legitimately 404'd, and the whole
  gate was skipped on every review, forever.
- **Step 2c could never see a README under a dot-prefixed ancestor** (`.github/`, `.claude/`).
  #602's original report is literally this invocation failing on `.github/workflows/README.md`.

### A second, independent false-absent at the same sites

Steps 2b and 2c never ran `git fetch`. The skill's only fetch instruction lives in the `## Notes`
follow-up bullet, which these steps do not invoke. So `git show origin/<headRefName>:…` also
reported "absent" whenever the PR's head ref simply had not been fetched into the current
checkout — and `/review` takes a **PR URL**, so it is routinely run against a repo that is not the
cwd and may not be cloned at all. `git show` exits **128 for both** an absent path and an invalid
ref, so even reading the exit code could not have separated these two; only the stderr text
distinguishes them (`does not exist in` vs. `invalid object name`).

## Decision

**1. Read remote blobs over the GitHub API, not `git show`.** Steps 2b and 2c now use:

```bash
gh api "repos/<OWNER>/<REPO>/contents/<path>?ref=<headRefName>" \
  -H "Accept: application/vnd.github.raw"
```

`OWNER`/`REPO` are extracted from `PR_URL` in Step 1. This form never hands a `<ref>:<path>`
argument to the shell, so it cannot mangle; it needs no prior `git fetch`; and it works when the
reviewed repo is not the cwd. Verified: the leading-dot path that mangles under `git show` reaches
GitHub intact and returns a genuine HTTP 404.

**2. Classify by exit status, never by stdout emptiness.** Both steps now use one table: exit 0 →
present; non-zero with `(HTTP 404)` on stderr → genuinely absent; **any other non-zero → a tool
error that stops the review and is reported to the user**, never recorded as absence. This matters
concretely because on a 404 `gh` writes the error JSON to *stdout*, so stdout is non-empty even in
the absence case — emptiness was never a valid signal in either direction.

**3. No `||` chaining of the two Step 2b probes.** They run as separate commands with separate
classification, because `||` is exactly what collapsed "first path absent" into "first probe
failed."

**4. Document the hazard where it will recur.** A **Remote reads on Windows** note in `## Notes`
records the deterministic leading-dot trigger, the `2>/dev/null` prohibition, the API-form
preference, and the 128-for-both-cases caveat. It sits directly beneath the follow-up /
merge-readiness bullet — the skill's ADR-004 "read from remote" step, which #602 identified as the
standing recurrence site. That bullet's own `git show` now carries `MSYS_NO_PATHCONV=1`, since a
local read against an already-fetched branch is a case where `git show` remains the right tool.

## Consequences

- The doc-reconciliation gate (Step 2b) and README-staleness gate (Step 2c) actually run on
  Windows for dot-prefixed paths, for the first time. Reviews of repos using `.claude/CLAUDE.md`
  were silently ungated before this.
- **ADR-004 is refined, not overturned.** Its principle — read PR state from the remote, never the
  local worktree — is strengthened, since `?ref=` is authoritative without depending on local fetch
  state. Only its prescribed *mechanism* (`git show origin/<branch>:<path>`) is narrowed to the
  case where the ref is known-fetched locally, and there it now carries `MSYS_NO_PATHCONV=1`.
  ADR-004 gains a pointer here so it stops prescribing the mangling form unqualified.
- Steps 2b/2c now consume GitHub **REST** quota instead of running locally: at most two calls for
  Step 2b, and one per *deduplicated* ancestor directory for Step 2c — single digits per review,
  against a 5,000/hr bucket. The skill is already network-bound (`gh pr view`, `gh pr diff`), so
  this adds no new prerequisite. It also puts these reads on the REST bucket while `gh pr *` uses
  GraphQL, which is a mild resilience gain: the two buckets exhaust independently, and a review can
  now complete its doc gates on a session where GraphQL is spent.
- A network or auth failure now **stops the review** where it previously produced a silently
  incomplete one. That is the intended trade: a loud stop beats a clean-looking report.
- No hook, script, or settings changes — `claude/skills/review/SKILL.md` plus docs. The hook test
  suite is unaffected but is run as the standing gate.

## Alternatives considered

- **Keep `git show`, just add `MSYS_NO_PATHCONV=1` and drop `2>/dev/null`.** The minimal fix the
  issue asked for, and rejected as insufficient. It fixes the mangle but leaves the second
  false-absent untouched: an unfetched ref still reports as absence, and `git show`'s 128-for-both
  exit code means the classification step would have to parse stderr strings to tell the two apart.
  The API form removes both failure modes and yields a cleaner discriminator.
- **`git fetch origin <headRefName>` first, then `MSYS_NO_PATHCONV=1 git show`.** Rejected — it
  presumes the cwd is a clone of the reviewed repo, which `/review <PR-URL>` does not guarantee,
  and adds a network round-trip anyway. If a fetch is happening regardless, the API call is
  strictly simpler.
- **`git cat-file blob <ref>:<path>`.** Rejected — same `<ref>:<path>` argument shape, so same
  mangle.
- **`--` separator (`git show <ref> -- <path>`).** Rejected — that form shows the *commit* filtered
  by path, not the blob contents, so it does not answer the question these steps ask.
- **A hook that greps skill files for `git show <ref>:<path>` paired with `2>/dev/null`.**
  Rejected for now, on ADR-117's reasoning: the detectable event is the *conclusion drawn from
  empty output*, not the command. A grep would also fire on the legitimate `MSYS_NO_PATHCONV=1`
  form this ADR introduces. Worth revisiting only if a third site appears.

## References

- [dev-env#602](https://github.com/brownm09/dev-env/issues/602) — the MSYS `git show <ref>:<path>`
  mangling report; this ADR closes its `/review`-skill half.
- [dev-env#877](https://github.com/brownm09/dev-env/issues/877) — the three concrete instances and
  the deterministic-trigger measurements.
- [ADR-117](117-absence-claims-need-absolute-paths.md) — the global rule this applies; its
  Consequences section records the #602 partial-coverage split that this ADR resolves.
- [ADR-004](004-pr-review-reads-from-remote.md) — read PR state from the remote; principle upheld,
  mechanism refined here.
- [ADR-011](011-adr-warrant-check.md) — the warrant criterion satisfied by this change (it touches
  a skill documented under `claude/`).
- [MSYS2 — Filesystem paths](https://www.msys2.org/docs/filesystem-paths/) — primary documentation
  of the automatic POSIX↔Windows argument conversion behind the mangling, including the
  `MSYS2_ARG_CONV_EXCL` / path-conversion escape hatches.
- [GitHub REST API — Get repository content](https://docs.github.com/en/rest/repos/contents#get-repository-content)
  — the `?ref=` parameter and the `application/vnd.github.raw` media type used by the new probes.
