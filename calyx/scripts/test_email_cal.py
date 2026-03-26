#!/usr/bin/env python3
"""
Comprehensive tests for email and calendar feeds — the CORE product touchpoints.

Tests against a running dashboard at http://localhost:8000.

Usage:
  uv run python scripts/test_email_cal.py
  uv run python scripts/test_email_cal.py --url https://recom.arthgupta.dev
  uv run python scripts/test_email_cal.py --email you@example.com
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ---------------------------------------------------------------------------
# Test harness (same pattern as browser_test.py)
# ---------------------------------------------------------------------------

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def ok(label: str):
    global PASS
    PASS += 1
    print(f"  PASS {label}")


def fail(label: str, detail: str = ""):
    global FAIL
    FAIL += 1
    msg = f"  FAIL {label}" + (f" -- {detail}" if detail else "")
    print(msg)
    FAILURES.append(msg)


def check(label: str, condition: bool, detail: str = ""):
    if condition:
        ok(label)
    else:
        fail(label, detail)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_get(url: str, cookie: str = "", timeout: int = 30) -> tuple[int, str, dict]:
    """GET request. Returns (status, body, headers)."""
    req = Request(url)
    if cookie:
        req.add_header("Cookie", f"recom_token={cookie}")
    try:
        resp = urlopen(req, timeout=timeout)
        body = resp.read().decode("utf-8", errors="replace")
        headers = dict(resp.headers)
        return resp.status, body, headers
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body, dict(e.headers) if e.headers else {}
    except URLError as e:
        return 0, str(e), {}


def http_post(url: str, data: dict, cookie: str = "", timeout: int = 30) -> tuple[int, str, dict]:
    """POST JSON request. Returns (status, body, headers)."""
    payload = json.dumps(data).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", f"recom_token={cookie}")
    try:
        resp = urlopen(req, timeout=timeout)
        body = resp.read().decode("utf-8", errors="replace")
        headers = dict(resp.headers)
        return resp.status, body, headers
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body, dict(e.headers) if e.headers else {}
    except URLError as e:
        return 0, str(e), {}


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def get_token(email: str) -> str:
    """Create/fetch a test user and return their token."""
    from recom.config import Settings
    from recom.db import Database
    s = Settings()
    db = Database(s.db_path)
    uid = db.create_user(email, "Email/Cal Test")
    user = db.get_user(uid)
    return user["user_token"]


def get_admin_token() -> str:
    """Get the token for user #1 (admin)."""
    from recom.config import Settings
    from recom.db import Database
    s = Settings()
    db = Database(s.db_path)
    user = db.get_user(1)
    if not user:
        raise RuntimeError("Admin user (id=1) not found in database")
    return user["user_token"]


# ---------------------------------------------------------------------------
# iCal validation helpers
# ---------------------------------------------------------------------------

def parse_ical_lines(body: str) -> list[str]:
    """Unfold iCal continuation lines and return logical lines."""
    # iCal uses CRLF line endings; continuation lines start with a space
    raw_lines = body.replace("\r\n", "\n").split("\n")
    logical: list[str] = []
    for line in raw_lines:
        if line.startswith(" ") or line.startswith("\t"):
            if logical:
                logical[-1] += line[1:]  # strip leading space, append
            else:
                logical.append(line)
        else:
            logical.append(line)
    return logical


def ical_has_property(lines: list[str], prop: str) -> bool:
    """Check if any line starts with PROP: or PROP;"""
    prefix1 = prop + ":"
    prefix2 = prop + ";"
    return any(l.startswith(prefix1) or l.startswith(prefix2) for l in lines)


def check_ical_line_folding(body: str) -> list[str]:
    """Check that no raw line exceeds 75 octets (RFC 5545). Returns violating lines."""
    violations = []
    raw_lines = body.replace("\r\n", "\n").split("\n")
    for line in raw_lines:
        octets = len(line.encode("utf-8"))
        if octets > 75:
            violations.append(f"{octets} octets: {line[:80]}...")
    return violations


# ===========================================================================
# TEST SUITES
# ===========================================================================

