#!/usr/bin/env python3
"""
Browser-based end-to-end tests using Playwright.
Tests actual rendered output after JS executes.

Usage:
  python scripts/browser_test.py                  # test localhost:8000
  python scripts/browser_test.py --url https://recom.arthgupta.dev
  python scripts/browser_test.py --url https://recom.arthgupta.dev --email you@example.com
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from playwright.sync_api import sync_playwright, Page, BrowserContext

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
    msg = f"  FAIL {label}" + (f" — {detail}" if detail else "")
    print(msg)
    FAILURES.append(msg)


def check(label: str, condition: bool, detail: str = ""):
    if condition:
        ok(label)
    else:
        fail(label, detail)


def get_token(email: str) -> str:
    from recom.config import Settings
    from recom.db import Database
    s = Settings()
    db = Database(s.db_path)
    uid = db.create_user(email, "Browser Test")
    user = db.get_user(uid)
    return user["user_token"]


def test_unauthenticated(page: Page, base: str):
    print("\n-- Unauthenticated --")

    # Home page loads and has event cards OR empty state message
    # Use commit (HTML received) since page is large and slow to fully parse
    page.goto(base + "/", wait_until="commit", timeout=120000)
    page.wait_for_timeout(2000)

    # Nav exists
    check("/ has nav", page.locator("nav").count() > 0)

    # Check list view has content
    page.wait_for_function("typeof switchView === 'function'", timeout=30000)
    page.evaluate("switchView('list')")
    page.wait_for_timeout(500)
    list_html = page.locator("#list-view").inner_html()
    has_events = "day-group" in list_html or "event-card" in list_html
    has_empty = "No events to display" in list_html or "No runs yet" in list_html
    check("/ list view has events or empty state", has_events or has_empty,
          f"list-view content: {list_html[:200]!r}")

    if has_events:
        # Count visible event cards
        count = page.locator("#list-view .evt-card").count()
        check("/ shows >0 event cards", count > 0, f"got {count} cards")

    # Score slider default — should be 0 (show all)
    score_val = page.evaluate("document.getElementById('score-slider')?.value")
    check("score slider defaults to 0", str(score_val) == "0", f"got {score_val!r}")

    # Dist slider default — should be 50 (any distance)
    dist_val = page.evaluate("document.getElementById('dist-slider')?.value")
    check("dist slider defaults to 50", str(dist_val) == "50", f"got {dist_val!r}")

    # Other pages load without errors
    for path, label in [("/taste", "/taste"), ("/groups", "/groups"),
                        ("/attended", "/attended"), ("/login", "/login")]:
        page.goto(base + path, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        check(f"{label} loads (no 5xx)", "500" not in page.title() and "error" not in page.title().lower())
        check(f"{label} has nav", page.locator("nav").count() > 0)

    # Auth-required pages redirect
    for path in ["/venues", "/search", "/budget", "/travel", "/profile"]:
        resp = page.goto(base + path, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        final_url = page.url
        check(f"{path} redirects unauth", "/login" in final_url or "login" in page.content().lower(),
              f"ended at {final_url}")


def test_authenticated(page: Page, base: str, token: str):
    print(f"\n-- Authenticated (token={token}) --")

    # Set cookie
    page.context.add_cookies([{
        "name": "recom_token",
        "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    # Home page (slow — large page, use commit + generous wait)
    page.goto(base + "/", wait_until="commit", timeout=120000)
    page.wait_for_function("typeof switchView === 'function'", timeout=60000)
    page.evaluate("switchView('list')")
    page.wait_for_timeout(800)

    list_html = page.locator("#list-view").inner_html()
    has_events = "day-group" in list_html or "event-card" in list_html
    check("/ (authed) shows events", has_events, f"list-view: {list_html[:300]!r}")

    if has_events:
        count = page.locator("#list-view .evt-card").count()
        check("/ (authed) >0 visible cards", count > 0, f"got {count}")

    # Score slider still 0
    score_val = page.evaluate("document.getElementById('score-slider')?.value")
    check("/ (authed) score slider is 0", str(score_val) == "0", f"got {score_val!r}")

    # All nav links present
    nav_html = page.locator("nav").inner_html()
    for link in ["Search", "Venues", "Budget", "Travel", "Taste"]:
        check(f"nav has {link}", link in nav_html, f"nav: {nav_html[:300]!r}")

    # Auth pages load without errors AND have consistent nav
    for path, label in [
        ("/venues", "/venues"),
        ("/search", "/search"),
        ("/budget", "/budget"),
        ("/travel", "/travel"),
        ("/profile", "/profile"),
        ("/taste", "/taste"),
    ]:
        page.goto(base + path, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        title = page.title()
        content = page.content()
        check(f"{label} (authed) no 500", "500" not in title and "Internal Server Error" not in content,
              f"title={title!r}")
        check(f"{label} (authed) has nav", page.locator("nav").count() > 0)
        # Verify nav links are actually styled (nav CSS loaded correctly)
        nav_bg = page.evaluate("getComputedStyle(document.querySelector('nav')).backgroundColor")
        check(f"{label} nav is dark (not unstyled)", nav_bg not in ("rgba(0, 0, 0, 0)", ""),
              f"nav bg={nav_bg!r}")

    # Travel page: no JS errors from f-string collision
    page.goto(base + "/travel")
    page.wait_for_timeout(1000)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.wait_for_timeout(500)
    check("/travel no JS errors", len(errors) == 0, "; ".join(errors))

    # RSVP buttons visible on home
    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    page.wait_for_function("typeof switchView === 'function'", timeout=30000)
    page.evaluate("switchView('list')")
    page.wait_for_timeout(800)


def test_filters(page: Page, base: str, token: str):
    print("\n-- Filter behavior --")

    page.context.add_cookies([{
        "name": "recom_token",
        "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    page.wait_for_function("typeof switchView === 'function'", timeout=30000)
    page.evaluate("switchView('list')")
    page.wait_for_timeout(800)

    # Count events at score=0 (all)
    count_all = page.evaluate("""() => {
        const sl = document.getElementById('score-slider');
        if (sl) sl.value = 0;
        if (typeof applyFilters === 'function') applyFilters();
        else if (typeof buildListView === 'function') buildListView();
        return document.querySelectorAll('#list-view .day-group').length;
    }""")
    check("score=0 shows day groups", count_all > 0, f"got {count_all} day groups")

    # Score filter at 90 should show fewer
    count_high = page.evaluate("""() => {
        const sl = document.getElementById('score-slider');
        if (sl) { sl.value = 90; sl.dispatchEvent(new Event('input')); }
        if (typeof buildListView === 'function') buildListView();
        return document.querySelectorAll('#list-view .day-group').length;
    }""")
    check("score=90 shows ≤ score=0 results", count_high <= count_all,
          f"high={count_high} vs all={count_all}")

    # Reset slider
    page.evaluate("""() => {
        const sl = document.getElementById('score-slider');
        if (sl) { sl.value = 0; sl.dispatchEvent(new Event('input')); }
        if (typeof buildListView === 'function') buildListView();
    }""")

    # All views render without throwing
    for view in ["list", "timeline", "heat", "calendar"]:
        try:
            page.evaluate(f"switchView('{view}')")
            page.wait_for_timeout(300)
            ok(f"switchView('{view}') no crash")
        except Exception as e:
            fail(f"switchView('{view}')", str(e))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--email", default="test@example.com")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    token = get_token(args.email)
    print(f"\n=== Recom Browser Test ===")
    print(f"URL: {base}")
    print(f"Token: {token}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(90000)

        # Capture console errors
        js_errors: list[str] = []
        page.on("pageerror", lambda e: js_errors.append(str(e)))

        try:
            test_unauthenticated(page, base)
            test_authenticated(page, base, token)
            test_filters(page, base, token)
        except Exception:
            fail("test runner crashed", traceback.format_exc())
        finally:
            # Report any JS errors seen across all pages
            if js_errors:
                print(f"\n  WARN {len(js_errors)} JS error(s) detected:")
                for e in js_errors[:5]:
                    print(f"    {e}")
            browser.close()

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    if FAILURES:
        print("\nFailed checks:")
        for f_ in FAILURES:
            print(f_)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
