"""Newsletter event extraction — uses Claude to find events in newsletter HTML."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import anthropic

from calyx.config import Settings, estimate_cost
from calyx.events.common import make_event_id
from calyx.models import CostRecord, Event, EventSource, parse_event_dt

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are an event extraction assistant. Analyze the following newsletter email and \
extract any events mentioned. For each event, provide:

- title: the event name
- date: date/time string (ISO 8601 preferred, e.g. "2026-03-15T19:00:00")
- end_date: end date/time if mentioned (ISO 8601), or null
- location: venue or location name
- address: street address if mentioned
- url: link to the event page or RSVP, or ""
- description: 1-2 sentence description of the event
- price: ticket price if mentioned, or null
- category: a short category (e.g. "music", "tech", "art", "food", "networking")
- organizer: who is hosting the event, or null
- is_online: true if virtual/online, false otherwise

Return a JSON array of event objects. If no events are found, return an empty array [].
Only return the JSON array, no other text.

Newsletter sender: {sender}
Newsletter subject: {subject}
Newsletter date: {date}

Newsletter body:
{body}
"""





def _extract_from_one_newsletter(
    newsletter: dict,
    client: anthropic.Anthropic,
    model: str,
) -> tuple[list[Event], CostRecord | None]:
    """Extract events from a single newsletter using Claude."""
    sender = newsletter.get("sender", "Unknown")
    subject = newsletter.get("subject", "")
    body = newsletter.get("html_body", "") or newsletter.get("body", "")
    date = newsletter.get("date", "")

    if not body:
        return [], None

    # Truncate very long newsletters to stay within token limits
    if len(body) > 30_000:
        body = body[:30_000] + "\n... [truncated]"

    prompt = EXTRACTION_PROMPT.format(
        sender=sender,
        subject=subject,
        date=date,
        body=body,
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        logger.exception("Claude API call failed for newsletter: %s", subject)
        return [], None

    # Track costs
    usage = response.usage
    cost = estimate_cost(model, usage.input_tokens, usage.output_tokens)
    cost_record = CostRecord(
        call_type="newsletter_extraction",
        model=model,
        tokens_in=usage.input_tokens,
        tokens_out=usage.output_tokens,
        cost_usd=cost,
    )

    # Parse the response
    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        raw_events = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Could not parse Claude response as JSON for newsletter: %s", subject)
        return [], cost_record

    if not isinstance(raw_events, list):
        logger.warning("Claude response is not a list for newsletter: %s", subject)
        return [], cost_record

    events: list[Event] = []
    for ev in raw_events:
        title = ev.get("title", "")
        if not title:
            continue

        date_str = ev.get("date", "")
        start_time = parse_event_dt(date_str)
        end_time = parse_event_dt(ev.get("end_date"))

        events.append(
            Event(
                id=make_event_id("newsletter", title, date_str),
                source=EventSource.NEWSLETTER,
                title=title,
                description=ev.get("description", "")[:500],
                url=ev.get("url", ""),
                start_time=start_time,
                end_time=end_time,
                location_name=ev.get("location", ""),
                location_address=ev.get("address", ""),
                is_online=ev.get("is_online", False),
                price=ev.get("price"),
                category=ev.get("category"),
                organizer=ev.get("organizer") or sender,
            )
        )

    return events, cost_record


def extract_newsletter_events(
    newsletters: list[dict],
    client: anthropic.Anthropic,
    model: str,
) -> tuple[list[Event], list[CostRecord]]:
    """Extract events from multiple newsletters using Claude.

    Args:
        newsletters: List of dicts with keys: sender, subject, html_body, date
        client: Anthropic client instance
        model: Claude model ID to use

    Returns:
        Tuple of (events, cost_records)
    """
    all_events: list[Event] = []
    all_costs: list[CostRecord] = []

    for newsletter in newsletters:
        subject = newsletter.get("subject", "")
        logger.info("Extracting events from newsletter: %s", subject)

        try:
            events, cost_record = _extract_from_one_newsletter(newsletter, client, model)
            all_events.extend(events)
            if cost_record:
                all_costs.append(cost_record)
            logger.info("Found %d events in newsletter: %s", len(events), subject)
        except Exception:
            logger.exception("Failed to extract events from newsletter: %s", subject)

    logger.info("Total newsletter events extracted: %d from %d newsletters", len(all_events), len(newsletters))
    return all_events, all_costs
