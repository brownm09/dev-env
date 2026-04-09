---
name: research
description: Find 1–3 primary sources for a decision or topic. Greps the shared source library first (zero cost), spawns a subagent only on a cache miss. Emits footnote-ready markdown. Invoke as /research [tag:] <decision description>.
argument-hint: "[<tag>:] <decision description>"
allowed-tools: Grep Read Agent Write Edit
---

You are finding primary sources for an engineering decision or topic.
Produce footnote-ready markdown that can be pasted directly into any document.

## Step 1 — Parse $ARGUMENTS

`$ARGUMENTS` may take one of two forms:

- **With tag prefix:** `architecture: chose hexagonal over layered for service boundary`
- **Without tag prefix:** `chose hexagonal over layered for service boundary`

Parse rule: if `$ARGUMENTS` matches the pattern `<word>: <rest>` where the prefix is a
single word or hyphenated word, treat the prefix as a **topic tag** and the rest as the
**decision description**. Otherwise treat the entire string as the decision description.

Extract:
- `TAG` — the topic prefix if present, otherwise empty
- `DECISION` — the decision description

Tell the user: "Researching: `<DECISION>`" (include tag if present: "Topic: `<TAG>`").

## Step 2 — Pass 1: Grep the source library

The shared source library is at `~/.claude/skills/sources.md`.

**If TAG is set:** use Grep to search only within the section whose `**tags:**` line
contains the tag value. Read that section of the file.

**If TAG is not set:** extract 2–4 significant keywords from DECISION (e.g., for
"chose hexagonal over layered for the service boundary" → "hexagonal", "layered",
"ports-adapters"). Grep for each keyword:

```bash
grep -i "<keyword>" ~/.claude/skills/sources.md -A 2
```

After grepping, read the matched entries in full. Do not cite a source just because the
keyword matched — read the one-sentence relevance note and confirm it fits the decision.

**Candidate selection:** Select up to 3 sources from the library that most directly
explain the reasoning behind DECISION. If 1 or more strong matches exist, skip Pass 2.
If fewer than 1 strong match exists, proceed to Pass 2.

## Step 3 — Pass 2: Research subagent (cache miss only)

Spawn a general-purpose subagent (via the Agent tool) with the following task:

> Find 1–2 primary sources (no summaries, no blog posts without a named author) that a
> senior engineer at a company like Stripe, Netflix, Google, or Uber would cite when
> making this decision: "<DECISION>".
> Prefer: named-practitioner engineering blog posts, peer-reviewed papers, official
> specifications, or free book chapters (SRE book, SE@Google, etc.).
> Return for each source: title, author or org, URL, and one sentence on why it is
> relevant to the specific decision.
> Do not fabricate URLs — only return sources you can verify exist.

The subagent runs in isolation; its output does not expand this conversation's context.

**Library feedback loop:** If the subagent returns a high-quality source not already in
`~/.claude/skills/sources.md`, append it under the appropriate `##` section. If no
section fits, add a new one. Format:

```
- **<Title>** | <Author/Org> | <URL> |
  <One-sentence relevance note.>
```

Tell the user: "Added `<Title>` to source library under `<Section>`."

## Step 4 — Emit footnote-ready output

Combine sources from Pass 1 and Pass 2 (up to 3 total). Assign sequential footnote
numbers starting at `[^1]`.

Output format:

```
**Decision:** <DECISION>

<!-- paste [^N] markers inline wherever this citation supports a claim in your document -->

[^1]: [<Title>](<URL>) — <Author/Org>, <year if known>. <One sentence: what this source explains and why it matters for this specific decision.>
[^2]: ...
```

If only one source was found, emit one footnote. If no sources were found after both
passes, say so explicitly and suggest rephrasing the decision description or broadening
the topic tag.

## Notes

- Inline placement of `[^N]` markers is the caller's responsibility — this skill emits
  the footnote definitions and a reminder to place the markers.
- GitHub has rendered `[^N]` footnotes in Markdown files since September 2022.
- For ADR alternatives coverage, invoke the skill once per decision branch (chosen
  approach + each rejected alternative). A `--compare` flag for single-invocation
  ADR coverage is tracked in brownm09/dev-env#2.
