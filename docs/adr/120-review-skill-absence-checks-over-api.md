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
gh api "repos/<HEAD_OWNER>/<HEAD_REPO>/contents/<path>?ref=<headRefName>" \
  -H "Accept: application/vnd.github.raw"
```

`HEAD_OWNER`/`HEAD_REPO` come from `gh pr view --json headRepositoryOwner,headRepository` in
Step 2 — the repo the head ref actually lives in. Deliberately **not** the `OWNER`/`REPO` parsed
out of `PR_URL`: that is the *base* repo, and on a fork PR the head ref does not exist there, so
every probe would 404. This form never hands a `<ref>:<path>` argument to the shell, so it cannot
mangle; it needs no prior `git fetch`; and it works when the reviewed repo is not the cwd.
Verified: the leading-dot path that mangles under `git show` reaches GitHub intact and returns a
genuine HTTP 404.

**2. Classify by exit status *and stderr text*, never by stdout emptiness.** On a 404 `gh` writes
the error JSON to *stdout*, so stdout is non-empty even in the absence case — emptiness was never
a valid signal in either direction.

Critically, **HTTP 404 is two conditions, not one**, and treating it as one would recreate this
ADR's own bug one layer up:

| stderr | Meaning | Action |
|---|---|---|
| `Not Found (HTTP 404)` | file genuinely absent | record absence |
| `No commit found for the ref … (HTTP 404)` | ref does not resolve in this repo — wrong repo for a fork head, deleted/renamed branch | **stop and report**; not an absence |
| anything else non-zero | auth, network, rate limit | **stop and report** |

Matching on `(HTTP 404)` alone collapses "this file is not in the repo" with "I asked the wrong
repo," and the second silently skips the gate — exactly the shape of the defect being fixed. This
was caught by the `/review` pass on the PR implementing this ADR, before merge.

**3. No `||` chaining of the two Step 2b probes.** They run as separate commands with separate
classification, because `||` is exactly what collapsed "first path absent" into "first probe
failed."

**4. Guard the pattern statically.** `claude/scripts/tests/check-remote-read-hygiene.sh` fails the
suite when any non-comment line under `claude/**` pairs a `git show <ref>:<path>` with
`2>/dev/null`. Skill behavior itself is prompt markdown and untestable, but *this specific defect*
is greppable, and nothing otherwise stops it being reintroduced here or in another skill. It
follows `check-script-path-hygiene.sh` exactly — same directory, same comment-stripping so a file
may *document* the hazard (as this one now does at length) without tripping the lint, same
exit-1-on-offender contract — and is picked up automatically by `run-hook-tests.py`'s glob
discovery.

The two dedicated test directories are excluded, on the precedent ADR-116 set for its own
anti-regression pass: a gate asserting a pattern is absent necessarily contains that pattern, and
this script's diagnostic `echo` lines are executable rather than comments. That exclusion was not
foreseen — it was forced by CI, and the way it surfaced is worth recording, because it is *this
ADR's own subject matter*. The gate passed local verification and then failed on its first CI run,
because `git ls-files` lists **tracked** files only and the script was still untracked when tested:
a clean local result that was really "the file was invisible to the scan," not "the file is clean."
That is ADR-117 item 5's *Visibility blind spots* bullet exactly. The lesson generalises past this
gate — **a self-referential lint must be verified with itself staged** — and is recorded in the
script header and Testing item 79 as well as here.

**5. Document the hazard where it will recur.** A **Remote reads on Windows** note in `## Notes`
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
- No hook or settings changes. The diff is `claude/skills/review/SKILL.md`, docs, and one new
  static lint (`claude/scripts/tests/check-remote-read-hygiene.sh`, Testing item 79) — so this is
  **not** a docs-only change and the item-4 docs-only guard does not apply.
- The lint costs ~20s per suite run (one grep per tracked file under `claude/`), which is the
  price of it scanning Markdown skills as well as executable scripts. Acceptable against a ~190s
  suite; if it becomes a bottleneck, a single `git grep` pass would replace the per-file loop
  without changing the rule.

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
- **A `PreToolUse` hook that warns when a `git show <ref>:<path>` command is executed.** Rejected,
  on ADR-117's reasoning: the detectable event is the *conclusion drawn from empty output*, not
  the command, and such a hook would fire on every legitimate remote read — habituation would
  neutralise it long before it caught a real case.

  A **static lint** over `claude/**` is a different mechanism and **was adopted** (decision 4
  above). The objection that a grep "would fire on the legitimate `MSYS_NO_PATHCONV=1` form" does
  not apply to it: the rule keys on `git show` **co-occurring with `2>/dev/null`**, and the
  `MSYS_NO_PATHCONV=1` form has no `2>/dev/null` — that is the entire point of it. This
  distinction was missed on the first pass of this ADR and corrected during review.

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
