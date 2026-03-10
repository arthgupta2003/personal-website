"""Claude-based event ranking -- the core scoring module."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import anthropic

from recom.config import estimate_cost
from recom.models import CostRecord, Event, InterestProfile, RankedEvent

logger = logging.getLogger(__name__)

_BATCH_SIZE = 40

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
add +10 (can exceed 15). Concerts by artists the user listens to are \
high-signal — always surface them.

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


def _build_user_message(
    profile: InterestProfile,
    events: list[Event],
    spotify_artists: list[str] | None = None,
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

    if spotify_artists:
        profile_lines.append("")
        profile_lines.append("=== SPOTIFY ARTISTS (user listens to these — boost concerts!) ===")
        profile_lines.append(", ".join(spotify_artists))

    # Events section
    event_dicts: list[dict] = []
    for ev in events:
        event_dicts.append(
            {
                "id": ev.id,
                "title": ev.title,
                "description": ev.description[:300] if ev.description else "",
                "start_time": ev.start_time.isoformat() if ev.start_time else None,
                "end_time": ev.end_time.isoformat() if ev.end_time else None,
                "location": ev.location_name,
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
    raw_text: str, events_by_id: dict[str, Event]
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

        dims = {
            "interest_score": float(item.get("interest_score", 0)),
            "social_score": float(item.get("social_score", 0)),
            "urgency_score": float(item.get("urgency_score", 0)),
            "logistics_score": float(item.get("logistics_score", 0)),
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


def rank_events(
    profile: InterestProfile,
    events: list[Event],
    client: anthropic.Anthropic,
    model: str,
    spotify_artists: list[str] | None = None,
) -> tuple[list[RankedEvent], list[CostRecord]]:
    """Rank all events against the interest profile using Claude.

    Events are batched into groups of 60 if there are more than 80.
    Returns (sorted ranked_events, cost_records).
    """

    if not events:
        logger.info("No events to rank.")
        return [], []

    # Build lookup
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
    cost_records: list[CostRecord] = []

    for batch_idx, batch in enumerate(batches):
        logger.info("Ranking batch %d/%d (%d events)", batch_idx + 1, len(batches), len(batch))

        user_message = _build_user_message(profile, batch, spotify_artists)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=16000,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIError as exc:
            logger.error("Claude API error during ranking batch %d: %s", batch_idx + 1, exc)
            raise

        raw_text = response.content[0].text
        batch_ranked = _parse_ranked_batch(raw_text, events_by_id)
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

    # Sort descending by score
    all_ranked.sort(key=lambda r: r.score, reverse=True)

    total_cost = sum(c.cost_usd for c in cost_records)
    logger.info(
        "Ranking complete: %d events scored, total cost $%.4f",
        len(all_ranked),
        total_cost,
    )

    return all_ranked, cost_records
