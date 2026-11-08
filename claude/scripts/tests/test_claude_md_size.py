#!/usr/bin/env python3
"""claude/CLAUDE.md loads in every session in every project, so its size is capped, and its links
are checked because rules now lean on them for their detail (dev-env#1216, ADR-159). The
repo-root CLAUDE.md, dev-env's project file, loads in every dev-env session and gets the same
checks with its own ceiling (dev-env#1297).

Run: py -3 claude/scripts/tests/test_claude_md_size.py
"""
import functools
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CLAUDE_MD = os.path.join(REPO, "claude", "CLAUDE.md")
REFERENCE = os.path.join(REPO, "docs", "REFERENCE.md")

# One entry per capped instruction file. Each ceiling is 4-5% above the file's size after its
# trim, and each known-bad size is the file before that trim: when the issue was filed, and when
# the trim landed.
CAPPED_FILES = (
    {
        "name": "claude/CLAUDE.md",
        "path": CLAUDE_MD,
        "ceiling": 96_500,  # trimmed to 91,862 bytes by #1216
        "known_bad": (166_696, 181_551),
        "min_links": 50,
        "detail_section": "instruction-detail",
        "detail_prefix": "detail-",
        "detail_link": "../docs/REFERENCE.md#",
        "min_details": 20,
        "how_to_fix": (
            "Write the new rule as a line or two and move its story to docs/REFERENCE.md -> "
            "Instruction Detail. Raise its ceiling only with a reason in the PR body (ADR-159)."
        ),
    },
    {
        "name": "CLAUDE.md",
        "path": os.path.join(REPO, "CLAUDE.md"),
        "ceiling": 38_500,  # trimmed to 36,860 bytes by #1297
        "known_bad": (52_943, 56_572),
        "min_links": 20,
        "detail_section": "project-instruction-detail",
        "detail_prefix": "project-detail-",
        "detail_link": "docs/REFERENCE.md#",
        "min_details": 1,
        "how_to_fix": (
            "Write the new rule as a line or two and move its story to docs/REFERENCE.md -> "
            "Project Instruction Detail, or a Testing item's detail to docs/TESTING.md. Raise its "
            "ceiling only with a reason in the PR body (ADR-159)."
        ),
    },
)

# A link's target, optional anchor, and optional quoted title.
LINK = re.compile(r"\]\((?!https?:)([^)#\s]*)(?:#([^)\s]*))?(?:\s+\"[^\"]*\")?\)")


def size_of(path):
    """Bytes with line endings normalized, so a CRLF checkout measures the same as LF."""
    with open(path, "rb") as f:
        return len(f.read().replace(b"\r\n", b"\n"))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def over_ceiling(size, ceiling):
    return size > ceiling


