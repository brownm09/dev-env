# ADR 002 — Journal-Compose Session Isolation

**Date:** 2026-04-04  
**Status:** Accepted

---

## Context

`/journal-compose` is a heavyweight operation: it reads all stub files for a day, cross-references open PRs, constructs the 11-section canonical document, updates READMEs, and opens a PR. Early usage ran it at the end of implementation sessions alongside other work. Two failure modes emerged:

1. **Context contamination:** Implementation context (code, test output, PR diffs) inflated the session token budget and bled into the journal's "Reflection" and "Key Decisions" sections, producing inaccurate narratives.
2. **Inconsistency:** Changes made after composition began (e.g., a PR merged mid-compose) were not captured, leaving the composed document out of date the moment it was written.

---

## Decision

`/journal-compose` must run in a **dedicated session** with no prior work in that session. If composition is requested mid-session (after other work has been done), the response is:

> "Journal composition must run in its own session. Open a new Claude Code session and invoke `/journal-compose` there."

Then stop — do not compose.

This rule is enforced both in `claude/CLAUDE.md` and as a hard check at Step 0 of the `journal-compose` skill.

---

## Consequences

- Day-end journal composition always starts with a clean context window — lower token cost and higher accuracy.
- Users must explicitly open a new session to compose; this is a deliberate friction point.
- The skill's Step 0 check catches accidental in-session invocations before any reads are performed.
- Review-only sessions (`/review <PR-URL>`) and push-only sessions (addressing review findings) are explicitly excluded from the auto-stub rule and do not trigger a new composition session.

---

## References

- Engineering journal: `sessions/meta/2026-04-05-workflow-and-journal-setup.md` (composition rules)
- `dev-env` commit `781da17` — `feat: enforce journal-compose session isolation`
- `claude/skills/journal-compose/SKILL.md` — Step 0 isolation check
- `claude/CLAUDE.md` § Engineering Journal — Composition rules
