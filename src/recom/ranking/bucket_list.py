"""Pick relevant bucket-list suggestions for this week's email."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import anthropic

from recom.config import estimate_cost
from recom.models import CostRecord

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a lifestyle assistant for a young professional in Cambridge, MA (02139).

Given a list of bucket-list activities and today's date, pick 3-5 that are \
most relevant RIGHT NOW based on:
- Season and weather (March in Boston = late winter/early spring, ~35-50°F, \
possibly still snow. Water sports won't work yet. Indoor activities and \
early spring hikes are good.)
- Day of week (weekends are better for day trips)
- Any that pair well with each other (e.g., "bike the trail then grab ramen")

For each pick, write a short (1-2 sentence) nudge — practical, specific, \
and motivating. Include any relevant details (where to go, what to bring, \
when to do it).

Return ONLY valid JSON:
{
  "suggestions": [
    {
      "activity": "the bucket list item text",
      "nudge": "Practical, specific suggestion for this week.",
      "best_day": "Saturday" or "any weekday evening" or "Sunday morning" etc.
    }
  ]
}
"""


def load_bucket_list(path: str) -> list[str]:
    """Load bucket list items from file, skipping comments and blanks."""
    p = Path(path)
    if not p.exists():
        return []
    items = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items


def pick_suggestions(
    items: list[str],
    client: anthropic.Anthropic,
    model: str,
) -> tuple[list[dict], CostRecord]:
    """Pick 3-5 relevant bucket-list suggestions for this week."""
    if not items:
        return [], CostRecord(
            call_type="bucket_list", model=model,
            tokens_in=0, tokens_out=0, cost_usd=0,
        )

    today = datetime.now().strftime("%A, %B %d, %Y")
    user_msg = (
        f"Today is {today}.\n\n"
        f"Bucket list items:\n"
        + "\n".join(f"- {item}" for item in items)
        + "\n\nPick 3-5 that make sense this week."
    )

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
        suggestions = data.get("suggestions", [])
    except json.JSONDecodeError:
        logger.error("Failed to parse bucket list response")
        suggestions = []

    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    cost = estimate_cost(model, tokens_in, tokens_out)

    logger.info("Bucket list: picked %d suggestions ($%.4f)", len(suggestions), cost)

    return suggestions, CostRecord(
        call_type="bucket_list",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )
