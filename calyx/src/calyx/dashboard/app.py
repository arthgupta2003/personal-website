from __future__ import annotations

import json
import logging
import secrets

from datetime import datetime

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.responses import RedirectResponse

from calyx.config import Settings
from calyx.db import Database
from calyx.dashboard.auth import build_login_url as google_login_url, exchange_code as google_exchange_code
from calyx.email.sender import send_invite_email, send_rsvp_notify, send_group_event_notification
from calyx.email.invite import send_event_invites_to_members, send_calendar_invite, build_invite_ics, event_uid_for
from calyx.gcal import get_or_create_calendar, push_event as gcal_push_event, update_attendees as gcal_update_attendees, sync_rsvps_to_db as gcal_sync_rsvps

logger = logging.getLogger(__name__)

app = FastAPI(title="Calyx Dashboard")

from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path
_static_dir = _Path(__file__).parent.parent.parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

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


def _oauth_redirect_uri(settings: Settings) -> str:
    return settings.dashboard_url.rstrip("/") + "/auth/google/callback"


_LOGO_SVG = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 2C9 6 4 8 4 13c0 4 3.5 7 8 7s8-3 8-7c0-5-5-7-8-11z" fill="#4a6741" opacity=".15"/><path d="M12 5c-2 3-5.5 4.5-5.5 8.5 0 3 2.5 5.5 5.5 5.5s5.5-2.5 5.5-5.5c0-4-3.5-5.5-5.5-8.5z" fill="#4a6741" opacity=".3"/><path d="M12 8c-1.5 2-3.5 3-3.5 5.5 0 2 1.5 3.5 3.5 3.5s3.5-1.5 3.5-3.5c0-2.5-2-3.5-3.5-5.5z" fill="#4a6741"/><path d="M12 12v6" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/><path d="M10.5 14.5c.5-.5 1.5-.5 1.5-.5" stroke="#fff" stroke-width=".8" stroke-linecap="round"/></svg>'


def render_nav(user: dict | None = None) -> str:
    if user:
        name = user.get("name") or user.get("email", "")
        return f"""<nav class="app-nav"><div class="app-nav-inner">
          <a href="/" class="app-logo">{_LOGO_SVG} calyx</a>
          <a href="/groups" class="nav-link">Groups</a>
          <a href="/calendar" class="nav-link">Discover</a>
          <a href="/taste-profile" class="nav-link">You</a>
          <div class="nav-divider"></div>
          <span class="nav-user-name">{name}</span>
          <a href="/auth/logout" class="nav-link nav-mobile-hide" style="font-size:11px;color:#aaa;margin-left:8px;">Sign out</a>
        </div></nav>"""
    return f"""<nav class="app-nav"><div class="app-nav-inner">
      <a href="/" class="app-logo">{_LOGO_SVG} calyx</a>
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
<meta property="og:image" content="{og_image}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:type" content="image/png">
<meta property="og:image:alt" content="{og_title}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{og_image}">'''
    if og_url:
        og_tags += f'\n<meta property="og:url" content="{og_url}">'
        og_tags += f'\n<link rel="canonical" href="{og_url}">'
    html = LAYOUT_STYLE.replace("__TITLE__", title).replace("__OG_TAGS__", og_tags)
    phone_banner = ""
    if user and not (user.get("phone") or "").strip():
        phone_banner = (
            '<div style="background:#fbf6f3;border-bottom:1px solid #e6cdc1;padding:10px 20px;font-size:13px;color:#5a2a18;text-align:center;">'
            '<strong style="font-weight:700;color:#8a3f25;">📱 One thing left</strong> — '
            '<a href="/taste-profile#settings" style="color:#c4734f;font-weight:600;text-decoration:none;border-bottom:1px dashed #c4734f;">add your phone</a> '
            'so group-mates can reach you.'
            '</div>'
        )
    return html + nav + phone_banner + '<div class="app-content">' + body + LAYOUT_FOOT


LAYOUT_STYLE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#4a6741">
<meta name="apple-mobile-web-app-capable" content="yes">
<link rel="icon" type="image/svg+xml" href="/static/favicon-v2.svg">
<link rel="apple-touch-icon" href="/static/favicon-v2.svg">
<link rel="manifest" href="/static/manifest.json">
__OG_TAGS__
<title>Calyx — __TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { overflow-x: hidden; }
  body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #fff; color: #111; font-size: 14px; line-height: 1.55; min-height: 100vh;
         -webkit-text-size-adjust: 100%; }
  img, video, iframe { max-width: 100%; height: auto; }
  /* --- App shell --- */
  .app-nav { background: #fff; padding: 0 20px; position: sticky; top: 0; z-index: 100; border-bottom: 2px solid #4a6741; }
  .app-nav-inner { display: flex; align-items: center; max-width: 960px; margin: 0 auto; height: 56px; gap: 4px; min-width: 0; }
  .app-logo { font-size: 20px; font-weight: 800; color: #4a6741; text-decoration: none; letter-spacing: -.8px; margin-right: auto; text-transform: lowercase; display: flex; align-items: center; gap: 6px; flex-shrink: 0; min-height: 44px; }
  .app-logo svg { width: 22px; height: 22px; }
  .app-logo:hover { text-decoration: none; opacity: .85; }
  .app-nav a.nav-link { font-size: 13px; font-weight: 500; color: #666; text-decoration: none; padding: 12px 14px; letter-spacing: .3px; text-transform: uppercase; transition: color .15s; min-height: 44px; display: inline-flex; align-items: center; }
  .app-nav a.nav-link:hover { color: #4a6741; text-decoration: none; }
  .app-nav a.nav-link.active { color: #4a6741; font-weight: 700; border-bottom: 2px solid #4a6741; margin-bottom: -2px; }
  .nav-divider { width: 1px; height: 20px; background: #ddd; margin: 0 8px; }
  .nav-user-name { font-size: 12px; color: #888; font-weight: 500; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .app-content { max-width: 960px; margin: 0 auto; padding: 32px 20px 60px; }
  /* --- Shared components --- */
  /* --- Botanical color system: sage (#4a6741) + terracotta (#c4734f) --- */
  h1 { margin-bottom: 24px; color: #1a1a1a; font-size: 2rem; font-weight: 800; letter-spacing: -.5px; }
  h2 { margin: 28px 0 16px; color: #4a6741; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; }
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
  .btn-secondary:hover { background: #edf2eb; }
  .btn-pill { padding: 6px 16px; font-size: 12px; }
  /* --- Mobile (≤768px): primary breakpoint, since most users are on phones --- */
  @media (max-width: 768px) {
    body { font-size: 15px; }
    /* Nav: tighter padding, hide secondary chrome (name + sign-out moved to /profile) */
    .app-nav { padding: 0 12px; }
    .app-nav-inner { height: 52px; gap: 2px; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
    .app-nav-inner::-webkit-scrollbar { display: none; }
    .app-nav a.nav-link { font-size: 12px; padding: 10px 10px; }
    .nav-divider, .nav-user-name, .nav-mobile-hide { display: none !important; }
    .app-logo { font-size: 18px; }
    /* Content padding tighter */
    .app-content { padding: 20px 14px 80px; }
    h1 { font-size: 1.5rem; margin-bottom: 18px; }
    h2 { margin: 22px 0 12px; }
    .card { padding: 18px 16px; }
    /* Bigger tap targets */
    .btn-primary, .btn-secondary { padding: 12px 18px; font-size: 14px; min-height: 44px; }
    .btn-pill { padding: 8px 14px; font-size: 12px; min-height: 36px; }
    button, input[type=submit], input[type=button] { min-height: 44px; }
    input[type=text], input[type=email], input[type=url], input[type=date], input[type=time],
    input[type=number], input[type=tel], select, textarea { min-height: 44px; font-size: 16px; /* prevents iOS zoom */ }
    /* Forms wrap on mobile */
    form { width: 100%; }
    form > div, form .form-row { flex-wrap: wrap !important; }
    form input, form select, form textarea { min-width: 0 !important; max-width: 100% !important; }
    /* Tables: horizontal scroll */
    table { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; }
    /* Generic flex rows wrap */
    .stack-mobile { flex-direction: column !important; align-items: stretch !important; }
    .stack-mobile > * { width: 100% !important; }
  }
  @media (max-width: 480px) {
    .app-content { padding: 16px 12px 80px; }
    .card { padding: 14px 12px; }
    h1 { font-size: 1.35rem; }
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
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/static/sw.js').catch(() => {});
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
    from calyx.models import RankedEvent, Event, EventSource

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
            <td>Run #{r['id']}{wip}</td>
            <td>{r['timestamp'][:16]}</td>
            <td>{r['event_count'] or 0}</td>
            <td>{score_badge(r['top_score'])}</td>
            <td>${r['cost_total']:.4f}</td>
            <td>{r['model_used'] or ''}</td>
        </tr>"""
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
    <p style="font-size:12px;color:#9ca3af;margin-top:24px;">Schedule is managed via <code>scripts/install_cron.sh</code>.</p>
    """
    return HTMLResponse(_layout("Admin", body, current_user))


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


@app.get("/profile")
async def profile_redirect():
    return RedirectResponse("/taste-profile#settings")




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
    if "phone" in body:
        import re as _re
        raw = (body.get("phone") or "").strip()[:30]
        cleaned = _re.sub(r"[^\d+\-\s()]", "", raw)
        updates.append("phone = ?")
        params.append(cleaned)
    if "location" in body and body["location"]:
        location_text = body["location"].strip()
        updates.append("location_query = ?")
        params.append(location_text)
        # Try to geocode the location to set lat/lon automatically
        try:
            from calyx.events.geocoder import _geocode_query
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
    if "filter_work_hours" in body:
        updates.append("filter_work_hours = ?")
        params.append(1 if body["filter_work_hours"] else 0)
    if "feed_include_recs" in body:
        updates.append("feed_include_recs = ?")
        params.append(1 if body["feed_include_recs"] else 0)
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
    show_welcome = request.query_params.get("welcome") == "1"
    user_id = current_user["id"]
    spotify_connected = bool(current_user.get("spotify_token_file"))
    youtube_connected = bool(current_user.get("youtube_token_file"))

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
    _tag_colors = ["#4a6741", "#c4734f", "#5b7fa5", "#8b6b47", "#7a5c8a", "#5a8a6e"]
    def _tags(items: list[str], color: str = "") -> str:
        if not items:
            return '<span style="color:#ccc;font-size:13px;">Nothing yet</span>'
        tags = []
        for i, item in enumerate(items[:30]):
            c = color or _tag_colors[i % len(_tag_colors)]
            tags.append(f'<span style="display:inline-block;padding:5px 14px;margin:3px;background:{c}10;color:{c};font-size:13px;font-weight:600;border-left:3px solid {c};">{item}</span>')
        return " ".join(tags)

    # Build interest tags grouped by source
    algo_interests = [i for i in interests if "manual" not in str(i.get("source_signals", []))]
    algo_tags = [i["topic"] for i in sorted(algo_interests, key=lambda x: -x.get("confidence", 0))]

    # Settings data
    name = current_user.get("name") or ""
    email = current_user.get("email") or ""
    email_digest = current_user.get("email_digest", 1)
    digest_checked = "checked" if email_digest else ""
    is_admin = current_user.get("id") == 1
    admin_html = '<div style="margin-top:20px;"><a href="/admin" style="font-size:12px;color:#888;">Admin</a> &middot; <a href="/admin/sources" style="font-size:12px;color:#888;">Sources</a></div>' if is_admin else ""

    # Calendar subscribe URLs (single feed: all RSVP'd events from groups + discover)
    user_token = current_user.get("user_token", "") or ""
    feed_url = f"{settings.dashboard_url}/u/{user_token}/feed.ics"
    gcal_url = f"https://calendar.google.com/calendar/r?cid={feed_url.replace('https://', 'http://')}"

    body = f"""
