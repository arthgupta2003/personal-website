#!/usr/bin/env bash
# Recom startup — bare metal with tmux
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

SESSION="recom"

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
  start) ;;
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
tmux send-keys -t "$SESSION" "cd $DIR && uv run recom-dashboard" Enter

# Cloudflare tunnel pane
tmux split-window -t "$SESSION" -v
tmux send-keys -t "$SESSION" "cloudflared tunnel run" Enter

# Claude Code Web pane (needs node 22)
tmux split-window -t "$SESSION" -v
tmux send-keys -t "$SESSION" "cd $DIR && ~/.nvm/versions/node/v22.22.1/bin/cc-web --port 32352" Enter

# Even layout
tmux select-layout -t "$SESSION" even-vertical

echo ""
echo "=== Recom running (bare metal) ==="
echo "  Dashboard:  http://localhost:8000  → recom.arthgupta.dev"
echo "  Code Web:   http://localhost:32352 → code.arthgupta.dev"
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
