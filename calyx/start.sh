#!/usr/bin/env bash
# Recom startup — bare metal with tmux
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

SESSION="recom"
LOG="$DIR/state/startup.log"

# Wait for network on boot (launchd may fire before WiFi/Ethernet is up)
if [ "${1:-start}" = "start" ]; then
  for i in $(seq 1 30); do
    curl -sf --max-time 2 https://www.google.com >/dev/null 2>&1 && break
    sleep 2
  done
fi

case "${1:-start}" in
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "Stopped" || echo "Not running"
    exit 0
    ;;
  status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "recom tmux session running"
      tmux list-panes -t "$SESSION" -F '  #{pane_title}: PID #{pane_pid}' 2>/dev/null
    else
      echo "Not running"
    fi
    exit 0
    ;;
  logs)
    tmux attach -t "$SESSION" 2>/dev/null || echo "Not running"
    exit 0
    ;;
  restart)
    $0 stop
    sleep 1
    exec $0 start
    ;;
  start)
    # Keep machine awake permanently — prevent idle, display, disk, and system sleep
    # Run caffeinate as a proper child of the tmux session (see below)
    ;;
  *)
    echo "Usage: $0 [start|stop|status|logs|restart]"
    exit 1
    ;;
esac

# Kill existing session if any
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Ensure node 22 (node 23 breaks node-pty for claude-code-web)
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
nvm use 22 >/dev/null 2>&1 || true

# Fix node-pty spawn-helper permissions (npm strips +x on install)
chmod +x ~/.nvm/versions/node/v22.22.1/lib/node_modules/claude-code-web/node_modules/node-pty/prebuilds/darwin-*/spawn-helper 2>/dev/null || true

# Sync Python deps
cd "$DIR"
uv sync --no-dev 2>&1 | tail -3

# Create tmux session with dashboard pane
tmux new-session -d -s "$SESSION" -n main
tmux send-keys -t "$SESSION" "while true; do cd $DIR && uv run recom-dashboard; echo 'Dashboard crashed, restarting in 5s...'; sleep 5; done" Enter

# Cloudflare tunnel pane
tmux split-window -t "$SESSION" -v
tmux send-keys -t "$SESSION" "while true; do cloudflared tunnel run; echo 'Cloudflared crashed, restarting in 5s...'; sleep 5; done" Enter

# Telegram bot pane
tmux split-window -t "$SESSION" -v
tmux send-keys -t "$SESSION" "while true; do cd $DIR && set -a && source .env && set +a && uv run python scripts/telegram_bot.py; echo 'Telegram bot crashed, restarting in 5s...'; sleep 5; done" Enter

# Caffeinate pane — keeps machine awake as long as tmux is alive
tmux split-window -t "$SESSION" -v
tmux send-keys -t "$SESSION" "caffeinate -dims" Enter

# Even layout for main window
tmux select-layout -t "$SESSION" even-vertical

# Claude agent in its own window (SSH in and select this window to talk directly)
tmux new-window -t "$SESSION" -n claude
tmux send-keys -t "$SESSION:claude" "cd $DIR && claude --dangerously-skip-permissions" Enter

# Switch back to main window
tmux select-window -t "$SESSION:main"

echo ""
echo "=== Recom running (bare metal) ==="
echo "  Dashboard:  http://localhost:8000  → calyx.arthgupta.dev"
echo "  Telegram:   @ArthRecomBot"
echo ""
echo "Commands:"
echo "  ./start.sh logs      — attach to tmux"
echo "  ./start.sh status    — check services"
echo "  ./start.sh restart   — restart everything"
echo "  ./start.sh stop      — stop everything"
echo ""
echo "  tmux attach -t recom — view all panes"
echo ""
echo "Cron jobs (run separately):"
echo "  bash scripts/install_cron.sh   — installs 5 cron jobs"
echo "  crontab -l                     — verify installed"
echo ""
echo "Logs: state/{cron,daily,taste,tonight,ratings}.log"
