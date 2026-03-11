"""Claude-based event ranking -- the core scoring module."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import anthropic

from recom.config import estimate_cost
from recom.models import CostRecord, Event, InterestProfile, RankedEvent, haversine_km

logger = logging.getLogger(__name__)

_BATCH_SIZE = 40

# Cambridge, MA default home
_HOME_LAT = 42.3736
_HOME_LON = -71.1097


_haversine_km = haversine_km  # local alias for backward compat


def _is_during_work_hours(event: Event) -> bool:
    """Return True if the event starts Mon-Fri 9am-5pm (user is unavailable)."""
    if not event.start_time:
        return False
    if event.is_online:
        return False
    dt = event.start_time
    # Normalize to naive local time if needed
    if dt.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo
            dt = dt.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
        except Exception:
            dt = dt.replace(tzinfo=None)
    # Mon=0 ... Fri=4
    if dt.weekday() >= 5:  # weekend — always OK
        return False
    hour = dt.hour + dt.minute / 60
    return 9.0 <= hour < 17.0


def _distance_logistics_adjustment(km: float) -> float:
    """Return logistics_score adjustment based on distance from home.

    Applied AFTER Claude scores so we get deterministic distance penalties
    regardless of whether Claude knows the exact location.
    """
    if km < 1.5:
        return 2.5   # walking distance — trivial to attend
    elif km < 3:
        return 1.5   # very close, short bike/walk
    elif km < 8:
        return 0.5   # normal Cambridge/Somerville range
    elif km < 20:
        return -0.5  # farther Boston neighborhoods
    elif km < 40:
        return -1.5  # suburbs
    else:
        return -3.0  # very far out

# Dimension weights per vibe — different events evaluated differently.
#
# "social" (concerts, sports, bars, festivals): friend-bringability and
#   social atmosphere are the point. Interest is a bonus.
# "intellectual" (lectures, seminars, talks, niche workshops): interest match
#   and quality/discovery are the point. Low friend score shouldn't kill it.
# "mixed" (hackathons, interactive workshops, meetups): balanced.
_VIBE_WEIGHTS: dict[str, dict[str, float]] = {
    "social": {
        "interest_score": 1.5,
        "social_score": 2.5,
        "urgency_score": 1.5,
        "logistics_score": 1.5,
        "friend_score": 2.5,
        "discovery_score": 0.5,
        "quality_score": 1.0,
    },
    "intellectual": {
        "interest_score": 3.5,
        "social_score": 0.5,
        "urgency_score": 1.5,
        "logistics_score": 1.5,
        "friend_score": 0.5,
        "discovery_score": 1.5,
        "quality_score": 2.0,
    },
    "mixed": {
        "interest_score": 2.5,
        "social_score": 1.5,
        "urgency_score": 1.5,
        "logistics_score": 1.5,
        "friend_score": 1.5,
        "discovery_score": 1.0,
        "quality_score": 1.0,
    },
}


def _compute_score(dims: dict[str, float], vibe: str = "mixed") -> float:
    """Compute weighted total score (0-100) from raw dimension scores."""
    weights = _VIBE_WEIGHTS.get(vibe, _VIBE_WEIGHTS["mixed"])
    max_raw = 15.0 * sum(weights.values())
    raw = sum(dims.get(k, 0) * w for k, w in weights.items())
    return round(raw * 100.0 / max_raw, 1)

# ---------------------------------------------------------------------------
# Ranking prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an event-ranking engine for a young professional in the \
Boston/Cambridge area (02139, Cambridge). Score EVERY event and decide \
which are worth attending. The user has disposable income — never penalize \
for price.

SCORING — 7 dimensions, each 0-15, total 0-105 (normalized to 0-100):

1. interest_score (0-15): Match to user's interests?
   - 0-3: No connection
   - 4-7: Tangential (same broad category)
   - 8-12: Moderate match on one interest
   - 13-15: Strong match on multiple interests
   ARTIST BOOST: If event features an artist from the SPOTIFY ARTISTS list, \
add +5 (can exceed 15). Concerts by artists the user listens to are \
a signal but should not dominate — the user also wants non-music events.

2. social_score (0-15): How fun/social is the activity itself?
   - 0-3: Solo, passive (webinars, solo gallery, reading groups)
   - 4-7: Some social element (lectures w/ Q&A, group classes)
   - 8-11: Actively social (live sports, concerts, comedy, bar events, \
meetups, game nights). You'd invite friends.
   - 12-15: Peak social (hackathons, festivals, pub crawls, unique group \
experiences)
   Live sports, concerts, comedy = 8+ minimum. A college basketball game \
is more fun than a random webinar.

3. urgency_score (0-15): How scarce / FOMO-worthy?
   - 0-3: Always available (weekly trivia, permanent exhibits, rolling enrollment)
   - 4-7: Periodic (monthly meetup, seasonal event)
   - 8-11: Specific date / limited (touring artist, one-weekend fest, visiting \
speaker, limited-ticket workshop)
   - 12-15: Once-in-a-lifetime (farewell tour, historic event, one-night premiere)

4. logistics_score (0-15): How easy to actually attend?
   User lives in Cambridge (02139), works 9-5 Mon-Fri.
   - 0-3: Hard (far, during work hours, needs car, advance registration)
   - 4-7: Moderate (20-30 min transit, weeknight early start)
   - 8-11: Easy (short T ride, evening/weekend, drop-in)
   - 12-15: Trivial (Cambridge/Somerville, weekend/after-work, no registration)
   Schedule: Weekday evenings 5:30pm+ or weekends → boost. \
Workday 9-5 Mon-Fri → penalize.

5. friend_score (0-15): Could you text the group chat "who's in?"
   - 0-3: Hard sell (grad seminars, niche hobby, support groups)
   - 4-7: Some friends might come (museum, interesting talk, food tasting)
   - 8-11: Easy sell (concerts, sports, comedy, bars, outdoor activities)
   - 12-15: Everyone wants in (major artists, festivals, bucket-list experiences)

6. discovery_score (0-15): Does this expand horizons or reinforce the familiar?
   - 0-3: More of the same (another event in a category user already does weekly)
   - 4-7: Slight variation on known interests
   - 8-11: Adjacent — could open up a new hobby or scene (ceramics workshop \
for a maker, salsa class for a music lover, rock climbing for someone active)
   - 12-15: Genuinely novel experience the user wouldn't find on their own \
(unique cultural events, unusual workshops, emerging scenes)
   Reward serendipity. The user already knows their interests — help them \
discover things they didn't know they'd love.

7. quality_score (0-15): Venue/organizer quality and crowd fit?
   - 0-3: Unknown organizer, sketchy venue, likely poor crowd fit (random \
Eventbrite, unclear details, spam-looking)
   - 4-7: Decent but unremarkable (standard meetup, generic venue)
   - 8-11: Good venue or established organizer (Sinclair, Regattabar, MIT \
Media Lab, ICA, established meetup groups, known promoters)
   - 12-15: Elite venue + perfect crowd fit (premier Boston venues, \
prestigious institutions, events curated for young professionals)
   Known venues in Cambridge/Somerville/Boston = quality signal. University \
events at MIT/Harvard = strong quality. Major ticketed shows = vetted.

DO NOT compute a total score — just return the 7 raw dimension scores. \
We compute the weighted total server-side using different weights per vibe.

CALIBRATION — USE THE FULL 0-15 RANGE:
- DO NOT cluster all scores in the 4-8 range. This defeats the purpose.
- A boring generic webinar with no interest match should get 0-2 on most dimensions.
- A must-attend concert by a favorite artist should get 13-15 on interest, social, urgency.
- Be DECISIVE: if something is mediocre, score it low (0-4). If it's great, score it high (12-15).
- Aim for a roughly uniform distribution: ~30% of events scoring below 4, ~40% in 5-10, ~30% above 10 on each dimension.
- The top ~20% of events should have at least one dimension at 13+.
- The bottom ~30% should have most dimensions below 5.

DIVERSITY — avoid recommending too many events of the same type:
- Live music concerts should NOT dominate. The user likes music but also wants \
comedy, talks, meetups, outdoor activities, workshops, food events, sports, etc.
- For the 5th+ concert/music event in a batch, apply a -3 penalty to interest_score \
(music fatigue — the user can only attend so many shows per week).
- Boost non-music events that are genuinely interesting: a great comedy show, \
an unusual workshop, or a unique social event should score just as high as a concert.
- If you see many similar events (e.g., 10 indie rock shows), only score the top \
2-3 highly. The rest should get diminishing interest_scores.

OUTDOOR & ALWAYS-AVAILABLE activities:
- Some events have no fixed date — they are always-available spots (hiking trails, \
parks, kayaking, outdoor recreation). These are VALUABLE especially in \
spring/summer/fall. Score them generously on discovery_score (they get you outside) \
and social_score (great for bringing friends on a weekend).
- A beautiful hike or kayak trip is worth recommending alongside concerts and talks.
- These should score 50-70+ overall — they are real recommendations, not filler.

CATEGORIZATION — two fields per event:
  "event_type":
    - "event": one-off events, concerts, talks, shows, workshops
    - "club": recurring clubs, leagues, meetup groups, memberships
    - "class": classes, courses, multi-session programs
  "vibe" — THIS IS CRITICAL, it determines which weight vector we use:
    - "social": concerts, sports, comedy, bar events, festivals, group outings, \
parties — the point is the social experience
    - "intellectual": lectures, seminars, talks, exhibits, academic events, \
niche workshops — the point is the content/learning
    - "mixed": hackathons, interactive workshops, meetups, classes — both \
social and intellectual elements

Return ONLY valid JSON (no markdown fences):
{
  "ranked_events": [
    {
      "event_id": "...",
      "interest_score": 13,
      "social_score": 10,
      "urgency_score": 10,
      "logistics_score": 12,
      "friend_score": 11,
      "discovery_score": 5,
      "quality_score": 11,
      "event_type": "event",
      "vibe": "social",
      "match_reason": "Jazz trio at Regattabar — strong interest match, \
easy walk from Central Sq, one-night show, great date or group outing."
    }
  ]
}

Score EVERY event. Do not skip any.
"""


