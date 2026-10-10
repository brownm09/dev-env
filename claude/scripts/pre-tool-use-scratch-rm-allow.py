#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- auto-approves a Bash command that does
nothing but delete files inside the scratch directory (`~/.claude/scratch/`),
so unattended sessions don't stall on a permission prompt for throwaway-file
cleanup. See ADR-147 and dev-env#1134.

Why a hook rather than a permission rule: the static allow rule
`Bash(rm -f C:/Users/brown/.claude/scratch/*)` matches only a command that
spells the literal path. Real sessions write `S=...; rm -f "$S/x.json"`,
`cd <scratch> && rm -f li.html`, `rm -f "$T"` with `T=` assigned earlier, and
so on. Permission rules can't resolve variables, and a broad `Bash(rm *)` rule
would approve deletions anywhere. This hook resolves the paths itself and
approves only when it can prove every target is inside scratch.

Decision contract (the whole safety argument):

  ALLOW -- emit a PreToolUse `permissionDecision: "allow"` (stdout JSON, exit
      0) ONLY when EVERY top-level segment of the command (split on `&&`,
      `;`, newline; any `||` rejects) is one of:
        * a pure literal assignment `NAME=value` (no command substitution,
          every `$VAR` resolved from an earlier in-command assignment or
          `$HOME`);
        * `cd <dir>` with an absolute <dir> that resolves inside (or to)
          scratch -- it sets the base for later relative `rm` targets, but
          only through an unbroken `&&` chain (after `;` or a newline the
          next segment runs even if the `cd` failed);
        * `rm` with flags only from ALLOWED_RM_FLAGS and >=1 target, every
          target resolving strictly inside scratch;
      and at least one segment is an `rm`.
  NO DECISION -- anything else: exit 0, no stdout, and Claude Code's normal
      permission flow runs unchanged. The hook NEVER blocks (never exit 2,
      never "deny"/"ask"). The worst a bug in it can do is approve
      something, so every path that isn't a proven scratch-only `rm` is no
      decision.

Approval must never reach a non-rm segment. A permission decision covers the
whole Bash call, so `gh pr view 1 && rm -f "$S/x"` must fall through even
though its `rm` half is harmless. Approving it would approve the `gh` half.

Why this file has its own lexer instead of `_hookio.split_top_level` /
`_shell_write_detect`: those primitives are built for *detection* and fail
PERMISSIVE by design. `split_top_level` drops an unterminated trailing
segment, and `tokenize_posix` throws away quoting, so it can't tell `'$S'`
(a literal) from `"$S"` (an expansion). Both are right for a guard that
looks for a hazard, because a missed match only skips a warning. Here a
missed segment would be APPROVED. The lexer below fails closed: any
character or construct it doesn't fully model rejects the whole command.

Control flow is modelled, not just split: `||` rejects the whole command
(its right side runs only when the left FAILED, so assignments across it
can't be tracked), and a relative `cd` rejects (it consults `$CDPATH`).

Rejected outright, wherever it appears unquoted: `|`, a lone `&`, `<`, `>`,
`(`, `)`, `{`, `}`, `!`, a backtick (also inside double quotes), `$(`,
`${...}` forms other than `${NAME}`, special parameters (`$1`, `$?`, ...),
a word-initial `#`, and C0 control characters. A path is rejected for: an
unresolved variable, an unquoted expansion whose value contains whitespace
or glob characters (word splitting/globbing), a relative path with no
in-command `cd`, any `..` component, a UNC or drive-relative form, glob characters in any
directory component, a recursive flag on scratch itself or on a glob final
component, or a target that doesn't resolve (realpath, case-insensitive on
Windows) strictly inside scratch's realpath.

`~` and `$HOME` are resolved only when the HOME environment variable is
unset or names the same directory Python resolves as home. Git Bash expands
both from HOME, but `os.path.expanduser` (and so `_hookutil.SCRATCH`) reads
USERPROFILE on Windows, so a divergent HOME would have the hook check one
path while bash deletes another. A divergent HOME makes every `~`/`$HOME`
unresolved (no decision); literal paths still work.

Fail-open: any unexpected exception becomes exit 0 with no output, which
means no decision and therefore the normal prompt. "Fail open" here means
"fail to the status quo", never "fail to approval".

