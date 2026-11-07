#!/usr/bin/env python3
"""Report deferred acceptance criteria whose follow-up issue closed after the PR merged, with a
sign that the deferred work wasn't done (dev-env#1292, ADR-153 -> "Follow-ups closed after merge").

/review Step 2g judges a follow-up as it stands at review time, so it can't see a later closure.
This reads merged PRs' deferrals and reports two closures:

- closed as not planned, or as a duplicate
- closed by a PR whose body says it doesn't close that issue (GitHub honors the keyword anyway)

It reports and never blocks. It finds about half of the silent drops in its calibration corpus,
so a closure that looks normal is counted as "not judged", not as delivered.

    py -3 deferred-followup-sweep.py --pr brownm09/switchyard#13
    py -3 deferred-followup-sweep.py --repo brownm09/dev-env --closed-since 2026-11-01

A failed read exits 1, after printing whatever the other repos returned. A reference to something
that doesn't exist isn't a failed read: it's counted as unresolved. Exit 0 says nothing about
whether anything was reported.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys

import _winsubp  # noqa: F401  -- suppress console windows on Windows

GH_TIMEOUT_SECONDS = 300
PR_LIST_LIMIT = 5000

_REF = re.compile(
    r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)"
    r"|(?<![\w./-])([\w.-]+/[\w.-]+)#(\d+)"
    r"|(?<![\w./-])([\w.-]+)#(\d+)"
    r"|(?<![\w/#\[.-])#(\d+)"
)
_BLOCK = re.compile(
    r"### Acceptance criteria evidence\s+Issue: https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)"
    r"(.*?)<!-- acceptance-criteria:",
    re.S,
)
_NOT_VERIFIABLE_ROW = re.compile(r"^\|(.*)\|\s*not verifiable\s*\|\s*$", re.M)
_DEFERRAL_WORD = re.compile(r"\bdefer\w*\b(?!\s+from\b)|\bfollow-?up|\btrack", re.I)
_REF_SHAPE = r"(?:#\d+|/issues/\d+)"
_DEFERRED_LINE = re.compile(
    rf"\bdefer\w*\b(?!\s+from\b)[^.|]{{0,60}}?{_REF_SHAPE}|[\w./:-]*{_REF_SHAPE}[^.|]{{0,20}}\bdefer", re.I
)
# Whitespace that may hold one line break, for a hard-wrapped sentence, but not a blank line.
_WRAP = r"[ \t]*(?:\r?\n)?[ \t]*"
_CLOSING_REF = re.compile(
    rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[*_]*:?[*_]*{_WRAP}\[?"
    r"(?:https://github\.com/(?P<url>[\w.-]+/[\w.-]+)/issues/(?P<url_n>\d+)|(?P<full>[\w.-]+/[\w.-]+)?#(?P<n>\d+))"
    r"(?:\]\(https://github\.com/(?P<link>[\w.-]+/[\w.-]+)/issues/\d+\))?",
    re.I,
)
_NEGATED_BEFORE = re.compile(
    rf"(?:n['’]t|\bnot\b|\bnever\b|\bcannot\b)[*_`]*(?:{_WRAP}(?:yet|(?!only\b)\w+ly)\b)*{_WRAP}[*_`]*\Z", re.I
)
FOLLOW_UP_FIELDS = """... on Issue {
  __typename number state stateReason(enableDuplicate: true) closedAt
  repository { nameWithOwner }
  timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {
    nodes { ... on ClosedEvent { closer { ... on PullRequest { number body } } } }
  }
}
... on PullRequest { __typename }"""


def issue_refs(text, repo):
    """Return (repo, number) for each issue reference. A bare #N belongs to `repo`, and name#N to
    `repo`'s owner."""
    refs = []
    for m in _REF.finditer(text):
        if m.group(1):
            refs.append((m.group(1), int(m.group(2))))
        elif m.group(3):
            refs.append((m.group(3), int(m.group(4))))
        elif m.group(5):
            refs.append((f"{repo.split('/')[0]}/{m.group(5)}", int(m.group(6))))
        else:
            refs.append((repo, int(m.group(7))))
    return refs


def _same_repo(a, b):
    """Compare by name only, because an owner can change: brownm09/lifting-logbook redirects."""
    return a.split("/")[-1].lower() == b.split("/")[-1].lower()


def _closing_mentions(body, repo):
    """Yield (repo, number, negated) for each closing keyword with a reference after it. A table
    row is skipped, because "fixed: #12 is amended" there records a review finding."""
    for m in _CLOSING_REF.finditer(body):
        if body[body.rfind("\n", 0, m.start()) + 1:m.start()].startswith("|"):
            continue
        target = m.group("url") or m.group("full") or m.group("link") or repo
        number = int(m.group("url_n") or m.group("n"))
        yield target, number, _NEGATED_BEFORE.search(body[max(0, m.start() - 80):m.start()]) is not None