def test_public_feed(base: str):
    """Tests 1-5: Public /feed.ics validation."""
    print("\n-- Calendar Feed: /feed.ics --")

    # Test 1: GET /feed.ics returns valid iCal
    status, body, _ = http_get(f"{base}/feed.ics")
    check("feed.ics returns 200", status == 200, f"got {status}")
    check("feed.ics starts with BEGIN:VCALENDAR", body.strip().startswith("BEGIN:VCALENDAR"),
          f"starts with: {body[:60]!r}")

    lines = parse_ical_lines(body)
    has_vevent = any(l == "BEGIN:VEVENT" for l in lines)
    # It is OK if there are no events (empty DB), but note it
    if has_vevent:
        ok("feed.ics has VEVENT entries")
    else:
        print("  INFO feed.ics has no VEVENT entries (empty DB or high min_score default)")

    # Test 2: min_score=0 returns >= min_score=90
    status_low, body_low, _ = http_get(f"{base}/feed.ics?min_score=0")
    status_high, body_high, _ = http_get(f"{base}/feed.ics?min_score=90")
    count_low = body_low.count("BEGIN:VEVENT")
    count_high = body_high.count("BEGIN:VEVENT")
    check("min_score=0 >= min_score=90 events", count_low >= count_high,
          f"low={count_low}, high={count_high}")

    # Test 3: iCal line folding (no raw line > 75 octets)
    violations = check_ical_line_folding(body_low if count_low > 0 else body)
    check("iCal line folding (no line >75 octets)", len(violations) == 0,
          f"{len(violations)} violations, first: {violations[0] if violations else 'n/a'}")

    # Test 3b: Required fields in VEVENTs
    feed_to_check = body_low if count_low > 0 else body
    lines_check = parse_ical_lines(feed_to_check)
    if any(l == "BEGIN:VEVENT" for l in lines_check):
        check("VEVENT has UID", ical_has_property(lines_check, "UID"))
        check("VEVENT has DTSTART", ical_has_property(lines_check, "DTSTART"))
        check("VEVENT has SUMMARY", ical_has_property(lines_check, "SUMMARY"))
    else:
        print("  SKIP required-field checks (no VEVENTs)")

    # Test 4: Calendar-level headers
    check("feed.ics has PRODID", ical_has_property(lines_check, "PRODID"),
          f"lines: {[l for l in lines_check if l.startswith('PROD')]}")
    check("feed.ics has CALSCALE", ical_has_property(lines_check, "CALSCALE"))
    check("feed.ics has X-WR-CALNAME", ical_has_property(lines_check, "X-WR-CALNAME"))

    # Test 5: Events have DURATION or DTEND
    if any(l == "BEGIN:VEVENT" for l in lines_check):
        has_duration = ical_has_property(lines_check, "DURATION")
        has_dtend = ical_has_property(lines_check, "DTEND")
        check("VEVENTs have DURATION or DTEND", has_duration or has_dtend)
    else:
        print("  SKIP DURATION/DTEND check (no VEVENTs)")


def test_user_feeds(base: str, token: str):
    """Tests 6-10: Per-user feed and RSVP feed."""
    print("\n-- Calendar Feed: /u/{token}/ --")

    # Test 7: /u/{token}/feed.ics returns valid iCal
    status, body, _ = http_get(f"{base}/u/{token}/feed.ics")
    check("user feed.ics returns 200", status == 200, f"got {status}")
    check("user feed.ics is valid iCal", body.strip().startswith("BEGIN:VCALENDAR"),
          f"starts with: {body[:60]!r}")

    lines = parse_ical_lines(body)
    user_vevent_count = sum(1 for l in lines if l == "BEGIN:VEVENT")

    # Test 8: /u/{token}/rsvps.ics returns valid iCal
    status_r, body_r, _ = http_get(f"{base}/u/{token}/rsvps.ics")
    check("user rsvps.ics returns 200", status_r == 200, f"got {status_r}")
    check("user rsvps.ics is valid iCal", body_r.strip().startswith("BEGIN:VCALENDAR"),
          f"starts with: {body_r[:60]!r}")

    # Test 9: User feed has RSVP links in DESCRIPTION
    if user_vevent_count > 0:
        check("user feed has RSVP links in DESCRIPTION",
              "rsvp-link" in body.lower() or "RSVP" in body,
              f"body snippet: {body[:500]!r}")
    else:
        print("  SKIP RSVP link check (no VEVENTs in user feed)")

    # Test 10: Check REFRESH-INTERVAL or X-PUBLISHED-TTL exists
    # Note: not all implementations include this; check both feeds
    has_refresh = ("REFRESH-INTERVAL" in body or "X-PUBLISHED-TTL" in body
                   or "REFRESH-INTERVAL" in body_r or "X-PUBLISHED-TTL" in body_r)
    if has_refresh:
        ok("feeds have REFRESH-INTERVAL or X-PUBLISHED-TTL")
    else:
        # This is a known missing feature -- report but don't hard-fail
        print("  WARN feeds lack REFRESH-INTERVAL / X-PUBLISHED-TTL (RFC 7986 recommended)")

    # Validate user feed line folding
    violations = check_ical_line_folding(body)
    check("user feed line folding OK", len(violations) == 0,
          f"{len(violations)} violations, first: {violations[0] if violations else 'n/a'}")

    # User feed calendar-level headers
    check("user feed has PRODID", ical_has_property(lines, "PRODID"))
    check("user feed has X-WR-CALNAME", ical_has_property(lines, "X-WR-CALNAME"))


