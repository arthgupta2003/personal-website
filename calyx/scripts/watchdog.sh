#!/usr/bin/env bash
# Watchdog — checks dashboard + tunnel health, restarts if down
# Intended to run via launchd every 5 minutes

DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$DIR/state/watchdog.log"
COOLDOWN_FILE="$DIR/state/.watchdog_cooldown"
COOLDOWN_SECS=300  # don't restart more than once per 5 minutes

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"; }

# Check 1: tmux session exists
if ! tmux has-session -t recom 2>/dev/null; then
    log "FAIL: tmux session 'recom' not found"
    NEEDS_RESTART=1
fi

# Check 2: dashboard responds on port 8000
if ! curl -sf -o /dev/null --max-time 5 http://localhost:8000/health 2>/dev/null; then
    # Fallback: try root path
    if ! curl -sf -o /dev/null --max-time 5 http://localhost:8000/ 2>/dev/null; then
        log "FAIL: dashboard not responding on port 8000"
        NEEDS_RESTART=1
    fi
fi

# Check 3: cloudflared process running
if ! pgrep -x cloudflared >/dev/null 2>&1; then
    log "FAIL: cloudflared not running"
    NEEDS_RESTART=1
fi

if [ "${NEEDS_RESTART:-0}" = "1" ]; then
    # Cooldown check — avoid restart loops
    if [ -f "$COOLDOWN_FILE" ]; then
        last=$(cat "$COOLDOWN_FILE")
        now=$(date +%s)
        if [ $((now - last)) -lt $COOLDOWN_SECS ]; then
            log "SKIP: cooldown active (last restart $(( (now - last) ))s ago)"
            exit 0
        fi
    fi

    log "ACTION: restarting via start.sh"
    date +%s > "$COOLDOWN_FILE"
    cd "$DIR"
    bash "$DIR/start.sh" restart >> "$LOG" 2>&1
    log "ACTION: restart complete"
else
    # Only log OK once per hour to avoid log spam
    HOUR_FILE="$DIR/state/.watchdog_ok_hour"
    CURRENT_HOUR=$(date '+%Y-%m-%d-%H')
    LAST_OK=$(cat "$HOUR_FILE" 2>/dev/null || echo "")
    if [ "$CURRENT_HOUR" != "$LAST_OK" ]; then
        log "OK: all services healthy"
        echo "$CURRENT_HOUR" > "$HOUR_FILE"
    fi
fi
