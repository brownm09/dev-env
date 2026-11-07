"""The one definition of a negated closing keyword in a PR body (dev-env#1300).

GitHub honors a closing keyword wherever it appears, so "It doesn't close #12" closes #12 on merge.
See GitHub's list of keywords:
https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue

The negation has to sit right before the keyword and the keyword right before the reference. So
"doesn't block closing #388" and "not blocked" followed by a `Closes` line don't match. Markdown
emphasis, a colon and an adverb are allowed between them, as in "does **not** close #12",
"not fixed: #12" and "doesn't fully resolve #12".
"""
import re

# Whitespace that may hold one line break, for a hard-wrapped sentence, but not a blank line.
_WRAP = r"[ \t]*(?:\r?\n)?[ \t]*"
CLOSING_REF = re.compile(
    rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[*_]*:?[*_]*{_WRAP}\[?"
    r"(?:https://github\.com/(?P<url>[\w.-]+/[\w.-]+)/issues/(?P<url_n>\d+)|(?P<full>[\w.-]+/[\w.-]+)?#(?P<n>\d+))"
    r"(?:\]\(https://github\.com/(?P<link>[\w.-]+/[\w.-]+)/issues/\d+\))?",
    re.I,
)
_NEGATED_BEFORE = re.compile(
    rf"(?:n['’]t|\bnot\b|\bnever\b|\bcannot\b)[*_`]*(?:{_WRAP}(?:yet|(?!only\b)\w+ly)\b)*{_WRAP}[*_`]*\Z", re.I
)
# Forms GitHub doesn't link, seen over the corpus in ADR-153: code, and emphasis closing after a
# keyword's colon, as in "fixed:** #12" (unlinked in 2 of 2).
_UNLINKED = re.compile(r"```.*?```|`[^`\n]*`|\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):[*_]+", re.I | re.S)


def same_repo(a, b):
    """Compare by name only, because an owner can change: brownm09/lifting-logbook redirects."""
    return a.split("/")[-1].lower() == b.split("/")[-1].lower()


def closing_mentions(body, repo):
    """Yield (repo, number, negated) for each closing keyword with a reference after it. A table
    row is skipped, because "fixed: #12 is amended" there records a review finding."""
    for m in CLOSING_REF.finditer(body):
        if body[body.rfind("\n", 0, m.start()) + 1:m.start()].startswith("|"):
            continue
        target = m.group("url") or m.group("full") or m.group("link") or repo
        number = int(m.group("url_n") or m.group("n"))
        yield target, number, _NEGATED_BEFORE.search(body[max(0, m.start() - 80):m.start()]) is not None


def says_it_does_not_close(pr_body, number, repo):
    """True if every closing keyword naming this issue is negated, and there is at least one."""
    mentions = [
        negated
        for target, n, negated in closing_mentions(pr_body or "", repo)
        if n == number and same_repo(target, repo)
    ]
    return bool(mentions) and all(mentions)


def accidental_closes(pr_body, repo):
    """Return references, such as "#12" or "o/r#12", that merging would close although the body says
    it won't: every mention GitHub would link is negated."""
    negated_by_ref = {}
    targets = {}
    for target, number, negated in closing_mentions(_UNLINKED.sub(" ", pr_body or ""), repo):
        key = (target.split("/")[-1].lower(), number)
        targets.setdefault(key, target)
        negated_by_ref[key] = negated_by_ref.get(key, True) and negated
    return [
        f"#{key[1]}" if same_repo(targets[key], repo) else f"{targets[key]}#{key[1]}"
        for key, every_mention_negated in negated_by_ref.items()
        if every_mention_negated
    ]
