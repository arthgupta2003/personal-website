#!/bin/bash
# Install cron jobs for calyx.
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
WEEKLY_CMD="0 ${PIPELINE_HOUR} * * ${DOW} cd ${RECOM_DIR} && ${UV_PATH} run calyx --all-users >> ${RECOM_DIR}/state/cron.log 2>&1"

# Daily digest email (send today's picks from latest run)
DAILY_CMD="0 ${DAILY_HOUR} * * * cd ${RECOM_DIR} && ${UV_PATH} run calyx-daily >> ${RECOM_DIR}/state/daily.log 2>&1"

# Weekend preview (Thursday 6pm — plan your weekend)
WEEKEND_CMD="0 18 * * 4 cd ${RECOM_DIR} && ${UV_PATH} run python scripts/send_weekend_preview.py --all-users >> ${RECOM_DIR}/state/weekend.log 2>&1"

# Tonight email (4pm Fri + Sat — last-minute impulse picks)
TONIGHT_CMD="0 16 * * 5,6 cd ${RECOM_DIR} && ${UV_PATH} run python scripts/send_tonight.py --all-users >> ${RECOM_DIR}/state/tonight.log 2>&1"

# Post-event rating emails (10pm daily)
RATINGS_CMD="0 22 * * * cd ${RECOM_DIR} && ${UV_PATH} run python scripts/send_ratings.py --send >> ${RECOM_DIR}/state/ratings.log 2>&1"

# Admin digest (Sunday 10am — source health, retros, TODOs)
ADMIN_CMD="0 10 * * 0 cd ${RECOM_DIR} && ${UV_PATH} run python scripts/send_admin_digest.py >> ${RECOM_DIR}/state/admin.log 2>&1"

# Remove existing calyx cron entries (also legacy "recom" entries) and install all fresh
(crontab -l 2>/dev/null | grep -v "calyx\|recom\|send_daily_taste\|send_tonight\|send_ratings\|send_weekend\|send_admin"; \
  echo "$WEEKLY_CMD"; \
  echo "$DAILY_CMD"; \
  echo "$WEEKEND_CMD"; \
  echo "$TONIGHT_CMD"; \
  echo "$RATINGS_CMD"; \
  echo "$ADMIN_CMD") | crontab -

# Ensure log dir exists
mkdir -p "${RECOM_DIR}/state"

echo "Cron jobs installed:"
echo ""
echo "  Weekly pipeline (DOW=${DOW} at ${PIPELINE_HOUR}:00):"
echo "    uv run calyx --all-users"
echo ""
echo "  Daily digest (${DAILY_HOUR}:00 every day):"
echo "    uv run calyx-daily"
echo ""
echo "  Weekend preview (Thursday 6pm):"
echo "    uv run python scripts/send_weekend_preview.py --all-users"
echo ""
echo "  Tonight picks (4pm Fri+Sat):"
echo "    uv run python scripts/send_tonight.py --all-users"
echo ""
echo "  Post-event ratings (10pm daily):"
echo "    uv run python scripts/send_ratings.py --send"
echo ""
echo "  Logs: ${RECOM_DIR}/state/{cron,daily,weekend,tonight,ratings}.log"
echo ""
echo "To verify: crontab -l"
echo "To remove: crontab -l | grep -v "calyx\|send_daily_taste\|send_weekend\|send_tonight\|send_ratings' | crontab -"
