#!/bin/bash
# Retry wrapper for the nightly journal-compose routine.
# Invoked by Windows Task Scheduler; replaces the Claude Code scheduled routine.
# Retries on non-zero exit up to MAX_RETRIES times with RETRY_DELAY seconds between attempts.

MAX_RETRIES=3
RETRY_DELAY=300  # 5 minutes — long enough for transient API issues to clear

LOG_DIR="C:/Users/brown/.claude/scratch"
LOG_FILE="$LOG_DIR/journal-compose-$(date -u +%Y-%m-%d).log"

# engineering-journal is a single shared checkout — sessions across every project write to it
# via `git -C`, not a per-session worktree of the journal itself (see claude/CLAUDE.md's Stub
# file workflow) — so the liveness pre-check below reads this path directly.
EJ="C:/Users/brown/Git/engineering-journal"

# Yesterday's LOCAL calendar date (not UTC) — matches the stub-filename/branch-naming convention
# and always targets a day that's genuinely complete, so /journal-compose's today-guard (ADR-017)
# never fires and no --force is needed. See ADR-084 for why "yesterday" was chosen over "always
# --force". LOG_FILE/log() above and below stay on `date -u` deliberately — UTC is reserved for
# internal operational artifacts (log naming/timestamps), per claude/CLAUDE.md.
DATE=$(date -d yesterday +%Y-%m-%d)

# "today" below is deliberately the execution day (this maintenance session's own journal
# entry), not $DATE (the compose target, yesterday) — a distinct, correct concept.
PROMPT="Run /journal-compose ${DATE}. Merge the result. Create a stub for today."

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"
}

log "=== journal-compose-with-retry starting (max $MAX_RETRIES attempts) ==="

for attempt in $(seq 1 $MAX_RETRIES); do
    log "Attempt $attempt of $MAX_RETRIES"

    # Liveness guard (ADR-086): a session may still be uncommitted for $DATE's stub in the
    # shared engineering-journal checkout (dev-env#579 activated this race; see ADR-084). This
    # is a deterministic bash pre-check — no dependency on whether claude -p's own exit code
    # would reflect an in-session abort. Skip only on non-final attempts: on the last attempt,
    # proceed anyway rather than let the day's journal never compose automatically at all — the
    # residual risk is covered by the existing draft/YYYY-MM-DD-recovery runbook.
    #
    # Scoped `set -o pipefail` (review finding, PR #587): without it, a bare
    # `VAR=$(cmd1 | cmd2)` only reports cmd2's exit code, so a `git status` failure (missing
    # $EJ, corrupt repo, index lock held by a concurrent process — itself a signal contention is
    # highest) would feed the Python script empty stdin, which exits 0 ("clean"), silently
    # defeating the guard exactly when it matters most. Setting pipefail inside a `( ... )`
    # subshell scopes it to this one pipeline only — it doesn't leak into the rest of the script
    # (e.g. the `tee`-based log() calls elsewhere) — and wrapping the whole subshell in `2>&1`
    # (not just the last command) captures git's stderr too, not only the Python script's.
    if [ "$attempt" -lt "$MAX_RETRIES" ]; then
        LIVENESS_OUTPUT=$( (set -o pipefail; git -C "$EJ" status --porcelain | py -3 C:/Users/brown/.claude/scripts/check-journal-compose-liveness.py "$DATE") 2>&1 )
        if [ $? -ne 0 ]; then
            log "Liveness guard: $LIVENESS_OUTPUT"
            log "Skipping this attempt without invoking claude. Retrying in ${RETRY_DELAY}s..."
            sleep $RETRY_DELAY
            continue
        fi
    fi

    claude --dangerously-skip-permissions -p "$PROMPT" >> "$LOG_FILE" 2>&1
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        log "SUCCESS on attempt $attempt"
        exit 0
    fi

    log "FAILED (exit $exit_code)"

    if [ $attempt -lt $MAX_RETRIES ]; then
        log "Retrying in ${RETRY_DELAY}s..."
        sleep $RETRY_DELAY
    fi
done

log "All $MAX_RETRIES attempts failed. Manual intervention required."
exit 1