Approval log: every approval appends one line (UTC ISO timestamp + the
reason, which lists the resolved targets) to `scratch-rm-allow.log` in the
scratch directory, immediately before the allow is emitted, so what the hook
approved can be audited afterwards. Best effort: any error writing it is
swallowed and never changes the decision or the exit code. Past
APPROVAL_LOG_MAX_BYTES the file is rotated to `scratch-rm-allow.log.1`
(one generation kept).

Wired under the PreToolUse `Bash` matcher only. It is approve-only (it
carries no safety check), so the Bash/PowerShell mirror invariant does not
apply to it; `test_settings_hook_wiring.py` exempts it by name. For any tool
other than Bash it returns no decision, because its lexer models POSIX shell
syntax only. `replay-scratch-rm-allow.py` replays `check_command` over
recorded session transcripts (ADR-147 section 6).

Stdin JSON shape (PreToolUse):
  {"hook_event_name": "PreToolUse", "tool_name": "Bash",
   "tool_input": {"command": "..."}, "session_id": "...", "cwd": "..."}

The scratch directory is `_hookutil.SCRATCH`. Test-only override:
`SCRATCH_RM_ALLOW_DIR_OVERRIDE` replaces it (read at call time); the approval
log follows it.
"""
import json
import os
import re
import sys
import time

from _hookio import read_command
import _hookout
import _hookutil

SCRATCH_DIR_OVERRIDE_ENV = "SCRATCH_RM_ALLOW_DIR_OVERRIDE"

APPROVAL_LOG_NAME = "scratch-rm-allow.log"
# Rotate the approval log once it passes this size. One approval line is a few
# hundred bytes, so 1 MB holds thousands of approvals; the cap only bounds disk
# use if something approves in a loop.
APPROVAL_LOG_MAX_BYTES = 1_000_000

# Commands longer than this get no decision. Real cleanup commands are a few
# hundred characters, and the cap bounds the lexer's work.
MAX_COMMAND_LEN = 8000

ALLOWED_RM_FLAGS = frozenset({"-f", "-r", "-R", "-rf", "-fr", "--force", "--recursive"})
RECURSIVE_RM_FLAGS = frozenset({"-r", "-R", "-rf", "-fr", "--recursive"})
GLOB_CHARS = frozenset("*?[]")
WHITESPACE = frozenset(" \t\n")

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=")
_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])(?=/|$)")
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")

# Unquoted characters this lexer refuses to model. `#` is only special at the
# start of a word and is handled separately.
_REJECT_UNQUOTED = frozenset("|&<>(){}!`")
# Characters that end an unquoted word (used to decide whether `~` stands alone).
_WORD_END = frozenset(" \t\n;&|")


class Reject(Exception):
    """The command is not a provable scratch-only rm: no decision."""


# --- Lexer -----------------------------------------------------------------
#
# A word is a list of parts, each a (kind, text) tuple:
#   "lit"   unquoted literal text      "qlit"  quoted/escaped literal text
#   "var"   unquoted $NAME / ${NAME}   "qvar"  $NAME inside double quotes
#   "tilde" a tilde-expansion to the home directory
# A segment is a list of words. lex() returns a list of (separator, words)
# pairs, where separator is the operator that preceded the segment: None for
# the first, "&&", or ";" (which also stands for a newline). `||` rejects the
# whole command: the segment after it runs only if the one before it FAILED,
# so `S=<outside> || S=<scratch>; rm -rf "$S/x"` leaves S outside scratch in
# real bash while a sequential evaluator would think it inside.


def _read_var(cmd, i):
    """Parse `$NAME` or `${NAME}` starting at cmd[i] == '$'. Returns
    (name, next_index). Anything else (`$(`, `$?`, `${X:-y}`, a bare `$`)
    rejects."""
    if cmd.startswith("${", i):
        m = _NAME_RE.match(cmd, i + 2)
        if not m or cmd[m.end():m.end() + 1] != "}":
            raise Reject("unsupported ${...} form")
        return m.group(0), m.end() + 1
    m = _NAME_RE.match(cmd, i + 1)
    if not m:
        raise Reject("unsupported $ form")
    return m.group(0), m.end()


def _tilde_eligible(parts, words):
    """True when a `~` here would be tilde-expanded by bash: at the start of a
    word, or right after the `=` of an assignment in a segment's first word."""
    if not parts:
        return True
    if words:
        return False
    if all(kind == "lit" for kind, _ in parts):
        return _ASSIGN_RE.fullmatch("".join(text for _, text in parts)) is not None
    return False


