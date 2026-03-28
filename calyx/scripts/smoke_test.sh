#!/bin/bash
# Smoke test: verify all dashboard routes return expected HTTP codes
# Run after any dashboard change: bash scripts/smoke_test.sh
#
# Usage:
#   bash scripts/smoke_test.sh              # unauthenticated checks only
#   bash scripts/smoke_test.sh test@example.com   # also run auth flow (creates/fetches test user)

RECOM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE="http://localhost:8000"
PASS=0
FAIL=0

check() {
  local path="$1" expected="$2" label="${3:-$1}" cookie="${4:-}"
  local curl_args=(-s -o /dev/null -w "%{http_code}")
  [ -n "$cookie" ] && curl_args+=(-b "recom_token=$cookie")
  actual=$(curl "${curl_args[@]}" "$BASE$path")
  if [ "$actual" = "$expected" ]; then
    echo "  PASS $label ($actual)"
    ((PASS++))
  else
    echo "  FAIL $label — expected $expected, got $actual"
    ((FAIL++))
  fi
}

echo "=== Calyx Dashboard Smoke Test ==="
echo ""
echo "-- Unauthenticated --"

# Root redirects to /groups (group-first design)
check "/" "302" "/ (→/groups)"
check "/landing" "200"
check "/admin" "200"
check "/admin/sources" "200"
check "/login" "200"
check "/feed.ics" "200"
check "/groups" "200"
check "/calendar" "200"

# Single-event .ics download
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/event/nonexistent_event.ics")
[ "$code" = "404" ] && { echo "  PASS /event/{id}.ics 404 for unknown"; ((PASS++)); } \
  || { echo "  FAIL /event/{id}.ics — expected 404, got $code"; ((FAIL++)); }
SAMPLE_EID=$(cd "$RECOM_DIR" && uv run python -c "
from recom.db import Database; db = Database('recom.db')
r = db.conn.execute('SELECT event_id FROM events ORDER BY run_id DESC LIMIT 1').fetchone()
print(r[0] if r else '')
" 2>/dev/null)
if [ -n "$SAMPLE_EID" ]; then
  code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/event/$SAMPLE_EID.ics")
  [ "$code" = "200" ] && { echo "  PASS /event/{id}.ics (200)"; ((PASS++)); } \
    || { echo "  FAIL /event/{id}.ics — expected 200, got $code"; ((FAIL++)); }
fi

# Auth-required pages (redirect without cookie)
check "/taste-profile" "307" "/taste-profile (unauth→307)"

# Authenticated flow (requires --email arg)
TEST_EMAIL="${1:-}"
if [ -n "$TEST_EMAIL" ]; then
  echo ""
  echo "-- Authenticated (email: $TEST_EMAIL) --"

  TOKEN=$(cd "$RECOM_DIR" && uv run python -c "
from recom.config import Settings
from recom.db import Database
s = Settings()
db = Database(s.db_path)
uid = db.create_user('$TEST_EMAIL', 'Smoke Test')
user = db.get_user(uid)
print(user['user_token'])
" 2>/dev/null)

  if [ -z "$TOKEN" ]; then
    echo "  FAIL Could not get/create test user token"
    ((FAIL++))
  else
    echo "  INFO token=${TOKEN}"
    check "/taste-profile" "200" "/taste-profile (authed)" "$TOKEN"
    check "/groups" "200" "/groups (authed)" "$TOKEN"
    check "/calendar" "200" "/calendar (authed)" "$TOKEN"

    # Root (authed) redirects to /groups
    check "/" "302" "/ (authed→/groups)" "$TOKEN"

    # Per-user .ics download
    if [ -n "$SAMPLE_EID" ]; then
      code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/u/$TOKEN/event/$SAMPLE_EID.ics")
      [ "$code" = "200" ] && { echo "  PASS /u/{token}/event/{id}.ics (200)"; ((PASS++)); } \
        || { echo "  FAIL /u/{token}/event/{id}.ics — expected 200, got $code"; ((FAIL++)); }
      code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/u/$TOKEN/event/$SAMPLE_EID/added")
      [ "$code" = "200" ] && { echo "  PASS /u/{token}/event/{id}/added (200)"; ((PASS++)); } \
        || { echo "  FAIL /u/{token}/event/{id}/added — expected 200, got $code"; ((FAIL++)); }
    fi

    # Group create page
    check "/group/create" "200" "/group/create (authed)" "$TOKEN"
  fi
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] && exit 0 || exit 1