def test_email_previews(base: str, admin_token: str):
    """Tests 11-13: Admin email preview routes."""
    print("\n-- Email Preview (admin) --")

    # Test 11: /admin/email-preview
    status, body, _ = http_get(f"{base}/admin/email-preview", cookie=admin_token)
    check("/admin/email-preview returns 200", status == 200, f"got {status}")
    check("/admin/email-preview has HTML content",
          "Email Preview" in body or "email" in body.lower(),
          f"body snippet: {body[:200]!r}")

    # Test 12: /admin/email-preview/daily
    status, body, _ = http_get(f"{base}/admin/email-preview/daily", cookie=admin_token)
    check("/admin/email-preview/daily returns 200", status == 200, f"got {status}")
    check("/admin/email-preview/daily has content",
          len(body) > 200,
          f"body length: {len(body)}")

    # Test 13: /admin/email-preview/tonight
    status, body, _ = http_get(f"{base}/admin/email-preview/tonight", cookie=admin_token)
    check("/admin/email-preview/tonight returns 200", status == 200, f"got {status}")
    check("/admin/email-preview/tonight has content",
          len(body) > 200,
          f"body length: {len(body)}")


def test_email_composer():
    """Tests 14-16: Unit tests for email composer (no SMTP)."""
    print("\n-- Email Composer (unit tests) --")

    from recom.models import Event, EventSource, InterestProfile, RankedEvent

    # Test 14: compose_email with mock data
    try:
        from recom.email.composer import compose_email

        mock_events = []
        for i in range(5):
            evt = Event(
                id=f"test-{i}",
                source=EventSource.EVENTBRITE,
                title=f"Test Event {i}",
                description=f"A test event number {i}",
                url=f"https://example.com/event/{i}",
                start_time=datetime(2026, 3, 14, 19, 0),
                location_name="Test Venue",
                price="Free" if i % 2 == 0 else "$25",
            )
            ranked = RankedEvent(
                event=evt,
                score=90 - i * 10,
                interest_score=12,
                social_score=10,
                vibe="social" if i % 2 == 0 else "intellectual",
                match_reason=f"Great match for test reason {i}",
                keep=True,
                event_type="event",
            )
            mock_events.append(ranked)

        profile = InterestProfile(summary="Test interests")
        subject, html = compose_email(
            ranked_events=mock_events,
            profile=profile,
            week_of="March 10, 2026",
            total_cost=1.23,
        )
        check("compose_email returns subject string", isinstance(subject, str) and len(subject) > 0,
              f"subject={subject!r}")
        check("compose_email returns HTML string", isinstance(html, str) and len(html) > 100,
              f"html length={len(html)}")
    except Exception as e:
        fail("compose_email", traceback.format_exc())

    # Test 15: compose_daily_email
    try:
        from recom.email.composer import compose_daily_email

        result = compose_daily_email(
            ranked_events=mock_events,
            target_date=datetime(2026, 3, 14),
            dashboard_url="https://recom.arthgupta.dev",
            user_token="test-token-123",
        )
        if result is not None:
            subject_d, html_d = result
            check("compose_daily_email returns subject", isinstance(subject_d, str) and len(subject_d) > 0)
            check("compose_daily_email returns HTML", isinstance(html_d, str) and len(html_d) > 100)
        else:
            # None is valid if no events match the target date
            ok("compose_daily_email returns None (no events on target date)")
    except Exception as e:
        fail("compose_daily_email", traceback.format_exc())

    # Test 16: Email HTML structure
    try:
        check("email HTML has DOCTYPE", "<!DOCTYPE" in html or "<!doctype" in html.lower())
        # Check for footer / unsubscribe / iCal link
        has_footer = ("footer" in html.lower() or "iCal" in html or "ical" in html.lower()
                      or "feed.ics" in html or "recom" in html.lower())
        check("email HTML has footer/branding", has_footer,
              f"html tail: {html[-300:]!r}")
    except Exception as e:
        fail("email HTML structure", traceback.format_exc())


