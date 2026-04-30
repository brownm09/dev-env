# dev-env — Project Instructions

The global Claude Code configuration lives in [`claude/CLAUDE.md`](claude/CLAUDE.md),
symlinked to `~/.claude/CLAUDE.md`. All workflow rules, hook invariants, model selection
guidelines, and journal conventions are defined there and apply to every project.

## Reference Documentation

| Doc | Purpose |
|---|---|
| [README.md](README.md) | Quick-reference tables for skills, hooks, and routines |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Detailed descriptions, invocation syntax, config options, and ADR links |
| [docs/adr/](docs/adr/) | Design decisions behind rules in `claude/CLAUDE.md` |

## Testing

Run from the repo root to verify all hook scripts are syntax-clean:

```bash
python3 -m py_compile claude/scripts/*.py
```

For docs-only changes to `claude/CLAUDE.md`: run `grep -n 'date -u' claude/CLAUDE.md` and
confirm every match is in an internal operational artifact context (lock files, log timestamps)
— not in stub filename or branch name descriptions.

## Documentation Maintenance

When a PR modifies any of the paths below, update the listed reference docs **in the same PR**.

| Change | Required updates |
|---|---|
| Add / remove / rename a skill in `claude/skills/` | Skills table in `README.md` + `docs/REFERENCE.md` Skills section |
| Add / remove / rename a script in `claude/scripts/` or `claude/hooks/` | Hooks table in `README.md` + `docs/REFERENCE.md` Hooks section |
| Add / remove / rename a routine in `claude/routines/` | Routines table in `README.md` + `docs/REFERENCE.md` Routines section |
| Change `hook-config.json` schema (new field, removed field, type change) | Configuration subsection in `docs/REFERENCE.md` |
| Change a skill's invocation syntax or options | Skill entry in `docs/REFERENCE.md` |
| Rename or move any file linked in `README.md` or `docs/REFERENCE.md` | Update the link in both files |
