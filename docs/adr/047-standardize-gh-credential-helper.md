# ADR-047 — Standardize git's GitHub Credential Helper on `gh` to Prevent Credential-Manager GUI Hangs in Agent Sessions

**Date:** 2026-06-20
**Status:** Accepted
**Closes:** [dev-env#371](https://github.com/brownm09/dev-env/issues/371)
**Tags:** git, credential-manager, worktree, agent-session, windows, gh-cli, workflow, global-rule
**Related:** [ADR-035](035-git-push-delete-web-session-constraint.md) (sibling remote-git-op platform constraint), [ADR-005](005-global-core-hooks-path.md) (global git-config standardization for a workflow invariant)

---

## Context

Git for Windows ships **Git Credential Manager (GCM)** as the default `credential.helper` for HTTPS
remotes. When git needs a credential for `github.com` and GCM has none cached (or must refresh one),
GCM launches the `GitHub.UI.exe` GUI OAuth dialog to authenticate interactively.

In Claude-managed worktree / non-interactive **agent sessions** on Windows there is no interactive
desktop session driving that dialog, so it never resolves. The consequence: **every remote git
operation over HTTPS — `git push`, `git fetch`, `git ls-remote` — hangs until timeout.** In the
originating incident (2026-06-20) ~15 stuck `GitHub.UI.exe` processes accumulated across one session
and had to be force-killed (`taskkill //F //IM GitHub.UI.exe`), burning tokens on recovery.

Crucially, `gh` itself stays authenticated the entire time — its OAuth token lives in the OS keyring,
not behind the GUI — so gh-mediated operations never hang. The two credential paths are independent:
**git → GCM (GUI)**, **gh → keyring token**. Only raw git-over-HTTPS is affected.

This is the git-credential analogue of [ADR-035](035-git-push-delete-web-session-constraint.md): a
remote git operation that works in an ordinary interactive local session fails opaquely in an agent
session and costs tokens to recover. There the failure was a sandbox proxy; here it is the local
credential helper. The fix follows the same shape — route the operation through `gh`, which is already
authenticated.

---

## Decision

Standardize git's GitHub credential helper on `gh`'s token, machine-wide, by running once:

```bash
gh auth setup-git
```

This sets the global config key `credential.https://github.com.helper` to `!gh auth git-credential`
([gh manual](https://cli.github.com/manual/gh_auth_setup-git)). git then resolves `github.com`
credentials from gh's keyring-stored OAuth token over authenticated HTTPS and **never invokes the GCM
GUI**, so remote operations complete non-interactively instead of hanging. Applied 2026-06-20 and
verified by a no-hang `git ls-remote` smoke test (and confirmed again by the merge-base `git fetch`
in the session that authored this ADR, which returned immediately).

Two further commitments:

1. **Per-command fallback** for any environment where the global helper is absent (a fresh machine, a
   wiped config) — point a single op at gh's token and fail fast instead of hanging:
   ```bash
   GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c 'credential.helper=!gh auth git-credential' <push|fetch|...>
   ```
   The empty `-c credential.helper=` clears any inherited helper so GCM does not run; the second `-c`
   uses gh's helper; `GIT_TERMINAL_PROMPT=0` makes it error rather than block if no credential is
   available.
2. **Document** the symptom, the per-command fallback, the standardization, the verification smoke
   test, and the revert in `docs/REFERENCE.md` →
   [Git Workflow Runbooks](../REFERENCE.md#remote-git-ops-hang-on-the-git-credential-manager-gui-agent--worktree-sessions),
   so the global-config change is explained in-repo rather than living only as an unexplained machine
   state ([ADR-038](038-durable-preferences-documented-in-repo.md)).

**Revert** (restores GCM as the github.com helper — and the hang in agent sessions):

```bash
git config --global --unset-all credential.https://github.com.helper
```

---

## Consequences

- Remote git ops in agent/worktree sessions no longer hang on the GCM GUI; the failure class (stuck
  `GitHub.UI.exe` dialogs → `taskkill` recovery → token waste) is removed.
- The change is **global git config, machine-wide** — it affects all repos and every session on this
  machine, not only agent sessions. Interactive local sessions also route `github.com` credentials
  through gh's token, which is fine (gh is the authenticated CLI here) and yields a single credential
  path regardless of session type.
- It depends on `gh` staying authenticated. If gh's token lapses, git ops fail with a **fast
  credential error**, not a GUI hang — still strictly better than the original failure mode.
- New machines / fresh environments must re-run `gh auth setup-git` (or use the per-command fallback)
  — captured in the runbook.
- Fully reversible with a single `--unset-all`. The change is recorded here precisely because
  `git log` over the docs would not explain *why* a global credential helper was standardized.

---

## Alternatives Considered

- **Per-command workaround only** (`GIT_TERMINAL_PROMPT=0 git -c credential.helper=… <op>` on every
  remote op). Rejected as the *primary* fix: it must be remembered and prepended to every push/fetch,
  and a single forgotten prefix reintroduces the hang. Kept as the documented fallback for
  environments without the global helper.
- **Set `GIT_TERMINAL_PROMPT=0` globally without changing the helper.** Rejected: it converts the
  hang into a hard failure (git can no longer prompt) but does not authenticate — git still has no
  working `github.com` credential in the agent session, so pushes/fetches fail outright. It removes
  the hang but not the inability to reach the remote.
- **Uninstall or disable Git Credential Manager.** Rejected: heavier-handed, affects all hosts (not
  just `github.com`), and removes a tool the user may want in interactive sessions. Pointing only the
  `github.com` helper at gh is the minimal, reversible change.
- **Switch remotes to SSH.** Rejected: would require re-keying remotes and changing clone URLs across
  many repos and worktrees; the HTTPS + gh-token path is already the authenticated path here (gh is
  set up) and needs no per-repo change.