def _evidence_deferrals(body, comments, repo):
    """Return refs from `not verifiable` Evidence cells that defer, in each issue's newest block."""
    newest = {}
    for text in [body or ""] + list(comments):
        for block in _BLOCK.finditer(text or ""):
            newest[(block.group(1), int(block.group(2)))] = block.group(3)
    found = set()
    for own, rows in newest.items():
        for row in _NOT_VERIFIABLE_ROW.finditer(rows):
            evidence = re.split(r"(?<!\\)\|", row.group(1))[-1]
            if _DEFERRAL_WORD.search(evidence):
                found.update(r for r in issue_refs(evidence, repo) if r != own)
    return found


def _prose_deferrals(body, repo):
    """Return refs from body lines saying something is deferred, for PRs older than the table.
    An issue this PR closes, un-negated, isn't a follow-up."""
    body = body or ""
    closes = [(r, n) for r, n, negated in _closing_mentions(body, repo) if not negated]
    found = set()
    for line in body.splitlines():
        if line.startswith("|"):
            continue
        text = _CLOSING_REF.sub(" ", line)
        m = _DEFERRED_LINE.search(text)
        if m:
            sentence_rest = re.split(r"\.\s|\|", text[m.end():])[0]
            found.update(issue_refs(m.group(0) + sentence_rest, repo))
    return {ref for ref in found if not any(ref[1] == n and _same_repo(ref[0], r) for r, n in closes)}


def extract_deferrals(body, comments, repo):
    """Return {(repo, number)} for issues this PR's body or comments defer a criterion to."""
    return _evidence_deferrals(body, comments, repo) | _prose_deferrals(body, repo)


def says_it_does_not_close(pr_body, number, repo):
    """True if every closing keyword naming this issue is negated, and there is at least one."""
    mentions = [
        negated
        for target, n, negated in _closing_mentions(pr_body or "", repo)
        if n == number and _same_repo(target, repo)
    ]
    return bool(mentions) and all(mentions)


def closure_finding(follow_up):
    """Return why this closed follow-up looks undone, or None if there's nothing to report."""
    reason = follow_up["stateReason"]
    if reason in ("NOT_PLANNED", "DUPLICATE"):
        return f"closed as {reason.lower().replace('_', ' ')}"
    nodes = follow_up["timelineItems"]["nodes"]
    closer = (nodes[0].get("closer") or {}) if nodes else {}
    if says_it_does_not_close(closer.get("body"), follow_up["number"], follow_up["repository"]["nameWithOwner"]):
        return f"closed by PR {closer['number']}, whose body says it doesn't close this issue"
    return None


def run_gh(*args):
    try:
        return subprocess.run(["gh", *args], capture_output=True, timeout=GH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"gh {' '.join(args[:3])}: timed out after {GH_TIMEOUT_SECONDS}s") from error
    except FileNotFoundError as error:
        raise RuntimeError("gh isn't installed or isn't on PATH") from error


def gh_json(*args):
    result = run_gh(*args)
    if result.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return json.loads(result.stdout.decode("utf-8"))


def _fetch_batch(repo, numbers):
    """Return ({number: issue}, unresolved count). A reference to a PR is left out of both."""
    owner, name = repo.split("/")
    selection = " ".join(f"n{n}: issueOrPullRequest(number: {n}) {{ {FOLLOW_UP_FIELDS} }}" for n in numbers)
    query = f'{{ r: repository(owner: "{owner}", name: "{name}") {{ {selection} }} }}'
    # A number or repo that doesn't exist comes back as null plus a NOT_FOUND error, which gh
    # reports as a failure, so read stdout before deciding. Any other error is a failed read.
    result = run_gh("api", "graphql", "-f", f"query={query}")
    payload = json.loads(result.stdout.decode("utf-8") or "{}")
    other_errors = [e for e in payload.get("errors", []) if e.get("type") != "NOT_FOUND"]
    if other_errors or "data" not in payload:
        detail = other_errors[0].get("message") if other_errors else result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"gh api graphql for {repo}: {detail}")
    nodes = (payload["data"] or {}).get("r") or {}
    issues, unresolved = {}, 0
    for number in numbers:
        node = nodes.get(f"n{number}")
        if node is None:
            unresolved += 1
        elif node["__typename"] == "Issue":
            issues[number] = node
    return issues, unresolved


