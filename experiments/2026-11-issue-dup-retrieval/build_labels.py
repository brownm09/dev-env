"""Build labels.json (numbers only) from declared relationships in the local issue cache.

Usage: py -3 build_labels.py <issue-cache-dir> <out-path>

Every label below was read off a recorded declaration (a closing comment, a body note, or the
global CLAUDE.md). None was chosen by textual similarity, and no retrieval had run when they were
fixed. Direction follows filing order: the later issue is the query.
"""
import json
import random
import re
import sys

# Stratum A: a duplicate was actually filed. (repo, earlier, later)
STRATUM_A = [
    ("career-playbook", 267, 268),
    ("career-playbook", 699, 709),
    ("career-playbook", 1064, 1247),
    ("career-playbook", 1111, 1114),
    ("career-playbook", 928, 930),
    ("career-playbook", 1428, 1731),
    ("career-playbook", 1500, 1505),
    ("career-playbook", 1520, 1523),
    ("career-playbook", 1575, 1590),
    ("career-playbook", 1575, 1594),
    ("career-playbook", 1575, 1608),
    ("career-playbook", 1502, 1596),
    ("career-playbook", 1466, 1702),
    ("career-playbook", 1767, 1768),
    ("career-playbook", 1745, 1769),
    ("career-playbook", 1997, 2000),
    ("career-playbook", 1997, 2014),
    ("dev-env", 488, 495),
    ("dev-env", 844, 862),
    ("dev-env", 856, 899),
    ("dev-env", 610, 627),
    ("dev-env", 734, 918),
    ("lifting-logbook", 275, 276),
    ("lifting-logbook", 480, 768),
    ("lifting-logbook", 483, 612),
    ("lifting-logbook", 627, 638),
    ("lifting-logbook", 789, 798),
    ("lifting-logbook", 798, 803),
    ("lifting-logbook", 852, 854),
    ("lifting-logbook", 996, 1080),
]

# Stratum B: a session had a finding, recorded it on the existing issue, and said it was not filing.
# The incumbent is presumed to work on these. The comment text stands in for the draft issue.
# (repo, host issue, date of the first such comment)
STRATUM_B = [
    ("dev-env", 903, "2026-08-17"),
    ("dev-env", 945, "2026-08-18"),
    ("dev-env", 1029, "2026-08-20"),
    ("dev-env", 1086, "2026-09-15"),
    ("career-playbook", 724, "2026-07-14"),
    ("career-playbook", 839, "2026-07-30"),
    ("career-playbook", 842, "2026-08-28"),
    ("career-playbook", 916, "2026-08-28"),
    ("career-playbook", 1471, "2026-09-04"),
    ("career-playbook", 1575, "2026-09-14"),
    ("career-playbook", 1659, "2026-09-21"),
    ("career-playbook", 1681, "2026-09-24"),
    ("lifting-logbook", 899, "2026-08-18"),
]
_NOT_FILING = re.compile(
    r"not filing|no new issue|rather than fil|instead of fil|rather than opening|filing (separately|fresh)|near-duplicate",
    re.I,
)

# Declared non-duplicates: an author looked and said these are distinct. (repo, partner, issue)
KNOWN_NEGATIVE = [
    ("dev-env", 302, 474),
    ("dev-env", 350, 1087),
    ("dev-env", 694, 697),
    ("dev-env", 906, 976),
    ("dev-env", 615, 684),
    ("dev-env", 924, 956),
    ("dev-env", 952, 955),
    ("career-playbook", 832, 1160),
    ("career-playbook", 847, 1706),
    ("career-playbook", 847, 1693),
    ("career-playbook", 1597, 1601),
    ("career-playbook", 1064, 1068),
    ("career-playbook", 664, 1018),
    ("career-playbook", 1471, 1494),
    ("career-playbook", 1131, 1361),
    ("career-playbook", 1797, 1812),
    ("career-playbook", 1134, 1139),
    ("career-playbook", 904, 925),
    ("career-playbook", 656, 1708),
    ("lifting-logbook", 869, 871),
    ("lifting-logbook", 739, 740),
    ("lifting-logbook", 927, 928),
    ("cover-letter-runtime", 140, 184),
]

# Reported only, never in the verdict: a backlog audit's near-duplicates (dev-env#847), a weaker
# label than a closing declaration.
AUDITOR_NEAR_DUP = [("dev-env", 424, 785), ("dev-env", 566, 571), ("dev-env", 594, 609), ("dev-env", 510, 514)]
# Reported only, vector arm only: gh issue list searches one repo, so there is no baseline.
CROSS_REPO = [
    {"a": ["dev-env", 527], "b": ["lifting-logbook", 638]},
    {"a": ["dev-env", 876], "b": ["career-playbook", 1065]},
]
EASY_NEGATIVE_SEED = 20261106
EASY_NEGATIVE_PER_REPO = 40


