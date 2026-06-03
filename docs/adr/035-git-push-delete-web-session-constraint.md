# ADR-035 — `git push --delete` Fails in Claude Code Web Sessions

**Date:** 2026-06-03
**Status:** Accepted
**Closes:** [dev-env#303](https://github.com/brownm09/dev-env/issues/303)
**Tags:** git, web-session, sandbox, proxy, shallow-clone, branch-deletion, workflow, global-rule
**Related:** [ADR-031](031-auto-merge-disabled.md)

---

## Context

Claude Code **web/cloud sessions** run inside a network-isolated sandbox. Git traffic is relayed
through the sandbox's **HTTP git proxy** rather than reaching the remote directly, and
repositories are **cloned shallow** (`--depth 1`).

The proxy was built and validated for the *fetch* path — clone and pull, which is the dominant
operation. Branch deletion exercises the *send-pack* (push) path, which has two properties the
proxy does not handle:

1. A delete-only push sets the new OID to the zero OID; there are no objects to transfer, so git
   POSTs an effectively empty packfile to `git-receive-pack`. The server's `unpack ok` /
   per-ref status is streamed back over the **sideband-64k** channel (bands 1/2/3).
2. The proxy closes the `git-receive-pack` POST connection before relaying that sideband status
   back to the client. Git surfaces this as a **sideband disconnect** —
   `the remote end hung up unexpectedly` — and the ref deletion never reaches GitHub.

The shallow clone compounds the problem: the delete-only send-pack sideband stream is a code path
the proxy was never exercised against. The symptom is reproducible for `git push origin --delete
<branch>` in web sessions and absent in local sessions, where git talks to the remote directly.

This matters operationally because the dev-env workflow deletes remote branches as part of routine
cleanup, and a session that reaches for `git push --delete` in the cloud fails opaquely and burns
tokens on recovery. Notably, `gh pr merge --squash --delete-branch` ([ADR-031](031-auto-merge-disabled.md))
is **unaffected** — gh deletes the branch through the GitHub REST API, not the git smart protocol —
which is why the failure is easy to miss until a direct `git push --delete` is attempted.

---

## Decision

Standardize on **API-based ref deletion** and document the constraint so sessions never reach for
`git push --delete` in a web session.

1. Add a Git Workflow bullet to `claude/CLAUDE.md`: never use `git push origin --delete <branch>`
   in a web/cloud session; delete the ref via the GitHub REST API instead —
   `gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"` — which works in both local
   and web sessions because it travels over authenticated HTTPS and bypasses send-pack entirely.
2. Add a **Platform Constraints** section to `docs/REFERENCE.md` recording the symptom, root cause,
   workaround, and proposed upstream fix.

The `gh api` form is recommended **everywhere**, not only in web sessions, so there is a single
deletion idiom that is correct regardless of environment.

---

## Consequences

- Branch deletion is environment-agnostic: the same `gh api` command works locally and in the
  cloud, removing a class of opaque web-session failures.
- `gh pr merge --squash --delete-branch` remains the primary path for the merge case and needs no
  change — it already uses the API.
- The workaround does not fix the underlying proxy bug. The proper upstream fix lives in the
  Claude Code sandbox: proxy the send-pack sideband for delete-only ref updates from shallow
  clones (relay the full receive-pack response before closing the POST), or fall back to a full
  clone when a push is detected. Until then, the documented workaround stands.

---

## Alternatives Considered

- **Rely solely on GitHub's "automatically delete head branches" setting / the weekly
  `prune-stale-worktrees` routine.** Valid as a deferral, but it leaves a window where stale
  remote branches accumulate and does not cover ad-hoc deletions. Kept as a secondary option, not
  the primary rule.
- **Force a full (non-shallow) clone in web sessions.** Not controllable from the repo side — the
  sandbox owns clone depth — so this is an upstream fix, not a workflow rule.