def strip_fences(text):
    """Lines inside fenced blocks aren't headings or links."""
    out, fence = [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            fence = not fence
            continue
        if not fence:
            out.append(line)
    return "\n".join(out)


def strip_code(text):
    """Nor is anything in inline code a link."""
    return re.sub(r"`[^`\n]*`", "`", strip_fences(text))


def anchor_of(heading):
    """GitHub's heading anchor: lower-case, drop punctuation, spaces become hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors_in(text):
    """Every heading's anchor. GitHub appends -1, -2 to repeats of the same one."""
    seen, anchors = {}, set()
    for m in re.finditer(r"^#{1,6} (.+)$", strip_fences(text), re.M):
        anchor = anchor_of(m.group(1))
        count = seen.get(anchor, 0)
        seen[anchor] = count + 1
        anchors.add(f"{anchor}-{count}" if count else anchor)
    return anchors


@functools.lru_cache(maxsize=None)
def anchors_in_file(path):
    return anchors_in(read(path))


def exists_with_exact_case(path):
    """os.path.exists ignores case on Windows, and a link with the wrong case breaks on GitHub."""
    return os.path.exists(path) and os.path.basename(path) in os.listdir(os.path.dirname(path))


def broken_links(text, base_dir):
    """Local links in text whose file is missing, or whose anchor isn't a heading in that file."""
    broken, found = [], 0
    for m in LINK.finditer(strip_code(text)):
        target, anchor = m.group(1), m.group(2)
        if target == "url":
            continue  # the placeholder in the tile table's template row, [#N](url)
        found += 1
        if not target:
            if anchor and anchor not in anchors_in(text):
                broken.append(f"#{anchor}")
            continue
        path = os.path.normpath(os.path.join(base_dir, target))
        if not exists_with_exact_case(path):
            broken.append(target)
        elif anchor and path.endswith(".md") and anchor not in anchors_in_file(path):
            broken.append(f"{target}#{anchor}")
    return found, broken


def test_each_file_is_under_its_ceiling():
    for f in CAPPED_FILES:
        size = size_of(f["path"])
        assert not over_ceiling(size, f["ceiling"]), (
            f"{f['name']} is {size} bytes, over the {f['ceiling']} ceiling. {f['how_to_fix']}"
        )


def test_each_old_file_would_have_failed():
    for f in CAPPED_FILES:
        for size in f["known_bad"]:
            assert over_ceiling(size, f["ceiling"]), f"{f['name']}: the ceiling must reject the pre-trim size {size}"


def test_each_measurement_is_not_vacuous():
    for f in CAPPED_FILES:
        assert size_of(f["path"]) > f["ceiling"] // 2, f"{f['name']} read back far too small to be the real file"


def test_every_local_link_resolves():
    for f in CAPPED_FILES:
        found, broken = broken_links(read(f["path"]), os.path.dirname(f["path"]))
        assert found > f["min_links"], (
            f"{f['name']}: only {found} local links extracted; the extraction is broken, a vacuous pass"
        )
        assert not broken, f"broken links in {f['name']}: {broken}"


def test_detail_links_exist_and_reach_the_detail_section():
    text = read(CLAUDE_MD)
    anchors = anchors_in_file(REFERENCE)
    details = re.findall(r"\(\.\./docs/REFERENCE\.md#(detail-[\w-]+)\)", text)
    assert len(details) > 20, f"only {len(details)} detail links found; the extraction is broken"
    missing = sorted({d for d in details if d not in anchors})
    assert not missing, f"detail headings missing from docs/REFERENCE.md: {missing}"


def test_every_detail_heading_is_linked():
    anchors = anchors_in_file(REFERENCE)
    for f in CAPPED_FILES:
        assert f["detail_section"] in anchors, f"docs/REFERENCE.md has no {f['detail_section']} section"
        headings = {a for a in anchors if a.startswith(f["detail_prefix"])}
        assert len(headings) >= f["min_details"], (
            f"only {len(headings)} {f['detail_prefix']} headings found: the extraction is broken, or "
            f"detail sections were removed and min_details for {f['name']} needs lowering"
        )
        text = read(f["path"])
        unlinked = sorted(h for h in headings if f"({f['detail_link']}{h})" not in text)
        assert not unlinked, f"detail sections nothing in {f['name']} points to: {unlinked}"


def test_the_detail_prefixes_do_not_overlap():
    prefixes = [f["detail_prefix"] for f in CAPPED_FILES]
    for a in prefixes:
        for b in prefixes:
            assert a == b or not b.startswith(a), f"{b} headings would also count as {a} headings"


def test_the_link_check_catches_a_broken_link():
    base = os.path.dirname(CLAUDE_MD)
    good = '[a](CLAUDE.md) [b](../docs/REFERENCE.md#instruction-detail) [c](CLAUDE.md "a title")'
    assert broken_links(good, base) == (3, [])
    assert broken_links("[a](no-such-file.md)", base)[1] == ["no-such-file.md"]
    assert broken_links('[a](no-such-file.md "a title")', base)[1] == ["no-such-file.md"]
    assert broken_links("[a](README)", base)[1] == ["README"]
    assert broken_links("[a](claude.MD)", base)[1] == ["claude.MD"]
    assert broken_links("[a](../docs/REFERENCE.md#no-such-heading)", base)[1] == ["../docs/REFERENCE.md#no-such-heading"]


def test_same_file_anchors_are_checked():
    base = os.path.dirname(CLAUDE_MD)
    assert broken_links("## Real\n[a](#real)\n", base) == (1, [])
    assert broken_links("## Real\n[a](#nope)\n", base) == (1, ["#nope"])


def test_the_template_placeholder_is_skipped():
    assert broken_links("[#N](url)", os.path.dirname(CLAUDE_MD)) == (0, [])


def test_code_is_not_read_as_links_or_headings():
    text = "`[a](missing.md)`\n```\n## Not a heading\n[b](missing.md)\n```\n## Real\n"
    assert broken_links(text, os.path.dirname(CLAUDE_MD)) == (0, [])
    assert anchors_in(text) == {"real"}


def test_anchor_matches_github_form():
    assert anchor_of("Disk-Full (ENOSPC) Recovery") == "disk-full-enospc-recovery"
    assert anchor_of("Detail: CI not firing") == "detail-ci-not-firing"
    assert anchor_of("Tile shards (`sessions/<project>/tiles/<issue-number>.json`)") == "tile-shards-sessionsprojecttilesissue-numberjson"
    assert anchors_in("## Same\n## Same\n") == {"same", "same-1"}


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
