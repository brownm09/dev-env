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

A failed read exits 1. Exit 0 says nothing about whether anything was reported.
"""
import argparse
import json
import re
import subprocess
import sys

import _winsubp  # noqa: F401  -- suppress console windows on Windows

_REF = re.compile(
    r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)"
    r"|([\w.-]+/[\w.-]+)#(\d+)"
    r"|(?<![\w/#])#(\d+)"
)
_BLOCK = re.compile(
    r"### Acceptance criteria evidence\s+Issue: https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)"
    r"(.*?)<!-- acceptance-criteria:",
    re.S,
)
_NOT_VERIFIABLE_ROW = re.compile(r"^\|(.*)\|\s*not verifiable\s*\|\s*$", re.M)
_REF_SHAPE = r"(?:#\d+|/issues/\d+)"
_DEFERRED_LINE = re.compile(
    rf"\bdefer\w*\b(?!\s+from\b)[^.|]{{0,60}}?{_REF_SHAPE}|{_REF_SHAPE}[^.|]{{0,20}}\bdefer", re.I
)
FOLLOW_UP_FIELDS = """... on Issue {
  __typename number state stateReason closedAt
  repository { nameWithOwner }
  timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {
    nodes { ... on ClosedEvent { closer { ... on PullRequest { number body } } } }
  }
}
... on PullRequest { __typename }"""


def issue_refs(text, repo):
    """Return (repo, number) for each issue reference; a bare #N belongs to `repo`."""
    refs = []
    for m in _REF.finditer(text):
        if m.group(1):
            refs.append((m.group(1), int(m.group(2))))
        elif m.group(3):
            refs.append((m.group(3), int(m.group(4))))
        else:
            refs.append((repo, int(m.group(5))))
    return refs


def extract_deferrals(body, comments, repo):
    """Return {(repo, number)} for issues this PR's body or comments defer a criterion to.

    Two sources: the Evidence cell of a `not verifiable` row in an evidence block, and, for PRs
    older than the evidence table, a body line saying something is deferred to an issue.
    """
    found = set()
    for text in [body or ""] + list(comments):
        for block in _BLOCK.finditer(text or ""):
            own = (block.group(1), int(block.group(2)))
            for row in _NOT_VERIFIABLE_ROW.finditer(block.group(3)):
                evidence = re.split(r"(?<!\\)\|", row.group(1))[-1]
                found.update(r for r in issue_refs(evidence, repo) if r != own)
    for line in (body or "").splitlines():
        if not line.startswith("|") and _DEFERRED_LINE.search(line):
            found.update(issue_refs(line, repo))
    return found


def says_it_does_not_close(pr_body, number):
    """True if the body says, right before the reference, that the PR doesn't close the issue."""
    negated = rf"(?:n't|\bnot|\bnever)\s+(?:yet\s+)?(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:[\w.-]+/[\w.-]+)?#{number}\b"
    return re.search(negated, pr_body or "", re.I) is not None


def closure_finding(follow_up, merged_at):
    """Return why this follow-up's closure looks undone, or None if there's nothing to report."""
    if follow_up["state"] != "CLOSED" or follow_up["closedAt"] <= merged_at:
        return None
    if follow_up["stateReason"] != "COMPLETED":
        return f"closed as {follow_up['stateReason'].lower().replace('_', ' ')}"
    closers = [n.get("closer") or {} for n in follow_up["timelineItems"]["nodes"]]
    closer = closers[-1] if closers else {}
    if says_it_does_not_close(closer.get("body"), follow_up["number"]):
        return f"closed by PR {closer['number']}, whose body says it doesn't close this issue"
    return None


def gh_json(*args):
    result = subprocess.run(["gh", *args], capture_output=True)
    if result.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return json.loads(result.stdout.decode("utf-8"))


