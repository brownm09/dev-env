# Cover Letter Skill — Execution Flow

Visual reference for the `/cover-letter` skill execution path. The workflow is two-pass: Opus produces a completeness draft, Sonnet produces a density revision, and the Haiku self-check runs against the revised version.

```mermaid
flowchart TD
    S0["Step 0 — Load JD\nfile / PDF / URL / pasted text"]
    S0 --> S0B["Step 0b — Save JD to disk\n__JD.md"]
    S0B --> S1["Step 1 — Company log check"]
    S1 -->|"Already logged"| STOP(["Stop"])
    S1 --> S2

    S2["Step 2 — Fit screening\nHaiku subagent"]
    S2 -->|"SKIP"| STOP
    S2 -->|"FLAG"| FLAG{"Proceed?"}
    FLAG -->|"No"| STOP
    FLAG -->|"Yes"| CTX_START
    S2 -->|"PROCEED"| CTX_START

    subgraph CTX ["Context loading — session-cached where noted"]
        CTX_START["Step 3 — style_rules.md ♻"]
        CTX_START --> CTX_3B["Step 3b — prose_style.md ♻"]
        CTX_3B --> CTX_4["Step 4 — Model letter\nalways re-read"]
        CTX_4 --> CTX_5["Step 5 — accomplishments.md ♻"]
        CTX_5 --> CTX_5B["Step 5b — VOICE_SYNOPSIS.md ♻"]
        CTX_5B --> S5C["Step 5c — Filter accomplishments\n4–8 relevant rows → RELEVANT_ACCOMPLISHMENTS"]
    end

    S5C --> S6["Step 6 — Completeness draft (Opus subagent)\nNarrative arc, philosophy, signal calibration\nNo word cap; 700-word soft warning\n→ __Cover_Letter_Draft.md"]
    S6 --> S7["Step 7 — Density revision (Sonnet, inline)\nPrecision-then-compactness pass\nTarget 400 / ceiling 450\n→ __Cover_Letter.md"]

    S7 --> S8["Step 8 — Haiku self-check against final\n① Universal Self-Check\n② Cover-Letter-Specific Self-Check"]
    S8 --> S9["Step 9 — Fix violations + word count ≤ 450"]
    S9 --> S10["Step 10 — Log application\n(company_log.md already in context)"]
    S10 --> S11["Step 11 — Report\nthree artifact links · word count · flags"]
    S11 --> S12["Step 12 — Pre-merge cleanup note\n__Cover_Letter_Draft.md must be deleted before PR merge"]
```

**Legend:**
- ♻ Session-cached: skip re-read if already in context from a prior letter this session
- Self-check ① then ②: Universal check covers all prose rules; Cover-Letter-Specific covers format, length, structure
- Three artifacts share the stem `MikeBrown_YYYYMMDD__Company__Role__`: `__JD.md` (Step 0b), `__Cover_Letter_Draft.md` (Step 6), `__Cover_Letter.md` (Step 7)

**Token profile (per letter, first in session):**

| Phase | Agent | ~Tokens |
|---|---|---|
| Fit screening | Haiku subagent | 650 |
| Context reads (Steps 3–5b) | Main agent | 1,735 |
| Completeness draft | Opus subagent | 1,620 |
| Density revision | Main agent (Sonnet) | inline |
| Style self-check | Haiku subagent | 700 |

**Batch savings (letters 2-N, session-cached reads):** ~1,170 tokens eliminated per letter (Steps 3, 3b, 5, 5b skip re-reads).

**Lifecycle of the three artifacts:**

| Artifact | Step | In PR? | After merge? |
|---|---|---|---|
| `__JD.md` | 0b | yes | kept |
| `__Cover_Letter_Draft.md` | 6 | yes (cut to final is reviewable) | **deleted** |
| `__Cover_Letter.md` | 7, post-revision | yes | kept (canonical) |

See [ADR 009](../../docs/adr/009-cover-letter-token-efficiency.md) for the original token-efficiency analysis (predates the two-pass workflow; numbers above reflect current flow).