def main(cache_dir, out_path):
    repos = {}
    for repo in ["dev-env", "career-playbook", "lifting-logbook", "cover-letter-runtime"]:
        with open(f"{cache_dir}/issues_{repo}.json", encoding="utf-8") as f:
            repos[repo] = {i["number"]: i for i in json.load(f)}

    def created(repo, number):
        return repos[repo][number]["createdAt"]

    earlier_members = {}
    inputs = []
    for repo, earlier, later in STRATUM_A:
        assert created(repo, earlier) < created(repo, later), (repo, earlier, later)
        earlier_members.setdefault((repo, later), set()).add(earlier)
    # A hit on any earlier member of the same family counts.
    changed = True
    while changed:
        changed = False
        for (repo, later), members in earlier_members.items():
            for other in list(members):
                for second in earlier_members.get((repo, other), set()):
                    if second not in members:
                        members.add(second)
                        changed = True
            family = {m for m in members}
            for (repo2, later2), members2 in earlier_members.items():
                if repo2 == repo and later2 != later and members2 & family:
                    if created(repo, later2) < created(repo, later) and later2 not in members:
                        members.add(later2)
                        changed = True
    for (repo, later), members in sorted(earlier_members.items()):
        inputs.append(
            {
                "id": f"A:{repo}#{later}",
                "stratum": "A",
                "repo": repo,
                "query_issue": later,
                "created_before": created(repo, later),
                "targets": sorted(members),
            }
        )
    for repo, host, date in STRATUM_B:
        comments = [
            c
            for c in repos[repo][host]["comments"]["nodes"]
            if c["createdAt"].startswith(date) and _NOT_FILING.search(c["body"] or "")
        ]
        assert comments, (repo, host, date)
        comment = min(comments, key=lambda c: c["createdAt"])
        assert created(repo, host) < comment["createdAt"]
        # A hit on another issue in the host's duplicate family counts too, if it existed by then.
        targets = {host}
        for (repo2, later), members in earlier_members.items():
            if repo2 == repo and host in members | {later}:
                targets |= {n for n in members | {later} if created(repo, n) < comment["createdAt"]}
        inputs.append(
            {
                "id": f"B:{repo}#{host}",
                "stratum": "B",
                "repo": repo,
                "host_issue": host,
                "comment_created_at": comment["createdAt"],
                "created_before": comment["createdAt"],
                "targets": sorted(targets),
            }
        )

    for repo, partner, issue in KNOWN_NEGATIVE:
        assert partner in repos[repo] and issue in repos[repo], (repo, partner, issue)
    for repo, a, b in AUDITOR_NEAR_DUP:
        assert a in repos[repo] and b in repos[repo], (repo, a, b)

    labelled = {(i["repo"], n) for i in inputs for n in i["targets"]}
    labelled |= {(i["repo"], i.get("query_issue")) for i in inputs}
    labelled |= {(r, n) for r, a, b in KNOWN_NEGATIVE + AUDITOR_NEAR_DUP for n in (a, b)}
    rng = random.Random(EASY_NEGATIVE_SEED)
    easy = []
    for repo in ["dev-env", "career-playbook", "lifting-logbook"]:
        pool = sorted(n for n in repos[repo] if (repo, n) not in labelled)
        taken = 0
        while taken < EASY_NEGATIVE_PER_REPO:
            a, b = sorted(rng.sample(pool, 2))
            text_a = (repos[repo][a]["body"] or "") + (repos[repo][a]["title"] or "")
            text_b = (repos[repo][b]["body"] or "") + (repos[repo][b]["title"] or "")
            if f"#{a}" in text_b or f"#{b}" in text_a or f"/{a}" in text_b or f"/{b}" in text_a:
                continue
            easy.append([repo, a, b])
            taken += 1

    labels = {
        "inputs": sorted(inputs, key=lambda i: i["id"]),
        "known_negative": [list(x) for x in KNOWN_NEGATIVE],
        "easy_negative": easy,
        "auditor_near_dup": [list(x) for x in AUDITOR_NEAR_DUP],
        "cross_repo": CROSS_REPO,
    }
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(labels, f, indent=1)
        f.write("\n")
    a = sum(i["stratum"] == "A" for i in inputs)
    print(f"stratum A={a} stratum B={len(inputs) - a} negatives={len(KNOWN_NEGATIVE)} easy={len(easy)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