def fetch_follow_ups(refs):
    """Return {(repo, number): issue}. A reference to a PR, or to nothing, is left out."""
    by_repo = {}
    for repo, number in refs:
        by_repo.setdefault(repo, []).append(number)
    follow_ups = {}
    for repo, numbers in by_repo.items():
        owner, name = repo.split("/")
        for start in range(0, len(numbers), 40):
            selection = " ".join(
                f"n{n}: issueOrPullRequest(number: {n}) {{ {FOLLOW_UP_FIELDS} }}" for n in numbers[start:start + 40]
            )
            query = f'{{ r: repository(owner: "{owner}", name: "{name}") {{ {selection} }} }}'
            # A number that doesn't exist comes back as null plus a GraphQL error, which gh reports
            # as a failure, so read stdout before deciding.
            result = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"], capture_output=True)
            data = json.loads(result.stdout.decode("utf-8") or "{}").get("data") or {}
            if not data.get("r"):
                raise RuntimeError(f"gh api graphql for {repo}: {result.stderr.decode('utf-8', 'replace').strip()}")
            for key, node in data["r"].items():
                if node and node["__typename"] == "Issue":
                    follow_ups[(repo, int(key[1:]))] = node
    return follow_ups


def fetch_prs(args):
    """Return merged PRs as (repo, number, mergedAt, body, [comment bodies])."""
    fields = "number,body,mergedAt,comments"
    raw = []
    for spec in args.pr:
        repo, number = spec.split("#")
        raw.append((repo, gh_json("pr", "view", number, "--repo", repo, "--json", fields)))
    for repo in args.repo:
        listed = gh_json("pr", "list", "--repo", repo, "--state", "merged", "--limit", "1000", "--json", fields)
        raw.extend((repo, pr) for pr in listed)
    return [
        (repo, pr["number"], pr["mergedAt"], pr["body"], [c["body"] for c in pr["comments"]])
        for repo, pr in raw
        if pr["mergedAt"]
    ]


def sweep(prs, follow_ups, closed_since=""):
    """Return (findings, counts). Pure, so the fixtures can drive it."""
    findings, reported = [], set()
    deferrals = open_count = closed_after = 0
    for repo, number, merged_at, body, comments in prs:
        for ref in sorted(extract_deferrals(body, comments, repo)):
            follow_up = follow_ups.get(ref)
            if not follow_up:
                continue
            canonical = (follow_up["repository"]["nameWithOwner"], follow_up["number"])
            deferrals += 1
            if follow_up["state"] == "OPEN":
                open_count += 1
            elif follow_up["closedAt"] > merged_at and follow_up["closedAt"] >= closed_since:
                closed_after += 1
                why = closure_finding(follow_up, merged_at)
                if why and (repo, number, canonical) not in reported:
                    reported.add((repo, number, canonical))
                    findings.append(f"{repo} PR {number} -> {canonical[0]}#{canonical[1]}: {why}")
    counts = {"deferrals": deferrals, "open": open_count, "closed_after_merge": closed_after, "reported": len(findings)}
    return findings, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pr", action="append", default=[], help="owner/repo#N; repeatable")
    parser.add_argument("--repo", action="append", default=[], help="owner/repo, every merged PR; repeatable")
    parser.add_argument("--closed-since", default="", help="YYYY-MM-DD: only follow-ups closed on or after this date")
    args = parser.parse_args()
    if not args.pr and not args.repo:
        parser.error("give at least one --pr or --repo")
    try:
        prs = fetch_prs(args)
        refs = {ref for repo, _, _, body, comments in prs for ref in extract_deferrals(body, comments, repo)}
        follow_ups = fetch_follow_ups(refs)
    except (RuntimeError, ValueError) as error:
        print(f"TOOL ERROR: {error}", file=sys.stderr)
        return 1
    findings, counts = sweep(prs, follow_ups, args.closed_since)
    for finding in findings:
        print(finding)
    print(
        f"PRs read: {len(prs)}. Deferrals found: {counts['deferrals']}, open: {counts['open']}. "
        f"Closed after merge: {counts['closed_after_merge']}, reported: {counts['reported']}, "
        f"not judged: {counts['closed_after_merge'] - counts['reported']}."
    )
    if not counts["deferrals"]:
        print("WARNING: no deferral was found, so this run checked nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