def _get_season_context() -> str:
    """Return a short seasonal context string for Boston."""
    month = datetime.now().month
    if month in (12, 1, 2):
        season = "winter"
        hint = "Cold and often snowy. Prioritize indoor events (concerts, museums, lectures, cooking classes, bars). Outdoor events need very good reason."
    elif month in (3, 4, 5):
        season = "spring"
        hint = "Weather improving. Start surfacing outdoor options (especially late April/May). Cherry blossoms, farmers markets, running clubs become attractive. Evenings still cool."
    elif month in (6, 7, 8):
        season = "summer"
        hint = "Warm and sunny. Strongly boost outdoor events: concerts, harbor islands, rooftop bars, beer gardens, outdoor festivals, hiking. Locals leave city on weekends — unique indoor events still valuable."
    else:  # 9, 10, 11
        season = "fall"
        hint = "Perfect outdoor weather through October. Boost foliage hikes, apple picking, outdoor markets, fall festivals. November: shift back to indoor."
    return f"Current season: {season} (month {month}) in Boston. {hint}"


def _build_user_message(
    profile: InterestProfile,
    events: list[Event],
    spotify_artists: list[str] | None = None,
    taste_top: list[dict] | None = None,
    home_lat: float = _HOME_LAT,
    home_lon: float = _HOME_LON,
    calendar_context: str | None = None,
) -> str:
    """Build the user-turn message containing the profile and event batch."""

    # Profile section
    profile_lines = [
        "=== USER INTEREST PROFILE ===",
        f"Summary: {profile.summary}",
        "",
        "Interests (topic | confidence | signals):",
    ]
    for interest in profile.interests:
        signals = ", ".join(interest.source_signals) if interest.source_signals else "n/a"
        profile_lines.append(
            f"  - {interest.topic} | {interest.confidence:.2f} | {signals}"
        )

    if taste_top:
        profile_lines.append("")
        profile_lines.append("=== TASTE STACK (Elo-ranked activity preferences, highest first) ===")
        profile_lines.append("Boost interest_score for events matching these high-ranked activity types:")
        for item in taste_top[:10]:
            profile_lines.append(f"  - {item['label']} (Elo: {round(item['elo_rating'])}, category: {item['category']})")

    if spotify_artists:
        profile_lines.append("")
        profile_lines.append("=== SPOTIFY ARTISTS (user listens to these — boost concerts!) ===")
        profile_lines.append(", ".join(spotify_artists))

    # Season context
    profile_lines.append("")
    profile_lines.append("=== SEASONALITY ===")
    profile_lines.append(_get_season_context())

    # Calendar density context (upcoming confirmed plans)
    if calendar_context:
        profile_lines.append("")
        profile_lines.append("=== USER'S UPCOMING PLANS (already confirmed) ===")
        profile_lines.append(calendar_context)
        profile_lines.append("Avoid over-recommending on days already full (2+ RSVPs). Surface good options for free slots.")

    # Events section
    event_dicts: list[dict] = []
    for ev in events:
        # Compute distance if geocoded
        if ev.lat is not None and ev.lon is not None and not ev.is_online:
            km = round(_haversine_km(home_lat, home_lon, ev.lat, ev.lon), 1)
            dist_str = f"{km}km from home"
        elif ev.is_online:
            dist_str = "online"
        else:
            dist_str = "unknown distance"

        event_dicts.append(
            {
                "id": ev.id,
                "title": ev.title,
                "description": ev.description[:300] if ev.description else "",
                "start_time": ev.start_time.isoformat() if ev.start_time else None,
                "end_time": ev.end_time.isoformat() if ev.end_time else None,
                "location": ev.location_name,
                "distance": dist_str,
                "is_online": ev.is_online,
                "price": ev.price,
                "category": ev.category,
                "organizer": ev.organizer,
                "attendee_count": ev.attendee_count,
            }
        )

    return (
        "\n".join(profile_lines)
        + "\n\n=== EVENTS TO RANK ===\n"
        + json.dumps(event_dicts, indent=2, default=str)
        + "\n\nScore every event above. Return only JSON."
    )


