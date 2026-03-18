#!/usr/bin/env python3
"""
Generate visual demo walkthroughs of key user stories using Playwright.
Each demo is a sequence of annotated screenshots saved as an HTML gallery.

Usage:
  uv run python scripts/generate_demos.py                    # all demos
  uv run python scripts/generate_demos.py --story group-join # specific story
  uv run python scripts/generate_demos.py --url https://recom.arthgupta.dev
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from playwright.sync_api import sync_playwright, Page

DEMOS_DIR = Path(__file__).parent.parent / "demos"
DEMOS_DIR.mkdir(exist_ok=True)


def get_token(email: str = "test@example.com") -> str:
    from recom.config import Settings
    from recom.db import Database
    s = Settings()
    db = Database(s.db_path)
    uid = db.create_user(email, "Demo User")
    user = db.get_user(uid)
    return user["user_token"]


def get_invite_code(group_id: int = 1) -> str:
    from recom.config import Settings
    from recom.db import Database
    s = Settings()
    db = Database(s.db_path)
    g = db.get_group_by_id(group_id)
    return g.get("invite_code", "") if g else ""


class DemoRecorder:
    """Records a sequence of screenshots with captions into an HTML gallery."""

    def __init__(self, name: str, title: str):
        self.name = name
        self.title = title
        self.steps: list[dict] = []

    def step(self, page: Page, caption: str, highlight_selector: str = ""):
        """Take a screenshot with a caption."""
        # Optionally highlight an element
        if highlight_selector:
            try:
                page.evaluate(f"""(() => {{
                    const el = document.querySelector('{highlight_selector}');
                    if (el) {{
                        el.style.outline = '3px solid #ef4444';
                        el.style.outlineOffset = '2px';
                        el.style.transition = 'outline 0.2s';
                    }}
                }})()""")
                page.wait_for_timeout(200)
            except Exception:
                pass

        screenshot = page.screenshot(full_page=False)
        b64 = base64.b64encode(screenshot).decode()

        # Remove highlight
        if highlight_selector:
            try:
                page.evaluate(f"""(() => {{
                    const el = document.querySelector('{highlight_selector}');
                    if (el) {{ el.style.outline = ''; el.style.outlineOffset = ''; }}
                }})()""")
            except Exception:
                pass

        self.steps.append({
            "caption": caption,
            "image_b64": b64,
            "url": page.url,
        })

    def save(self):
        """Generate an HTML gallery file."""
        steps_html = ""
        for i, s in enumerate(self.steps, 1):
            steps_html += f"""
            <div class="step">
                <div class="step-header">
                    <span class="step-num">{i}</span>
                    <span class="step-caption">{s['caption']}</span>
                </div>
                <div class="step-url">{s['url']}</div>
                <img src="data:image/png;base64,{s['image_b64']}" alt="Step {i}">
            </div>
            """

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Demo: {self.title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 40px 20px; }}
  h1 {{ text-align: center; font-size: 28px; margin-bottom: 8px; color: white; }}
  .meta {{ text-align: center; color: #64748b; font-size: 14px; margin-bottom: 40px; }}
  .steps {{ max-width: 800px; margin: 0 auto; }}
  .step {{ margin-bottom: 40px; background: #1e293b; border-radius: 12px; overflow: hidden; border: 1px solid #334155; }}
  .step-header {{ padding: 16px 20px; display: flex; align-items: center; gap: 12px; }}
  .step-num {{ width: 32px; height: 32px; border-radius: 50%; background: #4f46e5; color: white; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 14px; flex-shrink: 0; }}
  .step-caption {{ font-size: 16px; font-weight: 600; color: #f1f5f9; }}
  .step-url {{ padding: 0 20px 12px; font-size: 12px; color: #64748b; font-family: monospace; }}
  .step img {{ width: 100%; display: block; border-top: 1px solid #334155; }}
  .nav {{ text-align: center; margin-top: 40px; }}
  .nav a {{ color: #818cf8; text-decoration: none; font-size: 14px; }}
</style>
</head>
<body>
  <h1>{self.title}</h1>
  <p class="meta">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} &middot; {len(self.steps)} steps</p>
  <div class="steps">
    {steps_html}
  </div>
  <div class="nav"><a href="index.html">&larr; All demos</a></div>
</body>
</html>"""

        path = DEMOS_DIR / f"{self.name}.html"
        path.write_text(html)
        print(f"  Saved: {path} ({len(self.steps)} steps)")
        return path


