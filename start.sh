#!/usr/bin/env bash
# Recom tmux startup — manages dashboard, tunnel, and claude-code-web
set -euo pipefail

SESSION="recom"
DIR="$(cd "$(dirname "$0")" && pwd)"

case "${1:-start}" in
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "Stopped $SESSION" || echo "Not running"
    exit 0
    ;;
  status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "Session '$SESSION' is running. Panes:"
      tmux list-panes -t "$SESSION" -F '  #{pane_index}: #{pane_title} (#{pane_current_command})'
      echo ""
      echo "Attach: tmux attach -t $SESSION"
    else
      echo "Not running. Start with: ./start.sh"
    fi
    exit 0
    ;;
  start) ;;
  *)
    echo "Usage: $0 [start|stop|status]"
    exit 1
    ;;
esac

# Kill existing session if any
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Source nvm for Node version management
export NVM_DIR="$HOME/.nvm"
NVM_INIT="source $NVM_DIR/nvm.sh && nvm use 22 > /dev/null 2>&1"

# Create session with dashboard pane
tmux new-session -d -s "$SESSION" -n main -c "$DIR"
tmux select-pane -t "$SESSION" -T "dashboard"
tmux send-keys -t "$SESSION" "cd $DIR && uv run recom-dashboard" Enter

# Split for tunnel
tmux split-window -t "$SESSION" -v -c "$DIR"
tmux select-pane -t "$SESSION" -T "tunnel"
tmux send-keys -t "$SESSION" "sleep 2 && cloudflared tunnel run recom-dashboard" Enter

# Split for claude-code-web
tmux split-window -t "$SESSION" -v -c "$DIR"
tmux select-pane -t "$SESSION" -T "code-web"
tmux send-keys -t "$SESSION" "$NVM_INIT && sleep 3 && npx claude-code-web@latest" Enter

# Even out the panes
tmux select-layout -t "$SESSION" even-vertical

echo "Started tmux session '$SESSION' with 3 panes:"
echo "  0: dashboard  (port 8000 → recom.arthgupta.dev)"
echo "  1: tunnel     (cloudflare → arthgupta.dev)"
echo "  2: code-web   (port 32352 → code.arthgupta.dev)"
echo ""
echo "Attach: tmux attach -t $SESSION"
echo "Token for code.arthgupta.dev will be printed in pane 2"