def lex(cmd):
    segments = []
    words = []
    sep = None  # operator that preceded the current segment
    parts = None  # None = between words
    i = 0
    n = len(cmd)

    def end_word():
        nonlocal parts
        if parts is not None:
            words.append(parts)
            parts = None

    def end_segment(next_sep):
        nonlocal words, sep
        end_word()
        segments.append((sep, words))
        words = []
        sep = next_sep

    while i < n:
        c = cmd[i]
        if c in " \t":
            end_word()
            i += 1
            continue
        if c == "\r" and cmd[i + 1:i + 2] == "\n":
            i += 1
            continue
        if c in ";\n":
            end_segment(";")
            i += 1
            continue
        if c == "&" and cmd[i + 1:i + 2] == "&":
            end_segment("&&")
            i += 2
            continue
        if c == "|" and cmd[i + 1:i + 2] == "|":
            raise Reject("|| runs its right side only on failure")
        if c == "\\":
            if i + 1 >= n:
                raise Reject("trailing backslash")
            nxt = cmd[i + 1]
            if nxt == "\n":  # line continuation: bash deletes the pair
                i += 2
                continue
            if parts is None:
                parts = []
            parts.append(("qlit", nxt))
            i += 2
            continue
        if c == "'":
            j = cmd.find("'", i + 1)
            if j == -1:
                raise Reject("unterminated single quote")
            if parts is None:
                parts = []
            parts.append(("qlit", cmd[i + 1:j]))
            i = j + 1
            continue
        if c == '"':
            if parts is None:
                parts = []
            i += 1
            buf = []
            while True:
                if i >= n:
                    raise Reject("unterminated double quote")
                d = cmd[i]
                if d == '"':
                    i += 1
                    break
                if d == "`":
                    raise Reject("backtick")
                if d == "\\" and i + 1 < n and cmd[i + 1] in '$`"\\\n':
                    if cmd[i + 1] != "\n":
                        buf.append(cmd[i + 1])
                    i += 2
                    continue
                if d == "$":
                    parts.append(("qlit", "".join(buf)))
                    buf = []
                    name, i = _read_var(cmd, i)
                    parts.append(("qvar", name))
                    continue
                buf.append(d)
                i += 1
            parts.append(("qlit", "".join(buf)))
            continue
        if c == "$":
            if parts is None:
                parts = []
            name, i = _read_var(cmd, i)
            parts.append(("var", name))
            continue
        if c in _REJECT_UNQUOTED:
            raise Reject(f"unsupported character {c!r}")
        if c == "#" and parts is None:
            raise Reject("comment")
        if c == "~" and _tilde_eligible(parts, words):
            nxt = cmd[i + 1:i + 2]
            if nxt and nxt != "/" and nxt not in _WORD_END:
                raise Reject("~user form")
            if parts is None:
                parts = []
            parts.append(("tilde", ""))
            i += 1
            continue
        if parts is None:
            parts = []
        parts.append(("lit", c))
        i += 1
    end_segment(None)
    return segments


# --- Expansion and path resolution ------------------------------------------


def _python_home():
    """The home directory Python resolves: USERPROFILE on Windows (Python 3.8+),
    HOME elsewhere. `_hookutil.SCRATCH` is built from the same value."""
    return os.path.expanduser("~")


def _home():
    """The directory bash expands `~` and `$HOME` to, or Reject when this hook
    can't know it.

    Git Bash expands both from the HOME environment variable. Python's home
    (see `_python_home`) comes from USERPROFILE on Windows. When HOME is set
    and names a different directory, trusting either one would let the hook
    check one path while bash deletes another, so `~`/`$HOME` stay
    unresolved. HOME in MSYS form (`/c/Users/x`) is normalized first; one
    that can't be (an MSYS-root path like `/home/x`, a relative or empty
    value) counts as divergent. HOME unset means bash falls back to the same
    profile directory, so Python's home is used."""
    home = _python_home()
    env_home = os.environ.get("HOME")
    if env_home is None:
        return home
    native = _to_native(env_home) if env_home else ""
    if not native or not _is_abs(native):
        raise Reject("HOME is not an absolute native path")
    if os.path.normcase(os.path.realpath(native)) != os.path.normcase(os.path.realpath(home)):
        raise Reject("HOME differs from the home directory Python resolves")
    return home


