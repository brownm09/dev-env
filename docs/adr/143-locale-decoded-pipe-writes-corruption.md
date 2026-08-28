# ADR-143: A Locale-Decoded Pipe Can *Write* Corruption, Not Only Misreport It

**Date:** 2026-08-28
**Status:** Accepted
**Closes:** [dev-env#1073](https://github.com/brownm09/dev-env/issues/1073)
**Tags:** claude-behavior, cli-scripting, encoding, cp1252, utf-8, mojibake, subprocess, text-mode, silent-corruption, read-modify-write, gh-api, github, windows, numeric-verification, data-corruption, global-rule, correction, adr-007, adr-034, adr-117, adr-138

---

## Context

On 2026-08-27, a session ticking a checkbox in queue issue
[dev-env#963](https://github.com/brownm09/dev-env/issues/963) did a read-modify-write of the
issue body:

```python
raw = subprocess.run(["gh", "api", ".../issues/963", "--jq", ".body"],
                     capture_output=True, text=True, check=True).stdout   # <-- text=True
body = raw.replace(old, new, 1) + progress_note
# ... PATCH body back
```

`text=True` with no `encoding=` decodes the child's stdout with
`locale.getpreferredencoding(False)` — **cp1252** on this machine. `gh` emits UTF-8, so every
em dash, en dash, `×` and `→` in the body was reinterpreted as cp1252 and then **PATCHed back
to GitHub in that mangled form**. Measured on the live body afterwards: 38 × U+00E2, 35 × U+20AC,
33 × U+201D, where the original had 33 × U+2014, 4 × U+00D7, 2 × U+2013, 3 × U+2192. Both `gh`
calls returned `200`. Nothing errored.

`claude/CLAUDE.md` → CLI Scripting Checklist **item 6** already documented this exact decode —
but only in the *diagnostic* direction:

> a pipe-relayed "corruption" that a direct byte read doesn't reproduce is a
> diagnostic-pipeline artifact, not a real defect

That framing describes the pipe making clean data *look* corrupted. It says nothing about the
pipe *creating* corruption that is then written to a live remote resource — and a session that
has internalized item 6 is, if anything, **primed to dismiss the mojibake it just created**.
That is precisely what happened: the first post-write check was read as the known artifact, and
only a numeric codepoint histogram settled it.

## Decision

Extend CLI Scripting Checklist item 6 to cover both directions, restructured from one paragraph
into a broadened headline, a shared decode preamble, and two **labeled sub-bullets** — *Read
direction — the pipe misreports* and *Write direction — the pipe creates the corruption and you
ship it*. The write-direction bullet carries three concrete recipes:

1. **The safe read.** Never round-trip remote content through a locale-decoded pipe.
   `subprocess.run(..., capture_output=True)` **without** `text=True`, then an explicit
   `.decode("utf-8")` — or redirect to a file and read it with `encoding="utf-8"`.
2. **Verify numerically, never visually.** A console-printed sample is re-encoded on its way to
   the terminal, so it cannot distinguish corrupt *data* from a corrupt *display*. Count
   codepoints: `collections.Counter(ord(ch) for ch in body if ord(ch) > 127)`.
3. **The reversal**, since the transform is deterministic:
   `corrupted.encode("cp1252").decode("utf-8")` — lossless only while every mangled codepoint
   still round-trips through cp1252.

No hook, lint, or test is added. See *Alternatives considered*.

## Rationale

**The decode has two outcomes, and only the loud one was already handled.** This is the fact the
existing documentation obscured by treating "cp1252 decode" as one hazard:

| Byte, from a UTF-8 sequence | In cp1252 | Outcome | Status before this ADR |
|---|---|---|---|
| `0x9D` (and `0x81`, `0x8D`, `0x8F`, `0x90`) | *undefined* | `UnicodeDecodeError` — loud crash | [dev-env#503](https://github.com/brownm09/dev-env/issues/503); closed repo-wide by `_winsubp.py` ([ADR-007](007-hook-command-invocation.md), 2026-07-02 follow-up 3) |
| `0xE2 0x80 0x94` (em dash) | all three map | wrong characters, **no error at all** | undocumented |

An em dash is not an exotic input — it is the single most common non-ASCII character in this
repo's own prose. So the silent branch is not the rare tail of the distribution; on GitHub bodies
written in this house style it is the *overwhelmingly likely* branch, and it is the one nothing
was watching.

Reproduced live on this machine, read-only, at the time of writing:

```
preferred encoding: cp1252
text=True   codepoints: U+00C3, U+00E2 x3, U+2014, U+2019, U+201C, U+201D, U+2020, U+20AC x2
bytes+utf-8 codepoints: U+00D7, U+2013, U+2014, U+2192        <- correct
reversal works        : True
```

The arithmetic reproduces #963's measured histogram exactly: 33 × U+2014 + 2 × U+2013 +
3 × U+2192 contribute 38 `0xE2` bytes → 38 × U+00E2; 33 + 2 `0x80` bytes → 35 × U+20AC; 33
`0x94` bytes → 33 × U+201D.

**Why the read-direction framing was actively harmful, not merely incomplete.** An
under-specified rule leaves a session with no guidance. This one left it with *wrong* guidance:
item 6's conclusion — "probably an artifact, the file is fine" — is a ready-made dismissal, and
it is reached by the same surface evidence (mojibake glyphs in terminal output) that a genuine
write-side corruption produces. The rule did not fail to fire; it fired and returned the wrong
answer. That is why the fix is a structural one — two labeled directions — rather than another
sentence appended to a paragraph that already ends on "not a real defect." A reader who stops at
the end of the read bullet must not have been told to relax.

**Why the verification must be numeric.** When a display is suspected of lying, the display
cannot adjudicate. Printing a sample re-encodes it through `sys.stdout.encoding` (cp1252 here),
which introduces a *second* independent transform on top of the one under investigation — so
"looks wrong on screen" is consistent with both a corrupt body and a clean body rendered badly,
and "looks right" is consistent with both too. A codepoint histogram is computed from the
in-memory `str` and only its *counts* are printed, so the digits survive any terminal encoding.
This ADR's own drafting hit the trap: an inline `repr()` of the affected `claude/CLAUDE.md` line,
printed to the console, came back with a replacement character where the file holds U+00E2 —
while `Counter(ord(c) ...)` over the same line reported the true content. Same class as
[ADR-034](034-error-message-diligence.md): evidence that *looks* like a diagnosis but is
downstream of the very thing being diagnosed.

**Why the reversal is worth recording.** cp1252-decoding UTF-8 is injective over the bytes it
maps, so the mangling is exactly invertible and recovery is one expression rather than retyping
the content by hand. It is stated with its precondition, because the inverse is *not* total: a
U+FFFD left behind by an `errors="replace"` decode, or a byte that landed on one of the five
undefined positions, has destroyed the original irreversibly. Recording the recipe without the
precondition would invite a session to "recover" a body that had already lost information.

## Alternatives considered

**Amend [ADR-117](117-absence-claims-need-absolute-paths.md) instead of writing a new ADR.**
Rejected. ADR-117 owns CLI Scripting Checklist **item 5**, and its subject is the *absence
claim* — five mechanisms that turn a partial or stale view into output indistinguishable from a
genuine miss. It references item 6 only as a sibling in that family (and as one of the two whose
obvious fix is not self-confirming); it does not own or govern it. This ADR's hazard is not a
false-absent one at all: nothing is concluded absent, and the harm is a live remote resource
written with wrong bytes. Folding a write-corruption rule into an absence-claim ADR would make
both harder to find. Item 6 itself has no owning ADR — it was added by a docs-only commit
(`09743d2`, [dev-env#954](https://github.com/brownm09/dev-env/issues/954)) touching
`claude/CLAUDE.md` and nothing else — so there was nothing to amend.

**A lint over `claude/scripts/*.py` for `subprocess.run(..., text=True)` feeding a `gh api -X
PATCH/POST`.** Rejected, on two independent grounds.

*First, the enforceable half is already enforced.* `claude/scripts/_winsubp.py` patches
`subprocess.Popen.__init__` to default `encoding="utf-8", errors="replace"` on any text-mode call
that does not specify its own encoding, and `test_pyw_stdio.py` fails the build if a
subprocess-using hook ships without `import _winsubp` ([ADR-007](007-hook-command-invocation.md),
2026-07-02 follow-up 3). Verified at the time of writing: all 35 files under `claude/scripts/`
that use `text=True` — 73 call sites — import `_winsubp`, with no exceptions, so every one of
them is safe **by construction**. A lint keyed on `text=True` would produce zero true positives
and 35 false ones.

*Second, the lint's surface does not contain the bug.* The offending code was an **ad-hoc `py -3`
script written inline in a session** — never a file in `claude/scripts/`, and explicitly outside
[ADR-138](138-shell-content-write-guard.md)'s guard, which exempts a heredoc feeding an
interpreter's stdin ("a program, not file content"). No lint over committed files could have seen
it. Catching it would require taint analysis of Python source inside a heredoc, inside a hook
firing on every Bash call — and per ADR-117's *Why no mechanical guard*, there is no wrong call
to intercept: `subprocess.run(..., text=True)` is correct everywhere `_winsubp` is loaded, which
is everywhere a lint would be looking.

This is the shape [dev-env#963](https://github.com/brownm09/dev-env/issues/963) item 3
(scope-discipline review of self-referential churn) exists to catch: a gate built for a rule a
sentence can carry, over a surface that never held the defect. Recorded here so the question is
not re-litigated from scratch.

**Tell ad-hoc scripts to `sys.path`-insert `~/.claude/scripts` and `import _winsubp`.** Rejected.
It is more ceremony than simply dropping `text=True`, it couples throwaway scripts to a
dev-env-internal module, and it silently substitutes `errors="replace"` for an explicit decode —
turning a would-be `UnicodeDecodeError` into a U+FFFD, which is exactly the case the reversal
recipe cannot undo.

## Consequences

- Item 6 gains a preamble and grows from one paragraph to two labeled sub-bullets in the
  always-loaded global `CLAUDE.md` — roughly one bullet of context weight, the cost
  [ADR-114](114-slim-testing-section-index.md) flags, paid once. It adds no new *structure* to
  the file: item 5 is already sub-bulleted in the same shape.
- The two directions are now discoverable from either end. A session arriving with "is this
  corruption real?" and one arriving with "did I just corrupt this?" land on the same item and
  are told which conclusion applies.
- The loud/silent split is written down, so `_winsubp`'s coverage is no longer mistaken for
  coverage of the whole class. It protects dev-env's committed hooks; it protects nothing a
  session writes inline.
- No code, hook, or test changes. The `## Testing` docs-only guard (item 4) applies; the full
  suite is run unchanged as a regression check.
- If the ad-hoc-script surface later grows a bounded, machine-checkable form — a standard helper
  every session-authored script is expected to route `gh` reads through, say — the lint rejected
  above becomes worth revisiting. Nothing today provides that form.

## References

- [dev-env#1073](https://github.com/brownm09/dev-env/issues/1073) — the issue this ADR resolves.
- [dev-env#963](https://github.com/brownm09/dev-env/issues/963) — the queue issue whose body was
  corrupted and recovered; its item 3 is the scope-discipline check the lint was weighed against.
- [dev-env#952](https://github.com/brownm09/dev-env/issues/952) — the read-direction incident that
  created item 6, surfaced mid-investigation of
  [career-playbook#1139](https://github.com/brownm09/career-playbook/issues/1139).
- [dev-env#503](https://github.com/brownm09/dev-env/issues/503) — the loud branch of the same
  decode: `UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d`.
- [ADR-007](007-hook-command-invocation.md) — `_winsubp.py`, which closes both branches for
  committed hooks (2026-07-02 follow-up 3).
- [ADR-117](117-absence-claims-need-absolute-paths.md) — CLI Scripting Checklist item 5; the
  sibling item, and the ADR whose *Why no mechanical guard* reasoning this one extends.
- [ADR-034](034-error-message-diligence.md) — the kindred hazard: evidence that looks like a
  diagnosis but is produced downstream of the thing being diagnosed.
- [ADR-138](138-shell-content-write-guard.md) — the interpreter-stdin exemption that places the
  offending script outside every existing guard.
- [Python `subprocess` — frequently used arguments](https://docs.python.org/3/library/subprocess.html#frequently-used-arguments)
  — `text=True` opens the child's streams in text mode; with no `encoding=` they fall through to
  [`io.TextIOWrapper`](https://docs.python.org/3/library/io.html#io.TextIOWrapper)'s default,
  which is [`locale.getpreferredencoding(False)`](https://docs.python.org/3/library/locale.html#locale.getpreferredencoding).
- [Unicode Consortium — `CP1252.TXT`](https://www.unicode.org/Public/MAPPINGS/VENDORS/MICSFT/WINDOWS/CP1252.TXT)
  — the authoritative byte→codepoint table Python's `cp1252` codec implements, in which `0x81`,
  `0x8D`, `0x8F`, `0x90` and `0x9D` are left undefined. (The WHATWG Encoding Standard's
  `windows-1252` index deliberately fills those five with C1 controls, so it is *not* the right
  reference for this codec's failure branch.)
