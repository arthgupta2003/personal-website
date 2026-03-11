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

echo "=== Recom Dashboard Smoke Test ==="
echo ""
echo "-- Unauthenticated --"

# Public pages
check "/" "200"
check "/landing" "200"
check "/admin" "200"
check "/admin/sources" "200"
# Admin pages that require auth (redirect to login → 307)
check "/admin/email-preview" "307" "/admin/email-preview (unauth→307)"
check "/admin/cal-preview" "307" "/admin/cal-preview (unauth→307)"
check "/admin/ml" "307" "/admin/ml (unauth→307)"
# Admin pages that are public (200)
check "/admin/pipeline" "200"
check "/admin/backtest" "200"
check "/admin/retros" "200"
check "/admin/ranking-analysis" "200"
check "/login" "200"
check "/feed.ics" "200"
check "/taste" "200"
check "/groups" "200"
check "/attended" "200"
check "/bucket-list" "200"
check "/variants" "200"
check "/v/calendar/dense" "200"
check "/v/calendar/magazine" "200"
check "/v/calendar/app" "200"
check "/v/taste/dense" "200"
check "/v/groups/dense" "200"
check "/v/profile/dense" "200"

# Auth-required pages (redirect without cookie)
check "/venues" "307" "/venues (unauth→307)"
check "/search" "307" "/search (unauth→307)"
check "/profile" "307" "/profile (unauth→307)"

# API endpoints (unauthenticated → 401)
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/search" \
  -H "Content-Type: application/json" -d '{"query":"jazz"}')
[ "$code" = "401" ] && { echo "  PASS /api/search unauth (401)"; ((PASS++)); } \
  || { echo "  FAIL /api/search unauth — expected 401, got $code"; ((FAIL++)); }

# POST /api/taste/vote unauthenticated → 401 or 307
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/taste/vote" \
  -H "Content-Type: application/json" -d '{"winner":"a","loser":"b"}')
[ "$code" = "401" ] || [ "$code" = "307" ] && { echo "  PASS /api/taste/vote unauth ($code)"; ((PASS++)); } \
  || { echo "  FAIL /api/taste/vote unauth — expected 401 or 307, got $code"; ((FAIL++)); }

# Authenticated flow (requires --email arg)
TEST_EMAIL="${1:-}"
if [ -n "$TEST_EMAIL" ]; then
  echo ""
  echo "-- Authenticated (email: $TEST_EMAIL) --"

  # Create/ensure user exists and get their token
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
    # All auth pages should now return 200
    for path in /venues /search /profile /taste /bucket-list; do
      check "$path" "200" "$path (authed)" "$TOKEN"
    done

    # Calendar should show user's data
    check "/" "200" "/ (authed)" "$TOKEN"

    # API search should work (no events yet = empty results, not 401)
    code=$(curl -s -o /dev/null -w "%{http_code}" \
      -b "recom_token=$TOKEN" \
      -X POST "$BASE/api/search" \
      -H "Content-Type: application/json" -d '{"query":"jazz"}')
    [ "$code" = "200" ] && { echo "  PASS /api/search authed (200)"; ((PASS++)); } \
      || { echo "  FAIL /api/search authed — expected 200, got $code"; ((FAIL++)); }
  fi
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] && exit 0 || exit 1
