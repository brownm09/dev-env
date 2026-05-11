# ADR 020 — Documentation Coverage Check as LLM-Judged Step in /review

- **Status:** Accepted
- **Date:** 2026-05-11
- **Related:** ADR 019 (Documentation Reconciliation Enforcement), [issue #219](https://github.com/brownm09/dev-env/issues/219)

## Context

`brownm09/career-playbook#168` added `context/sentence_density.md` — a load-bearing file
referenced by the modular cover-letter workflow — without updating `context/README.md`
(the directory index). The miss surfaced two weeks later via `brownm09/career-playbook#170`.
A briefing regeneration in that window would have silently dropped sentence-density guidance.

Issue #219 originally proposed a mechanical validator: "fail when a file under
`career-playbook/context/` is added, renamed, or removed without a corresponding change
to `context/README.md` in the same diff." During planning, the scope expanded:

- The same risk applies to **every** directory in **every** repo that uses a README as an
  index, not only `career-playbook/context/`.
- "Warrants a README update" is **not** a path-level question. It depends on the semantic
  significance of the change: a minor bug fix in an existing file does not warrant an index
  update; a new file or a purpose change does. Cross-tree impacts (a skill change rippling
  into a top-level README) cannot be detected by directory adjacency alone.
- A mechanical script that flags every structural change as a missing README update would
  produce false positives in directories where the README intentionally describes only a
  subset of files, or where the index is implicit (folder name self-documents content).

ADR 019 already established the three-layer enforcement model for documentation
reconciliation: the `/review` skill (Opus judgment), an advisory hook at PR-create time, and
a CLAUDE.md instruction. Step 2b of `/review` mechanically checks the dev-env-specific
Documentation Maintenance table. The career-playbook gap is one rung more general than that
table covers.

## Decision

Extend the `/review` skill with **Step 2c — Documentation Coverage Check**, a semantic
LLM-judged step that runs after Step 2b and before the main correctness/security/reliability
analysis. Step 2c:

1. Identifies files in the diff that were added, deleted, renamed, or significantly rewritten.
2. Walks ancestor directories up to depth 3 from each changed file.
3. For each ancestor level that has a `README.md`, uses LLM judgment to decide whether the
   change warrants a README update.
4. Records a **blocking documentation finding** when a relevant README exists but is not
   in the diff. Records a **non-blocking suggestion** when an ancestor directory has no
   README and one would add navigational value.

The check is repo-agnostic — no hardcoded paths, no per-repo configuration. It uses the
same reasoning Mike applies in manual review: "does this change make the README stale?"

No mechanical script (Python or bash) is added.

## Rationale

**Why semantic instead of mechanical:**

The mechanical version would have to choose between two failure modes:

- **Strict (every structural change in a directory with a README requires that README in the
  diff):** produces false positives for directories where the README is not an exhaustive
  index. Reviewers learn to ignore the warning, eroding the value of all blocking findings.
- **Permissive (only flag a narrow set of known directories like `context/`):** requires
  per-repo configuration, does not generalize to new directories, and offers no help when
  a new directory is created.

The LLM-judged version sidesteps both by reading the change and the README's purpose, then
reasoning about staleness. The trade-off — needing Claude to run the check — is acceptable
because `/review` already runs Claude on every PR.

**Why a separate Step 2c rather than expanding Step 2b:**

Step 2b is a path-matching mechanical check tied to a specific Documentation Maintenance
table convention. It is fast and produces a deterministic finding. Folding semantic
reasoning into Step 2b would blur the two modes — readers of `SKILL.md` could no longer
tell at a glance which findings require LLM reasoning and which are mechanical.

**Why no pre-push hook:**

A pre-push hook cannot make semantic judgments. The mechanical version of the check would
need to be permissive enough to avoid blocking legitimate pushes, which means it would not
catch the case that prompted this ADR (a real file addition that needed a README update).
The `/review` skill — run as part of the PR-open → review → merge sequence specified in
ADR 019 — is the right enforcement point.

**Why both blocking and non-blocking findings:**

A missing update to an existing README is a regression — the index is wrong after this
change. A missing README in a directory that does not yet have one is a suggestion — the
maintainer may have a reason not to add one. The two cases warrant different severities.

## Consequences

- The `/review` skill now produces documentation coverage findings on every PR with
  structural file changes. Reviewers see them as part of the standard blocking/non-blocking
  output; no new section or flag is needed.
- Repos that previously had silent documentation drift (career-playbook `context/`,
  potentially others) will see findings on the next PR that exercises the pattern.
- The check uses LLM tokens proportional to the changed-file count. For PRs touching only
  one or two files, the cost is negligible (≤200 tokens of reasoning). For larger PRs the
  check is bounded by the changed-file count, not the diff size.
- Future repos do not need to opt in or add configuration. The check applies uniformly.
- The career-playbook-specific validator originally proposed in issue #219 is **not built**;
  this ADR documents the decision to solve the problem at a more general layer.

## Alternatives considered

- **Mechanical Python script in `claude/scripts/` invoked from `validate.sh` and a pre-push
  hook.** Rejected because it cannot distinguish significant changes from minor edits, and
  cannot detect cross-tree impacts. Would have produced either false positives (strict) or
  false negatives (permissive).
- **Per-repo configuration listing which directories require README updates.** Rejected
  because it requires maintenance per repo and per new directory, and still cannot handle
  cross-tree impacts.
- **Expanding Step 2b to cover the general case.** Rejected because it would conflate
  mechanical and semantic checks under one heading.

## References

- [Issue #219](https://github.com/brownm09/dev-env/issues/219) — original mechanical-validator proposal
- [ADR 019](019-doc-reconciliation-enforcement.md) — three-layer documentation enforcement
- [ADR 011](011-adr-warrant-check.md) — ADR-warrant check rationale
- [career-playbook#170](https://github.com/brownm09/career-playbook/pull/170) — incident PR that surfaced the gap
