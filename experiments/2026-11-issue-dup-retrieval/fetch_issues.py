"""Fetch every issue (title, body, dates, state reason, comments, timeline dup events) for a repo into
a local JSON file, via paginated GraphQL. Output stays on this machine."""
import json
import subprocess
import sys

QUERY = """
query($owner:String!,$name:String!,$cursor:String){
  repository(owner:$owner,name:$name){
    issues(first:100,after:$cursor,orderBy:{field:CREATED_AT,direction:ASC}){
      pageInfo{hasNextPage endCursor}
      nodes{
        number title body createdAt closedAt state stateReason lastEditedAt
        comments(first:50){nodes{body createdAt}}
        timelineItems(first:20,itemTypes:[MARKED_AS_DUPLICATE_EVENT]){
          nodes{... on MarkedAsDuplicateEvent{canonical{... on Issue{number}} duplicate{... on Issue{number}}}}
        }
      }
    }
  }
}
"""


def gh_graphql(owner, name, cursor):
    args = ["gh", "api", "graphql", "-f", "query=" + QUERY, "-F", f"owner={owner}", "-F", f"name={name}"]
    if cursor:
        args += ["-F", f"cursor={cursor}"]
    out = subprocess.run(args, capture_output=True)
    if out.returncode != 0:
        raise SystemExit(out.stderr.decode("utf-8", "replace"))
    return json.loads(out.stdout.decode("utf-8"))


def main(repo, outdir):
    owner, name = repo.split("/")
    cursor, issues = None, []
    while True:
        d = gh_graphql(owner, name, cursor)
        page = d["data"]["repository"]["issues"]
        issues += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    path = f"{outdir}/issues_{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False)
    print(repo, len(issues), path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
