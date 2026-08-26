#!/usr/bin/env python3
"""Resolve engineering-journal project directories to GitHub repo slugs (dev-env #1045).

Wired into the ``journal-compose`` skill as the Step 8a **Source 3** slug resolver: the
"Start here" dashboard fills its remaining slots from open issues labeled ``start-here``
across project repos, and to query a repo it first has to know which repo a given
``sessions/<project>/`` directory belongs to.

Why this exists as a script rather than inline in the skill
-----------------------------------------------------------
Source 3 previously resolved slugs with a regex applied to ``sessions/<proj>/README.md``::

    /Repo:\\s*\\[([^\\]]+)\\]\\(https:\\/\\/github\\.com\\/([^\\/]+\\/[^\\/\\)]+)\\)/

Verified live 2026-08-25, that pattern matched **zero** of the 11 project READMEs, so
Source 3 had never contributed an entry for any repo since it was added (dev-env#292). Two
independent reasons, either of which alone was fatal:

1. Only **one** of the 11 project READMEs (``gas-lifting-logbook``) carries a repo-link line
   at all. The other ten have none -- so no per-project pattern, however wide, can resolve
   them. The canonical project -> slug mapping lives in the **root** ``README.md``, which
   Step 8 regenerates on every compose immediately before Step 8a runs.
2. The one file that does carry a line spells it ``**Repository:**``, and ``Repo:`` is not a
   substring of ``Repository:`` (after ``Repo`` comes ``s``, not ``:``). The pattern also did
   not tolerate the ``**`` bold markers that actually precede the colon.

The failure was **silent**: the loop did a bare ``continue``, so a project skipped for a
broken mapping was indistinguishable from a project with no labeled issues. That is the part
this script exists to prevent recurring -- every unresolved project is reported by name with
a reason (see ``format_report``), and a wholesale mapping failure gets its own unmistakable
``SOURCE3_MAPPING_EMPTY`` signal.

Resolution order (first hit wins), per ``sessions/<project>/`` directory
------------------------------------------------------------------------
1. **Root README (primary).** ``<root>/README.md`` split on ``^### ``; within each section a
   ``**Repo:** [..](https://github.com/<owner>/<repo>)`` bullet is correlated with a
   ``**Journal:** [sessions/<project>/](...)`` bullet. The *Journal* bullet is what ties a
   section to a directory name -- section titles deliberately do not match directory names
   (``### Job Search`` -> ``sessions/job-search/``), so keying on the heading would not work.
2. **Per-project README (fallback).** ``<root>/sessions/<project>/README.md``, accepting
   ``Repo:`` **or** ``Repository:``, with or without ``**`` bold markers. This is what keeps
   a project resolvable when it has been given a repo line locally but does not yet have a
   root-README section.
3. Otherwise the project is **skipped**, with a reason naming both lookups and why each missed.

Every resolved slug is shape-validated against ``SLUG_RE`` before it is emitted, because the
caller interpolates it straight into a ``gh issue list --repo <slug>`` command line -- a
malformed README must produce a reported skip, never a shell argument.

Usage::

    py -3 journal-project-repo-map.py <engineering-journal-root> [--json <outfile>]

The root is a required positional on purpose: Step 8a runs inside the disposable compose
worktree (``$WT``), so defaulting to the canonical checkout would silently map the wrong tree.

Exit 0 -- resolution succeeded (skips are information, not failure).
Exit 1 -- ``SOURCE3_MAPPING_EMPTY``: at least one project directory exists and none resolved.
          Step 8a reports this and continues, matching the ``START_HERE_INSERT_FAILED``
          convention already in that step -- one dashboard sub-source must never abort a compose.
Exit 2 -- usage error: root missing, or it has no ``sessions/`` directory.
"""
from __future__ import annotations

import json
import os
import re
import sys

# ``**Repo:**`` / ``**Repository:**`` / ``Repo:`` / ``- **Repo:**``, anchored to line start so
# a mid-sentence prose mention cannot match, then the markdown link's github.com target.
REPO_RE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?\*{0,2}Repo(?:sitory)?:\*{0,2}[ \t]*"
    r"\[[^\]]*\]\([ \t]*https://github\.com/([^)\s]+?)[ \t]*\)",
    re.MULTILINE,
)