def _parse_ranked_batch(
    raw_text: str,
    events_by_id: dict[str, Event],
    home_lat: float = _HOME_LAT,
    home_lon: float = _HOME_LON,
) -> list[RankedEvent]:
    """Parse Claude's JSON response into RankedEvent objects."""

    # Strip markdown code fences if present
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to recover truncated JSON by finding the last complete object
        logger.warning("JSON truncated, attempting recovery...")
        last_brace = text.rfind("}")
        if last_brace > 0:
            # Find the end of the last complete array element
            truncated = text[:last_brace + 1]
            # Close any open array/object
            if truncated.count("[") > truncated.count("]"):
                truncated += "]"
            if truncated.count("{") > truncated.count("}"):
                truncated += "}"
            try:
                data = json.loads(truncated)
            except json.JSONDecodeError:
                logger.error("Failed to recover truncated JSON")
                return []
        else:
            logger.error("Failed to parse ranking response as JSON")
            return []

    ranked: list[RankedEvent] = []
    for item in data.get("ranked_events", []):
        event_id = item.get("event_id", "")
        event = events_by_id.get(event_id)
        if event is None:
            logger.warning("Claude returned unknown event_id: %s -- skipping", event_id)
            continue

        # Apply distance-based logistics adjustment if geocoded
        raw_logistics = float(item.get("logistics_score", 0))
        if event.lat is not None and event.lon is not None and not event.is_online:
            km = _haversine_km(home_lat, home_lon, event.lat, event.lon)
            adj = _distance_logistics_adjustment(km)
            raw_logistics = max(0.0, min(15.0, raw_logistics + adj))

        dims = {
            "interest_score": float(item.get("interest_score", 0)),
            "social_score": float(item.get("social_score", 0)),
            "urgency_score": float(item.get("urgency_score", 0)),
            "logistics_score": raw_logistics,
            "friend_score": float(item.get("friend_score", 0)),
            "discovery_score": float(item.get("discovery_score", 0)),
            "quality_score": float(item.get("quality_score", 0)),
        }
        vibe = item.get("vibe", "mixed")
        if vibe not in ("social", "intellectual", "mixed"):
            vibe = "mixed"
        score = _compute_score(dims, vibe)
        keep = score >= 25

        ranked.append(
            RankedEvent(
                event=event,
                score=score,
                **dims,
                vibe=vibe,
                match_reason=item.get("match_reason", ""),
                keep=keep,
                filter_reason=None if keep else "Score below threshold.",
                event_type=item.get("event_type", "event"),
            )
        )

    return ranked