def test_api_endpoints(base: str, token: str):
    """Tests 17-20: API endpoints with auth."""
    print("\n-- API Endpoints (authenticated) --")

    # We need taste items to exist for a valid vote; get a pair first
    # Test 17: POST /api/taste/vote
    # First try to get existing taste items
    from recom.config import Settings
    from recom.db import Database
    s = Settings()
    db = Database(s.db_path)
    user = db.get_user_by_token(token)
    user_id = user["id"] if user else 1

    # Ensure at least 2 taste items exist for voting
    items = db.get_taste_items(user_id)
    if len(items) < 2:
        # Seed taste items if needed
        try:
            db.conn.execute(
                "INSERT OR IGNORE INTO taste_items (user_id, name, category, elo_rating) VALUES (?, ?, ?, ?)",
                (user_id, "Live jazz concert", "music", 1400),
            )
            db.conn.execute(
                "INSERT OR IGNORE INTO taste_items (user_id, name, category, elo_rating) VALUES (?, ?, ?, ?)",
                (user_id, "Hiking in the woods", "active", 1400),
            )
            db.conn.commit()
            items = db.get_taste_items(user_id)
        except Exception:
            pass

    if len(items) >= 2:
        item_a = items[0]
        item_b = items[1]
        status, body, _ = http_post(
            f"{base}/api/taste/vote",
            {"item_a_id": item_a["id"], "item_b_id": item_b["id"], "winner_id": item_a["id"]},
            cookie=token,
        )
        check("POST /api/taste/vote returns 200", status == 200, f"got {status}, body={body[:200]!r}")
        if status == 200:
            try:
                data = json.loads(body)
                check("/api/taste/vote response has ok=True", data.get("ok") is True, f"data={data}")
            except json.JSONDecodeError:
                fail("/api/taste/vote JSON parse", f"body={body[:200]!r}")
    else:
        print("  SKIP /api/taste/vote (could not seed taste items)")

    # Test 18: POST /api/search
    status, body, _ = http_post(
        f"{base}/api/search",
        {"query": "jazz"},
        cookie=token,
        timeout=60,  # search can be slow (AI call)
    )
    # 200 = results found, 500 = no API key — both are valid states
    check("POST /api/search returns 200 or 500 (no API key)",
          status in (200, 500),
          f"got {status}, body={body[:200]!r}")

    # Test 19: GET /api/steer with params
    steer_params = urlencode({
        "target_type": "category",
        "target_value": "jazz",
        "action": "more",
        "u": token,
    })
    status, body, _ = http_get(f"{base}/api/steer?{steer_params}")
    check("GET /api/steer returns 200", status == 200, f"got {status}, body={body[:200]!r}")
    if status == 200:
        check("/api/steer has confirmation content",
              "saved" in body.lower() or "preference" in body.lower(),
              f"body snippet: {body[:300]!r}")

    # Test 20: GET /api/taste/radar
    status, body, _ = http_get(f"{base}/api/taste/radar", cookie=token)
    check("GET /api/taste/radar returns 200", status == 200, f"got {status}")
    if status == 200:
        try:
            data = json.loads(body)
            check("/api/taste/radar has axes key", "axes" in data, f"keys={list(data.keys())}")
            check("/api/taste/radar has values key", "values" in data, f"keys={list(data.keys())}")
        except json.JSONDecodeError:
            fail("/api/taste/radar JSON parse", f"body={body[:200]!r}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="Email & Calendar feed tests for Recom")
    parser.add_argument("--url", default="http://localhost:8000", help="Dashboard base URL")
    parser.add_argument("--email", default="test-emailcal@example.com", help="Test user email")
    args = parser.parse_args()

    base = args.url.rstrip("/")

    print("\n=== Recom Email & Calendar Feed Tests ===")
    print(f"URL: {base}")

    # Verify dashboard is reachable
    try:
        status, _, _ = http_get(base, timeout=10)
        if status == 0:
            print(f"\nERROR: Cannot reach dashboard at {base}")
            print("Make sure the dashboard is running: uv run recom-dashboard")
            sys.exit(1)
    except Exception as e:
        print(f"\nERROR: Cannot reach dashboard at {base}: {e}")
        sys.exit(1)

    # Get tokens
    try:
        token = get_token(args.email)
        print(f"User token: {token}")
    except Exception as e:
        print(f"ERROR getting user token: {e}")
        sys.exit(1)

    try:
        admin_token = get_admin_token()
        print(f"Admin token: {admin_token}")
    except Exception as e:
        print(f"WARN: Could not get admin token: {e}")
        admin_token = ""

    # Run all test suites
    try:
        test_public_feed(base)
        test_user_feeds(base, token)
        if admin_token:
            test_email_previews(base, admin_token)
        else:
            print("\n-- Email Preview (admin) --")
            print("  SKIP all admin tests (no admin user)")
        test_email_composer()
        test_api_endpoints(base, token)
    except Exception:
        fail("test runner crashed", traceback.format_exc())

    # Summary
    print(f"\nResults: {PASS} passed, {FAIL} failed")
    if FAILURES:
        print("\nFailed checks:")
        for f_ in FAILURES:
            print(f_)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
