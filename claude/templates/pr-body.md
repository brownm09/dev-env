# PR Body Template

This file defines the structure skills must follow when composing pull request bodies.
Read it before drafting any PR body. Adapt — do not copy verbatim.

---

## Required sections (every PR)

### Summary

1–3 bullet points. Lead with *what* changed, follow with *why*.
Use imperative mood: "Add X", "Remove Y", "Fix Z".
Do not restate the PR title.

---

## Conditional sections

### Changes
Include when 3+ files are changed or the file structure is non-obvious.
Brief list: what was added, modified, or deleted and where.
Omit for small, self-explanatory PRs.

### Test plan
Include for all code PRs. Omit for docs-only and journal PRs.
Use `- [ ]` checkboxes so reviewers can check off steps as they verify.

---

## Footer (every PR)

End every PR body with exactly:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## Patterns by PR type

### Code PR

```
## Summary

- <what changed and why — bullet 1>
- <what changed and why — bullet 2>

## Test plan

- [ ] <step a reviewer can follow to verify the change>
- [ ] <step>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### Docs / proposal PR

```
## Summary

- <what was written or updated>
- <why — which decision, feature, or gap it addresses>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### Journal PR

```
End-of-day journal: <one-line topic summary>.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```