def fetch_follow_ups(refs, failures):
    """Return ({(repo, number): issue}, unresolved count). Records a repo's failed read in `failures`."""
    by_repo = {}
    for repo, number in refs:
        by_repo.setdefault(repo, []).append(number)
    follow_ups, unresolved = {}, 0
    for repo, numbers in by_repo.items():
        try:
            for start in range(0, len(numbers), 40):
                issues, missing = _fetch_batch(repo, numbers[start:start + 40])
                unresolved += missing
                follow_ups.update({(repo, number): issue for number, issue in issues.items()})
        except (RuntimeError, ValueError) as error:
            failures[repo] = str(error)
    return follow_ups, unresolved


def fetch_prs(args, failures):
    """Return merged PRs as (repo, number, mergedAt, body, [comment bodies]), each PR once.
    Records a repo's failed read in `failures`."""
    fields = "number,body,mergedAt,comments"
    raw = []
    for spec in args.pr:
        try:
            repo, number = spec.split("#")
            raw.append((repo, gh_json("pr", "view", number, "--repo", repo, "--json", fields)))
        except (RuntimeError, ValueError) as error:
            failures[spec] = str(error)
    for repo in args.repo:
        try:
            listed = gh_json(
                "pr", "list", "--repo", repo, "--state", "merged", "--limit", str(PR_LIST_LIMIT), "--json", fields
            )
            if len(listed) >= PR_LIST_LIMIT:
                raise RuntimeError(f"read the {PR_LIST_LIMIT} PR limit, so older PRs were dropped; raise PR_LIST_LIMIT")
            raw.extend((repo, pr) for pr in listed)
        except (RuntimeError, ValueError) as error:
            failures[repo] = str(error)
    merged = {
        (repo, pr["number"]): (repo, pr["number"], pr["mergedAt"], pr["body"], [c["body"] for c in pr["comments"]])
        for repo, pr in raw
        if pr["mergedAt"]
    }
    return list(merged.values())


def sweep(prs, follow_ups, closed_since=""):
    """Return (findings, counts), counting each PR's follow-up once. Pure, so the fixtures can drive it."""
    findings, seen = [], set()
    deferrals = from_evidence = open_count = closed_after = 0
    for repo, number, merged_at, body, comments in prs:
        evidence = _evidence_deferrals(body, comments, repo)
        for ref in sorted(extract_deferrals(body, comments, repo)):
            follow_up = follow_ups.get(ref)
            if not follow_up:
                continue
            canonical = (follow_up["repository"]["nameWithOwner"], follow_up["number"])
            if (repo, number, canonical) in seen:
                continue
            seen.add((repo, number, canonical))
            deferrals += 1
            from_evidence += ref in evidence
            if follow_up["state"] == "OPEN":
                open_count += 1
            elif follow_up["closedAt"] > merged_at and follow_up["closedAt"] >= closed_since:
                closed_after += 1
                why = closure_finding(follow_up)
                if why:
                    findings.append(f"{repo} PR {number} -> {canonical[0]}#{canonical[1]}: {why}")
    counts = {
        "deferrals": deferrals,
        "from_evidence": from_evidence,
        "open": open_count,
        "closed_after_merge": closed_after,
        "reported": len(findings),
    }
    return findings, counts


def iso_date(value):
    """Reject anything but YYYY-MM-DD, because --closed-since is compared as a string."""
    datetime.datetime.strptime(value, "%Y-%m-%d")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pr", action="append", default=[], help="owner/repo#N; repeatable")
    parser.add_argument("--repo", action="append", default=[], help="owner/repo, every merged PR; repeatable")
    parser.add_argument("--closed-since", type=iso_date, help="YYYY-MM-DD: only follow-ups closed on or after this date")
    args = parser.parse_args()
    if not args.pr and not args.repo:
        parser.error("give at least one --pr or --repo")
    failures = {}
    prs = fetch_prs(args, failures)
    refs = {ref for repo, _, _, body, comments in prs for ref in extract_deferrals(body, comments, repo)}
    follow_ups, unresolved = fetch_follow_ups(refs, failures)
    findings, counts = sweep(prs, follow_ups, args.closed_since or "")
    for finding in findings:
        print(finding)
    print(
        f"PRs read: {len(prs)}. Deferrals found: {counts['deferrals']} "
        f"({counts['from_evidence']} from evidence tables), open: {counts['open']}. "
        f"Closed after merge: {counts['closed_after_merge']}, reported: {counts['reported']}, "
        f"not judged: {counts['closed_after_merge'] - counts['reported']}. Unresolved references: {unresolved}."
    )
    for where, error in failures.items():
        print(f"TOOL ERROR: {where}: {error}", file=sys.stderr)
    if failures:
        return 1
    if not counts["deferrals"]:
        print("WARNING: no deferral was found, so this run checked nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
