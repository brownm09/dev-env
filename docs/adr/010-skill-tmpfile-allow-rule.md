# ADR 010 — Skill Temp File Writes: `Bash(TMPFILE=*)` Allow Rule

**Date:** 2026-05-02  
**Status:** Accepted  

---

## Context

Skills that produce large text output (e.g., `/review`) need to pass that content to CLI tools like `gh pr comment`. Passing the content via a shell argument fails because review bodies contain backticks, code fences, and other characters that break shell quoting. The standard workaround is to write the content to a temp file and pass the path via `--body-file`.

The natural Bash pattern for this is:

```bash
TMPFILE="C:/Users/brown/.claude/scratch/review_comment_$$.md"
cat > "$TMPFILE" << 'REVIEW_EOF'
...content...
REVIEW_EOF
gh pr comment "<PR_URL>" --body-file "$TMPFILE"
rm -f "$TMPFILE"
```

### The permission matching problem

`permissions.allow` rules use prefix-wildcard matching against the full Bash command string. The above command starts with `TMPFILE=` (the variable assignment). No existing allow rule matched this prefix, so a permission prompt fired in bypass mode even though the intent was a straightforward scratch-file write.

---

## Decision

Add `Bash(TMPFILE=*)` to `permissions.allow`.

The pattern matches any Bash command starting with `TMPFILE=`, which is a recognizable, contained shell idiom for temp file setup. It does not permit arbitrary Bash execution — only commands where the first token is a `TMPFILE` variable assignment.

---

## Alternatives Considered

**Use the Write tool instead of a Bash heredoc:** The Write tool is already allowed for `C:/Users/brown/.claude/scratch/**`. Replacing the heredoc with a Write tool call eliminates the need for the allow rule. However, this approach requires the skill spec to carry an unusual constraint ("use the Write tool here, not Bash"), the `$$` PID — a reliable source of uniqueness — is unavailable, and the model must improvise a unique filename. The Bash heredoc is more natural and idiomatic; the allow rule is the appropriate place to capture the permission boundary.

**Add a narrower rule:** Allow rules match on prefixes; the full heredoc command is long and variable, making a stable narrow rule impractical without being overly brittle.

---

## Consequences

- `settings.json` includes `"Bash(TMPFILE=*)"` in `permissions.allow`.
- Skills may use the `TMPFILE=... cat > ... << 'EOF'` heredoc pattern for scratch writes without triggering a permission prompt.
- The `$$` PID expansion remains the standard way to generate unique temp filenames in skill Bash blocks.

---

## References

- Issue #155: `fix: review skill temp file write triggers permission prompt`
- PR #156: `fix: use Write tool for review temp file to avoid permission prompt` (initial approach; revised to allow rule in same PR)
- ADR 003: Config Artifacts in Version Control via Symlinks (scratch path conventions)
