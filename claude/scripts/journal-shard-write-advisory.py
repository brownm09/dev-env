#!/usr/bin/env python3
"""Claude Code PostToolUse hook — advisory reminder when a Write, Edit, or Bash call
touches an engineering-journal shard file that violates the required-field schema
(dev-env #423, #556; ADR-081).

`validate-manifest.py` gates manifest shards only at compose time — the *next day's*
`/journal-compose` run. A shard written (or re-created) after the morning compose
bypasses that gate for a full day; the gap surfaces only when a human hand-repairs it
during the next compose (observed 2026-07-02: 15 career-playbook shards missing
`topic`/`tokens`, 3 meta shards with `summary` instead of `topic`, 1 meta shard + an
open-PR shard written with a UTF-8 BOM that every JSON reader silently skips).

This hook converts that late, silent violation into an immediate, visible one — at the
moment the shard is written, in the writing session. It validates **on-disk bytes**, not
tool-call payload content: for Write/Edit the tool's own `file_path` is checked directly;
for Bash, candidate shard paths are harvested from the raw command text and each one that
resolves to a real file on disk is read and validated. Validating on-disk state (rather
than command intent) means the harvest is a plain regex scan over the whole command —
deliberately NOT anchored via `_hookio.scan_top_level` the way `pr-merge-reminder.py` /
`pre-merge-numbering-check.py` anchor their CLI-invocation detection. A path merely
mentioned inside a heredoc body, a quoted argument, or a `$()` subshell is validated
read-only if it happens to name a real file — over-matching is harmless here, unlike
those hooks where an unanchored match would misfire *action* on text that was never a
real command.

Three shard kinds are validated: manifest shards, open-PR shards, and — since ADR-118's
enforcement phase (dev-env#870) — **tile shards** `sessions/<project>/tiles/<issue>.json`.
The tile kind is the one whose write is otherwise wholly unverified: a manifest or open-PR
shard is written by a session that is also doing PR bookkeeping, while a tile shard is
written immediately after a `spawn_task` call whose payload it exists to preserve. If it is
malformed, the failure is silent *and* delayed — `iter_numeric_shards` skips an unparseable
shard without a word, so the loss surfaces only when someone needs the payload back after a
crash, which is exactly when it cannot be reconstructed.

Schema validation itself is shared with `validate-manifest.py` via `_journal_schema.py`
(never duplicated) — see that module's docstring; the tile kind uses its
`missing_tile_fields` / `TILE_REQUIRED_FIELDS`, not a third copy.
`_journal_shards.shard_number` supplies the numeric-filename check for **both** the open-PR
and tile kinds (it was `shard_pr_number` when open-PR was the only numeric kind; the generic
name is the honest one now that two kinds share it — the two are the same function).

The Bash trigger also fires for the PowerShell tool (dev-env#763): registered
under both the Bash and PowerShell PostToolUse matchers in settings.json (the
separate Write/Edit matchers are unaffected), since PowerShell is an equally
sanctioned way to run the git/journal commands this hook harvests candidate
shard paths from.

Stdin JSON shape (PostToolUse):
  Write/Edit: {"tool_name": "Write", "tool_input": {"file_path": "...", ...}, "cwd": "...", ...}
  Bash:       {"tool_name": "Bash", "tool_input": {"command": "..."}, "cwd": "...", ...}  # or "PowerShell"

Exit 0 — no candidate shard path had a problem (including: not a journal shard, no
         candidate paths found, or every found shard is healthy); silent.
Exit 2 — at least one candidate shard file violates the schema; advisory on stderr. The
         write already happened — this surfaces the problem, it does not block.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import _hookutil

try:
    from _journal_schema import (
        decode_shard_bytes,
        malformed_manifest_fields,
        missing_open_pr_fields,
        missing_required_fields,
        missing_tile_fields,
        parse_manifest_text,
    )
    from _journal_shards import shard_number
except Exception:
    # Module-level import failure would otherwise crash before main()'s own
    # try/except is ever reached, escaping the safe-exit guard entirely (an
    # advisory hook must exit 0 on every code path, not just the ones inside
    # __main__ — see docs/REFERENCE.md -> Hooks -> Authoring rules #2).
    sys.exit(0)

MAX_CANDIDATES = 20
MAX_SHARD_BYTES = 1_048_576
MAX_FILES_SHOWN = 10
MAX_COMMAND_CHARS = 2_000
JOURNAL_FALLBACK = Path.home() / "Git" / "engineering-journal"

# Raw scans over the whole command text — see module docstring for why this is
# deliberately not `scan_top_level`-anchored. `re.ASCII` restricts `\w` to plain
# [A-Za-z0-9_] since journal paths are always ASCII.
_MANIFEST_TOKEN_RE = re.compile(r"[\w./\\:~-]+\.manifest\.jsonl\b", re.ASCII)
_OPEN_PR_TOKEN_RE = re.compile(r"[\w./\\:~-]*open-prs[/\\][\w.-]+\.json\b", re.ASCII)
# Tile shards (ADR-118). Same shape as the open-PR token regex, one directory name over.
_TILE_TOKEN_RE = re.compile(r"[\w./\\:~-]*tiles[/\\][\w.-]+\.json\b", re.ASCII)
_DIR_ARG_RE = re.compile(r"(?:\bcd\s+|\bgit\s+-C\s+|--git-dir[= ]\s*)(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")


def normalize_path(token: str) -> str:
    """Normalize a path-like token for comparison and on-disk resolution: strip
    surrounding quotes, backslashes to forward slashes, a Git-Bash `/c/...` drive root
    to `C:/...`, and expand a leading `~`."""
    t = token.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        t = t[1:-1]
    t = t.replace("\\", "/")
    m = re.match(r"^/([A-Za-z])/(.*)$", t)
    if m:
        t = f"{m.group(1).upper()}:/{m.group(2)}"
    if t.startswith("~"):
        t = os.path.expanduser(t).replace("\\", "/")
    return t


def classify_shard_path(path: str) -> str | None:
    """Classify a filesystem path as an engineering-journal shard kind, or ``None``.

    Requires an ``engineering-journal`` or ``engineering_journal`` path *component*
    followed later by a ``sessions`` component — not anchored to the canonical checkout
    root, so a shard under a Claude-managed worktree of the journal repo (e.g.
    ``engineering-journal/.claude/worktrees/<x>/sessions/...``) still classifies. A path
    missing that component pair (a non-journal repo, or a journal path with no
    ``sessions``) returns ``None`` regardless of its filename.
    """
    norm = path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return None

    journal_idx = None
    for i, part in enumerate(parts):
        if part in ("engineering-journal", "engineering_journal"):
            journal_idx = i
            break
    if journal_idx is None:
        return None
    if "sessions" not in parts[journal_idx + 1:]:
        return None

    if norm.endswith(".manifest.jsonl"):
        return "manifest"
    if len(parts) >= 2 and parts[-2] == "open-prs" and norm.endswith(".json"):
        return "open-pr"
    if len(parts) >= 2 and parts[-2] == "tiles" and norm.endswith(".json"):
        return "tile"
    return None


def extract_candidate_tokens(command: str) -> list[str]:
    """Harvest candidate shard-path tokens from a raw Bash command string, deduped
    (preserving first-seen order) and capped at ``MAX_CANDIDATES``.

    Commands longer than ``MAX_COMMAND_CHARS`` are skipped entirely (return ``[]``)
    before either regex runs. Both token regexes lead with an unbounded greedy
    character class followed by a required literal suffix — a shape that costs
    O(n^2) via `re.findall`'s per-start-position retries when the suffix never
    appears in a long run of matching characters (verified: ~10s for one regex
    against a 40,000-character run of plain word characters). Real journal-touching
    commands are always short (a handful of file paths), so this cap costs nothing
    for legitimate use while bounding the worst case to a few tens of milliseconds —
    this hook fires on every Bash call in every session, not just journal work.
    """
    if len(command) > MAX_COMMAND_CHARS:
        return []
    tokens = []
    seen = set()
    for pattern in (_MANIFEST_TOKEN_RE, _OPEN_PR_TOKEN_RE, _TILE_TOKEN_RE):
        for match in pattern.findall(command):
            if match not in seen:
                seen.add(match)
                tokens.append(match)
    return tokens[:MAX_CANDIDATES]


def extract_base_dirs(command: str, cwd: str) -> list[str]:
    """Base directories to resolve a relative shard token against, most-authoritative
    first: the payload `cwd`, then any `cd`/`git -C`/`--git-dir=` directory argument
    harvested from the command (the dominant real shape is `git -C <journal-repo> add
    sessions/...` run from a *different* project's cwd, so `cwd` alone is not enough),
    then the constant journal-repo fallback."""
    bases = []
    if cwd:
        bases.append(cwd)
    for match in _DIR_ARG_RE.finditer(command):
        raw = match.group(1)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        bases.append(raw)
    bases.append(str(JOURNAL_FALLBACK))

    seen = set()
    result = []
    for b in bases:
        norm = normalize_path(b)
        if norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def resolve_candidates(tokens: list[str], bases: list[str], isfile=os.path.isfile) -> list[str]:
    """Resolve each harvested token to an on-disk path. A token that normalizes to an
    absolute (drive-letter) path is used directly if it exists; a relative token is
    resolved against the first base directory where the joined path exists. A token
    that resolves against no base is dropped — fail-open, nothing to validate if no
    real file corresponds to it. ``isfile`` is injectable so callers can test fully
    offline."""
    resolved = []
    for raw in tokens:
        norm = normalize_path(raw)
        if re.match(r"^[A-Za-z]:/", norm):
            if isfile(norm):
                resolved.append(norm)
            continue
        for base in bases:
            candidate = normalize_path(base).rstrip("/") + "/" + norm
            if isfile(candidate):
                resolved.append(candidate)
                break
    return resolved


def candidate_paths(tool_name: str, tool_input: dict, cwd: str, isfile=os.path.isfile) -> list[str]:
    """Candidate shard paths touched by this tool call. Write/Edit: the exact
    `file_path` from the payload (existence is checked later, uniformly, in
    `collect_problems`). Bash/PowerShell: paths resolved from tokens harvested out of
    the command string (dev-env#763). Any other tool: none."""
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        return [file_path] if file_path else []
    if tool_name in ("Bash", "PowerShell"):
        command = tool_input.get("command", "")
        if not command:
            return []
        tokens = extract_candidate_tokens(command)
        if not tokens:
            return []
        bases = extract_base_dirs(command, cwd)
        return resolve_candidates(tokens, bases, isfile)
    return []


def validate_shard_bytes(raw: bytes, kind: str, stem: str, num_from_name: int | None = None) -> list[str]:
    """Validate a shard file's raw bytes against its schema.

    ``kind`` is ``"manifest"``, ``"open-pr"``, or ``"tile"`` (from `classify_shard_path`).
    ``stem`` is the filename stem (no extension) used only for message text.
    ``num_from_name`` is the result of `_journal_shards.shard_number` against the real path
    — ``None`` means the filename stem is not a plain non-negative integer (not a valid
    numeric shard name); consulted for the two numeric kinds (``"open-pr"``, ``"tile"``) and
    ignored for ``"manifest"``, whose filename is a timestamp.

    Returns human-readable problem strings in the order found; empty when the shard is
    healthy. A decode problem (e.g. a BOM) does not short-circuit field validation — the
    text past the BOM is still checked, so both problems are reported together.
    """
    problems: list[str] = []
    text, encoding_problem = decode_shard_bytes(raw)
    if encoding_problem:
        problems.append(encoding_problem)
    if text is None:
        return problems

    # Checked before the empty/parse-failure returns below: the filename is invalid
    # regardless of whether the content is also empty or malformed, and it's the more
    # important diagnosis (every reader enumerates open-PR shards by filename) — an
    # empty-but-numeric-stem file shouldn't hide a non-numeric-stem problem or vice versa.
    if kind == "open-pr" and num_from_name is None:
        problems.append(
            f"non-numeric filename '{stem}.json' - invisible to every open-PR reader "
            "(reconcile/post-compact/compose)"
        )
    if kind == "tile" and num_from_name is None:
        problems.append(
            f"non-numeric filename '{stem}.json' - invisible to every tile reader "
            "(reconcile-pending-tiles/post-compact); the filename IS the issue key"
        )

    if not text.strip():
        problems.append("empty shard (no JSON object)")
        return problems

    if kind == "manifest":
        for lineno, entry in parse_manifest_text(text):
            if entry is None:
                problems.append(f"line {lineno}: not a JSON object")
                continue
            missing = missing_required_fields(entry)
            if missing:
                problems.append(f"missing {', '.join(missing)}")
            problems.extend(malformed_manifest_fields(entry))
        return problems

    # kind == "open-pr" or "tile" — both are a single JSON object in a <N>.json file.
    try:
        entry = json.loads(text.strip())
    except json.JSONDecodeError:
        problems.append("not a JSON object")
        return problems
    if not isinstance(entry, dict):
        problems.append("not a JSON object")
        return problems

    if kind == "tile":
        missing = missing_tile_fields(entry)
        if missing:
            problems.append(f"missing {', '.join(missing)}")
        # The filename is the authoritative issue key (ADR-118), so a disagreeing `issue`
        # field is not a cosmetic mismatch: reconcile-pending-tiles.py treats it as corrupt
        # and refuses to reconcile the shard at all, which silently exempts that tile from
        # pruning forever. Flag it at write time, where it is still one keystroke to fix.
        if num_from_name is not None and "issue" in entry and entry["issue"] != num_from_name:
            problems.append(
                f"filename stem '{stem}' does not match embedded issue={entry['issue']!r} "
                "- reconcile-pending-tiles skips this shard as corrupt"
            )
        return problems

    missing = missing_open_pr_fields(entry)
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if num_from_name is not None and "pr" in entry and entry["pr"] != num_from_name:
        problems.append(f"filename stem '{stem}' does not match embedded pr={entry['pr']!r}")
    return problems


def collect_problems(paths: list[str]) -> list[tuple[str, list[str]]]:
    """The one impure loop: classify -> exists-on-disk -> size cap -> read bytes ->
    `validate_shard_bytes`. A path that doesn't classify, doesn't exist, or exceeds the
    size cap is silently skipped (fail-open) rather than reported as a problem."""
    results: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)

        kind = classify_shard_path(path)
        if kind is None:
            continue
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getsize(path) > MAX_SHARD_BYTES:
                continue
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            continue

        stem = Path(path).stem
        num_from_name = shard_number(Path(path)) if kind in ("open-pr", "tile") else None
        problems = validate_shard_bytes(raw, kind, stem, num_from_name)
        if problems:
            results.append((path, problems))
    return results


def format_advisory(problems: list[tuple[str, list[str]]]) -> str:
    """Build the exit-2 advisory text. Aggregates every file's problems into one
    message; caps the number of files shown (further files are summarized by count, not
    silently dropped) so a pathological batch write can't produce unbounded stderr."""
    lines = [
        "[journal-shard] Journal shard file(s) violate the required-field schema "
        "(written or referenced by the last tool call):",
    ]
    shown = problems[:MAX_FILES_SHOWN]
    for path, file_problems in shown:
        lines.append(f"  - {path}: {'; '.join(file_problems)}")
    remaining = len(problems) - len(shown)
    if remaining > 0:
        noun = "file" if remaining == 1 else "files"
        lines.append(f"  ... and {remaining} more {noun} with problems (not shown)")
    lines.append(
        "Fix each file now, in this session - the write already happened; "
        "this notice does not block."
    )
    lines.append(
        '  manifest schema: {"stub":"sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md",'
        '"topic":"<H2>","tokens":{"input":N,"output":N,"cost":N},'
        '"prs_opened":[],"prs_closed":[]}'
    )
    lines.append(
        '  open-pr schema:  {"pr":N,"url":"https://github.com/<owner>/<repo>/pull/N",'
        '"topic":"<H2>","stub":"YYYY-MM-DD_HHMMSS.stub.md","opened":"YYYY-MM-DD"}'
    )
    lines.append(
        '  tile schema:     {"issue":N,"url":"https://github.com/<owner>/<repo>/issues/N",'
        '"title":"<chip label>","tldr":"<tooltip>","prompt":"<full spawn_task prompt>",'
        '"cwd":"<target repo path>","spawned":"YYYY-MM-DD"}  (stub optional, project-qualified)'
    )
    lines.append(
        "Build a tile shard with a JSON serializer, never echo - `prompt` is free prose, so "
        "interpolating it corrupts the shard or escapes into the shell."
    )
    lines.append(
        "A manifest re-created after a compose consumed the original must carry the "
        "FULL field set, not just the updated field."
    )
    lines.append(
        "Rewrite BOM'd files as UTF-8 without BOM. Schemas: docs/REFERENCE.md -> "
        "Engineering Journal Internals (dev-env ADR-081)."
    )
    return "\n".join(lines)


def main() -> None:
    _hookutil.record_heartbeat("journal-shard-write-advisory")
    raw_stdin = sys.stdin.read().strip()
    if not raw_stdin:
        sys.exit(0)
    try:
        data = json.loads(raw_stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "Bash", "PowerShell"):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        # PostToolUse always sends tool_input as an object; guard explicitly
        # rather than relying on the outer safe-exit guard to catch the
        # AttributeError a non-dict payload would otherwise raise.
        sys.exit(0)
    cwd = data.get("cwd", "") or ""

    paths = candidate_paths(tool_name, tool_input, cwd)
    if not paths:
        sys.exit(0)

    problems = collect_problems(paths)
    if problems:
        print(format_advisory(problems), file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # safe-exit guard: never block or crash a Write/Edit/Bash
        sys.exit(0)
