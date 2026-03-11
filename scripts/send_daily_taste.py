#!/usr/bin/env python3
"""Send the daily Elo taste matchup email.

Cron: 0 9 * * 1-5  (9am Mon-Fri)
Usage:
  uv run python scripts/send_daily_taste.py          # user 1
  uv run python scripts/send_daily_taste.py --user 2
  uv run python scripts/send_daily_taste.py --all-users
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from recom.config import Settings
from recom.db import Database
from recom.email.sender import send_daily_matchup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_for_user(user_id: int) -> bool:
    settings = Settings()
    db = Database(settings.db_path)

    user = db.get_user(user_id)
    if not user or not user.get("email"):
        logger.info("User %d has no email — skipping", user_id)
        return False

    # Seed taste items if none exist yet
    db.seed_taste_items(user_id)

    pair = db.get_taste_matchup_pair(user_id)
    if not pair:
        logger.info("No taste pair available for user %d", user_id)
        return False

    item_a, item_b = pair
    token = user.get("user_token", "")

    try:
        send_daily_matchup(
            email=user["email"],
            token=token,
            item_a=item_a,
            item_b=item_b,
            dashboard_url=settings.dashboard_url,
            settings=settings,
        )
        logger.info("Taste matchup email sent to %s: %r vs %r", user["email"], item_a["label"], item_b["label"])
        return True
    except Exception as exc:
        logger.error("Failed to send taste matchup to %s: %s", user["email"], exc)
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Send daily taste matchup email")
    parser.add_argument("--user", type=int, default=1)
    parser.add_argument("--all-users", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    db = Database(settings.db_path)

    if args.all_users:
        users = db.get_users(active_only=True)
        for u in users:
            run_for_user(u["id"])
    else:
        run_for_user(args.user)
