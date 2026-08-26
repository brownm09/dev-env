# ADR-139: Machine-Local, Project-Scoped App-Written Settings Belong in That Project's `.claude/settings.local.json`, Never the Global Tracked File

**Date:** 2026-08-25
**Status:** Accepted
**Tags:** config, symlinks, version-control, dev-env, settings, settings-local, auto-mode, dev-env-sync, silent-failure, global-rule, duplicate-issue, claude-behavior, correction, adr-003, adr-006, adr-071, adr-079, adr-098, adr-110, adr-130

---

## Context

`claude/settings.json` is dev-env's tracked, version-controlled global config
([ADR-003](003-config-in-version-control.md)), symlinked into `~/.claude/settings.json` on this
machine so every project's session reads the same permissions, hooks, and preferences. Claude Code
itself also writes to this file — `/config` toggles, permission "don't ask again" approvals, and
(the trigger for this ADR) an **auto-mode environment scan** it runs when auto-mode is enabled in a
specific project.

[dev-env#1050](https://github.com/brownm09/dev-env/issues/1050) and
[dev-env#1049](https://github.com/brownm09/dev-env/issues/1049) were filed independently, minutes
apart, by two concurrent sessions that each discovered the same live block: the canonical
checkout's `claude/settings.json` had uncommitted local modifications, so `dev-env-sync.py`'s
`git pull --ff-only` ([ADR-006](006-dev-env-sync-on-every-prompt.md)) failed on every prompt —
escalating to the persistent-failure path added in
[ADR-110](110-escalate-persistent-dev-env-sync-ff-failures.md). Per
[ADR-130](130-session-start-fetch-ff-only-or-warn.md), `session-start-sync.py` had already fetched
`origin/main` at session start, so the divergence was visible immediately; the block was the dirty
working tree, not a stale fetch.

The dirty diff was **four things at once**, not a single accidental edit:

1. Two `permissions.allow` entries **removed** (`Bash(node -e *)`, `Bash(rm -f
   C:/Users/brown/.claude/scratch/*)`) — both load-bearing for the `jq`-replacement recipe in
   `claude/CLAUDE.md` → Platform & Environment. No plausible intentional reason to drop either.
2. `"effortLevel": "max"` **removed**.
3. `"skipWorkflowUsageWarning": true` **added** — an ordinary boolean preference toggle, the same
   shape as the file's pre-existing `theme`/`tui`/`*NotifEnabled` keys.
4. A large `autoMode` block **added** — the auto-mode classifier's `environment` and `soft_deny`
   arrays, entirely **career-playbook-specific**: it names that repo's visibility, its
   `applications/` PII locations, and a career-playbook-only merge-policy rule.

Item 4 is the one that makes this more than a two-line fix: it is project-specific content sitting
in a file every project's Claude Code session on this machine loads. #1049's own analysis (filed
first, more structurally framed) proposed three directions and judged one "strongly preferred":
split the file so version-controlled/shared keys stay in `claude/settings.json` and
machine-local/app-written keys move to `settings.local.json` — "it removes the conflict by
construction rather than managing it." Neither #1049 nor #1050 was resolved unilaterally at
discovery time: per [ADR-079](079-backup-restore-convention.md) ("back up before you mutate"), the
#1049 session captured the exact dirty state in a labeled `git stash` (`"On main: dev-env#1049:
app-written settings.json state (autoMode/theme/tui/notif; drops effortLevel + 2 permissions)"`)
rather than guessing at commit-vs-discard — a discard would have destroyed the user's auto-mode
setup with no record; a blind commit would have shipped career-playbook's private environment scan
into the global tracked file. That stash is what silently unblocked the canonical mid-investigation
of #1050: once the working tree was clean, `dev-env-sync.py`'s own per-prompt pull succeeded
repeatedly (visible in `git reflog` as a run of `pull --ff-only origin main: Fast-forward` entries),
fast-forwarding to the then-current `origin/main` tip with no further intervention needed for the
*acute* blockage — only for the underlying question of where items 3 and 4 should actually live.

**#1049's proposed "split" assumed a mechanism that does not exist.** The obvious reading of
"move machine-local keys to `settings.local.json`" is a *global* counterpart to
`~/.claude/settings.json` — e.g. `~/.claude/settings.local.json`. Verified against the primary
source, [Claude Code settings documentation](https://code.claude.com/docs/en/settings-and-configuration.md):

> "Claude Code reads settings from four files": **User** (`~/.claude/settings.json`, "You, in every
> project on this machine"), **Shared project** (`.claude/settings.json`), **Project local**
> (`.claude/settings.local.json`, "You, in this one project only"), **Managed**.

There is no fifth, user-scope local file. `.claude/settings.local.json` is a **per-project**
mechanism exclusively — Claude Code auto-adds it to the user's global git-excludes file the first
time it writes one in a repo that doesn't already ignore it, and it is read *only* from the project
directory (or its repository root) where it lives. A `~/.claude/settings.local.json` would simply
never be loaded.

## Decision

Resolve both issues with one PR, since the fix satisfies the stated acceptance criteria of each:

1. **`autoMode` moves to career-playbook's own project-local settings** —
   `C:/Users/brown/Git/career-playbook/.claude/settings.local.json` — the one real mechanism for
   "personal, project-scoped, git-excluded settings" Claude Code actually has. Verified already
   covered by that project's own `.gitignore` (`git check-ignore -v` confirms
   `.claude/settings.local.json` is excluded there), so no further gitignore work was needed. This
   also means the content only loads for career-playbook sessions, never for dev-env,
   lifting-logbook, or any other project on this machine — the leak #1049 and #1050 both flagged is
   closed by construction, not by convention.
2. **`skipWorkflowUsageWarning` stays in the tracked global `claude/settings.json`** — unlike
   `autoMode`, it carries no project-specific content; it is the same shape as the file's existing
   `theme`/`tui`/`agentPushNotifEnabled`/`inputNeededNotifEnabled` preference toggles, which this
   file already carries as ordinary global preferences. No reason to treat it differently.
3. **The two `permissions.allow` entries and `"effortLevel": "max"` are restored** — mechanically
   a no-op, since neither was ever removed from `origin/main`; only the *local working copy* had
   dropped them. The net diff against `origin/main` for `claude/settings.json` is the single added
   `skipWorkflowUsageWarning` line.
4. **The canonical checkout's live block is cleared** by extracting the stashed content (read-only
   `git show stash@{0}:claude/settings.json`, so a concurrent session pushing another stash entry
   afterward can't shift which stash this reads) before doing anything else, so the only remaining
   copy of the auto-mode content is never at risk. The stash itself is left in place (not popped or
   dropped) — `stash pop`/`apply` are canonical-mutating operations blocked by
   [ADR-071](071-canonical-checkout-mutate-guard-hook.md), and there is no benefit to removing a
   correctly-labeled backup once its content has been safely relocated.
5. **This ADR's rule generalizes past this one incident**: any future Claude-Code-app-written key
   that embeds *project-specific* content (a future auto-mode rescan, a per-project credential
   hint, anything naming a specific repo's paths or policies) must go into that project's own
   `.claude/settings.local.json`, never into dev-env's tracked global `claude/settings.json` — this
   file backs `~/.claude/settings.json` for *every* project on the machine, and there is no
   narrower app-writable slot to put project-scoped state in.

### What this ADR does not do

It does not implement #1049's "Direction 2" (teach `dev-env-sync.py` to auto-stash-and-reapply a
known app-written key set on every dirty-pull failure). #1049's own text called Direction 1 (the
split implemented here) "strongly preferred" precisely because it "removes the conflict by
construction rather than managing it" — once `autoMode`-shaped content has nowhere to land in the
tracked file, there is nothing left for a future auto-stash mechanism to manage. If a *different*
app-written key later drifts into the tracked file in a way this split doesn't cover, that is a new
occurrence to evaluate on its own facts, not evidence this decision was wrong.

## Consequences

- Canonical `dev-env` fast-forwards cleanly; `dev-env-sync.py` stops warning on the next prompt.
- `~/.claude/` (symlinked into the canonical) serves current `origin/main` tooling again.
- career-playbook gains a `.claude/settings.local.json` — a new file, but one entirely outside
  dev-env's own repo and outside this PR's diff; nothing here touches career-playbook's tracked
  history.
- The next time Claude Code's own auto-mode (or any future app-writer) rewrites
  `claude/settings.json` for a *different* project, the same class of drift can recur for
  whatever new key it introduces — this ADR fixes the one instance found and states the rule for
  future ones, not a mechanical guard against every possible future key. No hook enforces this
  automatically; it is a documented convention (`claude/CLAUDE.md` → Platform & Environment).

## Alternatives considered

- **Commit `autoMode` into the tracked global file as-is.** Rejected: ships career-playbook's
  private environment scan (repo visibility, PII-adjacent paths, its merge-policy hook) into every
  project's session on this machine, and any later harness rewrite of the same block reintroduces
  this exact sync block.
- **Discard the whole diff (`git checkout -- claude/settings.json`).** Rejected: silently destroys
  whatever auto-mode setup produced the block, with no record anywhere once the stash — which this
  ADR does not depend on keeping — eventually ages out.
- **A global `~/.claude/settings.local.json` overlay.** Not a real option — see Context; verified
  against the primary source that Claude Code has no such file.
- **Implement #1049's Direction 2 (auto-stash-and-reapply in `dev-env-sync.py`).** Deferred — see
  "What this ADR does not do" above.

## References

- [Claude Code settings documentation](https://code.claude.com/docs/en/settings-and-configuration.md) —
  primary source for the four-file precedence stack and `.claude/settings.local.json` being
  project-scoped only.
- [ADR-003](003-config-in-version-control.md) — why `claude/settings.json` is tracked and
  symlinked in the first place.
- [ADR-006](006-dev-env-sync-on-every-prompt.md) / [ADR-110](110-escalate-persistent-dev-env-sync-ff-failures.md) —
  the sync mechanism this ADR's fix unblocks.
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — why the canonical's stash was extracted
  read-only rather than popped.
- [ADR-079](079-backup-restore-convention.md) — the "back up before you mutate" discipline the
  #1049 session's stash followed, which is why the exact `autoMode` content survived to be
  relocated here.
- [dev-env#1049](https://github.com/brownm09/dev-env/issues/1049) — the more structurally-framed
  duplicate; closed by this PR.
- [dev-env#1050](https://github.com/brownm09/dev-env/issues/1050) — the issue this ADR was
  written under; closed by this PR.