# ---------------------------------------------------------------------------
# Demo stories
# ---------------------------------------------------------------------------

def demo_group_join(page: Page, base: str, token: str):
    """New user joins a group via invite link."""
    demo = DemoRecorder("group-join", "New User Joins Group via Invite Link")

    invite_code = get_invite_code(1)
    if not invite_code:
        print("  SKIP: no group/invite code found")
        return

    # Step 1: User receives invite link and opens it
    page.goto(f"{base}/group/1/join/{invite_code}", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    demo.step(page, "User opens invite link — sees group page with join form")

    # Step 2: Highlight the join form
    demo.step(page, "Join form: just name + email, one tap to join", "form")

    demo.save()


def demo_group_page(page: Page, base: str, token: str):
    """Authenticated group page with all features."""
    demo = DemoRecorder("group-page", "Group Page — Members, Events, Invite")

    page.context.add_cookies([{
        "name": "recom_token", "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    page.goto(f"{base}/group/1", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    demo.step(page, "Group page — members list and group name")

    # Scroll to events
    page.evaluate("window.scrollBy(0, 400)")
    page.wait_for_timeout(500)
    demo.step(page, "Add events and view upcoming plans")

    # Scroll to invite section
    page.evaluate("window.scrollBy(0, 400)")
    page.wait_for_timeout(500)
    demo.step(page, "Invite friends — copy link or share via native share sheet")

    demo.save()


def demo_groups_listing(page: Page, base: str, token: str):
    """Groups listing page."""
    demo = DemoRecorder("groups-listing", "Groups Listing — Your Groups First")

    page.context.add_cookies([{
        "name": "recom_token", "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    page.goto(f"{base}/groups", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    demo.step(page, "Groups page — your groups shown first")

    # Scroll to see activity
    page.evaluate("window.scrollBy(0, 400)")
    page.wait_for_timeout(500)
    demo.step(page, "Friend activity and upcoming events below")

    demo.save()


def demo_profile(page: Page, base: str, token: str):
    """Profile page with settings."""
    demo = DemoRecorder("profile", "Profile — Settings and Preferences")

    page.context.add_cookies([{
        "name": "recom_token", "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    page.goto(f"{base}/profile", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    demo.step(page, "Profile page — name, email, location settings")

    # Scroll to see more
    page.evaluate("window.scrollBy(0, 400)")
    page.wait_for_timeout(500)
    demo.step(page, "Calendar feeds and connected services")

    demo.save()


def demo_calendar(page: Page, base: str, token: str):
    """Calendar/events view."""
    demo = DemoRecorder("calendar", "Calendar — Browse Upcoming Events")

    page.context.add_cookies([{
        "name": "recom_token", "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    page.goto(f"{base}/v/calendar/dense", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    demo.step(page, "Dense calendar view — all upcoming events ranked by score")

    # Scroll
    page.evaluate("window.scrollBy(0, 400)")
    page.wait_for_timeout(500)
    demo.step(page, "Events with scores, vibes, and match reasons")

    demo.save()


def demo_search(page: Page, base: str, token: str):
    """Search for events."""
    demo = DemoRecorder("search", "Search — Find Events by Description")

    page.context.add_cookies([{
        "name": "recom_token", "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    page.goto(f"{base}/search", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    demo.step(page, "Search page — natural language event search")

    demo.save()


def demo_venues(page: Page, base: str, token: str):
    """Venues page."""
    demo = DemoRecorder("venues", "Venues — Your Venue Taste Profile")

    page.context.add_cookies([{
        "name": "recom_token", "value": token,
        "domain": base.replace("https://", "").replace("http://", "").split("/")[0],
        "path": "/",
    }])

    page.goto(f"{base}/venues", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    demo.step(page, "Venues page — venue recommendations based on your taste")

    page.evaluate("window.scrollBy(0, 400)")
    page.wait_for_timeout(500)
    demo.step(page, "Venue cards with ratings and categories")

    demo.save()


def demo_admin_sources(page: Page, base: str, token: str):
    """Admin sources page."""
    demo = DemoRecorder("admin-sources", "Admin — Scraper Health Dashboard")

    page.goto(f"{base}/admin/sources", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    demo.step(page, "Source health — success rates, sparklines, cache age")

    page.evaluate("window.scrollBy(0, 300)")
    page.wait_for_timeout(500)
    demo.step(page, "Per-source breakdown with event counts")

    demo.save()


STORIES = {
    "group-join": demo_group_join,
    "group-page": demo_group_page,
    "groups-listing": demo_groups_listing,
    "profile": demo_profile,
    "calendar": demo_calendar,
    "search": demo_search,
    "venues": demo_venues,
    "admin-sources": demo_admin_sources,
}


def generate_index():
    """Generate an index.html linking all demos."""
    demos = sorted(DEMOS_DIR.glob("*.html"))
    demos = [d for d in demos if d.name != "index.html"]

    cards = ""
    for d in demos:
        name = d.stem.replace("-", " ").title()
        cards += f"""
        <a href="{d.name}" class="card">
            <span class="card-title">{name}</span>
            <span class="card-arrow">&rarr;</span>
        </a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Recom Demos</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 40px 20px; }}
  h1 {{ text-align: center; font-size: 32px; margin-bottom: 8px; color: white; }}
  .meta {{ text-align: center; color: #64748b; font-size: 14px; margin-bottom: 40px; }}
  .cards {{ max-width: 600px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }}
  .card {{ display: flex; justify-content: space-between; align-items: center; padding: 20px 24px; background: #1e293b; border-radius: 12px; border: 1px solid #334155; text-decoration: none; color: #f1f5f9; transition: border-color 0.2s; }}
  .card:hover {{ border-color: #4f46e5; }}
  .card-title {{ font-size: 16px; font-weight: 600; }}
  .card-arrow {{ font-size: 20px; color: #64748b; }}
</style>
</head>
<body>
  <h1>Recom Demos</h1>
  <p class="meta">Visual walkthroughs of key user stories &middot; Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  <div class="cards">
    {cards}
  </div>
</body>
</html>"""

    path = DEMOS_DIR / "index.html"
    path.write_text(html)
    print(f"Index: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--email", default="test@example.com")
    parser.add_argument("--story", help="Run specific story only")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    token = get_token(args.email)

    stories = STORIES
    if args.story:
        if args.story not in STORIES:
            print(f"Unknown story: {args.story}")
            print(f"Available: {', '.join(STORIES.keys())}")
            sys.exit(1)
        stories = {args.story: STORIES[args.story]}

    print(f"\n=== Generating Recom Demos ===")
    print(f"URL: {base}")
    print(f"Stories: {len(stories)}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Mobile-ish viewport for realistic screenshots
        context = browser.new_context(viewport={"width": 420, "height": 900})
        page = context.new_page()
        page.set_default_timeout(30000)

        for name, fn in stories.items():
            print(f"Recording: {name}")
            try:
                fn(page, base, token)
            except Exception as e:
                print(f"  ERROR: {e}")

        browser.close()

    generate_index()
    print(f"\nDone! Open demos/index.html to view.")


if __name__ == "__main__":
    main()
