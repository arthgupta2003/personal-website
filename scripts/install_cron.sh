#!/bin/bash
# Install cron jobs for recom:
#   - Weekly pipeline: Saturday 9am (discover + rank events)
#   - Daily email: Every day at 8am (send today's picks from latest run)

RECOM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV_PATH="$(which uv 2>/dev/null || echo "$HOME/.local/bin/uv")"

WEEKLY_CMD="0 9 * * 6 cd $RECOM_DIR && $UV_PATH run recom --all-users >> $RECOM_DIR/state/cron.log 2>&1"
DAILY_CMD="0 8 * * * cd $RECOM_DIR && $UV_PATH run recom-daily >> $RECOM_DIR/state/daily.log 2>&1"

# Remove existing recom cron entries and add new ones
(crontab -l 2>/dev/null | grep -v "uv run recom"; echo "$WEEKLY_CMD"; echo "$DAILY_CMD") | crontab -

echo "Cron jobs installed:"
echo ""
echo "  Weekly pipeline: Saturday 9am"
echo "    cd $RECOM_DIR && $UV_PATH run recom --all-users"
echo ""
echo "  Daily email: Every day at 8am"
echo "    cd $RECOM_DIR && $UV_PATH run recom-daily"
echo ""
echo "  Logs: $RECOM_DIR/state/cron.log, $RECOM_DIR/state/daily.log"
echo ""
echo "To verify: crontab -l"
echo "To remove: crontab -l | grep -v 'uv run recom' | crontab -"
