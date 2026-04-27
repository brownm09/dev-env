#!/bin/bash
# Retry wrapper for the nightly journal-compose routine.
# Invoked by Windows Task Scheduler; replaces the Claude Code scheduled routine.
# Retries on non-zero exit up to MAX_RETRIES times with RETRY_DELAY seconds between attempts.

MAX_RETRIES=3
RETRY_DELAY=300  # 5 minutes — long enough for transient API issues to clear

LOG_DIR="C:/Users/brown/.claude/scratch"
LOG_FILE="$LOG_DIR/journal-compose-$(date -u +%Y-%m-%d).log"

PROMPT="Run /journal-compose. Merge the result. Create a stub for today."

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"
}

log "=== journal-compose-with-retry starting (max $MAX_RETRIES attempts) ==="

for attempt in $(seq 1 $MAX_RETRIES); do
    log "Attempt $attempt of $MAX_RETRIES"

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