<style>
.you-page{{max-width:620px;margin:0 auto;padding:40px 0 80px}}
.you-page h1{{font-size:2rem;font-weight:800;color:#000;margin-bottom:16px;letter-spacing:-.5px}}
.you-tabs{{display:flex;gap:0;border-bottom:2px solid #e0e0e0;margin-bottom:28px}}
.you-tab{{padding:10px 20px;font-size:13px;font-weight:600;color:#888;cursor:pointer;border:none;background:none;font-family:inherit;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s;text-transform:uppercase;letter-spacing:.5px}}
.you-tab:hover{{color:#000}}
.you-tab.active{{color:#4a6741;border-bottom-color:#4a6741}}
.you-panel{{display:none}}
.you-panel.active{{display:block}}
.taste-section{{margin-bottom:28px}}
.taste-section h2{{font-size:10px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:2px;margin:0 0 12px}}
.taste-section .tags{{line-height:2.2}}
.field{{margin-bottom:14px}}
.field label{{display:block;font-size:12px;font-weight:600;color:#333;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px}}
.field input[type=text],.field input[type=tel],.field input[type=email],.field input[type=url],.field input[type=number]{{width:100%;padding:10px 12px;border:1px solid #ccc;font-size:14px;font-family:inherit;outline:none;background:#fff;color:#1a1a1a;transition:border-color .12s, box-shadow .12s;box-sizing:border-box}}
.field input:focus{{border-color:#4a6741;box-shadow:0 0 0 3px rgba(74,103,65,.12)}}
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
<div class="you-page">
  <div style="position:relative;overflow:hidden;">
    <svg style="position:absolute;right:-20px;top:-10px;opacity:.07;pointer-events:none;" width="180" height="180" viewBox="0 0 100 100"><path d="M50 5C35 25 10 35 10 60c0 22 18 35 40 35s40-13 40-35C90 35 65 25 50 5z" fill="#4a6741"/><path d="M50 20C40 35 20 42 20 58c0 17 13 27 30 27s30-10 30-27C80 42 60 35 50 20z" fill="#4a6741"/></svg>
    <h1 style="position:relative;">You</h1>
  </div>

  <div class="you-tabs">
    <button class="you-tab" onclick="switchYouTab('taste')">Taste</button>
    <button class="you-tab active" onclick="switchYouTab('settings')">Settings</button>
  </div>

  <!-- Taste tab -->
  <div id="you-taste" class="you-panel">
    <div class="taste-section">
      <h2>Tell us about yourself</h2>
      <p style="font-size:13px;color:#888;margin-bottom:10px;">Paste anything — your YouTube feed, bands you like, hobbies. We'll figure it out.</p>
      <textarea id="paste-box" placeholder="e.g. I love indie rock, just saw Magdalena Bay, really into climbing and art museums lately..." style="width:100%;min-height:80px;padding:10px 12px;border:1px solid #ccc;font-size:14px;font-family:inherit;resize:vertical;outline:none;box-sizing:border-box;"></textarea>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;">
        <span id="paste-status" style="font-size:12px;color:#888;"></span>
        <button onclick="submitPaste()" class="btn-primary" id="paste-btn" style="padding:8px 16px;">Save</button>
      </div>
    </div>

    {"<div class='taste-section'><h2>Your interests</h2><div class='tags'>" + _tags(algo_tags + manual + paste_keywords) + "</div></div>" if (algo_tags or manual or paste_keywords) else ""}

    {"<div class='taste-section'><h2>Music (from Spotify)</h2><div class='tags'>" + _tags(spotify_artists, "#8b6914") + "</div></div>" if spotify_artists else ""}

    {"<div class='taste-section'><h2>YouTube</h2><div class='tags'>" + _tags(youtube_subs, "#c4302b") + "</div></div>" if youtube_subs else ""}
  </div>

  <!-- Settings tab -->
  <div id="you-settings" class="you-panel active">
    <div style="border:1px solid #e0e0e0;padding:20px;margin-bottom:20px;">
      <div class="field"><label>Name</label><input type="text" id="name" value="{name}"></div>
      <div class="field"><label>Email</label><input type="text" id="email" value="{email}" disabled style="background:#f9fafb;color:#999"></div>
      <div class="field">
        <label>Phone</label>
        <input type="tel" id="phone" value="{current_user.get('phone') or ''}" placeholder="+1 555 123 4567">
      </div>
      <button onclick="save()" class="btn-primary" style="padding:8px 16px;">Save</button>
      <div id="success" style="display:none;border:1px solid #4a6741;color:#4a6741;padding:10px 14px;font-size:13px;margin-top:12px;">Saved!</div>
    </div>

    <div style="border:1px solid #e0e0e0;padding:20px;margin-bottom:20px;">
      <div class="toggle-row">
        <div class="toggle-label">
          <div>Weekly email digest</div>
          <div>Personalized event picks in your inbox</div>
        </div>
        <label class="toggle"><input type="checkbox" id="digest-toggle" {digest_checked} onchange="toggleDigest(this.checked)"><span class="slider"></span></label>
      </div>
      <div class="toggle-row" style="margin-top:12px;padding-top:12px;border-top:1px solid #f0f0f0;">
        <div class="toggle-label">
          <div>Hide weekday 9-5 events</div>
          <div>Filter out events during work/class hours (Mon-Fri 9am-5pm)</div>
        </div>
        <label class="toggle"><input type="checkbox" id="workhours-toggle" {"checked" if current_user.get("filter_work_hours", 1) else ""} onchange="toggleWorkHours(this.checked)"><span class="slider"></span></label>
      </div>
    </div>

    {('<div style="border:1.5px solid #4a6741;background:#edf2eb;padding:16px 18px;margin-bottom:16px;"><div style="font-size:11px;font-weight:700;color:#4a6741;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px;">◉ Welcome to Calyx</div><div style="font-size:14px;color:#1a1a1a;line-height:1.5;">Take 30 seconds and subscribe your real calendar below. Every event you RSVP <em>going</em> to — and what your group-mates are going to — flows in automatically.</div></div>') if show_welcome else ''}
    <div style="border:1px solid #e0e0e0;padding:20px;margin-bottom:20px;{'border-color:#4a6741;border-width:2px;' if show_welcome else ''}">
      <h2 style="margin:0 0 6px;">Your calendar</h2>
      <p style="font-size:13px;color:#666;margin-bottom:14px;line-height:1.5;">Subscribe to see every event you've RSVP'd <em>going</em> to plus what your group-mates are going to — in your real calendar.</p>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;">
        <a href="{gcal_url}" target="_blank" class="btn-primary" style="text-decoration:none;font-size:12px;padding:10px 16px;">Add to Google Calendar</a>
        <button type="button" onclick="navigator.clipboard.writeText(&apos;{feed_url}&apos;);this.textContent=&apos;✓ Copied&apos;;setTimeout(()=>this.textContent=&apos;Copy iCal URL&apos;,1500);" class="btn-secondary" style="font-size:12px;padding:10px 16px;">Copy iCal URL</button>
      </div>
      <div class="toggle-row" style="padding-top:12px;border-top:1px solid #f0f0f0;">
        <div class="toggle-label">
          <div>Include recommendations</div>
          <div>Up to 2 top-scored discoveries per day, marked with ★</div>
        </div>
        <label class="toggle"><input type="checkbox" id="feed-recs-toggle" {"checked" if current_user.get("feed_include_recs") else ""} onchange="toggleFeedPref('feed_include_recs', this.checked)"><span class="slider"></span></label>
      </div>
    </div>

    <div style="border:1px solid #e0e0e0;overflow:hidden;margin-bottom:20px;">
      <div style="padding:20px 20px 12px;"><h2 style="margin:0 0 8px;">Connected Services</h2></div>
      <div class="svc-row" style="border-top:1px solid #e0e0e0;">
        <div><span style="font-weight:700;font-size:14px;color:#000;">Spotify</span><br><span style="font-size:12px;color:#888;">{"Connected" if spotify_connected else "Top artists and listening history"}</span></div>
        {"<span style='font-size:12px;color:#888;font-weight:600;'>Connected</span>" if spotify_connected else '<a href="/auth/spotify" style="padding:6px 14px;background:#4a6741;color:#fff;font-size:11px;font-weight:700;text-decoration:none;text-transform:uppercase;letter-spacing:.5px;">Connect</a>'}
      </div>
      <div class="svc-row">
        <div><span style="font-weight:700;font-size:14px;color:#000;">YouTube</span><br><span style="font-size:12px;color:#888;">{"Connected" if youtube_connected else "Subscriptions and liked videos"}</span></div>
        {"<span style='font-size:12px;color:#888;font-weight:600;'>Connected</span>" if youtube_connected else '<a href="/auth/youtube" style="padding:6px 14px;background:#4a6741;color:#fff;font-size:11px;font-weight:700;text-decoration:none;text-transform:uppercase;letter-spacing:.5px;">Connect</a>'}
      </div>
    </div>

    {admin_html}
  </div>
</div>

<script>
function switchYouTab(tab) {{
  document.querySelectorAll('.you-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.you-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('you-' + tab).classList.add('active');
  event.target.classList.add('active');
  history.replaceState(null, '', '#' + tab);
}}
(function() {{
  const h = window.location.hash.slice(1);
  if (h === 'taste') {{
    document.querySelectorAll('.you-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.you-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('you-taste').classList.add('active');
    document.querySelectorAll('.you-tab')[0].classList.add('active');
  }}
}})();

function save() {{
  fetch('/api/profile/update', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      name: document.getElementById('name').value.trim(),
      phone: document.getElementById('phone').value.trim(),
    }}),
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

function toggleWorkHours(on) {{
  fetch('/api/profile/update', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ filter_work_hours: on ? 1 : 0 }}),
  }});
}}

function toggleFeedPref(field, on) {{
  fetch('/api/profile/update', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ [field]: on ? 1 : 0 }}),
  }});
}}

function submitPaste() {{
  const text = document.getElementById('paste-box').value.trim();
  if (!text) return;
  const btn = document.getElementById('paste-btn');
  const status = document.getElementById('paste-status');
  btn.disabled = true; btn.textContent = 'Processing...'; status.textContent = '';
  fetch('/api/profile/paste-interests', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ text: text }}),
  }}).then(r => r.json()).then(d => {{
    btn.disabled = false; btn.textContent = 'Save';
    if (d.ok) {{
      status.textContent = d.summary || 'Saved!';
      status.style.color = '#4a6741';
      document.getElementById('paste-box').value = '';
      setTimeout(() => location.reload(), 1500);
    }} else {{
      status.textContent = d.error || 'Failed'; status.style.color = '#d00';
    }}
  }}).catch(() => {{
    btn.disabled = false; btn.textContent = 'Save';
    status.textContent = 'Network error'; status.style.color = '#d00';
  }});
}}
</script>
"""
    return HTMLResponse(_layout("You", body, current_user))


@app.get("/favicon.svg")
async def favicon_svg():
    """Serve the Calyx logo as a tab favicon. SVG works in all modern browsers."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#4a6741"/>'
        '<path d="M16 7c-3 3.5-6.5 5-6.5 9 0 3.5 3 6 6.5 6s6.5-2.5 6.5-6c0-4-3.5-5.5-6.5-9z" fill="#fff" opacity=".25"/>'
        '<path d="M16 11c-2 2.5-4 3.5-4 6 0 2 1.8 3.8 4 3.8s4-1.8 4-3.8c0-2.5-2-3.5-4-6z" fill="#fff"/>'
        '<path d="M16 15v6" stroke="#4a6741" stroke-width="1.2" stroke-linecap="round"/>'
        '</svg>'
    )
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.ico")
async def favicon_ico():
    """Some browsers/clients fetch /favicon.ico regardless of <link> hints; just redirect."""
    return RedirectResponse("/static/favicon.svg", status_code=301)


@app.get("/landing", response_class=HTMLResponse)
async def landing_page(request: Request):
    current_user = _get_current_user(request)
    body = """
<style>
  .lp { max-width: 520px; margin: 0 auto; padding: 96px 24px 64px; }
  .lp h1 { font-size: 2.4rem; font-weight: 800; color: #1a1a1a; letter-spacing: -1.5px; line-height: 1.05; margin: 0 0 18px; }
  .lp h1 em { font-style: normal; color: #4a6741; }
  .lp p.sub { font-size: 16px; color: #555; line-height: 1.55; margin: 0 0 36px; max-width: 440px; }
  .lp .cta { display: inline-block; padding: 14px 28px; background: #4a6741; color: #fff; text-decoration: none; font-weight: 700; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; transition: background .15s; }
  .lp .cta:hover { background: #3a5334; }
  .lp .foot { margin-top: 80px; font-size: 11px; color: #aaa; letter-spacing: 1.5px; text-transform: uppercase; }
</style>
<div class="lp">
  <h1>A shared calendar<br>for the people<br>you <em>actually see</em>.</h1>
  <p class="sub">Drop an event. Everyone in the group RSVPs and it syncs to their calendar. That&rsquo;s it.</p>
  __CTA__
  <div class="foot">Calyx</div>
</div>
"""
    if current_user:
        cta = '<a href="/groups" class="cta">Open your groups</a>'
    else:
        cta = '<a href="/login" class="cta">Sign in to start</a>'
    return HTMLResponse(_layout("Calyx", body.replace("__CTA__", cta), current_user))


@app.get("/")
async def home_redirect(request: Request):
    user = _get_current_user(request)
    if user:
        resp = RedirectResponse("/groups", status_code=302)
        return _maybe_set_cookie(request, resp, user)
    return RedirectResponse("/landing", status_code=302)


_search_cache: dict[str, tuple[float, list]] = {}  # query → (timestamp, web_results)

@app.post("/api/search", response_class=JSONResponse)
async def api_search(request: Request):
    """Tiered event search: DB first, then web search fallback (cached 1hr)."""
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

    # Tier 2: Web search fallback (cached 1hr)
    import time as _time
    web_results = []
    cache_key = query.lower().strip()
    cached = _search_cache.get(cache_key)
    if cached and _time.time() - cached[0] < 3600:
        web_results = cached[1]
    elif settings.anthropic_api_key and len(db_matches) < 3:
        try:
            import anthropic, json as _json, re as _re
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
                messages=[{"role": "user", "content":
                    f"Search: {query} Boston Cambridge MA. "
                    f"Return JSON array only, no explanation: [{{\"title\":\"\",\"date\":\"\",\"location\":\"\",\"url\":\"\",\"description\":\"\"}}]"}],
            )
            # Extract text from response
            text = ""
            for block in resp.content:
                if hasattr(block, "text"):
                    text += block.text
            # Try JSON array first
            json_match = _re.search(r'\[.*\]', text, _re.DOTALL)
            if json_match:
                try:
                    events_raw = _json.loads(json_match.group())
                    for ev in events_raw[:6]:
                        web_results.append({
                            "title": ev.get("title", ""),
                            "start_time": ev.get("date", ""),
                            "location": ev.get("location", ""),
                            "url": ev.get("url", ""),
                            "score": 0,
                            "match_reason": ev.get("description", "")[:120],
                            "source": "web",
                        })
                except _json.JSONDecodeError:
                    pass
            # Fallback: if no JSON results, extract URLs and names from prose
            if not web_results and text:
                urls = _re.findall(r'(https?://[^\s\)\"\'<>]+)', text)
                # Use Claude's text as a single helpful result
                summary = text.strip()[:200]
                if urls:
                    for url in urls[:4]:
                        # Extract a title near the URL
                        idx = text.find(url)
                        context = text[max(0,idx-80):idx].strip()
                        title = context.split(".")[-1].strip().split(",")[-1].strip() or url.split("/")[2]
                        web_results.append({
                            "title": title[:60],
                            "start_time": "",
                            "location": "Boston area",
                            "url": url,
                            "score": 0,
                            "match_reason": "",
                            "source": "web",
                        })
                elif summary:
                    web_results.append({
                        "title": f"Web results for \"{query}\"",
                        "start_time": "",
                        "location": "",
                        "url": "",
                        "score": 0,
                        "match_reason": summary,
                        "source": "web",
                    })
        except Exception as exc:
            logger.exception("Web search failed for query: %s", query)
        # Cache web results
        if web_results:
            _search_cache[cache_key] = (_time.time(), web_results)

    # Merge: DB results first, then web results
    merged = db_matches[:5] + web_results[:5]

    # Ingest web results into DB so they're searchable and RSVPable in future
    if web_results:
        for wr in web_results:
            if not wr.get("title"):
                continue
            import hashlib
            eid = "web_" + hashlib.md5(f"{wr['title']}{wr.get('start_time','')}".encode()).hexdigest()[:12]
            # Check if already exists
            existing = db.conn.execute("SELECT id FROM events WHERE event_id=? LIMIT 1", (eid,)).fetchone()
            if not existing:
                # Find the latest run to attach to
                latest_run = db.conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
                run_id_for_web = latest_run["id"] if latest_run else 1
                db.conn.execute(
                    """INSERT INTO events (run_id, event_id, source, title, description, url, start_time,
                       location_name, location_address) VALUES (?, ?, 'web_search', ?, ?, ?, ?, ?, 'Boston, MA')""",
                    (run_id_for_web, eid, wr["title"], wr.get("match_reason", ""), wr.get("url", ""),
                     wr.get("start_time", ""), wr.get("location", "")),
                )
            wr["id"] = eid  # Add ID so frontend can RSVP
        db.conn.commit()

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

        # Email the retro to admin
        try:
            from calyx.email.sender import send_email as _send
            subject = f"[Calyx] Search gap: {query}"
            html = f"""<div style="font-family:Inter,system-ui,sans-serif;max-width:500px;">
                <div style="border-left:3px solid #c4734f;padding:12px 16px;margin-bottom:16px;">
                    <div style="font-size:11px;font-weight:700;color:#c4734f;text-transform:uppercase;letter-spacing:1px;">Search Gap Detected</div>
                    <div style="font-size:18px;font-weight:800;color:#1a1a1a;margin-top:4px;">{query}</div>
                </div>
                <div style="font-size:13px;color:#555;line-height:1.6;">
                    <p><strong>Your DB:</strong> {len(db_results)} results &nbsp;|&nbsp; <strong>Web:</strong> {len(web_results)} results</p>
                    <p style="background:#f8f8f8;padding:12px;border:1px solid #eee;margin-top:12px;">{diagnosis}</p>
                </div>
                <div style="margin-top:16px;font-size:12px;color:#888;">
                    <a href="{settings.dashboard_url}/admin" style="color:#4a6741;">View admin dashboard</a>
                </div>
            </div>"""
            _send(subject, html, settings)
        except Exception:
            pass

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

    # Group events the user is a member of (surfaced alongside discoveries)
    group_events = db.get_upcoming_group_events_for_user(user_id) if user_id else []

    if not kept and not group_events:
        return HTMLResponse(_layout("Discover", "<h1>Discover</h1><div class='card'><p>No scored events yet. Pipeline may still be running.</p></div>", current_user))

    # Find run_id for RSVP API calls
    run_id = kept[0].get("run_id", 0) if kept else 0

    # Fetch RSVPs (include group event IDs so we can show going/maybe state on group cards too)
    all_event_ids = [e.get("event_id", "") for e in kept if e.get("event_id")]
    all_event_ids += [f"grp_evt_{ge['id']}" for ge in group_events]
    rsvps_map = db.get_rsvps_for_events(all_event_ids)
    user_token = current_user["user_token"] if current_user else ""

    # Build JSON event array for JS — group events first (highest priority)
    events_json = []
    for ge in group_events:
        grp_eid = f"grp_evt_{ge['id']}"
        evt_rsvps = rsvps_map.get(grp_eid, [])
        rsvp_list = [{"user_name": rv["user_name"], "status": rv["status"]} for rv in evt_rsvps]
        my_rsvp = ""
        if current_user:
            for rv in evt_rsvps:
                if rv.get("user_id") == current_user["id"]:
                    my_rsvp = rv["status"]
                    break
        events_json.append({
            "id": grp_eid,
            "title": ge.get("title", ""),
            "start": ge.get("start_time") or "",
            "end": ge.get("end_time") or "",
            "url": ge.get("url") or "",
            "score": 95,
            "vibe": "social",
            "location": ge.get("location") or "",
            "price": "",
            "description": (ge.get("notes") or "")[:200],
            "match_reason": f"From {ge.get('group_display_name') or 'your group'}",
            "event_type": "group",
            "group_id": ge.get("g_id"),
            "group_name": ge.get("group_display_name") or "Group",
            "rsvps": rsvp_list,
            "my_rsvp": my_rsvp,
            "primary": True,
            "source": ge.get("group_display_name") or "Group",
            "image_url": "",
            "lat": None,
            "lon": None,
            "scores": {"interest": 14, "social": 15, "urgency": 12, "logistics": 14, "friend": 15, "discovery": 6, "quality": 13},
        })

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
      .view-toggle button {{ padding: 11px 18px; border: none; background: transparent; cursor: pointer; font-size: 12px; font-weight: 500; color: #888; transition: all .15s; text-transform: uppercase; letter-spacing: .5px; min-height: 40px; }}
      .view-toggle button.active {{ background: #000; color: #fff; }}
      .score-badge {{ display: inline-block; font-weight: 700; padding: 3px 10px; font-size: 13px; border-radius: 4px; }}
      .score-high {{ background: #4a6741; color: #fff; }}
      .score-mid {{ background: #e8ede7; color: #4a6741; }}
      .score-low {{ background: #f0eeeb; color: #999; }}
      .rsvp-btn {{ font-size: 11px; padding: 5px 14px; border: 1px solid #ccc; background: white; cursor: pointer; color: #888; font-weight: 700; transition: all .15s; text-transform: uppercase; letter-spacing: .3px; }}
      .rsvp-btn:hover {{ color: #4a6741; border-color: #4a6741; }}
      .rsvp-btn.going.active {{ background: #4a6741; color: #fff; border-color: #4a6741; }}
      .rsvp-btn.maybe.active {{ background: #edf2eb; color: #4a6741; border-color: #4a6741; }}
      .rsvp-btn.no:hover {{ color: #c4734f; border-color: #c4734f; }}
      .rsvp-btn.no.active {{ background: #f5f1ee; color: #c4734f; border-color: #c4734f; }}
      .filter-chip {{ font-size: 11px; padding: 5px 12px; border: 1px solid #e0e0e0; background: white; cursor: pointer; color: #888; font-weight: 600; transition: all .15s; text-transform: uppercase; letter-spacing: .3px; }}
      .filter-chip:hover {{ border-color: #4a6741; color: #4a6741; }}
      .filter-chip.active {{ border-color: #4a6741; color: #fff; background: #4a6741; }}
      /* --- Card list view --- */
      #list-view {{ display: none; }}
      .day-group {{ margin-bottom: 28px; }}
      .day-header {{ position: sticky; top: 56px; background: #fff; padding: 12px 0 8px; font-size: 11px; font-weight: 700; color: #4a6741; z-index: 10; border-bottom: 2px solid #4a6741; display: flex; justify-content: space-between; align-items: baseline; text-transform: uppercase; letter-spacing: 1.5px; }}
      .day-header .day-count {{ font-size: 11px; font-weight: 500; color: #888; text-transform: none; letter-spacing: 0; }}
      .see-more-btn {{ display: block; width: 100%; margin: 6px 0 10px; padding: 10px; background: #fff; border: 1px solid #e0e0e0; color: #c4734f; font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit; text-align: center; transition: all .15s; text-transform: uppercase; letter-spacing: .5px; }}
      .see-more-btn:hover {{ background: #fdf5f2; border-color: #c4734f; }}
      .see-more-collapse {{ color: #888; }}
      .see-more-collapse:hover {{ background: #f5f5f5; color: #000; }}
      .evt-card {{ background: white; margin: 4px 0; border: 1px solid #eee; border-left: 3px solid #ddd; transition: all .15s; cursor: pointer; overflow: hidden; display: flex; }}
      .evt-card:hover {{ background: #f8faf7; border-left-color: #4a6741; }}
      .evt-card.score-high-card {{ border-left-color: #4a6741; }}
      .evt-card.score-mid-card {{ border-left-color: #c4734f; }}
      .evt-card.rsvp-going-card {{ border-left: 4px solid #4a6741; background: #f8faf7; }}
      .evt-card.rsvp-maybe-card {{ border-left-color: #888; }}
      .evt-card.group-card {{ border-left: 4px solid #4a6741; background: linear-gradient(to right, #f4f7f2 0%, #fff 80%); }}
      .group-pill {{ display: inline-block; font-size: 10px; font-weight: 700; color: #4a6741; background: #edf2eb; padding: 3px 9px; border-radius: 999px; text-decoration: none; letter-spacing: .8px; text-transform: uppercase; margin-bottom: 6px; }}
      .group-pill:hover {{ background: #d4e0d1; text-decoration: none; }}
      .card-score.group-tag {{ background: #4a6741; color: #fff; font-size: 10px; padding: 3px 8px; letter-spacing: 1px; }}
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
      .map-tbtn {{ padding: 8px 18px; border: none; background: #fff; cursor: pointer; font-size: 12px; font-weight: 600; color: #888; font-family: inherit; transition: all .15s; }}
      .map-tbtn:hover {{ color: #4a6741; }}
      .map-tbtn.active {{ background: #4a6741; color: #fff; }}
      .map-tbtn + .map-tbtn {{ border-left: 1px solid #e0e0e0; }}
      #timeline-view {{ display: none; padding-bottom: 8px; margin: 0 -20px; padding: 0 20px; }}
      @media (min-width: 1100px) {{ #timeline-view {{ margin: 0 calc(-50vw + 480px); padding: 0 calc(50vw - 480px); }} }}
      .timeline-week {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 1px; background: #e0e0e0; }}
      .timeline-col {{ background: #fff; min-width: 0; }}
      .timeline-col-header {{ background: #fff; padding: 10px 10px 8px; border-bottom: 2px solid #e0e0e0; text-align: center; }}
      .timeline-col-header .col-day {{ font-size: 10px; font-weight: 700; color: #888; text-transform: uppercase; letter-spacing: 1px; }}
      .timeline-col-header .col-count {{ font-size: 10px; font-weight: 600; background: #edf2eb; color: #4a6741; padding: 1px 6px; display: inline-block; margin-top: 4px; }}
      .timeline-col-header.today {{ border-bottom-color: #4a6741; background: #f8faf7; }}
      .timeline-col-header.today .col-day {{ color: #4a6741; }}
      .tl-card {{ background: white; padding: 8px 10px; margin: 0; border-bottom: 1px solid #f0f0f0; cursor: pointer; transition: background .15s; }}
      .tl-card:hover {{ background: #f8faf7; }}
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
        <button id="btn-map" onclick="switchView('map')">Map</button>
      </div>
    </div>

    <div style="position:relative;margin-bottom:8px;">
      <input id="search-input" type="text" placeholder="Try &quot;jazz tonight&quot; or &quot;outdoor things this weekend&quot;" oninput="onSearchInput()" onkeydown="if(event.key==='Enter'){{event.preventDefault();doSearch();}}"
             style="width:100%;padding:12px 14px;border:1px solid #ccc;border-bottom:2px solid #ccc;font-size:14px;font-family:inherit;outline:none;box-sizing:border-box;transition:border-color .15s;"
             onfocus="this.style.borderBottomColor='#4a6741'" onblur="this.style.borderBottomColor='#ccc'">
    </div>
    <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#888;margin-bottom:16px;cursor:pointer;">
      <input id="show-dismissed" type="checkbox" onchange="applyFilters()" style="margin:0;">
      Show events I said no to
    </label>
    <div id="search-status" style="display:none;padding:16px 20px;margin-bottom:16px;background:#4a6741;font-size:15px;font-weight:700;color:#fff;text-align:center;animation:pulse 1.5s ease-in-out infinite;">
      Searching the web for &ldquo;<span id="search-status-query"></span>&rdquo;
    </div>
    <style>@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.7; }} }}</style>
    <div id="web-results-section" style="display:none;margin-bottom:16px;border-left:3px solid #c4734f;padding-left:16px;">
      <div style="font-size:11px;font-weight:700;color:#c4734f;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Found on the web</div>
      <div id="web-results-list"></div>
    </div>
    <input type="hidden" id="score-slider" value="0">
    <input type="hidden" id="dist-slider" value="50">
    <span id="score-label" style="display:none">0</span>
    <span id="dist-label" style="display:none">Any</span>

    <div id="cal-view" style="display:none"><div id="fc-container"></div></div>
    <div id="list-view"></div>
    <div id="timeline-view">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <button onclick="_weekOffset--;buildTimelineView()" style="background:none;border:1px solid #e0e0e0;padding:6px 14px;cursor:pointer;font-size:13px;font-weight:600;color:#4a6741;">&larr; Prev</button>
        <span id="tl-week-label" style="font-size:13px;font-weight:700;color:#4a6741;text-transform:uppercase;letter-spacing:1px;"></span>
        <button onclick="_weekOffset++;buildTimelineView()" style="background:none;border:1px solid #e0e0e0;padding:6px 14px;cursor:pointer;font-size:13px;font-weight:600;color:#4a6741;">Next &rarr;</button>
      </div>
      <div class="timeline-week" id="tl-week"></div>
    </div>
    <div id="heat-view" style="display:none"></div>
    <div id="map-view" style="display:none;margin:0 -20px;">
      <div style="position:relative;">
        <div id="event-map" style="height:70vh;width:100%;"></div>
        <div id="map-time-btns" style="position:absolute;bottom:16px;left:50%;transform:translateX(-50%);z-index:1000;display:flex;gap:0;background:#fff;border:1px solid #e0e0e0;box-shadow:0 2px 8px rgba(0,0,0,.12);overflow:hidden;">
          <button onclick="selectMapDay('today')" class="map-tbtn active" id="map-tb-today">Today</button>
          <button onclick="selectMapDay('week')" class="map-tbtn" id="map-tb-week">This week</button>
          <button onclick="selectMapDay('all')" class="map-tbtn" id="map-tb-all">All</button>
        </div>
        <div id="map-panel" style="display:none;position:absolute;top:12px;right:12px;width:300px;max-height:calc(70vh - 24px);overflow-y:auto;background:#fff;border:1px solid #e0e0e0;padding:16px;z-index:1000;box-shadow:0 2px 12px rgba(0,0,0,.1);"></div>
      </div>
    </div>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css">
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css">
    <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>

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
    let _searchAbort = null;

    function onSearchInput() {{
      const query = document.getElementById('search-input').value.trim();
      applyFilters();
      document.getElementById('web-results-section').style.display = 'none';
      if (!query) {{
        document.getElementById('search-status').style.display = 'none';
        return;
      }}
      clearTimeout(_searchTimeout);
      if (query.length >= 3) {{
        document.getElementById('search-status-query').textContent = query;
        document.getElementById('search-status').style.display = 'block';
        _searchTimeout = setTimeout(() => doSearch(), 600);
      }} else {{
        document.getElementById('search-status').style.display = 'none';
      }}
    }}

    function doSearch() {{
      const query = document.getElementById('search-input').value.trim();
      if (!query) return;

      // Cancel previous search
      if (_searchAbort) _searchAbort.abort();
      _searchAbort = new AbortController();

      const status = document.getElementById('search-status');
      document.getElementById('search-status-query').textContent = query;
      status.style.display = 'block';
      document.getElementById('web-results-section').style.display = 'none';

      fetch('/api/search', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{query}}),
        signal: _searchAbort.signal,
      }}).then(r => r.json()).then(d => {{
        status.style.display = 'none';

        const webSection = document.getElementById('web-results-section');
        const webList = document.getElementById('web-results-list');

        if (!d.ok || !d.results) return;

        const webResults = d.results.filter(r => r.source === 'web');
        if (!webResults.length) {{
          webSection.style.display = 'none';
          return;
        }}

        let html = '';
        webResults.forEach(r => {{
          let timeStr = '';
          if (r.start_time) {{
            try {{
              const dt = new Date(r.start_time);
              if (!isNaN(dt.getTime())) timeStr = dt.toLocaleDateString('en-US', {{weekday:'short', month:'short', day:'numeric'}});
            }} catch(e) {{}}
          }}
          const safeUrl = (r.url || '#').replace(/'/g, '');
          const meta = [timeStr, r.location].filter(Boolean).join(' · ');
          html += `<div style="padding:10px 0;border-bottom:1px solid #f0f0f0;cursor:pointer;" onclick="window.open('${{safeUrl}}','_blank')">
            <div style="font-weight:700;font-size:14px;color:#1a1a1a;">${{r.title}}${{safeUrl !== '#' ? ' <span style="font-size:11px;color:#c4734f;">↗</span>' : ''}}</div>
            ${{meta ? '<div style="font-size:12px;color:#888;margin-top:2px;">' + meta + '</div>' : ''}}
            ${{r.match_reason ? '<div style="font-size:12px;color:#555;margin-top:4px;">' + r.match_reason.slice(0,120) + '</div>' : ''}}
          </div>`;
        }});
        webList.innerHTML = html;
        webSection.style.display = 'block';
      }}).catch(e => {{
        if (e.name !== 'AbortError') status.style.display = 'none';
      }});
    }}

    function getFilteredEvents() {{
      const query = (document.getElementById('search-input')?.value || '').toLowerCase().trim();
      const showDismissed = !!document.getElementById('show-dismissed')?.checked;
      return EVENTS.filter(e => {{
        if (!e.start) return false;
        if (e.my_rsvp === 'no' && !showDismissed) return false;
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
      ['list','timeline','cal','heat','map'].forEach(v => {{
        const el = document.getElementById(v + '-view');
        if (el) el.style.display = v === view ? 'block' : 'none';
        const btn = document.getElementById('btn-' + v);
        if (btn) btn.classList.toggle('active', v === view);
      }});
      if (view === 'timeline') buildTimelineView();
      if (view === 'map') buildMapView();
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
        const noCls = e.my_rsvp === 'no' ? ' active' : '';
        rsvpBtns = `<div class="card-actions" onclick="event.stopPropagation()">
          <button class="rsvp-btn going${{goingCls}}" onclick="setRsvp(&apos;${{eid}}&apos;, ${{RUN_ID}}, &apos;going&apos;, this)">Going</button>
          <button class="rsvp-btn maybe${{maybeCls}}" onclick="setRsvp(&apos;${{eid}}&apos;, ${{RUN_ID}}, &apos;maybe&apos;, this)">Maybe</button>
          <button class="rsvp-btn no${{noCls}}" onclick="setRsvp(&apos;${{eid}}&apos;, ${{RUN_ID}}, &apos;no&apos;, this)" title="Hide this event">No</button>
        </div>`;
      }}
      const meta = [timeStr, e.location, e.price].filter(Boolean).join(' &middot; ');
      const isGroup = e.event_type === 'group';
      const scoreClass = isGroup ? ' group-card' : (e.score >= 70 ? ' score-high-card' : e.score >= 50 ? ' score-mid-card' : '');
      const groupPill = isGroup
        ? `<a href="/group/${{e.group_id}}" onclick="event.stopPropagation()" class="group-pill">▸ ${{e.group_name || 'Group'}}</a>`
        : '';
      const scoreOrTag = isGroup
        ? `<span class="card-score group-tag">PLAN</span>`
        : `<span class="card-score ${{scoreCls(e.score)}}">${{e.score}}</span>`;
      const sourceLine = isGroup
        ? ''
        : (e.source ? '<div style="font-size:10px;color:#bbb;margin-top:4px;text-transform:capitalize;">via ' + e.source + '</div>' : '');
      const cardOnclick = isGroup
        ? `onclick="if(event.target.tagName!=='BUTTON' && !event.target.closest('a'))location.href='/group/${{e.group_id}}'"`
        : `onclick="if(event.target.tagName!=='BUTTON')window.open(&apos;${{e.url || '#'}}&apos;, &apos;_blank&apos;)"`;
      return `<div class="evt-card${{scoreClass}}${{e.my_rsvp === 'going' ? ' rsvp-going-card' : ''}}" ${{cardOnclick}}>
        <div class="card-body">
          ${{groupPill}}
          <div class="card-top">
            <span class="card-title">${{e.title}}</span>
            ${{scoreOrTag}}
          </div>
          <div class="card-meta">${{meta}}</div>
          ${{e.description ? '<div class="card-reason">' + e.description + '</div>' : ''}}
          ${{sourceLine}}
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
    let _weekOffset = 0;
    function buildTimelineView() {{
      const container = document.getElementById('tl-week');
      const label = document.getElementById('tl-week-label');
      const filtered = getFilteredEvents();
      const today = new Date(); today.setHours(0,0,0,0);
      const todayStr = today.toISOString().slice(0,10);

      // Find Sunday of the target week
      const refDate = new Date(today);
      refDate.setDate(refDate.getDate() + (_weekOffset * 7));
      const sunday = new Date(refDate);
      sunday.setDate(sunday.getDate() - sunday.getDay()); // back to Sunday

      const saturday = new Date(sunday);
      saturday.setDate(saturday.getDate() + 6);
      label.textContent = sunday.toLocaleDateString('en-US', {{month:'short', day:'numeric'}}) + ' — ' + saturday.toLocaleDateString('en-US', {{month:'short', day:'numeric', year:'numeric'}});

      const groups = {{}};
      filtered.forEach(e => {{
        const day = e.start.slice(0, 10);
        if (!groups[day]) groups[day] = [];
        groups[day].push(e);
      }});

      let html = '';
      for (let i = 0; i < 7; i++) {{
        const d = new Date(sunday); d.setDate(sunday.getDate() + i);
        const key = d.toISOString().slice(0, 10);
        const dayEvts = (groups[key] || []).sort((a, b) => b.score - a.score);
        const isToday = key === todayStr;
        const isPast = key < todayStr;
        const dayName = d.toLocaleDateString('en-US', {{weekday: 'short'}});
        const dateNum = d.getDate();

        html += `<div class="timeline-col" style="${{isPast ? 'opacity:.5;' : ''}}">
          <div class="timeline-col-header ${{isToday ? 'today' : ''}}">
            <div class="col-day">${{dayName}}</div>
            <div style="font-size:22px;font-weight:800;color:${{isToday ? '#4a6741' : '#1a1a1a'}};margin:2px 0;">${{dateNum}}</div>
            ${{dayEvts.length ? '<span class="col-count">' + dayEvts.length + '</span>' : ''}}
          </div>`;
        if (!dayEvts.length) {{
          html += '<div class="tl-empty" style="padding:20px 0;color:#ddd;">—</div>';
        }} else {{
          dayEvts.slice(0, 5).forEach(e => {{
            let t = '';
            try {{ const dt = new Date(e.start); if (dt.getHours()||dt.getMinutes()) t = dt.toLocaleTimeString('en-US',{{hour:'numeric',minute:'2-digit'}}); }} catch(x){{}}
            html += `<div class="tl-card" onclick="window.open(&apos;${{(e.url||'#').replace(/'/g,'')}}&apos;,&apos;_blank&apos;)">
              <div class="tl-title">${{e.title}}</div>
              ${{t ? '<div class="tl-time">' + t + '</div>' : ''}}
              ${{e.location ? '<div class="tl-loc">' + e.location + '</div>' : ''}}
              <span class="tl-score ${{scoreCls(e.score)}}">${{e.score}}</span>
            </div>`;
          }});
          if (dayEvts.length > 5) html += `<div class="tl-empty" style="font-size:11px;color:#c4734f;font-weight:600;padding:8px 0;">+${{dayEvts.length - 5}} more</div>`;
        }}
        html += '</div>';
      }}
      container.innerHTML = html;
    }}

    // --- Map view with day tabs ---
    let _map = null;
    let _allMapEvents = [];
    let _mapMarkerLayer = null;
    let _mapSelectedDay = null;

    function buildMapView() {{
      if (!_map) {{
        _map = L.map('event-map', {{zoomControl: false}}).setView([HOME_LAT, HOME_LON], 13);
        L.control.zoom({{position: 'bottomright'}}).addTo(_map);
        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}@2x.png', {{
          attribution: '&copy; OSM &copy; CARTO', maxZoom: 19
        }}).addTo(_map);
        const homeIcon = L.divIcon({{
          html: '<div style="width:14px;height:14px;background:#4a6741;border:2px solid #fff;border-radius:50%;box-shadow:0 1px 4px rgba(0,0,0,.3);"></div>',
          iconSize: [14, 14], className: ''
        }});
        L.marker([HOME_LAT, HOME_LON], {{icon: homeIcon}}).addTo(_map);
        _mapMarkerLayer = L.markerClusterGroup({{
          maxClusterRadius: 30,
          spiderfyOnMaxZoom: true,
          showCoverageOnHover: false,
          iconCreateFunction: function(cluster) {{
            const count = cluster.getChildCount();
            return L.divIcon({{
              html: '<div style="width:32px;height:32px;background:#4a6741;color:#fff;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:800;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.2);">' + count + '</div>',
              iconSize: [32, 32], className: ''
            }});
          }}
        }}).addTo(_map);
      }}
      setTimeout(() => _map.invalidateSize(), 100);

      _allMapEvents = getFilteredEvents().filter(e => e.lat && e.lon);
      _renderMapPins();
    }}

    function selectMapDay(mode) {{
      _mapSelectedDay = mode;
      document.querySelectorAll('.map-tbtn').forEach(b => b.classList.remove('active'));
      document.getElementById('map-tb-' + mode).classList.add('active');
      _renderMapPins();
    }}

    function _renderMapPins() {{
      _mapMarkerLayer.clearLayers();
      const panel = document.getElementById('map-panel');
      const today = new Date(); today.setHours(0,0,0,0);
      const todayStr = today.toISOString().slice(0,10);
      const weekEnd = new Date(today); weekEnd.setDate(weekEnd.getDate() + 7);
      const weekEndStr = weekEnd.toISOString().slice(0,10);

      let events;
      if (_mapSelectedDay === 'today') {{
        events = _allMapEvents.filter(e => e.start && e.start.slice(0,10) === todayStr);
      }} else if (_mapSelectedDay === 'week') {{
        events = _allMapEvents.filter(e => e.start && e.start.slice(0,10) >= todayStr && e.start.slice(0,10) < weekEndStr);
      }} else {{
        events = _allMapEvents;
      }}

      events.forEach(e => {{
        const score = e.score || 0;
        const color = score >= 70 ? '#4a6741' : score >= 50 ? '#c4734f' : '#bbb';

        let marker;
        if (score >= 70) {{
          // Top picks: star marker
          const starSvg = `<svg width="28" height="28" viewBox="0 0 24 24"><polygon points="12,2 15,9 22,9 16.5,14 18.5,21 12,17 5.5,21 7.5,14 2,9 9,9" fill="${{color}}" stroke="#fff" stroke-width="1.5"/></svg>`;
          const icon = L.divIcon({{ html: starSvg, iconSize: [28, 28], iconAnchor: [14, 14], className: '' }});
          marker = L.marker([e.lat, e.lon], {{ icon }});
        }} else {{
          const size = score >= 50 ? 8 : 5;
          marker = L.circleMarker([e.lat, e.lon], {{
            radius: size, color: '#fff', fillColor: color, fillOpacity: 0.7, weight: 1.5
          }});
        }}

        marker.on('click', () => {{
          let timeStr = '';
          try {{ const dt = new Date(e.start); if (dt.getHours()||dt.getMinutes()) timeStr = dt.toLocaleTimeString('en-US',{{hour:'numeric',minute:'2-digit'}}); }} catch(x){{}}
          const dayStr = e.start ? new Date(e.start).toLocaleDateString('en-US',{{weekday:'short',month:'short',day:'numeric'}}) : '';

          panel.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px;">
              <span class="score-badge ${{scoreCls(score)}}">${{score}}</span>
              <button onclick="document.getElementById('map-panel').style.display='none'" style="background:none;border:none;color:#ccc;font-size:18px;cursor:pointer;">&times;</button>
            </div>
            <div style="font-weight:800;font-size:16px;color:#1a1a1a;margin-bottom:6px;">${{e.title}}</div>
            <div style="font-size:13px;color:#888;margin-bottom:4px;">${{[dayStr, timeStr, e.location].filter(Boolean).join(' · ')}}</div>
            ${{e.price ? '<div style="font-size:13px;font-weight:600;color:#c4734f;margin-bottom:8px;">' + e.price + '</div>' : ''}}
            ${{e.description ? '<div style="font-size:13px;color:#555;line-height:1.5;margin-bottom:12px;">' + e.description.slice(0,200) + '</div>' : ''}}
            ${{e.url ? '<a href="'+e.url+'" target="_blank" class="btn-primary" style="text-decoration:none;padding:8px 16px;font-size:12px;display:inline-block;">View</a>' : ''}}`;
          panel.style.display = 'block';
        }});

        _mapMarkerLayer.addLayer(marker);
      }});
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
    """Set or clear RSVP status for an event (works for discovered + manual events).

    JSON body: {event_id, status} — empty status clears the RSVP.
    Auth via cookie (preferred) or `user_token` (legacy email-link convenience).
    """
    data = await request.json()
    db = get_db()
    user = _get_current_user(request)
    if not user:
        token = data.get("user_token", "")
        if token:
            user = db.get_user_by_token(token)
    if not user:
        return JSONResponse({"error": "Sign in required"}, status_code=401)
    event_id = data.get("event_id", "")
    status = data.get("status", "")
    if not event_id:
        return JSONResponse({"error": "event_id required"}, status_code=400)
    if not status:
        # Clear the RSVP — if user was 'going' on a capacity-limited event, promote a waitlister.
        prev_row = db.conn.execute(
            "SELECT status FROM rsvps WHERE user_id = ? AND event_id = ?",
            (user["id"], event_id),
        ).fetchone()
        db.conn.execute(
            "DELETE FROM rsvps WHERE user_id = ? AND event_id = ?",
            (user["id"], event_id),
        )
        db.conn.commit()
        if prev_row and prev_row["status"] == "going":
            db._promote_waitlist(event_id)
        return {"ok": True, "status": ""}
    # Resolve run_id: caller may pass it; otherwise look it up from the event
    run_id = data.get("run_id")
    if run_id is None:
        row = db.conn.execute(
            "SELECT run_id FROM events WHERE event_id = ? ORDER BY run_id DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        run_id = row["run_id"] if row else 0
    effective_status, _prev = db.set_rsvp_with_capacity(user["id"], event_id, run_id, status)
    data["status"] = effective_status
    status = effective_status

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
            # Build human-friendly when/where strings
            event_when = ""
            event_location = ""
            if event_row:
                try:
                    from datetime import datetime as _dt
                    st = (event_row["start_time"] or "").strip()
                    if st:
                        d = _dt.fromisoformat(st)
                        event_when = d.strftime("%a %b %-d, %-I:%M %p")
                except (ValueError, TypeError):
                    pass
                event_location = (event_row["location_name"] or "").strip()

            # Resolve the originating group (for the event being RSVP'd to, if it's a group event).
            event_group_id = None
            event_group_row = db.conn.execute(
                "SELECT group_id FROM events WHERE event_id = ? AND group_id IS NOT NULL LIMIT 1",
                (data["event_id"],),
            ).fetchone()
            if event_group_row:
                event_group_id = event_group_row["group_id"]

            user_groups = db.get_user_groups(user["id"])
            notified: set[int] = set()
            for g in user_groups:
                gname = db.get_group_display_name(g) if hasattr(db, "get_group_display_name") else (g.get("display_name") or g.get("name") or "")
                # Prefer the actual originating group name when the event belongs to one.
                if event_group_id and g["id"] == event_group_id:
                    display_group_name = gname
                else:
                    display_group_name = gname
                members = db.get_group_members(g["id"])
                for m in members:
                    if m["id"] != user["id"] and m["id"] not in notified:
                        notified.add(m["id"])
                        try:
                            send_rsvp_notify(
                                m["email"], m.get("user_token", ""), rsvper_name,
                                event_title, event_url, settings.dashboard_url, settings,
                                event_when=event_when, event_location=event_location,
                                group_name=display_group_name,
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
    """Invite link landing.

    Valid code + authed user + not yet a member → auto-join, redirect to group page.
    Valid code + unauthed → show group preview with "Continue with Google" CTA, where
    the next param brings them right back here (which then auto-joins).
    """
    db = get_db()
    group = db.get_group_by_id(group_id)
    if not group or group.get("invite_code") != invite_code:
        return HTMLResponse("<h1>Invalid invite link</h1>", status_code=404)
    group_name = db.get_group_display_name(group)
    current_user = _get_current_user(request)
    if current_user:
        if not db.is_group_member(group_id, current_user["id"]):
            db.add_group_member(group_id, current_user["id"])
        return RedirectResponse(f"/group/{group_id}?success=Joined+{group_name.replace(' ', '+')}", status_code=303)
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
    is_creator = current_user and group.get("created_by") == current_user["id"]
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

    # User-added group events — split into tentative (no date), upcoming, past
    user_events = db.get_group_user_events(group["id"])
    tentative_user = [e for e in user_events if not (e.get("start_time") or "").strip()]
    dated_events = [e for e in user_events if (e.get("start_time") or "").strip()]
    upcoming_user = [e for e in dated_events if e["start_time"] >= now_str]
    past_user = sorted(
        (e for e in dated_events if e["start_time"] < now_str),
        key=lambda x: x["start_time"], reverse=True,
    )

    # Pipeline events where members RSVPd
    pipeline_events = db.get_group_events(group["id"])
    event_ids = [e.get("event_id", "") for e in pipeline_events if e.get("event_id")]
    user_evt_ids = [f"grp_evt_{e['id']}" for e in (tentative_user + upcoming_user + past_user)]
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

    # Bulk-fetch comments across tentative, upcoming, and past events shown on this page
    _shown_user_events = (tentative_user[:8] + upcoming_user[:8] + past_user[:12])
    _shown_ev_ids = [f"grp_evt_{e['id']}" for e in _shown_user_events]
    comments_by_event: dict[str, list[dict]] = {}
    if _shown_ev_ids:
        ph = ",".join("?" * len(_shown_ev_ids))
        crows = db.conn.execute(
            f"""SELECT c.event_id, c.id, c.body, c.created_at, c.user_id,
                       u.name AS user_name, u.email AS user_email
                FROM event_comments c JOIN users u ON u.id = c.user_id
                WHERE c.event_id IN ({ph})
                ORDER BY c.created_at ASC""",
            _shown_ev_ids,
        ).fetchall()
        for c in crows:
            c = dict(c)
            comments_by_event.setdefault(c.pop("event_id"), []).append(c)

    # Render a single user-added group event card. Used for tentative, upcoming, past lists.
    from html import escape as _esc

    def _render_user_event(e: dict, mode: str = "upcoming") -> str:
        """mode: 'tentative' (no date), 'upcoming' (future), 'past' (history)."""
        st = (e.get("start_time") or "").strip()
        et = (e.get("end_time") or "").strip()
        if mode == "tentative":
            time_str = "Tentative · no date yet"
            time_color = "#8a3f25"
        else:
            try:
                d = dt.fromisoformat(st)
                time_str = d.strftime("%a %b %-d, %-I:%M %p")
                if et:
                    try:
                        ed = dt.fromisoformat(et)
                        time_str += " → " + ed.strftime("%-I:%M %p")
                    except (ValueError, TypeError):
                        pass
            except (ValueError, TypeError):
                time_str = st[:16]
            time_color = "#6b7280"
        creator = e.get("creator_name") or ""
        loc = e.get("location") or ""
        url = e.get("url") or ""
        title_color = "#888" if mode == "past" else "#1a1a1a"
        title_link = (f'<a href="{url}" target="_blank" style="font-weight:700;font-size:15px;color:{title_color};text-decoration:none;">{_esc(e["title"][:65])}</a>'
                      if url else f'<span style="font-weight:700;font-size:15px;color:{title_color};">{_esc(e["title"][:65])}</span>')

        # Any group member may edit/remove — trust the group.
        edit_btn = delete_btn = ""
        if is_member and mode != "past":
            edit_btn = f'<button type="button" onclick="openEditEvent({group_id},{e["id"]})" class="evt-action-btn" title="Edit event">✎ Edit</button>'
            delete_btn = f'<form action="/api/group/{group_id}/delete-event" method="post" style="margin:0;" onsubmit="return confirm(&apos;Remove this event?&apos;)"><input type="hidden" name="event_id" value="{e["id"]}"><button type="submit" class="evt-action-btn evt-action-danger" title="Remove event">✕ Remove</button></form>'

        ue_eid = f"grp_evt_{e['id']}"
        ue_rsvps = rsvps_map.get(ue_eid, [])
        ue_avatars = _rsvp_avatars(ue_rsvps)

        capacity = e.get("capacity")
        going_n = sum(1 for r in ue_rsvps if r["status"] == "going")
        waitlist_n = sum(1 for r in ue_rsvps if r["status"] == "waitlist")
        cap_html = ""
        if capacity and mode != "past":
            if going_n >= capacity:
                cap_html = f'<span style="display:inline-block;padding:3px 10px;background:#fbf6f3;color:#8a3f25;border:1px solid #e6cdc1;font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;">Full · {going_n}/{capacity}</span>'
            else:
                cap_html = f'<span style="display:inline-block;padding:3px 10px;background:#edf2eb;color:#4a6741;border:1px solid #d4e0d1;font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;">{going_n}/{capacity} going</span>'
            if waitlist_n:
                cap_html += f' <span style="font-size:11px;color:#8a3f25;margin-left:6px;font-weight:600;">+{waitlist_n} on waitlist</span>'

        prereq = (e.get("prerequisites") or "").strip()
        prereq_html = ""
        if prereq and mode != "past":
            prereq_html = f'<div style="margin-top:8px;padding:9px 11px;background:#fbf6f3;border-left:3px solid #c4734f;font-size:12px;color:#5a2a18;line-height:1.45;"><strong style="font-weight:700;color:#8a3f25;text-transform:uppercase;letter-spacing:.5px;font-size:10px;">Prereqs</strong> &nbsp;{_esc(prereq)}</div>'

        notes = (e.get("notes") or "").strip()
        notes_html = f'<div style="font-size:13px;color:#475569;margin-top:6px;line-height:1.45;white-space:pre-wrap;">{_esc(notes)}</div>' if notes and mode != "past" else ""

        host_link = f'<span style="font-weight:600;color:#4a6741;">{_esc(creator)}</span>' if creator else ""

        my_ue_rsvp = ""
        if current_user:
            for r in ue_rsvps:
                if r.get("user_id") == current_user["id"]:
                    my_ue_rsvp = r["status"]
        rsvp_btns = ""
        my_status_label = ""
        if mode != "past":
            if my_ue_rsvp == "waitlist":
                my_status_label = '<span style="display:inline-block;margin-top:8px;padding:3px 10px;background:#fbf6f3;color:#8a3f25;border:1px solid #e6cdc1;font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;">You\'re on the waitlist</span>'
            if is_member and current_user:
                going_cls = " active" if my_ue_rsvp == "going" else ""
                maybe_cls = " active" if my_ue_rsvp == "maybe" else ""
                cant_cls = " active" if my_ue_rsvp == "cant" else ""
                going_label = "Going"
                if capacity and going_n >= capacity and my_ue_rsvp != "going":
                    going_label = "Join waitlist"
                rsvp_btns = f'''<div style="display:flex;gap:6px;margin-top:8px;">
                    <button onclick="rsvpGroupEvent({group_id}, &apos;{ue_eid}&apos;, &apos;going&apos;, this)" class="grp-rsvp-btn going{going_cls}">{going_label}</button>
                    <button onclick="rsvpGroupEvent({group_id}, &apos;{ue_eid}&apos;, &apos;maybe&apos;, this)" class="grp-rsvp-btn maybe{maybe_cls}">Maybe</button>
                    <button onclick="rsvpGroupEvent({group_id}, &apos;{ue_eid}&apos;, &apos;cant&apos;, this)" class="grp-rsvp-btn cant{cant_cls}">Can't</button>
                </div>'''

        # Chat affordance — the whole row is the click target, styled like an iMessage row.
        comments = comments_by_event.get(ue_eid, [])
        chat_count = len(comments)
        comments_block = ""
        if is_member and current_user:
            if chat_count:
                last = comments[-1]
                last_name = (last.get("user_name") or "").split()[0] or "?"
                last_body = (last.get("body") or "")[:80]
                truncated = "…" if len(last.get("body") or "") > 80 else ""
                primary = f"{chat_count} message{'s' if chat_count != 1 else ''}"
                preview = f'<span class="chat-row-preview"><strong>{_esc(last_name)}:</strong> {_esc(last_body)}{truncated}</span>'
            else:
                primary = "Open chat"
                preview = '<span class="chat-row-preview chat-row-empty">Start the thread for this event</span>'
            comments_block = f'''<button type="button" class="chat-row-btn" onclick="openChat(&apos;{ue_eid}&apos;, this.closest('.group-event-card').dataset.title)">
                <span class="chat-row-icon">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                </span>
                <span class="chat-row-text">
                    <span class="chat-row-primary">{primary}</span>
                    {preview}
                </span>
                <span class="chat-row-arrow">›</span>
            </button>'''

        _start_iso = st
        _end_iso = (e.get("end_time") or "") or ""
        _date_part = _start_iso[:10]
        _start_part = _start_iso[11:16] if len(_start_iso) >= 16 else ""
        _end_part = _end_iso[11:16] if len(_end_iso) >= 16 else ""
        _data_attrs = (
            f'data-event-id="{e["id"]}" '
            f'data-title="{_esc(e.get("title") or "", quote=True)}" '
            f'data-date="{_date_part}" '
            f'data-time="{_start_part}" '
            f'data-end="{_end_part}" '
            f'data-location="{_esc(e.get("location") or "", quote=True)}" '
            f'data-url="{_esc(e.get("url") or "", quote=True)}" '
            f'data-notes="{_esc(e.get("notes") or "", quote=True)}" '
            f'data-capacity="{capacity if capacity else ""}" '
            f'data-prerequisites="{_esc(prereq, quote=True)}"'
        )
        actions_row = ""
        if edit_btn or delete_btn:
            actions_row = f'<div class="evt-actions-row">{edit_btn}{delete_btn}</div>'
        host_row = f'<div style="font-size:12px;color:#9ca3af;margin-top:4px;">Hosted by {host_link}</div>' if host_link else ""
        cap_row = f'<div style="margin-top:6px;">{cap_html}</div>' if cap_html else ""
        card_bg = "#fafafa" if mode == "past" else "#fff"
        card_opacity = "opacity:.78;" if mode == "past" else ""
        return f'''<div class="card group-event-card" {_data_attrs} style="padding:18px 18px;margin-bottom:18px;background:{card_bg};{card_opacity}">
            {title_link}
            <div style="font-size:13px;color:{time_color};margin-top:2px;font-weight:{'600' if mode == 'tentative' else '400'};">{time_str}{" · " + _esc(loc) if loc else ""}</div>
            {host_row}
            {notes_html}
            {prereq_html}
            {cap_row}
            {ue_avatars}
            {my_status_label}
            {rsvp_btns}
            {comments_block}
            {actions_row}
        </div>'''

    upcoming_html = ""
    for e in tentative_user[:8]:
        upcoming_html += _render_user_event(e, mode="tentative")
    for e in upcoming_user[:8]:
        upcoming_html += _render_user_event(e, mode="upcoming")

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
        upcoming_html += f'''<div class="card" style="padding:14px 16px;margin-bottom:8px;">
            <div style="flex:1;min-width:0;">
                <a href="{url}" target="_blank" style="font-weight:700;font-size:15px;color:#1e293b;text-decoration:none;">{title}</a>
                <div style="font-size:13px;color:#6b7280;margin-top:2px;">{time_str}{" · " + loc if loc else ""}</div>
                {pe_avatars}
            </div>
        </div>'''

    if not upcoming_html:
        upcoming_html = '<p style="color:#9ca3af;font-size:14px;">No upcoming events yet.</p>'

    # Recently deleted (soft-deleted) events — restore or purge
    deleted_html = ""
    if is_member:
        deleted_events = db.get_deleted_group_events(group["id"])
        if deleted_events:
            from html import escape as _esc_d
            rows = ""
            for de in deleted_events[:50]:
                try:
                    dt_del = dt.fromisoformat(de["deleted_at"])
                    when_del = dt_del.strftime("%b %-d, %-I:%M %p")
                except (ValueError, TypeError):
                    when_del = ""
                st_d = (de.get("start_time") or "").strip()
                if st_d:
                    try:
                        sd = dt.fromisoformat(st_d)
                        when_evt = sd.strftime("%a %b %-d")
                    except (ValueError, TypeError):
                        when_evt = ""
                else:
                    when_evt = "Tentative"
                rows += f'''<div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-top:1px solid #f0f0f0;">
                    <div style="flex:1;min-width:0;">
                        <div style="font-size:14px;font-weight:600;color:#1a1a1a;">{_esc_d(de.get("title") or "")}</div>
                        <div style="font-size:12px;color:#888;margin-top:2px;">{when_evt} · deleted {when_del}</div>
                    </div>
                    <form action="/api/group/{group_id}/restore-event" method="post" style="margin:0;"><input type="hidden" name="event_id" value="{de["id"]}"><button type="submit" class="evt-action-btn evt-action-primary" style="white-space:nowrap;">↩ Restore</button></form>
                    <form action="/api/group/{group_id}/purge-event" method="post" style="margin:0;" onsubmit="return confirm(&apos;Permanently delete this event? Cannot be undone.&apos;)"><input type="hidden" name="event_id" value="{de["id"]}"><button type="submit" class="evt-action-btn evt-action-danger" style="white-space:nowrap;">✕ Purge</button></form>
                </div>'''
            count_d = len(deleted_events)
            deleted_html = f'''<details class="past-events" style="margin-bottom:28px;border-top:1px solid #e0e0e0;padding-top:18px;">
                <summary style="cursor:pointer;font-size:13px;font-weight:700;color:#8a3f25;letter-spacing:.5px;padding:8px 0;display:flex;align-items:center;gap:8px;">
                    <span class="past-caret" style="font-size:11px;color:#8a3f25;transition:transform .15s;">▸</span>
                    <span>Recently deleted <span style="color:#888;font-weight:500;">· {count_d}</span></span>
                </summary>
                <div style="margin-top:8px;">{rows}</div>
            </details>'''

    # Past events — collapsed memory lane (auto-expanded when short)
    past_html = ""
    if past_user:
        past_cards = "".join(_render_user_event(e, mode="past") for e in past_user[:30])
        count = len(past_user)
        open_attr = " open" if count <= 3 else ""
        past_html = f'''<details class="past-events"{open_attr} style="margin-bottom:28px;border-top:1px solid #e0e0e0;padding-top:18px;">
            <summary style="cursor:pointer;font-size:13px;font-weight:700;color:#4a6741;letter-spacing:.5px;padding:8px 0;display:flex;align-items:center;gap:8px;">
                <span class="past-caret" style="font-size:11px;color:#4a6741;transition:transform .15s;">▸</span>
                <span>Past events <span style="color:#888;font-weight:500;">· {count}</span></span>
            </summary>
            <div style="margin-top:14px;">{past_cards}</div>
        </details>
        <style>.past-events[open] .past-caret {{ transform: rotate(90deg); }} .past-events summary::-webkit-details-marker {{ display: none; }} .past-events summary {{ list-style: none; }}</style>'''

    # --- Add event form (members only) ---
    add_event_html = ""
    if is_member:
        from datetime import datetime as _dt, timedelta as _td2
        _now = _dt.now()
        if _now.hour < 17:
            default_date = _now.strftime("%Y-%m-%d")
        else:
            default_date = (_now + _td2(days=1)).strftime("%Y-%m-%d")
        default_time = "19:00"
        _empty_group = not upcoming_user and not tentative_user
        _panel_display = "block" if _empty_group else "none"
        _toggle_inner = ('<span style="font-size:18px;line-height:1;">×</span><span>Close</span>'
                         if _empty_group else
                         '<span style="font-size:18px;line-height:1;">+</span><span>Add event</span>')
        add_event_html = f'''<div style="margin-bottom:28px;">
            <button type="button" onclick="toggleAddEvent({group_id})" id="ae-toggle-{group_id}"
                    class="btn-primary" style="width:100%;padding:14px;font-size:14px;display:flex;align-items:center;justify-content:center;gap:8px;">
                {_toggle_inner}
            </button>
            <div id="ae-panel-{group_id}" style="display:{_panel_display};border:1px solid #e0e0e0;border-top:none;padding:18px 16px;background:#fafafa;">
                <div id="ae-edit-banner-{group_id}" style="display:none;padding:10px 12px;background:#edf2eb;border-left:3px solid #4a6741;font-size:13px;color:#1a1a1a;margin-bottom:10px;">
                    Editing <strong id="ae-edit-banner-title-{group_id}"></strong>
                </div>
                <form action="/api/group/{group_id}/add-event" method="post" id="add-event-form-{group_id}" class="ae-form">
                    <input type="hidden" name="edit_id" id="ae-edit-id-{group_id}" value="">

                    <input name="title" id="ae-title-{group_id}" placeholder="What's the plan?" required class="ae-title-input" autocomplete="off">

                    <div class="ae-when-row">
                        <input name="date" id="ae-date-{group_id}" type="date" value="{default_date}" class="ae-when-input" aria-label="Date">
                        <span class="ae-when-sep">·</span>
                        <input name="time" id="ae-time-{group_id}" type="time" value="{default_time}" class="ae-when-input" aria-label="Start time">
                        <span class="ae-when-sep">→</span>
                        <input name="end_time" id="ae-end-{group_id}" type="time" class="ae-when-input" aria-label="End time">
                        <button type="button" data-day="tentative" onclick="setQuickWhen({group_id}, this)" class="ae-skip-date" id="ae-skip-date-{group_id}">No date</button>
                    </div>
                    <div id="ae-when-readout-{group_id}" class="ae-readout"></div>

                    <input name="location" id="ae-loc-{group_id}" placeholder="Where? (optional)" class="ae-line-input" autocomplete="off">

                    <button type="button" id="ae-more-toggle-{group_id}" onclick="toggleAeMore({group_id})" class="ae-more-toggle">
                        <span class="ae-more-caret">▸</span> More
                    </button>

                    <div id="ae-more-{group_id}" class="ae-more-panel" style="display:none;">
                        <textarea name="notes" id="ae-notes-{group_id}" placeholder="Notes — anything to know?" rows="2" class="ae-line-input" style="resize:vertical;"></textarea>
                        <input name="prerequisites" id="ae-prereq-{group_id}" placeholder="Prereqs (e.g. comfortable swimmer)" class="ae-line-input" maxlength="240">
                        <input name="capacity" id="ae-cap-{group_id}" type="number" min="1" max="999" placeholder="Cap — max # going (extras waitlist)" class="ae-line-input">
                        <input name="url" id="ae-url-{group_id}" type="url" placeholder="Link (https://…)" class="ae-line-input">
                        <div id="ae-recurring-field-{group_id}" style="display:flex;align-items:center;gap:8px;">
                            <label for="ae-recurring-{group_id}" style="font-size:13px;color:#666;">Repeat:</label>
                            <select name="recurring" id="ae-recurring-{group_id}" class="ae-line-input" style="flex:1;">
                                <option value="">One time</option>
                                <option value="2">Weekly · 2 weeks</option>
                                <option value="4">Weekly · 4 weeks</option>
                                <option value="8">Weekly · 8 weeks</option>
                                <option value="12">Weekly · 12 weeks</option>
                            </select>
                        </div>
                    </div>

                    <div class="ae-actions">
                        <button type="button" onclick="toggleAddEvent({group_id})" class="ae-btn-secondary">Cancel</button>
                        <button type="submit" id="ae-submit-{group_id}" class="ae-btn-primary">Add event</button>
                    </div>
                </form>
            </div>
            <style>
            .ae-form {{ display: flex; flex-direction: column; gap: 14px; }}
            .ae-title-input {{ width: 100%; padding: 8px 0; border: none; border-bottom: 1.5px solid #e0e0e0; font-size: 20px; font-weight: 700; font-family: inherit; background: transparent; color: #1a1a1a; outline: none; transition: border-color .12s; box-sizing: border-box; }}
            .ae-title-input:focus {{ border-bottom-color: #4a6741; }}
            .ae-title-input::placeholder {{ color: #bbb; font-weight: 500; }}
            .ae-when-row {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
            .ae-when-input {{ padding: 8px 10px; border: 1px solid #e0e0e0; font-size: 14px; font-family: inherit; background: #fff; color: #1a1a1a; outline: none; transition: border-color .12s, box-shadow .12s; min-width: 0; }}
            .ae-when-input:focus {{ border-color: #4a6741; box-shadow: 0 0 0 3px rgba(74,103,65,.10); }}
            .ae-when-sep {{ color: #ccc; font-weight: 600; }}
            .ae-skip-date {{ background: none; border: none; color: #8a3f25; cursor: pointer; font-size: 12px; font-weight: 600; font-family: inherit; padding: 6px 8px; letter-spacing: .3px; border-bottom: 1px dashed #c4734f; margin-left: auto; }}
            .ae-skip-date:hover {{ color: #c4734f; }}
            .ae-skip-date.active {{ color: #c4734f; border-bottom-style: solid; }}
            .ae-line-input {{ width: 100%; padding: 9px 0; border: none; border-bottom: 1px solid #e0e0e0; font-size: 14px; font-family: inherit; background: transparent; color: #1a1a1a; outline: none; transition: border-color .12s; box-sizing: border-box; }}
            .ae-line-input:focus {{ border-bottom-color: #4a6741; }}
            .ae-line-input::placeholder {{ color: #aaa; }}
            .ae-readout {{ font-size: 12px; color: #4a6741; font-weight: 500; min-height: 16px; margin-top: -4px; }}
            .ae-more-toggle {{ background: none; border: none; color: #888; cursor: pointer; font-size: 12px; font-weight: 600; padding: 4px 0; text-align: left; font-family: inherit; display: inline-flex; align-items: center; gap: 6px; letter-spacing: .2px; align-self: flex-start; }}
            .ae-more-toggle:hover {{ color: #4a6741; }}
            .ae-more-caret {{ font-size: 10px; transition: transform .15s; display: inline-block; color: #888; }}
            .ae-more-toggle.open .ae-more-caret {{ transform: rotate(90deg); color: #4a6741; }}
            .ae-more-panel {{ display: flex; flex-direction: column; gap: 12px; padding: 4px 0 4px; }}
            .ae-actions {{ display: flex; gap: 8px; margin-top: 6px; align-items: center; }}
            .ae-btn-secondary {{ background: none; color: #888; border: none; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; padding: 10px 14px; }}
            .ae-btn-secondary:hover {{ color: #1a1a1a; }}
            .ae-btn-primary {{ flex: 1; padding: 12px 18px; background: #4a6741; color: #fff; border: none; font-size: 14px; font-weight: 700; cursor: pointer; font-family: inherit; letter-spacing: .3px; transition: background .12s; }}
            .ae-btn-primary:hover {{ background: #3a5334; }}
            </style>
            <script>
            function toggleAeMore(gid) {{
                const panel = document.getElementById('ae-more-' + gid);
                const toggle = document.getElementById('ae-more-toggle-' + gid);
                const isOpen = panel.style.display !== 'none';
                panel.style.display = isOpen ? 'none' : 'flex';
                toggle.classList.toggle('open', !isOpen);
            }}

            function toggleAddEvent(gid) {{
                const editIdEl = document.getElementById('ae-edit-id-' + gid);
                if (editIdEl && editIdEl.value) {{
                    cancelEdit(gid);
                    return;
                }}
                const panel = document.getElementById('ae-panel-' + gid);
                const toggle = document.getElementById('ae-toggle-' + gid);
                const isOpen = panel.style.display !== 'none';
                panel.style.display = isOpen ? 'none' : 'block';
                toggle.innerHTML = isOpen
                    ? '<span style="font-size:18px;line-height:1;">+</span><span>Add event</span>'
                    : '<span style="font-size:18px;line-height:1;">×</span><span>Close</span>';
                if (!isOpen) {{
                    setTimeout(() => document.getElementById('ae-title-' + gid).focus(), 50);
                    updateWhenReadout(gid);
                }}
            }}

            function setQuickWhen(gid, btn) {{
                const dayCode = btn.dataset.day;
                const t = btn.dataset.time || '';
                const dateEl = document.getElementById('ae-date-' + gid);
                const timeEl = document.getElementById('ae-time-' + gid);
                if (dayCode === 'tentative') {{
                    const endEl = document.getElementById('ae-end-' + gid);
                    const isActive = btn.classList.contains('active');
                    if (isActive) {{
                        const today = new Date();
                        const yyyy = today.getFullYear();
                        const mm = String(today.getMonth()+1).padStart(2,'0');
                        const dd = String(today.getDate()).padStart(2,'0');
                        dateEl.value = yyyy + '-' + mm + '-' + dd;
                        timeEl.value = '19:00';
                        btn.classList.remove('active');
                    }} else {{
                        dateEl.value = '';
                        timeEl.value = '';
                        if (endEl) endEl.value = '';
                        btn.classList.add('active');
                    }}
                    updateWhenReadout(gid);
                    return;
                }}
            }}

            function autoFillEnd(gid) {{
                const tEl = document.getElementById('ae-time-' + gid);
                const eEl = document.getElementById('ae-end-' + gid);
                if (!tEl || !eEl || !tEl.value || eEl.value) return;
                const [h, m] = tEl.value.split(':').map(Number);
                const endH = (h + 2) % 24;
                eEl.value = String(endH).padStart(2,'0') + ':' + String(m || 0).padStart(2,'0');
            }}

            function updateWhenReadout(gid) {{
                const dEl = document.getElementById('ae-date-' + gid);
                const tEl = document.getElementById('ae-time-' + gid);
                const readout = document.getElementById('ae-when-readout-' + gid);
                const skipBtn = document.getElementById('ae-skip-date-' + gid);
                if (!dEl || !dEl.value) {{
                    readout.textContent = 'Tentative — pick a date later';
                    readout.style.color = '#8a3f25';
                    if (skipBtn) skipBtn.classList.add('active');
                    return;
                }}
                if (skipBtn) skipBtn.classList.remove('active');
                readout.style.color = '#4a6741';
                const eEl = document.getElementById('ae-end-' + gid);
                const d = new Date(dEl.value + 'T' + (tEl.value || '00:00') + ':00');
                if (isNaN(d)) {{ readout.textContent = ''; return; }}
                const dayLabel = d.toLocaleDateString(undefined, {{weekday:'short', month:'short', day:'numeric'}});
                const timeLabel = tEl.value ? d.toLocaleTimeString(undefined, {{hour:'numeric', minute:'2-digit'}}) : '';
                let s = dayLabel + (timeLabel ? ' · ' + timeLabel : '');
                if (eEl && eEl.value) {{
                    const end = new Date(dEl.value + 'T' + eEl.value + ':00');
                    if (!isNaN(end)) {{
                        s += ' → ' + end.toLocaleTimeString(undefined, {{hour:'numeric', minute:'2-digit'}});
                    }}
                }}
                readout.textContent = s;
            }}

            (function() {{
                const gid = {group_id};
                ['ae-date-', 'ae-time-', 'ae-end-'].forEach(prefix => {{
                    const el = document.getElementById(prefix + gid);
                    if (el) el.addEventListener('input', () => updateWhenReadout(gid));
                }});
                const tEl = document.getElementById('ae-time-' + gid);
                if (tEl) tEl.addEventListener('change', () => autoFillEnd(gid));
            }})();
            </script>
        </div>'''

    # actions_html intentionally empty — the actions cluster lives below alongside mute/leave.
    actions_html = ""

    # --- Join CTA for non-members (only if they arrived via valid invite link) ---
    invite_code = group.get("invite_code", "")
    join_cta = ""
    if _valid_invite and not is_member:
        if not current_user:
            from urllib.parse import quote as _q
            invite_url = f"/group/{group_id}/join/{invite_code}"
            google_g = '<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg"><path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/><path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/><path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/><path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/></svg>'
            join_cta = f'''<div class="card" style="margin-bottom:20px;text-align:center;padding:24px 18px;border-top:3px solid #4a6741;">
                <h2 style="margin:0 0 6px;color:#1a1a1a;font-size:18px;text-transform:none;letter-spacing:0;font-weight:800;">Join {group_name}</h2>
                <p style="font-size:13px;color:#6b7280;margin-bottom:16px;line-height:1.5;">Sign in to join the group and start coordinating plans.</p>
                <a href="/auth/google/login?next={_q(invite_url, safe='/')}" style="display:inline-flex;align-items:center;gap:10px;padding:12px 20px;background:#fff;border:1.5px solid #dadce0;color:#3c4043;font-size:14px;font-weight:600;text-decoration:none;border-radius:8px;">
                    {google_g}
                    <span>Continue with Google</span>
                </a>
            </div>'''
        else:
            # Authed but not a member and not on the auto-join path — fallback CTA
            join_cta = f'''<div class="card" style="margin-bottom:20px;">
                <form action="/group/{group_id}/join" method="post" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
                    <span style="font-size:14px;color:#374151;">Join this group to add events and see plans.</span>
                    <button type="submit" class="btn-primary" style="white-space:nowrap;">Join</button>
                </form>
            </div>'''

    # --- Editable group name + quick invite (inline) ---
    name_html = f'<h1>{group_name}</h1>'
    if is_member:
        _settings_for_link = Settings()
        _invite_code = group.get("invite_code", "")
        _group_link = f"{_settings_for_link.dashboard_url}/group/{group_id}/join/{_invite_code}"
        name_html = f'''<div style="display:flex;align-items:center;gap:8px;margin-bottom:20px;flex-wrap:wrap;">
            <h1 style="margin:0;" id="groupName">{group_name}</h1>
            <button onclick="editGroupName()" style="background:none;border:none;cursor:pointer;color:#9ca3af;font-size:13px;padding:4px 8px;" title="Rename group">edit</button>
            <button onclick="quickShareGroup(this, &apos;{_group_link}&apos;, &apos;{group_name}&apos;)" style="background:none;border:1px solid #d4e0d1;cursor:pointer;color:#4a6741;font-size:13px;font-weight:600;padding:6px 12px;border-radius:999px;margin-left:auto;display:inline-flex;align-items:center;gap:5px;" title="Invite someone">
                <span style="font-size:14px;">↗</span> Invite
            </button>
        </div>
        <script>
        async function quickShareGroup(btn, link, title) {{
            const txt = 'Join ' + title + ' on Calyx';
            if (navigator.share) {{
                try {{ await navigator.share({{title: txt, url: link}}); return; }} catch(e) {{}}
            }}
            try {{
                await navigator.clipboard.writeText(link);
                const orig = btn.innerHTML;
                btn.innerHTML = '<span style="font-size:14px;">✓</span> Copied!';
                setTimeout(() => {{ btn.innerHTML = orig; }}, 1600);
            }} catch(e) {{
                window.prompt('Copy this invite link:', link);
            }}
        }}
        </script>
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

    # --- Bottom cluster: invite link, share, mute, leave/delete (members only) ---
    group_actions_html = ""
    mute_html = ""
    leave_html = ""
    if is_member and current_user:
        settings = Settings()
        invite_code = group.get("invite_code", "")
        group_link = f"{settings.dashboard_url}/group/{group_id}/join/{invite_code}"

        notif_row = db.conn.execute(
            "SELECT notifications FROM group_members WHERE group_id=? AND user_id=?",
            (group_id, current_user["id"]),
        ).fetchone()
        notifs_on = notif_row["notifications"] if notif_row else 1
        mute_label = "Mute notifications" if notifs_on else "Unmute notifications"

        if group.get("created_by") == current_user["id"]:
            danger_action = f'<form action="/api/group/{group_id}/delete" method="post" style="margin:0;"><button type="submit" onclick="return confirm(&apos;Delete this group and all its events? This cannot be undone.&apos;)" style="background:none;border:none;color:#a05439;cursor:pointer;font-size:12px;padding:4px 8px;font-family:inherit;text-decoration:underline;">Delete group</button></form>'
        else:
            danger_action = f'<form action="/api/group/{group_id}/leave" method="post" style="margin:0;"><button type="submit" onclick="return confirm(&apos;Leave this group?&apos;)" style="background:none;border:none;color:#a05439;cursor:pointer;font-size:12px;padding:4px 8px;font-family:inherit;text-decoration:underline;">Leave group</button></form>'

        group_actions_html = f'''<div style="margin-top:18px;padding-top:14px;border-top:1px dashed #e0e0e0;">
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
                <button onclick="navigator.clipboard.writeText(&apos;{group_link}&apos;);this.textContent=&apos;Copied!&apos;;setTimeout(()=>this.textContent=&apos;Copy invite link&apos;,1500)"
                        class="evt-action-btn evt-action-primary" style="flex:1;min-width:140px;padding:8px 14px;">Copy invite link</button>
                <button onclick="navigator.share({{title:&apos;Join {group_name} on Calyx&apos;,text:&apos;Join our group to coordinate plans&apos;,url:&apos;{group_link}&apos;}}).catch(()=>{{}})"
                        class="evt-action-btn" style="flex:1;min-width:120px;padding:8px 14px;">Share</button>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#888;">
                <button onclick="fetch(&apos;/api/group/{group_id}/mute&apos;,{{method:&apos;POST&apos;}}).then(()=>location.reload())" style="background:none;border:none;color:#888;cursor:pointer;font-size:12px;padding:4px 8px;font-family:inherit;text-decoration:underline;">{mute_label}</button>
                {danger_action}
            </div>
        </div>'''

    # Group RSVP + chat-modal CSS + JS
    group_rsvp_extras = f"""
    <style>
    .grp-rsvp-btn {{ font-size:13px; padding:9px 20px; border:1.5px solid #d4e0d1; background:#fff; cursor:pointer; color:#4a6741; font-weight:700; transition:all .15s; font-family:inherit; letter-spacing:.2px; min-height:40px; }}
    .grp-rsvp-btn:hover {{ background:#edf2eb; border-color:#4a6741; }}
    .grp-rsvp-btn.going.active {{ background:#4a6741; color:#fff; border-color:#4a6741; }}
    .grp-rsvp-btn.maybe.active {{ background:#fbf6f3; color:#8a3f25; border-color:#c4734f; }}
    .grp-rsvp-btn.cant {{ color:#888; border-color:#e0e0e0; }}
    .grp-rsvp-btn.cant:hover {{ background:#f5f5f5; border-color:#888; color:#1a1a1a; }}
    .grp-rsvp-btn.cant.active {{ background:#f0f0f0; color:#666; border-color:#bbb; text-decoration:line-through; }}
    .evt-actions-row {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; padding-top:10px; border-top:1px dashed #ececec; }}
    .evt-action-btn {{ font-size:11px; font-weight:600; padding:6px 11px; border:1px solid #d4dbd1; background:#fafbf9; color:#4a6741; cursor:pointer; font-family:inherit; border-radius:999px; letter-spacing:.3px; transition:all .12s; min-height:28px; }}
    .evt-action-btn:hover {{ background:#edf2eb; border-color:#4a6741; color:#3a5334; }}
    .evt-action-primary {{ color:#4a6741; border-color:#9ec097; background:#edf2eb; }}
    .evt-action-primary:hover {{ background:#d4e0d1; border-color:#4a6741; }}
    .evt-action-danger {{ color:#a05439; border-color:#e6cdc1; background:#fbf6f3; }}
    .evt-action-danger:hover {{ background:#f5e7df; border-color:#c4734f; color:#8a3f25; }}

    /* --- Chat affordance — full-width row, looks like a chat preview --- */
    .chat-row-btn {{ display:flex; align-items:center; gap:12px; width:100%; margin-top:14px; padding:11px 14px; background:#fafafa; border:1px solid #e8e8e8; cursor:pointer; font-family:inherit; text-align:left; transition:background .12s, border-color .12s; }}
    .chat-row-btn:hover {{ background:#edf2eb; border-color:#d4e0d1; }}
    .chat-row-icon {{ display:flex; align-items:center; justify-content:center; width:34px; height:34px; flex-shrink:0; background:#edf2eb; color:#4a6741; border-radius:50%; }}
    .chat-row-text {{ flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }}
    .chat-row-primary {{ font-size:13px; font-weight:700; color:#1a1a1a; letter-spacing:.1px; }}
    .chat-row-preview {{ font-size:12px; color:#666; line-height:1.4; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .chat-row-preview strong {{ color:#1a1a1a; font-weight:600; }}
    .chat-row-empty {{ color:#999; font-style:italic; }}
    .chat-row-arrow {{ color:#aaa; font-size:22px; line-height:1; flex-shrink:0; font-weight:400; }}
    .chat-backdrop {{ position:fixed; inset:0; background:rgba(20,20,20,0); pointer-events:none; transition:background .18s; z-index:90; }}
    .chat-sheet {{ position:fixed; right:0; top:0; bottom:0; width:min(420px, 100vw); background:#fff; box-shadow:-8px 0 24px rgba(0,0,0,.08); transform:translateX(100%); transition:transform .22s ease-out; z-index:100; display:flex; flex-direction:column; }}
    .chat-sheet.open {{ transform:translateX(0); }}
    .chat-header {{ display:flex; align-items:center; gap:10px; padding:14px 16px; border-bottom:1px solid #e0e0e0; background:#fafafa; }}
    .chat-header h3 {{ flex:1; margin:0; font-size:15px; font-weight:700; color:#1a1a1a; letter-spacing:0; text-transform:none; line-height:1.3; }}
    .chat-close {{ background:none; border:none; cursor:pointer; color:#888; font-size:22px; padding:4px 8px; line-height:1; font-family:inherit; }}
    .chat-close:hover {{ color:#1a1a1a; }}
    .chat-body {{ flex:1; overflow-y:auto; padding:16px; background:#f8f7f4; display:flex; flex-direction:column; gap:8px; }}
    .chat-empty {{ color:#888; font-size:13px; text-align:center; padding:32px 12px; }}
    .chat-row {{ display:flex; flex-direction:column; max-width:78%; }}
    .chat-row.them {{ align-self:flex-start; align-items:flex-start; }}
    .chat-row.me {{ align-self:flex-end; align-items:flex-end; }}
    .chat-row.grouped {{ margin-top:-4px; }}
    .chat-name {{ font-size:11px; color:#888; font-weight:600; margin-bottom:2px; padding:0 4px; }}
    .chat-bubble {{ position:relative; padding:8px 12px; font-size:14px; line-height:1.4; word-wrap:break-word; white-space:pre-wrap; color:#1a1a1a; }}
    .chat-row.them .chat-bubble {{ background:#fff; border:1px solid #e0e0e0; border-radius:14px 14px 14px 4px; }}
    .chat-row.me .chat-bubble {{ background:#4a6741; color:#fff; border-radius:14px 14px 4px 14px; }}
    .chat-row.grouped.them .chat-bubble, .chat-row.grouped.me .chat-bubble {{ border-radius:14px; }}
    .chat-time {{ font-size:10px; color:#aaa; margin-top:2px; padding:0 6px; }}
    .chat-del {{ display:none; position:absolute; top:-8px; right:-8px; background:#fff; border:1px solid #e0e0e0; color:#888; cursor:pointer; border-radius:50%; width:22px; height:22px; font-size:13px; line-height:1; font-family:inherit; }}
    .chat-row.me:hover .chat-del {{ display:inline-block; }}
    .chat-form {{ display:flex; gap:8px; padding:12px 14px; border-top:1px solid #e0e0e0; background:#fff; }}
    .chat-form input {{ flex:1; min-width:0; padding:11px 14px; border:1px solid #ccc; font-size:14px; font-family:inherit; background:#fff; color:#1a1a1a; outline:none; border-radius:999px; }}
    .chat-form input:focus {{ border-color:#4a6741; box-shadow:0 0 0 3px rgba(74,103,65,.12); }}
    .chat-form button {{ padding:0 18px; background:#4a6741; color:#fff; border:none; font-weight:700; font-size:13px; cursor:pointer; font-family:inherit; text-transform:uppercase; letter-spacing:.5px; border-radius:999px; }}
    .chat-form button:hover {{ background:#3a5334; }}
    @media (max-width:520px) {{ .chat-sheet {{ width:100vw; }} }}
    </style>
    <script>
    window.__aeHome = window.__aeHome || null;
    function restoreAeHome(groupId) {{
        if (!window.__aeHome) return;
        const toggle = document.getElementById('ae-toggle-' + groupId);
        const wrapper = toggle && toggle.parentNode;
        const home = window.__aeHome;
        if (wrapper && home.parent && wrapper !== home.nextSibling) {{
            try {{ home.parent.insertBefore(wrapper, home.nextSibling); }} catch(e) {{}}
        }}
        window.__aeHome = null;
    }}

    function openEditEvent(groupId, eventId) {{
        const card = document.querySelector('.group-event-card[data-event-id="' + eventId + '"]');
        if (!card) return;
        const panel = document.getElementById('ae-panel-' + groupId);
        const toggle = document.getElementById('ae-toggle-' + groupId);
        const wrapper = toggle.parentNode;
        if (wrapper && card.nextSibling !== wrapper) {{
            if (!window.__aeHome) {{
                window.__aeHome = {{parent: wrapper.parentNode, nextSibling: wrapper.nextSibling}};
            }}
            card.parentNode.insertBefore(wrapper, card.nextSibling);
        }}
        if (panel.style.display === 'none' || !panel.style.display) {{
            panel.style.display = 'block';
        }}
        toggle.style.display = 'none';
        document.getElementById('ae-edit-id-' + groupId).value = eventId;
        document.getElementById('ae-title-' + groupId).value = card.dataset.title || '';
        document.getElementById('ae-date-' + groupId).value = card.dataset.date || '';
        document.getElementById('ae-time-' + groupId).value = card.dataset.time || '';
        const endEl = document.getElementById('ae-end-' + groupId);
        if (endEl) endEl.value = card.dataset.end || '';
        document.getElementById('ae-loc-' + groupId).value = card.dataset.location || '';
        const urlEl = document.getElementById('ae-url-' + groupId);
        if (urlEl) urlEl.value = card.dataset.url || '';
        document.getElementById('ae-notes-' + groupId).value = card.dataset.notes || '';
        const prereqEl = document.getElementById('ae-prereq-' + groupId);
        if (prereqEl) prereqEl.value = card.dataset.prerequisites || '';
        const capEl = document.getElementById('ae-cap-' + groupId);
        if (capEl) capEl.value = card.dataset.capacity || '';
        const needsMore = (card.dataset.notes || card.dataset.prerequisites || card.dataset.capacity || card.dataset.url);
        if (needsMore) {{
            const morePanel = document.getElementById('ae-more-' + groupId);
            const moreToggle = document.getElementById('ae-more-toggle-' + groupId);
            if (morePanel && morePanel.style.display === 'none') {{
                morePanel.style.display = 'flex';
                if (moreToggle) moreToggle.classList.add('open');
            }}
        }}
        const recurringField = document.getElementById('ae-recurring-field-' + groupId);
        if (recurringField) recurringField.style.display = 'none';
        const banner = document.getElementById('ae-edit-banner-' + groupId);
        const bannerTitle = document.getElementById('ae-edit-banner-title-' + groupId);
        if (banner) banner.style.display = 'block';
        if (bannerTitle) bannerTitle.textContent = card.dataset.title || '';
        const submit = document.getElementById('ae-submit-' + groupId);
        if (submit) submit.textContent = 'Save changes';
        updateWhenReadout(groupId);
        setTimeout(() => panel.scrollIntoView({{behavior: 'smooth', block: 'nearest'}}), 60);
    }}

    function cancelEdit(groupId) {{
        document.getElementById('ae-edit-id-' + groupId).value = '';
        document.getElementById('add-event-form-' + groupId).reset();
        const banner = document.getElementById('ae-edit-banner-' + groupId);
        if (banner) banner.style.display = 'none';
        const recurringField = document.getElementById('ae-recurring-field-' + groupId);
        if (recurringField) recurringField.style.display = '';
        const submit = document.getElementById('ae-submit-' + groupId);
        if (submit) submit.textContent = 'Add event';
        const toggle = document.getElementById('ae-toggle-' + groupId);
        if (toggle) {{
            toggle.style.display = '';
            toggle.innerHTML = '<span style="font-size:18px;line-height:1;">+</span><span>Add event</span>';
        }}
        const panel = document.getElementById('ae-panel-' + groupId);
        if (panel) panel.style.display = 'none';
        restoreAeHome(groupId);
        updateWhenReadout(groupId);
    }}

    async function kickMember(groupId, userId, btn) {{
        if (!confirm('Remove this member from the group?')) return;
        const resp = await fetch('/api/group/' + groupId + '/kick', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{user_id: userId}})
        }});
        if (resp.ok) location.reload();
    }}

    /* --- Pop-out chat modal --- */
    const __ME_ID__ = {current_user["id"] if current_user else 0};
    let __chatEid = null;
    let __chatPollTimer = null;

    function openChat(eid, eventTitle) {{
        __chatEid = eid;
        const sheet = document.getElementById('chat-sheet');
        document.getElementById('chat-title').textContent = eventTitle || 'Chat';
        sheet.classList.add('open');
        document.body.style.overflow = 'hidden';
        renderChat();
        if (__chatPollTimer) clearInterval(__chatPollTimer);
        __chatPollTimer = setInterval(renderChat, 5000);
        setTimeout(() => {{
            const input = document.getElementById('chat-input');
            if (input) input.focus();
        }}, 80);
    }}

    function closeChat() {{
        const sheet = document.getElementById('chat-sheet');
        sheet.classList.remove('open');
        document.body.style.overflow = '';
        if (__chatPollTimer) {{ clearInterval(__chatPollTimer); __chatPollTimer = null; }}
        __chatEid = null;
    }}

    function fmtChatTime(iso) {{
        try {{
            const d = new Date(iso);
            const today = new Date(); today.setHours(0,0,0,0);
            const day = new Date(d); day.setHours(0,0,0,0);
            const sameDay = day.getTime() === today.getTime();
            const t = d.toLocaleTimeString(undefined, {{hour:'numeric', minute:'2-digit'}});
            return sameDay ? t : d.toLocaleDateString(undefined, {{month:'short', day:'numeric'}}) + ' · ' + t;
        }} catch(e) {{ return ''; }}
    }}

    async function renderChat() {{
        if (!__chatEid) return;
        const body = document.getElementById('chat-body');
        try {{
            const resp = await fetch('/api/event/' + encodeURIComponent(__chatEid) + '/comments');
            const data = await resp.json();
            const msgs = data.comments || [];
            if (!msgs.length) {{
                body.innerHTML = '<div class="chat-empty">No messages yet. Be the first.</div>';
                return;
            }}
            const rows = [];
            let prevUid = null;
            msgs.forEach(m => {{
                const isMe = m.user_id === __ME_ID__;
                const same = m.user_id === prevUid;
                const name = m.user_name || (m.user_email || '').split('@')[0] || '?';
                const safeBody = (m.body || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\\n/g, '<br>');
                const delBtn = isMe ? '<button class="chat-del" title="Delete" onclick="deleteChatMsg(' + m.id + ')">×</button>' : '';
                rows.push(
                    '<div class="chat-row ' + (isMe ? 'me' : 'them') + (same ? ' grouped' : '') + '">' +
                    (same ? '' : '<div class="chat-name">' + (isMe ? 'You' : name) + '</div>') +
                    '<div class="chat-bubble">' + safeBody + delBtn + '</div>' +
                    '<div class="chat-time">' + fmtChatTime(m.created_at) + '</div>' +
                    '</div>'
                );
                prevUid = m.user_id;
            }});
            body.innerHTML = rows.join('');
            body.scrollTop = body.scrollHeight;
        }} catch(e) {{
            body.innerHTML = '<div class="chat-empty">Couldn\\'t load messages.</div>';
        }}
    }}

    async function sendChat(ev) {{
        ev.preventDefault();
        if (!__chatEid) return false;
        const input = document.getElementById('chat-input');
        const text = input.value.trim();
        if (!text) return false;
        input.disabled = true;
        try {{
            const resp = await fetch('/api/event/' + encodeURIComponent(__chatEid) + '/comments', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{body: text}})
            }});
            if (resp.ok) {{
                input.value = '';
                await renderChat();
            }}
        }} catch(e) {{}}
        input.disabled = false;
        input.focus();
        return false;
    }}

    async function deleteChatMsg(commentId) {{
        if (!confirm('Delete this message?')) return;
        try {{
            const resp = await fetch('/api/comment/' + commentId, {{method: 'DELETE'}});
            if (resp.ok) renderChat();
        }} catch(e) {{}}
    }}

    document.addEventListener('keydown', (e) => {{
        if (e.key === 'Escape') {{
            const sheet = document.getElementById('chat-sheet');
            if (sheet && sheet.classList.contains('open')) closeChat();
        }}
    }});

    async function rsvpGroupEvent(groupId, eventId, status, btn) {{
        const container = btn.parentElement;
        const buttons = container.querySelectorAll('.grp-rsvp-btn');
        const wasActive = btn.classList.contains('active');
        buttons.forEach(b => b.classList.remove('active'));
        const newStatus = wasActive ? '' : status;
        if (!wasActive) btn.classList.add('active');
        try {{
            await fetch('/api/rsvp', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{event_id: eventId, status: newStatus, user_token: '{user_token}'}})
            }});
            setTimeout(() => location.reload(), 300);
        }} catch(e) {{ console.error(e); }}
    }}
    </script>
    """

    member_count = len(members)
    _colors = ["#4a6741", "#c4734f", "#5b7fa5", "#8b6b47", "#7a5c8a", "#5a8a6e"]
    _members_html = ""
    from html import escape as _esc_m
    for i, m in enumerate(members):
        initial = ((m.get("name") or m.get("email") or "?")[0]).upper()
        mname = m.get("name") or m.get("email", "").split("@")[0]
        is_me = current_user and m["id"] == current_user["id"]
        color = _colors[i % len(_colors)]
        kick = ""
        if is_creator and current_user and m["id"] != current_user["id"]:
            kick = f'<button onclick="kickMember({group_id},{m["id"]},this)" style="background:none;border:none;color:#ccc;cursor:pointer;font-size:16px;padding:0 4px;margin-left:auto;" title="Remove">&times;</button>'
        border = f"border:2px solid {color};" if is_me else ""
        phone = (m.get("phone") or "").strip()
        phone_html = ""
        if phone:
            _tel = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
            phone_html = f'<a href="tel:{_tel}" style="font-size:12px;color:#6b7280;text-decoration:none;margin-left:6px;">{_esc_m(phone)}</a>'
        elif is_me:
            phone_html = f'<a href="/taste-profile#settings" style="font-size:11px;color:#c4734f;text-decoration:none;margin-left:6px;border-bottom:1px dashed #c4734f;">+ add your phone</a>'
        _members_html += f'''<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{"border-bottom:1px solid #f0f0f0;" if i < len(members)-1 else ""}">
            <div style="width:36px;height:36px;background:{color}15;color:{color};display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:800;flex-shrink:0;{border}">{initial}</div>
            <div style="display:flex;flex-direction:column;flex:1;min-width:0;">
                <span style="font-size:14px;font-weight:{"700" if is_me else "500"};color:#1a1a1a;">{_esc_m(mname)}{"  (you)" if is_me else ""}</span>
                {phone_html}
            </div>
            {kick}
        </div>'''

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

    <details class="group-meta" style="margin-bottom:28px;padding-bottom:18px;border-bottom:1px solid #e0e0e0;">
        <summary style="cursor:pointer;font-size:13px;color:#888;letter-spacing:.5px;padding:6px 0;display:flex;align-items:center;gap:8px;">
            <span class="meta-caret" style="font-size:11px;color:#888;transition:transform .15s;">▸</span>
            <span>{len(members)} member{"s" if len(members) != 1 else ""} · settings</span>
        </summary>
        <div style="margin-top:12px;">
            {_members_html}
            {group_actions_html}
        </div>
    </details>
    <style>.group-meta[open] .meta-caret {{ transform: rotate(90deg); }} .group-meta summary::-webkit-details-marker {{ display:none; }} .group-meta summary {{ list-style: none; }}</style>

    {join_cta}

    {f'<div style="border-bottom:1px solid #e0e0e0;padding-bottom:28px;margin-bottom:28px;">{upcoming_html}</div>' if upcoming_html and "No upcoming" not in upcoming_html else '<p style="color:#888;font-size:14px;margin-bottom:28px;">No upcoming events yet.</p>'}

    {add_event_html}

    {past_html}

    {deleted_html}
    </div>
    <div class="chat-backdrop" onclick="closeChat()"></div>
    <aside id="chat-sheet" class="chat-sheet" role="dialog" aria-modal="true" aria-labelledby="chat-title">
      <div class="chat-header">
        <h3 id="chat-title">Chat</h3>
        <button class="chat-close" onclick="closeChat()" aria-label="Close">×</button>
      </div>
      <div id="chat-body" class="chat-body"></div>
      <form class="chat-form" onsubmit="return sendChat(event)">
        <input id="chat-input" type="text" maxlength="1000" placeholder="Message…" autocomplete="off" {'' if current_user else 'disabled'}>
        <button type="submit">Send</button>
      </form>
    </aside>
    {group_rsvp_extras}
    """, user=current_user, og=og))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/share/event/{group_id:int}/{event_id:int}/{invite_code}", response_class=HTMLResponse)
async def share_event_preview(group_id: int, event_id: int, invite_code: str, request: Request):
    """Public preview of a group event with one-click RSVP. Anyone with the link can RSVP;
    if they're not signed in, the RSVP buttons bounce them through Google sign-in first.
    Acting on a button (the /rsvp/{status} sub-route) auto-joins them to the group."""
    db = get_db()
    group = db.get_group_by_id(group_id)
    if not group or group.get("invite_code") != invite_code:
        return HTMLResponse("<h1>Invalid share link</h1>", status_code=404)
    event = db.get_group_event_by_id(event_id)
    if not event or event["group_id"] != group_id:
        return HTMLResponse("<h1>Event not found</h1>", status_code=404)

    current_user = _get_current_user(request)
    group_name = db.get_group_display_name(group)
    event_id_key = f"grp_evt_{event_id}"

    # Format when
    from datetime import datetime as _dt
    try:
        start = _dt.fromisoformat(event["start_time"])
        when_str = start.strftime("%A, %B %-d · %-I:%M %p")
    except Exception:
        when_str = event.get("start_time", "")

    # Tally current RSVPs (sum across all users for this event_id)
    rsvps = db.get_rsvps_for_events([event_id_key]).get(event_id_key, [])
    going = [r for r in rsvps if r.get("status") == "going"]
    maybe = [r for r in rsvps if r.get("status") == "maybe"]
    going_names = ", ".join(r.get("user_name") or "Someone" for r in going[:8]) or "Be the first"
    going_count = len(going)
    maybe_count = len(maybe)

    # User's existing RSVP (if any)
    my_status = ""
    if current_user:
        for r in rsvps:
            if r.get("user_id") == current_user["id"]:
                my_status = r.get("status", "")
                break

    location_html = f'<div style="font-size:14px;color:#6b7280;margin-bottom:6px;">📍 {event["location"]}</div>' if event.get("location") else ""
    notes_html = f'<div style="font-size:14px;color:#374151;margin-top:14px;background:#fafafa;padding:12px;border-radius:6px;line-height:1.5;">{event["notes"]}</div>' if event.get("notes") else ""
    creator_html = f'<div style="font-size:12px;color:#9ca3af;margin-bottom:18px;">Added by {event.get("creator_name") or event.get("creator_email", "").split("@")[0]} · {group_name}</div>'

    # Build RSVP buttons. If unauthed, each button is a Google sign-in link with next pointing to
    # the rsvp action URL — Google return → action → auto-join + RSVP.
    base = f"/share/event/{group_id}/{event_id}/{invite_code}/rsvp"
    from urllib.parse import quote as _q
    def rsvp_btn(status: str, label: str, color: str, active: bool) -> str:
        href = f"{base}/{status}" if current_user else f"/auth/google/login?next={_q(base + '/' + status, safe='/')}"
        bg = color if active else "#fff"
        fg = "#fff" if active else color
        return f'<a href="{href}" class="share-rsvp-btn" style="display:flex;align-items:center;justify-content:center;flex:1;min-width:0;padding:14px 8px;border:1.5px solid {color};background:{bg};color:{fg};font-size:13px;font-weight:700;text-decoration:none;text-transform:uppercase;letter-spacing:.5px;border-radius:8px;">{label}</a>'

    rsvp_row = f'''<div style="display:flex;gap:8px;margin-bottom:14px;">
        {rsvp_btn("going", "Going", "#4a6741", my_status == "going")}
        {rsvp_btn("maybe", "Maybe", "#c4734f", my_status == "maybe")}
        {rsvp_btn("no", "Can't go", "#888", my_status == "no")}
    </div>'''

    sign_in_hint = ""
    if not current_user:
        sign_in_hint = '<p style="font-size:12px;color:#888;text-align:center;margin-top:6px;">You\'ll sign in with Google to RSVP — we\'ll add you to the group automatically.</p>'

    going_section = f'''<div style="margin-top:20px;padding-top:18px;border-top:1px solid #eee;">
        <div style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">{going_count} going{f" · {maybe_count} maybe" if maybe_count else ""}</div>
        <div style="font-size:14px;color:#1a1a1a;line-height:1.5;">{going_names}</div>
    </div>''' if rsvps else ""

    body = f'''
    <div style="max-width:560px;margin:0 auto;">
        {creator_html}
        <h1 style="margin-bottom:8px;font-size:1.6rem;">{event["title"]}</h1>
        <div style="font-size:15px;color:#4a6741;font-weight:600;margin-bottom:6px;">{when_str}</div>
        {location_html}
        {notes_html}
        <div style="margin-top:24px;">
            {rsvp_row}
            {sign_in_hint}
        </div>
        {going_section}
        <div style="margin-top:32px;padding-top:18px;border-top:1px solid #eee;text-align:center;">
            <a href="/group/{group_id}" style="font-size:13px;color:#6b7280;">View full group →</a>
        </div>
    </div>
    '''
    settings = Settings()
    canonical = f"{settings.dashboard_url}/share/event/{group_id}/{event_id}/{invite_code}"
    og = {
        "title": f"{event['title']} · {group_name}",
        "description": f"{when_str}{' · ' + event['location'] if event.get('location') else ''}",
        "image": f"{settings.dashboard_url}/og/event/{group_id}/{event_id}/{invite_code}.png",
        "url": canonical,
    }
    return HTMLResponse(_layout(event["title"], body, user=current_user, og=og))


@app.get("/share/event/{group_id:int}/{event_id:int}/{invite_code}/rsvp/{status}")
async def share_event_rsvp(group_id: int, event_id: int, invite_code: str, status: str, request: Request):
    """Authed-only action: validate share link, ensure membership, record RSVP, redirect."""
    if status not in ("going", "maybe", "no"):
        return HTMLResponse("<h1>Invalid RSVP status</h1>", status_code=400)
    user = _get_current_user(request)
    if not user:
        from urllib.parse import quote as _q
        nxt = f"/share/event/{group_id}/{event_id}/{invite_code}/rsvp/{status}"
        return RedirectResponse(f"/auth/google/login?next={_q(nxt, safe='/')}", status_code=303)
    db = get_db()
    group = db.get_group_by_id(group_id)
    if not group or group.get("invite_code") != invite_code:
        return HTMLResponse("<h1>Invalid share link</h1>", status_code=404)
    event = db.get_group_event_by_id(event_id)
    if not event or event["group_id"] != group_id:
        return HTMLResponse("<h1>Event not found</h1>", status_code=404)
    if not db.is_group_member(group_id, user["id"]):
        db.add_group_member(group_id, user["id"])
    db.set_rsvp(user["id"], f"grp_evt_{event_id}", 0, status)
    label = {"going": "going", "maybe": "maybe", "no": "can't go"}[status]
    return RedirectResponse(f"/group/{group_id}?success=RSVP+saved+({label})", status_code=303)


# --- OG image helpers (used by /share/event/* unfurl previews) ---

_OG_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_OG_FONT_REGULAR_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _load_font(candidates: list[str], size: int):
    from PIL import ImageFont
    import os
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    """Greedy line wrap to fit max_width pixels."""
    words = text.split()
    lines, line = [], ""
    for w in words:
        candidate = (line + " " + w).strip()
        bbox = font.getbbox(candidate)
        width = bbox[2] - bbox[0]
        if width > max_width and line:
            lines.append(line)
            line = w
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


@app.get("/og/event/{group_id:int}/{event_id:int}/{invite_code}.png")
async def og_event_image(group_id: int, event_id: int, invite_code: str):
    """1200x630 OG image for share/event/* unfurls. Cached for 1h."""
    db = get_db()
    group = db.get_group_by_id(group_id)
    if not group or group.get("invite_code") != invite_code:
        return Response(status_code=404)
    event = db.get_group_event_by_id(event_id)
    if not event or event["group_id"] != group_id:
        return Response(status_code=404)

    from PIL import Image, ImageDraw
    from datetime import datetime as _dt
    import io

    W, H = 1200, 630
    SAGE = (74, 103, 65)
    SAGE_DARK = (58, 83, 52)
    TERRA = (196, 115, 79)
    WHITE = (255, 255, 255)
    OFF_WHITE = (240, 238, 232)

    img = Image.new("RGB", (W, H), SAGE)
    draw = ImageDraw.Draw(img)

    # Subtle vertical gradient (sage → darker sage)
    for y in range(H):
        t = y / H
        r = int(SAGE[0] * (1 - t) + SAGE_DARK[0] * t)
        g = int(SAGE[1] * (1 - t) + SAGE_DARK[1] * t)
        b = int(SAGE[2] * (1 - t) + SAGE_DARK[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Decorative leaf accent (top-right, soft)
    for r in range(220, 50, -10):
        alpha_color = (
            min(255, SAGE[0] + (220 - r) // 4),
            min(255, SAGE[1] + (220 - r) // 4),
            min(255, SAGE[2] + (220 - r) // 4),
        )
        draw.ellipse([(W - 60 - r, -r // 2), (W - 60 + r, r * 2)], outline=alpha_color, width=1)

    # Calyx wordmark (top-left)
    logo_font = _load_font(_OG_FONT_CANDIDATES, 36)
    draw.text((60, 50), "calyx", fill=WHITE, font=logo_font)

    # Group name chip (top-left under logo)
    group_name = db.get_group_display_name(group)
    group_font = _load_font(_OG_FONT_REGULAR_CANDIDATES, 22)
    draw.text((60, 102), group_name.upper(), fill=OFF_WHITE, font=group_font)

    # Event title (large, wrapped)
    title = event.get("title") or "Event"
    title_font = _load_font(_OG_FONT_CANDIDATES, 78)
    title_lines = _wrap_text(title, title_font, max_width=W - 120)[:3]  # max 3 lines
    title_y = 200
    for line in title_lines:
        draw.text((60, title_y), line, fill=WHITE, font=title_font)
        bbox = title_font.getbbox(line)
        title_y += (bbox[3] - bbox[1]) + 12

    # When (large)
    try:
        start = _dt.fromisoformat(event["start_time"])
        when_str = start.strftime("%A, %B %-d · %-I:%M %p")
    except Exception:
        when_str = event.get("start_time", "")
    when_font = _load_font(_OG_FONT_CANDIDATES, 42)
    when_y = max(title_y + 30, 470)
    draw.text((60, when_y), when_str, fill=(220, 230, 215), font=when_font)

    # Location (smaller)
    loc = event.get("location")
    if loc:
        loc_font = _load_font(_OG_FONT_REGULAR_CANDIDATES, 30)
        # Tiny terracotta circle as a "pin", then the location text
        cx, cy = 70, when_y + 78
        draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)], fill=TERRA)
        draw.ellipse([(cx - 3, cy - 3), (cx + 3, cy + 3)], fill=SAGE_DARK)
        draw.text((90, when_y + 60), loc, fill=OFF_WHITE, font=loc_font)

    # Bottom bar accent
    draw.rectangle([(0, H - 12), (W, H)], fill=TERRA)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
    end_time_input = (form.get("end_time") or "").strip()
    location = (form.get("location") or "").strip()
    event_url = (form.get("url") or "").strip()
    notes = (form.get("notes") or "").strip()
    prerequisites = (form.get("prerequisites") or "").strip()
    capacity_raw = (form.get("capacity") or "").strip()
    try:
        capacity = int(capacity_raw) if capacity_raw else None
        if capacity is not None and capacity <= 0:
            capacity = None
    except ValueError:
        capacity = None
    edit_id_str = (form.get("edit_id") or "").strip()
    if not title:
        return HTMLResponse("<h1>Title required</h1>", status_code=400)
    repeat_weeks = int(form.get("recurring") or "0")
    from datetime import datetime as _dt, timedelta as _td
    settings = Settings()
    start_time = f"{date}T{time}:00" if date else ""
    end_time = f"{date}T{end_time_input}:00" if (date and end_time_input) else ""

    # --- Edit mode: update existing event, no recurring expansion ---
    if edit_id_str:
        try:
            edit_id = int(edit_id_str)
        except ValueError:
            return HTMLResponse("<h1>Invalid edit id</h1>", status_code=400)
        existing = db.get_group_event_by_id(edit_id)
        if not existing or existing["group_id"] != group_id:
            return HTMLResponse("<h1>Event not found</h1>", status_code=404)
        ok = db.update_group_event(edit_id, user["id"], title=title, start_time=start_time,
                                   end_time=end_time, location=location, url=event_url,
                                   notes=notes, capacity=capacity, prerequisites=prerequisites)
        if not ok:
            return HTMLResponse("<h1>Not allowed</h1>", status_code=403)
        ue_eid = f"grp_evt_{edit_id}"
        if capacity is not None:
            going_count = db.count_rsvps(ue_eid, "going")
            if going_count > capacity:
                overflow = going_count - capacity
                rows = db.conn.execute(
                    "SELECT id FROM rsvps WHERE event_id = ? AND status = 'going' ORDER BY created_at DESC LIMIT ?",
                    (ue_eid, overflow),
                ).fetchall()
                for r in rows:
                    db.conn.execute("UPDATE rsvps SET status = 'waitlist' WHERE id = ?", (r["id"],))
                db.conn.commit()
            else:
                db._promote_waitlist(ue_eid)
        try:
            updated = db.get_group_event_by_id(edit_id)
            members = db.get_group_members(group_id)
            going_ids = {r["user_id"] for r in db.conn.execute(
                "SELECT user_id FROM rsvps WHERE event_id = ? AND status IN ('going','maybe')",
                (ue_eid,)).fetchall()}
            was_scheduled = bool((existing.get("start_time") or "").strip())
            now_scheduled = bool(start_time)
            if was_scheduled and not now_scheduled:
                send_event_invites_to_members(
                    settings=settings, event_id=ue_eid, title=existing.get("title") or title,
                    start_time=existing.get("start_time"), end_time=existing.get("end_time"),
                    location=existing.get("location") or "", description=notes,
                    url=event_url, members=members, organizer_user=user,
                    accepted_user_ids=set(), method="CANCEL",
                    sequence=int(updated.get("gcal_sequence") or 0),
                    group_name=group.get("display_name") or group.get("name") or "",
                )
            elif now_scheduled:
                send_event_invites_to_members(
                    settings=settings, event_id=ue_eid, title=title,
                    start_time=start_time, end_time=end_time, location=location,
                    description=notes, url=event_url, members=members,
                    organizer_user=user, accepted_user_ids=going_ids,
                    method="REQUEST", sequence=int(updated.get("gcal_sequence") or 0),
                    group_name=group.get("display_name") or group.get("name") or "",
                )
        except Exception:
            logger.exception("Failed to send updated calendar invite for event %d", edit_id)
        return RedirectResponse(f"/group/{group_id}?success=Event+updated", status_code=303)

    # --- Create mode ---
    event_row_id = db.add_group_event(group_id, user["id"], title, start_time,
                                       end_time=end_time, location=location, url=event_url,
                                       notes=notes, capacity=capacity, prerequisites=prerequisites)
    ue_eid = f"grp_evt_{event_row_id}"
    db.set_rsvp(user["id"], ue_eid, 0, "going")
    if repeat_weeks > 1 and start_time:
        base_date = _dt.fromisoformat(start_time)
        end_base = _dt.fromisoformat(end_time) if end_time else None
        for week in range(1, repeat_weeks):
            next_dt = base_date + _td(weeks=week)
            next_end = (end_base + _td(weeks=week)).isoformat() if end_base else ""
            db.add_group_event(group_id, user["id"], title, next_dt.isoformat(),
                               end_time=next_end, location=location, url=event_url,
                               notes=notes, capacity=capacity, prerequisites=prerequisites)
    if start_time:
        try:
            members = db.get_group_members(group_id)
            muted_users = set()
            for m in members:
                mrow = db.conn.execute(
                    "SELECT notifications FROM group_members WHERE group_id=? AND user_id=?",
                    (group_id, m["id"]),
                ).fetchone()
                if mrow and not mrow["notifications"]:
                    muted_users.add(m["id"])
            invitees = [m for m in members if m["id"] not in muted_users and m.get("email")]
            send_event_invites_to_members(
                settings=settings, event_id=ue_eid, title=title,
                start_time=start_time, end_time=end_time, location=location,
                description=notes, url=event_url, members=invitees,
                organizer_user=user, accepted_user_ids={user["id"]},
                method="REQUEST", sequence=0,
                group_name=group.get("display_name") or group.get("name") or "",
            )
        except Exception:
            logger.exception("Failed to send calendar invites for group %d", group_id)
    return RedirectResponse(f"/group/{group_id}?success=Event+added", status_code=303)


@app.get("/api/event/{event_id}/comments", response_class=JSONResponse)
async def api_get_event_comments(event_id: str):
    db = get_db()
    return {"comments": db.get_event_comments(event_id)}


@app.post("/api/event/{event_id}/comments", response_class=JSONResponse)
async def api_post_event_comment(event_id: str, request: Request):
    user = _get_current_user(request)
    if not user:
        return JSONResponse({"error": "Sign in required"}, status_code=401)
    db = get_db()
    ev = db.conn.execute(
        "SELECT group_id, source FROM events WHERE event_id = ? LIMIT 1", (event_id,)
    ).fetchone()
    if ev and ev["group_id"] and not db.is_group_member(ev["group_id"], user["id"]):
        return JSONResponse({"error": "Not a group member"}, status_code=403)
    data = await request.json()
    body = (data.get("body") or "").strip()
    if not body or len(body) > 1000:
        return JSONResponse({"error": "Comment must be 1–1000 chars"}, status_code=400)
    cid = db.add_event_comment(event_id, user["id"], body)
    return {"ok": True, "id": cid}


@app.delete("/api/comment/{comment_id:int}", response_class=JSONResponse)
async def api_delete_comment(comment_id: int, request: Request):
    user = _get_current_user(request)
    if not user:
        return JSONResponse({"error": "Sign in required"}, status_code=401)
    db = get_db()
    if not db.delete_event_comment(comment_id, user["id"]):
        return JSONResponse({"error": "Not allowed"}, status_code=403)
    return {"ok": True}


@app.post("/api/group/{group_id:int}/restore-event")
async def api_group_restore_event(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    if not db.is_group_member(group_id, user["id"]):
        return HTMLResponse("<h1>Not a member</h1>", status_code=403)
    form = await request.form()
    event_id = int(form.get("event_id") or 0)
    if event_id:
        db.restore_group_event(event_id, user["id"])
    return RedirectResponse(f"/group/{group_id}?success=Event+restored", status_code=303)


@app.post("/api/group/{group_id:int}/purge-event")
async def api_group_purge_event(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    if not db.is_group_member(group_id, user["id"]):
        return HTMLResponse("<h1>Not a member</h1>", status_code=403)
    form = await request.form()
    event_id = int(form.get("event_id") or 0)
    if event_id:
        db.purge_group_event(event_id, user["id"])
    return RedirectResponse(f"/group/{group_id}?success=Event+permanently+removed", status_code=303)


@app.post("/api/group/{group_id:int}/delete-event")
async def api_group_delete_event(group_id: int, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    form = await request.form()
    event_id = int(form.get("event_id") or 0)
    if event_id:
        existing = db.get_group_event_by_id(event_id)
        if existing and existing["group_id"] == group_id and (existing.get("start_time") or "").strip():
            ue_eid = f"grp_evt_{event_id}"
            try:
                settings = Settings()
                members = db.get_group_members(group_id)
                send_event_invites_to_members(
                    settings=settings, event_id=ue_eid, title=existing.get("title") or "",
                    start_time=existing.get("start_time"), end_time=existing.get("end_time"),
                    location=existing.get("location") or "", description=existing.get("notes") or "",
                    url=existing.get("url") or "", members=members, organizer_user=user,
                    accepted_user_ids=set(), method="CANCEL",
                    sequence=int(existing.get("gcal_sequence") or 0) + 1,
                    group_name="",
                )
            except Exception:
                logger.exception("Failed to send CANCEL invite for event %d", event_id)
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

@app.post("/api/group/{group_id:int}/mute")
async def api_group_mute(group_id: int, request: Request):
    """Toggle notification mute for current user in this group."""
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    row = db.conn.execute(
        "SELECT notifications FROM group_members WHERE group_id=? AND user_id=?",
        (group_id, user["id"]),
    ).fetchone()
    if not row:
        return JSONResponse({"ok": False}, status_code=404)
    new_val = 0 if row["notifications"] else 1
    db.conn.execute(
        "UPDATE group_members SET notifications=? WHERE group_id=? AND user_id=?",
        (new_val, group_id, user["id"]),
    )
    db.conn.commit()
    return JSONResponse({"ok": True, "notifications": new_val})


@app.post("/api/group/{group_id:int}/kick")
async def api_group_kick(group_id: int, request: Request):
    """Creator can remove a member from the group."""
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    group = db.get_group_by_id(group_id)
    if not group or group["created_by"] != user["id"]:
        return JSONResponse({"ok": False, "error": "Only the group creator can remove members"}, status_code=403)
    body = await request.json()
    member_id = body.get("user_id")
    if not member_id or member_id == user["id"]:
        return JSONResponse({"ok": False, "error": "Invalid"})
    db.leave_group(group_id, member_id)
    return JSONResponse({"ok": True})


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


def _google_signin_cta(next_url: str = "/groups", heading: str | None = None, subhead: str | None = None) -> str:
    """Render the standard Google sign-in CTA card. Used by /login and invite flows."""
    from urllib.parse import quote
    href = f"/auth/google/login?next={quote(next_url, safe='/')}"
    h = heading or "Sign in to Calyx"
    sub = subhead or "Find events and make plans with friends."
    google_g = '<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg"><path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/><path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/><path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/><path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/></svg>'
    return f"""
    <div class="app-content" style="max-width:480px;">
    <div style="text-align:center;padding:48px 0 24px;">
      <div style="font-size:13px;font-weight:700;letter-spacing:2px;color:#4a6741;text-transform:uppercase;margin-bottom:12px;">◉ CALYX</div>
      <h1 style="font-size:28px;font-weight:800;letter-spacing:-.5px;margin-bottom:10px;">{h}</h1>
      <p style="color:#6b7280;font-size:15px;line-height:1.55;">{sub}</p>
    </div>
    <div class="card" style="text-align:center;padding:28px 24px;">
      <a href="{href}" style="display:inline-flex;align-items:center;gap:10px;padding:12px 20px;background:#fff;border:1.5px solid #dadce0;color:#3c4043;font-size:15px;font-weight:600;text-decoration:none;border-radius:8px;transition:all .15s;">
        {google_g}
        <span>Continue with Google</span>
      </a>
      <p style="margin-top:16px;font-size:12px;color:#9ca3af;line-height:1.5;">We use Google to sign you in. New here? Same button — your account is created automatically.</p>
    </div>
    </div>
    """


@app.get("/login", response_class=HTMLResponse)
async def login_page(next: str = "/groups"):
    body = _google_signin_cta(next_url=next)
    page_html = LAYOUT_STYLE.replace("__TITLE__", "Sign in to Calyx").replace("__OG_TAGS__", "") + render_nav(None) + body + LAYOUT_FOOT
    return HTMLResponse(page_html)


@app.get("/auth/google/login")
async def auth_google_login(request: Request, next: str = "/groups"):
    settings = Settings()
    if not settings.google_client_id or not settings.google_client_secret:
        return HTMLResponse(
            "<h1>Google sign-in not configured</h1><p>Set <code>RECOM_GOOGLE_CLIENT_ID</code> and <code>RECOM_GOOGLE_CLIENT_SECRET</code> in <code>.env</code>.</p>",
            status_code=500,
        )
    state = secrets.token_urlsafe(16)
    redirect_uri = _oauth_redirect_uri(settings)
    url, verifier = google_login_url(settings.google_client_id, settings.google_client_secret, redirect_uri, state)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="lax")
    response.set_cookie("oauth_verifier", verifier, max_age=600, httponly=True, samesite="lax")
    response.set_cookie("oauth_next", next if next.startswith("/") else "/groups", max_age=600, httponly=True, samesite="lax")
    return response


@app.get("/welcome", response_class=HTMLResponse)
async def welcome_page(request: Request, next: str = "/groups"):
    """Post-signup phone collection. New users land here before reaching their actual destination."""
    user = _get_current_user(request)
    if not user:
        return RedirectResponse(f"/login?next={next}", status_code=303)
    if (user.get("phone") or "").strip():
        return RedirectResponse(next if next.startswith("/") else "/groups", status_code=303)
    nxt_safe = next if next.startswith("/") else "/groups"
    name = (user.get("name") or "").split()[0] or "there"
    body = f"""
    <style>
      .welcome-card {{ max-width: 460px; margin: 60px auto 0; padding: 32px 28px; border: 1px solid #e0e0e0; }}
      .welcome-card h1 {{ font-size: 1.6rem; font-weight: 800; color: #1a1a1a; letter-spacing: -.5px; margin: 0 0 8px; }}
      .welcome-card p.sub {{ font-size: 14px; color: #555; line-height: 1.55; margin: 0 0 24px; }}
      .welcome-card label {{ display:block; font-size: 11px; font-weight: 700; color: #4a6741; text-transform: uppercase; letter-spacing: .8px; margin-bottom: 8px; }}
      .welcome-card input {{ width: 100%; padding: 12px 14px; border: 1px solid #ccc; font-size: 16px; font-family: inherit; background: #fff; color: #1a1a1a; outline: none; transition: border-color .12s, box-shadow .12s; box-sizing: border-box; }}
      .welcome-card input:focus {{ border-color: #4a6741; box-shadow: 0 0 0 3px rgba(74,103,65,.12); }}
      .welcome-card .actions {{ display:flex; gap:8px; margin-top:18px; align-items:center; }}
      .welcome-card .primary {{ flex:1; padding: 13px 18px; background: #4a6741; color: #fff; border: none; font-size: 14px; font-weight: 700; cursor: pointer; font-family: inherit; letter-spacing: .3px; }}
      .welcome-card .primary:hover {{ background: #3a5334; }}
      .welcome-card .skip {{ background: none; border: none; color: #888; cursor: pointer; font-size: 13px; padding: 12px 14px; font-family: inherit; }}
      .welcome-card .skip:hover {{ color: #1a1a1a; text-decoration: underline; }}
    </style>
    <form class="welcome-card" action="/welcome" method="post">
      <input type="hidden" name="next" value="{nxt_safe}">
      <h1>Welcome, {name}.</h1>
      <p class="sub">One thing before you head in: drop your phone so your group-mates can reach you without re-pasting it everywhere.</p>
      <label for="phone">Phone</label>
      <input id="phone" name="phone" type="tel" placeholder="+1 555 123 4567" autocomplete="tel" autofocus>
      <div class="actions">
        <button type="submit" class="primary">Continue →</button>
        <a href="{nxt_safe}" class="skip">Skip for now</a>
      </div>
    </form>
    """
    return HTMLResponse(_layout("Welcome", body, user))


@app.post("/welcome")
async def welcome_submit(request: Request):
    user = _get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    phone = (form.get("phone") or "").strip()[:30]
    nxt = (form.get("next") or "/groups").strip() or "/groups"
    if not nxt.startswith("/"):
        nxt = "/groups"
    if phone:
        import re as _re
        cleaned = _re.sub(r"[^\d+\-\s()]", "", phone)
        db = get_db()
        db.update_user(user["id"], phone=cleaned)
    return RedirectResponse(nxt, status_code=303)


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<h1>Sign-in failed</h1><p>{error}</p><p><a href='/login'>Try again</a></p>", status_code=400)
    cookie_state = request.cookies.get("oauth_state", "")
    if not state or state != cookie_state:
        return HTMLResponse("<h1>Invalid OAuth state</h1><p><a href='/login'>Try again</a></p>", status_code=400)
    settings = Settings()
    redirect_uri = _oauth_redirect_uri(settings)
    verifier = request.cookies.get("oauth_verifier", "")
    try:
        info = google_exchange_code(settings.google_client_id, settings.google_client_secret, redirect_uri, code, code_verifier=verifier)
    except Exception:
        logger.exception("Google OAuth exchange failed")
        return HTMLResponse("<h1>Sign-in failed</h1><p><a href='/login'>Try again</a></p>", status_code=500)
    email = info["email"]
    if not email or not info.get("email_verified"):
        return HTMLResponse("<h1>Email not verified by Google</h1>", status_code=400)
    db = get_db()
    user = db.get_user_by_email(email)
    is_new = False
    if not user:
        is_new = True
        user_id = db.create_user(email, info.get("name", ""))
        db.seed_taste_items(user_id)
        user = db.get_user(user_id)
    nxt = request.cookies.get("oauth_next", "/groups") or "/groups"
    if not nxt.startswith("/"):
        nxt = "/groups"
    # Force phone collection as part of signup. Existing users with no phone get caught too —
    # the persistent banner is the fallback; this is the first-class flow.
    if not (user.get("phone") or "").strip():
        from urllib.parse import quote as _q
        nxt = f"/welcome?next={_q(nxt, safe='/')}"
    response = RedirectResponse(nxt, status_code=303)
    _set_token_cookie(response, user["user_token"])
    response.delete_cookie("oauth_state")
    response.delete_cookie("oauth_verifier")
    response.delete_cookie("oauth_next")
    return response


@app.get("/auth/logout")
async def auth_logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


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


@app.get("/e/{event_id}", response_class=HTMLResponse)
async def public_event_page(event_id: str, request: Request):
    """Public shareable event detail page — works without login."""
    db = get_db()
    current_user = _get_current_user(request)
    event = _find_event(db, event_id)
    if not event:
        return HTMLResponse(_layout("Event Not Found", "<h1>Event not found</h1><p>This event may have expired.</p>", current_user), status_code=404)

    title = event.get("title", "Event")
    desc = event.get("description", "")
    location = event.get("location_name", "")
    url = event.get("url", "")
    score = int(event.get("score") or 0)
    vibe = event.get("vibe", "")
    reason = event.get("match_reason", "")

    time_str = ""
    if event.get("start_time"):
        try:
            from datetime import datetime as _dt
            d = _dt.fromisoformat(event["start_time"])
            time_str = d.strftime("%A, %B %-d at %-I:%M %p")
        except (ValueError, TypeError):
            time_str = event["start_time"][:16]

    # RSVP info
    rsvps = db.get_rsvps_for_events([event_id]).get(event_id, [])
    going = [r for r in rsvps if r["status"] == "going"]
    maybe = [r for r in rsvps if r["status"] == "maybe"]
    social_html = ""
    if going or maybe:
        names = ", ".join(r["user_name"] for r in (going + maybe)[:5])
        social_html = f'<div style="margin-top:16px;padding:12px;background:#f4f7f3;border:1px solid #e0e0e0;font-size:13px;"><strong>{len(going)} going</strong>{f", {len(maybe)} maybe" if maybe else ""} &mdash; {names}</div>'

    og = {
        "title": title,
        "description": (desc or reason)[:200],
        "image": event.get("image_url", ""),
        "url": f"https://calyx.arthgupta.dev/e/{event_id}",
    }

    body = f"""
<div style="max-width:560px;margin:0 auto;padding:40px 0;">
  <h1 style="margin-bottom:8px;">{title}</h1>
  <div style="font-size:15px;color:#555;margin-bottom:4px;">{time_str}</div>
  {"<div style='font-size:14px;color:#888;margin-bottom:16px;'>" + location + "</div>" if location else ""}
  {f'<div style="font-size:14px;color:#555;line-height:1.6;margin-bottom:16px;">{desc[:500]}</div>' if desc else ""}
  {f'<div style="font-size:13px;color:#4a6741;font-style:italic;margin-bottom:16px;">{reason}</div>' if reason else ""}
  {social_html}
  <div style="display:flex;gap:10px;margin-top:20px;">
    {f'<a href="{url}" target="_blank" class="btn-primary" style="text-decoration:none;">View event</a>' if url else ""}
    <a href="/event/{event_id}.ics" class="btn-secondary" style="text-decoration:none;">Add to calendar</a>
  </div>
  {f'<div style="margin-top:24px;"><a href="/join" style="font-size:13px;">Join Calyx to RSVP and get personalized picks</a></div>' if not current_user else ""}
</div>
"""
    return HTMLResponse(_layout(title, body, current_user, og))


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
async def user_ical_feed(token: str):
    """Per-user iCal feed. Composition (token IS the auth — calendar clients can't OAuth):

      - Always: events you've RSVP'd `going` or `maybe` to.
      - Always: events your group-mates have RSVP'd `going` or `maybe` to.
      - Toggle (Settings → "Include recommendations"): up to 2 top-scored discovered events
        per day from your latest pipeline run.

    SUMMARY prefixes give visual distinction (calendar apps can't recolor per-event reliably):
      [GroupName] for group events, ★ for recommendations, [→ Sarah going] for friend events.
    """
    db = get_db()
    user = db.get_user_by_token(token)
    _empty = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//calyx//Plans//EN\r\nCALSCALE:GREGORIAN\r\nX-WR-CALNAME:Calyx Plans\r\nEND:VCALENDAR"
    if not user:
        return Response(content=_empty, media_type="text/calendar")

    import html as _html
    from datetime import timezone as _tz, datetime as _dt
    from collections import defaultdict

    def _esc(text):
        text = _html.unescape(str(text or ""))
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def _fold(line):
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

    utcnow = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    user_name = user.get("name") or user.get("email", "")
    user_email = user.get("email", "")
    settings = Settings()
    dashboard_url = settings.dashboard_url
    include_recs = bool(user.get("feed_include_recs"))

    # Set of (event_id, kind, label_suffix) tuples to include. Each event_id appears once.
    # kind ∈ {"my_going","my_maybe","friend","rec"} — drives the prefix.
    seen: dict[str, dict] = {}

    def _add(event_id: str, kind: str, label: str = ""):
        if event_id not in seen:
            seen[event_id] = {"kind": kind, "label": label}

    # 1. My going + maybe
    rows = db.conn.execute(
        "SELECT event_id, status FROM rsvps WHERE user_id = ? AND status IN ('going','maybe')",
        (user["id"],),
    ).fetchall()
    for r in rows:
        _add(r["event_id"], "my_going" if r["status"] == "going" else "my_maybe")

    # 2. Group-mate going/maybe RSVPs
    group_member_ids = set()
    for g in db.get_user_groups(user["id"]):
        for m in db.get_group_members(g["id"]):
            if m["id"] != user["id"]:
                group_member_ids.add(m["id"])
    if group_member_ids:
        ph = ",".join("?" * len(group_member_ids))
        friend_rows = db.conn.execute(
            f"""SELECT r.event_id, r.status, u.name, u.email
                FROM rsvps r JOIN users u ON u.id = r.user_id
                WHERE r.user_id IN ({ph}) AND r.status IN ('going','maybe')""",
            list(group_member_ids),
        ).fetchall()
        # Bucket per event_id, choose first friend label
        friend_first: dict[str, str] = {}
        for fr in friend_rows:
            eid = fr["event_id"]
            if eid in friend_first:
                continue
            fname = (fr["name"] or fr["email"] or "?").split()[0]
            friend_first[eid] = f"→ {fname} {fr['status']}"
        for eid, label in friend_first.items():
            _add(eid, "friend", label)

    # 3. Recommendations (toggle): top 2/day from latest run
    if include_recs:
        run = db.get_user_latest_run(user["id"])
        if run:
            kept = [e for e in db.get_run_events(run["id"]) if e.get("keep") and e.get("start_time") and (e.get("score") or 0) >= 50]
            kept.sort(key=lambda x: -(x.get("score") or 0))
            per_day: dict[str, int] = defaultdict(int)
            for e in kept:
                day = (e.get("start_time") or "")[:10]
                if per_day[day] >= 2:
                    continue
                per_day[day] += 1
                _add(e.get("event_id", ""), "rec")

    if not seen:
        return Response(content=_empty, media_type="text/calendar")

    # Fetch event details for every collected id
    placeholders = ",".join("?" * len(seen))
    detail_rows = db.conn.execute(
        f"""SELECT e.event_id, e.title, e.start_time, e.end_time, e.location_name, e.url,
                   e.notes, e.source, e.group_id, e.lat, e.lon,
                   COALESCE(g.display_name, '') as group_name
            FROM events e
            LEFT JOIN groups g ON g.id = e.group_id
            WHERE e.event_id IN ({placeholders})
              AND e.start_time IS NOT NULL
              AND (e.deleted_at IS NULL OR e.source <> 'manual')
            GROUP BY e.event_id""",
        list(seen.keys()),
    ).fetchall()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//calyx//Plans {_esc(user_name)}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Calyx — {_esc(user_name)}'s Plans",
        "X-APPLE-CALENDAR-COLOR:#4a6741",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]
    for r in detail_rows:
        try:
            dt = _dt.fromisoformat(r["start_time"])
        except (ValueError, TypeError):
            continue
        info = seen[r["event_id"]]
        kind = info["kind"]
        label = info["label"]
        dtstart = dt.strftime("%Y%m%dT%H%M%S")
        dtend = ""
        if r["end_time"]:
            try:
                dtend = _dt.fromisoformat(r["end_time"]).strftime("%Y%m%dT%H%M%S")
            except (ValueError, TypeError):
                pass
        is_manual = r["source"] == "manual"
        title = _esc(r["title"])
        location = _esc(r["location_name"])
        url = r["url"] or (f"{dashboard_url}/group/{r['group_id']}" if r["group_id"] else "")
        notes = _esc(r["notes"] or "")

        # SUMMARY prefix by kind
        if kind == "my_going":
            prefix = f"[{_esc(r['group_name'])}] " if is_manual and r["group_name"] else ""
        elif kind == "my_maybe":
            prefix = "(?) "
        elif kind == "friend":
            prefix = f"[{_esc(label)}] "
        elif kind == "rec":
            prefix = "★ "
        else:
            prefix = ""

        desc_parts = []
        if r["group_name"]:
            desc_parts.append(f"From {_esc(r['group_name'])} on Calyx")
        if kind == "rec":
            desc_parts.append("Recommended for you")
        if kind == "friend":
            desc_parts.append(_esc(label))
        if notes:
            desc_parts.append(notes)
        desc = "\\n\\n".join(desc_parts) if desc_parts else "Event from Calyx"
        # Only `my_going` is OPAQUE (busy); everything else stays TENTATIVE/TRANSPARENT.
        transp = "OPAQUE" if kind == "my_going" else "TRANSPARENT"

        vevent = [
            "BEGIN:VEVENT",
            f"UID:{r['event_id']}-{kind}@calyx-{token}",
            f"DTSTAMP:{utcnow}",
            f"DTSTART:{dtstart}",
        ]
        if dtend:
            vevent.append(f"DTEND:{dtend}")
        else:
            vevent.append("DURATION:PT2H")
        vevent.extend([
            _fold(f"SUMMARY:{prefix}{title}"),
            _fold(f"LOCATION:{location}"),
            _fold(f"URL:{url}"),
            _fold(f"DESCRIPTION:{desc}"),
            f"CATEGORIES:{kind}",
            f"TRANSP:{transp}",
        ])
        if r["lat"] and r["lon"]:
            vevent.append(f"GEO:{r['lat']};{r['lon']}")
        if user_email and kind in ("my_going", "my_maybe"):
            partstat = "ACCEPTED" if kind == "my_going" else "TENTATIVE"
            vevent.append(f"ATTENDEE;PARTSTAT={partstat};CN={_esc(user_name)}:mailto:{user_email}")
        vevent.extend([
            "BEGIN:VALARM",
            "TRIGGER:-PT2H",
            "ACTION:DISPLAY",
            f"DESCRIPTION:Reminder: {title}",
            "END:VALARM",
            "END:VEVENT",
        ])
        lines.extend(vevent)

    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar",
        headers={"Content-Disposition": f"inline; filename=calyx-{token}.ics"},
    )






def run():
    """Entry point for calyx-dashboard command."""
    import uvicorn
    uvicorn.run(
        "calyx.dashboard.app:app",
        host="0.0.0.0", port=8000,
        reload=True,
        reload_dirs=["src/calyx"],
    )
