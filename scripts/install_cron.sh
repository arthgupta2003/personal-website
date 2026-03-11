#!/bin/bash
# Install cron jobs for recom.
# Usage: bash scripts/install_cron.sh [pipeline_hour] [daily_hour] [dow]
#   pipeline_hour: 0-23 (default 9)
#   daily_hour:    0-23 (default 8)
#   dow:           0=Sun 1=Mon ... 6=Sat (default 6=Sat)

RECOM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV_PATH="$(which uv 2>/dev/null || echo "$HOME/.local/bin/uv")"

PIPELINE_HOUR="${1:-9}"
DAILY_HOUR="${2:-8}"
DOW="${3:-6}"

# Weekly pipeline (discover + rank events)
WEEKLY_CMD="0 ${PIPELINE_HOUR} * * ${DOW} cd ${RECOM_DIR} && ${UV_PATH} run recom --all-users >> ${RECOM_DIR}/state/cron.log 2>&1"

# Daily digest email (send today's picks from latest run)
DAILY_CMD="0 ${DAILY_HOUR} * * * cd ${RECOM_DIR} && ${UV_PATH} run recom-daily >> ${RECOM_DIR}/state/daily.log 2>&1"

# Daily taste matchup email (9am Mon-Fri)
TASTE_CMD="0 9 * * 1-5 cd ${RECOM_DIR} && ${UV_PATH} run python scripts/send_daily_taste.py --all-users >> ${RECOM_DIR}/state/taste.log 2>&1"

# Tonight email (4pm Fri + Sat — last-minute picks)
TONIGHT_CMD="0 16 * * 5,6 cd ${RECOM_DIR} && ${UV_PATH} run python scripts/send_tonight.py --all-users >> ${RECOM_DIR}/state/tonight.log 2>&1"

# Post-event rating emails (10pm daily)
RATINGS_CMD="0 22 * * * cd ${RECOM_DIR} && ${UV_PATH} run python scripts/send_ratings.py --send >> ${RECOM_DIR}/state/ratings.log 2>&1"

# Remove existing recom cron entries and install all fresh
(crontab -l 2>/dev/null | grep -v "recom\|send_daily_taste\|send_tonight\|send_ratings"; \
  echo "$WEEKLY_CMD"; \
  echo "$DAILY_CMD"; \
  echo "$TASTE_CMD"; \
  echo "$TONIGHT_CMD"; \
  echo "$RATINGS_CMD") | crontab -

# Ensure log dir exists
mkdir -p "${RECOM_DIR}/state"

echo "Cron jobs installed:"
echo ""
echo "  Weekly pipeline (DOW=${DOW} at ${PIPELINE_HOUR}:00):"
echo "    uv run recom --all-users"
echo ""
echo "  Daily digest (${DAILY_HOUR}:00 every day):"
echo "    uv run recom-daily"
echo ""
echo "  Daily taste matchup (9am Mon-Fri):"
echo "    uv run python scripts/send_daily_taste.py --all-users"
echo ""
echo "  Tonight picks (4pm Fri+Sat):"
echo "    uv run python scripts/send_tonight.py --all-users"
echo ""
echo "  Post-event ratings (10pm daily):"
echo "    uv run python scripts/send_ratings.py --send"
echo ""
echo "  Logs: ${RECOM_DIR}/state/{cron,daily,taste,tonight,ratings}.log"
echo ""
echo "To verify: crontab -l"
echo "To remove: crontab -l | grep -v 'recom\|send_daily_taste\|send_tonight\|send_ratings' | crontab -"
