from __future__ import annotations

import json
import logging

from datetime import datetime

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.responses import RedirectResponse

from recom.config import Settings
from recom.db import Database
from recom.email.sender import send_magic_link, send_invite_email, send_rsvp_notify, send_group_ping, send_group_event_notification
from recom.gcal import get_or_create_calendar, push_event as gcal_push_event, update_attendees as gcal_update_attendees, sync_rsvps_to_db as gcal_sync_rsvps

logger = logging.getLogger(__name__)

app = FastAPI(title="Calyx Dashboard")

_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        settings = Settings()
        _db = Database(settings.db_path)
    return _db


COOKIE_NAME = "recom_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


def _get_current_user(request: Request) -> dict | None:
    """Resolve user from ?u= query param (e.g. magic link) or cookie."""
    db = get_db()
    token = request.query_params.get("u", "") or request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    return db.get_user_by_token(token)


def _set_token_cookie(response: Response, token: str) -> Response:
    """Set the auth cookie on a response."""
    response.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return response


def _maybe_set_cookie(request: Request, response: Response, user: dict | None) -> Response:
    """If user is logged in via ?u= param, persist to cookie so links don't need ?u=."""
    if user and request.query_params.get("u"):
        _set_token_cookie(response, user["user_token"])
    return response


def render_nav(user: dict | None = None) -> str:
    if user:
        name = user.get("name") or user.get("email", "")
        return f"""<nav class="app-nav"><div class="app-nav-inner">
          <a href="/" class="app-logo">calyx</a>
          <a href="/groups" class="nav-link">Groups</a>
          <a href="/calendar" class="nav-link">Discover</a>
          <a href="/taste-profile" class="nav-link">You</a>
          <div class="nav-divider"></div>
          <span style="font-size:12px;color:#888;font-weight:500;">{name}</span>
        </div></nav>"""
    return """<nav class="app-nav"><div class="app-nav-inner">
      <a href="/" class="app-logo">calyx</a>
      <a href="/groups" class="nav-link">Groups</a>
      <a href="/calendar" class="nav-link">Discover</a>
      <a href="/login" class="nav-link">Log in</a>
    </div></nav>"""


def _layout(title: str, body: str, user: dict | None = None, og: dict | None = None) -> str:
    nav = render_nav(user)
    base_url = "https://calyx.arthgupta.dev"
    og_title = (og or {}).get("title", title)
    og_desc = (og or {}).get("description", "Find events and make plans with friends")
    og_image = (og or {}).get("image", f"{base_url}/static/og-image.png")
    og_url = (og or {}).get("url", "")
    og_tags = f'''<meta property="og:site_name" content="Calyx">
<meta property="og:type" content="website">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_desc}">
<meta property="og:image" content="{og_image}">'''
    if og_url:
        og_tags += f'\n<meta property="og:url" content="{og_url}">'
    og_tags += '\n<meta name="twitter:card" content="summary_large_image">'
    html = LAYOUT_STYLE.replace("__TITLE__", title).replace("__OG_TAGS__", og_tags)
    return html + nav + '<div class="app-content">' + body + LAYOUT_FOOT


