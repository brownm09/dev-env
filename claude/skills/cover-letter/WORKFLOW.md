# Cover Letter Skill — Execution Flow

Visual reference for the `/cover-letter` skill execution path, including which steps are
session-cached for batch runs and where the Sonnet and Opus paths diverge.

```mermaid
flowchart TD
    S_1["Step -1 — Model selection\nSonnet ✦ recommended\nOpus — high-stakes"]
    S_1 -->|"Sonnet"| SON(["DRAFT_MODEL = sonnet"])
    S_1 -->|"Opus"| OPU(["DRAFT_MODEL = opus"])
    SON & OPU --> S0

    S0["Step 0 — Load JD\nfile / PDF / URL / pasted text"]
    S0 --> S1["Step 1 — Company log check"]
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
    end

    CTX_5B --> BRANCH{"DRAFT_MODEL?"}

    BRANCH -->|"opus"| S5C["Step 5c — Filter accomplishments\n4–8 relevant rows → RELEVANT_ACCOMPLISHMENTS"]
    S5C --> OPUS_DRAFT["Step 6 (Opus)\nSpawn Opus subagent\nPasses: style rules, model letter,\nRELEVANT_ACCOMPLISHMENTS,\nvoice synopsis, JD"]
    OPUS_DRAFT --> S7["Step 7 — Haiku self-check\n① Universal Self-Check\n② Cover-Letter-Specific Self-Check"]

    BRANCH -->|"sonnet"| SONNET_DRAFT["Step 6 (Sonnet)\nDraft inline — all context in window\nApply: style rules, model letter,\naccomplishments, voice synopsis"]
    SONNET_DRAFT --> S6_CHECK["Inline self-check\n① Universal Self-Check\n② Cover-Letter-Specific Self-Check\n↳ Skip Step 7"]

    S7 --> S8["Step 8 — Fix violations"]
    S6_CHECK --> S8

    S8 --> S9["Step 9 — Word count ≤ 475"]
    S9 --> S10["Step 10 — Save letter"]
    S10 --> S11["Step 11 — Log application\n(company_log.md already in context)"]
    S11 --> S12["Step 12 — Report\nfile link · word count · model used · flags"]
```

**Legend:**
- ♻ Session-cached: skip re-read if already in context from a prior letter this session
- Self-check ① then ②: Universal check covers all prose rules; Cover-Letter-Specific covers format, length, structure

**Token profile (per letter, first in session):**

| Phase | Agent | ~Tokens |
|---|---|---|
| Fit screening | Haiku subagent | 650 |
| Context reads (Steps 3–5b) | Main agent | 1,735 |
| Draft — Sonnet inline | Main agent | 0 extra |
| Draft — Opus subagent | Opus subagent | 1,620 |
| Self-check — Sonnet inline | Main agent | 0 extra |
| Self-check — Haiku (Opus path) | Haiku subagent | 700 |

**Batch savings (letters 2-N, session-cached reads):** ~1,170 tokens eliminated per letter
(Steps 3, 3b, 5, 5b skip re-reads).

See [ADR 009](../../docs/adr/009-cover-letter-token-efficiency.md) for the full analysis.
