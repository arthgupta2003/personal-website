#!/usr/bin/env python3
"""
Browser-based end-to-end tests using Playwright.
Tests actual rendered output after JS executes.

Usage:
  python scripts/browser_test.py                  # test localhost:8000
  python scripts/browser_test.py --url https://calyx.arthgupta.dev
  python scripts/browser_test.py --url https://calyx.arthgupta.dev --email you@example.com
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

    # Root redirects to /groups
    resp = page.goto(base + "/", wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(2000)
    check("/ redirects (unauth)", "/groups" in page.url or "/landing" in page.url, f"ended at {page.url}")

    # Groups page has nav
    check("/groups has nav", page.locator("nav").count() > 0)

    # Groups page has content
    content = page.content()
    has_groups = "group" in content.lower()
    check("/groups has group content", has_groups, f"content snippet: {content[:300]!r}")

    # Core pages load without errors
    for path, label in [("/groups", "/groups"), ("/calendar", "/calendar (discover)"), ("/login", "/login")]:
        page.goto(base + path, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        check(f"{label} loads (no 5xx)", "500" not in page.title() and "error" not in page.title().lower())
        check(f"{label} has nav", page.locator("nav").count() > 0)

    # Auth-required pages redirect
    for path in ["/profile"]:
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

    # Root redirects to /groups even when authed
    page.goto(base + "/", wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(2000)
    check("/ (authed) redirects to /groups", "/groups" in page.url, f"ended at {page.url}")

    # Groups page loads
    content = page.content()
    check("/groups (authed) no 500", "500" not in page.title() and "Internal Server Error" not in content)
    check("/groups (authed) has nav", page.locator("nav").count() > 0)

    # Nav has exactly 3 core links: Groups, Discover, Profile
    nav_html = page.locator("nav").inner_html()
    for link in ["Groups", "Discover", "Profile"]:
        check(f"nav has {link}", link in nav_html, f"nav: {nav_html[:300]!r}")

    # Core auth pages load without errors and have consistent nav
    for path, label in [
        ("/calendar", "/calendar (discover)"),
        ("/profile", "/profile"),
        ("/groups", "/groups"),
    ]:
        page.goto(base + path, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        title = page.title()
        content = page.content()
        check(f"{label} (authed) no 500", "500" not in title and "Internal Server Error" not in content,
              f"title={title!r}")
        check(f"{label} (authed) has nav", page.locator("nav").count() > 0)
        nav_bg = page.evaluate("getComputedStyle(document.querySelector('nav')).backgroundColor")
        check(f"{label} nav is dark (not unstyled)", nav_bg not in ("rgba(0, 0, 0, 0)", ""),
              f"nav bg={nav_bg!r}")


def test_group_page(page: Page, base: str, token: str):
    """Test group pages including the share/join flow."""
    print("\n-- Group pages --")

    page.context.add_cookies([{
        "name": "recom_token",
        "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    # /groups loads
    page.goto(base + "/groups", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    content = page.content()
    check("/groups loads (no 500)", "500" not in page.title() and "Internal Server Error" not in content)

    # Try to load group/1 (may or may not exist)
    page.goto(base + "/group/1", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    title = page.title()
    status = page.evaluate("document.querySelector('h1')?.textContent || ''")

    if "not found" in status.lower() or "404" in title:
        ok("/group/1 returns 404 (no group)")
    else:
        content = page.content()
        check("/group/1 loads (no 500)", "Internal Server Error" not in content)
        has_share = "Share" in content or "Copy" in content or "group-link" in content
        check("/group/1 has share section", has_share, f"content snippet: {content[:500]!r}")

    # Admin pages still load
    for path in ["/admin", "/admin/sources"]:
        page.goto(base + path, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        title = page.title()
        content = page.content()
        check(f"{path} loads (no 500)", "500" not in title and "Internal Server Error" not in content,
              f"title={title!r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--email", default="test@example.com")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    token = get_token(args.email)
    print(f"\n=== Calyx Browser Test ===")
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
            test_group_page(page, base, token)
        except Exception:
            fail("test runner crashed", traceback.format_exc())
        finally:
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