LAYOUT_STYLE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#4a6741">
<meta name="apple-mobile-web-app-capable" content="yes">
__OG_TAGS__
<title>Calyx — __TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #fff; color: #111; font-size: 14px; line-height: 1.55; min-height: 100vh; }
  /* --- App shell — MoMA-inspired: white, clean, typographic --- */
  .app-nav { background: #fff; padding: 0 20px; position: sticky; top: 0; z-index: 100; border-bottom: 1px solid #000; }
  .app-nav-inner { display: flex; align-items: center; max-width: 960px; margin: 0 auto; height: 56px; gap: 4px; }
  .app-logo { font-size: 20px; font-weight: 800; color: #4a6741; text-decoration: none; letter-spacing: -.8px; margin-right: auto; text-transform: lowercase; }
  .app-logo:hover { text-decoration: none; opacity: .8; }
  .app-nav a.nav-link { font-size: 13px; font-weight: 500; color: #888; text-decoration: none; padding: 8px 14px; letter-spacing: .3px; text-transform: uppercase; transition: color .15s; }
  .app-nav a.nav-link:hover { color: #4a6741; text-decoration: none; }
  .app-nav a.nav-link.active { color: #4a6741; font-weight: 700; }
  .nav-divider { width: 1px; height: 20px; background: #ddd; margin: 0 8px; }
  .app-content { max-width: 960px; margin: 0 auto; padding: 32px 20px 60px; }
  /* --- Shared components --- */
  h1 { margin-bottom: 24px; color: #000; font-size: 2rem; font-weight: 800; letter-spacing: -.5px; }
  h2 { margin: 28px 0 16px; color: #000; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; }
  a { color: #4a6741; text-decoration: underline; text-underline-offset: 2px; }
  a:hover { text-decoration-thickness: 2px; color: #3a5334; }
  .card { background: #fff; border: 1px solid #e0e0e0; padding: 24px; margin-bottom: 24px; }
  .badge { display: inline-block; padding: 2px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px; }
  .badge-green { background: #000; color: #fff; }
  .badge-yellow { background: #f5f5f5; color: #555; }
  .badge-gray { background: #f5f5f5; color: #555; }
  .badge-red { background: #d00; color: #fff; }
  table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e0e0e0; }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e0e0e0; }
  th { background: #fafafa; font-weight: 700; font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: #888; cursor: pointer; }
  th:hover { color: #000; }
  tr:hover { background: #fafafa; }
  .stat { display: inline-block; margin-right: 24px; }
  .stat-value { font-size: 28px; font-weight: 800; color: #000; letter-spacing: -1px; }
  .stat-label { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 1.5px; }
  .score-bar { height: 4px; background: #eee; }
  .score-fill { height: 100%; }
  .filter-row { margin-bottom: 16px; }
  .filter-row input, .filter-row select { padding: 10px 14px; border: 1px solid #ccc; font-size: 14px; font-family: inherit; transition: border-color .15s; }
  .filter-row input:focus, .filter-row select:focus { outline: none; border-color: #000; }
  .interests-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
  .interest-tag { padding: 4px 12px; background: #f5f5f5; color: #333; font-size: 12px; font-weight: 500; }
  .cost-box { background: #fafafa; border: 1px solid #e0e0e0; padding: 12px; margin: 10px 0; }
  .btn-primary { background: #4a6741; color: #fff; border: none; padding: 10px 24px; font-weight: 700; font-size: 13px; cursor: pointer; font-family: inherit; text-transform: uppercase; letter-spacing: .5px; transition: background .15s; }
  .btn-primary:hover { background: #3a5334; }
  .btn-secondary { background: #fff; color: #4a6741; border: 1px solid #4a6741; padding: 10px 24px; font-weight: 600; font-size: 13px; cursor: pointer; font-family: inherit; text-transform: uppercase; letter-spacing: .5px; }
  .btn-secondary:hover { background: #f4f7f3; }
  .btn-pill { padding: 6px 16px; font-size: 12px; }
  @media (max-width: 640px) {
    .app-nav a.nav-link { font-size: 11px; padding: 8px 8px; }
    .app-content { padding: 20px 16px 40px; }
    h1 { font-size: 1.6rem; }
  }
</style>
</head>
<body>
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
// Highlight active nav link
const path = window.location.pathname;
document.querySelectorAll('.nav-link').forEach(a => {
  const href = a.getAttribute('href');
  if ((href === '/' && (path === '/' || path.startsWith('/calendar'))) ||
      (href !== '/' && path.startsWith(href)))
    a.classList.add('active');
});
</script>
<div id="toast" style="position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(100px);opacity:0;padding:12px 24px;font-size:13px;font-weight:600;font-family:inherit;color:#fff;background:#000;z-index:9999;pointer-events:none;transition:transform .3s ease,opacity .3s ease;"></div>
<script>
function showToast(msg, type) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.style.background = '#000';
  t.style.opacity = '1';
  t.style.transform = 'translateX(-50%) translateY(0)';
  clearTimeout(t._tid);
  t._tid = setTimeout(function() {
    t.style.opacity = '0';
    t.style.transform = 'translateX(-50%) translateY(100px)';
  }, 3000);
}
(function() {
  const p = new URLSearchParams(window.location.search);
  const s = p.get('success');
  const i = p.get('info');
  if (s) showToast(s, 'success');
  else if (i) showToast(i, 'info');
})();
</script>
</div><!-- .app-content -->
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


def _build_ranked_events_from_run(run_id: int):
    """Helper: reconstruct RankedEvent list from a DB run_id."""
    import re as _re
    from datetime import datetime
    from recom.models import RankedEvent, Event, EventSource

    db = get_db()
    raw_events = db.get_run_events(run_id)
    ranked = []
    for row in raw_events:
        if not row.get("keep"):
            continue
        try:
            src = row.get("source", "eventbrite")
            try:
                source_enum = EventSource(src)
            except ValueError:
                source_enum = EventSource.EVENTBRITE
            raw_desc = row.get("description") or ""
            clean_desc = _re.sub(r'<[^>]+>', '', raw_desc).replace("&nbsp;", " ").strip()
            start_raw = row.get("start_time")
            start_time = datetime.fromisoformat(start_raw) if start_raw else None
            ev = Event(
                id=row.get("event_id", ""),
                source=source_enum,
                title=row.get("title", ""),
                description=clean_desc[:500],
                url=row.get("url") or "",
                start_time=start_time,
                location_name=row.get("location_name") or "",
                price=row.get("price"),
                image_url=row.get("image_url"),
            )
            ranked.append(RankedEvent(
                event=ev,
                score=float(row.get("score") or 0),
                vibe=row.get("vibe", "mixed"),
                match_reason=row.get("match_reason") or "",
                keep=True,
                event_type=row.get("event_type", "event"),
            ))
        except Exception:
            pass
    return ranked


@app.get("/admin", response_class=HTMLResponse)
async def run_history(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
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
    # Load schedule settings
    db_obj = get_db()
    sched_pipeline_day = db_obj.get_setting("schedule_pipeline_day", "Saturday")
    sched_pipeline_hour = db_obj.get_setting("schedule_pipeline_hour", "9")
    sched_daily_hour = db_obj.get_setting("schedule_daily_hour", "8")
    day_opts = "".join(
        f'<option value="{d}"{" selected" if d == sched_pipeline_day else ""}>{d}</option>'
        for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    )
    pipeline_hour_opts = "".join(
        f'<option value="{h}"{" selected" if str(h) == sched_pipeline_hour else ""}>{h}:00</option>'
        for h in range(6, 13)
    )
    daily_hour_opts = "".join(
        f'<option value="{h}"{" selected" if str(h) == sched_daily_hour else ""}>{h}:00</option>'
        for h in range(6, 13)
    )
    body = f"""
    <h1>⚙️ Admin</h1>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
        <a href="/admin/sources" style="font-size:12px;padding:6px 12px;background:#f3f4f6;border-radius:8px;color:#374151;text-decoration:none;font-weight:600;">Sources</a>
    </div>
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

    <details style="margin-top:24px;">
      <summary style="cursor:pointer;font-size:14px;font-weight:600;color:#374151;">Schedule Settings</summary>
      <div class="card" style="max-width:480px;margin-top:8px;">
        <form id="sched-form" style="display:flex;flex-direction:column;gap:14px;">
            <div>
                <label style="font-size:13px;color:#6b7280;display:block;margin-bottom:4px;">Weekly pipeline day</label>
                <select name="pipeline_day" style="padding:7px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;width:100%;">{day_opts}</select>
            </div>
            <div>
                <label style="font-size:13px;color:#6b7280;display:block;margin-bottom:4px;">Weekly pipeline hour</label>
                <select name="pipeline_hour" style="padding:7px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;width:100%;">{pipeline_hour_opts}</select>
            </div>
            <div>
                <label style="font-size:13px;color:#6b7280;display:block;margin-bottom:4px;">Daily digest email hour</label>
                <select name="daily_hour" style="padding:7px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;width:100%;">{daily_hour_opts}</select>
            </div>
            <div style="display:flex;align-items:center;gap:12px;">
                <button type="button" onclick="saveSchedule()"
                        style="padding:8px 20px;background:#4f46e5;color:white;border:none;border-radius:10px;font-size:14px;cursor:pointer;font-weight:600;">Save &amp; Reinstall Cron</button>
                <span id="sched-status" style="font-size:13px;color:#6b7280;"></span>
            </div>
        </form>
      </div>
    </details>
    <script>
    async function saveSchedule() {{
        const form = document.getElementById('sched-form');
        const data = {{
            pipeline_day: form.pipeline_day.value,
            pipeline_hour: form.pipeline_hour.value,
            daily_hour: form.daily_hour.value,
        }};
        const st = document.getElementById('sched-status');
        st.textContent = 'Saving...';
        try {{
            const r = await fetch('/api/admin/schedule', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(data)
            }});
            const res = await r.json();
            st.textContent = res.ok ? '✓ Saved' + (res.cron_updated ? ' + cron updated' : '') : '✗ ' + (res.error || 'Failed');
            st.style.color = res.ok ? '#16a34a' : '#dc2626';
        }} catch(e) {{ st.textContent = '✗ ' + e; st.style.color = '#dc2626'; }}
    }}
    </script>
    """
    return HTMLResponse(_layout("Admin", body, current_user))


@app.post("/api/admin/schedule")
async def api_admin_schedule(request: Request):
    """Save schedule settings and optionally regenerate crontab."""
    import subprocess as _sub
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    db = get_db()
    day = body.get("pipeline_day", "Saturday")
    pipeline_hour = str(body.get("pipeline_hour", "9"))
    daily_hour = str(body.get("daily_hour", "8"))
    db.set_setting("schedule_pipeline_day", day)
    db.set_setting("schedule_pipeline_hour", pipeline_hour)
    db.set_setting("schedule_daily_hour", daily_hour)
    # Try to reinstall cron (best-effort)
    cron_updated = False
    install_script = Path(__file__).parent.parent.parent.parent / "scripts" / "install_cron.sh"
    if install_script.exists():
        try:
            env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
            day_abbr = day[:3].lower()
            # Convert day name to cron day-of-week number (0=Sun)
            _dow = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
            dow = _dow.get(day_abbr, 6)
            res = _sub.run(
                ["bash", str(install_script), pipeline_hour, daily_hour, str(dow)],
                capture_output=True, text=True, timeout=10, env=env
            )
            cron_updated = res.returncode == 0
        except Exception:
            pass
    return JSONResponse({"ok": True, "cron_updated": cron_updated})


@app.get("/admin/sources", response_class=HTMLResponse)
async def source_health(request: Request):
    """Scraper health dashboard — per-source and per-run views."""
    db = get_db()
    current_user = _get_current_user(request)
    sources = db.get_source_health(last_n_runs=10)
    by_run = db.get_source_stats_by_run(last_n_runs=10)
    cache_status = {r["source_name"]: r for r in db.get_source_cache_status()}

    # --- Aggregate summary table ---
    rows_html = ""
    for s in sources:
        if s["source_name"] == "_dedup":
            continue
        success_rate = round(s["successes"] / s["run_count"] * 100) if s["run_count"] else 0
        rate_color = "#16a34a" if success_rate >= 90 else "#d97706" if success_rate >= 60 else "#dc2626"
        status_icon = "✅" if success_rate >= 90 else "⚠️" if success_rate >= 60 else "❌"
        history = [int(x) for x in (s["event_history"] or "0").split(",") if x.strip().isdigit()]
        max_h = max(history) if history else 1
        if max_h == 0:
            max_h = 1
        bars = "".join(
            f'<span style="display:inline-block;width:6px;height:{max(2, round(v/max_h*24))}px;background:{"#3b82f6" if v > 0 else "#fca5a5"};border-radius:1px;margin-right:1px;vertical-align:bottom;" title="{v}"></span>'
            for v in reversed(history[:10])
        )
        err_html = f'<span title="{(s["last_error"] or "")[:200]}" style="color:#dc2626;font-size:11px;cursor:help;">⚠ {(s["last_error"] or "")[:40]}...</span>' if s["last_error"] else ''
        avg_dur = s.get("avg_duration_s")
        dur_str = f"{avg_dur:.1f}s" if avg_dur else "—"
        cache = cache_status.get(s["source_name"])
        if cache:
            age_h = round(cache.get("age_hours") or 0, 1)
            interval_h = cache.get("refresh_interval_hours") or 24
            fresh = age_h < interval_h
            cache_str = f'<span style="color:{"#16a34a" if fresh else "#d97706"}">{age_h}h ago</span>'
        else:
            cache_str = '<span style="color:#9ca3af">—</span>'
        rows_html += f"""<tr>
            <td><strong>{s['source_name']}</strong></td>
            <td style="color:{rate_color};font-weight:700;">{status_icon} {success_rate}%</td>
            <td>{round(s['avg_events'] or 0)}</td>
            <td>{s['max_events']}</td>
            <td style="color:#9ca3af;font-size:12px;">{dur_str}</td>
            <td>{cache_str}</td>
            <td><div style="display:flex;align-items:flex-end;height:28px;gap:1px;">{bars}</div></td>
            <td>{err_html}</td>
        </tr>"""

    # --- Per-run breakdown ---
    from collections import OrderedDict
    runs_map: OrderedDict[int, dict] = OrderedDict()
    for row in by_run:
        if row["source_name"] == "_dedup":
            continue
        rid = row["run_id"]
        if rid not in runs_map:
            runs_map[rid] = {"timestamp": row["timestamp"], "sources": []}
        runs_map[rid]["sources"].append(row)

    run_sections = ""
    for rid, rdata in runs_map.items():
        ts = rdata["timestamp"][:16].replace("T", " ")
        total_events = sum(s["events_found"] for s in rdata["sources"])
        ok_count = sum(1 for s in rdata["sources"] if not s["error_message"] and s["events_found"] > 0)
        fail_count = len(rdata["sources"]) - ok_count
        src_rows = ""
        for s in rdata["sources"]:
            ev = s["events_found"]
            err = s["error_message"]
            dur = s["duration_seconds"]
            dur_str = f"{dur:.1f}s" if dur else "—"
            if err:
                icon = "❌"
                color = "#dc2626"
            elif ev == 0:
                icon = "⚠️"
                color = "#d97706"
            else:
                icon = "✅"
                color = "#16a34a"
            err_tip = f' title="{(err or "")[:200]}"' if err else ""
            src_rows += f'<tr><td>{icon} {s["source_name"]}</td><td style="color:{color};font-weight:600;">{ev}</td><td style="color:#9ca3af;font-size:12px;">{dur_str}</td><td style="font-size:11px;color:#dc2626;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"{err_tip}>{(err or "")[:60]}</td></tr>'
        run_sections += f"""
        <details style="margin-bottom:8px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
          <summary style="padding:10px 14px;background:#f9fafb;cursor:pointer;font-size:13px;font-weight:600;display:flex;gap:12px;align-items:center;">
            <span>Run #{rid}</span>
            <span style="color:#9ca3af;font-weight:400;">{ts}</span>
            <span style="color:#16a34a;font-weight:700;">{total_events} events</span>
            <span style="font-size:11px;color:#6b7280;">{ok_count}✅ {fail_count}❌</span>
          </summary>
          <table style="margin:0;border-radius:0;"><thead><tr><th>Source</th><th>Events</th><th>Time</th><th>Error</th></tr></thead><tbody>{src_rows}</tbody></table>
        </details>"""

    body = f"""
    <h1>📡 Source Health</h1>
    <p style="color:#6b7280;margin-bottom:16px;font-size:14px;">
        Success = returned &gt;0 events with no error. Sources returning 0 events count as failures.
    </p>
    <h2 style="font-size:16px;margin-bottom:8px;">Aggregate (last 10 runs)</h2>
    <table>
        <thead><tr>
            <th data-sort="source">Source</th>
            <th data-sort="rate">Success Rate</th>
            <th data-sort="avg">Avg Events</th>
            <th data-sort="max">Max Events</th>
            <th>Avg Time</th>
            <th>Cache Age</th>
            <th>Trend (newest →)</th>
            <th>Last Error</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
    <h2 style="font-size:16px;margin:24px 0 8px;">Per-Run Breakdown</h2>
    <p style="color:#6b7280;margin-bottom:12px;font-size:13px;">Expand each run to see individual source results.</p>
    {run_sections}
    <p style="margin-top:16px;font-size:13px;color:#9ca3af;">
        <a href="/admin">← Admin</a>
    </p>
    """
    return HTMLResponse(_layout("Source Health", body, current_user))


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, response: Response):
    """User profile — name, email digest toggle, connected services."""
    db = get_db()
    current_user = _get_current_user(request)
    if not current_user:
        return RedirectResponse("/login")
    name = current_user.get("name") or ""
    email = current_user.get("email") or ""
    email_digest = current_user.get("email_digest", 1)
    digest_checked = "checked" if email_digest else ""
    is_admin = current_user.get("id") == 1
    admin_html = '<div style="margin-top:40px;padding-top:20px;border-top:1px solid #e0e0e0;"><a href="/admin" style="font-size:12px;color:#888;">Admin</a> &middot; <a href="/admin/sources" style="font-size:12px;color:#888;">Sources</a></div>' if is_admin else ""
    spotify_connected = bool(current_user.get("spotify_token_file"))
    youtube_connected = bool(current_user.get("youtube_token_file"))

    resp = HTMLResponse(_layout("Profile", f"""
<style>
.profile-page{{max-width:520px;margin:0 auto;padding:40px 0 80px}}
.profile-page h1{{font-size:2rem;font-weight:800;color:#000;margin-bottom:32px;letter-spacing:-.5px}}
.profile-page .card{{background:#fff;border:1px solid #e0e0e0;padding:20px;margin-bottom:20px}}
.profile-page .card h2{{font-size:10px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:2px;margin:0 0 12px}}
.profile-page label{{display:block;font-size:12px;font-weight:600;color:#333;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px}}
.profile-page input[type=text]{{width:100%;padding:10px 12px;border:1px solid #ccc;font-size:14px;font-family:inherit;outline:none;transition:border-color .15s}}
.profile-page input[type=text]:focus{{border-color:#000}}
.field{{margin-bottom:14px}}
.save-btn{{background:#4a6741;color:#fff;border:none;padding:10px 24px;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;text-transform:uppercase;letter-spacing:.5px;transition:background .15s}}
.save-btn:hover{{background:#3a5334}}
.save-ok{{display:none;border:1px solid #000;color:#000;padding:10px 14px;font-size:13px;margin-top:12px}}
.toggle-row{{display:flex;align-items:center;justify-content:space-between;padding:4px 0}}
.toggle-label div:first-child{{font-weight:700;font-size:14px;color:#000}}
.toggle-label div:last-child{{font-size:12px;color:#888;margin-top:2px}}
.toggle{{position:relative;width:44px;height:24px;flex-shrink:0}}
.toggle input{{opacity:0;width:0;height:0}}
.toggle .slider{{position:absolute;inset:0;background:#ccc;border-radius:24px;cursor:pointer;transition:.2s}}
.toggle .slider::before{{content:'';position:absolute;width:18px;height:18px;left:3px;top:3px;background:white;border-radius:50%;transition:.2s}}
.toggle input:checked+.slider{{background:#4a6741}}
.toggle input:checked+.slider::before{{transform:translateX(20px)}}
.svc-row{{display:flex;align-items:center;justify-content:space-between;padding:14px 20px}}
.svc-row+.svc-row{{border-top:1px solid #e0e0e0}}
</style>
<div class="profile-page">
  <h1>Profile</h1>

  <div class="card">
    <div class="field"><label>Name</label><input type="text" id="name" value="{name}"></div>
    <div class="field"><label>Email</label><input type="text" id="email" value="{email}" disabled style="background:#f9fafb;color:#9ca3af"></div>
    <button class="save-btn" onclick="save()">Save</button>
    <div class="save-ok" id="success">Saved!</div>
  </div>

  <div class="card">
    <h2>Email Digest</h2>
    <div class="toggle-row">
      <div class="toggle-label">
        <div>Weekly event picks</div>
        <div>Personalized recommendations delivered to your inbox</div>
      </div>
      <label class="toggle"><input type="checkbox" id="digest-toggle" {digest_checked} onchange="toggleDigest(this.checked)"><span class="slider"></span></label>
    </div>
  </div>

  <div class="card" style="padding:0;overflow:hidden;">
    <div style="padding:20px 20px 0;">
      <h2>Connected Services</h2>
      <p style="font-size:13px;color:#888;margin-bottom:12px;">We use these to personalize your event recommendations.</p>
    </div>
    <div class="svc-row" style="border-top:1px solid #e0e0e0;">
      <div><span style="font-weight:700;font-size:14px;color:#000;">Spotify</span><br><span style="font-size:12px;color:#888;">{"Connected" if spotify_connected else "Your top artists and listening history"}</span></div>
      {"<span style='font-size:12px;color:#888;font-weight:600;'>Connected</span>" if spotify_connected else '<a href="/auth/spotify" style="padding:6px 14px;background:#4a6741;color:#fff;font-size:11px;font-weight:700;text-decoration:none;text-transform:uppercase;letter-spacing:.5px;">Connect</a>'}
    </div>
    <div class="svc-row">
      <div><span style="font-weight:700;font-size:14px;color:#000;">YouTube</span><br><span style="font-size:12px;color:#888;">{"Connected — subscriptions and likes" if youtube_connected else "Your subscriptions and liked videos"}</span></div>
      {"<span style='font-size:12px;color:#888;font-weight:600;'>Connected</span>" if youtube_connected else '<a href="/auth/youtube" style="padding:6px 14px;background:#4a6741;color:#fff;font-size:11px;font-weight:700;text-decoration:none;text-transform:uppercase;letter-spacing:.5px;">Connect</a>'}
    </div>
  </div>

  <div class="card">
    <h2>Tell us about yourself</h2>
    <p style="font-size:13px;color:#888;margin-bottom:12px;">Paste anything — your YouTube feed, a list of bands you like, hobbies, whatever. We'll figure out your interests from it.</p>
    <textarea id="paste-box" placeholder="e.g. I love indie rock, just saw Magdalena Bay, really into climbing and art museums lately..." style="width:100%;min-height:100px;padding:10px 12px;border:1px solid #ccc;font-size:14px;font-family:inherit;resize:vertical;outline:none;box-sizing:border-box;"></textarea>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;">
      <span id="paste-status" style="font-size:12px;color:#888;"></span>
      <button onclick="submitPaste()" class="btn-primary" id="paste-btn">Save interests</button>
    </div>
  </div>

  <div style="text-align:center;margin-top:24px;">
    <a href="/taste-profile" style="font-size:13px;color:#4a6741;font-weight:600;">View your taste profile &rarr;</a>
  </div>

  {admin_html}
</div>

<script>
function save() {{
  fetch('/api/profile/update', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ name: document.getElementById('name').value.trim() }}),
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      const s = document.getElementById('success');
      s.style.display = 'block';
      setTimeout(() => s.style.display = 'none', 3000);
    }}
  }});
}}

function toggleDigest(on) {{
  fetch('/api/profile/update', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ email_digest: on ? 1 : 0 }}),
  }});
}}

function submitPaste() {{
  const text = document.getElementById('paste-box').value.trim();
  if (!text) return;
  const btn = document.getElementById('paste-btn');
  const status = document.getElementById('paste-status');
  btn.disabled = true;
  btn.textContent = 'Processing...';
  status.textContent = '';
  fetch('/api/profile/paste-interests', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ text: text }}),
  }}).then(r => r.json()).then(d => {{
    btn.disabled = false;
    btn.textContent = 'Save interests';
    if (d.ok) {{
      status.textContent = 'Saved! ' + (d.summary || '');
      status.style.color = '#000';
      document.getElementById('paste-box').value = '';
    }} else {{
      status.textContent = d.error || 'Failed';
      status.style.color = '#d00';
    }}
  }}).catch(() => {{
    btn.disabled = false;
    btn.textContent = 'Save interests';
    status.textContent = 'Network error';
    status.style.color = '#d00';
  }});
}}
</script>
""", current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.post("/api/profile/update")
async def profile_update(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "Not logged in"}, status_code=401)
    body = await request.json()
    user_id = current_user["id"]
    updates = []
    params = []
    if "name" in body:
        updates.append("name = ?")
        params.append(body["name"])
    if "location" in body and body["location"]:
        location_text = body["location"].strip()
        updates.append("location_query = ?")
        params.append(location_text)
        # Try to geocode the location to set lat/lon automatically
        try:
            from recom.events.geocoder import _geocode_query
            coords = _geocode_query(location_text)
            if coords:
                updates.append("home_lat = ?")
                params.append(coords[0])
                updates.append("home_lon = ?")
                params.append(coords[1])
        except Exception:
            pass  # Geocoding is best-effort; pipeline will retry later
    if "home_lat" in body and body["home_lat"] is not None:
        updates.append("home_lat = ?")
        params.append(body["home_lat"])
    if "home_lon" in body and body["home_lon"] is not None:
        updates.append("home_lon = ?")
        params.append(body["home_lon"])
    if "email_digest" in body:
        updates.append("email_digest = ?")
        params.append(1 if body["email_digest"] else 0)
    if updates:
        params.append(user_id)
        db.conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        db.conn.commit()
    return {"ok": True}


@app.post("/api/profile/paste-interests")
async def paste_interests(request: Request):
    """Parse free-form text (YouTube feed dump, interest list, etc.) into interests via Claude."""
    current_user = _get_current_user(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "Not logged in"}, status_code=401)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "Nothing to parse"})

    import anthropic
    settings = Settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    # Truncate to ~8k chars to avoid huge prompts
    text_truncated = text[:8000]
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": f"""Extract a list of interests, hobbies, favorite artists, genres, and topics from this text. Return ONLY a comma-separated list of keywords/phrases, nothing else. Be specific (e.g. "Magdalena Bay" not just "music"). Max 30 items.

Text:
{text_truncated}"""}],
        )
        keywords_raw = resp.content[0].text.strip()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})

    # Save to user's interests file
    from pathlib import Path
    interests_dir = Path("state/interests")
    interests_dir.mkdir(parents=True, exist_ok=True)
    interests_file = interests_dir / f"user_{current_user['id']}_paste.txt"
    # Append to existing paste interests
    existing = interests_file.read_text() if interests_file.exists() else ""
    combined = (existing + "\n" + keywords_raw).strip()
    interests_file.write_text(combined)
    # Update user record
    db = get_db()
    db.conn.execute("UPDATE users SET interests_file = ? WHERE id = ?",
                    (str(interests_file), current_user["id"]))
    db.conn.commit()

    # Count keywords for summary
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    return JSONResponse({"ok": True, "summary": f"Found {len(keywords)} interests", "keywords": keywords})


@app.post("/api/profile/upload-youtube")
async def upload_youtube_takeout(request: Request):
    """Accept YouTube Takeout watch-history (.json or .html) and save for ingest."""
    current_user = _get_current_user(request)
    if not current_user:
        return RedirectResponse("/login", status_code=307)
    form = await request.form()
    upload = form.get("file")
    if not upload or not hasattr(upload, "read"):
        return HTMLResponse("No file uploaded", status_code=400)
    import json as _json
    from pathlib import Path
    content = await upload.read()
    filename = getattr(upload, "filename", "") or ""

    takeout_dir = Path("state/takeout")
    takeout_dir.mkdir(parents=True, exist_ok=True)

    if filename.endswith(".html") or content[:50].strip().startswith(b"<"):
        # HTML format — parse video titles from the Takeout HTML
        import re as _re
        text = content.decode("utf-8", errors="ignore")
        # Google Takeout HTML has video titles in links like: <a href="https://www.youtube.com/watch?v=...">Title</a>
        titles = _re.findall(r'href="https?://(?:www\.)?youtube\.com/watch\?v=[^"]*"[^>]*>([^<]+)</a>', text)
        if not titles:
            # Try broader pattern
            titles = _re.findall(r'>([^<]{5,80})</a>', text)
        # Save as JSON array of titles
        out_path = takeout_dir / f"youtube_user{current_user['id']}.json"
        out_path.write_text(_json.dumps([{"title": t.strip()} for t in titles[:2000]]))
        count = len(titles)
    else:
        # JSON format
        try:
            data = _json.loads(content)
        except _json.JSONDecodeError:
            return HTMLResponse("Could not parse file. Upload a .json or .html file from Google Takeout.", status_code=400)
        out_path = takeout_dir / f"youtube_user{current_user['id']}.json"
        out_path.write_bytes(content)
        count = len(data) if isinstance(data, list) else 1

    logger.info("YouTube takeout saved for user %s: %d items, %s", current_user["id"], count, out_path)
    return RedirectResponse(f"/profile?success=YouTube+history+uploaded+({count}+videos)", status_code=303)


def _radar_svg(axes: list[str], values: list[float], colors: list[str] | None = None,
               size: int = 200, fill: str = "rgba(129,140,248,0.25)",
               stroke: str = "#818cf8") -> str:
    """Generate a pure-SVG radar (spider) chart. values should be 0.0–1.0 each."""
    import math
    n = len(axes)
    if n < 3:
        return ""
    cx = cy = size / 2
    r = size * 0.38
    label_r = size * 0.48
    # Grid rings
    rings_svg = ""
    for level in [0.25, 0.5, 0.75, 1.0]:
        pts = []
        for i in range(n):
            angle = math.pi * 2 * i / n - math.pi / 2
            x = cx + math.cos(angle) * r * level
            y = cy + math.sin(angle) * r * level
            pts.append(f"{x:.1f},{y:.1f}")
        rings_svg += f'<polygon points="{" ".join(pts)}" fill="none" stroke="#2d2d5e" stroke-width="0.8"/>'
    # Axis lines
    axes_svg = ""
    for i in range(n):
        angle = math.pi * 2 * i / n - math.pi / 2
        x = cx + math.cos(angle) * r
        y = cy + math.sin(angle) * r
        axes_svg += f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#2d2d5e" stroke-width="0.8"/>'
    # Data polygon
    data_pts = []
    for i, val in enumerate(values):
        val = max(0.0, min(1.0, val))
        angle = math.pi * 2 * i / n - math.pi / 2
        x = cx + math.cos(angle) * r * val
        y = cy + math.sin(angle) * r * val
        data_pts.append(f"{x:.1f},{y:.1f}")
    data_svg = f'<polygon points="{" ".join(data_pts)}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    # Dots at each vertex
    dots_svg = ""
    for i, val in enumerate(values):
        val = max(0.0, min(1.0, val))
        angle = math.pi * 2 * i / n - math.pi / 2
        x = cx + math.cos(angle) * r * val
        y = cy + math.sin(angle) * r * val
        dots_svg += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{stroke}"/>'
    # Axis labels
    labels_svg = ""
    for i, label in enumerate(axes):
        angle = math.pi * 2 * i / n - math.pi / 2
        lx = cx + math.cos(angle) * label_r
        ly = cy + math.sin(angle) * label_r
        anchor = "middle"
        if lx < cx - 5:
            anchor = "end"
        elif lx > cx + 5:
            anchor = "start"
        labels_svg += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'dominant-baseline="middle" font-size="9" fill="#94a3b8" font-family="system-ui">'
            f'{label}</text>'
        )
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'xmlns="http://www.w3.org/2000/svg" style="overflow:visible">'
        f'{rings_svg}{axes_svg}{data_svg}{dots_svg}{labels_svg}'
        f'</svg>'
    )


@app.get("/taste-profile", response_class=HTMLResponse)
async def taste_profile_page(request: Request):
    """Show users what Calyx knows about them — transparent interest profile."""
    db = get_db()
    current_user = _get_current_user(request)
    if not current_user:
        return RedirectResponse("/login")
    user_id = current_user["id"]

    # 1. Interest profile from latest pipeline run
    import json as _json
    interests = []
    run = db.get_user_latest_run(user_id)
    if not run:
        # Fall back to any run
        runs = db.get_runs()
        if runs:
            run = runs[0]
    if run and run.get("interest_profile_json"):
        try:
            profile = _json.loads(run["interest_profile_json"])
            interests = profile.get("interests", [])
        except (ValueError, TypeError):
            pass

    # 2. Manual interests from file
    from pathlib import Path
    manual = []
    settings = Settings()
    interests_path = Path(settings.interests_file)
    if interests_path.exists():
        for line in interests_path.read_text().splitlines():
            word = line.strip().split("\t")[-1].strip() if "\t" in line else line.strip()
            if word:
                manual.append(word)

    # 3. Paste-box interests
    paste_keywords = []
    paste_file = Path(f"state/interests/user_{user_id}_paste.txt")
    if paste_file.exists():
        for line in paste_file.read_text().splitlines():
            for kw in line.split(","):
                kw = kw.strip()
                if kw:
                    paste_keywords.append(kw)

    # 4. Spotify top artists — fetch live from token
    spotify_artists = []
    spotify_token_file = current_user.get("spotify_token_file")
    if spotify_token_file:
        try:
            import httpx as _httpx
            token_data = _json.loads(Path(spotify_token_file).read_text())
            refresh_token = token_data.get("refresh_token", "")
            access_token = token_data.get("access_token", "")
            # Refresh if we have a refresh token
            if refresh_token:
                settings_obj = Settings()
                r = _httpx.post("https://accounts.spotify.com/api/token", data={
                    "grant_type": "refresh_token", "refresh_token": refresh_token,
                    "client_id": settings_obj.spotify_client_id, "client_secret": settings_obj.spotify_client_secret,
                })
                if r.status_code == 200:
                    access_token = r.json().get("access_token", access_token)
            if access_token:
                r = _httpx.get("https://api.spotify.com/v1/me/top/artists?limit=20&time_range=medium_term",
                    headers={"Authorization": f"Bearer {access_token}"})
                if r.status_code == 200:
                    for a in r.json().get("items", []):
                        genres = a.get("genres", [])[:2]
                        label = a["name"] + (f" ({', '.join(genres)})" if genres else "")
                        spotify_artists.append(label)
        except Exception:
            pass

    # 5. YouTube subscriptions — fetch live from token
    youtube_subs = []
    youtube_token_file = current_user.get("youtube_token_file")
    if youtube_token_file and Path(youtube_token_file).exists():
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials.from_authorized_user_file(str(Path(youtube_token_file)))
            yt = build("youtube", "v3", credentials=creds)
            subs = yt.subscriptions().list(mine=True, part="snippet", maxResults=20).execute()
            youtube_subs = [item["snippet"]["title"] for item in subs.get("items", [])]
        except Exception:
            pass

    # Build tag HTML
    def _tags(items: list[str], color: str = "#4a6741") -> str:
        if not items:
            return '<span style="color:#ccc;font-size:13px;">Nothing yet</span>'
        return " ".join(
            f'<span style="display:inline-block;padding:4px 12px;margin:3px;background:#f4f7f3;color:{color};font-size:13px;font-weight:500;border:1px solid #e0e0e0;">{item}</span>'
            for item in items[:30]
        )

    # Build interest tags grouped by source
    algo_interests = [i for i in interests if "manual" not in str(i.get("source_signals", []))]
    algo_tags = [i["topic"] for i in sorted(algo_interests, key=lambda x: -x.get("confidence", 0))]

    body = f"""
<style>
.taste-page{{max-width:620px;margin:0 auto;padding:40px 0 80px}}
.taste-page h1{{font-size:2rem;font-weight:800;color:#000;margin-bottom:8px;letter-spacing:-.5px}}
.taste-page .sub{{font-size:14px;color:#888;margin-bottom:32px}}
.taste-section{{margin-bottom:32px}}
.taste-section h2{{font-size:10px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:2px;margin:0 0 12px}}
.taste-section .tags{{line-height:2}}
</style>
<div class="taste-page">
  <h1>Your Taste Profile</h1>
  <p class="sub">This is what Calyx knows about you. It shapes your event recommendations.</p>

  <div class="taste-section">
    <h2>Interests (from pipeline analysis)</h2>
    <div class="tags">{_tags(algo_tags)}</div>
  </div>

  <div class="taste-section">
    <h2>Manual interests</h2>
    <div class="tags">{_tags(manual)}</div>
  </div>

  {"<div class='taste-section'><h2>From your paste</h2><div class='tags'>" + _tags(paste_keywords) + "</div></div>" if paste_keywords else ""}

  {"<div class='taste-section'><h2>Music (from Spotify)</h2><div class='tags'>" + _tags(spotify_artists, "#8b6914") + "</div></div>" if spotify_artists else ""}

  {"<div class='taste-section'><h2>YouTube subscriptions</h2><div class='tags'>" + _tags(youtube_subs, "#c4302b") + "</div></div>" if youtube_subs else ""}

  <div style="margin-top:32px;">
    <a href="/profile" class="btn-secondary" style="display:inline-block;text-decoration:none;">Account settings</a>
  </div>
</div>
"""
    return HTMLResponse(_layout("Your Taste Profile", body, current_user))


@app.get("/landing", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Marketing / about page for Calyx — uses dashboard design language."""
    current_user = _get_current_user(request)
    body = """
<style>
  .landing-hero { text-align: center; padding: 40px 0 32px; }
  .landing-hero h1 { font-size: 2.2rem; font-weight: 800; color: #1e293b; line-height: 1.15; letter-spacing: -1px; margin-bottom: 16px; }
  .landing-hero h1 .accent { color: #4f46e5; }
  .landing-hero .sub { font-size: 16px; color: #6b7280; max-width: 520px; margin: 0 auto 28px; line-height: 1.6; }
  .landing-hero .cta-row { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
  .landing-eyebrow { display: inline-block; font-size: 11px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: #4f46e5; background: #ede9fe; padding: 5px 14px; border-radius: 20px; margin-bottom: 20px; }

  .steps-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 28px; }
  .step-card { background: white; border: 1px solid #e2e8f0; border-radius: 16px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  .step-card .step-num { font-size: 11px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: #4f46e5; margin-bottom: 10px; }
  .step-card h3 { font-size: 15px; font-weight: 700; color: #1e293b; margin-bottom: 6px; }
  .step-card p { font-size: 13px; color: #6b7280; line-height: 1.55; }

  .features-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-top: 16px; }
  .feat-item { display: flex; gap: 12px; align-items: flex-start; }
  .feat-icon { width: 36px; height: 36px; border-radius: 10px; background: #ede9fe; display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }
  .feat-item h4 { font-size: 14px; font-weight: 700; color: #1e293b; margin-bottom: 2px; }
  .feat-item p { font-size: 12px; color: #6b7280; line-height: 1.5; }

  .vibe-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 16px; }
  .vibe-card { border-radius: 16px; padding: 20px; border: 1px solid #e2e8f0; background: white; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  .vibe-card .vibe-label { font-size: 11px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 8px; }
  .vibe-card.social .vibe-label { color: #d97706; }
  .vibe-card.intellectual .vibe-label { color: #7c3aed; }
  .vibe-card.mixed .vibe-label { color: #2563eb; }
  .vibe-card.social { border-left: 3px solid #f59e0b; }
  .vibe-card.intellectual { border-left: 3px solid #8b5cf6; }
  .vibe-card.mixed { border-left: 3px solid #3b82f6; }
  .vibe-card h4 { font-size: 14px; font-weight: 700; color: #1e293b; margin-bottom: 4px; }
  .vibe-card p { font-size: 12px; color: #6b7280; line-height: 1.5; }
  .vibe-score { display: inline-block; font-size: 12px; font-weight: 800; padding: 2px 8px; border-radius: 8px; margin-top: 8px; background: #f1f5f9; color: #374151; }

  .landing-cta { text-align: center; padding: 36px 0 20px; }
  .landing-cta h2 { font-size: 1.5rem; font-weight: 800; color: #1e293b; margin-bottom: 10px; letter-spacing: -.5px; }
  .landing-cta p { font-size: 15px; color: #6b7280; margin-bottom: 24px; }
  .landing-cta .note { font-size: 12px; color: #9ca3af; margin-top: 16px; }

  .landing-footer { text-align: center; padding: 24px 0 0; border-top: 1px solid #e2e8f0; margin-top: 20px; }
  .landing-footer p { font-size: 12px; color: #9ca3af; }
  .landing-footer a { color: #4f46e5; }

  @media (max-width: 640px) {
    .vibe-cards { grid-template-columns: 1fr; }
    .landing-hero h1 { font-size: 1.7rem; }
  }
</style>

<!-- Hero -->
<div class="landing-hero">
  <div class="landing-eyebrow">The plans that keep them close</div>
  <h1>Drop an event.<br>Your crew <span class="accent">taps in</span>.</h1>
  <p class="sub">Every friend group has a rhythm: the dinners, the weekend plans, the spontaneous &quot;who&apos;s free tonight.&quot; Calyx is the shared calendar that quietly holds your people together &mdash; without the back-and-forth.</p>
  <div class="cta-row">
    <a href="/join" class="btn-primary btn-pill" style="padding:12px 28px;font-size:15px;">Create your group &rarr;</a>
    <a href="/" class="btn-secondary btn-pill" style="padding:12px 28px;font-size:15px;">See what&apos;s happening</a>
  </div>
</div>

<!-- How it works -->
<h2>How it works</h2>
<div class="steps-grid">
  <div class="step-card">
    <div class="step-num">01 &middot; Drop</div>
    <h3>Share an event or browse recs</h3>
    <p>Add something you found, or let Calyx surface what&apos;s happening from 13+ sources across Boston &amp; Cambridge.</p>
  </div>
  <div class="step-card">
    <div class="step-num">02 &middot; Tap in</div>
    <h3>Your crew RSVPs in real time</h3>
    <p>Headcounts build instantly. See who&apos;s going, who&apos;s maybe, who needs a nudge. No group chat chaos.</p>
  </div>
  <div class="step-card">
    <div class="step-num">03 &middot; Show up</div>
    <h3>Everyone&apos;s synced</h3>
    <p>Calendar feeds, daily digests, and nudge notifications keep the whole crew on the same page.</p>
  </div>
</div>

<!-- Features -->
<div class="card">
  <h2 style="margin-top:0;">Everything your group chat wishes it could do</h2>
  <div class="features-grid">
    <div class="feat-item">
      <div class="feat-icon">&#x1F465;</div>
      <div><h4>Group calendar</h4><p>Shared events, live headcounts, one link to invite everyone.</p></div>
    </div>
    <div class="feat-item">
      <div class="feat-icon">&#x1F44B;</div>
      <div><h4>One-tap RSVP</h4><p>Going, maybe, or can&apos;t &mdash; your friends see instantly.</p></div>
    </div>
    <div class="feat-item">
      <div class="feat-icon">&#x1F514;</div>
      <div><h4>Nudge &amp; notify</h4><p>Poke your crew about events. Get notified when friends RSVP.</p></div>
    </div>
    <div class="feat-item">
      <div class="feat-icon">&#x1F4E1;</div>
      <div><h4>Calendar sync</h4><p>Subscribe in Apple Calendar, Google Calendar, or any app.</p></div>
    </div>
    <div class="feat-item">
      <div class="feat-icon">&#x2728;</div>
      <div><h4>Smart recommendations</h4><p>AI surfaces events matched to your taste from Spotify, YouTube, and newsletters.</p></div>
    </div>
    <div class="feat-item">
      <div class="feat-icon">&#x1F4EC;</div>
      <div><h4>Daily picks email</h4><p>A short digest of today&apos;s best events, sent every morning.</p></div>
    </div>
  </div>
</div>

<!-- The Calyx difference -->
<h2>Named after what holds it together</h2>
<p style="font-size:14px;color:#6b7280;margin-bottom:16px;max-width:520px;">A calyx is the part of a flower that holds all the petals together. That rhythm of dinners, weekend plans, and spontaneous nights out? It lives scattered across texts and half-made plans. Calyx is the structure underneath.</p>
<div class="vibe-cards">
  <div class="vibe-card social">
    <div class="vibe-label">Not a to-do list</div>
    <h4>Less scheduling, more showing up</h4>
    <p>Plans shouldn&apos;t feel like work. Drop an event, friends tap in, done.</p>
  </div>
  <div class="vibe-card intellectual">
    <div class="vibe-label">Not another group chat</div>
    <h4>Signal without noise</h4>
    <p>No &quot;who&apos;s free Saturday?&quot; threads. Just events, RSVPs, and a headcount.</p>
  </div>
  <div class="vibe-card mixed">
    <div class="vibe-label">Not just your calendar</div>
    <h4>Your crew&apos;s calendar</h4>
    <p>See what friends are going to. Get nudged about things you&apos;d love.</p>
  </div>
</div>

<!-- CTA -->
<div class="landing-cta">
  <h2>Because the best friendships aren&apos;t just people.</h2>
  <p>They&apos;re the plans that keep them close.</p>
  <a href="/join" class="btn-primary" style="padding:12px 32px;font-size:15px;border-radius:20px;">Get started &rarr;</a>
  <p class="note">Free. No credit card. Boston &amp; Cambridge.</p>
</div>

<div class="landing-footer">
  <p>&copy; 2026 Calyx</p>
</div>
"""
    return HTMLResponse(_layout("About", body, current_user))


@app.get("/")
async def home_redirect(request: Request):
    user = _get_current_user(request)
    if user:
        resp = RedirectResponse("/groups", status_code=302)
        return _maybe_set_cookie(request, resp, user)
    return RedirectResponse("/landing", status_code=302)


@app.post("/api/search", response_class=JSONResponse)
async def api_search(request: Request):
    """Tiered event search: DB first, then Gemini web search fallback."""
    current_user = _get_current_user(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "Not logged in"}, status_code=401)
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"ok": False, "error": "Empty query"})

    db = get_db()
    settings = Settings()

    # Tier 1: Search DB events via text match
    user_id = current_user["id"]
    all_events = db.get_latest_scored_events(user_id)
    kept = [e for e in all_events if e.get("keep") and e.get("start_time")]
    q_lower = query.lower()
    db_matches = []
    for e in kept:
        haystack = f"{e.get('title','')} {e.get('location_name','')} {e.get('description','')} {e.get('match_reason','')} {e.get('category','')}".lower()
        if q_lower in haystack:
            db_matches.append({
                "title": e.get("title", ""),
                "start_time": e.get("start_time", ""),
                "location": e.get("location_name", ""),
                "url": e.get("url", ""),
                "score": int(e.get("score") or 0),
                "match_reason": e.get("match_reason", ""),
                "source": "db",
            })

    # If we have enough DB results, return them
    if len(db_matches) >= 3:
        return JSONResponse({"ok": True, "results": db_matches[:10], "source": "db"})

    # Tier 2: Gemini web search fallback
    web_results = []
    if settings.gemini_api_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=settings.gemini_api_key)
            model = genai.GenerativeModel("gemini-2.0-flash")
            from google.generativeai import types as gtypes
            resp = model.generate_content(
                f"Find upcoming events in Boston/Cambridge area matching: {query}. "
                f"Return JSON array of objects with fields: title, date (ISO 8601), location, url, description, price. "
                f"Max 8 results. Only real, verifiable events.",
                tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
            )
            # Parse the response text as JSON
            import json as _json, re as _re
            text = resp.text
            json_match = _re.search(r'\[.*\]', text, _re.DOTALL)
            if json_match:
                events_raw = _json.loads(json_match.group())
                for ev in events_raw[:8]:
                    web_results.append({
                        "title": ev.get("title", ""),
                        "start_time": ev.get("date", ""),
                        "location": ev.get("location", ""),
                        "url": ev.get("url", ""),
                        "score": 0,
                        "match_reason": ev.get("description", "")[:120],
                        "source": "web",
                    })
        except Exception as exc:
            logger.exception("Gemini search failed for query: %s", query)

    # Merge: DB results first, then web results
    merged = db_matches[:5] + web_results[:5]

    # Fire-and-forget: retro analysis when web found things DB didn't
    if web_results and len(db_matches) < 3:
        import asyncio
        asyncio.create_task(_run_search_retro(query, db_matches, web_results, settings))

    return JSONResponse({
        "ok": True,
        "results": merged,
        "source": "merged" if web_results else "db",
        "db_count": len(db_matches),
        "web_count": len(web_results),
    })


async def _run_search_retro(query: str, db_results: list, web_results: list, settings):
    """Background: Claude diagnoses why DB missed events the web found."""
    try:
        import anthropic, json as _json
        from pathlib import Path

        db = get_db()
        web_titles = [r["title"] for r in web_results[:5]]
        db_titles = [r["title"] for r in db_results[:5]]

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": f"""A user searched for "{query}" in a Boston/Cambridge event app.
Our database had {len(db_results)} results: {db_titles[:5]}
Web search found {len(web_results)} additional results: {web_titles}

In 2-3 sentences: Why did our database miss these? What event sources or scrapers should we add? Be specific (name websites/platforms)."""}],
        )
        diagnosis = resp.content[0].text.strip()

        # Save to DB
        db.conn.execute(
            "INSERT INTO search_retros (query, timestamp, db_count, web_count, diagnosis) VALUES (?, ?, ?, ?, ?)",
            (query, datetime.now().isoformat(), len(db_results), len(web_results), diagnosis),
        )
        db.conn.commit()

        # Append to todo.txt if actionable
        if "add" in diagnosis.lower() or "scraper" in diagnosis.lower() or "source" in diagnosis.lower():
            todo_path = Path("todo.txt")
            with open(todo_path, "a") as f:
                f.write(f"\n# TODO: [auto-retro] {diagnosis[:120]}\n")
                f.write(f"#   Query: \"{query}\" — {len(web_results)} web results vs {len(db_results)} DB results\n")

        logger.info("Search retro for '%s': %s", query, diagnosis[:100])
    except Exception:
        logger.exception("Search retro failed for '%s'", query)


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_view(request: Request):
    import re as _re

    db = get_db()
    settings = Settings()
    current_user = _get_current_user(request)

    home_lat = float(current_user["home_lat"]) if current_user and current_user.get("home_lat") else settings.latitude
    home_lon = float(current_user["home_lon"]) if current_user and current_user.get("home_lon") else settings.longitude

    # Get latest scored events — simple, no run-finding logic
    user_id = current_user["id"] if current_user else None
    all_events = db.get_latest_scored_events(user_id)
    kept = [e for e in all_events if e.get("keep") and e.get("start_time")]
    if not kept:
        return HTMLResponse(_layout("Discover", "<h1>Discover</h1><div class='card'><p>No scored events yet. Pipeline may still be running.</p></div>", current_user))

    # Find run_id for RSVP API calls
    run_id = kept[0].get("run_id", 0)

    # Fetch RSVPs
    all_event_ids = [e.get("event_id", "") for e in kept if e.get("event_id")]
    rsvps_map = db.get_rsvps_for_events(all_event_ids)
    user_token = current_user["user_token"] if current_user else ""

    # Build JSON event array for JS
    events_json = []
    for e in kept:
        eid = e.get("event_id", "")
        raw_desc = e.get("description") or ""
        clean_desc = _re.sub(r'<[^>]+>', '', raw_desc).replace("&nbsp;", " ").strip()
        evt_rsvps = rsvps_map.get(eid, [])
        rsvp_list = [{"user_name": rv["user_name"], "status": rv["status"]} for rv in evt_rsvps]
        # Find current user's own RSVP status
        my_rsvp = ""
        if current_user:
            for rv in evt_rsvps:
                if rv.get("user_id") == current_user["id"]:
                    my_rsvp = rv["status"]
                    break
        events_json.append({
            "id": eid,
            "title": e.get("title", ""),
            "start": e.get("start_time") or "",
            "end": e.get("end_time") or "",
            "url": e.get("url") or "",
            "score": int(e.get("score") or 0),
            "vibe": e.get("vibe", "mixed"),
            "location": e.get("location_name") or "",
            "price": e.get("price") or "",
            "description": clean_desc[:200],
            "match_reason": (e.get("match_reason") or "")[:120],
            "event_type": e.get("event_type", "event"),
            "rsvps": rsvp_list,
            "my_rsvp": my_rsvp,
            "primary": True,
            "source": (e.get("source") or "").replace("_", " "),
            "image_url": e.get("image_url") or "",
            "lat": e.get("lat"),
            "lon": e.get("lon"),
            "scores": {
                "interest": round(float(e.get("interest_score") or 0)),
                "social": round(float(e.get("social_score") or 0)),
                "urgency": round(float(e.get("urgency_score") or 0)),
                "logistics": round(float(e.get("logistics_score") or 0)),
                "friend": round(float(e.get("friend_score") or 0)),
                "discovery": round(float(e.get("discovery_score") or 0)),
                "quality": round(float(e.get("quality_score") or 0)),
            },
        })

    events_json_str = json.dumps(events_json, default=str)

    top_picks = len(kept)

    _default_og = '<meta property="og:site_name" content="Calyx"><meta property="og:type" content="website"><meta property="og:title" content="This Week in Cambridge"><meta property="og:description" content="Find events and make plans with friends"><meta property="og:image" content="https://calyx.arthgupta.dev/static/og-image.png"><meta name="twitter:card" content="summary_large_image">'
    page_html = LAYOUT_STYLE.replace("__TITLE__", "This Week in Cambridge").replace("__OG_TAGS__", _default_og) + render_nav(current_user) + '<div class="app-content">' + f"""
    <style>
      /* --- Top bar --- */
      .page-header {{ margin-bottom: 16px; }}
      .page-header h1 {{ font-size: 22px; margin-bottom: 4px; }}
      .page-header .subtitle {{ font-size: 13px; color: #888; }}
      .toolbar {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
      .view-toggle {{ display: flex; border: 1px solid #e0e0e0; }}
      .view-toggle button {{ padding: 7px 16px; border: none; background: transparent; cursor: pointer; font-size: 12px; font-weight: 500; color: #888; transition: all .15s; text-transform: uppercase; letter-spacing: .5px; }}
      .view-toggle button.active {{ background: #000; color: #fff; }}
      .score-badge {{ display: inline-block; font-weight: 700; padding: 2px 10px; font-size: 13px; }}
      .score-high {{ background: #4a6741; color: #fff; }}
      .score-mid {{ background: #e8ede7; color: #4a6741; }}
      .score-low {{ background: #f5f5f5; color: #999; }}
      .rsvp-btn {{ font-size: 11px; padding: 5px 14px; border: 1px solid #ccc; background: white; cursor: pointer; color: #888; font-weight: 700; transition: all .15s; text-transform: uppercase; letter-spacing: .3px; }}
      .rsvp-btn:hover, .rsvp-btn.active {{ color: #4a6741; border-color: #4a6741; }}
      .rsvp-btn.going:hover, .rsvp-btn.going.active {{ background: #4a6741; color: #fff; border-color: #4a6741; }}
      .rsvp-btn.maybe:hover, .rsvp-btn.maybe.active {{ background: #f4f7f3; color: #4a6741; border-color: #4a6741; }}
      /* --- Card list view --- */
      #list-view {{ display: none; }}
      .day-group {{ margin-bottom: 28px; }}
      .day-header {{ position: sticky; top: 56px; background: #fff; padding: 12px 0 8px; font-size: 11px; font-weight: 700; color: #000; z-index: 10; border-bottom: 1px solid #000; display: flex; justify-content: space-between; align-items: baseline; text-transform: uppercase; letter-spacing: 1.5px; }}
      .day-header .day-count {{ font-size: 11px; font-weight: 500; color: #888; text-transform: none; letter-spacing: 0; }}
      .see-more-btn {{ display: block; width: 100%; margin: 6px 0 10px; padding: 10px; background: #fff; border: 1px solid #e0e0e0; color: #000; font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit; text-align: center; transition: all .15s; text-transform: uppercase; letter-spacing: .5px; }}
      .see-more-btn:hover {{ background: #f5f5f5; }}
      .see-more-collapse {{ color: #888; }}
      .see-more-collapse:hover {{ background: #f5f5f5; color: #000; }}
      .evt-card {{ background: white; margin: 0; border-bottom: 1px solid #e0e0e0; border-left: 3px solid; transition: background .15s; cursor: pointer; overflow: hidden; display: flex; }}
      .evt-card:hover {{ background: #fafafa; }}
      .evt-card.vibe-social {{ border-left-color: #c9a227; }}
      .evt-card.vibe-intellectual {{ border-left-color: #4a6741; }}
      .evt-card.vibe-mixed {{ border-left-color: #555; }}
      .evt-card.rsvp-going-card {{ border-left-color: #4a6741; border-left-width: 4px; }}
      .evt-card.rsvp-maybe-card {{ border-left-color: #888; }}
      .evt-card .card-body {{ flex: 1; padding: 14px 16px; min-width: 0; }}
      .evt-card .card-top {{ display: flex; align-items: flex-start; gap: 8px; }}
      .evt-card .card-title {{ font-size: 14px; font-weight: 700; color: #000; flex: 1; text-decoration: none; line-height: 1.35; }}
      .evt-card .card-title:hover {{ text-decoration: underline; }}
      .evt-card .card-score {{ font-weight: 800; padding: 2px 8px; font-size: 12px; white-space: nowrap; flex-shrink: 0; }}
      .evt-card .card-meta {{ font-size: 12px; color: #888; margin-top: 3px; }}
      .evt-card .card-reason {{ font-size: 12px; color: #555; background: #f5f5f5; padding: 4px 8px; margin-top: 6px; line-height: 1.35; font-style: italic; }}
      .evt-card .card-actions {{ display: flex; gap: 6px; align-items: center; margin-top: 8px; flex-wrap: wrap; }}
      .source-badge {{ font-size: 10px; font-weight: 600; padding: 1px 7px; background: #f5f5f5; color: #888; text-transform: capitalize; }}
      /* --- Timeline view --- */
      #timeline-view {{ display: none; overflow-x: auto; padding-bottom: 8px; }}
      .timeline-week {{ display: flex; gap: 1px; min-width: max-content; padding: 4px 0 12px; background: #e0e0e0; }}
      .timeline-col {{ width: 220px; flex-shrink: 0; background: #fff; }}
      .timeline-col-header {{ background: #fff; padding: 10px 14px 8px; border-bottom: 2px solid #e0e0e0; margin-bottom: 0; }}
      .timeline-col-header .col-day {{ font-size: 11px; font-weight: 700; color: #000; text-transform: uppercase; letter-spacing: 1px; }}
      .timeline-col-header .col-date {{ font-size: 11px; color: #888; margin-top: 2px; }}
      .timeline-col-header .col-count {{ font-size: 10px; font-weight: 600; background: #f5f5f5; color: #888; padding: 1px 6px; display: inline-block; margin-top: 4px; }}
      .timeline-col-header.today {{ border-bottom-color: #000; }}
      .timeline-col-header.today .col-day {{ color: #000; }}
      .tl-card {{ background: white; padding: 10px 12px; margin: 0; border-bottom: 1px solid #e0e0e0; border-left: 3px solid; cursor: pointer; transition: background .15s; }}
      .tl-card:hover {{ background: #fafafa; }}
      .tl-card.vibe-social {{ border-left-color: #c9a227; }}
      .tl-card.vibe-intellectual {{ border-left-color: #4a6741; }}
      .tl-card.vibe-mixed {{ border-left-color: #555; }}
      .tl-card .tl-title {{ font-size: 13px; font-weight: 600; color: #000; line-height: 1.3; margin-bottom: 5px; }}
      .tl-card .tl-time {{ font-size: 11px; color: #888; margin-bottom: 3px; }}
      .tl-card .tl-loc {{ font-size: 11px; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
      .tl-card .tl-score {{ font-size: 11px; font-weight: 700; padding: 1px 6px; display: inline-block; margin-top: 4px; }}
      .tl-empty {{ text-align: center; color: #ccc; font-size: 12px; padding: 20px 0; }}
      .tl-overflow {{ opacity: .7; }}
      .tl-more-btn {{ display: block; width: 100%; padding: 6px; background: #fff; border: 1px solid #e0e0e0; color: #000; font-size: 11px; font-weight: 600; cursor: pointer; font-family: inherit; text-align: center; margin-top: 0; transition: all .15s; text-transform: uppercase; letter-spacing: .5px; }}
      .tl-more-btn:hover {{ background: #f5f5f5; }}
      .tl-collapse-btn {{ color: #888; }}
      @media (min-width: 641px) {{
        .evt-modal-overlay {{ align-items: center; }}
        .evt-modal {{ border-radius: 16px; }}
      }}
      @media (max-width: 640px) {{
        .toolbar {{ gap: 8px; }}
        .evt-card {{ margin: 6px 0; padding: 12px 14px; }}
        body {{ padding: 12px; }}
        .page-header h1 {{ font-size: 20px; }}
        .heat-grid {{ grid-template-columns: 1fr; }}
        .timeline-col {{ width: 180px; }}
      }}
    </style>

    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:8px;">
      <div>
        <h1 style="margin:0;">Discover</h1>
        <div style="font-size:13px;color:#888;margin-top:4px;">{top_picks} events this week</div>
      </div>
      <div class="view-toggle">
        <button id="btn-list" onclick="switchView('list')">List</button>
        <button id="btn-timeline" onclick="switchView('timeline')">Week</button>
      </div>
    </div>

    <div style="position:relative;margin-bottom:20px;">
      <input id="search-input" type="text" placeholder="Try &quot;jazz tonight&quot; or &quot;outdoor things this weekend&quot;" oninput="onSearchInput()" onkeydown="if(event.key==='Enter')doSearch()"
             style="width:100%;padding:12px 14px;border:1px solid #ccc;font-size:14px;font-family:inherit;outline:none;box-sizing:border-box;">
      <span id="search-spinner" style="display:none;position:absolute;right:14px;top:50%;transform:translateY(-50%);font-size:12px;color:#888;">searching...</span>
    </div>
    <div id="search-results" style="display:none;margin-bottom:24px;"></div>
    <input type="hidden" id="score-slider" value="0">
    <input type="hidden" id="dist-slider" value="50">
    <span id="score-label" style="display:none">0</span>
    <span id="dist-label" style="display:none">Any</span>

    <div id="cal-view" style="display:none"><div id="fc-container"></div></div>
    <div id="list-view"></div>
    <div id="timeline-view"><div class="timeline-week" id="tl-week"></div></div>
    <div id="heat-view" style="display:none"></div>

    <script>
    const EVENTS = {events_json_str};
    const RUN_ID = {run_id};
    const USER_TOKEN = '{user_token}';
    const HAS_USER = {'true' if current_user else 'false'};
    const HOME_LAT = {home_lat};
    const HOME_LON = {home_lon};
    const VIBE_COLORS = {{social:'#c9a227', intellectual:'#4a6741', mixed:'#555'}};
    const VIBE_BG = {{social:'#faf8f0', intellectual:'#f2f5f1', mixed:'#f5f5f5'}};

    function distKm(lat, lon) {{
      if (lat == null || lon == null) return null;
      const R = 6371, dLat = (lat-HOME_LAT)*Math.PI/180, dLon = (lon-HOME_LON)*Math.PI/180;
      const a = Math.sin(dLat/2)**2 + Math.cos(HOME_LAT*Math.PI/180)*Math.cos(lat*Math.PI/180)*Math.sin(dLon/2)**2;
      return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    }}
    function distLabel(e) {{
      const km = distKm(e.lat, e.lon);
      if (km == null) return '';
      if (km < 1) return Math.round(km*1000) + 'm away';
      return km.toFixed(1) + 'km away';
    }}
    function scoreCls(s) {{ return s >= 70 ? 'score-high' : s >= 50 ? 'score-mid' : 'score-low'; }}

    let _searchTimeout = null;
    function onSearchInput() {{
      const query = document.getElementById('search-input').value.trim();
      // Instant local filter
      applyFilters();
      // Clear previous search results if query is empty
      if (!query) {{
        document.getElementById('search-results').style.display = 'none';
        return;
      }}
      // Debounce: if user stops typing for 600ms with few visible results, auto-search
      clearTimeout(_searchTimeout);
      _searchTimeout = setTimeout(() => {{
        const visible = document.querySelectorAll('.evt-card:not([style*="display: none"])').length;
        if (visible < 3 && query.length >= 3) doSearch();
      }}, 800);
    }}

    function doSearch() {{
      const query = document.getElementById('search-input').value.trim();
      if (!query) return;
      const spinner = document.getElementById('search-spinner');
      const container = document.getElementById('search-results');
      spinner.style.display = 'inline';
      fetch('/api/search', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{query}})
      }}).then(r => r.json()).then(d => {{
        spinner.style.display = 'none';
        if (!d.ok || !d.results || !d.results.length) {{
          container.innerHTML = '<p style="color:#888;font-size:13px;">Nothing found for that query.</p>';
          container.style.display = 'block';
          return;
        }}
        const webCount = d.web_count || 0;
        let html = '';
        if (webCount > 0) {{
          html += `<div style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:10px;">Also found on the web</div>`;
        }}
        d.results.filter(r => r.source === 'web').forEach(r => {{
          let timeStr = '';
          if (r.start_time) {{
            try {{ const dt = new Date(r.start_time); timeStr = dt.toLocaleDateString('en-US', {{weekday:'short', month:'short', day:'numeric'}}); }} catch(e) {{}}
          }}
          html += `<div class="evt-card" style="cursor:pointer;" onclick="window.open(&apos;${{(r.url||'#').replace(/'/g, '')}}&apos;,&apos;_blank&apos;)">
            <div class="card-body">
              <div class="card-top">
                <span class="card-title">${{r.title}}</span>
                <span style="font-size:10px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.5px;">Web</span>
              </div>
              <div class="card-meta">${{[timeStr, r.location].filter(Boolean).join(' &middot; ')}}</div>
              ${{r.match_reason ? '<div class="card-reason">' + r.match_reason + '</div>' : ''}}
            </div>
          </div>`;
        }});
        container.innerHTML = html || '';
        container.style.display = html ? 'block' : 'none';
      }}).catch(() => {{
        spinner.style.display = 'none';
      }});
    }}

    function getFilteredEvents() {{
      const query = (document.getElementById('search-input')?.value || '').toLowerCase().trim();
      return EVENTS.filter(e => {{
        if (!e.start) return false; // skip undated
        if (query) {{
          const haystack = (e.title + ' ' + e.location + ' ' + e.description + ' ' + e.match_reason).toLowerCase();
          if (!haystack.includes(query)) return false;
        }}
        return true;
      }});
    }}

    function applyFilters() {{
      buildListView();
      buildTimelineView();
    }}

    // --- RSVP & Attend ---
    function markAttend(eventId, runId, title, btn) {{
      if (btn.classList.contains('done')) return;
      const escEvt = eventId.replace(/'/g, "\\'");
      fetch('/api/attend', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{event_id: eventId, run_id: runId, title: title}})
      }}).then(r => r.json()).then(d => {{
        if (d.ok) {{
          btn.innerHTML = '✓ Went! <span style="display:inline-flex;gap:2px;margin-left:6px;">' +
            [1,2,3,4,5].map(n => `<span onclick="rateEvent('${{escEvt}}',${{n}},this.parentElement.parentElement)" style="cursor:pointer;font-size:16px;color:#d1d5db;" onmouseover="this.parentElement.querySelectorAll('span').forEach((s,i)=>s.style.color=i<${{n}}?'#f59e0b':'#d1d5db')" onmouseout="this.parentElement.querySelectorAll('span').forEach(s=>s.style.color='#d1d5db')">★</span>`).join('') +
            '</span>';
        }}
      }});
    }}
    function rateEvent(eventId, rating, container) {{
      fetch('/api/attend/rate', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{event_id: eventId, rating: rating}})
      }}).then(r => r.json()).then(d => {{
        if (d.ok && container) {{
          container.innerHTML = '✓ Rated ' + '★'.repeat(rating) + '☆'.repeat(5-rating);
          container.style.fontSize = '13px';
          container.style.color = '#f59e0b';
        }}
      }});
    }}
    function setRsvp(eventId, runId, status, btn) {{
      fetch('/api/rsvp', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{event_id: eventId, run_id: runId, status: status, user_token: USER_TOKEN}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) {{
          // Handle both .rsvp-btn and .rsvp-btn-lg button classes
          const btns = btn.parentElement.querySelectorAll('.rsvp-btn, .rsvp-btn-lg');
          btns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          // Quick scale feedback
          btn.style.transform = 'scale(1.05)';
          setTimeout(() => btn.style.transform = '', 200);
          // Update EVENTS so card re-renders with indicator
          const ev = EVENTS.find(x => x.id === eventId);
          if (ev) {{ ev.my_rsvp = status; applyFilters(); }}
        }}
      }});
    }}

    // --- Modal (removed — events link directly now) ---
    // --- View toggle ---
    function switchView(view) {{
      localStorage.setItem('recom-view', view);
      document.getElementById('list-view').style.display = view === 'list' ? 'block' : 'none';
      document.getElementById('timeline-view').style.display = view === 'timeline' ? 'block' : 'none';
      document.getElementById('cal-view').style.display = 'none';
      document.getElementById('heat-view').style.display = 'none';
      ['list','timeline'].forEach(v => {{
        const btn = document.getElementById('btn-' + v);
        if (btn) btn.classList.toggle('active', v === view);
      }});
      if (view === 'timeline') buildTimelineView();
    }}

    const _expandedDays = new Set();
    function buildListView() {{
      const container = document.getElementById('list-view');
      const filtered = getFilteredEvents();
      const sorted = [...filtered].sort((a, b) => a.start.localeCompare(b.start));
      const groups = {{}};
      sorted.forEach(e => {{
        const day = e.start.slice(0, 10);
        if (!groups[day]) groups[day] = [];
        groups[day].push(e);
      }});
      const today = new Date(); today.setHours(0,0,0,0);
      const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate() + 1);
      const todayStr = today.toISOString().slice(0, 10);
      let html = '';
      Object.keys(groups).sort().filter(day => day >= todayStr).forEach(day => {{
        const d = new Date(day + 'T00:00:00');
        let label = d.toLocaleDateString('en-US', {{weekday:'long', month:'long', day:'numeric'}});
        if (d.getTime() === today.getTime()) label = 'Today, ' + d.toLocaleDateString('en-US', {{month:'long', day:'numeric'}});
        else if (d.getTime() === tomorrow.getTime()) label = 'Tomorrow, ' + d.toLocaleDateString('en-US', {{month:'long', day:'numeric'}});
        // Diversify: pick top 5 with max 2 per vibe to avoid all-jazz etc
        const allDay = [...groups[day]].sort((a,b) => b.score - a.score);
        const dayEvts = [];
        const vibeCounts = {{}};
        const rest = [];
        for (const e of allDay) {{
          const v = e.vibe || 'mixed';
          if ((vibeCounts[v] || 0) < 2 && dayEvts.length < 5) {{
            dayEvts.push(e);
            vibeCounts[v] = (vibeCounts[v] || 0) + 1;
          }} else {{
            rest.push(e);
          }}
        }}
        // Fill remaining slots if <5 picked
        for (const e of rest) {{
          if (dayEvts.length >= 5) break;
          dayEvts.push(e);
        }}
        // Append overflow for "show more"
        const fullDay = [...dayEvts, ...rest.filter(e => !dayEvts.includes(e))];
        const MAX_SHOW = 5;
        const isExpanded = _expandedDays.has(day);
        const shown = isExpanded ? fullDay : dayEvts.slice(0, MAX_SHOW);
        const hidden = fullDay.length - MAX_SHOW;
        html += `<div class="day-group"><div class="day-header">
          <span>${{label}}</span>
          <span class="day-count">${{fullDay.length}}</span>
        </div>`;
        shown.forEach(e => {{ html += renderCard(e); }});
        if (hidden > 0 && !isExpanded) {{
          html += `<button class="see-more-btn" onclick="_expandedDays.add(&apos;${{day}}&apos;);buildListView()">+ ${{hidden}} more</button>`;
        }} else if (hidden > 0 && isExpanded) {{
          html += `<button class="see-more-btn" onclick="_expandedDays.delete(&apos;${{day}}&apos;);buildListView()">Show less</button>`;
        }}
        html += '</div>';
      }});
      container.innerHTML = html || '<p style="color:#888">No events to display.</p>';
    }}

    function renderCard(e) {{
      let timeStr = '';
      if (e.start) {{
        try {{
          const d = new Date(e.start);
          if (d.getHours() !== 0 || d.getMinutes() !== 0)
            timeStr = d.toLocaleTimeString('en-US', {{hour:'numeric', minute:'2-digit'}});
        }} catch(x) {{}}
      }}
      const eid = e.id.replace(/'/g, "\\\\'");
      let rsvpBtns = '';
      if (HAS_USER) {{
        const goingCls = e.my_rsvp === 'going' ? ' active' : '';
        const maybeCls = e.my_rsvp === 'maybe' ? ' active' : '';
        rsvpBtns = `<div class="card-actions" onclick="event.stopPropagation()">
          <button class="rsvp-btn going${{goingCls}}" onclick="setRsvp(&apos;${{eid}}&apos;, ${{RUN_ID}}, &apos;going&apos;, this)">Going</button>
          <button class="rsvp-btn maybe${{maybeCls}}" onclick="setRsvp(&apos;${{eid}}&apos;, ${{RUN_ID}}, &apos;maybe&apos;, this)">Maybe</button>
        </div>`;
      }}
      const meta = [timeStr, e.location, e.price].filter(Boolean).join(' &middot; ');
      return `<div class="evt-card vibe-${{e.vibe}}${{e.my_rsvp === 'going' ? ' rsvp-going-card' : ''}}" onclick="if(event.target.tagName!=='BUTTON')window.open(&apos;${{e.url || '#'}}&apos;, &apos;_blank&apos;)">
        <div class="card-body">
          <div class="card-top">
            <span class="card-title">${{e.title}}</span>
            <span class="card-score ${{scoreCls(e.score)}}">${{e.score}}</span>
          </div>
          <div class="card-meta">${{meta}}</div>
          ${{e.match_reason ? '<div class="card-reason">' + e.match_reason + '</div>' : ''}}
          ${{rsvpBtns}}
        </div>
      </div>`;
    }}

    function setRsvp(eventId, runId, status, btn) {{
      fetch('/api/rsvp', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{event_id: eventId, run_id: runId, status: status, user_token: USER_TOKEN}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) {{
          const btns = btn.parentElement.querySelectorAll('.rsvp-btn');
          btns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          const ev = EVENTS.find(x => x.id === eventId);
          if (ev) {{ ev.my_rsvp = status; applyFilters(); }}
        }}
      }});
    }}

    // --- Timeline (week columns) view ---
    function buildTimelineView() {{
      const container = document.getElementById('tl-week');
      const filtered = getFilteredEvents();
      const today = new Date(); today.setHours(0,0,0,0);
      const groups = {{}};
      filtered.forEach(e => {{
        const day = e.start.slice(0, 10);
        if (!groups[day]) groups[day] = [];
        groups[day].push(e);
      }});
      let html = '';
      for (let i = 0; i < 7; i++) {{
        const d = new Date(today); d.setDate(today.getDate() + i);
        const key = d.toISOString().slice(0, 10);
        const dayEvts = (groups[key] || []).sort((a, b) => b.score - a.score);
        const isToday = i === 0;
        const dayName = isToday ? 'Today' : d.toLocaleDateString('en-US', {{weekday: 'short'}});
        const dateFmt = d.toLocaleDateString('en-US', {{month: 'short', day: 'numeric'}});
        html += `<div class="timeline-col">
          <div class="timeline-col-header ${{isToday ? 'today' : ''}}">
            <div class="col-day">${{dayName}}</div>
            <div class="col-date">${{dateFmt}}</div>
            ${{dayEvts.length ? '<span class="col-count">' + dayEvts.length + '</span>' : ''}}
          </div>`;
        if (!dayEvts.length) {{
          html += '<div class="tl-empty">-</div>';
        }} else {{
          dayEvts.slice(0, 6).forEach(e => {{
            const eid = e.id.replace(/'/g, "\\\\'");
            let t = '';
            try {{ const dt = new Date(e.start); if (dt.getHours()||dt.getMinutes()) t = dt.toLocaleTimeString('en-US',{{hour:'numeric',minute:'2-digit'}}); }} catch(x){{}}
            html += `<div class="tl-card vibe-${{e.vibe}}" onclick="window.open(&apos;${{e.url||'#'}}&apos;,&apos;_blank&apos;)">
              <div class="tl-title">${{e.title}}</div>
              ${{t ? '<div class="tl-time">' + t + '</div>' : ''}}
              ${{e.location ? '<div class="tl-loc">' + e.location + '</div>' : ''}}
              <span class="tl-score ${{scoreCls(e.score)}}">${{e.score}}</span>
            </div>`;
          }});
          if (dayEvts.length > 6) html += `<div class="tl-empty" style="font-size:11px;color:#888;">+${{dayEvts.length - 6}} more</div>`;
        }}
        html += '</div>';
      }}
      container.innerHTML = html;
    }}

    // --- Init ---
    document.addEventListener('DOMContentLoaded', function() {{
      buildListView();
      buildTimelineView();
      const saved = localStorage.getItem('recom-view');
      switchView(saved || 'list');
    }});
    </script>
    """ + LAYOUT_FOOT
    resp = HTMLResponse(page_html)
    return _maybe_set_cookie(request, resp, current_user)


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


@app.post("/api/attend/rate")
async def attend_rate(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    body = await request.json()
    event_id = body.get("event_id", "")
    rating = int(body.get("rating", 0))
    if not event_id or rating not in range(1, 6):
        return JSONResponse({"ok": False})
    db.conn.execute(
        "UPDATE attended SET rating = ? WHERE user_id = ? AND event_id = ?",
        (rating, user_id, event_id),
    )
    db.conn.commit()
    # Adjust Elo taste items based on rating - find matching taste items by event category
    # Get the event's category/vibe from the rankings table
    row = db.conn.execute(
        """SELECT e.category, rk.vibe FROM events e
           LEFT JOIN rankings rk ON rk.event_id = e.event_id AND rk.run_id = e.run_id
           WHERE e.event_id = ? LIMIT 1""",
        (event_id,),
    ).fetchone()
    if row:
        category = (row["category"] or row["vibe"] or "").lower()
        if category and rating >= 4:
            # Boost matching taste items
            db.conn.execute(
                """UPDATE taste_items SET elo_rating = MIN(2000, elo_rating + ?)
                   WHERE user_id = ? AND (LOWER(category) = ? OR LOWER(label) LIKE ?)""",
                (20 * (rating - 3), user_id, category, f"%{category}%"),
            )
            db.conn.commit()
        elif category and rating <= 2:
            # Demote matching taste items
            db.conn.execute(
                """UPDATE taste_items SET elo_rating = MAX(800, elo_rating - 20)
                   WHERE user_id = ? AND (LOWER(category) = ? OR LOWER(label) LIKE ?)""",
                (user_id, category, f"%{category}%"),
            )
            db.conn.commit()
    return JSONResponse({"ok": True})


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
    .box {{ background: white; border-radius: 16px; padding: 32px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}</style></head>
    <body><div class="box"><h2 style="color:#059669">Marked as attended!</h2><p style="color:#6b7280">{title[:60]}</p>
    <a href="/" style="color:#4f46e5;margin-top:12px;display:inline-block">Back to calendar</a></div></body></html>""")


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

    # Notify group-mates and push to GCal when RSVP is "going"
    if data["status"] == "going":
        try:
            settings = Settings()
            rsvper_name = user.get("name") or user.get("email", "")
            # Get event info
            event_row = db.conn.execute(
                "SELECT title, url, start_time, end_time, location_name, location_address, description"
                " FROM events WHERE event_id = ? LIMIT 1",
                (data["event_id"],),
            ).fetchone()
            event_title = event_row["title"] if event_row else data["event_id"]
            event_url = event_row["url"] if event_row else ""
            # Get group-mates
            user_groups = db.get_user_groups(user["id"])
            notified: set[int] = set()
            for g in user_groups:
                members = db.get_group_members(g["id"])
                for m in members:
                    if m["id"] != user["id"] and m["id"] not in notified:
                        notified.add(m["id"])
                        try:
                            send_rsvp_notify(
                                m["email"], m.get("user_token", ""), rsvper_name,
                                event_title, event_url, settings.dashboard_url, settings,
                            )
                        except Exception:
                            logger.exception("Failed to send RSVP notify to %s", m["email"])

            # Push to Google Calendar if configured
            calendar_id = get_or_create_calendar(settings)
            if calendar_id and event_row:
                location = event_row["location_name"] or ""
                if event_row["location_address"]:
                    location = f"{location}, {event_row['location_address']}" if location else event_row["location_address"]
                gcal_push_event(
                    settings=settings, db=db, calendar_id=calendar_id,
                    event_id=data["event_id"], title=event_row["title"],
                    start_time=event_row["start_time"], end_time=event_row["end_time"],
                    location=location,
                    description=event_row["description"] or "",
                    url=event_row["url"] or "",
                    attendee_emails=[user["email"]],
                )
        except Exception:
            logger.exception("Error with RSVP notifications / GCal push")

    return {"ok": True, "status": data["status"]}


@app.get("/api/rsvp/{event_id}", response_class=JSONResponse)
async def api_get_rsvps(event_id: str):
    """Get all RSVPs for an event."""
    db = get_db()
    rsvps = db.get_event_rsvps(event_id)
    return {"rsvps": rsvps}


@app.get("/api/gcal/status", response_class=JSONResponse)
async def api_gcal_status(request: Request):
    """Check if Google Calendar is configured and connected."""
    from pathlib import Path
    settings = Settings()
    token_exists = Path(settings.gcal_token_file).exists()
    return {
        "connected": token_exists and bool(settings.gcal_calendar_id),
        "token_exists": token_exists,
        "calendar_id": settings.gcal_calendar_id or None,
    }


@app.post("/api/gcal/sync", response_class=JSONResponse)
async def api_gcal_sync(request: Request):
    """Sync GCal attendee responses back to local RSVPs."""
    user = _get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    db = get_db()
    settings = Settings()
    run = db.get_user_latest_run(user["id"])
    if not run:
        return {"synced": 0}
    # Get all events with GCal mappings for this run
    event_ids = [r["event_id"] for r in db.conn.execute(
        "SELECT event_id FROM events WHERE run_id = ?", (run["id"],)
    ).fetchall()]
    gcal_mappings = db.get_gcal_events(event_ids)
    total_synced = 0
    for eid in gcal_mappings:
        total_synced += gcal_sync_rsvps(settings, db, eid, run["id"])
    return {"synced": total_synced}


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
    .box {{ background: white; border-radius: 16px; padding: 32px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}</style></head>
    <body><div class="box"><h2 style="color:#4f46e5">RSVP: {status_labels.get(status, status)}</h2>
    <p style="color:#6b7280">{title[:60]}</p>
    <a href="/?u={u}" style="color:#4f46e5;margin-top:12px;display:inline-block">Back to calendar</a></div></body></html>""")


@app.get("/api/ping-group", response_class=HTMLResponse)
async def ping_group(request: Request, event_id: str = "", u: str = "", group_id: int = 0):
    """Send a 'Bring friends?' ping to group members from email link."""
    db = get_db()
    user = db.get_user_by_token(u) if u else None
    if not user:
        return HTMLResponse("<h1>Invalid link</h1><p>Please log in.</p>", status_code=401)
    event = _find_event(db, event_id)
    if not event:
        return HTMLResponse("<h1>Event not found</h1>", status_code=404)

    user_id = user["id"]
    user_name = user.get("name") or user.get("email", "Someone")
    groups = db.get_user_groups(user_id)

    if not groups:
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 80vh; background: #f5f5f5; }}
        .box {{ background: white; border-radius: 16px; padding: 32px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 400px; }}</style></head>
        <body><div class="box"><h2>No groups yet</h2>
        <p style="color:#6b7280;">Create a group to share events with friends.</p>
        <a href="/groups?u={u}" style="display:inline-block;padding:12px 24px;background:#4f46e5;color:white;border-radius:8px;text-decoration:none;font-weight:600;margin-top:12px;">Go to Groups</a>
        </div></body></html>""")

    # If multiple groups and no group_id specified, show picker
    if len(groups) > 1 and group_id == 0:
        title = event.get("title", "Event")[:60]
        links = ""
        for g in groups:
            gid = g["id"]
            gname = g["name"]
            count = g.get("member_count", 0)
            links += f'<a href="/api/ping-group?event_id={event_id}&u={u}&group_id={gid}" style="display:block;padding:14px 20px;margin:8px 0;background:#f1f5f9;border-radius:16px;text-decoration:none;color:#1e293b;font-weight:600;border:1px solid #e2e8f0;">{gname} <span style="color:#9ca3af;font-weight:400;">({count} members)</span></a>'
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 80vh; background: #f5f5f5; }}
        .box {{ background: white; border-radius: 16px; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 400px; }}</style></head>
        <body><div class="box"><h2>Which group?</h2>
        <p style="color:#6b7280;margin-bottom:16px;">Share <strong>{title}</strong> with:</p>
        {links}
        </div></body></html>""")

    # Use single group if only one, or the specified group_id
    target_group = None
    if group_id > 0:
        for g in groups:
            if g["id"] == group_id:
                target_group = g
                break
        if not target_group:
            return HTMLResponse("<h1>Group not found</h1>", status_code=404)
    else:
        target_group = groups[0]

    gid = target_group["id"]

    # Rate limit check
    if not db.can_ping(user_id, event_id, gid):
        # Determine which limit was hit
        from datetime import datetime as _dt
        row = db.conn.execute(
            "SELECT 1 FROM ping_log WHERE event_id = ? AND group_id = ?",
            (event_id, gid),
        ).fetchone()
        if row:
            msg = "This event was already shared with this group."
        else:
            msg = "Daily ping limit reached (max 3 per day). Try again tomorrow."
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 80vh; background: #f5f5f5; }}
        .box {{ background: white; border-radius: 16px; padding: 32px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 400px; }}</style></head>
        <body><div class="box"><h2 style="color:#f59e0b;">Already pinged</h2>
        <p style="color:#6b7280;">{msg}</p>
        <a href="/?u={u}" style="color:#4f46e5;margin-top:12px;display:inline-block;">Back to calendar</a>
        </div></body></html>""")

    # Send ping emails to all group members (skip the sender)
    members = db.get_group_members(gid)
    settings = Settings()
    title = event.get("title", "Event")
    sent_count = 0
    for member in members:
        if member["id"] == user_id:
            continue
        member_token = member.get("user_token", "")
        member_email = member.get("email", "")
        if not member_email:
            continue
        try:
            send_group_ping(
                to_email=member_email,
                to_token=member_token,
                pinger_name=user_name,
                event=event,
                dashboard_url=settings.dashboard_url,
                settings=settings,
            )
            sent_count += 1
        except Exception:
            logger.exception("Failed to send ping to %s", member_email)

    db.log_ping(user_id, event_id, gid)
    group_name = target_group["name"]

    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 80vh; background: #f5f5f5; }}
    .box {{ background: white; border-radius: 16px; padding: 32px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 400px; }}</style></head>
    <body><div class="box"><h2 style="color:#059669;">Pinged {group_name}!</h2>
    <p style="color:#6b7280;">Sent to {sent_count} friend{"s" if sent_count != 1 else ""} about <strong>{title[:60]}</strong></p>
    <a href="/?u={u}" style="color:#4f46e5;margin-top:12px;display:inline-block;">Back to calendar</a>
    </div></body></html>""")


@app.get("/api/steer", response_class=HTMLResponse)
async def steer(request: Request, target_type: str = "", target_value: str = "", action: str = "", u: str = ""):
    """One-click steering from email links. Shows confirmation page."""
    db = get_db()
    token = u or request.cookies.get(COOKIE_NAME, "")
    user = db.get_user_by_token(token) if token else None
    if not user:
        return HTMLResponse("<h1>Link expired</h1><p>Please log in to update preferences.</p>", status_code=401)
    if not target_type or not target_value or not action:
        return HTMLResponse("<h1>Invalid link</h1>", status_code=400)

    action_labels = {"more": "More like this", "less": "Less like this", "block": "Block", "done": "I did this", "pause": "Paused for now"}
    label = action_labels.get(action, action)

    if action == "done":
        # Mark attended
        db.set_steering(user["id"], target_type, target_value, "done")
    elif action in ("more", "less", "block", "pause"):
        expires = None
        if action == "pause":
            from datetime import timedelta
            expires = (datetime.now() + timedelta(days=14)).isoformat()
        db.set_steering(user["id"], target_type, target_value, action, expires)

    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Preference saved</title>
<style>body{{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f8fafc;margin:0}}
.card{{background:white;border-radius:16px;padding:32px;max-width:400px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
h2{{color:#1e293b;margin-bottom:8px}}p{{color:#64748b;}}
a{{color:#4f46e5;font-weight:600;}}</style></head>
<body><div class="card">
<div style="font-size:48px;margin-bottom:16px">{'✓' if action not in ('block',) else '🚫'}</div>
<h2>{label}</h2>
<p><strong>{target_value}</strong> preference saved.</p>
<p style="margin-top:20px"><a href="/">Back to calendar →</a></p>
</div></body></html>""")


@app.get("/api/rate", response_class=HTMLResponse)
async def api_rate_event(request: Request, event_id: str = "", rating: int = 0,
                          u: str = "", no_go: int = 0):
    """One-click event rating from email links. GET-based for email compatibility."""
    db = get_db()
    token = u or request.cookies.get(COOKIE_NAME, "")
    user = db.get_user_by_token(token) if token else None
    if not user:
        return HTMLResponse("<h1>Link expired</h1><p>Please log in to rate events.</p>", status_code=401)
    if not event_id:
        return HTMLResponse("<h1>Invalid link</h1>", status_code=400)

    user_id = user["id"]

    if no_go:
        # User says they didn't actually go — update RSVP to cant, no rating
        db.conn.execute(
            "INSERT OR REPLACE INTO rsvps (user_id, event_id, status, updated_at) VALUES (?, ?, 'cant', ?)",
            (user_id, event_id, datetime.now().isoformat()),
        )
        db.conn.commit()
        message = "Got it! Marked as not attended."
        icon = "👍"
    elif 1 <= rating <= 5:
        # Record attendance + rating
        db.conn.execute(
            """INSERT INTO attended (user_id, event_id, attended_at, rating)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, event_id) DO UPDATE SET rating = excluded.rating, attended_at = excluded.attended_at""",
            (user_id, event_id, datetime.now().isoformat(), rating),
        )
        db.conn.commit()
        # Adjust Elo taste items for matching categories based on rating
        # Get event category/vibe from rankings
        row = db.conn.execute(
            "SELECT vibe, event_type FROM rankings WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
        if row:
            vibe = row["vibe"] or "general"
            elo_adjustment = (rating - 3) * 10  # -20 to +20
            if elo_adjustment != 0:
                db.conn.execute(
                    """UPDATE taste_items
                       SET elo_rating = MAX(1000, MIN(2000, elo_rating + ?))
                       WHERE user_id = ? AND category = ?""",
                    (elo_adjustment, user_id, vibe),
                )
                db.conn.commit()
        stars = "★" * rating + "☆" * (5 - rating)
        message = f"Rated {stars}"
        icon = "🌟" if rating >= 4 else "✓"
    else:
        return HTMLResponse("<h1>Invalid rating</h1>", status_code=400)

    ev_row = db.conn.execute("SELECT title FROM events WHERE event_id = ? LIMIT 1", (event_id,)).fetchone()
    ev_title = ev_row["title"] if ev_row else event_id

    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rating saved</title>
<style>body{{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f8fafc;margin:0}}
.card{{background:white;border-radius:16px;padding:32px;max-width:380px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
h2{{color:#1e293b;margin-bottom:8px}}p{{color:#64748b;}}
a{{color:#4f46e5;font-weight:600;}}</style></head>
<body><div class="card">
<div style="font-size:48px;margin-bottom:16px">{icon}</div>
<h2>{message}</h2>
<p style="font-weight:600;color:#374151;">{ev_title[:60]}</p>
<p style="margin-top:20px"><a href="/?u={u}">Back to calendar →</a></p>
</div></body></html>""")


@app.get("/group/create", response_class=HTMLResponse)
async def group_create_page(request: Request):
    user = _get_current_user(request)
    if not user:
        return HTMLResponse(_layout("Create Group", """
        <h1>Create a Group</h1>
        <div class="card" style="max-width:400px;">
            <p style="color:#6b7280;">Sign in to create a group.</p>
            <div style="margin-top:12px;">
                <a href="/login" style="padding:8px 20px;background:#4f46e5;color:white;border-radius:10px;
                   font-size:14px;text-decoration:none;font-weight:600;">Sign in</a>
                <a href="/join" style="margin-left:12px;font-size:14px;">or join</a>
            </div>
        </div>
        """))

    return HTMLResponse(_layout("Create Group", f"""
    <h1>Create a Group</h1>
    <div class="card" style="max-width:400px;">
        <form action="/group/create" method="post" style="display:flex;gap:8px;align-items:end;">
            <input name="name" placeholder="Group name (optional)"
                   style="flex:1;padding:10px 14px;border:1.5px solid #e2e8f0;border-radius:10px;font-size:14px;font-family:inherit;">
            <button type="submit" class="btn-primary" style="white-space:nowrap;">Create</button>
        </form>
    </div>
    """, user=user))


@app.post("/group/create")
async def group_create_submit(request: Request):
    user = _get_current_user(request)
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    form = await request.form()
    name = form.get("name", "").strip()
    db = get_db()
    group_id = db.create_group(user["id"], display_name=name)
    db.add_group_member(group_id, user["id"])
    return RedirectResponse(f"/group/{group_id}", status_code=303)


@app.post("/group/{group_id:int}/invite")
async def group_invite(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    group = db.get_group_by_id(group_id)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)
    form = await request.form()
    email = form.get("email", "").strip()
    if not email:
        return HTMLResponse("<h1>Email required</h1>", status_code=400)
    settings = Settings()
    group_display = db.get_group_display_name(group)
    inviter_name = user.get("name") or user.get("email", "")
    # Send invite email with invite code link
    try:
        send_invite_email(
            email, "", group_display, inviter_name,
            group_id, settings.dashboard_url, settings,
            invite_code=group.get("invite_code", ""),
        )
    except Exception:
        logger.exception("Failed to send invite email to %s", email)
    return RedirectResponse(f"/group/{group_id}", status_code=303)


@app.get("/group/{group_id:int}/join/{invite_code}", response_class=HTMLResponse)
async def group_join_page(group_id: int, invite_code: str, request: Request):
    """Invite link landing — validates code then renders group page with join form."""
    db = get_db()
    group = db.get_group_by_id(group_id)
    if not group or group.get("invite_code") != invite_code:
        return HTMLResponse("<h1>Invalid invite link</h1>", status_code=404)
    group_name = db.get_group_display_name(group)
    og = {
        "title": f"{group_name} on Calyx",
        "description": "Join the group to coordinate plans together",
    }
    return await group_page(group_id, request, _valid_invite=True, _og_override=og)


@app.get("/group/{group_id:int}", response_class=HTMLResponse)
async def group_page(group_id: int, request: Request, _valid_invite: bool = False, _og_override: dict | None = None):
    db = get_db()
    current_user = _get_current_user(request)
    group = db.get_group_by_id(group_id)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)

    members = db.get_group_members(group["id"])
    is_member = current_user and any(m["id"] == current_user["id"] for m in members)
    group_name = db.get_group_display_name(group)

    # --- Members list ---
    members_html = ""
    for m in members:
        initial = ((m.get("name") or m.get("email") or "?")[0]).upper()
        name = m.get("name") or m.get("email") or ""
        email = m.get("email", "")
        is_me = current_user and m["id"] == current_user["id"]
        ring = "border:2.5px solid #4f46e5;" if is_me else "border:2px solid #e2e8f0;"
        members_html += f'''<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #f3f4f6;">
            <div style="width:40px;height:40px;border-radius:50%;background:#e0e7ff;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:700;color:#4338ca;{ring}flex-shrink:0;">{initial}</div>
            <div style="flex:1;min-width:0;">
                <div style="font-weight:600;font-size:14px;color:#1e293b;">{name}{"  (you)" if is_me else ""}</div>
                <div style="font-size:13px;color:#9ca3af;">{email}</div>
            </div>
        </div>'''

    # --- Upcoming events (user-added + member RSVPs) ---
    from datetime import datetime as dt
    now_str = dt.now().strftime("%Y-%m-%dT%H:%M:%S")

    # User-added group events
    user_events = db.get_group_user_events(group["id"])
    upcoming_user = [e for e in user_events if (e.get("start_time") or "") >= now_str]

    # Pipeline events where members RSVPd
    pipeline_events = db.get_group_events(group["id"])
    event_ids = [e.get("event_id", "") for e in pipeline_events if e.get("event_id")]
    # Also fetch RSVPs for user-added events (keyed as "grp_evt_{id}")
    user_evt_ids = [f"grp_evt_{e['id']}" for e in upcoming_user]
    all_rsvp_ids = event_ids + user_evt_ids
    rsvps_map = db.get_rsvps_for_events(all_rsvp_ids) if all_rsvp_ids else {}
    user_token = current_user.get("user_token", "") if current_user else ""
    # Only show pipeline events someone RSVPd to
    rsvpd_events = []
    for e in pipeline_events:
        eid = e.get("event_id", "")
        rsvp_list = rsvps_map.get(eid, [])
        going_or_maybe = [r for r in rsvp_list if r["status"] in ("going", "maybe")]
        if going_or_maybe and (e.get("start_time") or "") >= now_str:
            e["_rsvps"] = going_or_maybe
            rsvpd_events.append(e)

    # Helper: render RSVP avatar circles + summary text
    def _rsvp_avatars(rsvp_list: list[dict]) -> str:
        going = [r for r in rsvp_list if r["status"] == "going"]
        maybe = [r for r in rsvp_list if r["status"] == "maybe"]
        if not going and not maybe:
            return ""
        avatars = ""
        all_r = (going + maybe)[:5]
        for i, r in enumerate(all_r):
            bg = "#dcfce7" if r["status"] == "going" else "#fef3c7"
            fg = "#166534" if r["status"] == "going" else "#92400e"
            bd = "#86efac" if r["status"] == "going" else "#fde68a"
            initial = ((r.get("user_name") or "?")[0]).upper()
            ml = "-6px" if i > 0 else "0"
            avatars += f'<div title="{r.get("user_name", "")} ({r["status"]})" style="width:22px;height:22px;border-radius:50%;background:{bg};display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:{fg};border:2px solid {bd};margin-left:{ml};position:relative;z-index:{5-i};flex-shrink:0;">{initial}</div>'
        summary = ""
        if going:
            summary += f'<span style="color:#166534;font-weight:600;">{len(going)} going</span>'
        if going and maybe:
            summary += " &middot; "
        if maybe:
            summary += f'<span style="color:#92400e;">{len(maybe)} maybe</span>'
        return f'<div style="display:flex;align-items:center;gap:6px;margin-top:6px;">{avatars}<span style="font-size:11px;color:#6b7280;">{summary}</span></div>'

    # Build unified upcoming list
    upcoming_html = ""
    # User-added events
    for e in upcoming_user[:8]:
        try:
            d = dt.fromisoformat(e["start_time"])
            time_str = d.strftime("%a %b %-d, %-I:%M %p")
        except (ValueError, TypeError):
            time_str = e.get("start_time", "")[:16]
        creator = e.get("creator_name") or ""
        loc = e.get("location") or ""
        url = e.get("url") or ""
        title_link = f'<a href="{url}" target="_blank" style="font-weight:700;font-size:15px;color:#1e293b;text-decoration:none;">{e["title"][:55]}</a>' if url else f'<span style="font-weight:700;font-size:15px;color:#1e293b;">{e["title"][:55]}</span>'
        delete_btn = ""
        is_event_creator = current_user and e.get("created_by") == current_user["id"]
        is_group_creator = current_user and group.get("created_by") == current_user["id"]
        if is_event_creator or is_group_creator:
            delete_btn = f'<form action="/api/group/{group_id}/delete-event" method="post" style="margin:0;" onsubmit="return confirm(&apos;Remove this event?&apos;)"><input type="hidden" name="event_id" value="{e["id"]}"><button type="submit" style="background:none;border:none;color:#9ca3af;cursor:pointer;font-size:12px;padding:4px 8px;">remove</button></form>'
        ue_eid = f"grp_evt_{e['id']}"
        ue_rsvps = rsvps_map.get(ue_eid, [])
        ue_avatars = _rsvp_avatars(ue_rsvps)
        my_ue_rsvp = ""
        if current_user:
            for r in ue_rsvps:
                if r.get("user_id") == current_user["id"]:
                    my_ue_rsvp = r["status"]
        rsvp_btns = ""
        nudge_btn = ""
        if is_member and current_user:
            going_cls = " active" if my_ue_rsvp == "going" else ""
            maybe_cls = " active" if my_ue_rsvp == "maybe" else ""
            rsvp_btns = f'''<div style="display:flex;gap:6px;margin-top:8px;">
                <button onclick="rsvpGroupEvent({group_id}, &apos;{ue_eid}&apos;, &apos;going&apos;, this)" class="grp-rsvp-btn going{going_cls}">Going</button>
                <button onclick="rsvpGroupEvent({group_id}, &apos;{ue_eid}&apos;, &apos;maybe&apos;, this)" class="grp-rsvp-btn maybe{maybe_cls}">Maybe</button>
            </div>'''
            nudge_btn = f'<a href="/api/ping-group?event_id={ue_eid}&amp;u={user_token}" style="font-size:11px;color:#6b7280;text-decoration:none;margin-top:6px;display:inline-block;" onmouseover="this.style.color=&apos;#4f46e5&apos;" onmouseout="this.style.color=&apos;#6b7280&apos;">Nudge group</a>'
        upcoming_html += f'''<div class="card" style="padding:14px 16px;margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;align-items:start;">
                <div style="flex:1;min-width:0;">
                    {title_link}
                    <div style="font-size:13px;color:#6b7280;margin-top:2px;">{time_str}{" · " + loc if loc else ""}</div>
                    <div style="font-size:12px;color:#9ca3af;margin-top:2px;">Added by {creator}</div>
                    {ue_avatars}
                    {rsvp_btns}
                    {nudge_btn}
                </div>
                {delete_btn}
            </div>
        </div>'''

    # RSVPd pipeline events
    for e in rsvpd_events[:8]:
        try:
            d = dt.fromisoformat(e["start_time"])
            time_str = d.strftime("%a %b %-d, %-I:%M %p")
        except (ValueError, TypeError):
            time_str = ""
        title = (e.get("title") or "")[:55]
        url = e.get("url", "#")
        loc = (e.get("location_name") or "")[:35]
        rsvp_list = e.get("_rsvps", [])
        pe_avatars = _rsvp_avatars(rsvp_list)
        pe_nudge = ""
        pe_eid = e.get("event_id", "")
        if is_member and current_user and pe_eid:
            pe_nudge = f'<a href="/api/ping-group?event_id={pe_eid}&amp;u={user_token}" style="font-size:11px;color:#6b7280;text-decoration:none;margin-top:6px;display:inline-block;" onmouseover="this.style.color=&apos;#4f46e5&apos;" onmouseout="this.style.color=&apos;#6b7280&apos;">Nudge group</a>'
        upcoming_html += f'''<div class="card" style="padding:14px 16px;margin-bottom:8px;">
            <div style="flex:1;min-width:0;">
                <a href="{url}" target="_blank" style="font-weight:700;font-size:15px;color:#1e293b;text-decoration:none;">{title}</a>
                <div style="font-size:13px;color:#6b7280;margin-top:2px;">{time_str}{" · " + loc if loc else ""}</div>
                {pe_avatars}
                {pe_nudge}
            </div>
        </div>'''

    if not upcoming_html:
        upcoming_html = '<p style="color:#9ca3af;font-size:14px;">No upcoming events yet.</p>'

    # --- Add event form (members only) ---
    add_event_html = ""
    if is_member:
        from datetime import datetime as _dt
        default_date = _dt.now().strftime("%Y-%m-%d")
        add_event_html = f'''<div style="border:1px solid #e0e0e0;padding:20px;margin-bottom:28px;">
            <h2 style="margin:0 0 12px;">Add Event</h2>
            <form action="/api/group/{group_id}/add-event" method="post">
                <input name="title" placeholder="What are you doing?" required
                       style="width:100%;padding:10px 12px;border:1px solid #ccc;font-size:14px;font-family:inherit;margin-bottom:8px;box-sizing:border-box;">
                <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
                    <input name="date" type="date" value="{default_date}" required
                           style="flex:1;min-width:130px;padding:10px 12px;border:1px solid #ccc;font-size:14px;font-family:inherit;">
                    <input name="time" type="time" value="19:00"
                           style="flex:1;min-width:100px;padding:10px 12px;border:1px solid #ccc;font-size:14px;font-family:inherit;">
                    <input name="location" placeholder="Where? (optional)"
                           style="flex:1;min-width:130px;padding:10px 12px;border:1px solid #ccc;font-size:14px;font-family:inherit;">
                </div>
                <button type="submit" class="btn-primary" style="width:100%;">Add Event</button>
            </form>
        </div>'''

    # --- Invite + Calendar subscribe (members only) ---
    actions_html = ""
    if is_member:
        settings = Settings()
        feed_url = f"{settings.dashboard_url}/group/{group_id}/feed.ics"
        webcal_url = feed_url.replace("https://", "webcal://").replace("http://", "webcal://")
        gcal_url = f"https://calendar.google.com/calendar/r?cid={feed_url.replace('https://', 'http://')}"
        invite_code = group.get("invite_code", "")
        group_link = f"{settings.dashboard_url}/group/{group_id}/join/{invite_code}"

        actions_html = f'''<div style="border-top:1px solid #e0e0e0;padding-top:24px;margin-top:28px;">
            <div style="display:flex;gap:8px;margin-bottom:16px;">
                <button onclick="navigator.clipboard.writeText(&apos;{group_link}&apos;);this.textContent=&apos;Copied!&apos;;setTimeout(()=>this.textContent=&apos;Copy invite link&apos;,1500)"
                        class="btn-primary" style="flex:1;text-align:center;">Copy invite link</button>
                <button onclick="navigator.share({{title:&apos;Join {group_name} on Calyx&apos;,text:&apos;Join our group to coordinate plans&apos;,url:&apos;{group_link}&apos;}}).catch(()=>{{}})"
                        class="btn-secondary" style="flex:1;text-align:center;">Share</button>
            </div>
            <div style="display:flex;gap:8px;font-size:12px;">
                <a href="{webcal_url}" style="color:#888;">Add to Apple Calendar</a>
                <span style="color:#ddd;">|</span>
                <a href="{gcal_url}" target="_blank" style="color:#888;">Google Calendar</a>
                <span style="color:#ddd;">|</span>
                <a href="#" onclick="navigator.clipboard.writeText(&apos;{feed_url}&apos;);this.textContent=&apos;Copied&apos;;return false;" style="color:#888;">Copy iCal URL</a>
            </div>
        </div>'''

    # --- Join CTA for non-members (only if they arrived via valid invite link) ---
    invite_code = group.get("invite_code", "")
    join_cta = ""
    if _valid_invite and not is_member:
        if not current_user:
            join_cta = f'''<div class="card" style="margin-bottom:20px;">
                <form action="/api/join-group/{group_id}/{invite_code}" method="post" style="display:flex;flex-direction:column;gap:8px;">
                    <input name="name" placeholder="Your name" required
                           style="padding:10px 14px;border:1.5px solid #e2e8f0;border-radius:10px;font-size:14px;font-family:inherit;">
                    <input name="email" type="email" placeholder="you@gmail.com" required
                           style="padding:10px 14px;border:1.5px solid #e2e8f0;border-radius:10px;font-size:14px;font-family:inherit;">
                    <button type="submit" class="btn-primary">Join this group</button>
                </form>
            </div>'''
        else:
            join_cta = f'''<div class="card" style="margin-bottom:20px;">
                <form action="/group/{group_id}/join" method="post" style="display:flex;align-items:center;justify-content:space-between;">
                    <span style="font-size:14px;color:#374151;">Join this group to add events and see plans.</span>
                    <button type="submit" class="btn-primary" style="white-space:nowrap;">Join</button>
                </form>
            </div>'''

    # --- Editable group name (inline) ---
    name_html = f'<h1>{group_name}</h1>'
    if is_member:
        name_html = f'''<div style="display:flex;align-items:center;gap:8px;margin-bottom:20px;">
            <h1 style="margin:0;" id="groupName">{group_name}</h1>
            <button onclick="editGroupName()" style="background:none;border:none;cursor:pointer;color:#9ca3af;font-size:13px;padding:4px 8px;">edit</button>
        </div>
        <script>
        function editGroupName() {{
            const h1 = document.getElementById('groupName');
            const current = h1.textContent;
            const input = document.createElement('input');
            input.value = current;
            input.style.cssText = 'font-size:1.6rem;font-weight:800;border:1.5px solid #e2e8f0;border-radius:8px;padding:4px 8px;font-family:inherit;width:100%;';
            h1.replaceWith(input);
            input.focus();
            input.select();
            const save = () => {{
                const val = input.value.trim();
                if (val && val !== current) {{
                    fetch('/api/group/{group_id}/rename', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{name: val}})
                    }});
                }}
                const newH1 = document.createElement('h1');
                newH1.style.margin = '0';
                newH1.id = 'groupName';
                newH1.textContent = val || current;
                input.replaceWith(newH1);
            }};
            input.addEventListener('blur', save);
            input.addEventListener('keydown', e => {{ if (e.key === 'Enter') save(); }});
        }}
        </script>'''

    # --- Leave / Delete group button ---
    leave_html = ""
    if is_member and current_user and group.get("created_by") == current_user["id"]:
        leave_html = f'''<div style="text-align:center;margin-top:32px;margin-bottom:16px;">
            <form action="/api/group/{group_id}/delete" method="post" style="margin:0;">
                <button type="submit" onclick="return confirm(&apos;Delete this group and all its events? This cannot be undone.&apos;)"
                        style="background:none;border:none;color:#dc2626;cursor:pointer;font-size:13px;padding:8px 16px;font-family:inherit;">Delete group</button>
            </form>
        </div>'''
    elif is_member and current_user and group.get("created_by") != current_user["id"]:
        leave_html = f'''<div style="text-align:center;margin-top:32px;margin-bottom:16px;">
            <form action="/api/group/{group_id}/leave" method="post" style="margin:0;">
                <button type="submit" onclick="return confirm(&apos;Leave this group?&apos;)"
                        style="background:none;border:none;color:#dc2626;cursor:pointer;font-size:13px;padding:8px 16px;font-family:inherit;">Leave group</button>
            </form>
        </div>'''

    # Group RSVP button CSS + JS
    group_rsvp_extras = f"""
    <style>
    .grp-rsvp-btn {{ font-size:11px; padding:4px 14px; border:1px solid #ccc; background:white; cursor:pointer; color:#888; font-weight:700; transition:all .15s; font-family:inherit; text-transform:uppercase; letter-spacing:.3px; }}
    .grp-rsvp-btn:hover, .grp-rsvp-btn.active {{ color:#000; border-color:#000; }}
    .grp-rsvp-btn.going:hover, .grp-rsvp-btn.going.active {{ background:#000; color:#fff; border-color:#000; }}
    .grp-rsvp-btn.maybe:hover, .grp-rsvp-btn.maybe.active {{ background:#f5f5f5; color:#000; border-color:#000; }}
    </style>
    <script>
    async function rsvpGroupEvent(groupId, eventId, status, btn) {{
        const container = btn.parentElement;
        const buttons = container.querySelectorAll('.grp-rsvp-btn');
        const wasActive = btn.classList.contains('active');
        buttons.forEach(b => b.classList.remove('active'));
        const newStatus = wasActive ? '' : status;
        if (!wasActive) btn.classList.add('active');
        try {{
            await fetch('/api/group/' + groupId + '/rsvp', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{event_id: eventId, status: newStatus, user_token: '{user_token}'}})
            }});
            if (!wasActive) setTimeout(() => location.reload(), 300);
            else setTimeout(() => location.reload(), 300);
        }} catch(e) {{ console.error(e); }}
    }}
    </script>
    """

    member_count = len(members)
    og = _og_override or {
        "title": group_name,
        "description": f"{member_count} members \u00b7 Upcoming events",
    }

    resp = HTMLResponse(_layout(group_name, f"""
    <style>
    .group-page {{max-width:620px;margin:0 auto}}
    .group-page h1 {{font-size:2rem;font-weight:800;letter-spacing:-.5px;margin-bottom:4px}}
    .member-row {{display:inline-flex;align-items:center;margin-right:-6px}}
    .member-avatar {{width:32px;height:32px;border-radius:50%;background:#f0f0f0;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#555;border:2px solid #fff;margin-left:-8px;position:relative}}
    .member-avatar.me {{border-color:#000}}
    .member-names {{font-size:13px;color:#888;margin-left:8px}}
    </style>
    <div class="group-page">
    {name_html}

    <div style="display:flex;align-items:center;margin-bottom:32px;">
        <div class="member-row">
            {"".join(f'<div class="member-avatar{" me" if current_user and m["id"] == current_user["id"] else ""}" title="{m.get("name") or m.get("email","")}">{((m.get("name") or m.get("email") or "?")[0]).upper()}</div>' for m in members)}
        </div>
        <span class="member-names">{", ".join((m.get("name") or m.get("email","").split("@")[0]) for m in members[:4])}{f" +{len(members)-4}" if len(members) > 4 else ""}</span>
    </div>

    {join_cta}

    {f'<div style="border-bottom:1px solid #e0e0e0;padding-bottom:28px;margin-bottom:28px;">{upcoming_html}</div>' if upcoming_html and "No upcoming" not in upcoming_html else '<p style="color:#888;font-size:14px;margin-bottom:28px;">No upcoming events yet.</p>'}

    {add_event_html}
    {actions_html}
    {leave_html}
    </div>
    {group_rsvp_extras}
    """, user=current_user, og=og))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/group/{group_id:int}/feed.ics")
async def group_ical_feed(group_id: int, min_score: int = 40):
    """Group iCal feed with RSVP info in descriptions."""
    db = get_db()
    group = db.get_group_by_id(group_id)
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

    from datetime import timezone as _tz
    utcnow = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//recom//Group {_ical_escape(group['name'])}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Calyx - {_ical_escape(group['name'])}",
        "X-APPLE-CALENDAR-COLOR:#f59e0b",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
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
        vibe = e.get("vibe", "mixed")
        lat, lon = e.get("lat"), e.get("lon")

        # Add RSVP info to description
        rsvp_lines = []
        event_rsvps = rsvps_map.get(eid, [])
        for rv in event_rsvps:
            rv_label = {"going": "Going", "maybe": "Maybe", "cant": "Can't go"}.get(rv["status"], rv["status"])
            rsvp_lines.append(f"{rv['user_name']}: {rv_label}")
        rsvp_text = "\\n".join(rsvp_lines) if rsvp_lines else ""
        desc_parts = [f"Score: {score}/100", reason]
        if rsvp_text:
            desc_parts.append(f"\\nRSVPs:\\n{rsvp_text}")

        # Determine if anyone is "going" for TRANSP
        has_going = any(rv["status"] == "going" for rv in event_rsvps)

        uid = f"{eid}@recom-group-{group_id}"
        vevent_lines = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{utcnow}",
            f"DTSTART:{dtstart}",
            f"SUMMARY:[{score}] {title}",
            f"LOCATION:{location}",
            f"URL:{url}",
            f"DESCRIPTION:{_ical_escape('\\n'.join(desc_parts))}",
            f"CATEGORIES:{vibe}",
            f"TRANSP:{'OPAQUE' if has_going else 'TRANSPARENT'}",
            "DURATION:PT2H",
        ]
        if lat and lon:
            vevent_lines.append(f"GEO:{lat};{lon}")
        # ATTENDEE lines for group member RSVPs
        partstat_map = {"going": "ACCEPTED", "maybe": "TENTATIVE", "cant": "DECLINED"}
        for rv in event_rsvps:
            ps = partstat_map.get(rv["status"])
            if ps:
                cn = _ical_escape(rv.get("user_name", ""))
                email = rv.get("user_email", "")
                if email:
                    vevent_lines.append(f"ATTENDEE;PARTSTAT={ps};CN={cn}:mailto:{email}")
        # Reminder alarm
        vevent_lines.extend([
            "BEGIN:VALARM",
            "TRIGGER:-PT2H",
            "ACTION:DISPLAY",
            f"DESCRIPTION:Reminder: {title}",
            "END:VALARM",
        ])
        vevent_lines.append("END:VEVENT")
        lines.extend(vevent_lines)

    # Include user-added group events
    user_events = db.get_group_user_events(group["id"])
    for ue in user_events:
        start = ue.get("start_time")
        if not start:
            continue
        try:
            dt_obj = datetime.fromisoformat(start)
        except (ValueError, TypeError):
            continue
        dtstart = dt_obj.strftime("%Y%m%dT%H%M%S")
        ue_title = _ical_escape(ue.get("title") or "")
        ue_location = _ical_escape(ue.get("location") or "")
        ue_url = ue.get("url") or ""
        creator = ue.get("creator_name") or ""
        notes = ue.get("notes") or ""
        desc_parts = [f"Added by {creator}"]
        if notes:
            desc_parts.append(notes)
        end = ue.get("end_time")
        ue_lines = [
            "BEGIN:VEVENT",
            f"UID:group-event-{ue['id']}@recom",
            f"DTSTAMP:{utcnow}",
            f"DTSTART:{dtstart}",
        ]
        if end:
            try:
                ue_lines.append(f"DTEND:{datetime.fromisoformat(end).strftime('%Y%m%dT%H%M%S')}")
            except (ValueError, TypeError):
                ue_lines.append("DURATION:PT2H")
        else:
            ue_lines.append("DURATION:PT2H")
        ue_lines.extend([
            f"SUMMARY:{ue_title}",
            f"LOCATION:{ue_location}",
            f"URL:{ue_url}",
            f"DESCRIPTION:{_ical_escape(chr(10).join(desc_parts))}",
            "END:VEVENT",
        ])
        lines.extend(ue_lines)

    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar",
        headers={"Content-Disposition": f"inline; filename=recom-group-{group_id}.ics"},
    )


@app.get("/group/{group_id:int}/plan", response_class=HTMLResponse)
async def group_planner(group_id: int, request: Request):
    """When2Meet-style group event planner — no account required."""
    db = get_db()
    current_user = _get_current_user(request)
    group = db.get_group_by_id(group_id)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)

    settings = Settings()
    share_url = f"{settings.dashboard_url}/group/{group_id}/plan"

    body = f"""
    <style>
      .planner-name-bar {{ background:#eef2ff;border:1.5px solid #c7d2fe;border-radius:12px;padding:12px 16px;margin-bottom:16px;display:flex;align-items:center;gap:10px; }}
      .planner-name-bar input {{ flex:1;padding:8px 12px;border:1.5px solid #d1d5db;border-radius:8px;font-size:14px;font-family:inherit; }}
      .planner-name-bar button {{ padding:8px 18px;background:#4f46e5;color:white;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer; }}
      .planner-name-bar .name-set {{ font-weight:700;font-size:15px;color:#1e1b4b; }}
      .grid-wrap {{ overflow-x:auto;-webkit-overflow-scrolling:touch;margin-bottom:16px; }}
      .plan-grid {{ border-collapse:collapse;min-width:100%; }}
      .plan-grid th, .plan-grid td {{ padding:6px 10px;text-align:center;font-size:13px;white-space:nowrap; }}
      .plan-grid thead th {{ position:sticky;top:0;background:#f8fafc;z-index:2;border-bottom:2px solid #e2e8f0;font-weight:600;color:#4f46e5; }}
      .plan-grid .event-cell {{ text-align:left;position:sticky;left:0;background:#f8fafc;z-index:3;min-width:180px;max-width:260px;white-space:normal; }}
      .plan-grid thead .event-cell {{ z-index:4; }}
      .plan-grid .day-row td {{ background:#f1f5f9;font-weight:700;color:#334155;font-size:12px;text-transform:uppercase;letter-spacing:.5px;padding:8px 10px; }}
      .rsvp-cell {{ cursor:pointer;width:48px;height:40px;border-radius:8px;transition:all .12s;user-select:none; }}
      .rsvp-cell:hover {{ transform:scale(1.15);box-shadow:0 2px 8px rgba(0,0,0,.12); }}
      .rsvp-cell[data-status="going"] {{ background:#22c55e;color:white; }}
      .rsvp-cell[data-status="maybe"] {{ background:#eab308;color:white; }}
      .rsvp-cell[data-status="cant"] {{ background:#ef4444;color:white; }}
      .rsvp-cell[data-status=""] {{ background:#f1f5f9;color:#cbd5e1; }}
      .event-title {{ font-weight:600;font-size:13px;color:#1e1b4b; }}
      .event-meta {{ font-size:11px;color:#6b7280; }}
      .share-box {{ background:#f0fdf4;border:1.5px solid #86efac;border-radius:12px;padding:12px 16px;display:flex;align-items:center;gap:10px; }}
      .share-box input {{ flex:1;padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:13px;font-family:monospace;background:white; }}
      .share-box button {{ padding:6px 14px;background:#16a34a;color:white;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer; }}
      .tally {{ font-size:11px;color:#6b7280;margin-left:4px; }}
    </style>

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
      <h1 style="margin:0;">{group["name"]} &mdash; Plan</h1>
      <a href="/group/{group_id}" style="font-size:13px;color:#4f46e5;">Back to group</a>
    </div>

    <div id="nameBar" class="planner-name-bar" style="display:none;">
      <span>Your name:</span>
      <input id="nameInput" placeholder="Type your name..." autocomplete="off">
      <button onclick="setName()">Join</button>
    </div>
    <div id="nameDisplay" class="planner-name-bar" style="display:none;">
      <span class="name-set" id="nameLabel"></span>
      <button onclick="changeName()" style="background:transparent;color:#4f46e5;font-size:13px;padding:4px 10px;">Change</button>
    </div>

    <div class="grid-wrap">
      <table class="plan-grid" id="planGrid">
        <thead><tr><th class="event-cell">Event</th></tr></thead>
        <tbody id="gridBody"></tbody>
      </table>
    </div>

    <div class="share-box" style="margin-bottom:16px;">
      <span style="font-weight:600;font-size:13px;">Share this link:</span>
      <input id="shareUrl" value="{share_url}" readonly onclick="this.select()">
      <button onclick="navigator.clipboard.writeText(document.getElementById(&apos;shareUrl&apos;).value);this.textContent=&apos;Copied!&apos;;setTimeout(()=>this.textContent=&apos;Copy&apos;,1500)">Copy</button>
    </div>

    <div style="font-size:12px;color:#9ca3af;margin-bottom:8px;">
      Click cells to cycle: <span style="color:#22c55e;font-weight:700;">Going</span> &rarr;
      <span style="color:#eab308;font-weight:700;">Maybe</span> &rarr;
      <span style="color:#ef4444;font-weight:700;">Can&apos;t</span> &rarr; Empty
    </div>

    <script>
    const SLUG = '{group_id}';
    const USER_TOKEN = '{current_user.get("user_token", "") if current_user else ""}';
    const USER_NAME = '{(current_user.get("name") or "").replace(chr(39), "&apos;")}' || '';
    const CYCLE = ['', 'going', 'maybe', 'cant'];
    const ICONS = {{'going': '&#10003;', 'maybe': '?', 'cant': '&#10007;', '': '&middot;'}};
    let myName = '';
    let gridData = null;

    function loadName() {{
      if (USER_NAME) {{
        myName = USER_NAME;
      }} else {{
        myName = localStorage.getItem('recom_guest_' + SLUG) || '';
      }}
      updateNameUI();
    }}

    function updateNameUI() {{
      if (myName) {{
        document.getElementById('nameBar').style.display = 'none';
        document.getElementById('nameDisplay').style.display = 'flex';
        document.getElementById('nameLabel').textContent = myName;
      }} else {{
        document.getElementById('nameBar').style.display = 'flex';
        document.getElementById('nameDisplay').style.display = 'none';
      }}
    }}

    function setName() {{
      const n = document.getElementById('nameInput').value.trim();
      if (!n) return;
      myName = n;
      if (!USER_TOKEN) localStorage.setItem('recom_guest_' + SLUG, n);
      updateNameUI();
      fetchGrid();
    }}

    function changeName() {{
      if (USER_TOKEN) return;
      myName = '';
      localStorage.removeItem('recom_guest_' + SLUG);
      updateNameUI();
    }}

    async function fetchGrid() {{
      const resp = await fetch('/api/group/' + SLUG + '/grid');
      gridData = await resp.json();
      renderGrid();
    }}

    function renderGrid() {{
      if (!gridData) return;
      const people = gridData.people;
      const events = gridData.events;
      const rsvps = gridData.rsvps;

      // Header row
      const thead = document.querySelector('#planGrid thead tr');
      thead.innerHTML = '<th class="event-cell">Event</th>';
      people.forEach(p => {{
        const initials = p.name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
        const typeIcon = p.type === 'member' ? '' : '<span style="font-size:9px;color:#9ca3af;"> guest</span>';
        const isMe = p.name === myName;
        thead.innerHTML += '<th style="' + (isMe ? 'background:#eef2ff;' : '') + '">' + initials + typeIcon + '<br><span style="font-size:10px;font-weight:400;color:#6b7280;">' + p.name.split(' ')[0] + '</span></th>';
      }});
      // Tally column
      thead.innerHTML += '<th class="tally">Total</th>';

      // Group events by day
      const tbody = document.getElementById('gridBody');
      tbody.innerHTML = '';
      let currentDay = '';
      events.forEach(ev => {{
        if (ev.day_label !== currentDay) {{
          currentDay = ev.day_label;
          const dayRow = document.createElement('tr');
          dayRow.className = 'day-row';
          dayRow.innerHTML = '<td class="event-cell" colspan="' + (people.length + 2) + '">' + currentDay + '</td>';
          tbody.appendChild(dayRow);
        }}
        const row = document.createElement('tr');
        row.innerHTML = '<td class="event-cell"><div class="event-title">' + ev.title + '</div><div class="event-meta">' + ev.time + (ev.location ? ' &middot; ' + ev.location : '') + '</div></td>';

        let goingCount = 0;
        people.forEach(p => {{
          const status = (rsvps[ev.event_id] && rsvps[ev.event_id][p.name]) || '';
          if (status === 'going') goingCount++;
          const cell = document.createElement('td');
          cell.className = 'rsvp-cell';
          cell.dataset.status = status;
          cell.dataset.eid = ev.event_id;
          cell.dataset.person = p.name;
          cell.innerHTML = ICONS[status] || ICONS[''];
          if (p.name === myName) {{
            cell.style.cursor = 'pointer';
            cell.onclick = () => cycleRsvp(cell);
          }} else {{
            cell.style.cursor = 'default';
            cell.style.opacity = status ? '1' : '0.4';
          }}
          row.appendChild(cell);
        }});
        // Tally
        const tally = document.createElement('td');
        tally.className = 'tally';
        tally.textContent = goingCount || '';
        row.appendChild(tally);

        tbody.appendChild(row);
      }});
    }}

    async function cycleRsvp(cell) {{
      if (!myName) return;
      const cur = cell.dataset.status || '';
      const idx = CYCLE.indexOf(cur);
      const next = CYCLE[(idx + 1) % CYCLE.length];
      cell.dataset.status = next;
      cell.innerHTML = ICONS[next] || ICONS[''];

      const body = {{event_id: cell.dataset.eid, status: next}};
      if (USER_TOKEN) {{
        body.user_token = USER_TOKEN;
      }} else {{
        body.guest_name = myName;
      }}
      await fetch('/api/group/' + SLUG + '/rsvp', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body)
      }});
      // Re-fetch to update tallies
      fetchGrid();
    }}

    // Init
    loadName();
    fetchGrid();
    // Poll every 30s
    setInterval(fetchGrid, 30000);
    </script>
    """

    resp = HTMLResponse(_layout(f"{group['name']} Plan", body, user=current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.post("/api/group/{group_id:int}/add-event")
async def api_group_add_event(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    group = db.get_group_by_id(group_id)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)
    if not db.is_group_member(group_id, user["id"]):
        return HTMLResponse("<h1>Not a member</h1>", status_code=403)
    form = await request.form()
    title = (form.get("title") or "").strip()
    date = (form.get("date") or "").strip()
    time = (form.get("time") or "19:00").strip()
    location = (form.get("location") or "").strip()
    if not title or not date:
        return HTMLResponse("<h1>Title and date required</h1>", status_code=400)
    start_time = f"{date}T{time}:00"
    db.add_group_event(group_id, user["id"], title, start_time, location=location)
    # Notify other group members
    try:
        members = db.get_group_members(group_id)
        other_emails = [m["email"] for m in members if m["id"] != user["id"] and m.get("email")]
        if other_emails:
            adder_name = user.get("name") or user.get("email", "Someone")
            event_date = f"{date} {time}"
            send_group_event_notification(
                to_emails=other_emails,
                adder_name=adder_name,
                event_title=title,
                event_date=event_date,
                group_name=group["name"],
                group_id=group_id,
                dashboard_url=settings.dashboard_url,
                settings=settings,
            )
    except Exception:
        logger.exception("Failed to send group event notification for group %d", group_id)
    return RedirectResponse(f"/group/{group_id}?success=Event+added", status_code=303)


@app.post("/api/group/{group_id:int}/delete-event")
async def api_group_delete_event(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    form = await request.form()
    event_id = int(form.get("event_id") or 0)
    if event_id:
        db.delete_group_event(event_id, user["id"])
    return RedirectResponse(f"/group/{group_id}?success=Event+removed", status_code=303)


@app.post("/api/group/{group_id:int}/rename", response_class=JSONResponse)
async def api_group_rename(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not db.is_group_member(group_id, user["id"]):
        return JSONResponse({"error": "Not a member"}, status_code=403)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if name:
        db.update_group_display_name(group_id, name)
    return {"ok": True}


@app.post("/api/group/{group_id:int}/leave")
async def api_group_leave(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    group = db.get_group_by_id(group_id)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)
    if group["created_by"] == user["id"]:
        return HTMLResponse("<h1>Group creator cannot leave — delete the group instead</h1>", status_code=403)
    if not db.is_group_member(group_id, user["id"]):
        return HTMLResponse("<h1>Not a member</h1>", status_code=403)
    db.leave_group(group_id, user["id"])
    return RedirectResponse("/groups?success=Left+group", status_code=303)


@app.post("/api/group/{group_id:int}/delete")
async def api_group_delete(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    group = db.get_group_by_id(group_id)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)
    if not db.delete_group(group_id, user["id"]):
        return HTMLResponse("<h1>Only the group creator can delete it</h1>", status_code=403)
    return RedirectResponse("/groups", status_code=303)


@app.post("/api/group/{group_id:int}/rsvp", response_class=JSONResponse)
async def api_group_planner_rsvp(group_id: int, request: Request):
    """Set RSVP from the group planner — works for guests and members."""
    db = get_db()
    group = db.get_group_by_id(group_id)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)

    data = await request.json()
    event_id = data.get("event_id", "")
    status = data.get("status", "")
    user_token = data.get("user_token", "")
    guest_name = data.get("guest_name", "")

    if not event_id:
        return JSONResponse({"error": "event_id required"}, status_code=400)

    if user_token:
        user = db.get_user_by_token(user_token)
        if not user:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        # Find a run_id for this event
        row = db.conn.execute(
            "SELECT run_id FROM events WHERE event_id = ? ORDER BY run_id DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        run_id = row["run_id"] if row else 0
        if status:
            db.set_rsvp(user["id"], event_id, run_id, status)
        else:
            # Remove RSVP
            db.conn.execute(
                "DELETE FROM rsvps WHERE user_id = ? AND event_id = ?",
                (user["id"], event_id),
            )
            db.conn.commit()
    elif guest_name:
        db.set_guest_rsvp(group["id"], event_id, guest_name.strip(), status)
    else:
        return JSONResponse({"error": "user_token or guest_name required"}, status_code=400)

    return {"ok": True}


@app.get("/api/group/{group_id:int}/grid", response_class=JSONResponse)
async def api_group_planner_grid(group_id: int):
    """Return JSON grid data for the group planner."""
    db = get_db()
    group = db.get_group_by_id(group_id)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)

    gid = group["id"]
    events = db.get_group_events(gid)
    members = db.get_group_members(gid)
    guests = db.get_group_guests(gid)

    # Build event list grouped by day
    from collections import defaultdict
    from datetime import datetime as dt

    event_ids = [e.get("event_id", "") for e in events if e.get("event_id")]
    member_rsvps = db.get_rsvps_for_events(event_ids)
    guest_rsvps = db.get_group_guest_rsvps(gid, event_ids)

    # People list: members first, then guests (excluding names that match members)
    member_names = {(m.get("name") or m.get("email", "")) for m in members}
    people = [{"name": m.get("name") or m.get("email", ""), "type": "member"} for m in members]
    for g in guests:
        if g not in member_names:
            people.append({"name": g, "type": "guest"})

    # Build rsvps map: {event_id: {person_name: status}}
    rsvps: dict[str, dict[str, str]] = {}
    for eid in event_ids:
        rsvps[eid] = {}
        for rv in member_rsvps.get(eid, []):
            name = rv.get("user_name") or rv.get("user_email", "")
            rsvps[eid][name] = rv["status"]
        for rv in guest_rsvps.get(eid, []):
            rsvps[eid][rv["guest_name"]] = rv["status"]

    # Build event rows grouped by day
    day_events: dict[str, list] = defaultdict(list)
    for e in events:
        if not e.get("start_time"):
            continue
        try:
            d = dt.fromisoformat(e["start_time"])
            day_key = d.strftime("%Y-%m-%d")
            day_events[day_key].append((d, e))
        except (ValueError, TypeError):
            pass

    event_rows = []
    for day_str in sorted(day_events.keys()):
        items = day_events[day_str]
        items.sort(key=lambda x: -(x[1].get("score") or 0))
        try:
            d = dt.strptime(day_str, "%Y-%m-%d")
            day_label = d.strftime("%A, %b %-d")
        except ValueError:
            day_label = day_str

        for event_dt, e in items[:8]:
            try:
                time_str = event_dt.strftime("%-I:%M %p")
            except ValueError:
                time_str = ""
            event_rows.append({
                "event_id": e.get("event_id", ""),
                "title": (e.get("title") or "")[:55],
                "time": time_str,
                "location": (e.get("location_name") or "")[:30],
                "score": int(e.get("score") or 0),
                "day_label": day_label,
            })

    return {"events": event_rows, "people": people, "rsvps": rsvps}


@app.get("/feed.ics")
async def ical_feed(min_score: int = 55):
    """iCal feed of top recommended events. Subscribe in Google/Apple Calendar.
    Default: score >= 55 (strong matches). Use ?min_score=25 for everything kept."""
    db = get_db()
    settings = Settings()
    runs = db.get_runs()
    if not runs:
        return Response(content="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR",
                       media_type="text/calendar")

    run_id = runs[0]["id"]
    events = db.get_run_events(run_id)
    kept = [e for e in events if e.get("keep") and (e.get("score") or 0) >= min_score]

    from datetime import timezone as _tz
    utcnow = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//recom//Event Recommender//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Calyx Events",
        "X-WR-CALDESC:Personalized event recommendations for Boston/Cambridge",
        "X-APPLE-CALENDAR-COLOR:#4f46e5",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
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

        # Distance info
        dist_str = ""
        lat, lon = e.get("lat"), e.get("lon")
        if lat and lon and not e.get("is_online"):
            import math as _math
            dlat = _math.radians(lat - settings.latitude)
            dlon = _math.radians(lon - settings.longitude)
            a = _math.sin(dlat/2)**2 + _math.cos(_math.radians(settings.latitude))*_math.cos(_math.radians(lat))*_math.sin(dlon/2)**2
            km = 6371 * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1-a))
            dist_str = f"\\n📍 {km:.1f}km from home"

        vevent_lines = [
            "BEGIN:VEVENT",
            _fold_line(f"UID:{uid}"),
            f"DTSTAMP:{utcnow}",
            f"DTSTART:{dtstart}",
            _fold_line(f"SUMMARY:[{score}] {title}"),
            _fold_line(f"LOCATION:{location}"),
            _fold_line(f"URL:{url}"),
            _fold_line(f"DESCRIPTION:{price}\\nScore: {score}/100{dist_str}\\n{reason}"),
            f"CATEGORIES:{vibe}",
            "TRANSP:TRANSPARENT",
            "DURATION:PT2H",
        ]
        if lat and lon:
            vevent_lines.append(f"GEO:{lat};{lon}")
        vevent_lines.append("END:VEVENT")
        lines.extend(vevent_lines)

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

    _default_og = '<meta property="og:site_name" content="Calyx"><meta property="og:type" content="website"><meta property="og:title" content="Join Calyx"><meta property="og:description" content="Find events and make plans with friends"><meta property="og:image" content="https://calyx.arthgupta.dev/static/og-image.png"><meta name="twitter:card" content="summary_large_image">'
    return HTMLResponse(LAYOUT_STYLE.replace("__TITLE__", "Join Calyx").replace("__OG_TAGS__", _default_og) + render_nav(None) + f"""
    <div class="app-content" style="max-width:560px;">

    {success_banner}

    <div style="text-align:center;padding:32px 0 24px;">
      <div style="font-size:13px;font-weight:700;letter-spacing:2px;color:#818cf8;text-transform:uppercase;margin-bottom:12px;">◉ CALYX</div>
      <h1 style="font-size:30px;font-weight:800;letter-spacing:-.5px;margin-bottom:10px;">Get your Discover Weekly<br>for real life</h1>
      <p style="color:#6b7280;font-size:15px;line-height:1.6;">AI-curated Boston events every week, based on what you actually listen to and watch.</p>
    </div>

    <div class="card" style="margin-bottom:16px;border-top:3px solid #818cf8;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
        <div style="background:#ede9fe;color:#7c3aed;font-weight:800;font-size:12px;padding:4px 10px;border-radius:20px;">Step 1</div>
        <h2 style="font-size:16px;font-weight:700;">Create your account</h2>
      </div>
      <form action="/api/join" method="post" style="display:flex;flex-direction:column;gap:10px;">
        <input name="name" placeholder="Your name" required
               style="padding:10px 14px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:14px;width:100%;">
        <input name="email" type="email" placeholder="you@gmail.com" required
               style="padding:10px 14px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:14px;width:100%;">
        <input name="location" placeholder="Cambridge, MA" value="Cambridge, MA"
               style="padding:10px 14px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:14px;width:100%;">
        <button type="submit" style="padding:12px;background:linear-gradient(135deg,#4f46e5,#7c3aed);color:white;border:none;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer;">
          Get started &rarr;
        </button>
      </form>
      <p style="margin-top:12px;font-size:13px;color:#9ca3af;text-align:center;">Already have an account? <a href="/login" style="color:#4f46e5;font-weight:600;">Sign in</a></p>
    </div>

    <p style="text-align:center;color:#6b7280;font-size:13px;margin-top:8px;">Takes 2 minutes. No credit card.</p>
    </div>
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

    # Seed taste items for new user
    db.seed_taste_items(user_id)

    # Send magic link email
    user = db.get_user(user_id)
    token = user["user_token"] if user else ""
    settings = Settings()
    try:
        send_magic_link(email, token, settings.dashboard_url, settings)
    except Exception:
        logger.exception("Failed to send magic link to %s", email)

    resp = RedirectResponse(f"/onboarding/{token}", status_code=303)
    _set_token_cookie(resp, token)
    return resp


@app.get("/onboarding/{token}", response_class=HTMLResponse)
async def onboarding_page(token: str):
    """Post-signup Elo taste onboarding — 10 quick matchups."""
    db = get_db()
    owner = db.get_user_by_token(token)
    if not owner:
        return RedirectResponse("/join")
    user_id = owner["id"]
    name = (owner.get("name") or "").split()[0] or "there"
    settings = Settings()

    db.seed_taste_items(user_id)
    items = db.get_taste_items(user_id)
    pair = db.get_taste_matchup_pair(user_id)
    pair_json = json.dumps([dict(pair[0]), dict(pair[1])]) if pair else "null"
    items_json = json.dumps(items, default=str)
    feed_url = f"{settings.dashboard_url}/u/{token}/feed.ics"

    resp = HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up your taste — Calyx</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Inter', sans-serif; background: #0f0f1a; color: #e2e8f0; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: flex-start; padding: 40px 16px 80px; }}
.logo {{ font-size: 15px; font-weight: 800; color: #818cf8; letter-spacing: .5px; margin-bottom: 40px; }}
.progress-wrap {{ width: 100%; max-width: 480px; margin-bottom: 32px; }}
.progress-bar {{ height: 4px; background: #1e1e3a; border-radius: 2px; overflow: hidden; }}
.progress-fill {{ height: 100%; background: linear-gradient(90deg, #818cf8, #c084fc); border-radius: 2px; transition: width .4s ease; }}
.progress-label {{ font-size: 12px; color: #4b5563; margin-top: 8px; text-align: right; }}
.card {{ width: 100%; max-width: 480px; }}
.phase {{ display: none; }}
.phase.active {{ display: block; }}
/* Welcome */
.welcome-icon {{ font-size: 48px; text-align: center; margin-bottom: 16px; }}
.welcome-title {{ font-size: 1.8rem; font-weight: 900; text-align: center; background: linear-gradient(135deg, #818cf8, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 8px; }}
.welcome-sub {{ text-align: center; color: #94a3b8; font-size: 15px; line-height: 1.6; margin-bottom: 28px; }}
.btn-primary {{ display: block; width: 100%; padding: 14px; background: linear-gradient(135deg, #4f46e5, #7c3aed); color: white; border: none; border-radius: 16px; font-size: 16px; font-weight: 700; cursor: pointer; font-family: inherit; text-align: center; text-decoration: none; transition: transform .15s, box-shadow .15s; box-shadow: 0 4px 20px rgba(79,70,229,.4); }}
.btn-primary:hover {{ transform: translateY(-1px); box-shadow: 0 6px 28px rgba(79,70,229,.5); }}
.btn-secondary {{ display: block; width: 100%; padding: 12px; background: transparent; color: #6b7280; border: 1px solid #374151; border-radius: 16px; font-size: 14px; font-weight: 600; cursor: pointer; font-family: inherit; text-align: center; margin-top: 10px; transition: all .15s; text-decoration: none; }}
.btn-secondary:hover {{ border-color: #6b7280; color: #9ca3af; }}
/* Matchup */
.matchup-label {{ text-align: center; font-size: 11px; font-weight: 700; letter-spacing: 2.5px; text-transform: uppercase; color: #6366f1; margin-bottom: 20px; }}
.question {{ text-align: center; font-size: 1.1rem; font-weight: 700; color: #e2e8f0; margin-bottom: 24px; line-height: 1.4; }}
.vs-grid {{ display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px; align-items: center; margin-bottom: 16px; }}
.opt {{ background: #1e1e3a; border: 2px solid #2d2d5e; border-radius: 16px; padding: 20px 12px; text-align: center; cursor: pointer; transition: all .2s; }}
.opt:hover {{ border-color: #818cf8; background: #1a1a2e; transform: translateY(-2px); box-shadow: 0 6px 24px rgba(129,140,248,.2); }}
.opt.chosen {{ border-color: #22c55e; background: #052e16; pointer-events: none; }}
.opt-cat {{ font-size: 10px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; color: #6b7280; margin-bottom: 8px; }}
.opt-label {{ font-size: 15px; font-weight: 700; color: #e2e8f0; line-height: 1.3; }}
.vs-badge {{ font-size: 20px; font-weight: 900; color: #374151; text-align: center; }}
.equal-link {{ display: block; text-align: center; font-size: 13px; color: #4b5563; cursor: pointer; margin-top: 4px; padding: 8px; transition: color .15s; background: none; border: none; font-family: inherit; width: 100%; }}
.equal-link:hover {{ color: #6b7280; }}
/* Finish */
.finish-icon {{ font-size: 56px; text-align: center; margin-bottom: 16px; }}
.finish-title {{ font-size: 1.6rem; font-weight: 900; text-align: center; color: #e2e8f0; margin-bottom: 8px; }}
.finish-sub {{ text-align: center; color: #94a3b8; font-size: 14px; line-height: 1.6; margin-bottom: 24px; }}
.top-3 {{ background: #1e1e3a; border-radius: 16px; padding: 16px; margin-bottom: 20px; border: 1px solid #2d2d5e; }}
.top-3-label {{ font-size: 11px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: #6366f1; margin-bottom: 12px; }}
.top-item {{ display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid #2d2d5e; }}
.top-item:last-child {{ border-bottom: none; }}
.top-rank {{ font-size: 13px; font-weight: 800; color: #4b5563; width: 20px; }}
.top-label {{ font-size: 14px; font-weight: 600; color: #e2e8f0; flex: 1; }}
.steps-next {{ display: flex; flex-direction: column; gap: 10px; }}
.step-chip {{ display: flex; align-items: center; gap: 12px; background: #1e1e3a; border-radius: 12px; padding: 14px 16px; border: 1px solid #2d2d5e; text-decoration: none; color: inherit; transition: border-color .15s; }}
.step-chip:hover {{ border-color: #4b5563; }}
.step-chip-icon {{ font-size: 22px; flex-shrink: 0; }}
.step-chip-text {{ flex: 1; }}
.step-chip-title {{ font-size: 14px; font-weight: 700; color: #e2e8f0; }}
.step-chip-sub {{ font-size: 12px; color: #6b7280; margin-top: 2px; }}
.step-chip-arrow {{ color: #4b5563; font-size: 18px; }}
</style>
</head>
<body>
<div class="logo">◉ calyx</div>

<div class="progress-wrap">
  <div class="progress-bar"><div class="progress-fill" id="prog" style="width:0%"></div></div>
  <div class="progress-label" id="prog-label">0 / 10 matchups</div>
</div>

<!-- Phase 1: Welcome -->
<div class="card phase active" id="phase-welcome">
  <div class="welcome-icon">👋</div>
  <div class="welcome-title">Hey {name}!</div>
  <div class="welcome-sub">Before your first email, tell us what you love doing. 10 quick picks and we'll have enough to find you something great.</div>
  <button class="btn-primary" onclick="startMatchups()">Let's go →</button>
  <a href="/" class="btn-secondary">Skip for now</a>
</div>

<!-- Phase 2: Matchups -->
<div class="card phase" id="phase-matchup">
  <div class="matchup-label" id="matchup-counter">Matchup 1 of 10</div>
  <div class="question">Which sounds more fun to you?</div>
  <div class="vs-grid">
    <div class="opt" id="opt-a" onclick="vote(null, 'a')">
      <div class="opt-cat" id="cat-a"></div>
      <div class="opt-label" id="label-a"></div>
    </div>
    <div class="vs-badge">vs</div>
    <div class="opt" id="opt-b" onclick="vote(null, 'b')">
      <div class="opt-cat" id="cat-b"></div>
      <div class="opt-label" id="label-b"></div>
    </div>
  </div>
  <button class="equal-link" onclick="vote(null, null)">Equal / hard to choose</button>
</div>

<!-- Phase 3: Done -->
<div class="card phase" id="phase-done">
  <div class="finish-icon">🎯</div>
  <div class="finish-title">Taste profile set!</div>
  <div class="finish-sub">Based on your picks, here's what we'll look for first:</div>
  <div class="top-3" id="top-3-list"></div>
  <div class="steps-next">
    <a href="/auth/spotify" class="step-chip">
      <span class="step-chip-icon">🎵</span>
      <div class="step-chip-text">
        <div class="step-chip-title">Connect Spotify</div>
        <div class="step-chip-sub">Find concerts by artists you already love</div>
      </div>
      <span class="step-chip-arrow">→</span>
    </a>
    <div class="step-chip" style="cursor:default;" onclick="copyFeed()">
      <span class="step-chip-icon">📅</span>
      <div class="step-chip-text">
        <div class="step-chip-title">Subscribe to your calendar</div>
        <div class="step-chip-sub" id="feed-url" style="word-break:break-all;font-size:11px">{feed_url}</div>
      </div>
      <span class="step-chip-arrow" id="copy-icon">⎘</span>
    </div>
    <a href="/" class="btn-primary" style="margin-top:4px">Go to my calendar →</a>
  </div>
</div>

<script>
const ITEMS = {items_json};
let PAIR = {pair_json};
let matchupsDone = 0;
const TARGET = 10;

const CAT_COLORS = {{
  music:'#f59e0b', social:'#3b82f6', arts:'#ec4899',
  intellectual:'#8b5cf6', active:'#22c55e', food:'#f97316',
  maker:'#06b6d4', general:'#6b7280'
}};

function setPhase(id) {{
  document.querySelectorAll('.phase').forEach(p => p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}}

function startMatchups() {{
  setPhase('phase-matchup');
  renderPair();
}}

function renderPair() {{
  if (!PAIR) {{ finish(); return; }}
  const [a, b] = PAIR;
  document.getElementById('cat-a').textContent = a.category;
  document.getElementById('cat-a').style.color = CAT_COLORS[a.category] || '#6b7280';
  document.getElementById('label-a').textContent = a.label;
  document.getElementById('cat-b').textContent = b.category;
  document.getElementById('cat-b').style.color = CAT_COLORS[b.category] || '#6b7280';
  document.getElementById('label-b').textContent = b.label;
  document.getElementById('opt-a').classList.remove('chosen');
  document.getElementById('opt-b').classList.remove('chosen');
}}

function vote(e, side) {{
  if (!PAIR) return;
  const [a, b] = PAIR;
  const winnerId = side === 'a' ? a.id : side === 'b' ? b.id : null;
  if (side) document.getElementById('opt-' + side).classList.add('chosen');

  fetch('/api/taste/vote', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{item_a_id: a.id, item_b_id: b.id, winner_id: winnerId}})
  }}).then(r => r.json()).then(d => {{
    PAIR = d.next_pair;
    matchupsDone++;
    const pct = Math.min(100, Math.round(matchupsDone / TARGET * 100));
    document.getElementById('prog').style.width = pct + '%';
    document.getElementById('prog-label').textContent = matchupsDone + ' / ' + TARGET + ' matchups';
    document.getElementById('matchup-counter').textContent = 'Matchup ' + (matchupsDone + 1) + ' of ' + TARGET;

    if (matchupsDone >= TARGET || !PAIR) {{
      // Brief flash of chosen option, then finish
      setTimeout(() => finish(d.items), 300);
    }} else {{
      setTimeout(() => renderPair(), 200);
    }}
  }});
}}

function finish(items) {{
  setPhase('phase-done');
  document.getElementById('prog').style.width = '100%';
  document.getElementById('prog-label').textContent = 'Done!';

  if (!items) items = ITEMS;
  const sorted = [...items].sort((a, b) => b.elo_rating - a.elo_rating).slice(0, 3);
  const list = document.getElementById('top-3-list');
  list.innerHTML = '<div class="top-3-label">Your top picks so far</div>' +
    sorted.map((item, i) => `<div class="top-item">
      <span class="top-rank">#${{i+1}}</span>
      <span class="top-label">${{item.label}}</span>
    </div>`).join('');
}}

function copyFeed() {{
  const url = document.getElementById('feed-url').textContent.trim();
  navigator.clipboard.writeText(url).then(() => {{
    document.getElementById('copy-icon').textContent = '✓';
    setTimeout(() => document.getElementById('copy-icon').textContent = '⎘', 2000);
  }});
}}
</script>
</body>
</html>""")
    _set_token_cookie(resp, token)
    return resp


@app.get("/auth/spotify")
async def spotify_auth_start(request: Request):
    """Redirect user to Spotify OAuth."""
    current_user = _get_current_user(request)
    if not current_user:
        return RedirectResponse("/login", status_code=307)
    settings = Settings()
    if not settings.spotify_client_id:
        return HTMLResponse("<h1>Spotify not configured</h1><p>Set RECOM_SPOTIFY_CLIENT_ID in .env</p>")

    import urllib.parse
    scopes = "user-read-recently-played user-top-read user-library-read"
    redirect_uri = f"{settings.dashboard_url}/callback"
    params = urllib.parse.urlencode({
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "show_dialog": "true",
        "state": current_user["user_token"],
    })
    return RedirectResponse(f"https://accounts.spotify.com/authorize?{params}")


@app.get("/callback")
async def spotify_callback(code: str = "", error: str = "", state: str = ""):
    """Handle Spotify OAuth callback."""
    if error or not code:
        return HTMLResponse(f"<h1>Spotify auth failed</h1><p>{error}</p>")

    settings = Settings()
    redirect_uri = f"{settings.dashboard_url}/callback"
    import httpx
    # Exchange code for token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.spotify_client_id,
                "client_secret": settings.spotify_client_secret,
            },
        )
        if resp.status_code != 200:
            return HTMLResponse(f"<h1>Token exchange failed</h1><pre>{resp.text}</pre>")
        token_data = resp.json()

    # Save token per-user
    import json as _json
    from pathlib import Path
    db = get_db()
    user = db.get_user_by_token(state) if state else None
    user_id = user["id"] if user else 1
    token_dir = Path("state/tokens")
    token_dir.mkdir(parents=True, exist_ok=True)
    token_path = token_dir / f"spotify_user_{user_id}.json"
    token_path.write_text(_json.dumps(token_data))
    # Update user record
    db.conn.execute("UPDATE users SET spotify_token_file = ? WHERE id = ?", (str(token_path), user_id))
    db.conn.commit()

    # Redirect back to profile with success message
    resp = RedirectResponse("/profile?success=Spotify+connected", status_code=303)
    if state:
        _set_token_cookie(resp, state)
    return resp


@app.get("/auth/youtube")
async def youtube_auth_start(request: Request):
    """Redirect user to Google OAuth for YouTube access."""
    current_user = _get_current_user(request)
    if not current_user:
        return RedirectResponse("/login", status_code=307)
    settings = Settings()
    # Get client ID from env or from client_secrets.json
    client_id = settings.google_client_id
    if not client_id:
        try:
            import json as _json
            from pathlib import Path
            secrets = _json.loads(Path(settings.google_client_secrets_file).read_text())
            cred = secrets.get("web") or secrets.get("installed", {})
            client_id = cred.get("client_id", "")
        except Exception:
            pass
    if not client_id:
        return HTMLResponse("<h1>YouTube not configured</h1><p>Set RECOM_GOOGLE_CLIENT_ID in .env</p>")

    import urllib.parse
    redirect_uri = f"{settings.dashboard_url}/callback/youtube"
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "https://www.googleapis.com/auth/youtube.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "state": current_user["user_token"],
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@app.get("/callback/youtube")
async def youtube_callback(code: str = "", error: str = "", state: str = ""):
    """Handle Google OAuth callback for YouTube."""
    if error or not code:
        return HTMLResponse(f"<h1>YouTube auth failed</h1><p>{error}</p>")

    settings = Settings()
    # Get client credentials
    client_id = settings.google_client_id
    client_secret = settings.google_client_secret
    if not client_id or not client_secret:
        try:
            import json as _json
            from pathlib import Path
            secrets = _json.loads(Path(settings.google_client_secrets_file).read_text())
            cred = secrets.get("web") or secrets.get("installed", {})
            client_id = client_id or cred.get("client_id", "")
            client_secret = client_secret or cred.get("client_secret", "")
        except Exception:
            pass

    redirect_uri = f"{settings.dashboard_url}/callback/youtube"
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if resp.status_code != 200:
            return HTMLResponse(f"<h1>YouTube token exchange failed</h1><pre>{resp.text}</pre>")
        token_data = resp.json()

    # Save token per-user (format compatible with google-auth library)
    import json as _json
    from pathlib import Path
    db = get_db()
    user = db.get_user_by_token(state) if state else None
    user_id = user["id"] if user else 1
    token_dir = Path("state/tokens")
    token_dir.mkdir(parents=True, exist_ok=True)
    token_path = token_dir / f"youtube_user_{user_id}.json"
    # Save in google-auth-compatible format
    google_token = {
        "token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": ["https://www.googleapis.com/auth/youtube.readonly"],
    }
    token_path.write_text(_json.dumps(google_token))
    db.conn.execute("UPDATE users SET youtube_token_file = ? WHERE id = ?", (str(token_path), user_id))
    db.conn.commit()

    resp = RedirectResponse("/profile?success=YouTube+connected", status_code=303)
    if state:
        _set_token_cookie(resp, state)
    return resp


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_layout("Login", """
    <h1>Sign In</h1>
    <div class="card" style="max-width:400px;">
        <p style="color:#6b7280;margin-bottom:16px;">Enter your email and we'll send you a link to your events.</p>
        <form action="/api/login" method="post" style="display:flex;flex-direction:column;gap:12px;">
            <input name="email" type="email" placeholder="you@gmail.com" required
                   style="padding:10px 14px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:15px;">
            <button type="submit" style="padding:10px 20px;background:#4f46e5;color:white;border:none;
                    border-radius:8px;font-size:15px;cursor:pointer;font-weight:600;">Send me my link</button>
        </form>
        <p style="margin-top:16px;font-size:13px;color:#9ca3af;">New here? <a href="/join">Join Calyx</a></p>
    </div>
    """))


@app.post("/api/login")
async def api_login(request: Request):
    form = await request.form()
    email = form.get("email", "").strip()
    if not email:
        return HTMLResponse("<h1>Email required</h1>", status_code=400)
    db = get_db()
    user = db.get_user_by_email(email)
    if user:
        settings = Settings()
        try:
            send_magic_link(email, user["user_token"], settings.dashboard_url, settings)
        except Exception:
            logger.exception("Failed to send magic link to %s", email)
    # Always show same message (prevent enumeration)
    return HTMLResponse(_layout("Check your email", """
    <div class="card" style="max-width:400px;text-align:center;padding:32px;">
        <h2 style="color:#059669;">Check your email!</h2>
        <p style="color:#6b7280;margin-top:8px;">If an account exists, we sent you a login link.</p>
        <p style="margin-top:16px;font-size:13px;"><a href="/join">Don't have an account? Join</a></p>
    </div>
    """))


@app.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    groups = db.get_all_groups()


    # Upcoming with friends — only events where group-mates are actually going
    upcoming_html = ""
    if current_user:
        from datetime import datetime as _dt
        friend_rsvps = db.get_recent_friend_rsvps(current_user["id"], hours=24*14)
        # Filter to going only, future events, and dedupe by event
        now_str = _dt.now().strftime("%Y-%m-%dT%H:%M:%S")
        seen_events: dict[str, dict] = {}
        for fr in friend_rsvps:
            if fr["status"] != "going":
                continue
            if (fr.get("start_time") or "") < now_str:
                continue
            title = (fr.get("event_title") or "")[:50]
            key = title.lower().strip()
            if key not in seen_events:
                seen_events[key] = {"title": title, "start_time": fr.get("start_time", ""),
                                     "url": fr.get("event_url", "#"), "people": []}
            seen_events[key]["people"].append(fr["user_name"])
        # Sort by date, take first 6
        events_list = sorted(seen_events.values(), key=lambda e: e["start_time"])[:6]
        if events_list:
            items = ""
            for e in events_list:
                day_label = ""
                if e["start_time"]:
                    try:
                        d = _dt.fromisoformat(e["start_time"])
                        day_label = d.strftime("%a %b %-d")
                    except (ValueError, TypeError):
                        pass
                names = ", ".join(e["people"][:3])
                if len(e["people"]) > 3:
                    names += f" +{len(e['people']) - 3}"
                items += f'''<div style="padding:12px 0;border-bottom:1px solid #e0e0e0;">
                    <a href="{e["url"]}" target="_blank" style="font-weight:700;font-size:14px;color:#000;">{e["title"]}</a>
                    <div style="font-size:12px;color:#888;margin-top:2px;">{day_label}</div>
                    <div style="font-size:12px;color:#555;margin-top:4px;">{names}</div>
                </div>'''
            upcoming_html = f'''<div class="card" style="margin-bottom:24px;">
                <h2 style="margin-top:0;">Friends are going</h2>
                {items}
            </div>'''

    # Group cards
    cards_html = ""
    for g in groups:
        is_member = db.is_group_member(g["id"], current_user["id"]) if current_user else False
        members = db.get_group_members(g["id"])
        member_avatars = ""
        for m in members[:6]:
            initial = ((m.get("name") or m.get("email") or "?")[0]).upper()
            member_avatars += f'<div style="width:30px;height:30px;border-radius:50%;background:#e0e7ff;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#4338ca;border:2px solid white;margin-left:-8px;">{initial}</div>'
        if g["member_count"] > 6:
            member_avatars += f'<div style="width:30px;height:30px;border-radius:50%;background:#f3f4f6;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#6b7280;border:2px solid white;margin-left:-8px;">+{g["member_count"] - 6}</div>'

        gname = db.get_group_display_name(g)
        action = f'<a href="/group/{g["id"]}" class="btn-primary" style="padding:8px 20px;font-size:13px;text-decoration:none;">View</a>'
        if current_user and not is_member:
            action = f'''<form action="/group/{g["id"]}/join" method="post" style="display:inline;">
                <button type="submit" class="btn-primary" style="padding:8px 20px;font-size:13px;">Join</button></form>'''

        cards_html += f"""<div class="card" style="padding:16px 20px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <a href="/group/{g["id"]}" style="font-weight:700;font-size:16px;color:#1e293b;text-decoration:none;">{gname}</a>
                    <div style="display:flex;align-items:center;margin-top:6px;padding-left:8px;">{member_avatars}
                        <span style="color:#9ca3af;font-size:13px;margin-left:10px;">{g["member_count"]} member{"s" if g["member_count"] != 1 else ""}</span>
                    </div>
                </div>
                {action}
            </div>
        </div>"""

    create_btn = ""
    if current_user:
        create_btn = f'<a href="/group/create" class="btn-primary" style="display:inline-block;padding:10px 24px;text-decoration:none;margin-bottom:20px;">+ Create Group</a>'

    return HTMLResponse(_layout("Groups", f"""
    <h1 style="display:flex;align-items:center;gap:10px;">Groups</h1>
    {create_btn}
    {upcoming_html}
    {cards_html if cards_html else '<div class="card"><p style="color:#888;">No groups yet. Create one and invite friends.</p></div>'}
    """, user=current_user))


@app.post("/group/{group_id:int}/join")
async def group_join(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    group = db.get_group_by_id(group_id)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)
    db.add_group_member(group["id"], user["id"])
    return RedirectResponse(f"/group/{group_id}", status_code=303)


@app.post("/api/join-group/{group_id:int}/{invite_code}")
async def api_join_group(group_id: int, invite_code: str, request: Request):
    """Combined signup + join group for new users. Validates invite code, creates account, joins group."""
    db = get_db()
    group = db.get_group_by_id(group_id)
    if not group or group.get("invite_code") != invite_code:
        return HTMLResponse("Invalid invite link", status_code=403)

    form = await request.form()
    email = (form.get("email") or "").strip()
    name = (form.get("name") or "").strip()
    if not email:
        return HTMLResponse("Email required", status_code=400)

    existing = db.conn.execute("SELECT id, user_token FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        user_id = existing["id"]
        token = existing["user_token"]
    else:
        user_id = db.create_user(email, name)
        user = db.get_user(user_id)
        token = user["user_token"] if user else ""
        db.seed_taste_items(user_id)

    db.add_group_member(group["id"], user_id)

    # No magic link email — just set cookie and redirect. Instant join.
    resp = RedirectResponse(f"/group/{group_id}?u={token}&success=Welcome+to+the+group!", status_code=303)
    _set_token_cookie(resp, token)
    return resp


# ---------------------------------------------------------------------------
# Single-event .ics download
# ---------------------------------------------------------------------------

def _build_single_event_ics(event: dict, match_reason: str = "", score: int = 0) -> str:
    """Build a VCALENDAR string for a single event."""
    import html as _html
    import urllib.parse as _urlparse
    from datetime import timezone as _tz

    def _esc(text: str) -> str:
        text = _html.unescape(text)
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def _fold(line: str) -> str:
        encoded = line.encode("utf-8")
        if len(encoded) <= 75:
            return line
        chunks = []
        while len(encoded) > 75:
            cut = 75
            while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
                cut -= 1
            if cut == 0:
                cut = 75
            chunks.append(encoded[:cut].decode("utf-8"))
            encoded = encoded[cut:]
        if encoded:
            chunks.append(encoded.decode("utf-8"))
        return "\r\n ".join(chunks)

    utcnow = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    start = event.get("start_time")
    if start:
        try:
            dt = datetime.fromisoformat(start)
            dtstart = dt.strftime("%Y%m%dT%H%M%S")
        except (ValueError, TypeError):
            dtstart = None
    else:
        dtstart = None

    title = _esc(event.get("title") or "Event")
    location = _esc(event.get("location_name") or "")
    url = event.get("url") or ""
    price = _esc(event.get("price") or "Free")
    eid = event.get("event_id", "unknown")

    desc_parts = []
    if match_reason:
        desc_parts.append(match_reason)
    if price and price != "Free":
        desc_parts.append(f"Price: {price}")
    if url:
        desc_parts.append(f"Get tickets: {url}")
    desc = "\\n".join(_esc(p) for p in desc_parts) if desc_parts else ""

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//recom//Event Recommender//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{eid}@recom",
        f"DTSTAMP:{utcnow}",
    ]
    if dtstart:
        lines.append(f"DTSTART:{dtstart}")
    lines.extend([
        _fold(f"SUMMARY:{title}"),
        _fold(f"LOCATION:{location}"),
        _fold(f"URL:{url}"),
        _fold(f"DESCRIPTION:{desc}"),
        "DURATION:PT2H",
        "TRANSP:TRANSPARENT",
    ])
    lat, lon = event.get("lat"), event.get("lon")
    if lat and lon:
        lines.append(f"GEO:{lat};{lon}")
    lines.extend([
        "BEGIN:VALARM",
        "TRIGGER:-PT2H",
        "ACTION:DISPLAY",
        f"DESCRIPTION:Reminder: {title}",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ])
    return "\r\n".join(lines)


def _find_event(db, event_id: str) -> dict | None:
    """Find an event by event_id, preferring the latest run."""
    row = db.conn.execute(
        """SELECT e.*, rk.score, rk.match_reason, rk.vibe
           FROM events e
           LEFT JOIN rankings rk ON rk.run_id = e.run_id AND rk.event_id = e.event_id
           WHERE e.event_id = ?
           ORDER BY e.run_id DESC LIMIT 1""",
        (event_id,),
    ).fetchone()
    return dict(row) if row else None


@app.get("/event/{event_id}.ics")
async def single_event_ics(event_id: str):
    """Public single-event .ics download — no auth required."""
    db = get_db()
    event = _find_event(db, event_id)
    if not event:
        return Response(content="Event not found", status_code=404)
    import re as _re
    slug = _re.sub(r'[^a-z0-9]+', '-', (event.get("title") or "event").lower()).strip('-')[:50]
    ics = _build_single_event_ics(event, match_reason=event.get("match_reason") or "", score=int(event.get("score") or 0))
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{slug}.ics"'},
    )


@app.get("/u/{token}/event/{event_id}.ics")
async def user_single_event_ics(token: str, event_id: str):
    """Per-user single-event .ics download — also sets RSVP to going."""
    import re as _re
    db = get_db()
    user = db.get_user_by_token(token)
    if not user:
        return Response(content="Invalid link", status_code=401)
    event = _find_event(db, event_id)
    if not event:
        return Response(content="Event not found", status_code=404)
    # Set RSVP to maybe (adding to calendar = interested, not committed)
    run = db.get_user_latest_run(user["id"])
    run_id = run["id"] if run else event.get("run_id", 0)
    db.set_rsvp(user["id"], event_id, run_id, "maybe")
    slug = _re.sub(r'[^a-z0-9]+', '-', (event.get("title") or "event").lower()).strip('-')[:50]
    ics = _build_single_event_ics(event, match_reason=event.get("match_reason") or "", score=int(event.get("score") or 0))
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{slug}.ics"'},
    )


@app.get("/u/{token}/event/{event_id}/added", response_class=HTMLResponse)
async def event_added_confirmation(token: str, event_id: str):
    """Confirmation page shown after adding event to calendar."""
    db = get_db()
    user = db.get_user_by_token(token)
    if not user:
        return HTMLResponse("<h1>Invalid link</h1>", status_code=401)
    event = _find_event(db, event_id)
    if not event:
        return HTMLResponse("<h1>Event not found</h1>", status_code=404)
    title = event.get("title") or "Event"
    start = event.get("start_time") or ""
    start_display = start[:16].replace("T", " ") if start else "Date TBD"
    url = event.get("url") or ""
    settings = Settings()
    cal_url = f"{settings.dashboard_url}/u/{token}/cal"
    body = f"""
    <div style="display:flex;justify-content:center;align-items:center;min-height:80vh;">
      <div style="background:white;border-radius:16px;padding:40px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.08);max-width:420px;">
        <div style="font-size:48px;margin-bottom:12px;">&#10003;</div>
        <h2 style="color:#166534;margin:0 0 8px;">Added to your calendar</h2>
        <p style="font-size:16px;font-weight:600;color:#1e293b;margin:0 0 4px;">{title[:80]}</p>
        <p style="font-size:14px;color:#6b7280;margin:0 0 20px;">{start_display}</p>
        <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">
          {'<a href="' + url + '" target="_blank" style="display:inline-block;background:#4f46e5;color:white;text-decoration:none;font-weight:700;font-size:13px;padding:10px 22px;border-radius:50px;">Get tickets &rarr;</a>' if url else ''}
          <a href="/?u={token}" style="display:inline-block;background:#f1f5f9;color:#374151;text-decoration:none;font-weight:600;font-size:13px;padding:10px 22px;border-radius:50px;">Back to calendar</a>
        </div>
        <div style="margin-top:24px;padding-top:20px;border-top:1px solid #e5e7eb;">
          <p style="font-size:13px;color:#9ca3af;margin:0 0 8px;">Get all your picks automatically:</p>
          <a href="{cal_url}" style="color:#4f46e5;font-weight:600;font-size:13px;text-decoration:none;">Subscribe to calendar feed &rarr;</a>
        </div>
      </div>
    </div>"""
    return HTMLResponse(_layout("Event Added", body, user))


@app.get("/u/{token}/feed.ics")
async def user_ical_feed(token: str, min_score: int = 40):
    """Per-user shareable iCal feed. The token in the URL IS the auth."""
    db = get_db()
    user = db.get_user_by_token(token)
    _empty_cal = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//recom//Event Recommender//EN\r\nCALSCALE:GREGORIAN\r\nX-WR-CALNAME:Calyx Events\r\nEND:VCALENDAR"
    if not user:
        return Response(content=_empty_cal, media_type="text/calendar")

    run = db.get_user_latest_run(user["id"])
    if not run:
        return Response(content=_empty_cal, media_type="text/calendar")

    # Use daily_picks table (shared with email) if available, else fall back
    kept = db.get_daily_picks(run["id"])
    if not kept:
        # Fallback: compute on the fly (first time or old runs)
        db.compute_daily_picks(run["id"], user["id"], min_score=min_score)
        kept = db.get_daily_picks(run["id"])
    if not kept:
        events = db.get_run_events(run["id"])
        kept = [e for e in events if e.get("keep") and (e.get("score") or 0) >= min_score]

    import html as _html
    import urllib.parse as _urlparse

    def _ical_escape(text: str) -> str:
        text = _html.unescape(text)
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def _ical_fold(line: str) -> str:
        """RFC 5545 line folding at 75 octets."""
        encoded = line.encode("utf-8")
        if len(encoded) <= 75:
            return line
        chunks = []
        while len(encoded) > 75:
            cut = 75
            # Don't split a multi-byte UTF-8 char: back up if we're in the middle of one
            while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
                cut -= 1
            if cut == 0:
                cut = 75  # safety: shouldn't happen, but avoid infinite loop
            chunks.append(encoded[:cut].decode("utf-8"))
            encoded = encoded[cut:]
        if encoded:
            chunks.append(encoded.decode("utf-8"))
        return "\r\n ".join(chunks)

    from datetime import timezone as _tz
    utcnow = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")

    user_name = user.get("name") or user.get("email", "")
    user_email = user.get("email", "")
    settings = Settings()
    dashboard_url = settings.dashboard_url

    # Get user's RSVPs for ATTENDEE/TRANSP
    user_rsvp_rows = db.conn.execute(
        "SELECT event_id, status FROM rsvps WHERE user_id = ?", (user["id"],)
    ).fetchall()
    user_rsvp_map = {r["event_id"]: r["status"] for r in user_rsvp_rows}

    # Get friend RSVPs (from shared groups) for title annotation
    friend_rsvps: dict[str, list[str]] = {}  # event_id -> ["Name going", "Name maybe"]
    user_groups = db.get_user_groups(user["id"])
    if user_groups:
        group_member_ids = set()
        for g in user_groups:
            for m in db.get_group_members(g["id"]):
                if m["id"] != user["id"]:
                    group_member_ids.add(m["id"])
        if group_member_ids:
            placeholders = ",".join("?" * len(group_member_ids))
            friend_rows = db.conn.execute(
                f"""SELECT r.event_id, r.status, u.name, u.email
                    FROM rsvps r JOIN users u ON u.id = r.user_id
                    WHERE r.user_id IN ({placeholders}) AND r.status IN ('going', 'maybe')""",
                list(group_member_ids),
            ).fetchall()
            for fr in friend_rows:
                fname = (fr["name"] or fr["email"] or "?").split()[0]
                eid = fr["event_id"]
                label = f"{fname} {'going' if fr['status'] == 'going' else 'maybe'}"
                friend_rsvps.setdefault(eid, []).append(label)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//recom//User {_ical_escape(user_name)}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Calyx — {_ical_escape(user_name)}'s Picks",
        "X-APPLE-CALENDAR-COLOR:#818cf8",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]

    kept.sort(key=lambda x: -(x.get("score") or 0))
    for e in kept[:50]:
        start = e.get("start_time")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start)
        except (ValueError, TypeError):
            continue
        dtstart = dt.strftime("%Y%m%dT%H%M%S")
        raw_title = e.get("title") or ""
        title = _ical_escape(raw_title)
        location = _ical_escape(e.get("location_name") or "")
        url = e.get("url") or ""
        score = int(e.get("score") or 0)
        reason = _ical_escape(e.get("match_reason") or "")
        price = _ical_escape(e.get("price") or "Free")
        eid = e.get("event_id", "")
        uid = f"{eid}@recom-user-{token}"
        vibe = e.get("vibe", "mixed")
        lat, lon = e.get("lat"), e.get("lon")

        # Build RSVP links for the description
        enc_title = _urlparse.quote_plus(raw_title)
        rsvp_going = f"{dashboard_url}/api/rsvp-link?event_id={eid}&status=going&u={token}&title={enc_title}"
        rsvp_maybe = f"{dashboard_url}/api/rsvp-link?event_id={eid}&status=maybe&u={token}&title={enc_title}"
        desc = f"{price}\\nScore: {score}/100\\n{reason}\\n\\nRSVP Going: {rsvp_going}\\nRSVP Maybe: {rsvp_maybe}"

        # RSVP status for this user
        user_rsvp_status = user_rsvp_map.get(eid)
        is_going = user_rsvp_status == "going"

        # Build title with friend RSVPs
        friends_tag = ""
        fr_list = friend_rsvps.get(eid, [])
        if fr_list:
            friends_tag = f" ({_ical_escape(', '.join(fr_list))})"

        vevent_lines = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{utcnow}",
            f"DTSTART:{dtstart}",
            _ical_fold(f"SUMMARY:[{score}] {title}{friends_tag}"),
            _ical_fold(f"LOCATION:{location}"),
            _ical_fold(f"URL:{url}"),
            _ical_fold(f"DESCRIPTION:{desc}"),
            f"CATEGORIES:{vibe}",
            f"TRANSP:{'OPAQUE' if is_going else 'TRANSPARENT'}",
            "DURATION:PT2H",
        ]
        if lat and lon:
            vevent_lines.append(f"GEO:{lat};{lon}")
        # ATTENDEE for user's own RSVP
        if user_rsvp_status and user_email:
            partstat_map = {"going": "ACCEPTED", "maybe": "TENTATIVE", "cant": "DECLINED"}
            ps = partstat_map.get(user_rsvp_status)
            if ps:
                cn = _ical_escape(user_name)
                vevent_lines.append(f"ATTENDEE;PARTSTAT={ps};CN={cn}:mailto:{user_email}")
        # Reminder alarm
        vevent_lines.extend([
            "BEGIN:VALARM",
            "TRIGGER:-PT2H",
            "ACTION:DISPLAY",
            f"DESCRIPTION:Reminder: {title}",
            "END:VALARM",
        ])
        vevent_lines.append("END:VEVENT")
        lines.extend(vevent_lines)

    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar",
        headers={"Content-Disposition": f"inline; filename=recom-{token}.ics"},
    )


@app.get("/u/{token}/rsvps.ics")
async def user_rsvps_ical(token: str, recs: int = 0):
    """Unified RSVP feed: your RSVPs + all group members' RSVPs, with names.
    Add ?recs=1 to also include your personal recommendations (daily picks)."""
    import html as _html

    db = get_db()
    user = db.get_user_by_token(token)
    if not user:
        return Response(content="BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR",
                       media_type="text/calendar")

    from datetime import timezone as _tz
    utcnow = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")

    user_id = user["id"]
    user_name = user.get("name") or user.get("email", "")
    user_first = user_name.split()[0] if user_name else "You"

    # Collect all relevant user IDs: self + group members
    all_user_ids = {user_id}
    user_groups = db.get_user_groups(user_id)
    for g in user_groups:
        for m in db.get_group_members(g["id"]):
            all_user_ids.add(m["id"])

    # Get all RSVPs from all relevant users
    placeholders = ",".join("?" * len(all_user_ids))
    rows = db.conn.execute(
        f"""SELECT r.user_id, r.status, r.event_id, e.title, e.start_time,
                   e.location_name, e.url, e.price, e.lat, e.lon,
                   COALESCE(rk.vibe, 'mixed') as vibe,
                   COALESCE(rk.match_reason, '') as match_reason,
                   u.name as rsvp_user_name, u.email as rsvp_user_email
            FROM rsvps r
            JOIN events e ON e.event_id = r.event_id
            JOIN users u ON u.id = r.user_id
            LEFT JOIN rankings rk ON rk.event_id = r.event_id AND rk.run_id = e.run_id
            WHERE r.user_id IN ({placeholders}) AND r.status IN ('going', 'maybe')
            ORDER BY e.start_time ASC""",
        list(all_user_ids),
    ).fetchall()

    # Group RSVPs by event_id
    event_rsvps: dict[str, list[dict]] = {}
    event_data: dict[str, dict] = {}
    for r in rows:
        rd = dict(r)
        eid = rd["event_id"]
        if eid not in event_data:
            event_data[eid] = rd
        event_rsvps.setdefault(eid, []).append(rd)

    def _esc(text: str) -> str:
        text = _html.unescape(str(text))
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def _fold(line: str) -> str:
        encoded = line.encode("utf-8")
        if len(encoded) <= 75:
            return line
        chunks = []
        while len(encoded) > 75:
            cut = 75
            while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
                cut -= 1
            if cut == 0:
                cut = 75
            chunks.append(encoded[:cut].decode("utf-8"))
            encoded = encoded[cut:]
        if encoded:
            chunks.append(encoded.decode("utf-8"))
        return "\r\n ".join(chunks)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//recom//Plans {_esc(user_name)}//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:Calyx — Plans",
        "X-APPLE-CALENDAR-COLOR:#22c55e",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]

    settings = Settings()
    dashboard_url = settings.dashboard_url

    for eid, ev in event_data.items():
        start = ev.get("start_time")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start)
        except (ValueError, TypeError):
            continue
        dtstart = dt.strftime("%Y%m%dT%H%M%S")

        title = _esc(ev.get("title") or "Event")
        location = _esc(ev.get("location_name") or "")
        url = ev.get("url") or ""

        # Build who's going tag for title
        rsvp_list = event_rsvps.get(eid, [])
        people = []
        my_status = None
        for rr in rsvp_list:
            fname = (rr["rsvp_user_name"] or rr["rsvp_user_email"] or "?").split()[0]
            if rr["user_id"] == user_id:
                my_status = rr["status"]
                fname = "You"
            label = "going" if rr["status"] == "going" else "maybe"
            people.append(f"{fname} {label}")
        people_tag = f" ({_esc(', '.join(people))})" if people else ""

        # RSVP links in description
        import urllib.parse as _urlparse
        enc_title = _urlparse.quote_plus(ev.get("title") or "")
        rsvp_going = f"{dashboard_url}/api/rsvp-link?event_id={eid}&status=going&u={token}&title={enc_title}"
        rsvp_maybe = f"{dashboard_url}/api/rsvp-link?event_id={eid}&status=maybe&u={token}&title={enc_title}"
        reason = _esc(ev.get("match_reason") or "")
        desc_parts = []
        if reason:
            desc_parts.append(reason)
        if url:
            desc_parts.append(f"Tickets: {url}")
        desc_parts.append(f"RSVP Going: {rsvp_going}")
        desc_parts.append(f"RSVP Maybe: {rsvp_maybe}")
        desc = "\\n".join(_esc(p) for p in desc_parts)

        is_going = my_status == "going"

        vevent_lines = [
            "BEGIN:VEVENT",
            f"UID:{eid}@rsvps-{token}",
            f"DTSTAMP:{utcnow}",
            f"DTSTART:{dtstart}",
            _fold(f"SUMMARY:{title}{people_tag}"),
            _fold(f"LOCATION:{location}"),
            _fold(f"URL:{url}"),
            _fold(f"DESCRIPTION:{desc}"),
            f"TRANSP:{'OPAQUE' if is_going else 'TRANSPARENT'}",
            "DURATION:PT2H",
        ]
        lat, lon = ev.get("lat"), ev.get("lon")
        if lat and lon:
            vevent_lines.append(f"GEO:{lat};{lon}")
        vevent_lines.extend([
            "BEGIN:VALARM",
            "TRIGGER:-PT2H",
            "ACTION:DISPLAY",
            f"DESCRIPTION:Reminder: {title}",
            "END:VALARM",
            "END:VEVENT",
        ])
        lines.extend(vevent_lines)

    # Optionally include personal recs (daily picks not already RSVP'd)
    if recs:
        rsvp_eids = set(event_data.keys())
        run = db.get_user_latest_run(user_id)
        if run:
            picks = db.get_daily_picks(run["id"])
            if not picks:
                db.compute_daily_picks(run["id"], user_id)
                picks = db.get_daily_picks(run["id"])
            for p in picks:
                peid = p.get("event_id", "")
                if peid in rsvp_eids or not p.get("start_time"):
                    continue
                try:
                    pdt = datetime.fromisoformat(p["start_time"])
                except (ValueError, TypeError):
                    continue
                ptitle = _esc(p.get("title") or "Event")
                ploc = _esc(p.get("location_name") or "")
                purl = p.get("url") or ""
                pscore = int(p.get("score") or 0)
                preason = _esc(p.get("match_reason") or "")
                pdesc_parts = []
                if preason:
                    pdesc_parts.append(preason)
                if purl:
                    pdesc_parts.append(f"Tickets: {purl}")
                import urllib.parse as _up2
                penc = _up2.quote_plus(p.get("title") or "")
                pdesc_parts.append(f"RSVP Going: {dashboard_url}/api/rsvp-link?event_id={peid}&status=going&u={token}&title={penc}")
                pdesc_parts.append(f"RSVP Maybe: {dashboard_url}/api/rsvp-link?event_id={peid}&status=maybe&u={token}&title={penc}")
                pdesc = "\\n".join(_esc(pp) for pp in pdesc_parts)
                vlines = [
                    "BEGIN:VEVENT",
                    f"UID:{peid}@recs-{token}",
                    f"DTSTAMP:{utcnow}",
                    f"DTSTART:{pdt.strftime('%Y%m%dT%H%M%S')}",
                    _fold(f"SUMMARY:[{pscore}] {ptitle}"),
                    _fold(f"LOCATION:{ploc}"),
                    _fold(f"URL:{purl}"),
                    _fold(f"DESCRIPTION:{pdesc}"),
                    "TRANSP:TRANSPARENT",
                    "DURATION:PT2H",
                    "END:VEVENT",
                ]
                lines.extend(vlines)

    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar",
        headers={"Content-Disposition": f"inline; filename=recom-plans-{token}.ics"},
    )




def run():
    """Entry point for recom-dashboard command."""
    import uvicorn
    uvicorn.run("recom.dashboard.app:app", host="0.0.0.0", port=8000, reload=False)
