#!/usr/bin/env python3
"""Tests for turn-count-hook.py's two context-growth signals.

`turn-count-hook.py` is a UserPromptSubmit hook. Its `[session-size]` warning
fires on cumulative context growth since the session's first turn, so a large
start with no growth stays silent (dev-env#1217, ADR-150 Amendment 1). It also
nudges Claude to update the session's journal stub the first time that growth
reaches each band (dev-env#1160, ADR-150). This suite pins:

  * get_context_stats: current = the last main-thread assistant turn's
    context; growth = the sum of increases between consecutive turns, so a
    compaction's drop never subtracts and sidechain records are ignored;
  * check_context_tokens: silent at a large start with no growth, fires when
    growth crosses each threshold, a multi-threshold jump fires once, and a
    post-compaction re-climb does not re-fire;
  * check_stub_band: fires once per band, a multi-band jump fires once, a
    post-compaction re-climb past the same absolute size does not re-fire
    until growth reaches the next band, and an unsafe or missing session id
    never fires (no shared state file);
  * find_session_stub: the newest Write/Edit of a `*.stub.md`, not a Read or a
    sidechain write; the nudge names it, or says to create one;
  * load_stub_bands: hook-config.json override and per-key default fallback;
  * the nudge text is ASCII and carries the ADR-129 write rule;
  * main() end to end in a subprocess with a temporary home directory: one
    nudge across two prompts at the same band, the size warning absent at a
    large start and present on stdout after growth, and exit 0 throughout,
    garbage stdin included.

Usage:
    py -3 claude/scripts/tests/test_turn_count_hook.py

Exit 0 = all pass.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "turn-count-hook.py"

sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename -- import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("turn_count_hook", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
tch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tch)  # safe: main() is guarded by __main__

FIRST = tch.STUB_FIRST_TOKENS  # 150_000
STEP = tch.STUB_STEP_TOKENS    # 100_000
WARN = tch.CONTEXT_WARN_TOKENS
REPEAT = tch.CONTEXT_REPEAT_TOKENS
LARGE_START = 167_000  # the starting context dev-env#1217 was filed against
SID = "11111111-2222-3333-4444-555555555555"


def _assistant(tokens: int, sidechain: bool = False, content: list | None = None) -> dict:
    rec = {
        "type": "assistant",
        "message": {
            "content": content or [],
            "usage": {
                "input_tokens": 10,
                "cache_read_input_tokens": tokens - 10 - 5,
                "cache_creation_input_tokens": 5,
            },
        },
    }
    if sidechain:
        rec["isSidechain"] = True
    return rec


def _tool(name: str, **inp) -> dict:
    return {"type": "tool_use", "name": name, "input": inp}


def _write_transcript(path: Path, records: list) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


class _Tmp:
    """A temporary scratch dir swapped into the module, plus a transcript path."""

    def __enter__(self):
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name)
        self._saved = tch.SCRATCH
        tch.SCRATCH = root
        self.root = root
        self.transcript = root / "t.jsonl"
        self.cwd = str(root)  # no .claude/hook-config.json -> defaults
        return self

    def __exit__(self, *exc):
        tch.SCRATCH = self._saved
        self._td.cleanup()

    def band(self, records: list, session_id: str = SID):
        _write_transcript(self.transcript, records)
        stats = tch.get_context_stats(str(self.transcript))
        assert stats is not None, "stats unexpectedly None"
        return tch.check_stub_band(stats[1], session_id, self.cwd, str(self.transcript))

    def warn(self, records: list, session_id: str = SID):
        _write_transcript(self.transcript, records)
        stats = tch.get_context_stats(str(self.transcript))
        assert stats is not None, "stats unexpectedly None"
        return tch.check_context_tokens(stats[0], stats[1], session_id)


# --- get_context_stats ---------------------------------------------------------

def test_stats_missing_or_empty() -> str:
    assert tch.get_context_stats("") is None
    with _Tmp() as t:
        assert tch.get_context_stats(str(t.transcript)) is None, "missing file"
        _write_transcript(t.transcript, [{"type": "user", "message": {"content": "hi"}}])
        assert tch.get_context_stats(str(t.transcript)) is None, "no assistant turn"
    return "no path / missing file / no assistant turn -> None"


def test_stats_growth_ignores_compaction_drop() -> str:
    with _Tmp() as t:
        # 170k start, climb to 400k, compaction drops to 180k, climb to 300k.
        _write_transcript(t.transcript, [_assistant(n) for n in (170_000, 400_000, 180_000, 300_000)])
        current, growth = tch.get_context_stats(str(t.transcript))
    assert current == 300_000, current
    assert growth == 230_000 + 120_000, growth
    return f"current={current:,}, growth={growth:,} (drop not subtracted)"


def test_stats_skip_sidechain() -> str:
    with _Tmp() as t:
        _write_transcript(t.transcript, [
            _assistant(170_000),
            _assistant(20_000, sidechain=True),
            _assistant(200_000),
            _assistant(30_000, sidechain=True),
        ])
        current, growth = tch.get_context_stats(str(t.transcript))
    assert (current, growth) == (200_000, 30_000), (current, growth)
    return "sidechain records neither set current nor add spurious growth"


def test_stats_skip_zero_usage_synthetic() -> str:
    synthetic = {
        "type": "assistant",
        "message": {
            "model": "<synthetic>",
            "content": [{"type": "text", "text": "No response requested."}],
            "usage": {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        },
    }
    with _Tmp() as t:
        _write_transcript(t.transcript, [_assistant(170_000), synthetic, _assistant(200_000), synthetic])
        current, growth = tch.get_context_stats(str(t.transcript))
    assert (current, growth) == (200_000, 30_000), (current, growth)
    return "a zero-usage synthetic turn neither resets current nor adds growth"


def test_stats_skip_malformed_usage() -> str:
    bad = {"type": "assistant", "message": {"usage": {"input_tokens": None, "cache_read_input_tokens": 5}}}
    worse = {"type": "assistant", "message": {"usage": {"input_tokens": "12"}}}
    with _Tmp() as t:
        _write_transcript(t.transcript, [_assistant(170_000), bad, worse, _assistant(200_000)])
        current, growth = tch.get_context_stats(str(t.transcript))
    assert (current, growth) == (200_000, 30_000), (current, growth)
    return "a non-integer usage field skips that record instead of raising"


# --- check_context_tokens --------------------------------------------------------

def test_warn_silent_at_large_start() -> str:
    with _Tmp() as t:
        assert t.warn([_assistant(LARGE_START)]) is None, "a large start alone must not warn"
        assert t.warn([_assistant(LARGE_START)]) is None, "no growth on a later turn"
        assert not list(t.root.glob("ctx-warn-*")), "nothing to record yet"
    return f"{LARGE_START:,} start with no growth -> no warning and no state file"


def test_warn_fires_when_growth_crosses() -> str:
    with _Tmp() as t:
        assert t.warn([_assistant(LARGE_START), _assistant(LARGE_START + WARN - 1)]) is None, "just under"
        msg = t.warn([_assistant(LARGE_START + WARN)])
        assert msg is not None, "should fire at the first threshold"
        assert f"{WARN:,} tokens" in msg, msg
        assert f"{LARGE_START + WARN:,}" in msg, msg
        assert t.warn([_assistant(LARGE_START + WARN + REPEAT - 1)]) is None, "must not re-fire below the next step"
        assert t.warn([_assistant(LARGE_START + WARN + REPEAT)]) is not None, "fires at the next step"
    return "fires at the first threshold and each step, naming the growth and the current size"


def test_warn_multi_jump_fires_once() -> str:
    with _Tmp() as t:
        jump = LARGE_START + WARN + 2 * REPEAT + 5
        msg = t.warn([_assistant(LARGE_START), _assistant(jump)])
        assert msg is not None and "catching up" in msg, msg
        state = (t.root / f"ctx-warn-{SID}.txt").read_text().strip()
        assert state == str(WARN + 2 * REPEAT), state
        assert t.warn([_assistant(jump + 9)]) is None, "must not fire again in the same step"
    return "a jump over several thresholds warns once and records the highest"


def test_warn_post_compaction_no_refire() -> str:
    with _Tmp() as t:
        # Fires at 150k growth, at 317k absolute.
        assert t.warn([_assistant(LARGE_START), _assistant(LARGE_START + WARN)]) is not None
        # A compaction's drop is not growth, and climbing back past 317k absolute
        # adds only what it adds: the next warning is at 250k of growth.
        assert t.warn([_assistant(180_000), _assistant(270_000)]) is None, "240k of growth"
        msg = t.warn([_assistant(280_000)])
        assert msg is not None and f"{WARN + REPEAT:,}" in msg, msg
    return "a post-compaction re-climb fires only when growth reaches the next threshold"


def test_warn_text_is_ascii() -> str:
    with _Tmp() as t:
        msg = t.warn([_assistant(LARGE_START), _assistant(LARGE_START + WARN)])
    assert msg.startswith("[session-size]"), msg
    assert msg.isascii(), f"warning must be ASCII (cp1252-safe stdout): {msg!r}"
    assert "/compact" in msg and "/clear" in msg, msg
    return "starts with [session-size], ASCII, names /compact and /clear"


# --- check_stub_band -----------------------------------------------------------

def test_band_fires_once_per_band() -> str:
    with _Tmp() as t:
        base = 170_000
        assert t.band([_assistant(base), _assistant(base + FIRST - 1)]) is None, "below first band"
        assert t.band([_assistant(base + FIRST)]) is not None, "band 1 should fire"
        assert t.band([_assistant(base + FIRST + STEP - 1)]) is None, "band 1 must not re-fire"
        msg = t.band([_assistant(base + FIRST + STEP)])
        assert msg is not None and "band 2" in msg, msg
        assert t.band([_assistant(base + FIRST + STEP + 10)]) is None, "band 2 must not re-fire"
    return "band 1 and band 2 each fire exactly once"


def test_band_multi_jump_fires_once() -> str:
    with _Tmp() as t:
        msg = t.band([_assistant(170_000), _assistant(170_000 + FIRST + 2 * STEP + 5)])
        assert msg is not None and "band 3" in msg, msg
        state = (t.root / f"ctx-stub-{SID}.txt").read_text().strip()
        assert state == "3", state
        assert t.band([_assistant(170_000 + FIRST + 2 * STEP + 9)]) is None
    return "a jump straight to band 3 nudges once and records band 3"


def test_band_post_compaction_no_refire() -> str:
    with _Tmp() as t:
        # Band 1 fires at 330k absolute (160k growth).
        assert t.band([_assistant(170_000), _assistant(330_000)]) is not None
        # Compaction drops to 180k; climbing back past 330k absolute adds only
        # 160k growth more (320k total) -- band 2 starts at 250k, so it fires
        # once, at the right growth, not because 330k was re-crossed.
        assert t.band([_assistant(180_000), _assistant(260_000)]) is None, "growth 240k: still band 1"
        msg = t.band([_assistant(340_000)])
        assert msg is not None and "band 2" in msg, msg
        # A second compaction with a smaller re-climb (growth 320k + 20k) stays
        # in band 2: the drop neither re-fires band 2 nor resets the state.
        assert t.band([_assistant(150_000), _assistant(170_000)]) is None, "growth 340k: still band 2"
        state = (t.root / f"ctx-stub-{SID}.txt").read_text().strip()
        assert state == "2", state
    return "a post-compaction re-climb fires only when growth reaches the next band"


def test_band_requires_safe_session_id() -> str:
    with _Tmp() as t:
        records = [_assistant(170_000), _assistant(170_000 + FIRST)]
        assert t.band(records, session_id="") is None, "empty id"
        assert t.band([], session_id="../evil") is None, "path traversal id"
        assert t.band([], session_id=SID + "\n") is None, "trailing newline id"
        assert t.band([], session_id=None) is None, "missing id"
        assert not list(t.root.glob("ctx-stub-*")), "no state file written"
    return "empty or unsafe session id never fires or writes state"


def test_band_config_override() -> str:
    with _Tmp() as t:
        cfg_dir = t.root / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "hook-config.json").write_text(json.dumps({
            "journal_stub_context_first_tokens": 50_000,
            "journal_stub_context_step_tokens": 25_000,
        }), encoding="utf-8")
        msg = t.band([_assistant(170_000), _assistant(220_000)])
        assert msg is not None and "50,000" in msg, msg
        assert t.band([_assistant(245_000)]) is not None, "band 2 at 75k growth"
    return "hook-config.json bands are honored"


# --- load_stub_bands -------------------------------------------------------------

def test_load_stub_bands_fallbacks() -> str:
    with tempfile.TemporaryDirectory() as d:
        assert tch.load_stub_bands(d) == (FIRST, STEP), "missing config"
        (Path(d) / ".claude").mkdir()
        cfg = Path(d) / ".claude" / "hook-config.json"
        cfg.write_text(json.dumps({
            "journal_stub_context_first_tokens": 0,
            "journal_stub_context_step_tokens": "x",
        }), encoding="utf-8")
        assert tch.load_stub_bands(d) == (FIRST, STEP), "non-positive / non-integer"
        cfg.write_text(json.dumps({"journal_stub_context_step_tokens": 40_000}), encoding="utf-8")
        assert tch.load_stub_bands(d) == (FIRST, 40_000), "per-key fallback"
        cfg.write_text('{"journal_stub_context_step_tokens": Infinity}', encoding="utf-8")
        assert tch.load_stub_bands(d) == (FIRST, STEP), "JSON Infinity"
        cfg.write_text("[1, 2]", encoding="utf-8")
        assert tch.load_stub_bands(d) == (FIRST, STEP), "non-object config"
        cfg.write_text("{not json", encoding="utf-8")
        assert tch.load_stub_bands(d) == (FIRST, STEP), "malformed config"
    return "each key falls back to its default independently"


# --- find_session_stub + message ------------------------------------------------

def test_find_session_stub_newest_write_or_edit() -> str:
    old = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/2026-10-30_090000.stub.md"
    new = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/2026-10-30_120000.stub.md"
    decoy = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/2026-10-29_080000.stub.md"
    with _Tmp() as t:
        assert tch.find_session_stub(str(t.transcript)) is None, "missing transcript"
        _write_transcript(t.transcript, [
            _assistant(170_000, content=[_tool("Write", file_path=old, content="x")]),
            _assistant(171_000, content=[_tool("Edit", file_path=new, old_string="a", new_string="b")]),
            _assistant(172_000, content=[_tool("Read", file_path=decoy)]),
            _assistant(173_000, sidechain=True, content=[_tool("Write", file_path=decoy, content="x")]),
            _assistant(174_000, content=[_tool("Bash", command=f"git add {decoy}")]),
        ])
        found = tch.find_session_stub(str(t.transcript))
    assert found == new, found
    return "newest main-thread Write/Edit wins; Read, Bash, sidechain ignored"


def test_find_session_stub_skips_failed_write() -> str:
    good = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/2026-10-30_090000.stub.md"
    blocked = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/2026-10-30_120000.stub.md"
    with _Tmp() as t:
        _write_transcript(t.transcript, [
            _assistant(170_000, content=[{"type": "tool_use", "id": "tu_1", "name": "Write", "input": {"file_path": good}}]),
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}]}},
            _assistant(171_000, content=[{"type": "tool_use", "id": "tu_2", "name": "Write", "input": {"file_path": blocked}}]),
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_2", "is_error": True, "content": "blocked by hook"},
            ]}},
        ])
        found = tch.find_session_stub(str(t.transcript))
    assert found == good, found
    return "a stub Write whose tool_result is an error is not named"


def test_main_unsafe_session_id_falls_back() -> str:
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        scratch = home / ".claude" / "scratch"
        scratch.mkdir(parents=True)
        transcript = home / "t.jsonl"
        _write_transcript(transcript, [_assistant(170_000), _assistant(170_000 + FIRST + 1)])
        proc = _run_hook(home, {"session_id": "../x", "cwd": d, "transcript_path": str(transcript)})
        names = sorted(p.name for p in scratch.iterdir())
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0 and "[session-size]" in out, (proc.returncode, out)
    assert "[journal-stub]" not in out, out
    assert all(".." not in n for n in names) and "ctx-warn-unknown.txt" in names, names
    return "an unsafe id writes only *-unknown state files and never a band nudge"


def test_message_names_stub_or_says_create() -> str:
    stub = "C:/Users/brown/Git/engineering-journal/sessions/dev-env/2026-10-30_120000.stub.md"
    with _Tmp() as t:
        msg = t.band([_assistant(170_000), _assistant(170_000 + FIRST)])
        assert "No stub was written" in msg and "Write tool" in msg, msg
    with _Tmp() as t:
        msg = t.band([
            _assistant(170_000, content=[_tool("Write", file_path=stub, content="x")]),
            _assistant(170_000 + FIRST),
        ])
        assert stub in msg and "Edit tool" in msg, msg
    assert "ADR-129" in msg and "heredoc" in msg, msg
    assert msg.isascii(), "nudge must be ASCII (cp1252-safe stdout)"
    assert msg.startswith("[journal-stub]"), msg
    return "nudge names the stub (Edit) or says to create one (Write); ASCII; ADR-129"


# --- main() end to end -----------------------------------------------------------

def _run_hook(home: Path, payload) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    env["HOOK_HEARTBEAT_DIR_OVERRIDE"] = str(home / "hb")
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin.encode("utf-8"), capture_output=True, env=env, timeout=60,
    )


def test_main_end_to_end() -> str:
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        (home / ".claude" / "scratch").mkdir(parents=True)
        transcript = home / "t.jsonl"
        _write_transcript(transcript, [_assistant(170_000), _assistant(170_000 + FIRST + 1)])
        payload = {"session_id": SID, "cwd": d, "transcript_path": str(transcript)}
        first = _run_hook(home, payload)
        second = _run_hook(home, payload)
        garbage = _run_hook(home, "{not json")
    out1 = first.stdout.decode("utf-8", "replace")
    out2 = second.stdout.decode("utf-8", "replace")
    assert first.returncode == 0 and "[journal-stub]" in out1, (first.returncode, out1, first.stderr)
    assert second.returncode == 0 and "[journal-stub]" not in out2, out2
    assert garbage.returncode == 0, garbage.returncode
    return "one nudge across two prompts in the same band; exit 0 on garbage stdin"


def test_main_size_warning_end_to_end() -> str:
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        (home / ".claude" / "scratch").mkdir(parents=True)
        transcript = home / "t.jsonl"
        payload = {"session_id": SID, "cwd": d, "transcript_path": str(transcript)}
        _write_transcript(transcript, [_assistant(LARGE_START)])
        start = _run_hook(home, payload)
        _write_transcript(transcript, [_assistant(LARGE_START + WARN)])
        grown = _run_hook(home, payload)
    out_start = start.stdout.decode("utf-8", "replace")
    out_grown = grown.stdout.decode("utf-8", "replace")
    assert start.returncode == 0 and "[session-size]" not in out_start, (start.returncode, out_start)
    assert grown.returncode == 0 and "[session-size]" in out_grown, (grown.returncode, out_grown)
    assert b"session-size" not in start.stderr + grown.stderr, "the warning belongs on stdout"
    return "silent at a large start; warns on stdout after growth; exit 0 both times"


def main() -> int:
    tests = [
        ("stats: missing/empty -> None", test_stats_missing_or_empty),
        ("stats: growth ignores compaction drop", test_stats_growth_ignores_compaction_drop),
        ("stats: sidechain records skipped", test_stats_skip_sidechain),
        ("stats: zero-usage synthetic records skipped", test_stats_skip_zero_usage_synthetic),
        ("stats: malformed usage skipped, not raised", test_stats_skip_malformed_usage),
        ("size warning: silent at a large start", test_warn_silent_at_large_start),
        ("size warning: fires when growth crosses", test_warn_fires_when_growth_crosses),
        ("size warning: multi-threshold jump fires once", test_warn_multi_jump_fires_once),
        ("size warning: post-compaction re-climb", test_warn_post_compaction_no_refire),
        ("size warning: ASCII text", test_warn_text_is_ascii),
        ("band: fires once per band", test_band_fires_once_per_band),
        ("band: multi-band jump fires once", test_band_multi_jump_fires_once),
        ("band: post-compaction re-climb", test_band_post_compaction_no_refire),
        ("band: unsafe/empty session id", test_band_requires_safe_session_id),
        ("band: hook-config override", test_band_config_override),
        ("load_stub_bands fallbacks", test_load_stub_bands_fallbacks),
        ("find_session_stub newest Write/Edit", test_find_session_stub_newest_write_or_edit),
        ("find_session_stub skips failed write", test_find_session_stub_skips_failed_write),
        ("nudge names stub or says create", test_message_names_stub_or_says_create),
        ("main() end to end", test_main_end_to_end),
        ("main() size warning end to end", test_main_size_warning_end_to_end),
        ("main() unsafe session id falls back", test_main_unsafe_session_id_falls_back),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
