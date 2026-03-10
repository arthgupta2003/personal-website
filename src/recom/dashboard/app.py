from __future__ import annotations

import json
import logging

from datetime import datetime

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.responses import RedirectResponse

from recom.config import Settings
from recom.db import Database

logger = logging.getLogger(__name__)

app = FastAPI(title="Recom Dashboard")

_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        settings = Settings()
        _db = Database(settings.db_path)
    return _db


LAYOUT_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recom - __TITLE__</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f5f5f5; color: #333; line-height: 1.6; padding: 20px; max-width: 1200px; margin: 0 auto; }
  h1 { margin-bottom: 20px; color: #1a1a1a; }
  h2 { margin: 20px 0 10px; color: #2a2a2a; }
  a { color: #2563eb; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .card { background: white; border-radius: 8px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .badge-green { background: #dcfce7; color: #166534; }
  .badge-yellow { background: #fef3c7; color: #92400e; }
  .badge-gray { background: #f3f4f6; color: #374151; }
  .badge-red { background: #fee2e2; color: #991b1b; }
  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
  th { background: #f9fafb; font-weight: 600; font-size: 13px; text-transform: uppercase; color: #6b7280; cursor: pointer; }
  th:hover { background: #f3f4f6; }
  tr:hover { background: #f9fafb; }
  .stat { display: inline-block; margin-right: 20px; }
  .stat-value { font-size: 24px; font-weight: 700; color: #1a1a1a; }
  .stat-label { font-size: 13px; color: #6b7280; }
  .nav { margin-bottom: 20px; }
  .nav a { margin-right: 16px; font-weight: 500; }
  .score-bar { height: 8px; border-radius: 4px; background: #e5e7eb; }
  .score-fill { height: 100%; border-radius: 4px; }
  .filter-row { margin-bottom: 16px; }
  .filter-row input, .filter-row select { padding: 6px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; }
  .interests-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
  .interest-tag { padding: 4px 12px; border-radius: 16px; background: #ede9fe; color: #5b21b6; font-size: 13px; }
  .cost-box { background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; padding: 12px; margin: 10px 0; }
</style>
</head>
<body>
<div class="nav">
  <a href="/">Calendar</a>
  <a href="/attended">Attended</a>
  <a href="/feed.ics">Subscribe</a>
  <a href="/group/create">Groups</a>
  <span style="color:#d1d5db">|</span>
  <a href="/admin" style="color:#9ca3af;font-size:13px">Admin</a>
</div>
"""

LAYOUT_FOOT = """
<script>
document.querySelectorAll('th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const table = th.closest('table');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const col = th.dataset.sort;
    const idx = Array.from(th.parentElement.children).indexOf(th);
    const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
    th.dataset.dir = dir;
    rows.sort((a, b) => {
      let va = a.children[idx]?.dataset.val || a.children[idx]?.textContent || '';
      let vb = b.children[idx]?.dataset.val || b.children[idx]?.textContent || '';
      const na = parseFloat(va), nb = parseFloat(vb);
      if (!isNaN(na) && !isNaN(nb)) return dir === 'asc' ? na - nb : nb - na;
      return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    });
    rows.forEach(r => tbody.appendChild(r));
  });
});
document.querySelectorAll('.filter-input').forEach(input => {
  input.addEventListener('input', () => {
    const table = input.closest('.card').querySelector('table');
    const query = input.value.toLowerCase();
    table.querySelectorAll('tbody tr').forEach(row => {
      row.style.display = row.textContent.toLowerCase().includes(query) ? '' : 'none';
    });
  });
});
</script>
</body></html>"""


def score_badge(score: float | None) -> str:
    if score is None:
        return '<span class="badge badge-gray">N/A</span>'
    s = float(score)
    if s >= 70:
        cls = "badge-green"
    elif s >= 40:
        cls = "badge-yellow"
    elif s >= 25:
        cls = "badge-gray"
    else:
        cls = "badge-red"
    return f'<span class="badge {cls}">{s:.0f}</span>'


@app.get("/admin", response_class=HTMLResponse)
async def run_history():
    db = get_db()
    runs = db.get_runs()
    rows_html = ""
    for r in runs:
        wip = ""
        if r['top_score'] is None and (r['event_count'] or 0) == 0 and r['cost_total'] < 0.05:
            wip = ' <span class="badge badge-yellow">⏳ In Progress</span>'
        rows_html += f"""<tr>
            <td><a href="/run/{r['id']}">Run #{r['id']}</a>{wip}</td>
            <td>{r['timestamp'][:16]}</td>
            <td>{r['event_count'] or 0}</td>
            <td>{score_badge(r['top_score'])}</td>
            <td>${r['cost_total']:.4f}</td>
            <td>{r['model_used'] or ''}</td>
        </tr>"""
    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__","Run History") + f"""
    <h1>Run History</h1>
    <table>
        <thead><tr>
            <th data-sort="id">Run</th>
            <th data-sort="date">Date</th>
            <th data-sort="events">Events</th>
            <th data-sort="score">Top Score</th>
            <th data-sort="cost">Cost</th>
            <th>Model</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
    """ + LAYOUT_FOOT)


@app.get("/run/{run_id}", response_class=HTMLResponse)
async def run_detail(run_id: int):
    db = get_db()
    run = db.get_run(run_id)
    if not run:
        return HTMLResponse("<h1>Run not found</h1>", status_code=404)

    events = db.get_run_events(run_id)
    costs = db.get_run_costs(run_id)
    stats = db.get_run_source_stats(run_id)
    ingest_stats = db.get_ingest_stats(run_id)

    # Interest profile
    profile_html = ""
    if run["interest_profile_json"]:
        profile = json.loads(run["interest_profile_json"])
        profile_html = f'<p>{profile.get("summary", "")}</p><div class="interests-list">'
        for i in profile.get("interests", []):
            conf = i.get("confidence", 0)
            profile_html += f'<span class="interest-tag">{i["topic"]} ({conf:.0%})</span>'
        profile_html += "</div>"

    # Source stats
    stats_html = ""
    for s in stats:
        err = f' <span class="badge badge-red">{s["error_message"]}</span>' if s["error_message"] else ""
        stats_html += f"<tr><td>{s['source_name']}</td><td>{s['events_found']}</td><td>{err}</td></tr>"

    # Cost breakdown
    cost_html = ""
    for c in costs:
        cost_html += f"<tr><td>{c['call_type']}</td><td>{c['model']}</td><td>{c['tokens_in']:,}</td><td>{c['tokens_out']:,}</td><td>${c['cost_usd']:.4f}</td></tr>"

    # Events table
    events_html = ""
    for e in events:
        keep = e.get("keep")
        keep_str = "Yes" if keep else "No"
        reason = e.get("match_reason") or e.get("filter_reason") or ""
        # Short description: strip HTML, truncate
        import re as _re
        raw_desc = e.get("description") or ""
        clean_desc = _re.sub(r'<[^>]+>', '', raw_desc).replace("&nbsp;", " ").replace("&#8217;", "'").replace("&#8220;", '"').replace("&#8221;", '"').strip()
        short_desc = clean_desc[:100] + ("..." if len(clean_desc) > 100 else "") if clean_desc else ""
        events_html += f"""<tr>
            <td><a href="{e.get('url', '#')}" target="_blank">{e['title'][:60]}</a>
                {f'<div style="font-size:11px;color:#6b7280;margin-top:2px">{short_desc}</div>' if short_desc else ''}
            </td>
            <td>{e.get('source', '')}</td>
            <td>{(e.get('start_time') or '')[:16]}</td>
            <td>{e.get('location_name', '')[:30]}</td>
            <td>{e.get('price') or 'Free'}</td>
            <td data-val="{e.get('interest_score', 0)}">{score_badge(e.get('interest_score'))}</td>
            <td data-val="{e.get('social_score', 0)}">{score_badge(e.get('social_score'))}</td>
            <td data-val="{e.get('score', 0)}">{score_badge(e.get('score'))}</td>
            <td>{keep_str}</td>
            <td title="{reason}">{reason[:80]}</td>
        </tr>"""

    # Detect in-progress run: has events but few/no rankings, or no source stats yet
    ranked_count = sum(1 for e in events if e.get("score") is not None and e.get("score", 0) > 0)
    is_wip = (len(events) > 0 and ranked_count < len(events) * 0.9) or (len(events) == 0 and len(stats) == 0)
    wip_banner = ""
    if is_wip:
        pct = int(ranked_count / len(events) * 100) if len(events) > 0 else 0
        wip_banner = f"""
        <div style="background: #fef3c7; border: 2px solid #f59e0b; border-radius: 8px; padding: 16px; margin-bottom: 16px; text-align: center;">
            <span style="font-size: 18px; font-weight: 700; color: #92400e;">
                ⏳ Run in progress — {ranked_count}/{len(events)} events ranked ({pct}%)
            </span>
            <p style="color: #92400e; margin-top: 4px; font-size: 14px;">
                Refresh this page to see updated results. Ranking ~40 events per batch.
            </p>
        </div>"""

    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__",f"Run #{run_id}") + f"""
    <h1>Run #{run_id} — {run['timestamp'][:16]}</h1>
    {wip_banner}

    <div style="margin: 16px 0;">
        <div class="stat"><div class="stat-value">{len(events)}</div><div class="stat-label">Events Found</div></div>
        <div class="stat"><div class="stat-value">${run['cost_total']:.4f}</div><div class="stat-label">Total Cost</div></div>
        <div class="stat"><div class="stat-value">{run['tokens_in_total']:,}</div><div class="stat-label">Input Tokens</div></div>
        <div class="stat"><div class="stat-value">{run['tokens_out_total']:,}</div><div class="stat-label">Output Tokens</div></div>
    </div>

    <h2>Data Sources (Ingest)</h2>
    <div class="card">{''.join(f'<div style="margin:4px 0"><strong>{s["source"]}</strong>: {s["item_count"]} items — {s["detail"]}</div>' for s in ingest_stats) if ingest_stats else '<p style="color:#9ca3af">No ingest stats recorded (older run)</p>'}</div>

    <h2>Interest Profile</h2>
    <div class="card">{profile_html}</div>

    <h2>Source Stats</h2>
    <table>
        <thead><tr><th>Source</th><th>Events</th><th>Errors</th></tr></thead>
        <tbody>{stats_html}</tbody>
    </table>

    <h2>Cost Breakdown</h2>
    <div class="cost-box">
        <table>
            <thead><tr><th>Call</th><th>Model</th><th>In Tokens</th><th>Out Tokens</th><th>Cost</th></tr></thead>
            <tbody>{cost_html}</tbody>
        </table>
    </div>

    <h2>All Events (Ranked)</h2>
    <div class="card">
        <div class="filter-row"><input class="filter-input" placeholder="Filter events..." style="width:100%;"></div>
        <table>
            <thead><tr>
                <th data-sort="title">Title</th>
                <th data-sort="source">Source</th>
                <th data-sort="date">Date</th>
                <th>Location</th>
                <th>Price</th>
                <th data-sort="interest">Interest</th>
                <th data-sort="social">Social</th>
                <th data-sort="total">Total</th>
                <th data-sort="keep">Keep</th>
                <th>Reason</th>
            </tr></thead>
            <tbody>{events_html}</tbody>
        </table>
    </div>
    """ + LAYOUT_FOOT)


@app.get("/admin/interests", response_class=HTMLResponse)
async def interests_page():
    db = get_db()
    profile = db.get_cached_interest_profile(max_age_days=30)
    if not profile:
        return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__","Interests") + """
        <h1>Interests</h1>
        <div class="card"><p>No interest profile yet. Run the pipeline first.</p></div>
        """ + LAYOUT_FOOT)

    # Get latest ingest stats for the data sources section
    runs = db.get_runs()
    ingest_stats = []
    if runs:
        for r in runs:
            ingest_stats = db.get_ingest_stats(r["id"])
            if ingest_stats:
                break

    # Data sources section
    sources_html = ""
    for s in ingest_stats:
        icon = {"YouTube": "🎬", "Spotify": "🎵", "Newsletters": "📧"}.get(s["source"], "📊")
        sources_html += f"""
        <div style="background: white; border-radius: 8px; padding: 16px; flex: 1; min-width: 180px;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center;">
            <div style="font-size: 28px; margin-bottom: 8px;">{icon}</div>
            <div style="font-size: 24px; font-weight: 700; color: #1a1a1a;">{s['item_count']}</div>
            <div style="font-size: 14px; color: #6b7280; font-weight: 600;">{s['source']}</div>
            <div style="font-size: 12px; color: #9ca3af; margin-top: 4px;">{s['detail']}</div>
        </div>"""

    # Manual keywords
    from pathlib import Path
    manual_path = Path("my_interests.txt")
    manual_keywords = []
    if manual_path.exists():
        manual_keywords = [l.strip() for l in manual_path.read_text().splitlines()
                          if l.strip() and not l.strip().startswith("#")]

    manual_html = ""
    if manual_keywords:
        tags = " ".join(f'<span style="display:inline-block; background:#ede9fe; color:#6d28d9; '
                        f'padding:4px 12px; border-radius:16px; font-size:13px; font-weight:600;">'
                        f'{kw}</span>' for kw in manual_keywords)
        manual_html = f"""
        <div class="card" style="border-left: 4px solid #8b5cf6;">
            <h3 style="margin-bottom: 8px; color: #6d28d9;">Manual Keywords (my_interests.txt)</h3>
            <div style="display: flex; flex-wrap: wrap; gap: 8px;">{tags}</div>
            <p style="font-size: 12px; color: #9ca3af; margin-top: 8px;">
                These are injected at 0.90 confidence. Edit my_interests.txt to update.
            </p>
        </div>"""

    # Bucket list
    bucket_path = Path("bucket_list.txt")
    bucket_items = []
    if bucket_path.exists():
        bucket_items = [l.strip() for l in bucket_path.read_text().splitlines()
                       if l.strip() and not l.strip().startswith("#")]

    bucket_html = ""
    if bucket_items:
        items = "".join(f"<li style='margin: 4px 0; font-size: 14px;'>{item}</li>"
                       for item in bucket_items)
        bucket_html = f"""
        <div class="card" style="border-left: 4px solid #10b981;">
            <h3 style="margin-bottom: 8px; color: #059669;">Bucket List ({len(bucket_items)} items)</h3>
            <ul style="columns: 2; column-gap: 24px; padding-left: 20px;">{items}</ul>
            <p style="font-size: 12px; color: #9ca3af; margin-top: 8px;">
                Claude picks 3-5 seasonally relevant ones each week for the email. Edit bucket_list.txt to update.
            </p>
        </div>"""

    # Group interests by source type
    yt_interests = []
    sp_interests = []
    manual_interests = []
    for i in sorted(profile.interests, key=lambda x: x.confidence, reverse=True):
        signals = i.source_signals or []
        if "manual" in signals:
            manual_interests.append(i)
        elif any(s.startswith("youtube:") for s in signals):
            yt_interests.append(i)
        elif any(s.startswith("spotify:") for s in signals):
            sp_interests.append(i)
        else:
            yt_interests.append(i)  # default

    def interest_card(interest, color="#3b82f6"):
        width = int(interest.confidence * 100)
        signals = interest.source_signals or []
        signal_tags = " ".join(
            f'<span style="display:inline-block; background:#f3f4f6; color:#374151; '
            f'padding:2px 8px; border-radius:10px; font-size:11px;">{s}</span>'
            for s in signals[:5]
        )
        return f"""
        <div style="background: white; border-radius: 8px; padding: 14px 16px; margin-bottom: 8px;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-left: 4px solid {color};">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <span style="font-weight: 600; font-size: 15px;">{interest.topic}</span>
                <span style="font-weight: 700; font-size: 14px; color: {color};">{interest.confidence:.0%}</span>
            </div>
            <div style="background: #e5e7eb; border-radius: 4px; height: 6px; margin-bottom: 8px;">
                <div style="background: {color}; height: 6px; border-radius: 4px; width: {width}%;"></div>
            </div>
            <div style="display: flex; flex-wrap: wrap; gap: 4px;">{signal_tags}</div>
        </div>"""

    yt_cards = "".join(interest_card(i, "#ef4444") for i in yt_interests)
    sp_cards = "".join(interest_card(i, "#22c55e") for i in sp_interests)
    manual_cards = "".join(interest_card(i, "#8b5cf6") for i in manual_interests)

    yt_section = f"""
    <div style="margin-bottom: 24px;">
        <h3 style="margin-bottom: 12px;">🎬 From YouTube ({len(yt_interests)} interests)</h3>
        {yt_cards}
    </div>""" if yt_interests else ""

    sp_section = f"""
    <div style="margin-bottom: 24px;">
        <h3 style="margin-bottom: 12px;">🎵 From Spotify ({len(sp_interests)} interests)</h3>
        {sp_cards}
    </div>""" if sp_interests else ""

    manual_section = f"""
    <div style="margin-bottom: 24px;">
        <h3 style="margin-bottom: 12px;">✏️ Manual ({len(manual_interests)} interests)</h3>
        {manual_cards}
    </div>""" if manual_interests else ""

    generated = profile.generated_at.strftime("%B %d, %Y at %I:%M %p") if profile.generated_at else "Unknown"

    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__","Interests") + f"""
    <h1>Interest Profile</h1>

    <div class="card" style="border-left: 4px solid #2563eb; margin-bottom: 20px;">
        <p style="font-size: 15px; line-height: 1.6; color: #374151;">{profile.summary}</p>
        <p style="font-size: 12px; color: #9ca3af; margin-top: 8px;">
            Generated {generated} &middot; {len(profile.interests)} interests extracted
        </p>
    </div>

    <h2>Data Sources</h2>
    <div style="display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px;">
        {sources_html or '<div class="card"><p style="color:#9ca3af;">No ingest stats yet.</p></div>'}
    </div>

    {manual_html}

    <h2 style="margin-top: 24px;">Extracted Interests</h2>
    {yt_section}
    {sp_section}
    {manual_section}

    {bucket_html}

    """ + LAYOUT_FOOT)


@app.get("/", response_class=HTMLResponse)
@app.get("/calendar", response_class=HTMLResponse)
@app.get("/calendar/{run_id}", response_class=HTMLResponse)
async def calendar_view(run_id: int | None = None, u: str = ""):
    db = get_db()

    # Resolve user from token
    current_user = db.get_user_by_token(u) if u else None

    # Default to latest run
    if run_id is None:
        runs = db.get_runs()
        if not runs:
            return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__", "Calendar") + """
            <h1>Calendar</h1><div class="card"><p>No runs yet.</p></div>
            """ + LAYOUT_FOOT)
        run_id = runs[0]["id"]

    events = db.get_run_events(run_id)
    kept = [e for e in events if e.get("keep")]

    # Group by date
    from collections import defaultdict
    from datetime import datetime as dt

    day_groups: dict[str, list] = defaultdict(list)
    undated = []

    for e in kept:
        if e.get("start_time"):
            try:
                d = dt.fromisoformat(e["start_time"])
                day_key = d.strftime("%Y-%m-%d")
                day_groups[day_key].append((d, e))
            except (ValueError, TypeError):
                undated.append(e)
        else:
            undated.append(e)

    # Sort days and pick top 5 per day with vibe diversity
    sorted_days = sorted(day_groups.items())

    # Build calendar grid — week view
    from collections import defaultdict as _cal_dd

    # Diverse pick: top 5 per day, max 2 per vibe
    diverse_days = []
    total_shown = 0
    for day_str, day_events in sorted_days:
        day_events.sort(key=lambda x: -(x[1].get("score") or 0))
        picked = []
        vibe_counts: dict[str, int] = _cal_dd(int)
        for event_dt, e in day_events:
            if len(picked) >= 5:
                break
            vibe = e.get("vibe", "mixed")
            if vibe_counts[vibe] >= 2:
                continue
            picked.append((event_dt, e))
            vibe_counts[vibe] += 1
        # Sort picked by time
        picked.sort(key=lambda x: x[0].replace(tzinfo=None) if x[0].tzinfo else x[0])
        diverse_days.append((day_str, picked))
        total_shown += len(picked)

    # Fetch RSVPs for all kept events
    all_event_ids = [e.get("event_id", "") for e in kept if e.get("event_id")]
    rsvps_map = db.get_rsvps_for_events(all_event_ids)
    user_token = u  # pass through to JS

    # Build week-based grid
    cal_html = ""
    current_week_start = None
    week_cells = {}  # day_of_week -> (day_str, events)
    DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def _render_week(week_start_str, cells):
        """Render one week row as a calendar grid."""
        html = '<div class="cal-week">'
        for dow in range(7):
            if dow in cells:
                day_str, events = cells[dow]
                try:
                    d = dt.strptime(day_str, "%Y-%m-%d")
                    day_num = d.strftime("%-d")
                    month_label = d.strftime("%b") if d.day <= 7 or dow == 0 else ""
                except ValueError:
                    day_num = day_str
                    month_label = ""
                is_today = day_str == dt.now().strftime("%Y-%m-%d")
                today_cls = " cal-today" if is_today else ""
                html += f'<div class="cal-cell{today_cls}"><div class="cal-day-num">{month_label} {day_num}</div>'
                for event_dt, e in events:
                    if event_dt.hour == 0 and event_dt.minute == 0:
                        time_str = ""
                    else:
                        try:
                            time_str = event_dt.strftime("%-I:%M %p") + " "
                        except ValueError:
                            time_str = ""
                    score = int(e.get("score") or 0)
                    vibe = e.get("vibe", "mixed")
                    vibe_cls = f"vibe-{vibe}"
                    title = e["title"][:40]
                    url = e.get("url", "#")
                    loc = e.get("location_name", "")[:25]
                    eid = e.get("event_id", "")
                    etitle_js = title.replace("'", "\\'").replace('"', "&quot;")
                    event_type = e.get("event_type", "event")
                    type_tag = ""
                    if event_type == "club":
                        type_tag = '<span class="evt-type club">CLUB</span>'
                    elif event_type == "class":
                        type_tag = '<span class="evt-type cls">CLASS</span>'
                    import re as _re
                    raw_desc = e.get("description") or ""
                    clean_desc = _re.sub(r'<[^>]+>', '', raw_desc).replace("&nbsp;", " ").strip()
                    snippet = clean_desc[:60] + "..." if len(clean_desc) > 60 else clean_desc
                    reason_short = (e.get("match_reason") or "")[:60]
                    desc_line = snippet or reason_short
                    price = e.get("price") or ""
                    price_str = f" &middot; {price}" if price else ""
                    # RSVP badges
                    rsvp_badges = ""
                    evt_rsvps = rsvps_map.get(eid, [])
                    for rv in evt_rsvps:
                        rv_cls = {"going": "rsvp-going", "maybe": "rsvp-maybe", "cant": "rsvp-cant"}.get(rv["status"], "")
                        rv_label = {"going": "going", "maybe": "maybe", "cant": "can't"}.get(rv["status"], rv["status"])
                        rsvp_badges += f'<span class="rsvp-pill {rv_cls}">{rv["user_name"]} {rv_label}</span>'

                    # RSVP buttons (only when user identified)
                    rsvp_btns = ""
                    if current_user:
                        rsvp_btns = f'''<div class="rsvp-btns" data-eid="{eid}">
                            <button class="rsvp-btn going" onclick="setRsvp('{eid}',{run_id},'going',this)">Going</button>
                            <button class="rsvp-btn maybe" onclick="setRsvp('{eid}',{run_id},'maybe',this)">Maybe</button>
                            <button class="rsvp-btn cant" onclick="setRsvp('{eid}',{run_id},'cant',this)">Can't</button>
                        </div>'''

                    html += f'''<div class="cal-evt {vibe_cls}">
                        <a href="{url}" target="_blank" class="evt-title">{title}</a>
                        <span class="evt-score">{score}</span>{type_tag}
                        <div class="evt-desc">{desc_line}</div>
                        <div class="evt-meta">{time_str}{loc}{price_str}</div>
                        {f'<div class="rsvp-badges">{rsvp_badges}</div>' if rsvp_badges else ''}
                        {rsvp_btns}
                        <button class="attend-btn" onclick="markAttend('{eid}',{run_id},'{etitle_js}',this)">I went</button>
                    </div>'''
                html += '</div>'
            else:
                html += '<div class="cal-cell cal-empty"></div>'
        html += '</div>'
        return html

    # Header row
    cal_html += '<div class="cal-header">'
    for name in DOW_NAMES:
        cal_html += f'<div class="cal-header-cell">{name}</div>'
    cal_html += '</div>'

    # Group days into weeks
    weeks: list[dict[int, tuple]] = []
    current_week: dict[int, tuple] = {}
    last_iso_week = None

    for day_str, events in diverse_days:
        try:
            d = dt.strptime(day_str, "%Y-%m-%d")
            iso_week = d.isocalendar()[1]
            dow = d.weekday()  # 0=Mon
        except ValueError:
            continue
        if last_iso_week is not None and iso_week != last_iso_week:
            weeks.append(current_week)
            current_week = {}
        current_week[dow] = (day_str, events)
        last_iso_week = iso_week
    if current_week:
        weeks.append(current_week)

    for week in weeks:
        cal_html += _render_week("", week)

    calendar_css = """
    <style>
      .cal-header { display: grid; grid-template-columns: repeat(7, 1fr); gap: 1px; margin-bottom: 1px; }
      .cal-header-cell { text-align: center; font-weight: 700; font-size: 13px; color: #6b7280; padding: 8px 0; background: #f9fafb; border: 1px solid #e5e7eb; }
      .cal-week { display: grid; grid-template-columns: repeat(7, 1fr); gap: 1px; margin-bottom: 1px; }
      .cal-cell { border: 1px solid #e5e7eb; min-height: 140px; padding: 6px; background: #fff; }
      .cal-cell.cal-empty { background: #f9fafb; }
      .cal-cell.cal-today { background: #eff6ff; border-color: #3b82f6; }
      .cal-day-num { font-size: 13px; font-weight: 700; color: #374151; margin-bottom: 6px; }
      .cal-today .cal-day-num { color: #2563eb; }
      .cal-evt { padding: 4px 6px; margin-bottom: 4px; border-radius: 4px; border-left: 3px solid; font-size: 12px; }
      .cal-evt.vibe-social { border-left-color: #f59e0b; background: #fffbeb; }
      .cal-evt.vibe-intellectual { border-left-color: #8b5cf6; background: #faf5ff; }
      .cal-evt.vibe-mixed { border-left-color: #3b82f6; background: #eff6ff; }
      .evt-title { color: #1e40af; text-decoration: none; font-weight: 600; font-size: 11px; line-height: 1.3; display: block; }
      .evt-title:hover { text-decoration: underline; }
      .evt-score { display: inline-block; font-size: 10px; font-weight: 700; color: #059669; margin-top: 2px; }
      .evt-type { font-size: 9px; font-weight: 600; padding: 0 4px; border-radius: 4px; margin-left: 3px; }
      .evt-type.club { background: #ede9fe; color: #6d28d9; }
      .evt-type.cls { background: #fef3c7; color: #92400e; }
      .evt-desc { font-size: 10px; color: #6b7280; margin-top: 1px; line-height: 1.3; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
      .evt-meta { font-size: 10px; color: #9ca3af; margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .attend-btn { font-size: 10px; padding: 0 6px; border: 1px solid #e5e7eb; border-radius: 8px; background: white; cursor: pointer; color: #9ca3af; margin-top: 2px; }
      .attend-btn:hover { background: #dcfce7; border-color: #86efac; color: #166534; }
      .attend-btn.done { background: #dcfce7; color: #166534; border-color: #86efac; cursor: default; }
      .rsvp-badges { display: flex; flex-wrap: wrap; gap: 2px; margin-top: 2px; }
      .rsvp-pill { font-size: 9px; padding: 1px 5px; border-radius: 8px; font-weight: 600; white-space: nowrap; }
      .rsvp-going { background: #dcfce7; color: #166534; }
      .rsvp-maybe { background: #fef3c7; color: #92400e; }
      .rsvp-cant { background: #fee2e2; color: #991b1b; }
      .rsvp-btns { display: flex; gap: 2px; margin-top: 2px; }
      .rsvp-btn { font-size: 9px; padding: 1px 5px; border: 1px solid #e5e7eb; border-radius: 6px; background: white; cursor: pointer; color: #6b7280; }
      .rsvp-btn:hover, .rsvp-btn.active { font-weight: 700; }
      .rsvp-btn.going:hover, .rsvp-btn.going.active { background: #dcfce7; color: #166534; border-color: #86efac; }
      .rsvp-btn.maybe:hover, .rsvp-btn.maybe.active { background: #fef3c7; color: #92400e; border-color: #fde68a; }
      .rsvp-btn.cant:hover, .rsvp-btn.cant.active { background: #fee2e2; color: #991b1b; border-color: #fca5a5; }
      .cal-legend { display: flex; gap: 16px; margin-bottom: 12px; font-size: 13px; color: #6b7280; }
      .cal-legend-item { display: flex; align-items: center; gap: 4px; }
      .cal-legend-dot { width: 10px; height: 10px; border-radius: 2px; }
    </style>
    <script>
    const USER_TOKEN = '__USER_TOKEN__';
    function markAttend(eventId, runId, title, btn) {
      if (btn.classList.contains('done')) return;
      fetch('/api/attend', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({event_id: eventId, run_id: runId, title: title})
      }).then(r => r.json()).then(() => {
        btn.textContent = 'Attended!';
        btn.classList.add('done');
      });
    }
    function setRsvp(eventId, runId, status, btn) {
      fetch('/api/rsvp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({event_id: eventId, run_id: runId, status: status, user_token: USER_TOKEN})
      }).then(r => r.json()).then(data => {
        if (data.ok) {
          const btns = btn.parentElement.querySelectorAll('.rsvp-btn');
          btns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
        }
      });
    }
    </script>
    """

    legend = """
    <div class="cal-legend">
        <div class="cal-legend-item"><div class="cal-legend-dot" style="background:#f59e0b"></div> Social</div>
        <div class="cal-legend-item"><div class="cal-legend-dot" style="background:#8b5cf6"></div> Intellectual</div>
        <div class="cal-legend-item"><div class="cal-legend-dot" style="background:#3b82f6"></div> Mixed</div>
    </div>
    """

    user_name = current_user["name"] if current_user else ""
    user_banner = ""
    if current_user:
        user_banner = f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:8px 16px;margin-bottom:12px;font-size:14px;">Viewing as <strong>{user_name}</strong> &middot; RSVP to events below</div>'

    html = LAYOUT_HEAD.replace("__TITLE__", "Calendar") + calendar_css.replace("__USER_TOKEN__", user_token) + f"""
    <h1>Calendar — Run #{run_id}</h1>
    {user_banner}
    {legend}
    <div style="margin-bottom:12px;">
        <span class="stat"><span class="stat-value">{total_shown}</span> <span class="stat-label">top picks</span></span>
        <span class="stat"><span class="stat-value">{len(kept)}</span> <span class="stat-label">total events</span></span>
    </div>
    {cal_html if cal_html else '<p style="color:#9ca3af">No events to display.</p>'}
    """ + LAYOUT_FOOT
    return HTMLResponse(html)


@app.post("/api/attend", response_class=JSONResponse)
async def mark_attended(request: Request):
    """Mark an event as attended."""
    data = await request.json()
    db = get_db()
    db.conn.execute(
        "INSERT INTO attended (event_id, run_id, title, attended_at, rating, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (data["event_id"], data["run_id"], data["title"], datetime.now().isoformat(), data.get("rating"), data.get("notes")),
    )
    db.conn.commit()
    return {"ok": True}


@app.get("/api/attend-link", response_class=HTMLResponse)
async def attend_via_link(event_id: str, title: str = ""):
    """Mark attendance via GET link (for use in emails)."""
    db = get_db()
    # Get latest run
    runs = db.get_runs()
    run_id = runs[0]["id"] if runs else 0
    db.conn.execute(
        "INSERT INTO attended (event_id, run_id, title, attended_at, rating, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (event_id, run_id, title, datetime.now().isoformat(), None, None),
    )
    db.conn.commit()
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 80vh; background: #f5f5f5; }}
    .box {{ background: white; border-radius: 12px; padding: 32px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}</style></head>
    <body><div class="box"><h2 style="color:#059669">Marked as attended!</h2><p style="color:#6b7280">{title[:60]}</p>
    <a href="/" style="color:#2563eb;margin-top:12px;display:inline-block">Back to calendar</a></div></body></html>""")


@app.post("/api/rsvp", response_class=JSONResponse)
async def api_rsvp(request: Request):
    """Set RSVP status for an event."""
    data = await request.json()
    token = data.get("user_token", "")
    db = get_db()
    user = db.get_user_by_token(token)
    if not user:
        return JSONResponse({"error": "Invalid user token"}, status_code=401)
    db.set_rsvp(user["id"], data["event_id"], data["run_id"], data["status"])
    return {"ok": True, "status": data["status"]}


@app.get("/api/rsvp/{event_id}", response_class=JSONResponse)
async def api_get_rsvps(event_id: str):
    """Get all RSVPs for an event."""
    db = get_db()
    rsvps = db.get_event_rsvps(event_id)
    return {"rsvps": rsvps}


@app.get("/api/rsvp-link", response_class=HTMLResponse)
async def rsvp_via_link(event_id: str, status: str, u: str = "", title: str = ""):
    """Handle RSVP via GET link from email."""
    db = get_db()
    user = db.get_user_by_token(u)
    if not user:
        return HTMLResponse("<h1>Invalid link</h1>", status_code=401)
    runs = db.get_runs()
    run_id = runs[0]["id"] if runs else 0
    if status not in ("going", "maybe", "cant"):
        return HTMLResponse("<h1>Invalid status</h1>", status_code=400)
    db.set_rsvp(user["id"], event_id, run_id, status)
    status_labels = {"going": "Going", "maybe": "Maybe", "cant": "Can't go"}
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 80vh; background: #f5f5f5; }}
    .box {{ background: white; border-radius: 12px; padding: 32px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}</style></head>
    <body><div class="box"><h2 style="color:#2563eb">RSVP: {status_labels.get(status, status)}</h2>
    <p style="color:#6b7280">{title[:60]}</p>
    <a href="/?u={u}" style="color:#2563eb;margin-top:12px;display:inline-block">Back to calendar</a></div></body></html>""")


@app.get("/attended", response_class=HTMLResponse)
async def attended_page():
    db = get_db()
    # Check if table exists
    try:
        rows = db.conn.execute(
            "SELECT * FROM attended ORDER BY attended_at DESC"
        ).fetchall()
    except Exception:
        rows = []

    rows_html = ""
    for r in rows:
        r = dict(r)
        rating = f"{'*' * (r.get('rating') or 0)}" if r.get("rating") else ""
        rows_html += f"""<tr>
            <td>{r['title'][:50]}</td>
            <td>{(r.get('attended_at') or '')[:10]}</td>
            <td>{rating}</td>
            <td>{r.get('notes') or ''}</td>
        </tr>"""

    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__", "Attended") + f"""
    <h1>Events You Attended</h1>
    <p style="color:#6b7280;margin-bottom:16px;">Mark events as attended from the calendar view. This data improves future recommendations.</p>
    <table>
        <thead><tr><th>Event</th><th>Date</th><th>Rating</th><th>Notes</th></tr></thead>
        <tbody>{rows_html if rows_html else '<tr><td colspan="4" style="color:#9ca3af">No events marked yet.</td></tr>'}</tbody>
    </table>
    """ + LAYOUT_FOOT)


@app.get("/group/create", response_class=HTMLResponse)
async def group_create_page(u: str = ""):
    db = get_db()
    user = db.get_user_by_token(u) if u else None
    if not user:
        return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__", "Create Group") + """
        <h1>Create Group</h1>
        <div class="card"><p>You need a user token to create a group. Add <code>?u=YOUR_TOKEN</code> to the URL.</p></div>
        """ + LAYOUT_FOOT)

    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__", "Create Group") + f"""
    <h1>Create a Group</h1>
    <div class="card">
        <form action="/group/create?u={u}" method="post" style="display:flex;flex-direction:column;gap:12px;max-width:400px;">
            <div>
                <label style="font-size:13px;color:#6b7280;">Group Name</label><br>
                <input name="name" placeholder="Weekend Crew" required
                       style="padding:8px 12px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;width:100%;">
            </div>
            <div>
                <label style="font-size:13px;color:#6b7280;">URL Slug (lowercase, no spaces)</label><br>
                <input name="slug" placeholder="weekend-crew" required pattern="[a-z0-9\\-]+"
                       style="padding:8px 12px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;width:100%;">
            </div>
            <button type="submit" style="padding:8px 20px;background:#2563eb;color:white;border:none;border-radius:6px;font-size:14px;cursor:pointer;font-weight:600;width:fit-content;">
                Create Group
            </button>
        </form>
    </div>
    """ + LAYOUT_FOOT)


@app.post("/group/create")
async def group_create_submit(request: Request, u: str = ""):
    db = get_db()
    user = db.get_user_by_token(u) if u else None
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    form = await request.form()
    name = form.get("name", "").strip()
    slug = form.get("slug", "").strip().lower()
    if not name or not slug:
        return HTMLResponse("<h1>Name and slug required</h1>", status_code=400)
    group_id = db.create_group(name, slug, user["id"])
    db.add_group_member(group_id, user["id"])
    return RedirectResponse(f"/group/{slug}?u={u}", status_code=303)


@app.post("/group/{slug}/invite")
async def group_invite(slug: str, request: Request, u: str = ""):
    db = get_db()
    user = db.get_user_by_token(u) if u else None
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    group = db.get_group(slug)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)
    form = await request.form()
    email = form.get("email", "").strip()
    if not email:
        return HTMLResponse("<h1>Email required</h1>", status_code=400)
    invited = db.get_user_by_email(email)
    if not invited:
        return HTMLResponse(f"<h1>No user with email {email}</h1>", status_code=404)
    db.add_group_member(group["id"], invited["id"])
    return RedirectResponse(f"/group/{slug}?u={u}", status_code=303)


@app.get("/group/{slug}", response_class=HTMLResponse)
async def group_calendar(slug: str, u: str = ""):
    db = get_db()
    current_user = db.get_user_by_token(u) if u else None
    group = db.get_group(slug)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)

    members = db.get_group_members(group["id"])
    events = db.get_group_events(group["id"])

    # Fetch RSVPs for all events
    event_ids = [e.get("event_id", "") for e in events if e.get("event_id")]
    rsvps_map = db.get_rsvps_for_events(event_ids)

    # Group events by day
    from collections import defaultdict
    from datetime import datetime as dt

    day_groups: dict[str, list] = defaultdict(list)
    for e in events:
        if e.get("start_time"):
            try:
                d = dt.fromisoformat(e["start_time"])
                day_key = d.strftime("%Y-%m-%d")
                day_groups[day_key].append((d, e))
            except (ValueError, TypeError):
                pass

    # Build simple list view
    events_html = ""
    for day_str in sorted(day_groups.keys()):
        day_events = day_groups[day_str]
        day_events.sort(key=lambda x: -(x[1].get("score") or 0))
        try:
            d = dt.strptime(day_str, "%Y-%m-%d")
            day_label = d.strftime("%A, %b %-d")
        except ValueError:
            day_label = day_str
        is_today = day_str == dt.now().strftime("%Y-%m-%d")
        today_style = "background:#eff6ff;border-color:#3b82f6;" if is_today else ""
        events_html += f'<h3 style="margin:16px 0 8px;color:#1e40af;font-size:14px;{today_style}">{day_label}</h3>'

        for event_dt, e in day_events[:5]:
            eid = e.get("event_id", "")
            score = int(e.get("score") or 0)
            title = e["title"][:60]
            url = e.get("url", "#")
            loc = e.get("location_name", "")[:30]
            try:
                time_str = event_dt.strftime("%-I:%M %p")
            except ValueError:
                time_str = ""
            price = e.get("price") or ""

            # RSVP badges
            rsvp_pills = ""
            for rv in rsvps_map.get(eid, []):
                rv_cls = {"going": "rsvp-going", "maybe": "rsvp-maybe", "cant": "rsvp-cant"}.get(rv["status"], "")
                rv_label = {"going": "going", "maybe": "maybe", "cant": "can't"}.get(rv["status"], rv["status"])
                rsvp_pills += f'<span class="rsvp-pill {rv_cls}">{rv["user_name"]} {rv_label}</span>'

            # RSVP buttons
            rsvp_btns = ""
            if current_user:
                runs = db.get_runs()
                rid = runs[0]["id"] if runs else 0
                rsvp_btns = f'''<div class="rsvp-btns" data-eid="{eid}" style="margin-top:4px;">
                    <button class="rsvp-btn going" onclick="setRsvp('{eid}',{rid},'going',this)">Going</button>
                    <button class="rsvp-btn maybe" onclick="setRsvp('{eid}',{rid},'maybe',this)">Maybe</button>
                    <button class="rsvp-btn cant" onclick="setRsvp('{eid}',{rid},'cant',this)">Can't</button>
                </div>'''

            events_html += f'''<div class="card" style="padding:10px 14px;margin-bottom:6px;">
                <div style="display:flex;justify-content:space-between;align-items:start;">
                    <div>
                        <a href="{url}" target="_blank" style="font-weight:600;font-size:14px;">{title}</a>
                        {score_badge(score)}
                        <div style="font-size:12px;color:#6b7280;">{time_str} &middot; {loc}{f" &middot; {price}" if price else ""}</div>
                    </div>
                </div>
                {f'<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:4px;">{rsvp_pills}</div>' if rsvp_pills else ''}
                {rsvp_btns}
            </div>'''

    members_html = ", ".join(m["name"] or m["email"] for m in members)
    invite_form = ""
    if current_user:
        invite_form = f'''<div class="card" style="margin-bottom:16px;">
            <form action="/group/{slug}/invite?u={u}" method="post" style="display:flex;gap:8px;align-items:end;">
                <div>
                    <label style="font-size:12px;color:#6b7280;">Invite by email</label><br>
                    <input name="email" type="email" placeholder="friend@gmail.com" required
                           style="padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:13px;">
                </div>
                <button type="submit" style="padding:6px 14px;background:#2563eb;color:white;border:none;border-radius:6px;font-size:13px;cursor:pointer;">Invite</button>
            </form>
        </div>'''

    rsvp_css = """<style>
      .rsvp-pill { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }
      .rsvp-going { background: #dcfce7; color: #166534; }
      .rsvp-maybe { background: #fef3c7; color: #92400e; }
      .rsvp-cant { background: #fee2e2; color: #991b1b; }
      .rsvp-btns { display: flex; gap: 4px; }
      .rsvp-btn { font-size: 11px; padding: 2px 8px; border: 1px solid #e5e7eb; border-radius: 8px; background: white; cursor: pointer; color: #6b7280; }
      .rsvp-btn.going:hover, .rsvp-btn.going.active { background: #dcfce7; color: #166534; border-color: #86efac; }
      .rsvp-btn.maybe:hover, .rsvp-btn.maybe.active { background: #fef3c7; color: #92400e; border-color: #fde68a; }
      .rsvp-btn.cant:hover, .rsvp-btn.cant.active { background: #fee2e2; color: #991b1b; border-color: #fca5a5; }
    </style>
    <script>
    const USER_TOKEN = '""" + (u or "") + """';
    function setRsvp(eventId, runId, status, btn) {
      fetch('/api/rsvp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({event_id: eventId, run_id: runId, status: status, user_token: USER_TOKEN})
      }).then(r => r.json()).then(data => {
        if (data.ok) {
          const btns = btn.parentElement.querySelectorAll('.rsvp-btn');
          btns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
        }
      });
    }
    </script>"""

    user_banner = ""
    if current_user:
        user_banner = f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:8px 16px;margin-bottom:12px;font-size:14px;">Viewing as <strong>{current_user["name"]}</strong></div>'

    ical_link = f"/group/{slug}/feed.ics"

    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__", group["name"]) + rsvp_css + f"""
    <h1>{group["name"]}</h1>
    {user_banner}
    <p style="color:#6b7280;margin-bottom:12px;">Members: {members_html}
        &middot; <a href="{ical_link}">Subscribe to iCal</a>
        &middot; <a href="/group/create?u={u}">Create new group</a>
    </p>
    {invite_form}
    {events_html if events_html else '<div class="card"><p style="color:#9ca3af;">No events yet. Run the pipeline for group members first.</p></div>'}
    """ + LAYOUT_FOOT)


@app.get("/group/{slug}/feed.ics")
async def group_ical_feed(slug: str, min_score: int = 40):
    """Group iCal feed with RSVP info in descriptions."""
    db = get_db()
    group = db.get_group(slug)
    if not group:
        return Response(content="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR",
                       media_type="text/calendar")

    events = db.get_group_events(group["id"])
    kept = [e for e in events if (e.get("score") or 0) >= min_score]
    event_ids = [e.get("event_id", "") for e in kept if e.get("event_id")]
    rsvps_map = db.get_rsvps_for_events(event_ids)

    import html as _html

    def _ical_escape(text: str) -> str:
        text = _html.unescape(text)
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//recom//Group {_ical_escape(group['name'])}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Recom - {_ical_escape(group['name'])}",
    ]

    for e in kept[:50]:
        start = e.get("start_time")
        if not start:
            continue
        try:
            dt_obj = datetime.fromisoformat(start)
        except (ValueError, TypeError):
            continue

        dtstart = dt_obj.strftime("%Y%m%dT%H%M%S")
        title = _ical_escape(e.get("title") or "")
        location = _ical_escape(e.get("location_name") or "")
        url = e.get("url") or ""
        score = int(e.get("score") or 0)
        reason = _ical_escape(e.get("match_reason") or "")
        eid = e.get("event_id", "")

        # Add RSVP info to description
        rsvp_lines = []
        for rv in rsvps_map.get(eid, []):
            rv_label = {"going": "Going", "maybe": "Maybe", "cant": "Can't go"}.get(rv["status"], rv["status"])
            rsvp_lines.append(f"{rv['user_name']}: {rv_label}")
        rsvp_text = "\\n".join(rsvp_lines) if rsvp_lines else ""
        desc_parts = [f"Score: {score}/100", reason]
        if rsvp_text:
            desc_parts.append(f"\\nRSVPs:\\n{rsvp_text}")

        uid = f"{eid}@recom-group-{slug}"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART:{dtstart}",
            f"SUMMARY:[{score}] {title}",
            f"LOCATION:{location}",
            f"URL:{url}",
            f"DESCRIPTION:{_ical_escape('\\n'.join(desc_parts))}",
            "DURATION:PT2H",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar",
        headers={"Content-Disposition": f"inline; filename=recom-{slug}.ics"},
    )


@app.get("/feed.ics")
async def ical_feed(min_score: int = 55):
    """iCal feed of top recommended events. Subscribe in Google/Apple Calendar.
    Default: score >= 55 (strong matches). Use ?min_score=25 for everything kept."""
    db = get_db()
    runs = db.get_runs()
    if not runs:
        return Response(content="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR",
                       media_type="text/calendar")

    run_id = runs[0]["id"]
    events = db.get_run_events(run_id)
    kept = [e for e in events if e.get("keep") and (e.get("score") or 0) >= min_score]

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//recom//Event Recommender//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Recom Events",
        "X-WR-CALDESC:Personalized event recommendations for Boston/Cambridge",
    ]

    def _ical_escape(text: str) -> str:
        """Escape text for iCal property values per RFC 5545."""
        import html as _html
        text = _html.unescape(text)  # convert &quot; &#8212; etc to real chars
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def _fold_line(line: str) -> str:
        """Fold long lines per RFC 5545 (max 75 octets)."""
        if len(line.encode("utf-8")) <= 75:
            return line
        result = []
        encoded = line.encode("utf-8")
        first = True
        while encoded:
            limit = 75 if first else 74  # continuation lines have leading space
            chunk = encoded[:limit]
            # Don't split multi-byte chars
            while limit > 0:
                try:
                    chunk.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    limit -= 1
                    chunk = encoded[:limit]
            if first:
                result.append(chunk.decode("utf-8"))
                first = False
            else:
                result.append(" " + chunk.decode("utf-8"))
            encoded = encoded[limit:]
        return "\r\n".join(result)

    # Sort by score descending, then pick top 5 per day with diversity
    kept.sort(key=lambda x: -(x.get("score") or 0))
    from collections import defaultdict as _dd
    day_counts: dict[str, int] = _dd(int)
    day_source_counts: dict[str, dict[str, int]] = _dd(lambda: _dd(int))
    max_per_day = 5
    max_per_source_per_day = 2

    for e in kept:
        start = e.get("start_time")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start)
            day_key = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        if day_counts[day_key] >= max_per_day:
            continue

        # Limit per vibe per day for diversity (max 2 social, 2 intellectual, etc.)
        vibe = e.get("vibe", "mixed")
        if day_source_counts[day_key][vibe] >= max_per_source_per_day:
            continue
        day_counts[day_key] += 1
        day_source_counts[day_key][vibe] += 1

        dtstart = dt.strftime("%Y%m%dT%H%M%S")
        title = _ical_escape(e.get("title") or "")
        location = _ical_escape(e.get("location_name") or "")
        url = e.get("url") or ""
        score = int(e.get("score") or 0)
        reason = _ical_escape(e.get("match_reason") or "")
        price = _ical_escape(e.get("price") or "Free")
        uid = f"{e.get('event_id', '')}@recom"

        lines.extend([
            "BEGIN:VEVENT",
            _fold_line(f"UID:{uid}"),
            f"DTSTART:{dtstart}",
            _fold_line(f"SUMMARY:[{score}] {title}"),
            _fold_line(f"LOCATION:{location}"),
            _fold_line(f"URL:{url}"),
            _fold_line(f"DESCRIPTION:{price}\\nScore: {score}/100\\n{reason}"),
            "DURATION:PT2H",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    ical_text = "\r\n".join(lines)

    return Response(
        content=ical_text,
        media_type="text/calendar",
        headers={"Content-Disposition": "inline; filename=recom.ics"},
    )


# ---------------------------------------------------------------------------
# Onboarding — /join page for new users
# ---------------------------------------------------------------------------

@app.get("/join", response_class=HTMLResponse)
async def join_page(success: str = ""):
    db = get_db()
    users = db.get_users()
    users_html = ""
    for u in users:
        users_html += f"""<tr>
            <td>{u['name'] or '—'}</td>
            <td>{u['email']}</td>
            <td>{'✅' if u.get('spotify_token_file') else '❌'}</td>
            <td>{'✅' if u.get('youtube_token_file') else '❌'}</td>
            <td>{u['created_at'][:10]}</td>
        </tr>"""

    success_banner = ""
    if success:
        success_banner = f"""
        <div style="background: #dcfce7; border: 2px solid #22c55e; border-radius: 8px;
                    padding: 16px; margin-bottom: 16px; text-align: center; color: #166534;">
            ✅ {success}
        </div>"""

    settings = Settings()

    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__", "Join") + f"""
    <h1>Join Recom</h1>
    <p style="color: #6b7280; margin-bottom: 20px;">
        Get personalized weekly event recommendations for Boston/Cambridge.
        Connect your Spotify and YouTube to let us learn your interests.
    </p>

    {success_banner}

    <div class="card" style="margin-bottom: 20px;">
        <h2 style="margin-bottom: 12px;">Step 1: Create Account</h2>
        <form action="/api/join" method="post" style="display: flex; gap: 12px; align-items: end; flex-wrap: wrap;">
            <div>
                <label style="font-size: 13px; color: #6b7280;">Name</label><br>
                <input name="name" placeholder="Your name" required
                       style="padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px;">
            </div>
            <div>
                <label style="font-size: 13px; color: #6b7280;">Email</label><br>
                <input name="email" type="email" placeholder="you@gmail.com" required
                       style="padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px;">
            </div>
            <div>
                <label style="font-size: 13px; color: #6b7280;">Location</label><br>
                <input name="location" placeholder="Cambridge, MA" value="Cambridge, MA"
                       style="padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px;">
            </div>
            <button type="submit" style="padding: 8px 20px; background: #2563eb; color: white;
                    border: none; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 600;">
                Create Account
            </button>
        </form>
    </div>

    <div class="card" style="margin-bottom: 20px;">
        <h2 style="margin-bottom: 12px;">Step 2: Connect Spotify</h2>
        <p style="color: #6b7280; font-size: 14px; margin-bottom: 12px;">
            We read your top artists, tracks, and recently played to understand your music taste.
            This helps us find concerts and events you'd love.
        </p>
        <a href="/auth/spotify" style="display: inline-block; padding: 10px 24px; background: #1DB954;
           color: white; border-radius: 24px; font-weight: 600; font-size: 14px; text-decoration: none;">
            Connect Spotify
        </a>
    </div>

    <div class="card" style="margin-bottom: 20px;">
        <h2 style="margin-bottom: 12px;">Step 3: Subscribe to Calendar</h2>
        <p style="color: #6b7280; font-size: 14px; margin-bottom: 12px;">
            Add this URL to Google Calendar or Apple Calendar to see recommended events:
        </p>
        <code style="background: #f3f4f6; padding: 8px 16px; border-radius: 6px; font-size: 13px; display: block; word-break: break-all;">
            https://recom.arthgupta.dev/feed.ics
        </code>
        <p style="color: #9ca3af; font-size: 12px; margin-top: 8px;">
            Google Calendar → Other calendars → From URL → paste the link above.
            Add ?min_score=25 for all events, or ?min_score=70 for only top picks.
        </p>
    </div>

    <h2>Current Users</h2>
    <table>
        <thead><tr><th>Name</th><th>Email</th><th>Spotify</th><th>YouTube</th><th>Joined</th></tr></thead>
        <tbody>{users_html if users_html else '<tr><td colspan="5" style="color:#9ca3af">No users yet.</td></tr>'}</tbody>
    </table>
    """ + LAYOUT_FOOT)


@app.post("/api/join")
async def api_join(request: Request):
    form = await request.form()
    email = form.get("email", "").strip()
    name = form.get("name", "").strip()
    location = form.get("location", "Cambridge, MA").strip()

    if not email:
        return HTMLResponse("<h1>Email required</h1>", status_code=400)

    db = get_db()
    user_id = db.create_user(email, name)
    if location:
        db.update_user(user_id, location_query=location)

    return RedirectResponse(f"/join?success=Welcome+{name}!+Account+created.", status_code=303)


@app.get("/auth/spotify")
async def spotify_auth_start():
    """Redirect user to Spotify OAuth."""
    settings = Settings()
    if not settings.spotify_client_id:
        return HTMLResponse("<h1>Spotify not configured</h1><p>Set RECOM_SPOTIFY_CLIENT_ID in .env</p>")

    import urllib.parse
    scopes = "user-read-recently-played user-top-read user-library-read"
    params = urllib.parse.urlencode({
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": scopes,
        "show_dialog": "true",
    })
    return RedirectResponse(f"https://accounts.spotify.com/authorize?{params}")


@app.get("/callback")
async def spotify_callback(code: str = "", error: str = ""):
    """Handle Spotify OAuth callback."""
    if error or not code:
        return HTMLResponse(f"<h1>Spotify auth failed</h1><p>{error}</p>")

    settings = Settings()
    import httpx
    # Exchange code for token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.spotify_redirect_uri,
                "client_id": settings.spotify_client_id,
                "client_secret": settings.spotify_client_secret,
            },
        )
        if resp.status_code != 200:
            return HTMLResponse(f"<h1>Token exchange failed</h1><pre>{resp.text}</pre>")
        token_data = resp.json()

    # Save token — for now save to default path
    # TODO: per-user token files
    import json as _json
    from pathlib import Path
    token_path = Path(settings.spotify_token_file)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(_json.dumps(token_data))

    return RedirectResponse("/join?success=Spotify+connected+successfully!", status_code=303)


def run():
    import uvicorn
    settings = Settings()
    logger.info(f"Starting dashboard at http://{settings.dashboard_host}:{settings.dashboard_port}")
    uvicorn.run(app, host=settings.dashboard_host, port=settings.dashboard_port)