# ---------------------------------------------------------------------------
# Main ranking function
# ---------------------------------------------------------------------------


_PREFILTER_BATCH = 100
_PREFILTER_SYSTEM = """\
You are a fast relevance screener. Given a user interest profile and a list of events, \
return only the event IDs that have ANY relevance to the user's interests (score >= 30/100). \
Be inclusive — it's better to pass through borderline events than to miss good ones. \
Skip only events that are clearly irrelevant (wrong audience, completely mismatched interests, corporate events for non-employees, kids events for adults, etc.).

Return ONLY valid JSON (no markdown):
{"keep": ["event_id_1", "event_id_2", ...]}
"""


def _prefilter_events(
    profile: InterestProfile,
    events: list[Event],
    client: anthropic.Anthropic,
    prefilter_model: str = "claude-haiku-4-5-20251001",
) -> tuple[list[Event], list[Event], list[CostRecord]]:
    """Quick relevance pass using a cheap model. Returns (kept, filtered_out, costs)."""
    if not events:
        return [], [], []

    profile_summary = f"Summary: {profile.summary}\nInterests: {', '.join(i.topic for i in profile.interests[:15])}"
    cost_records: list[CostRecord] = []
    kept: list[Event] = []
    filtered_out: list[Event] = []
    events_by_id = {ev.id: ev for ev in events}

    batches = [events[i: i + _PREFILTER_BATCH] for i in range(0, len(events), _PREFILTER_BATCH)]

    for batch_idx, batch in enumerate(batches):
        event_list = [{"id": ev.id, "title": ev.title, "description": (ev.description or "")[:150], "location": ev.location_name, "category": ev.category} for ev in batch]
        user_msg = f"{profile_summary}\n\nEvents:\n{json.dumps(event_list, default=str)}\n\nReturn JSON with keep array of relevant event IDs."

        try:
            resp = client.messages.create(
                model=prefilter_model,
                max_tokens=4096,
                system=_PREFILTER_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
                timeout=120.0,
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
            data = json.loads(text)
            keep_ids = set(data.get("keep", []))
            cost_records.append(CostRecord(
                call_type="prefilter",
                model=prefilter_model,
                tokens_in=resp.usage.input_tokens,
                tokens_out=resp.usage.output_tokens,
                cost_usd=estimate_cost(prefilter_model, resp.usage.input_tokens, resp.usage.output_tokens),
            ))
            for ev in batch:
                if ev.id in keep_ids:
                    kept.append(ev)
                else:
                    filtered_out.append(ev)
        except Exception as exc:
            logger.warning("Prefilter batch %d failed (%s) — passing all through", batch_idx + 1, exc)
            kept.extend(batch)

    logger.info("Prefilter: %d/%d events passed (%d filtered out)", len(kept), len(events), len(filtered_out))
    return kept, filtered_out, cost_records


def rank_events(
    profile: InterestProfile,
    events: list[Event],
    client: anthropic.Anthropic,
    model: str,
    spotify_artists: list[str] | None = None,
    taste_top: list[dict] | None = None,
    home_lat: float = _HOME_LAT,
    home_lon: float = _HOME_LON,
    use_prefilter: bool = True,
    prefilter_model: str = "claude-haiku-4-5-20251001",
    friend_rsvps: dict[str, list[str]] | None = None,
    steering: list[dict] | None = None,
    calendar_context: str | None = None,
) -> tuple[list[RankedEvent], list[CostRecord]]:
    """Rank all events against the interest profile using Claude.

    Events are batched into groups of 60 if there are more than 80.
    Returns (sorted ranked_events, cost_records).
    """

    if not events:
        logger.info("No events to rank.")
        return [], []

    # Pre-filter 1: skip weekday 9-5 events (user can't attend)
    work_hours_skipped = [ev for ev in events if _is_during_work_hours(ev)]
    events = [ev for ev in events if not _is_during_work_hours(ev)]
    if work_hours_skipped:
        logger.info(
            "Pre-filter: removed %d weekday-daytime events (9am-5pm Mon-Fri)",
            len(work_hours_skipped),
        )

    # Pre-filter 2: fast haiku relevance pass (only if large enough to justify cost)
    # Exempt always-available events (no start_time, e.g. outdoor spots) — they'd
    # get filtered out by Haiku since they look like "places" not "events"
    prefilter_discarded: list[Event] = []
    prefilter_costs: list[CostRecord] = []
    if use_prefilter and len(events) > 150:
        always_available = [ev for ev in events if ev.start_time is None]
        filterable = [ev for ev in events if ev.start_time is not None]
        filterable, prefilter_discarded, prefilter_costs = _prefilter_events(
            profile, filterable, client, prefilter_model
        )
        events = filterable + always_available  # always-available skip prefilter

    # Build lookup (include skipped so they can be returned as score=0)
    events_by_id: dict[str, Event] = {ev.id: ev for ev in events}

    # Decide batching
    if len(events) > 80:
        batches = [events[i : i + _BATCH_SIZE] for i in range(0, len(events), _BATCH_SIZE)]
    else:
        batches = [events]

    logger.info(
        "Ranking %d events in %d batch(es) with model %s",
        len(events),
        len(batches),
        model,
    )

    all_ranked: list[RankedEvent] = []
    cost_records: list[CostRecord] = list(prefilter_costs)

    for batch_idx, batch in enumerate(batches):
        logger.info("Ranking batch %d/%d (%d events)", batch_idx + 1, len(batches), len(batch))

        user_message = _build_user_message(profile, batch, spotify_artists, taste_top, home_lat, home_lon, calendar_context)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=16000,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                timeout=180.0,
            )
        except anthropic.APIError as exc:
            logger.error("Claude API error during ranking batch %d: %s", batch_idx + 1, exc)
            raise

        raw_text = response.content[0].text
        batch_ranked = _parse_ranked_batch(raw_text, events_by_id, home_lat, home_lon)
        all_ranked.extend(batch_ranked)

        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = estimate_cost(model, tokens_in, tokens_out)

        cost_records.append(
            CostRecord(
                call_type="event_ranking",
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
            )
        )

        logger.info(
            "Batch %d: ranked %d events, cost $%.4f",
            batch_idx + 1,
            len(batch_ranked),
            cost,
        )

    # Check for events that Claude missed
    ranked_ids = {r.event.id for r in all_ranked}
    missed = [ev for ev in events if ev.id not in ranked_ids]
    if missed:
        logger.warning(
            "%d event(s) not returned by Claude -- assigning score 0 with keep=False",
            len(missed),
        )
        for ev in missed:
            all_ranked.append(
                RankedEvent(
                    event=ev,
                    score=0,
                    interest_score=0,
                    social_score=0,
                    match_reason="Not scored by ranking model.",
                    keep=False,
                    filter_reason="Event was not returned in ranking response.",
                )
            )

    # Apply friend RSVP boost: friends going = +25, maybe = +10
    if friend_rsvps:
        for ranked_ev in all_ranked:
            friends = friend_rsvps.get(ranked_ev.event.id, [])
            if not friends:
                continue
            going_count = sum(1 for f in friends if f.endswith("★"))
            maybe_count = len(friends) - going_count
            bonus = going_count * 25 + maybe_count * 10
            if bonus > 0:
                old_score = ranked_ev.score
                ranked_ev.score = min(100.0, ranked_ev.score + bonus)
                ranked_ev.keep = ranked_ev.score >= 25
                names = ", ".join(f.rstrip("★?") for f in friends[:3])
                ranked_ev.match_reason = f"🫂 {names} {'is' if len(friends)==1 else 'are'} going! " + ranked_ev.match_reason
                logger.debug("Friend boost for %s: +%d (%s -> %s)", ranked_ev.event.title, bonus, old_score, ranked_ev.score)

    # Apply steering directives
    if steering:
        steer_map: dict[str, str] = {}
        for s in steering:
            if s["target_type"] in ("event_id", "keyword", "category", "source"):
                steer_map[f"{s['target_type']}:{s['target_value'].lower()}"] = s["action"]

        for ranked_ev in all_ranked:
            action = None
            # Check event_id
            action = action or steer_map.get(f"event_id:{ranked_ev.event.id}")
            # Check source
            action = action or steer_map.get(f"source:{ranked_ev.event.source.value.lower()}")
            # Check category
            if ranked_ev.event.category:
                action = action or steer_map.get(f"category:{ranked_ev.event.category.lower()}")
            # Check keyword in title
            title_lower = ranked_ev.event.title.lower()
            for key, act in steer_map.items():
                if key.startswith("keyword:") and key[8:] in title_lower:
                    action = act
                    break

            if action == "block" or action == "done":
                ranked_ev.keep = False
                ranked_ev.score = 0
                ranked_ev.filter_reason = f"Steering: {action}"
            elif action == "more":
                ranked_ev.score = min(100.0, ranked_ev.score + 15)
                ranked_ev.keep = ranked_ev.score >= 25
            elif action == "less":
                ranked_ev.score = max(0.0, ranked_ev.score - 15)
                ranked_ev.keep = ranked_ev.score >= 25
            elif action == "pause":
                ranked_ev.keep = False
                ranked_ev.filter_reason = "Steering: paused"

    # Add prefilter-discarded events as keep=False
    for ev in prefilter_discarded:
        all_ranked.append(
            RankedEvent(
                event=ev,
                score=0,
                interest_score=0,
                social_score=0,
                match_reason="",
                keep=False,
                filter_reason="Filtered by relevance pre-pass.",
            )
        )

    # Add work-hours-filtered events as keep=False (they were never sent to Claude)
    for ev in work_hours_skipped:
        all_ranked.append(
            RankedEvent(
                event=ev,
                score=0,
                interest_score=0,
                social_score=0,
                match_reason="",
                keep=False,
                filter_reason="During work hours (Mon-Fri 9am-5pm).",
            )
        )

    # ── Diversity reranking ──────────────────────────────────────────────────
    # When scores are tightly clustered, apply category fatigue penalties
    # so the top picks aren't all the same type (especially music).
    kept_ranked = [r for r in all_ranked if r.keep]
    if len(kept_ranked) > 10:
        import statistics as _stats
        scores = [r.score for r in kept_ranked]
        stdev = _stats.stdev(scores) if len(scores) > 1 else 0
        if stdev < 15:
            logger.info(
                "Score spread is tight (stdev=%.1f) — applying diversity reranking",
                stdev,
            )
            # Group by broad category, penalize oversaturated categories
            from collections import Counter as _Counter
            cat_counts = _Counter()
            for r in sorted(all_ranked, key=lambda x: -x.score):
                if not r.keep:
                    continue
                cat = _categorize_broad(r.event.category, r.event.title)
                cat_counts[cat] += 1
                # After the 3rd event in the same category, apply diminishing penalty
                n = cat_counts[cat]
                if n > 3:
                    penalty = min(10.0, (n - 3) * 2.0)  # -2, -4, -6, -8, -10
                    r.score = max(0.0, r.score - penalty)
                    r.keep = r.score >= 25

    # Sort descending by score
    all_ranked.sort(key=lambda r: r.score, reverse=True)

    total_cost = sum(c.cost_usd for c in cost_records)
    logger.info(
        "Ranking complete: %d events scored, total cost $%.4f",
        len(all_ranked),
        total_cost,
    )

    return all_ranked, cost_records


def _categorize_broad(category: str | None, title: str) -> str:
    """Map event category/title into broad bucket for diversity tracking."""
    cat = (category or "").lower()
    title_l = title.lower()
    if any(k in cat for k in ("music", "concert", "dj")) or any(k in title_l for k in ("concert", "live music", "dj ", " tour")):
        return "music"
    if any(k in cat for k in ("comedy", "stand-up", "improv")):
        return "comedy"
    if any(k in cat for k in ("theatre", "theater", "dance", "performance")):
        return "performing_arts"
    if any(k in cat for k in ("lecture", "seminar", "talk", "workshop", "class")):
        return "learning"
    if any(k in cat for k in ("sport", "fitness", "outdoor", "run", "hike")):
        return "active"
    if any(k in cat for k in ("food", "drink", "tasting", "beer", "wine")):
        return "food_drink"
    if any(k in cat for k in ("art", "gallery", "museum", "exhibit")):
        return "arts"
    if any(k in cat for k in ("social", "networking", "meetup")):
        return "social"
    return "other"
