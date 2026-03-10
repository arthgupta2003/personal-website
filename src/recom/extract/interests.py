"""Claude-based interest extraction from activity data."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime

import anthropic

from recom.config import estimate_cost
from recom.models import (
    ActivityItem,
    CostRecord,
    Interest,
    InterestProfile,
    RawActivity,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_manual_keywords(path: str) -> list[str]:
    """Read my_interests.txt -- one keyword/phrase per line."""
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logger.warning("Manual keywords file not found: %s", path)
        return []


def _summarize_activity(activity: RawActivity) -> str:
    """Produce a compact summary of the user's raw activity so we don't
    blow up the prompt with hundreds of items."""

    sections: list[str] = []

    # --- YouTube ---
    if activity.youtube:
        channel_counter: Counter[str] = Counter()
        categories: Counter[str] = Counter()
        sample_titles: list[str] = []
        for item in activity.youtube:
            channel_counter[item.title.split(" - ")[0] if " - " in item.title else item.title] += 1
            if item.category:
                categories[item.category] += 1
            if len(sample_titles) < 15:
                sample_titles.append(item.title)

        lines = [f"YouTube ({len(activity.youtube)} videos watched):"]
        top_channels = channel_counter.most_common(10)
        if top_channels:
            lines.append("  Top channels: " + ", ".join(f"{ch} ({n})" for ch, n in top_channels))
        top_cats = categories.most_common(5)
        if top_cats:
            lines.append("  Top categories: " + ", ".join(f"{c} ({n})" for c, n in top_cats))
        lines.append("  Sample titles: " + "; ".join(sample_titles))
        sections.append("\n".join(lines))

    # --- Spotify ---
    if activity.spotify:
        artist_counter: Counter[str] = Counter()
        genre_counter: Counter[str] = Counter()
        for item in activity.spotify:
            artist_counter[item.title] += 1
            if item.category:
                for genre in item.category.split(","):
                    genre_counter[genre.strip()] += 1

        lines = [f"Spotify ({len(activity.spotify)} tracks):"]
        top_artists = artist_counter.most_common(10)
        if top_artists:
            lines.append("  Top artists: " + ", ".join(f"{a} ({n})" for a, n in top_artists))
        top_genres = genre_counter.most_common(8)
        if top_genres:
            lines.append("  Top genres: " + ", ".join(f"{g} ({n})" for g, n in top_genres))
        sections.append("\n".join(lines))

    # --- Newsletters ---
    if activity.newsletters:
        lines = [f"Newsletters ({len(activity.newsletters)} items):"]
        for item in activity.newsletters[:15]:
            desc = f" - {item.description[:80]}" if item.description else ""
            lines.append(f"  - {item.title}{desc}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections) if sections else "(no activity data)"


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an interest-extraction engine. Given a summary of a user's recent \
activity (YouTube, Spotify, newsletters), extract 10-20 specific, actionable \
interests.

Rules:
- Each interest must be specific enough to match against event listings \
  (e.g. "live jazz music" not just "music").
- Assign a confidence score 0.0-1.0 based on signal strength.
- List the source signals that support each interest \
  (e.g. ["youtube:channel_name", "spotify:genre"]).
- Also produce a 2-3 sentence summary of the user's overall taste profile.

Respond with ONLY valid JSON -- no markdown fences, no commentary:
{
  "interests": [
    {"topic": "...", "confidence": 0.85, "source_signals": ["...", "..."]}
  ],
  "summary": "..."
}
"""


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------


def extract_interests(
    activity: RawActivity,
    manual_keywords: list[str],
    client: anthropic.Anthropic,
    model: str,
    taste_top: list[dict] | None = None,
) -> tuple[InterestProfile, CostRecord]:
    """Call Claude to extract interests from activity data, then merge
    manual keywords.  Returns (InterestProfile, CostRecord)."""

    activity_summary = _summarize_activity(activity)
    logger.info("Activity summary length: %d chars", len(activity_summary))

    user_message = (
        "Here is the user's recent activity:\n\n"
        f"{activity_summary}\n\n"
        "Extract their interests as described."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        logger.error("Claude API error during interest extraction: %s", exc)
        raise

    # --- Parse response ---
    raw_text = response.content[0].text
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response as JSON: %.500s", raw_text)
        raise ValueError("Interest extraction returned invalid JSON")

    interests: list[Interest] = []
    for item in data.get("interests", []):
        interests.append(
            Interest(
                topic=item["topic"],
                confidence=float(item.get("confidence", 0.5)),
                source_signals=item.get("source_signals", []),
            )
        )

    summary = data.get("summary", "")

    # --- Merge manual keywords ---
    existing_topics = {i.topic.lower() for i in interests}
    for kw in manual_keywords:
        if kw.lower() not in existing_topics:
            interests.append(
                Interest(topic=kw, confidence=0.9, source_signals=["manual"])
            )
            existing_topics.add(kw.lower())

    # --- Merge Elo taste stack (top items as explicit interest signals) ---
    if taste_top:
        # Map Elo to confidence: top item at 1400 baseline → 0.7, each win adds ~0.02
        max_elo = max(t["elo_rating"] for t in taste_top) if taste_top else 1400
        min_elo = min(t["elo_rating"] for t in taste_top) if taste_top else 1400
        elo_range = max(max_elo - min_elo, 1)
        for item in taste_top:
            label = item["label"]
            if label.lower() not in existing_topics:
                # Confidence 0.6–0.95 scaled by relative Elo position
                relative = (item["elo_rating"] - min_elo) / elo_range
                conf = round(0.6 + relative * 0.35, 2)
                interests.append(
                    Interest(
                        topic=label,
                        confidence=conf,
                        source_signals=[f"elo_taste_stack:{item['category']}"],
                    )
                )
                existing_topics.add(label.lower())

    profile = InterestProfile(
        interests=interests,
        summary=summary,
        generated_at=datetime.now(),
    )

    # --- Cost tracking ---
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    cost = estimate_cost(model, tokens_in, tokens_out)

    cost_record = CostRecord(
        call_type="interest_extraction",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )

    logger.info(
        "Extracted %d interests (+ %d manual). Cost: $%.4f",
        len(data.get("interests", [])),
        len(manual_keywords),
        cost,
    )

    return profile, cost_record
