"""Compose the weekly event-digest HTML email."""

from __future__ import annotations

import logging
import math
import re as _re
from collections import defaultdict
from datetime import datetime

from jinja2 import Environment

from recom.models import InterestProfile, RankedEvent, haversine_km

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


def _short_desc(text: str, max_len: int = 100) -> str:
    """Strip HTML and truncate to a short snippet."""
    if not text:
        return ""
    clean = _re.sub(r'<[^>]+>', '', text).replace("&nbsp;", " ").replace("&#8217;", "'").replace("&#8220;", '"').replace("&#8221;", '"').strip()
    if len(clean) > max_len:
        return clean[:max_len] + "..."
    return clean


_haversine_km = haversine_km  # use shared implementation


def _make_dist_filter(home_lat: float, home_lon: float):
    def dist_str(event) -> str:
        if event.is_online:
            return ""
        lat = getattr(event, "lat", None)
        lon = getattr(event, "lon", None)
        if lat is None or lon is None:
            return ""
        km = _haversine_km(home_lat, home_lon, lat, lon)
        if km < 1:
            return f"{round(km*1000)}m"
        return f"{km:.1f}km"
    return dist_str


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
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#0f0f1a;margin:0;padding:0;color:#1a1a1a;">

<!-- Wrapper -->
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0f1a;">
<tr><td align="center" style="padding:24px 16px 40px;">
<table width="100%" style="max-width:600px;" cellpadding="0" cellspacing="0">

  <!-- HERO HEADER — Spotify-Wrapped energy -->
  <tr><td style="background:linear-gradient(160deg,#312e81 0%,#1e1b4b 40%,#0f172a 100%);border-radius:20px 20px 0 0;padding:40px 32px 32px;text-align:center;">
    <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:#818cf8;">◉ RECOM</p>
    <h1 style="margin:0 0 8px;font-size:38px;font-weight:800;color:white;line-height:1.1;letter-spacing:-1px;">Your Week<br><span style="color:#818cf8;">in Events</span></h1>
    <p style="margin:0;font-size:15px;color:rgba(255,255,255,.6);">{{ week_of }}</p>
    {% if top_recs %}
    <!-- Stats pills -->
    <div style="margin-top:24px;display:inline-flex;gap:10px;flex-wrap:wrap;justify-content:center;">
      <span style="background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);color:white;border-radius:20px;padding:6px 16px;font-size:13px;font-weight:600;">{{ top_recs|length }} top picks</span>
      {% set social_cnt = top_recs|selectattr("vibe","equalto","social")|list|length %}
      {% set intel_cnt = top_recs|selectattr("vibe","equalto","intellectual")|list|length %}
      {% if social_cnt > 0 %}<span style="background:rgba(245,158,11,.2);border:1px solid rgba(245,158,11,.3);color:#fbbf24;border-radius:20px;padding:6px 16px;font-size:13px;font-weight:600;">{{ social_cnt }} social</span>{% endif %}
      {% if intel_cnt > 0 %}<span style="background:rgba(139,92,246,.2);border:1px solid rgba(139,92,246,.3);color:#a78bfa;border-radius:20px;padding:6px 16px;font-size:13px;font-weight:600;">{{ intel_cnt }} brainy</span>{% endif %}
    </div>
    {% endif %}
    <div style="margin-top:24px;">
      <a href="{{ dashboard_url }}{% if run_id %}/run/{{ run_id }}{% endif %}" style="display:inline-block;background:#818cf8;color:#1e1b4b;text-decoration:none;font-weight:800;font-size:14px;padding:12px 28px;border-radius:50px;letter-spacing:.3px;">Open calendar &rarr;</a>
    </div>
  </td></tr>

  <!-- White content area -->
  <tr><td style="background:white;border-radius:0 0 20px 20px;padding:32px 24px;">

    {# --- #1 FEATURED PICK --- #}
    {% if top_recs %}
    {% set hero = top_recs[0] %}
    <p style="margin:0 0 12px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#818cf8;">This week's top pick</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:linear-gradient(135deg,#eef2ff,#f5f3ff);border-radius:16px;margin-bottom:28px;overflow:hidden;">
      <tr>
        {% if hero.event.image_url %}
        <td style="width:100%;">
          <img src="{{ hero.event.image_url }}" alt="" width="100%" style="width:100%;height:180px;object-fit:cover;display:block;border-radius:16px 16px 0 0;">
        </td>
        {% endif %}
      </tr>
      <tr><td style="padding:20px 20px 24px;">
        <p style="margin:0 0 4px;font-size:11px;font-weight:700;color:#6d28d9;letter-spacing:1.5px;text-transform:uppercase;">
          {{ hero.event.start_time | format_dt }}{% if hero.event.location_name %} · {{ hero.event.location_name }}{% endif %}{% if dist_labels.get(hero.event.id) %} · {{ dist_labels[hero.event.id] }}{% endif %}
        </p>
        <h2 style="margin:0 0 8px;font-size:22px;font-weight:800;color:#1e1b4b;line-height:1.25;">
          {% if hero.event.url %}<a href="{{ hero.event.url }}" style="color:#1e1b4b;text-decoration:none;">{{ hero.event.title }}</a>{% else %}{{ hero.event.title }}{% endif %}
        </h2>
        {% if hero.match_reason %}<p style="margin:0 0 12px;font-size:14px;color:#4c1d95;line-height:1.5;background:rgba(139,92,246,.1);padding:10px 14px;border-radius:8px;border-left:3px solid #8b5cf6;">{{ hero.match_reason }}</p>{% endif %}
        {% if hero.event.price %}<p style="margin:0;font-size:13px;color:#6b7280;">{{ hero.event.price }}</p>{% endif %}
        <div style="margin-top:14px;">
          {% if user_token %}<a href="{{ dashboard_url }}/u/{{ user_token }}/event/{{ hero.event.id }}.ics" style="display:inline-block;background:#16a34a;color:white;text-decoration:none;font-weight:700;font-size:14px;padding:10px 24px;border-radius:50px;">&#128197; Add to my week</a>{% endif %}
          {% if hero.event.url %}<a href="{{ hero.event.url }}" style="display:inline-block;margin-left:8px;color:#4f46e5;text-decoration:none;font-weight:600;font-size:13px;">Get tickets &rarr;</a>{% endif %}
          <span style="display:inline-block;margin-left:8px;background:#dcfce7;color:#166534;font-weight:800;font-size:13px;padding:9px 14px;border-radius:50px;">{{ hero.score | int }}</span>
        </div>
      </td></tr>
    </table>

    {# --- FEEDBACK SECTION --- #}
    {% if feedback_items %}
    <div style="margin:20px 0;padding:16px 20px;background:#faf5ff;border-radius:14px;border:1px solid #e9d5ff;">
      <p style="margin:0 0 12px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#7c3aed;">Your feedback is working</p>
      {% for fb in feedback_items %}
      {% if fb.rating >= 4 %}
      <p style="margin:0 0 4px;font-size:14px;color:#1e293b;">
        You loved <strong>{{ fb.title }}</strong> {{ "&starf;" * fb.rating }}
        {% if fb.similar_picks %}&mdash; here are {{ fb.similar_picks | length }} more like it:{% endif %}
      </p>
      {% if fb.similar_picks %}
      <p style="margin:0 0 12px;font-size:13px;color:#6d28d9;">
        {% for pick in fb.similar_picks %}{{ pick.event.title }}{% if not loop.last %} &middot; {% endif %}{% endfor %}
      </p>
      {% endif %}
      {% elif fb.rating <= 2 %}
      <p style="margin:0 0 12px;font-size:13px;color:#6b7280;">
        {{ fb.title }} {{ "&starf;" * fb.rating }} &mdash; showing fewer of those
      </p>
      {% endif %}
      {% endfor %}
    </div>
    {% endif %}

    {# --- TOP PICKS 2-10 --- #}
    <p style="margin:0 0 14px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#9ca3af;">More top picks</p>
    {% for r in top_recs[1:] %}
    {% set vibe_color = "#f59e0b" if r.vibe == "social" else ("#8b5cf6" if r.vibe == "intellectual" else "#3b82f6") %}
    {% set score_bg = "#dcfce7" if r.score >= 70 else ("#fef3c7" if r.score >= 50 else "#f3f4f6") %}
    {% set score_color = "#166534" if r.score >= 70 else ("#92400e" if r.score >= 50 else "#6b7280") %}
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px;border-radius:12px;border:1px solid #f3f4f6;overflow:hidden;">
      <tr>
        {% if r.event.image_url %}
        <td style="width:72px;vertical-align:top;">
          <img src="{{ r.event.image_url }}" alt="" width="72" height="72" style="display:block;object-fit:cover;width:72px;height:72px;border-radius:12px 0 0 12px;">
        </td>
        {% endif %}
        <td style="padding:12px 14px;vertical-align:top;border-left:4px solid {{ vibe_color }};">
          <p style="margin:0 0 2px;font-size:14px;font-weight:700;line-height:1.3;">
            {% if r.event.url %}<a href="{{ r.event.url }}" style="color:#111827;text-decoration:none;">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
            <span style="display:inline-block;background:{{ score_bg }};color:{{ score_color }};font-size:11px;font-weight:800;padding:1px 8px;border-radius:8px;margin-left:6px;">{{ r.score | int }}</span>
          </p>
          <p style="margin:0 0 4px;font-size:12px;color:#6b7280;">{{ r.event.start_time | format_dt }}{% if r.event.location_name %} · {{ r.event.location_name }}{% endif %}{% if r.event.price %} · {{ r.event.price }}{% endif %}{% if dist_labels.get(r.event.id) %} · <span style="color:#059669;">{{ dist_labels[r.event.id] }}</span>{% endif %}</p>
          {% if r.match_reason %}<p style="margin:0;font-size:12px;color:#6d28d9;line-height:1.35;">{{ r.match_reason }}</p>{% endif %}
        </td>
      </tr>
    </table>
    {% endfor %}
    {% endif %}

    {# --- FREE PICKS --- #}
    {% if free_picks %}
    <div style="margin:28px 0 0;padding:16px 20px;background:#f0fdf4;border-radius:14px;border:1px solid #bbf7d0;">
      <p style="margin:0 0 12px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#059669;">Free this week</p>
      {% for r in free_picks %}
      <p style="margin:0 0 3px;font-size:14px;font-weight:600;color:#065f46;">
        {% if r.event.url %}<a href="{{ r.event.url }}" style="color:#065f46;text-decoration:none;">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
      </p>
      <p style="margin:0 0 10px;font-size:12px;color:#047857;">{{ r.event.start_time | format_dt }}{% if r.event.location_name %} · {{ r.event.location_name }}{% endif %}</p>
      {% endfor %}
    </div>
    {% endif %}

    {# --- BUCKET LIST --- #}
    {% if bucket_suggestions %}
    <div style="margin:28px 0 0;padding:20px;background:#f0fdf4;border-radius:14px;border:1px solid #bbf7d0;">
      <p style="margin:0 0 12px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#059669;">This week you could also...</p>
      {% for s in bucket_suggestions %}
      <p style="margin:0 0 8px;font-size:14px;color:#065f46;"><strong>{{ s.activity }}</strong>{% if s.best_day %} — {{ s.best_day }}{% endif %}</p>
      <p style="margin:0 0 14px;font-size:13px;color:#047857;line-height:1.4;">{{ s.nudge }}</p>
      {% endfor %}
    </div>
    {% endif %}

    {# --- CLUBS & CLASSES --- #}
    {% if clubs_classes %}
    <div style="margin-top:28px;">
      <p style="margin:0 0 14px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#9ca3af;">Clubs, Classes & Memberships</p>
      {% for r in clubs_classes %}
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px;border-radius:12px;border:1px solid #f3f4f6;overflow:hidden;">
        <tr>
          <td style="padding:12px 14px;border-left:4px solid #8b5cf6;">
            <p style="margin:0 0 2px;font-size:14px;font-weight:700;line-height:1.3;">
              {% if r.event.url %}<a href="{{ r.event.url }}" style="color:#111827;text-decoration:none;">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
              <span style="display:inline-block;background:#ede9fe;color:#6d28d9;font-size:10px;font-weight:700;padding:1px 7px;border-radius:8px;margin-left:4px;text-transform:uppercase;">{{ r.event_type }}</span>
            </p>
            <p style="margin:0 0 4px;font-size:12px;color:#6b7280;">{{ r.event.start_time | format_dt }}{% if r.event.location_name %} · {{ r.event.location_name }}{% endif %}</p>
            {% if r.match_reason %}<p style="margin:0;font-size:12px;color:#6d28d9;">{{ r.match_reason }}</p>{% endif %}
          </td>
        </tr>
      </table>
      {% endfor %}
    </div>
    {% endif %}

    {# --- BY DAY --- #}
    {% if by_day %}
    <div style="margin-top:32px;padding-top:24px;border-top:1px solid #f3f4f6;">
      <p style="margin:0 0 16px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#9ca3af;">Browse by day</p>
      {% for day_label, day_events in by_day %}
      <p style="margin:20px 0 8px;font-size:14px;font-weight:700;color:#1e40af;padding:4px 0;border-bottom:1px solid #dbeafe;">{{ day_label }}</p>
      {% for r in day_events %}
      {% set vibe_color = "#f59e0b" if r.vibe == "social" else ("#8b5cf6" if r.vibe == "intellectual" else "#3b82f6") %}
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;border-radius:10px;background:#fafafa;overflow:hidden;">
        <tr>
          <td style="padding:10px 14px;border-left:3px solid {{ vibe_color }};">
            <p style="margin:0 0 2px;font-size:13px;font-weight:700;">
              {% if r.event.url %}<a href="{{ r.event.url }}" style="color:#1a1a1a;text-decoration:none;">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
            </p>
            <p style="margin:0;font-size:12px;color:#6b7280;">{{ r.event.start_time | format_dt }}{% if r.event.location_name %} · {{ r.event.location_name }}{% endif %}{% if r.event.price %} · {{ r.event.price }}{% endif %}</p>
            {% if r.match_reason %}<p style="margin:4px 0 0;font-size:11px;color:#7c3aed;">{{ r.match_reason }}</p>{% endif %}
          </td>
        </tr>
      </table>
      {% endfor %}
      {% endfor %}
    </div>
    {% endif %}

  </td></tr>

  <!-- FOOTER -->
  <tr><td style="padding:24px 16px;text-align:center;">
    <a href="{{ dashboard_url }}" style="display:inline-block;margin-bottom:12px;color:#818cf8;text-decoration:none;font-size:14px;font-weight:600;">Open full calendar &rarr;</a><br>
    <a href="{{ dashboard_url }}/feed.ics" style="color:#6b7280;text-decoration:none;font-size:12px;">Subscribe to iCal feed</a>
    <p style="margin:12px 0 0;font-size:11px;color:#4b5563;">Curated by <strong style="color:#818cf8;">recom</strong> &middot; AI cost: ${{ "%.4f" | format(total_cost) }}</p>
  </td></tr>

</table>
</td></tr>
</table>

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
    home_lat: float = 42.3736,
    home_lon: float = -71.1097,
    recent_ratings: list[dict] | None = None,
    user_token: str = "",
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

    # Free picks (score >= 30, not already shown, price indicates free)
    shown_ids_all = top_ids | {id(r) for r in clubs_classes}
    for day_items in by_day:
        for r in day_items[1]:
            shown_ids_all.add(id(r))
    for r in undated:
        shown_ids_all.add(id(r))

    def _is_free(r: RankedEvent) -> bool:
        price = (r.event.price or "").lower()
        return price in ("", "free", "$0", "0") or price.startswith("free")

    free_picks = [
        r for r in kept
        if id(r) not in shown_ids_all and _is_free(r) and r.score >= 30
    ][:4]

    subject = f"Your Week: {len(top_recs)} top picks, {len(kept)} events total ({week_of})"

    # Pre-compute distance strings for all events
    dist_filter = _make_dist_filter(home_lat, home_lon)
    dist_labels: dict[str, str] = {}
    for r in ranked_events:
        d = dist_filter(r.event)
        if d:
            dist_labels[r.event.id] = d

    # Build feedback items from recent ratings
    feedback_items: list[dict] = []
    if recent_ratings:
        for rating in recent_ratings[:3]:
            fb: dict = {"title": rating["title"], "rating": rating["rating"], "similar_picks": []}
            if rating["rating"] >= 4:
                # Find similar events in current run (same vibe or category)
                r_vibe = rating.get("vibe") or ""
                r_cat = rating.get("category") or ""
                similar = [
                    r for r in kept
                    if id(r) not in top_ids
                    and ((r_vibe and r.vibe == r_vibe) or (r_cat and r.event.category == r_cat))
                ][:3]
                fb["similar_picks"] = similar
            feedback_items.append(fb)

    html_body = _DIGEST_TEMPLATE.render(
        subject=subject,
        week_of=week_of,
        top_recs=top_recs,
        clubs_classes=clubs_classes,
        by_day=by_day,
        undated=undated,
        free_picks=free_picks,
        bucket_suggestions=bucket_suggestions or [],
        total_cost=total_cost,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        profile=profile,
        dashboard_url=dashboard_url,
        run_id=run_id,
        dist_labels=dist_labels,
        feedback_items=feedback_items,
        user_token=user_token,
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
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#0f0f1a;margin:0;padding:0;color:#1a1a1a;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0f1a;">
<tr><td align="center" style="padding:20px 16px 32px;">
<table width="100%" style="max-width:600px;" cellpadding="0" cellspacing="0">

  <!-- Header -->
  <tr><td style="background:linear-gradient(135deg,#1e1b4b,#312e81);border-radius:16px 16px 0 0;padding:28px 28px 24px;text-align:center;">
    <p style="margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:3px;color:#818cf8;text-transform:uppercase;">◉ RECOM · Daily</p>
    <h1 style="margin:0;font-size:28px;font-weight:800;color:white;letter-spacing:-.5px;">
      <a href="{{ dashboard_url }}" style="color:white;text-decoration:none;">{{ day_label }}</a>
    </h1>
    <p style="margin:6px 0 0;font-size:14px;color:rgba(255,255,255,.6);">{{ event_count }} picks for you today</p>
  </td></tr>

  <!-- Content -->
  <tr><td style="background:white;border-radius:0 0 16px 16px;padding:24px 20px;">

    {% for r in events %}
    {% set vibe_color = "#f59e0b" if r.vibe == "social" else ("#8b5cf6" if r.vibe == "intellectual" else "#3b82f6") %}
    {% set score_bg = "#dcfce7" if r.score >= 70 else ("#fef3c7" if r.score >= 50 else "#f3f4f6") %}
    {% set score_color = "#166534" if r.score >= 70 else ("#92400e" if r.score >= 50 else "#6b7280") %}
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;border-radius:12px;border:1px solid #f3f4f6;overflow:hidden;">
      {% if r.event.image_url %}
      <tr><td><img src="{{ r.event.image_url }}" alt="" width="100%" style="width:100%;height:140px;object-fit:cover;display:block;border-radius:12px 12px 0 0;"></td></tr>
      {% endif %}
      <tr>
        <td style="padding:14px 16px;border-left:4px solid {{ vibe_color }};">
          <p style="margin:0 0 3px;font-size:15px;font-weight:700;line-height:1.3;">
            {% if r.event.url %}<a href="{{ r.event.url }}" style="color:#111827;text-decoration:none;">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
            <span style="display:inline-block;background:{{ score_bg }};color:{{ score_color }};font-size:11px;font-weight:800;padding:1px 8px;border-radius:8px;margin-left:6px;">{{ r.score | int }}</span>
          </p>
          <p style="margin:0 0 6px;font-size:12px;color:#6b7280;">
            {{ r.event.start_time | format_dt }}{% if r.event.location_name %} · {{ r.event.location_name }}{% endif %}{% if r.event.price %} · {{ r.event.price }}{% endif %}
          </p>
          {% if r.match_reason %}<p style="margin:0 0 8px;font-size:13px;color:#6d28d9;background:#faf5ff;padding:7px 10px;border-radius:7px;line-height:1.4;">{{ r.match_reason }}</p>{% endif %}
          {% if friend_rsvps and r.event.id in friend_rsvps %}
          <p style="margin:0 0 8px;">
            {% for rv in friend_rsvps[r.event.id] %}
            <span style="display:inline-block;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600;margin-right:3px;{% if rv.status == 'going' %}background:#dcfce7;color:#166534;{% elif rv.status == 'maybe' %}background:#fef3c7;color:#92400e;{% else %}background:#fee2e2;color:#991b1b;{% endif %}">{{ rv.user_name }} {{ 'going' if rv.status == 'going' else ('maybe' if rv.status == 'maybe' else "can't") }}</span>
            {% endfor %}
          </p>
          {% endif %}
          <p style="margin:0;">
            {% if user_token %}
            <a href="{{ dashboard_url }}/u/{{ user_token }}/event/{{ r.event.id }}.ics" style="display:inline-block;font-size:12px;font-weight:700;color:white;background:#16a34a;text-decoration:none;padding:5px 16px;border-radius:10px;margin-right:6px;">&#128197; Add to my week</a>
            <a href="{{ dashboard_url }}/api/rsvp-link?event_id={{ r.event.id }}&status=maybe&u={{ user_token }}&title={{ r.event.title | urlencode }}" style="display:inline-block;font-size:12px;font-weight:600;color:#92400e;text-decoration:none;border:1.5px solid #fde68a;padding:4px 14px;border-radius:10px;margin-right:6px;">Maybe</a>
            {% endif %}
            {% if r.event.url %}<a href="{{ r.event.url }}" style="display:inline-block;font-size:12px;font-weight:600;color:#4f46e5;text-decoration:none;padding:4px 0;">Get tickets &rarr;</a>{% endif %}
          </p>
        </td>
      </tr>
    </table>
    {% endfor %}

    {% if not events %}
    <p style="color:#9ca3af;font-style:italic;font-size:14px;text-align:center;padding:20px 0;">Nothing great on the calendar today. Check the <a href="{{ dashboard_url }}" style="color:#4f46e5;">full calendar</a>.</p>
    {% endif %}

    {% if bucket_suggestions %}
    <div style="margin-top:20px;padding:16px;background:#f0fdf4;border-radius:12px;border:1px solid #bbf7d0;">
      <p style="margin:0 0 10px;font-size:11px;font-weight:700;letter-spacing:1.5px;color:#059669;text-transform:uppercase;">You could also today...</p>
      {% for s in bucket_suggestions %}
      <p style="margin:0 0 4px;font-size:14px;font-weight:600;color:#065f46;">{{ s.activity }}</p>
      <p style="margin:0 0 10px;font-size:13px;color:#047857;">{{ s.nudge }}</p>
      {% endfor %}
    </div>
    {% endif %}

  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:20px 16px;text-align:center;">
    <a href="{{ dashboard_url }}" style="color:#818cf8;text-decoration:none;font-size:13px;font-weight:600;">Full calendar &rarr;</a>
    &nbsp;·&nbsp;
    <a href="{{ dashboard_url }}/feed.ics" style="color:#6b7280;text-decoration:none;font-size:12px;">Subscribe to iCal</a>
  </td></tr>

</table>
</td></tr>
</table>

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


# ---------------------------------------------------------------------------
# Weekend preview email
# ---------------------------------------------------------------------------

_WEEKEND_TEMPLATE = _env.from_string(
    """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ subject }}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f8fafc;margin:0;padding:0;color:#1a1a1a;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:20px 16px 32px;">
<table width="100%" style="max-width:600px;" cellpadding="0" cellspacing="0">

  <!-- Header -->
  <tr><td style="background:linear-gradient(135deg,#1e1b4b,#312e81);border-radius:16px 16px 0 0;padding:28px 28px 24px;text-align:center;">
    <p style="margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:3px;color:#818cf8;text-transform:uppercase;">&loz; RECOM &middot; Weekend</p>
    <h1 style="margin:0;font-size:26px;font-weight:800;color:white;letter-spacing:-.5px;">
      <a href="{{ dashboard_url }}" style="color:white;text-decoration:none;">Your Weekend</a>
    </h1>
    <p style="margin:8px 0 0;font-size:15px;color:rgba(255,255,255,.7);">{{ total_count }} things to do</p>
  </td></tr>

  <!-- Content -->
  <tr><td style="background:white;border-radius:0 0 16px 16px;padding:24px 20px;">

    {% for day_label, events in days %}
    <p style="margin:{% if not loop.first %}24px{% else %}0{% endif %} 0 12px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#4f46e5;">{{ day_label }}</p>

    {% for r in events %}
    {% set vibe_color = "#f59e0b" if r.vibe == "social" else ("#8b5cf6" if r.vibe == "intellectual" else "#3b82f6") %}
    {% set score_bg = "#dcfce7" if r.score >= 70 else ("#fef3c7" if r.score >= 50 else "#f3f4f6") %}
    {% set score_color = "#166534" if r.score >= 70 else ("#92400e" if r.score >= 50 else "#6b7280") %}
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;border-radius:10px;border:1px solid #f3f4f6;overflow:hidden;">
      <tr>
        <td style="padding:12px 14px;border-left:4px solid {{ vibe_color }};">
          <p style="margin:0 0 2px;font-size:14px;font-weight:700;line-height:1.3;">
            {% if r.event.url %}<a href="{{ r.event.url }}" style="color:#111827;text-decoration:none;">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
            <span style="display:inline-block;background:{{ score_bg }};color:{{ score_color }};font-size:11px;font-weight:800;padding:1px 8px;border-radius:8px;margin-left:6px;">{{ r.score | int }}</span>
          </p>
          <p style="margin:0 0 4px;font-size:12px;color:#6b7280;">{{ r.event.start_time | format_dt }}{% if r.event.location_name %} &middot; {{ r.event.location_name }}{% endif %}{% if r.event.price %} &middot; {{ r.event.price }}{% endif %}</p>
          {% if r.match_reason %}<p style="margin:0 0 6px;font-size:12px;color:#6d28d9;">{{ r.match_reason }}</p>{% endif %}
          {% if user_token %}
          <a href="{{ dashboard_url }}/u/{{ user_token }}/event/{{ r.event.id }}.ics" style="display:inline-block;font-size:12px;font-weight:700;color:white;background:#16a34a;text-decoration:none;padding:4px 14px;border-radius:8px;">&#128197; Add to my week</a>
          {% endif %}
        </td>
      </tr>
    </table>
    {% endfor %}
    {% endfor %}

    {% if not days %}
    <p style="color:#9ca3af;text-align:center;padding:20px 0;">No weekend events found. Check the <a href="{{ dashboard_url }}" style="color:#4f46e5;">full calendar</a>.</p>
    {% endif %}

    <div style="text-align:center;margin-top:20px;">
      <a href="{{ dashboard_url }}/?u={{ user_token }}" style="display:inline-block;background:#f1f5f9;color:#374151;text-decoration:none;font-weight:600;font-size:14px;padding:12px 28px;border-radius:50px;">
        See all events &rarr;
      </a>
    </div>

  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px;text-align:center;">
    <p style="margin:0;font-size:12px;color:#9ca3af;">
      Powered by <a href="{{ dashboard_url }}" style="color:#6366f1;text-decoration:none;">Recom</a> &middot; Discover Weekly for your real life
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""
)


def compose_weekend_email(
    ranked_events: list[RankedEvent],
    dashboard_url: str = "https://recom.arthgupta.dev",
    user_token: str = "",
) -> tuple[str, str] | None:
    """Build a Thursday weekend preview email with Fri/Sat/Sun events by day.

    Returns (subject, html_body) or None if fewer than 3 weekend events.
    """
    from datetime import timedelta

    now = datetime.now()
    # Find next Fri/Sat/Sun
    days_ahead = (4 - now.weekday()) % 7  # 4 = Friday
    if days_ahead == 0 and now.hour >= 18:
        days_ahead = 7
    friday = (now + timedelta(days=days_ahead)).date()
    saturday = friday + timedelta(days=1)
    sunday = friday + timedelta(days=2)
    weekend_dates = {
        friday.isoformat(): friday.strftime("%A, %B %-d"),
        saturday.isoformat(): saturday.strftime("%A, %B %-d"),
        sunday.isoformat(): sunday.strftime("%A, %B %-d"),
    }

    # Filter to weekend events
    day_groups: dict[str, list[RankedEvent]] = {d: [] for d in weekend_dates}
    for r in ranked_events:
        if not r.keep or r.score < 25 or r.event.start_time is None:
            continue
        st = r.event.start_time
        event_date = (st.replace(tzinfo=None) if st.tzinfo else st).date().isoformat()
        if event_date in day_groups:
            day_groups[event_date].append(r)

    # Sort each day by score, limit to 5
    for date_key in day_groups:
        day_groups[date_key].sort(key=lambda r: r.score, reverse=True)
        day_groups[date_key] = day_groups[date_key][:5]

    # Build ordered list of (label, events) tuples
    days = [(weekend_dates[d], day_groups[d]) for d in sorted(day_groups) if day_groups[d]]
    total_count = sum(len(evts) for _, evts in days)

    if total_count < 3:
        return None

    subject = f"Your weekend: {total_count} things to do"
    html_body = _WEEKEND_TEMPLATE.render(
        subject=subject,
        total_count=total_count,
        days=days,
        dashboard_url=dashboard_url,
        user_token=user_token,
    )
    return subject, html_body


# ---------------------------------------------------------------------------
# Welcome email
# ---------------------------------------------------------------------------

_WELCOME_TEMPLATE = _env.from_string(
    """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ subject }}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f8fafc;margin:0;padding:0;color:#1a1a1a;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:20px 16px 32px;">
<table width="100%" style="max-width:600px;" cellpadding="0" cellspacing="0">

  <!-- Header -->
  <tr><td style="background:linear-gradient(135deg,#1e1b4b,#312e81);border-radius:16px 16px 0 0;padding:32px 28px;text-align:center;">
    <p style="margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:3px;color:#818cf8;text-transform:uppercase;">&loz; RECOM</p>
    <h1 style="margin:0;font-size:26px;font-weight:800;color:white;letter-spacing:-.5px;">Your event calendar is ready</h1>
    <p style="margin:8px 0 0;font-size:15px;color:rgba(255,255,255,.7);">We found {{ event_count }} events this week that match your interests.</p>
  </td></tr>

  <!-- Content -->
  <tr><td style="background:white;padding:28px 24px;">

    <p style="font-size:15px;color:#374151;line-height:1.6;margin:0 0 24px;">
      Hey {{ user_name }}, get your picks on your calendar in one click:
    </p>

    <!-- Calendar buttons -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
      <tr>
        <td align="center" style="padding:6px;">
          <a href="{{ webcal_url }}" style="display:block;background:#1e1b4b;color:white;text-decoration:none;font-weight:700;font-size:14px;padding:14px 20px;border-radius:12px;text-align:center;">
            &#127823; Add to Apple Calendar
          </a>
        </td>
      </tr>
      <tr>
        <td align="center" style="padding:6px;">
          <a href="{{ gcal_url }}" style="display:block;background:#4285f4;color:white;text-decoration:none;font-weight:700;font-size:14px;padding:14px 20px;border-radius:12px;text-align:center;">
            &#128197; Add to Google Calendar
          </a>
        </td>
      </tr>
      <tr>
        <td align="center" style="padding:6px;">
          <a href="{{ outlook_url }}" style="display:block;background:#0078d4;color:white;text-decoration:none;font-weight:700;font-size:14px;padding:14px 20px;border-radius:12px;text-align:center;">
            &#128233; Add to Outlook
          </a>
        </td>
      </tr>
    </table>

    <p style="font-size:13px;color:#9ca3af;text-align:center;margin:0 0 24px;">
      Your calendar updates automatically every week with fresh picks.
    </p>

    {% if top_events %}
    <div style="border-top:1px solid #e5e7eb;padding-top:20px;margin-top:4px;">
      <p style="margin:0 0 14px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#9ca3af;">Preview: this week&apos;s top picks</p>
      {% for r in top_events %}
      {% set vibe_color = "#f59e0b" if r.vibe == "social" else ("#8b5cf6" if r.vibe == "intellectual" else "#3b82f6") %}
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px;border-radius:10px;border:1px solid #f3f4f6;overflow:hidden;">
        <tr>
          <td style="padding:12px 14px;border-left:4px solid {{ vibe_color }};">
            <p style="margin:0 0 2px;font-size:14px;font-weight:700;line-height:1.3;">
              {% if r.event.url %}<a href="{{ r.event.url }}" style="color:#111827;text-decoration:none;">{{ r.event.title }}</a>{% else %}{{ r.event.title }}{% endif %}
            </p>
            <p style="margin:0 0 4px;font-size:12px;color:#6b7280;">{{ r.event.start_time | format_dt }}{% if r.event.location_name %} &middot; {{ r.event.location_name }}{% endif %}</p>
            {% if user_token %}
            <a href="{{ dashboard_url }}/u/{{ user_token }}/event/{{ r.event.id }}.ics" style="display:inline-block;font-size:12px;font-weight:700;color:white;background:#16a34a;text-decoration:none;padding:4px 14px;border-radius:8px;margin-top:4px;">&#128197; Add to my week</a>
            {% endif %}
          </td>
        </tr>
      </table>
      {% endfor %}
    </div>
    {% endif %}

    <div style="text-align:center;margin-top:20px;">
      <a href="{{ dashboard_url }}/?u={{ user_token }}" style="display:inline-block;background:#f1f5f9;color:#374151;text-decoration:none;font-weight:600;font-size:14px;padding:12px 28px;border-radius:50px;">
        See all {{ event_count }} events &rarr;
      </a>
    </div>

  </td></tr>

  <!-- Footer -->
  <tr><td style="background:white;border-radius:0 0 16px 16px;border-top:1px solid #f3f4f6;padding:16px 24px;text-align:center;">
    <p style="margin:0;font-size:12px;color:#9ca3af;">
      Powered by <a href="{{ dashboard_url }}" style="color:#6366f1;text-decoration:none;">Recom</a> &middot; Discover Weekly for your real life
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""
)


def compose_welcome_email(
    user_name: str,
    user_token: str,
    event_count: int,
    top_events: list[RankedEvent],
    dashboard_url: str = "https://recom.arthgupta.dev",
) -> tuple[str, str]:
    """Build the welcome/onboarding email for a new user.

    Returns (subject, html_body).
    """
    import urllib.parse as _urlparse

    feed_url = f"{dashboard_url}/u/{user_token}/feed.ics"
    webcal_url = feed_url.replace("https://", "webcal://").replace("http://", "webcal://")
    gcal_url = f"https://calendar.google.com/calendar/r?cid={_urlparse.quote(feed_url, safe='')}"
    outlook_url = feed_url

    subject = "Your event calendar is ready"
    html_body = _WELCOME_TEMPLATE.render(
        subject=subject,
        user_name=user_name or "there",
        user_token=user_token,
        event_count=event_count,
        top_events=top_events[:3],
        webcal_url=webcal_url,
        gcal_url=gcal_url,
        outlook_url=outlook_url,
        dashboard_url=dashboard_url,
    )
    return subject, html_body
