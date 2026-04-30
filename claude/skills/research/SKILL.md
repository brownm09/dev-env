---
name: research
description: Find 1–3 primary sources for a decision or topic. Greps the shared source library first (zero cost), spawns a subagent only on a cache miss. Emits footnote-ready markdown. Invoke as /research [tag:] <decision> [--compare <alternative>].
argument-hint: "[<tag>:] <decision> [--compare <alternative>]"
allowed-tools: Grep Read Agent Write Edit AskUserQuestion
---

You are finding primary sources for an engineering decision or topic.
Produce footnote-ready markdown that can be pasted directly into any document.

## Step 1 — Parse $ARGUMENTS

`$ARGUMENTS` may take one of these forms:

- **Basic:** `chose hexagonal over layered for service boundary`
- **With tag:** `architecture: chose hexagonal over layered for service boundary`
- **With compare:** `chose hexagonal over layered --compare layered monolith`
- **With tag and compare:** `architecture: chose hexagonal over layered --compare layered monolith`

Parse rules:
1. If `$ARGUMENTS` matches `<word>: <rest>` where the prefix is a single word or
   hyphenated word, treat the prefix as **TAG** and the rest as the remaining string.
   Otherwise TAG is empty.
2. If the remaining string contains ` --compare `, split on the first occurrence:
   everything before is **DECISION**, everything after is **ALTERNATIVE**.
   Otherwise DECISION is the full remaining string and ALTERNATIVE is empty.

Extract:
- `TAG` — topic prefix if present, otherwise empty
- `DECISION` — the chosen approach being researched
- `ALTERNATIVE` — the rejected alternative (only if `--compare` was given)

Tell the user:
- "Researching: `<DECISION>`" (include tag if present: "Topic: `<TAG>`")
- If ALTERNATIVE is set: "Comparing against: `<ALTERNATIVE>`"

## Step 2 — Pass 1: Grep the source library (DECISION)

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
keyword matched — read the one-sentence relevance note and confirm it fits DECISION.

**Candidate selection:** Select up to 3 sources that most directly explain the reasoning
behind DECISION. If 1 or more strong matches exist, skip Steps 3a–3b for DECISION.

## Step 3a — Haiku quick scan for DECISION (cache miss only)

If fewer than 1 strong match was found in Step 2, spawn a **Haiku** subagent
(`model: "haiku"`) with the following task:

> First, call ToolSearch with query "select:WebSearch,WebFetch" to load web tools.
>
> Then find 1–2 primary sources (no summaries, no blog posts without a named author)
> that a senior engineer at a company like Stripe, Netflix, Google, or Uber would cite
> when making this decision: "<DECISION>".
> Prefer: named-practitioner engineering blog posts, peer-reviewed papers, official
> specifications, or free book chapters (SRE book, SE@Google, etc.).
> Return for each source: title, author or org, URL, and one sentence on why it is
> relevant to the specific decision.
> Do not fabricate URLs — only return sources you can confirm exist via web search.
> If you cannot find verifiable sources, say so explicitly and explain what you searched.

Report the Haiku subagent's findings to the user in a brief summary.

If the Haiku subagent returned 1+ sources with confirmed URLs, skip Step 3b and proceed
to Step 4.

## Step 3b — Approval gate for deep DECISION search

If the Haiku quick scan found no confirmed sources, ask the user for approval before
spending more tokens on a deeper Opus search:

Use AskUserQuestion with:
- Question: "The quick scan found no verified sources. Run a deeper Opus search? (Higher quality, ~15× more tokens than the quick scan.)"
- Header: "Deep search"
- Options:
  - "Yes, continue" — spawn an Opus subagent for a thorough search
  - "No, skip" — report no sources found and move on

If the user approves, spawn a **general-purpose** subagent (`model: "opus"`) with the following task:

> First, call ToolSearch with query "select:WebSearch,WebFetch" to load web tools.
>
> Find 1–2 primary sources (no summaries, no blog posts without a named author) that a
> senior engineer at a company like Stripe, Netflix, Google, or Uber would cite when
> making this decision: "<DECISION>".
> Prefer: named-practitioner engineering blog posts, peer-reviewed papers, official
> specifications, or free book chapters (SRE book, SE@Google, etc.).
> Return for each source: title, author or org, URL, and one sentence on why it is
> relevant to the specific decision.
> Do not fabricate URLs — only return sources you can confirm exist via web search.

**Library feedback loop (Steps 3a and 3b):** If either subagent returns a high-quality
source not already in `~/.claude/skills/sources.md`, append it under the appropriate
`##` section. If no section fits, add a new one. Format:

```
- **<Title>** | <Author/Org> | <URL> |
  <One-sentence relevance note.>
```

Tell the user: "Added `<Title>` to source library under `<Section>`."

## Step 4 — Pass 1 for ALTERNATIVE (only if --compare was given)

Skip this step if ALTERNATIVE is empty.

Repeat the same Pass 1 grep logic from Step 2, but using ALTERNATIVE as the query
subject. Extract 2–4 keywords from ALTERNATIVE and grep the source library. Select up
to 3 sources that most directly explain the reasoning for choosing the alternative.

If 1 or more strong matches exist, skip Steps 5a–5b for ALTERNATIVE.

## Step 5a — Haiku quick scan for ALTERNATIVE (cache miss only)

Skip this step if ALTERNATIVE is empty, or if Step 4 found sufficient sources.

Spawn a **Haiku** subagent (`model: "haiku"`) with the following task (runs
independently, does not expand conversation context):

> First, call ToolSearch with query "select:WebSearch,WebFetch" to load web tools.
>
> Find 1–2 primary sources (no summaries, no blog posts without a named author) that a
> senior engineer would cite when arguing for this approach: "<ALTERNATIVE>".
> This is the rejected alternative to: "<DECISION>".
> Prefer: named-practitioner engineering blog posts, peer-reviewed papers, official
> specifications, or free book chapters.
> Return for each source: title, author or org, URL, and one sentence on why it is
> relevant to this specific alternative.
> Do not fabricate URLs — only return sources you can confirm exist via web search.
> If you cannot find verifiable sources, say so explicitly and explain what you searched.

Report the Haiku subagent's findings to the user.

If the Haiku subagent returned 1+ sources with confirmed URLs, skip Step 5b and proceed
to Step 6.

## Step 5b — Approval gate for deep ALTERNATIVE search

Skip this step if ALTERNATIVE is empty, or if Step 5a found confirmed sources.

Use AskUserQuestion with:
- Question: "The quick scan found no verified sources for the alternative. Run a deeper Opus search? (Higher quality, ~15× more tokens than the quick scan.)"
- Header: "Deep search"
- Options:
  - "Yes, continue" — spawn an Opus subagent for a thorough search
  - "No, skip" — report no sources found for this side

If approved, spawn a **general-purpose** subagent (`model: "opus"`) with the following task:

> First, call ToolSearch with query "select:WebSearch,WebFetch" to load web tools.
>
> Find 1–2 primary sources (no summaries, no blog posts without a named author) that a
> senior engineer would cite when arguing for this approach: "<ALTERNATIVE>".
> This is the rejected alternative to: "<DECISION>".
> Prefer: named-practitioner engineering blog posts, peer-reviewed papers, official
> specifications, or free book chapters.
> Return for each source: title, author or org, URL, and one sentence on why it is
> relevant to this specific alternative.
> Do not fabricate URLs — only return sources you can confirm exist via web search.

Apply the same library feedback loop as Steps 3a–3b for any new high-quality sources found.

## Step 6 — Emit footnote-ready output

### Single-decision output (no --compare)

Combine sources from Steps 2–3b (up to 3 total). Assign sequential footnote numbers
starting at `[^1]`.

```
**Decision:** <DECISION>

<!-- paste [^N] markers inline wherever this citation supports a claim in your document -->

[^1]: [<Title>](<URL>) — <Author/Org>, <year if known>. <One sentence: what this source explains and why it matters for this specific decision.>
[^2]: ...
```

### Dual-decision output (with --compare)

Assign sequential footnote numbers across both groups (chosen starts at `[^1]`,
rejected continues from where chosen left off). Each group gets a label comment.

```
**Decision:** <DECISION>
**Alternative considered:** <ALTERNATIVE>

<!-- paste [^N] markers inline wherever these citations support claims in your document -->

<!-- chosen: <DECISION> -->
[^1]: [<Title>](<URL>) — <Author/Org>, <year if known>. <One sentence relevance.>
[^2]: ...

<!-- rejected: <ALTERNATIVE> -->
[^3]: [<Title>](<URL>) — <Author/Org>, <year if known>. <One sentence relevance.>
[^4]: ...
```

If only one source was found for a side, emit one footnote for that side. If no sources
were found for a side after both passes, say so explicitly under that side's label.

## Notes

- Inline placement of `[^N]` markers is the caller's responsibility — this skill emits
  the footnote definitions and a reminder to place the markers.
- GitHub has rendered `[^N]` footnotes in Markdown files since September 2022.
- For ADR alternatives coverage, `--compare <alternative>` covers both sides in a single
  invocation — closes brownm09/dev-env#2.
