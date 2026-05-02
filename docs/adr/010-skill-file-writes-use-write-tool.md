# ADR 010 — Skill File Writes: Use the Write Tool, Not Bash

**Date:** 2026-05-02  
**Status:** Accepted  

---

## Context

Skills that produce large text output (e.g., `/review`) need to pass that content to CLI tools like `gh pr comment`. Passing the content via a shell argument fails because review bodies contain backticks, code fences, and other characters that break shell quoting. The standard workaround is to write the content to a temp file and pass the path via `--body-file`.

The question is *how* to perform the file write step.

### Claude Code permission matching

`permissions.allow` rules use prefix-wildcard matching. `Bash(gh pr comment *)` matches any Bash command string that starts with `gh pr comment`. A Bash command that starts with `TMPFILE=` matches nothing — it triggers a permission prompt regardless of what the command does.

`Write(C:/Users/brown/.claude/scratch/**)` covers the **Write tool**, not Bash commands that write files. Shell heredocs (`cat > "$TMPFILE" << 'EOF'`) and similar patterns are Bash tool calls, not Write tool calls — the allow rule does not apply to them.

### The heredoc failure mode

The original `/review` skill Step 9 used a placeholder comment inside a Bash block:

```bash
TMPFILE="C:/Users/brown/.claude/scratch/review_comment_$$.md"
# write the full review output from Step 8 to $TMPFILE
gh pr comment "<PR_URL>" --body-file "$TMPFILE"
```

When executing this step, the model naturally resolves "write to `$TMPFILE`" as a Bash heredoc:

```bash
cat > "$TMPFILE" << 'REVIEW_EOF'
...review content...
REVIEW_EOF
```

The full command string starts with `TMPFILE=` (the variable assignment), which matches no allow rule. A permission prompt fires even in bypass mode.

---

## Decision

Skills that need to write content to a scratch file before passing it to a CLI tool **must use the Write tool** for the file-write step, not a Bash command.

The scratch path `C:/Users/brown/.claude/scratch/**` is already in `permissions.allow` for the Write tool. No additional allow rule is needed.

The skill spec must be explicit — a comment placeholder inside a Bash block is underspecified and will be resolved as a Bash heredoc by the model.

**Correct pattern:**

```
TMPFILE = "C:/Users/brown/.claude/scratch/<name>_<PID>.md"
[Write tool] → write content to TMPFILE

Then in Bash:
<cli-tool> --body-file "C:/Users/brown/.claude/scratch/<name>_<PID>.md"
rm -f "C:/Users/brown/.claude/scratch/<name>_<PID>.md"
```

---

## Alternatives Considered

**Add `Bash(TMPFILE=*)` to `permissions.allow`:** Rejected. The pattern is too broad — it would silently allow any Bash command that starts with a variable assignment, well beyond the intended scope of "write a review to a temp file."

**Add a narrower allow rule (e.g., `Bash(TMPFILE=... cat > ...)`):** Not feasible. Allow rules match on command string prefixes; the full heredoc command is long and variable, making a stable narrow rule impractical.

---

## Consequences

- All skills that write scratch files must use the Write tool for that step.
- The Bash block in Step 9 of `/review` (and any similar skill step) contains only the CLI invocation and cleanup — no file write.
- The Write tool handles arbitrary markdown content (backticks, code fences, special characters) without quoting issues, so there is no functional downside.
- Any future skill following the "write to scratch, pass path to CLI" pattern must follow this convention from the start.

---

## References

- PR #155: `fix: review skill temp file write triggers permission prompt` (issue)
- PR #156: `fix: use Write tool for review temp file to avoid permission prompt`
- ADR 003: Config Artifacts in Version Control via Symlinks (scratch path conventions)