def expand(parts, env, *, assignment=False):
    """Expand a word's parts to its final text. Unquoted expansions in a
    command word (not an assignment value) undergo word splitting and
    globbing in bash, so a value with whitespace or glob characters rejects."""
    out = []
    for kind, text in parts:
        if kind in ("lit", "qlit"):
            out.append(text)
        elif kind == "tilde":
            out.append(_home())
        else:
            if text in env:
                value = env[text]
            elif text == "HOME":
                value = _home()
            else:
                raise Reject(f"unresolved variable ${text}")
            if kind == "var" and not assignment and (
                any(ch in WHITESPACE for ch in value) or any(ch in GLOB_CHARS for ch in value)
            ):
                raise Reject("unquoted expansion would split or glob")
            out.append(value)
    return "".join(out)


def _has_glob(text):
    return any(ch in GLOB_CHARS for ch in text)


def scratch_root():
    """Realpath of the scratch directory: `_hookutil.SCRATCH`, the single
    definition the rest of the tooling uses, unless the test-only override is
    set."""
    override = os.environ.get(SCRATCH_DIR_OVERRIDE_ENV)
    return os.path.realpath(override if override else _hookutil.SCRATCH)


def _to_native(path):
    """Normalize the drive-letter spellings Git Bash accepts (`/c/x`) to the
    native form on Windows. Rejects forms whose meaning depends on state this
    hook can't see: UNC paths, a drive-relative `C:x`, and an MSYS-root path
    like `/tmp`."""
    if os.name != "nt":
        return path
    if path.startswith(("\\\\", "//")):
        raise Reject("UNC path")
    path = _MSYS_DRIVE_RE.sub(lambda m: m.group(1) + ":", path, count=1)
    if path.endswith(":") and len(path) == 2:
        path += "/"
    if re.match(r"^[A-Za-z]:", path) and not _WIN_ABS_RE.match(path):
        raise Reject("drive-relative path")
    if path.startswith(("/", "\\")):
        raise Reject("MSYS-root path")
    return path


def _is_abs(path):
    if os.name == "nt":
        return _WIN_ABS_RE.match(path) is not None
    return path.startswith("/")


def _inside(resolved, root, *, allow_equal):
    r = os.path.normcase(resolved)
    s = os.path.normcase(root)
    try:
        common = os.path.commonpath([r, s])
    except ValueError:  # different drives
        return False
    if common != s:
        return False
    if r == s:
        return allow_equal
    return True


def resolve(path, cwd, *, allow_glob_tail):
    """Resolve *path* to an absolute realpath. Glob characters are allowed
    only in the final component, and only when *allow_glob_tail*."""
    if not path or "\x00" in path:
        raise Reject("empty path")
    if ".." in re.split(r"[\\/]", path):
        # Windows collapses `..` lexically before following links, POSIX
        # physically, and bash `cd` logically. Rejecting it outright means
        # realpath() below never has to agree with any of them.
        raise Reject(".. component")
    p = _to_native(path)
    if not _is_abs(p):
        if cwd is None:
            raise Reject("relative path with no in-command cd")
        p = os.path.join(cwd, p)
    head, tail = os.path.split(p)
    if _has_glob(head):
        raise Reject("glob in a directory component")
    if _has_glob(tail):
        if not allow_glob_tail:
            raise Reject("glob not allowed here")
        return os.path.join(os.path.realpath(head), tail), True
    return os.path.realpath(p), False


# --- Segment evaluation -----------------------------------------------------


