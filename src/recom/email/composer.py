"""Compose the weekly event-digest HTML email."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from jinja2 import Environment

from recom.models import InterestProfile, RankedEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_dt(dt: datetime | None) -> str:
    """Human-friendly date/time string for the email."""
    if dt is None:
        return "Date TBD"
    try:
        if dt.hour == 0 and dt.minute == 0:
            return dt.strftime("%a %b %-d")
        return dt.strftime("%a %b %-d, %-I:%M %p")
    except (ValueError, TypeError):
        return str(dt)[:16]


def _format_day(dt: datetime | None) -> str:
    if dt is None:
        return "Date TBD"
    try:
        return dt.strftime("%A, %b %-d")
    except (ValueError, TypeError):
        return str(dt)[:10]


import re as _re


def _short_desc(text: str, max_len: int = 100) -> str:
    """Strip HTML and truncate to a short snippet."""
    if not text:
        return ""
    clean = _re.sub(r'<[^>]+>', '', text).replace("&nbsp;", " ").replace("&#8217;", "'").replace("&#8220;", '"').replace("&#8221;", '"').strip()
    if len(clean) > max_len:
        return clean[:max_len] + "..."
    return clean


_env = Environment(autoescape=False)
_env.filters["format_dt"] = _format_dt
_env.filters["format_day"] = _format_day
_env.filters["short_desc"] = _short_desc

# ---------------------------------------------------------------------------
# Inline Jinja2 template -- mobile-friendly HTML email
# ---------------------------------------------------------------------------

_DIGEST_TEMPLATE = _env.from_string(
    """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ subject }}</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f5f5f5; margin: 0; padding: 0; color: #333;
  }
  .container { max-width: 640px; margin: 0 auto; padding: 16px; }
  .header {
    background: linear-gradient(135deg, #1a1a2e, #16213e);
    color: #fff; padding: 24px; border-radius: 12px 12px 0 0;
    text-align: center;
  }
  .header h1 { margin: 0 0 4px 0; font-size: 22px; }
  .header p { margin: 0; opacity: 0.8; font-size: 14px; }
  .section { padding: 16px 0; }
  .section-title {
    font-size: 16px; font-weight: 700; margin: 0 0 12px 0;
    padding-bottom: 6px; border-bottom: 2px solid;
  }
  .section-title.gold { color: #b8860b; border-color: #daa520; }
  .section-title.purple { color: #7c3aed; border-color: #8b5cf6; }
  .section-title.blue { color: #2563eb; border-color: #3b82f6; }
  .day-header {
    font-size: 14px; font-weight: 700; color: #1e40af;
    margin: 16px 0 8px 0; padding: 4px 0;
    border-bottom: 1px solid #dbeafe;
  }
  .card {
    background: #fff; border-radius: 8px; padding: 14px 16px;
    margin-bottom: 10px; border-left: 4px solid; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  .card.gold { border-left-color: #daa520; }
  .card.purple { border-left-color: #8b5cf6; }
  .card.blue { border-left-color: #3b82f6; }
  .card-title { font-size: 15px; font-weight: 600; margin: 0 0 4px 0; }
  .card-title a { color: #2563eb; text-decoration: underline; }
  .card-title a:hover { color: #1d4ed8; }
  .card-meta { font-size: 13px; color: #6b7280; margin: 0 0 6px 0; }
  .card-desc { font-size: 12px; color: #6b7280; margin: 0 0 4px 0; line-height: 1.3; }
  .card-reason { font-size: 13px; color: #4b5563; margin: 0; line-height: 1.4; }
  .score-badge {
    display: inline-block; font-size: 12px; font-weight: 700;
    padding: 2px 8px; border-radius: 10px; color: #fff; margin-left: 6px;
  }
  .score-badge.high { background: #059669; }
  .score-badge.mid  { background: #2563eb; }
  .score-badge.low  { background: #9ca3af; }
  .type-badge {
    display: inline-block; font-size: 10px; font-weight: 600;
    padding: 1px 6px; border-radius: 8px; margin-left: 4px;
    text-transform: uppercase;
  }
  .type-badge.club { background: #ede9fe; color: #6d28d9; }
  .type-badge.class { background: #fef3c7; color: #92400e; }
  .footer {
    text-align: center; font-size: 12px; color: #9ca3af;
    padding: 20px 0 8px 0; border-top: 1px solid #e5e7eb; margin-top: 12px;
  }
  .empty { color: #9ca3af; font-style: italic; font-size: 14px; }
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1><a href="{{ dashboard_url }}/run/{{ run_id }}" style="color: #fff; text-decoration: none;">Your Weekly Event Digest</a></h1>
    <p>Week of {{ week_of }}</p>
  </div>

  {# --- Top 10 Recommendations --- #}
  <div class="section">
    <p class="section-title gold">Top 10 Recommendations</p>
    {% if top_recs %}
    {% for r in top_recs %}
    <div class="card gold">
      <p class="card-title">
        {{ loop.index }}.
        {% if r.event.url %}<a href="{{ r.event.url }}">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
        <span class="score-badge high">{{ r.score | int }}</span>
        {% if r.event_type == "club" %}<span class="type-badge club">club</span>{% endif %}
        {% if r.event_type == "class" %}<span class="type-badge class">class</span>{% endif %}
      </p>
      <p class="card-meta">
        {{ r.event.start_time | format_dt }} &middot; {{ r.event.location_name or "TBD" }}
        {% if r.event.price %} &middot; {{ r.event.price }}{% endif %}
      </p>
      {% if r.event.description | short_desc %}<p class="card-desc">{{ r.event.description | short_desc }}</p>{% endif %}
      <p class="card-reason">{{ r.match_reason }}</p>
    </div>
    {% endfor %}
    {% else %}
    <p class="empty">No standout picks this week.</p>
    {% endif %}
  </div>

  {# --- Clubs & Classes --- #}
  {% if clubs_classes %}
  <div class="section">
    <p class="section-title purple">Clubs, Classes & Memberships</p>
    {% for r in clubs_classes %}
    <div class="card purple">
      <p class="card-title">
        {% if r.event.url %}<a href="{{ r.event.url }}">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
        <span class="score-badge mid">{{ r.score | int }}</span>
        {% if r.event_type == "club" %}<span class="type-badge club">club</span>{% endif %}
        {% if r.event_type == "class" %}<span class="type-badge class">class</span>{% endif %}
      </p>
      <p class="card-meta">
        {{ r.event.start_time | format_dt }} &middot; {{ r.event.location_name or "TBD" }}
        {% if r.event.price %} &middot; {{ r.event.price }}{% endif %}
      </p>
      {% if r.event.description | short_desc %}<p class="card-desc">{{ r.event.description | short_desc }}</p>{% endif %}
      <p class="card-reason">{{ r.match_reason }}</p>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {# --- Bucket List Suggestions --- #}
  {% if bucket_suggestions %}
  <div class="section">
    <p class="section-title" style="color: #059669; border-color: #10b981;">This Week You Could Also...</p>
    {% for s in bucket_suggestions %}
    <div class="card" style="border-left-color: #10b981;">
      <p class="card-title" style="font-size: 14px;">{{ s.activity }}</p>
      <p class="card-meta">Best: {{ s.best_day }}</p>
      <p class="card-reason">{{ s.nudge }}</p>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {# --- By Day --- #}
  <div class="section">
    <p class="section-title blue">Browse by Day</p>
    {% for day_label, day_events in by_day %}
    <p class="day-header">{{ day_label }}</p>
    {% for r in day_events %}
    <div class="card blue">
      <p class="card-title">
        {% if r.event.url %}<a href="{{ r.event.url }}">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
        <span class="score-badge {% if r.score >= 60 %}high{% elif r.score >= 35 %}mid{% else %}low{% endif %}">{{ r.score | int }}</span>
      </p>
      <p class="card-meta">
        {{ r.event.start_time | format_dt }} &middot; {{ r.event.location_name or "TBD" }}
        {% if r.event.price %} &middot; {{ r.event.price }}{% endif %}
      </p>
      {% if r.event.description | short_desc %}<p class="card-desc">{{ r.event.description | short_desc }}</p>{% endif %}
      <p class="card-reason">{{ r.match_reason }}</p>
    </div>
    {% endfor %}
    {% endfor %}
    {% if undated %}
    <p class="day-header">Date TBD</p>
    {% for r in undated %}
    <div class="card blue">
      <p class="card-title">
        {% if r.event.url %}<a href="{{ r.event.url }}">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
        <span class="score-badge {% if r.score >= 60 %}high{% elif r.score >= 35 %}mid{% else %}low{% endif %}">{{ r.score | int }}</span>
      </p>
      <p class="card-meta">
        {{ r.event.location_name or "TBD" }}
        {% if r.event.price %} &middot; {{ r.event.price }}{% endif %}
      </p>
      <p class="card-reason">{{ r.match_reason }}</p>
    </div>
    {% endfor %}
    {% endif %}
  </div>

  <div class="footer">
    <a href="{{ dashboard_url }}" style="color: #2563eb; text-decoration: none; font-weight: 600;">View full dashboard &rarr;</a>
    &nbsp;&middot;&nbsp;
    <a href="{{ dashboard_url }}/feed.ics" style="color: #2563eb; text-decoration: none; font-weight: 600;">Subscribe to calendar &rarr;</a>
    <br>
    Curated by <strong>recom</strong> &middot; AI cost: ${{ "%.4f" | format(total_cost) }}
    &middot; Tokens: {{ "{:,}".format(tokens_in) }} in / {{ "{:,}".format(tokens_out) }} out
  </div>

</div>
</body>
</html>
"""
)


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------


def compose_email(
    ranked_events: list[RankedEvent],
    profile: InterestProfile,
    week_of: str,
    total_cost: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
    bucket_suggestions: list[dict] | None = None,
    dashboard_url: str = "https://recom.arthgupta.dev",
    run_id: int | None = None,
) -> tuple[str, str]:
    """Build the digest email.

    Returns (subject, html_body).
    """

    kept = sorted([r for r in ranked_events if r.keep], key=lambda r: r.score, reverse=True)

    # Top 10 overall recommendations
    top_recs = kept[:10]

    # Clubs / classes / memberships (not already in top 10, cap at 5)
    top_ids = {id(r) for r in top_recs}
    clubs_classes = [
        r for r in kept
        if r.event_type in ("club", "class") and id(r) not in top_ids
    ][:5]

    # Remaining events organized by day
    shown_ids = top_ids | {id(r) for r in clubs_classes}
    remaining = [r for r in kept if id(r) not in shown_ids and r.event_type == "event"]

    dated = [r for r in remaining if r.event.start_time is not None]
    undated = [r for r in remaining if r.event.start_time is None]

    # Group by day
    day_groups: dict[str, list[RankedEvent]] = defaultdict(list)
    for r in dated:
        day_label = _format_day(r.event.start_time)
        day_groups[day_label].append(r)

    # Sort days chronologically, events within each day by score
    def _sort_key(item):
        dt = item[1][0].event.start_time
        if dt is None:
            return datetime.min
        # Strip timezone info for consistent comparison
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    by_day = sorted(day_groups.items(), key=_sort_key)
    for _, evts in by_day:
        evts.sort(key=lambda r: r.score, reverse=True)
        # Pick top 5 per day with vibe diversity (max 2 per vibe)
        diverse: list[RankedEvent] = []
        vibe_counts: dict[str, int] = defaultdict(int)
        for r in evts:
            if len(diverse) >= 5:
                break
            if vibe_counts[r.vibe] >= 2:
                continue
            diverse.append(r)
            vibe_counts[r.vibe] += 1
        evts[:] = diverse
    undated.sort(key=lambda r: r.score, reverse=True)
    undated = undated[:5]

    subject = f"Your Week: {len(top_recs)} top picks, {len(kept)} events total ({week_of})"

    html_body = _DIGEST_TEMPLATE.render(
        subject=subject,
        week_of=week_of,
        top_recs=top_recs,
        clubs_classes=clubs_classes,
        by_day=by_day,
        undated=undated,
        bucket_suggestions=bucket_suggestions or [],
        total_cost=total_cost,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        profile=profile,
        dashboard_url=dashboard_url,
        run_id=run_id,
    )

    logger.info(
        "Composed email: %d top recs, %d clubs/classes, %d by-day, %d undated (%d total kept of %d ranked)",
        len(top_recs),
        len(clubs_classes),
        len(dated),
        len(undated),
        len(kept),
        len(ranked_events),
    )

    return subject, html_body


# ---------------------------------------------------------------------------
# Daily email template
# ---------------------------------------------------------------------------

_DAILY_TEMPLATE = _env.from_string(
    """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ subject }}</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f5f5f5; margin: 0; padding: 0; color: #333;
  }
  .container { max-width: 640px; margin: 0 auto; padding: 16px; }
  .header {
    background: linear-gradient(135deg, #1a1a2e, #16213e);
    color: #fff; padding: 20px; border-radius: 12px 12px 0 0;
    text-align: center;
  }
  .header h1 { margin: 0 0 4px 0; font-size: 20px; }
  .header h1 a { color: #fff; text-decoration: none; }
  .header p { margin: 0; opacity: 0.8; font-size: 14px; }
  .card {
    background: #fff; border-radius: 8px; padding: 14px 16px;
    margin-bottom: 10px; border-left: 4px solid; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  .card.social { border-left-color: #f59e0b; }
  .card.intellectual { border-left-color: #8b5cf6; }
  .card.mixed { border-left-color: #3b82f6; }
  .card-title { font-size: 15px; font-weight: 600; margin: 0 0 4px 0; }
  .card-title a { color: #2563eb; text-decoration: underline; }
  .card-title a:hover { color: #1d4ed8; }
  .card-meta { font-size: 13px; color: #6b7280; margin: 0 0 4px 0; }
  .card-desc { font-size: 12px; color: #6b7280; margin: 0 0 4px 0; line-height: 1.3; }
  .card-reason { font-size: 13px; color: #4b5563; margin: 0; line-height: 1.4; }
  .score-badge {
    display: inline-block; font-size: 12px; font-weight: 700;
    padding: 2px 8px; border-radius: 10px; color: #fff; margin-left: 6px;
  }
  .score-badge.high { background: #059669; }
  .score-badge.mid  { background: #2563eb; }
  .score-badge.low  { background: #9ca3af; }
  .section { padding: 12px 0; }
  .section-title { font-size: 14px; font-weight: 700; color: #6b7280; margin-bottom: 8px; }
  .footer {
    text-align: center; font-size: 12px; color: #9ca3af;
    padding: 16px 0 8px 0; border-top: 1px solid #e5e7eb; margin-top: 12px;
  }
  .empty { color: #9ca3af; font-style: italic; font-size: 14px; }
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1><a href="{{ dashboard_url }}">{{ day_label }}</a></h1>
    <p>{{ event_count }} picks for today</p>
  </div>

  {% for r in events %}
  <div class="card {{ r.vibe }}">
    <p class="card-title">
      {% if r.event.url %}<a href="{{ r.event.url }}">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
      <span class="score-badge {% if r.score >= 60 %}high{% elif r.score >= 35 %}mid{% else %}low{% endif %}">{{ r.score | int }}</span>
    </p>
    <p class="card-meta">
      {{ r.event.start_time | format_dt }}
      &middot; {{ r.event.location_name or "TBD" }}
      {% if r.event.price %} &middot; {{ r.event.price }}{% endif %}
    </p>
    {% if r.event.description | short_desc %}<p class="card-desc">{{ r.event.description | short_desc }}</p>{% endif %}
    <p class="card-reason">{{ r.match_reason }}</p>
    {% if friend_rsvps and r.event.id in friend_rsvps %}
    <p style="margin-top:4px;font-size:12px;">
      {% for rv in friend_rsvps[r.event.id] %}
      <span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600;margin-right:3px;
        {% if rv.status == 'going' %}background:#dcfce7;color:#166534;{% elif rv.status == 'maybe' %}background:#fef3c7;color:#92400e;{% else %}background:#fee2e2;color:#991b1b;{% endif %}">
        {{ rv.user_name }} {{ 'going' if rv.status == 'going' else ('maybe' if rv.status == 'maybe' else "can't") }}
      </span>
      {% endfor %}
    </p>
    {% endif %}
    <p style="margin-top:6px">
      {% if user_token %}
      <a href="{{ dashboard_url }}/api/rsvp-link?event_id={{ r.event.id }}&status=going&u={{ user_token }}&title={{ r.event.title | urlencode }}" style="font-size:12px;color:#166534;text-decoration:none;border:1px solid #86efac;padding:2px 10px;border-radius:10px;">Going</a>
      <a href="{{ dashboard_url }}/api/rsvp-link?event_id={{ r.event.id }}&status=maybe&u={{ user_token }}&title={{ r.event.title | urlencode }}" style="font-size:12px;color:#92400e;text-decoration:none;border:1px solid #fde68a;padding:2px 10px;border-radius:10px;margin-left:4px;">Maybe</a>
      {% endif %}
      <a href="{{ dashboard_url }}/api/attend-link?event_id={{ r.event.id }}&title={{ r.event.title | urlencode }}" style="font-size:12px;color:#059669;text-decoration:none;border:1px solid #86efac;padding:2px 10px;border-radius:10px;margin-left:4px;">I went</a>
    </p>
  </div>
  {% endfor %}

  {% if not events %}
  <div class="section">
    <p class="empty">Nothing great on the calendar today. Check the <a href="{{ dashboard_url }}">full calendar</a> for upcoming events.</p>
  </div>
  {% endif %}

  {% if bucket_suggestions %}
  <div class="section">
    <p class="section-title" style="color: #059669;">You could also...</p>
    {% for s in bucket_suggestions %}
    <div class="card" style="border-left-color: #10b981;">
      <p class="card-title" style="font-size: 14px;">{{ s.activity }}</p>
      <p class="card-reason">{{ s.nudge }}</p>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <div class="footer">
    <a href="{{ dashboard_url }}" style="color: #2563eb; text-decoration: none; font-weight: 600;">Full calendar &rarr;</a>
    &nbsp;&middot;&nbsp;
    <a href="{{ dashboard_url }}/feed.ics" style="color: #2563eb; text-decoration: none; font-weight: 600;">Subscribe &rarr;</a>
  </div>

</div>
</body>
</html>
"""
)


def compose_daily_email(
    ranked_events: list[RankedEvent],
    target_date: datetime,
    bucket_suggestions: list[dict] | None = None,
    dashboard_url: str = "https://recom.arthgupta.dev",
    user_token: str = "",
    friend_rsvps: dict[str, list[dict]] | None = None,
) -> tuple[str, str] | None:
    """Build a daily digest for a specific date.

    Returns (subject, html_body) or None if no events for that day.
    """
    target_str = target_date.strftime("%Y-%m-%d")
    day_label = target_date.strftime("%A, %B %-d")

    # Filter to events on this day
    todays = []
    for r in ranked_events:
        if not r.keep or r.score < 25:
            continue
        if r.event.start_time is None:
            continue
        st = r.event.start_time
        event_date = st.replace(tzinfo=None) if st.tzinfo else st
        if event_date.strftime("%Y-%m-%d") == target_str:
            todays.append(r)

    # Sort by score, pick top 5 with vibe diversity
    todays.sort(key=lambda r: r.score, reverse=True)
    diverse: list[RankedEvent] = []
    vibe_counts: dict[str, int] = defaultdict(int)
    for r in todays:
        if len(diverse) >= 10:
            break
        if vibe_counts[r.vibe] >= 4:
            continue
        diverse.append(r)
        vibe_counts[r.vibe] += 1

    if not diverse and not bucket_suggestions:
        return None

    subject = f"{day_label}: {len(diverse)} events for you" if diverse else f"{day_label}: No events, but here are some ideas"

    html_body = _DAILY_TEMPLATE.render(
        subject=subject,
        day_label=day_label,
        event_count=len(diverse),
        events=diverse,
        bucket_suggestions=bucket_suggestions or [],
        dashboard_url=dashboard_url,
        user_token=user_token,
        friend_rsvps=friend_rsvps or {},
    )

    return subject, html_body
