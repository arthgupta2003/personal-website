from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import anthropic

from recom.config import Settings
from recom.db import Database
from recom.email.composer import compose_email
from recom.email.sender import send_email
from recom.events.aggregator import discover_all_events
from recom.extract.interests import extract_interests, load_manual_keywords
from recom.ingest.gmail import get_newsletter_emails
from recom.ingest.spotify import get_spotify_activity
from recom.ingest.youtube import get_youtube_activity
from recom.models import CostRecord, RawActivity
from recom.ranking.bucket_list import load_bucket_list, pick_suggestions
from recom.ranking.ranker import rank_events

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_for_user(settings: Settings, db: Database, client: anthropic.Anthropic, user_id: int = 1):
    """Run the full pipeline for a single user."""
    user = db.get_user(user_id)
    user_label = f"{user['name'] or user['email']}" if user else "default"
    logger.info(f"Running pipeline for user {user_id} ({user_label})")

    # Per-user overrides (token files, interests, location)
    if user:
        if user.get("spotify_token_file"):
            settings.spotify_token_file = user["spotify_token_file"]
        if user.get("youtube_token_file"):
            settings.youtube_token_file = user["youtube_token_file"]
        if user.get("gmail_token_file"):
            settings.gmail_token_file = user["gmail_token_file"]
        if user.get("interests_file"):
            settings.interests_file = user["interests_file"]
        if user.get("bucket_list_file"):
            settings.bucket_list_file = user["bucket_list_file"]
        if user.get("location_query"):
            settings.location_query = user["location_query"]
        if user.get("zip_code"):
            settings.zip_code = user["zip_code"]
        email_to = user["email"]
    else:
        email_to = settings.email_to

    run_id = db.create_run(model=settings.claude_model, user_id=user_id)
    logger.info(f"Starting run #{run_id}")

    all_costs: list[CostRecord] = []

    # === Steps 1+3 in parallel: Ingest + Discover events concurrently ===
    # Event discovery is IO-bound and doesn't depend on interest extraction,
    # so we run ingest + discovery at the same time for ~2x speedup.

    import concurrent.futures

    logger.info("Step 1+3: Ingesting activity data + discovering events in parallel...")

    # Run ingest sources in parallel threads (all IO-bound)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        yt_future = pool.submit(get_youtube_activity, settings)
        sp_future = pool.submit(get_spotify_activity, settings)
        nl_future = pool.submit(get_newsletter_emails, settings)

        # While ingest runs, start event discovery (async, in main thread)
        # We need Spotify artists for Ticketmaster, but we can start discovery
        # with empty artists and let Ticketmaster use its own defaults
        yt_activity = yt_future.result()
        sp_activity = sp_future.result()
        newsletters = nl_future.result()

    logger.info(f"  Ingest: YouTube={len(yt_activity)}, Spotify={len(sp_activity)}, Newsletters={len(newsletters)}")

    # Save ingest stats for dashboard audit
    yt_subs = sum(1 for i in yt_activity if i.category == "subscription")
    yt_liked = sum(1 for i in yt_activity if i.category == "liked_video")
    yt_watch = sum(1 for i in yt_activity if i.category == "watch_history")
    db.save_ingest_stat(run_id, "YouTube", len(yt_activity),
                        f"{yt_subs} subscriptions, {yt_liked} liked videos, {yt_watch} watch history")
    sp_artists_count = sum(1 for i in sp_activity if i.category == "top_artist")
    sp_tracks = sum(1 for i in sp_activity if i.category == "top_track")
    sp_recent = sum(1 for i in sp_activity if i.category == "recently_played")
    db.save_ingest_stat(run_id, "Spotify", len(sp_activity),
                        f"{sp_artists_count} top artists, {sp_tracks} top tracks, {sp_recent} recently played")
    db.save_ingest_stat(run_id, "Newsletters", len(newsletters), f"{len(newsletters)} emails scanned")

    activity = RawActivity(youtube=yt_activity, spotify=sp_activity)

    # Collect unique Spotify artist names for concert matching
    artist_names: set[str] = set()
    for item in sp_activity:
        if item.category == "top_artist":
            artist_names.add(item.title)
        elif item.description and item.description.startswith("by "):
            for artist in item.description[3:].split(", "):
                artist = artist.strip()
                if artist:
                    artist_names.add(artist)
    spotify_artists = list(artist_names)

    # === Step 2+3: Extract interests + discover events in parallel ===
    logger.info("Step 2: Extracting interests...")
    manual_keywords = db.get_manual_interest_keywords(user_id) or load_manual_keywords(settings.interests_file)
    taste_top = db.get_taste_items(user_id)[:15] if hasattr(db, "get_taste_items") else None

    cached_profile = db.get_cached_interest_profile(max_age_days=7)

    # Run interest extraction and event discovery concurrently
    async def _discover():
        return await discover_all_events(
            settings, newsletters=newsletters,
            claude_client=client, claude_model=settings.claude_model,
            spotify_artists=spotify_artists,
        )

    logger.info("Step 3: Discovering events (parallel with interest extraction)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        # Event discovery in a thread (runs its own asyncio loop)
        event_future = pool.submit(asyncio.run, _discover())

        # Interest extraction in main thread
        if cached_profile and not yt_activity and not sp_activity:
            logger.info("  Using cached interest profile")
            profile = cached_profile
        else:
            profile, interest_cost = extract_interests(
                activity, manual_keywords, client, settings.claude_model,
                taste_top=taste_top,
            )
            all_costs.append(interest_cost)
            db.save_cost(run_id, interest_cost)
            logger.info(f"  Extracted {len(profile.interests)} interests (${interest_cost.cost_usd:.4f})")

        # Wait for event discovery
        events, source_stats, newsletter_costs, source_durations = event_future.result()

    db.save_interest_profile(run_id, profile)

    for stat in source_stats:
        db.save_source_stat(run_id, stat, duration_seconds=source_durations.get(stat.source_name))
        if not stat.error_message and stat.events_found > 0:
            db.update_source_cache(stat.source_name, stat.events_found)
    for cost in newsletter_costs:
        all_costs.append(cost)
        db.save_cost(run_id, cost)

    db.save_events(run_id, events)
    logger.info(f"  Found {len(events)} events from {len(source_stats)} sources")
    for stat in source_stats:
        err = f" (ERROR: {stat.error_message})" if stat.error_message else ""
        logger.info(f"    {stat.source_name}: {stat.events_found}{err}")

    # === Step 4: Rank events ===
    logger.info("Step 4: Ranking events with Claude...")
    if events:
        # Get friend RSVPs for group members (to boost events friends are attending)
        friend_rsvps = db.get_friend_rsvps_for_run(user_id, run_id)
        if friend_rsvps:
            logger.info("  Found friend RSVPs for %d events", len(friend_rsvps))

        steering = db.get_steering(user_id)

        # Build calendar density context: user's upcoming RSVPs this week
        calendar_context = db.get_calendar_context(user_id)

        ranked, rank_costs = rank_events(
            profile, events, client, settings.claude_model,
            spotify_artists=spotify_artists,
            taste_top=taste_top,
            home_lat=settings.latitude,
            home_lon=settings.longitude,
            friend_rsvps=friend_rsvps if friend_rsvps else None,
            steering=steering if steering else None,
            calendar_context=calendar_context or None,
        )
        for cost in rank_costs:
            all_costs.append(cost)
            db.save_cost(run_id, cost)
        db.save_rankings(run_id, ranked)
        kept = sum(1 for r in ranked if r.keep)
        logger.info(f"  Ranked {len(ranked)} events, {kept} recommended")
    else:
        ranked = []
        logger.info("  No events to rank")

    # === Step 4.5: Bucket list suggestions ===
    # Prefer DB-backed bucket list; fall back to flat file
    bucket_items = db.get_bucket_list_activities(user_id) or load_bucket_list(settings.bucket_list_file)
    bucket_suggestions: list[dict] = []
    if bucket_items:
        logger.info("Step 4.5: Picking bucket list suggestions...")
        bucket_suggestions, bucket_cost = pick_suggestions(
            bucket_items, client, settings.claude_model,
        )
        all_costs.append(bucket_cost)
        db.save_cost(run_id, bucket_cost)
        logger.info(f"  {len(bucket_suggestions)} suggestions picked (${bucket_cost.cost_usd:.4f})")

    # === Step 5: Email ===
    recommended = [r for r in ranked if r.keep and r.score >= 25]
    total_cost = sum(c.cost_usd for c in all_costs)
    logger.info(f"Total AI cost: ${total_cost:.4f}")

    if recommended:
        logger.info("Step 5: Sending email...")
        week_of = datetime.now().strftime("%B %d, %Y")
        total_tokens_in = sum(c.tokens_in for c in all_costs)
        total_tokens_out = sum(c.tokens_out for c in all_costs)
        subject, html_body = compose_email(
            recommended, profile, week_of, total_cost,
            tokens_in=total_tokens_in, tokens_out=total_tokens_out,
            bucket_suggestions=bucket_suggestions,
            run_id=run_id,
            home_lat=settings.latitude,
            home_lon=settings.longitude,
        )

        if settings.smtp_password:
            settings.email_to = email_to
            send_email(subject, html_body, settings)
            logger.info(f"  Sent to {email_to}")
        else:
            email_path = Path(settings.state_dir) / f"email_{run_id}.html"
            email_path.write_text(html_body)
            logger.info(f"  SMTP not configured — saved to {email_path}")
    else:
        logger.info("Step 5: No events to recommend — skipping email")

    logger.info(f"Run #{run_id} complete for {user_label}")
    return run_id


def main():
    settings = Settings()

    # --- Hard-fail on required config ---
    if not settings.anthropic_api_key:
        logger.error("Missing RECOM_ANTHROPIC_API_KEY")
        sys.exit(1)

    # Ensure state dirs exist
    Path(settings.state_dir).mkdir(exist_ok=True)
    Path(settings.state_dir, "tokens").mkdir(exist_ok=True)

    db = Database(settings.db_path)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Check for --user flag or run for all active users
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=int, help="Run for specific user ID")
    parser.add_argument("--all-users", action="store_true", help="Run for all active users")
    args, _ = parser.parse_known_args()

    if args.all_users:
        users = db.get_users(active_only=True)
        logger.info(f"Running for {len(users)} active users")
        for user in users:
            try:
                run_for_user(settings, db, client, user_id=user["id"])
            except Exception:
                logger.exception(f"Pipeline failed for user {user['id']} ({user['email']})")
    else:
        user_id = args.user or 1
        run_for_user(settings, db, client, user_id=user_id)

    db.close()
    logger.info("All done. Dashboard: uv run recom-dashboard")


if __name__ == "__main__":
    main()