# The ``**Journal:**`` bullet, whose ``sessions/<name>/`` is the project *directory* name.
# Matched in two steps (line, then path) so both the label form
# ``[sessions/x/](sessions/x/README.md)`` and a relabelled ``[journal](sessions/x/README.md)``
# resolve -- the directory name is the load-bearing part, not the link text.
JOURNAL_LINE_RE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?\*{0,2}Journal:\*{0,2}.*$",
    re.MULTILINE,
)
SESSIONS_PATH_RE = re.compile(r"sessions/([^/\]\)\s]+)/")

# A GitHub ``owner/repo`` slug. Deliberately strict: this value reaches a command line.
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def normalize_slug(raw):
    """Trim a github.com URL tail down to ``owner/repo``, or return None if it is not one.

    Handles the ``.git`` suffix and a trailing slash. Returns None for anything that does not
    then match ``SLUG_RE`` -- including a deeper path such as ``owner/repo/issues/4``, which
    is exactly the shape a prose PR/issue link has. That distinction matters: several project
    READMEs open with a PR link, and ``sessions/cover-letter-runtime/README.md``'s first
    github.com link points at **career-playbook** -- a different repo entirely. Anything that
    is not a bare two-segment slug must be rejected rather than guessed at.
    """
    if not raw:
        return None
    slug = raw.strip().rstrip("/")
    if slug.endswith(".git"):
        slug = slug[: -len(".git")]
    return slug if SLUG_RE.match(slug) else None


def parse_root_readme(text):
    """Map project directory name -> repo slug from the root README's ``### `` sections.

    Returns ``(mapping, malformed)``. ``malformed`` lists
    ``(project, raw_slug)`` for sections that paired a Journal bullet with a repo link whose
    target is not a clean ``owner/repo`` -- surfaced as a skip reason rather than dropped, so a
    corrupted bullet is distinguishable from an absent one.
    """
    mapping = {}
    malformed = []
    # ``split`` on the heading keeps each section's body with the heading that owns it; the
    # leading element is the preamble above the first ``###`` and is correctly discarded.
    for section in re.split(r"^### ", text, flags=re.MULTILINE)[1:]:
        journal_line = JOURNAL_LINE_RE.search(section)
        if not journal_line:
            continue
        project_match = SESSIONS_PATH_RE.search(journal_line.group(0))
        if not project_match:
            continue
        project = project_match.group(1)
        repo_match = REPO_RE.search(section)
        if not repo_match:
            continue
        slug = normalize_slug(repo_match.group(1))
        if slug is None:
            malformed.append((project, repo_match.group(1).strip()))
            continue
        # First section wins, so a duplicate heading cannot silently override an earlier one.
        mapping.setdefault(project, slug)
    return mapping, malformed


def parse_project_readme(text):
    """Resolve a slug from one ``sessions/<project>/README.md``.

    Returns ``(slug, raw)``: ``slug`` is None when no repo line is present (``raw`` None) or
    when one is present but malformed (``raw`` is the offending target, for the skip reason).
    """
    match = REPO_RE.search(text)
    if not match:
        return None, None
    raw = match.group(1).strip()
    return normalize_slug(raw), raw