def evaluate(cmd, root):
    """Return the list of resolved rm targets when *cmd* is a provable
    scratch-only rm, else raise Reject."""
    env = {}
    cwd = None
    targets_out = []
    for sep, words in lex(cmd):
        if sep == ";" and cwd is not None:
            # After `;` (or a newline) this segment runs even if an earlier
            # `cd` failed, so the cwd is either scratch or the session's own
            # (a repo). Relative targets are no longer provable.
            cwd = None
        if not words:
            continue
        first = words[0]
        lead = []
        for kind, text in first:
            if kind != "lit":
                break
            lead.append(text)
        m = _ASSIGN_RE.match("".join(lead))
        if m:
            if len(words) != 1:
                raise Reject("assignment prefix on a command")
            # Rebuild the value parts: drop the `NAME=` prefix from the
            # leading literal run.
            skip = m.end()
            value_parts = []
            for kind, text in first:
                if skip > 0 and kind == "lit":
                    take = min(skip, len(text))
                    skip -= take
                    text = text[take:]
                    if not text:
                        continue
                value_parts.append((kind, text))
            env[m.group(1)] = expand(value_parts, env, assignment=True)
            continue

        texts = [expand(w, env) for w in words]
        verb = texts[0]
        if verb == "cd":
            if len(texts) != 2:
                raise Reject("cd needs exactly one argument")
            if not _is_abs(_to_native(texts[1])):
                # A relative `cd` consults $CDPATH, which this hook can't see.
                raise Reject("relative cd")
            resolved, _ = resolve(texts[1], None, allow_glob_tail=False)
            if not _inside(resolved, root, allow_equal=True):
                raise Reject("cd outside scratch")
            cwd = resolved
            continue
        if verb != "rm":
            raise Reject(f"non-rm segment: {verb!r}")

        recursive = False
        opts_done = False
        targets = []
        for t in texts[1:]:
            if not opts_done and t == "--":
                opts_done = True
                continue
            # GNU rm permutes options, so a flag after a target still counts.
            if not opts_done and t.startswith("-") and t != "-":
                if t not in ALLOWED_RM_FLAGS:
                    raise Reject(f"rm flag not allowed: {t}")
                if t in RECURSIVE_RM_FLAGS:
                    recursive = True
                continue
            targets.append(t)
        if not targets:
            raise Reject("rm with no target")
        for t in targets:
            resolved, globbed = resolve(t, cwd, allow_glob_tail=True)
            if recursive and globbed:
                raise Reject("recursive rm with a glob")
            if not _inside(resolved, root, allow_equal=False):
                raise Reject("rm target not strictly inside scratch")
            targets_out.append(resolved)
    if not targets_out:
        raise Reject("no rm segment")
    return targets_out


def check_command(cmd):
    """Return the resolved rm targets when the Bash command *cmd* is a provable
    scratch-only rm, else raise Reject naming why. `decide` and the replay
    harness (`replay-scratch-rm-allow.py`) share this one entry point."""
    if not isinstance(cmd, str) or not cmd:
        raise Reject("empty command")
    if len(cmd) > MAX_COMMAND_LEN:
        raise Reject("command longer than MAX_COMMAND_LEN")
    if any((ord(ch) < 32 and ch not in "\t\n\r") or ord(ch) == 127 for ch in cmd):
        raise Reject("control character")
    return evaluate(cmd, scratch_root())


def decide(data):
    """Return the allow reason for a PreToolUse payload, or None for no
    decision. Pure apart from realpath lookups; never raises Reject."""
    if not isinstance(data, dict) or data.get("tool_name") != "Bash":
        return None
    try:
        targets = check_command(read_command(data))
    except Reject:
        return None
    return "scratch-only rm: " + ", ".join(targets)


def log_approval(reason, root=None):
    """Append one `<UTC ISO timestamp> <reason>` line to the approval log in
    *root* (default: the scratch directory). Rotates the log to `.1` once it
    passes APPROVAL_LOG_MAX_BYTES. Best effort: never raises, so it can't
    change the decision or the exit code."""
    try:
        path = os.path.join(root if root is not None else scratch_root(), APPROVAL_LOG_NAME)
        try:
            if os.path.getsize(path) > APPROVAL_LOG_MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"{stamp} {reason}\n")
    except Exception:  # noqa: BLE001 -- best effort by contract
        pass


def main():
    _hookutil.record_heartbeat("pre-tool-use-scratch-rm-allow")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    reason = decide(data)
    if reason is None:
        sys.exit(0)
    log_approval(reason)
    _hookout.emit_allow(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