def _read_text(path):
    """Return ``(text, error)``; exactly one is non-None. Never raises."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, str(exc)
    try:
        # utf-8-sig so a BOM-prefixed README parses instead of failing its first regex.
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError as exc:
        return None, f"not valid UTF-8: {exc}"


def list_projects(sessions_dir):
    """Sorted names of the project directories under ``sessions/``. Never raises."""
    try:
        entries = os.listdir(sessions_dir)
    except OSError:
        return []
    return sorted(
        name for name in entries if os.path.isdir(os.path.join(sessions_dir, name))
    )


def build_map(root):
    """Resolve every project under ``<root>/sessions`` to a repo slug.

    Returns a result dict with ``resolved`` (project -> slug), ``query_order`` (distinct
    slugs, each with the first project that claimed it), ``skipped`` (project + reason), and
    ``root_readme_error`` (None unless the root README could not be read -- which is reported
    but not fatal, since the per-project fallback may still resolve some projects).

    ``query_order`` exists so the caller queries each *repo* once rather than each *project*
    once. Two projects legitimately share a slug today (``meta`` and ``dev-env`` both map to
    ``brownm09/dev-env``), and deduping here rather than at the call site keeps the behavior
    inside the tested unit.
    """
    sessions_dir = os.path.join(root, "sessions")
    projects = list_projects(sessions_dir)

    root_text, root_error = _read_text(os.path.join(root, "README.md"))
    if root_text is None:
        root_mapping, root_malformed = {}, []
    else:
        root_mapping, root_malformed = parse_root_readme(root_text)
    root_malformed_by_project = dict(root_malformed)

    resolved = {}
    query_order = []
    seen_slugs = set()
    skipped = []

    for project in projects:
        slug = root_mapping.get(project)
        if slug is None:
            # Fall back to the project's own README, and build the skip reason as we go so it
            # names what was actually tried rather than a generic "not found".
            if root_error is not None:
                root_reason = f"root README.md unreadable ({root_error})"
            elif project in root_malformed_by_project:
                root_reason = (
                    "root README.md section has a **Repo:** link whose target is not an "
                    f"owner/repo slug ({root_malformed_by_project[project]!r})"
                )
            else:
                root_reason = (
                    "no ### section in root README.md pairs a **Journal:** "
                    f"[sessions/{project}/ bullet with a **Repo:** link"
                )

            project_readme = os.path.join(sessions_dir, project, "README.md")
            text, error = _read_text(project_readme)
            if text is None:
                project_reason = f"sessions/{project}/README.md unreadable ({error})"
            else:
                slug, raw = parse_project_readme(text)
                if slug is None and raw is None:
                    project_reason = (
                        f"sessions/{project}/README.md has no Repo:/Repository: link line"
                    )
                elif slug is None:
                    project_reason = (
                        f"sessions/{project}/README.md Repo:/Repository: link target is not "
                        f"an owner/repo slug ({raw!r})"
                    )
                else:
                    project_reason = None

            if slug is None:
                skipped.append(
                    {"project": project, "reason": f"{root_reason}; {project_reason}"}
                )
                continue

        resolved[project] = slug
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            query_order.append({"slug": slug, "project": project})

    return {
        "resolved": resolved,
        "query_order": query_order,
        "skipped": skipped,
        "root_readme_error": root_error,
    }


def mapping_empty(result, project_count):
    """True when projects exist but none resolved -- the dev-env#1045 signature itself."""
    return project_count > 0 and not result["resolved"]


def format_report(result, project_count):
    """The stdout report, as a list of lines.

    dev-env has no runtime logger (see its ``## Observability`` section); for an on-demand
    utility script the report goes to stdout, and every skipped project is named with a reason
    so a future mapping drift is loud rather than an inexplicably empty dashboard block.
    """
    lines = [
        f"SOURCE3_RESOLVED={len(result['resolved'])}",
        f"SOURCE3_SKIPPED={len(result['skipped'])}",
    ]
    if result["root_readme_error"] is not None:
        lines.append(f"SOURCE3_ROOT_README_UNREADABLE={result['root_readme_error']}")
    for entry in result["skipped"]:
        lines.append(f"SOURCE3_SKIP {entry['project']} -- {entry['reason']}")
    if mapping_empty(result, project_count):
        lines.append(
            "SOURCE3_MAPPING_EMPTY -- "
            f"{project_count} project director"
            f"{'y' if project_count == 1 else 'ies'} found and none resolved to a repo slug. "
            "Source 3 will contribute nothing to the Start here block. This is the dev-env#1045 "
            "signature: check that the root README.md still pairs **Repo:** with **Journal:** "
            "[sessions/<project>/ bullets in each ### section."
        )
    return lines


def main(argv):
    args = argv[1:]
    root = None
    json_path = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--json":
            index += 1
            if index >= len(args):
                sys.stderr.write("[journal-project-repo-map] --json requires a path\n")
                return 2
            json_path = args[index]
        elif root is None:
            root = arg
        else:
            sys.stderr.write(f"[journal-project-repo-map] unexpected argument: {arg}\n")
            return 2
        index += 1

    if root is None:
        sys.stderr.write(
            "[journal-project-repo-map] usage: py -3 journal-project-repo-map.py "
            "<engineering-journal-root> [--json <outfile>]\n"
        )
        return 2
    sessions_dir = os.path.join(root, "sessions")
    if not os.path.isdir(sessions_dir):
        sys.stderr.write(
            f"[journal-project-repo-map] no sessions/ directory under {root!r} -- "
            "is that an engineering-journal checkout?\n"
        )
        return 2

    result = build_map(root)
    project_count = len(list_projects(sessions_dir))

    if json_path is not None:
        payload = {
            "resolved": result["resolved"],
            "query_order": result["query_order"],
            "skipped": result["skipped"],
        }
        try:
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            sys.stderr.write(
                f"[journal-project-repo-map] could not write {json_path!r}: {exc}\n"
            )
            return 2

    for line in format_report(result, project_count):
        print(line)

    return 1 if mapping_empty(result, project_count) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
