from __future__ import annotations

import json
import logging

from datetime import datetime

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.responses import RedirectResponse

from recom.config import Settings
from recom.db import Database
from recom.email.sender import send_magic_link, send_invite_email, send_rsvp_notify

logger = logging.getLogger(__name__)

app = FastAPI(title="Recom Dashboard")

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
        is_admin = user.get("id") == 1
        admin_overflow = '<a href="/admin" class="nav-overflow-item">Admin</a>' if is_admin else ""
        return f"""<nav class="app-nav"><div class="app-nav-inner">
          <a href="/" class="app-logo">recom</a>
          <a href="/" class="nav-link">Events</a>
          <a href="/groups" class="nav-link">Groups</a>
          <a href="/search" class="nav-link">Search</a>
          <a href="/profile" class="nav-link">Profile</a>
          <div class="nav-overflow">
            <button class="nav-overflow-btn" onclick="this.nextElementSibling.classList.toggle('open')" aria-label="More">···</button>
            <div class="nav-overflow-menu">
              <a href="/attended" class="nav-overflow-item">History</a>
              <a href="/taste" class="nav-overflow-item">Taste</a>
              <a href="/venues" class="nav-overflow-item">Venues</a>
              {admin_overflow}
            </div>
          </div>
          <div class="nav-divider"></div>
          <span style="font-size:13px;color:rgba(255,255,255,.7);font-weight:500;">{name}</span>
        </div></nav>"""
    return """<nav class="app-nav"><div class="app-nav-inner">
      <a href="/" class="app-logo">recom</a>
      <a href="/" class="nav-link">Events</a>
      <a href="/groups" class="nav-link">Groups</a>
      <a href="/landing" class="nav-link">About</a>
      <a href="/login" class="nav-link">Login</a>
    </div></nav>"""


def _layout(title: str, body: str, user: dict | None = None) -> str:
    nav = render_nav(user)
    return LAYOUT_STYLE.replace("__TITLE__", title) + nav + '<div class="app-content">' + body + LAYOUT_FOOT


LAYOUT_STYLE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#1a1a2e">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Recom — __TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f4f4f8; color: #1a1a1a; line-height: 1.5; min-height: 100vh; }
  /* --- App shell --- */
  .app-nav { background: #1a1a2e; padding: 0 16px; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 12px rgba(0,0,0,.25); }
  .app-nav-inner { display: flex; align-items: center; max-width: 960px; margin: 0 auto; height: 52px; gap: 8px; }
  .app-logo { font-size: 17px; font-weight: 800; color: white; text-decoration: none; letter-spacing: -.5px; margin-right: auto; display: flex; align-items: center; gap: 6px; }
  .app-logo::before { content: '◉'; color: #818cf8; font-size: 13px; }
  .app-logo:hover { text-decoration: none; opacity: .9; }
  .app-nav a.nav-link { font-size: 13px; font-weight: 500; color: rgba(255,255,255,.6); text-decoration: none; padding: 6px 12px; border-radius: 8px; transition: all .15s; }
  .app-nav a.nav-link:hover { background: rgba(255,255,255,.1); color: white; text-decoration: none; }
  .app-nav a.nav-link.active { color: white; background: rgba(129,140,248,.25); }
  .nav-divider { width: 1px; height: 20px; background: rgba(255,255,255,.15); margin: 0 4px; }
  .app-content { max-width: 960px; margin: 0 auto; padding: 20px 16px 40px; }
  /* --- Shared components --- */
  h1 { margin-bottom: 16px; color: #1a1a1a; font-size: 22px; font-weight: 700; }
  h2 { margin: 20px 0 10px; color: #1a1a1a; font-size: 17px; font-weight: 700; }
  a { color: #1e40af; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .card { background: white; border-radius: 10px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .badge-green { background: #dcfce7; color: #166534; }
  .badge-yellow { background: #fef3c7; color: #92400e; }
  .badge-gray { background: #f3f4f6; color: #374151; }
  .badge-red { background: #fee2e2; color: #991b1b; }
  table { width: 100%; border-collapse: collapse; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #f3f4f6; }
  th { background: #fafafa; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #9ca3af; cursor: pointer; }
  th:hover { background: #f3f4f6; }
  tr:hover { background: #fafafa; }
  .stat { display: inline-block; margin-right: 20px; }
  .stat-value { font-size: 22px; font-weight: 700; color: #1a1a1a; }
  .stat-label { font-size: 12px; color: #9ca3af; text-transform: uppercase; letter-spacing: .5px; }
  .score-bar { height: 6px; border-radius: 3px; background: #f3f4f6; }
  .score-fill { height: 100%; border-radius: 3px; }
  .filter-row { margin-bottom: 16px; }
  .filter-row input, .filter-row select { padding: 8px 12px; border: 1.5px solid #e5e7eb; border-radius: 8px; font-size: 14px; transition: border-color .15s; }
  .filter-row input:focus, .filter-row select:focus { outline: none; border-color: #1e40af; box-shadow: 0 0 0 3px rgba(30,64,175,.1); }
  .interests-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
  .interest-tag { padding: 4px 12px; border-radius: 16px; background: #ede9fe; color: #5b21b6; font-size: 13px; }
  .cost-box { background: #fffbeb; border: 1px solid #fde68a; border-radius: 10px; padding: 12px; margin: 10px 0; }
  /* --- Overflow nav menu --- */
  .nav-overflow { position: relative; }
  .nav-overflow-btn { background: none; border: none; color: rgba(255,255,255,.6); font-size: 18px; font-weight: 700; cursor: pointer; padding: 6px 10px; border-radius: 8px; line-height: 1; letter-spacing: 2px; transition: all .15s; }
  .nav-overflow-btn:hover { background: rgba(255,255,255,.1); color: white; }
  .nav-overflow-menu { display: none; position: absolute; right: 0; top: calc(100% + 6px); background: #1a1a2e; border: 1px solid rgba(255,255,255,.15); border-radius: 10px; min-width: 140px; z-index: 200; padding: 6px 0; box-shadow: 0 8px 24px rgba(0,0,0,.35); }
  .nav-overflow-menu.open { display: block; }
  .nav-overflow-item { display: block; padding: 8px 16px; font-size: 13px; font-weight: 500; color: rgba(255,255,255,.7); text-decoration: none; transition: all .12s; }
  .nav-overflow-item:hover { background: rgba(255,255,255,.08); color: white; text-decoration: none; }
  @media (max-width: 640px) {
    .app-nav a.nav-link { font-size: 13px; padding: 6px 8px; }
    .app-content { padding: 16px 12px 32px; }
  }
</style>
</head>
<body>
"""

# Keep LAYOUT_HEAD as alias for backwards compat with admin pages
LAYOUT_HEAD = LAYOUT_STYLE + """<nav class="app-nav">
  <div class="app-nav-inner">
    <a href="/" class="app-logo">recom</a>
    <a href="/" class="nav-link">Events</a>
    <a href="/groups" class="nav-link">Groups</a>
    <a href="/attended" class="nav-link">History</a>
    <a href="/login" class="nav-link">Login</a>
    <div class="nav-divider"></div>
    <a href="/admin" class="nav-link" style="font-size:12px;color:#9ca3af">Admin</a>
  </div>
</nav>
<div class="app-content">
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
// Close overflow menu when clicking outside
document.addEventListener('click', function(e) {
  const menu = document.querySelector('.nav-overflow-menu');
  if (menu && !menu.closest('.nav-overflow').contains(e.target)) {
    menu.classList.remove('open');
  }
});
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


@app.get("/admin/email-preview", response_class=HTMLResponse)
async def email_preview_index(request: Request):
    """Admin: show all email preview links."""
    current_user = _get_current_user(request)
    if not current_user or current_user.get("id") != 1:
        return RedirectResponse("/login")
    body = """
    <h1>Email Preview</h1>
    <div class="card" style="max-width:500px;">
        <p style="color:#6b7280;margin-bottom:16px;">Preview and test outgoing emails without sending them via SMTP.</p>
        <ul style="list-style:none;padding:0;display:flex;flex-direction:column;gap:10px;">
            <li><a href="/admin/email-preview/daily" style="color:#2563eb;font-weight:600;">Weekly Digest Email</a>
                <span style="color:#9ca3af;font-size:13px;margin-left:8px;">— rendered from latest run</span></li>
            <li><a href="/admin/email-preview/tonight" style="color:#2563eb;font-weight:600;">Tonight Email</a>
                <span style="color:#9ca3af;font-size:13px;margin-left:8px;">— last minute daily digest</span></li>
        </ul>
    </div>
    """
    return HTMLResponse(_layout("Email Preview", body, current_user))


@app.get("/admin/email-preview/daily", response_class=HTMLResponse)
async def email_preview_daily(request: Request):
    """Admin: render the weekly digest email inline."""
    import html as _html
    from datetime import datetime
    from recom.models import InterestProfile

    current_user = _get_current_user(request)
    if not current_user or current_user.get("id") != 1:
        return RedirectResponse("/login")

    db = get_db()
    settings = Settings()

    run = db.get_user_latest_run(current_user["id"])
    if not run:
        runs = db.get_runs()
        run = runs[0] if runs else None
    if not run:
        return HTMLResponse(_layout("Email Preview: Daily", "<h1>No runs yet</h1>", current_user))

    run_id = run["id"]
    from recom.models import RankedEvent
    ranked = _build_ranked_events_from_run(run_id)
    ranked_kept = [r for r in ranked if r.keep and r.score >= 25]

    profile = db.get_cached_interest_profile(max_age_days=30) or InterestProfile()
    week_of = datetime.now().strftime("%B %-d, %Y")

    from recom.email.composer import compose_email
    subject, html_body = compose_email(
        ranked_events=ranked_kept,
        profile=profile,
        week_of=week_of,
        total_cost=0.0,
        tokens_in=0,
        tokens_out=0,
        dashboard_url=settings.dashboard_url,
        run_id=run_id,
    )

    escaped = _html.escape(html_body)
    body = f"""
    <h1>Email Preview: Weekly Digest</h1>
    <div class="card" style="margin-bottom:16px;">
        <div style="font-size:12px;color:#6b7280;margin-bottom:4px;">Subject line:</div>
        <h2 style="font-size:18px;font-weight:700;color:#1e293b;">{_html.escape(subject)}</h2>
    </div>
    <div class="card" style="margin-bottom:16px;">
        <div style="font-size:12px;color:#6b7280;margin-bottom:8px;">HTML body preview:</div>
        <iframe srcdoc="{escaped}" style="width:100%;height:700px;border:1px solid #e2e8f0;border-radius:6px;"></iframe>
    </div>
    <div class="card" style="max-width:400px;">
        <h3 style="margin:0 0 8px;font-size:15px;">Send test to me</h3>
        <form onsubmit="sendTest(event)">
            <input type="hidden" name="type" value="daily">
            <button type="submit" style="padding:8px 20px;background:#2563eb;color:white;border:none;border-radius:6px;font-size:14px;cursor:pointer;font-weight:600;">Send Test Email</button>
            <span id="sendMsg" style="margin-left:10px;font-size:13px;"></span>
        </form>
    </div>
    <script>
    async function sendTest(e) {{
        e.preventDefault();
        const msg = document.getElementById('sendMsg');
        msg.textContent = 'Sending...';
        msg.style.color = '#6b7280';
        try {{
            const r = await fetch('/admin/email-preview/send-test', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{type: 'daily'}})
            }});
            const d = await r.json();
            if (d.ok) {{
                msg.textContent = 'Sent!';
                msg.style.color = '#16a34a';
            }} else {{
                msg.textContent = d.error || 'Error sending';
                msg.style.color = '#dc2626';
            }}
        }} catch(err) {{
            msg.textContent = 'Network error';
            msg.style.color = '#dc2626';
        }}
    }}
    </script>
    """
    resp = HTMLResponse(_layout("Email Preview: Daily", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/admin/email-preview/tonight", response_class=HTMLResponse)
async def email_preview_tonight(request: Request):
    """Admin: preview last-minute tonight email."""
    import html as _html
    from datetime import datetime

    current_user = _get_current_user(request)
    if not current_user or current_user.get("id") != 1:
        return RedirectResponse("/login")

    db = get_db()
    settings = Settings()

    run = db.get_user_latest_run(current_user["id"])
    if not run:
        runs = db.get_runs()
        run = runs[0] if runs else None

    try:
        from recom.email.composer import compose_daily_email
        has_tonight = True
    except ImportError:
        has_tonight = False

    if not run or not has_tonight:
        body = """
        <h1>Email Preview: Tonight</h1>
        <div class="card"><p style="color:#6b7280;">No runs available or tonight email composer not found.</p></div>
        """
        return HTMLResponse(_layout("Email Preview: Tonight", body, current_user))

    run_id = run["id"]
    ranked = _build_ranked_events_from_run(run_id)
    today = datetime.now()
    result = compose_daily_email(
        ranked_events=ranked,
        target_date=today,
        dashboard_url=settings.dashboard_url,
        user_token=current_user.get("user_token", ""),
    )

    if result is None:
        body = """
        <h1>Email Preview: Tonight</h1>
        <div class="card"><p style="color:#6b7280;">No events for today in the latest run.</p></div>
        """
        return HTMLResponse(_layout("Email Preview: Tonight", body, current_user))

    subject, html_body = result
    escaped = _html.escape(html_body)
    body = f"""
    <h1>Email Preview: Tonight</h1>
    <div class="card" style="margin-bottom:16px;">
        <div style="font-size:12px;color:#6b7280;margin-bottom:4px;">Subject line:</div>
        <h2 style="font-size:18px;font-weight:700;color:#1e293b;">{_html.escape(subject)}</h2>
    </div>
    <div class="card">
        <div style="font-size:12px;color:#6b7280;margin-bottom:8px;">HTML body preview:</div>
        <iframe srcdoc="{escaped}" style="width:100%;height:600px;border:1px solid #e2e8f0;border-radius:6px;"></iframe>
    </div>
    """
    resp = HTMLResponse(_layout("Email Preview: Tonight", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.post("/admin/email-preview/send-test")
async def email_preview_send_test(request: Request):
    """Admin: send a test email to the admin user."""
    current_user = _get_current_user(request)
    if not current_user or current_user.get("id") != 1:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=403)

    try:
        body = await request.json()
        email_type = body.get("type", "daily")
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)

    settings = Settings()
    if not settings.smtp_user or not settings.smtp_password:
        return JSONResponse({"ok": False, "error": "SMTP not configured (SMTP_USER / SMTP_PASSWORD missing)"})

    try:
        from datetime import datetime
        from recom.models import InterestProfile
        from recom.email.composer import compose_email
        from recom.email.sender import send_email

        db = get_db()
        run = db.get_user_latest_run(current_user["id"])
        if not run:
            runs = db.get_runs()
            run = runs[0] if runs else None
        if not run:
            return JSONResponse({"ok": False, "error": "No runs available"})

        ranked = _build_ranked_events_from_run(run["id"])
        ranked_kept = [r for r in ranked if r.keep and r.score >= 25]
        profile = db.get_cached_interest_profile(max_age_days=30) or InterestProfile()
        week_of = datetime.now().strftime("%B %-d, %Y")

        subject, html_body = compose_email(
            ranked_events=ranked_kept,
            profile=profile,
            week_of=week_of,
            total_cost=0.0,
            tokens_in=0,
            tokens_out=0,
            dashboard_url=settings.dashboard_url,
            run_id=run["id"],
        )
        subject = f"[TEST] {subject}"
        send_email(
            subject=subject,
            html_body=html_body,
            settings=settings,
            to=current_user["email"],
        )
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


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
    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__","Run History") + f"""
    <h1>Run History</h1>
    <div style="margin-bottom:12px;display:flex;gap:12px;flex-wrap:wrap;">
        <a href="/admin/sources" style="font-size:13px;color:#4f46e5;font-weight:600;">📡 Source Health</a>
        <a href="/admin/interests" style="font-size:13px;color:#4f46e5;font-weight:600;">🧠 Interest Profile</a>
        <a href="/admin/email-preview" style="font-size:13px;color:#4f46e5;font-weight:600;">✉ Email Preview</a>
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

    <h2 style="margin-top:32px;">Schedule Settings</h2>
    <div class="card" style="max-width:480px;">
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
                        style="padding:8px 20px;background:#2563eb;color:white;border:none;border-radius:6px;font-size:14px;cursor:pointer;font-weight:600;">Save &amp; Reinstall Cron</button>
                <span id="sched-status" style="font-size:13px;color:#6b7280;"></span>
            </div>
        </form>
    </div>
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
    """ + LAYOUT_FOOT)


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


@app.post("/api/interests/add", response_class=JSONResponse)
async def api_add_interest(request: Request):
    data = await request.json()
    db = get_db()
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return JSONResponse({"ok": False, "error": "empty"})
    ok = db.add_user_manual_interest(keyword)
    return JSONResponse({"ok": ok})


@app.post("/api/interests/delete", response_class=JSONResponse)
async def api_delete_interest(request: Request):
    data = await request.json()
    db = get_db()
    db.delete_user_manual_interest(int(data.get("id", 0)))
    return JSONResponse({"ok": True})


@app.post("/api/bucket/add", response_class=JSONResponse)
async def api_add_bucket(request: Request):
    data = await request.json()
    db = get_db()
    activity = (data.get("activity") or "").strip()
    if not activity:
        return JSONResponse({"ok": False, "error": "empty"})
    ok = db.add_bucket_item(activity)
    return JSONResponse({"ok": ok})


@app.post("/api/bucket/delete", response_class=JSONResponse)
async def api_delete_bucket(request: Request):
    data = await request.json()
    db = get_db()
    db.delete_bucket_item(int(data.get("id", 0)))
    return JSONResponse({"ok": True})


@app.get("/admin/sources", response_class=HTMLResponse)
async def source_health():
    """Scraper health dashboard."""
    db = get_db()
    sources = db.get_source_health(last_n_runs=10)
    # Build cache freshness map
    cache_status = {r["source_name"]: r for r in db.get_source_cache_status()}
    rows_html = ""
    for s in sources:
        success_rate = round(s["successes"] / s["run_count"] * 100) if s["run_count"] else 0
        rate_color = "#16a34a" if success_rate >= 90 else "#d97706" if success_rate >= 60 else "#dc2626"
        status_icon = "✓" if success_rate >= 90 else "⚠" if success_rate >= 60 else "✗"
        # Sparkline from event_history (comma-separated, newest first)
        history = [int(x) for x in (s["event_history"] or "0").split(",") if x.strip().isdigit()]
        max_h = max(history) if history else 1
        if max_h == 0:
            max_h = 1
        bars = "".join(
            f'<span style="display:inline-block;width:6px;height:{max(2, round(v/max_h*24))}px;background:#3b82f6;border-radius:1px;margin-right:1px;vertical-align:bottom;" title="{v}"></span>'
            for v in reversed(history[:10])
        )
        err_html = f'<span title="{s["last_error"][:200] if s["last_error"] else ""}" style="color:#dc2626;font-size:11px;cursor:help;">⚠ {(s["last_error"] or "")[:40]}...</span>' if s["last_error"] else ''
        avg_dur = s.get("avg_duration_s")
        dur_str = f"{avg_dur:.1f}s" if avg_dur else "—"
        # Cache freshness
        cache = cache_status.get(s["source_name"])
        if cache:
            age_h = round(cache.get("age_hours") or 0, 1)
            interval_h = cache.get("refresh_interval_hours") or 24
            fresh = age_h < interval_h
            cache_str = f'<span style="color:{"#16a34a" if fresh else "#d97706"}">{age_h}h ago</span>'
        else:
            cache_str = '<span style="color:#9ca3af">never</span>'
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
    return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__","Source Health") + f"""
    <h1>Source Health</h1>
    <p style="color:#6b7280;margin-bottom:16px;font-size:14px;">Last 10 runs. Click a source to see recent error details.</p>
    <table>
        <thead><tr>
            <th>Source</th>
            <th>Success Rate</th>
            <th>Avg Events</th>
            <th>Max Events</th>
            <th>Avg Time</th>
            <th>Cache Age</th>
            <th>Trend (newest →)</th>
            <th>Last Error</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
    <p style="margin-top:16px;font-size:13px;color:#9ca3af;">
        <a href="/admin">← Run History</a>
    </p>
    """ + LAYOUT_FOOT)


@app.get("/admin/interests", response_class=HTMLResponse)
async def interests_page(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    profile = db.get_cached_interest_profile(max_age_days=30)
    if not profile:
        return HTMLResponse(LAYOUT_HEAD.replace("__TITLE__","Interests") + """
        <h1>Interests</h1>
        <div class="card"><p>No interest profile yet. Run the pipeline first.</p></div>
        """ + LAYOUT_FOOT)

    # Taste stack preview (top 5 by Elo)
    db.seed_taste_items(user_id)
    taste_items = db.get_taste_items(user_id)[:5]
    taste_count = db.get_taste_matchup_count(user_id)
    CAT_COLORS_PY = {
        "music": "#f59e0b", "social": "#3b82f6", "arts": "#ec4899",
        "intellectual": "#8b5cf6", "active": "#22c55e", "food": "#f97316",
        "maker": "#06b6d4", "general": "#6b7280"
    }
    taste_rows = ""
    for rank, item in enumerate(taste_items, 1):
        col = CAT_COLORS_PY.get(item["category"], "#6b7280")
        taste_rows += f"""<div style="display:flex;align-items:center;gap:10px;padding:8px 0;{'' if rank == len(taste_items) else 'border-bottom:1px solid #f3f4f6;'}">
          <span style="font-size:12px;font-weight:800;color:#9ca3af;width:18px;">#{rank}</span>
          <span style="font-size:13px;font-weight:600;color:#1a1a1a;flex:1;">{item['label']}</span>
          <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:{col}">{item['category']}</span>
          <span style="font-size:12px;font-weight:700;color:#818cf8">{round(item['elo_rating'])}</span>
        </div>"""
    taste_preview_html = f"""
    <div class="card" style="margin-bottom:16px;border-left:4px solid #6366f1;background:linear-gradient(135deg,#fafafa,#f5f3ff);">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <div>
          <h3 style="font-size:15px;font-weight:800;color:#1a1a1a;margin-bottom:2px;">🏆 Taste Stack</h3>
          <p style="font-size:12px;color:#9ca3af;">{taste_count} matchups · Elo-ranked activity preferences</p>
        </div>
        <a href="/taste" style="font-size:13px;font-weight:600;color:#4f46e5;text-decoration:none;">View all →</a>
      </div>
      {taste_rows if taste_rows else '<p style="font-size:13px;color:#9ca3af;">No matchups yet. <a href="/taste" style="color:#4f46e5">Start ranking →</a></p>'}
    </div>"""

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

    # DB-backed manual interests
    db_interests = db.get_user_manual_interests()
    interest_tags = ""
    for item in db_interests:
        interest_tags += (
            f'<span style="display:inline-flex;align-items:center;gap:4px;background:#ede9fe;color:#6d28d9;'
            f'padding:4px 12px 4px 14px;border-radius:16px;font-size:13px;font-weight:600;">'
            f'{item["keyword"]}'
            f'<button onclick="deleteInterest({item["id"]},this)" style="background:none;border:none;cursor:pointer;'
            f'color:#9ca3af;font-size:14px;padding:0 0 0 4px;line-height:1;">&times;</button></span>'
        )
    manual_html = f"""
    <div class="card" style="border-left:4px solid #8b5cf6;margin-bottom:16px;">
      <h3 style="margin-bottom:8px;color:#6d28d9;">Manual Keywords ({len(db_interests)})</h3>
      <p style="font-size:12px;color:#9ca3af;margin-bottom:12px;">Injected at 0.90 confidence — boost topics not picked up from Spotify/YouTube.</p>
      <div id="interest-tags" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">{interest_tags}</div>
      <div style="display:flex;gap:8px;">
        <input id="new-interest" type="text" placeholder="e.g. electronic music, improv comedy..." style="flex:1;padding:8px 12px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:13px;">
        <button onclick="addInterest()" style="padding:8px 16px;background:#8b5cf6;color:white;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">Add</button>
      </div>
    </div>"""

    # DB-backed bucket list
    db_bucket = db.get_user_bucket_list()
    bucket_items_html = ""
    for item in db_bucket:
        bucket_items_html += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f3f4f6;">'
            f'<span style="font-size:14px;">{item["activity"]}</span>'
            f'<button onclick="deleteBucket({item["id"]},this)" style="background:none;border:none;cursor:pointer;color:#d1d5db;font-size:16px;">&times;</button>'
            f'</div>'
        )
    bucket_html = f"""
    <div class="card" style="border-left:4px solid #10b981;margin-bottom:16px;">
      <h3 style="margin-bottom:4px;color:#059669;">Bucket List ({len(db_bucket)} items)</h3>
      <p style="font-size:12px;color:#9ca3af;margin-bottom:12px;">Claude picks 3-5 seasonally relevant ones each week for the email digest.</p>
      <div id="bucket-items">{bucket_items_html or '<p style="color:#9ca3af;font-size:13px;">No items yet.</p>'}</div>
      <div style="display:flex;gap:8px;margin-top:12px;">
        <input id="new-bucket" type="text" placeholder="e.g. see a comedy show, take a salsa class..." style="flex:1;padding:8px 12px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:13px;">
        <button onclick="addBucket()" style="padding:8px 16px;background:#10b981;color:white;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">Add</button>
      </div>
    </div>
    <script>
    async function addInterest() {{
      const inp = document.getElementById('new-interest');
      const kw = inp.value.trim();
      if (!kw) return;
      const r = await fetch('/api/interests/add', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{keyword:kw}})}});
      const d = await r.json();
      if (d.ok) {{ inp.value=''; location.reload(); }}
    }}
    async function deleteInterest(id, btn) {{
      await fetch('/api/interests/delete', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:id}})}});
      btn.closest('span').remove();
    }}
    async function addBucket() {{
      const inp = document.getElementById('new-bucket');
      const act = inp.value.trim();
      if (!act) return;
      const r = await fetch('/api/bucket/add', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{activity:act}})}});
      const d = await r.json();
      if (d.ok) {{ inp.value=''; location.reload(); }}
    }}
    async function deleteBucket(id, btn) {{
      await fetch('/api/bucket/delete', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:id}})}});
      btn.closest('div').remove();
    }}
    document.getElementById('new-interest')?.addEventListener('keydown',e=>{{if(e.key==='Enter')addInterest();}});
    document.getElementById('new-bucket')?.addEventListener('keydown',e=>{{if(e.key==='Enter')addBucket();}});
    </script>"""

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

    top_interests = sorted(profile.interests, key=lambda x: x.confidence, reverse=True)[:6]
    top_tags = "".join(
        f'<span style="display:inline-block;padding:6px 16px;border-radius:20px;font-size:13px;font-weight:700;margin:4px;background:rgba(129,140,248,.15);border:1px solid rgba(129,140,248,.3);color:#818cf8;">'
        f'{i.topic} <span style="opacity:.6">{i.confidence:.0%}</span></span>'
        for i in top_interests
    )

    return HTMLResponse(LAYOUT_STYLE.replace("__TITLE__","Taste Profile") + render_nav(None) + f"""
    <div class="app-content">
    <!-- Hero banner -->
    <div style="background:linear-gradient(135deg,#1e1b4b,#312e81);border-radius:16px;padding:32px;margin-bottom:24px;color:white;">
      <p style="font-size:11px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:#818cf8;margin:0 0 10px;">◉ YOUR TASTE PROFILE</p>
      <h1 style="font-size:28px;font-weight:800;letter-spacing:-.5px;margin:0 0 14px;color:white;">What Claude thinks<br>you're into</h1>
      <p style="font-size:15px;line-height:1.6;color:rgba(255,255,255,.75);margin:0 0 20px;max-width:560px;">{profile.summary}</p>
      <div style="display:flex;flex-wrap:wrap;gap:6px;">{top_tags}</div>
      <p style="font-size:12px;color:rgba(255,255,255,.4);margin:16px 0 0;">Generated {generated} &middot; {len(profile.interests)} interests extracted</p>
    </div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:24px;">
        {sources_html or '<div class="card"><p style="color:#9ca3af;">No ingest stats yet.</p></div>'}
    </div>

    {manual_html}

    {taste_preview_html}

    <h2 style="margin:24px 0 14px;">All Interests</h2>
    {yt_section}
    {sp_section}
    {manual_section}

    {bucket_html}
    </div>
    """ + LAYOUT_FOOT)


@app.get("/interests", response_class=HTMLResponse)
async def interests_redirect():
    from fastapi.responses import RedirectResponse as _RR
    return _RR("/admin/interests")


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, response: Response):
    """User profile / settings page."""
    db = get_db()
    current_user = _get_current_user(request)
    if not current_user:
        return RedirectResponse("/login")
    settings = Settings()
    home_lat = float(current_user["home_lat"]) if current_user.get("home_lat") else settings.latitude
    home_lon = float(current_user["home_lon"]) if current_user.get("home_lon") else settings.longitude
    name = current_user.get("name") or ""
    email = current_user.get("email") or ""
    resp = HTMLResponse(_layout("Profile", f"""
<style>
.profile-page{{max-width:560px;margin:0 auto;padding:32px 16px 80px}}
.profile-page h1{{font-size:1.8rem;font-weight:800;color:#1e293b;margin-bottom:4px}}
.profile-page .sub{{font-size:14px;color:#64748b;margin-bottom:32px}}
.profile-page .card{{background:white;border-radius:16px;padding:24px;margin-bottom:20px;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
.profile-page .card h2{{font-size:14px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:16px}}
.profile-page label{{display:block;font-size:13px;font-weight:600;color:#374151;margin-bottom:4px}}
.profile-page input{{width:100%;padding:9px 12px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:14px;font-family:inherit;outline:none;transition:border-color .15s}}
.profile-page input:focus{{border-color:#4f46e5;box-shadow:0 0 0 3px rgba(79,70,229,.1)}}
.profile-page .row{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}}
.profile-page .field{{margin-bottom:12px}}
.save-btn{{background:#4f46e5;color:white;border:none;border-radius:8px;padding:10px 24px;font-size:14px;font-weight:700;cursor:pointer;font-family:inherit;transition:background .15s}}
.save-btn:hover{{background:#4338ca}}
.map-hint{{font-size:12px;color:#9ca3af;margin-top:6px}}
.save-success{{display:none;background:#f0fdf4;border:1px solid #bbf7d0;color:#166534;border-radius:8px;padding:10px 14px;font-size:13px;margin-top:12px}}
.locate-btn{{background:white;border:1.5px solid #e5e7eb;color:#374151;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;margin-bottom:12px;display:flex;align-items:center;gap:6px}}
.locate-btn:hover{{border-color:#4f46e5;color:#4f46e5}}
</style>
<div class="profile-page">
  <h1>Profile</h1>
  <p class="sub">Your personal settings and preferences.</p>

  <div class="card">
    <h2>Account</h2>
    <div class="field"><label>Name</label><input id="name" value="{name}"></div>
    <div class="field"><label>Email</label><input id="email" value="{email}" disabled style="background:#f9fafb;color:#9ca3af"></div>
  </div>

  <div class="card">
    <h2>Home Location</h2>
    <p style="font-size:13px;color:#64748b;margin-bottom:16px;">Your home coordinates are used to calculate event distances and improve recommendations.</p>
    <button class="locate-btn" onclick="useGPS()">Use my current location</button>
    <div class="row">
      <div class="field"><label>Latitude</label><input id="home_lat" type="number" step="0.0001" value="{home_lat}"></div>
      <div class="field"><label>Longitude</label><input id="home_lon" type="number" step="0.0001" value="{home_lon}"></div>
    </div>
    <p class="map-hint">Tip: You can get coordinates from <a href="https://maps.google.com" target="_blank" style="color:#4f46e5">Google Maps</a> by right-clicking your location.</p>
  </div>

  <button class="save-btn" onclick="save()">Save changes</button>
  <div class="save-success" id="success">Saved!</div>
</div>

<script>
function useGPS() {{
  if (!navigator.geolocation) {{ alert('Geolocation not supported'); return; }}
  navigator.geolocation.getCurrentPosition(pos => {{
    document.getElementById('home_lat').value = pos.coords.latitude.toFixed(5);
    document.getElementById('home_lon').value = pos.coords.longitude.toFixed(5);
  }}, () => alert('Could not get location'));
}}

function save() {{
  const payload = {{
    name: document.getElementById('name').value.trim(),
    home_lat: parseFloat(document.getElementById('home_lat').value) || null,
    home_lon: parseFloat(document.getElementById('home_lon').value) || null,
  }};
  fetch('/api/profile/update', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload),
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      const s = document.getElementById('success');
      s.style.display = 'block';
      setTimeout(() => s.style.display = 'none', 3000);
    }}
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
    if "home_lat" in body and body["home_lat"] is not None:
        updates.append("home_lat = ?")
        params.append(body["home_lat"])
    if "home_lon" in body and body["home_lon"] is not None:
        updates.append("home_lon = ?")
        params.append(body["home_lon"])
    if updates:
        params.append(user_id)
        db.conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        db.conn.commit()
    return {"ok": True}


@app.get("/taste", response_class=HTMLResponse)
async def taste_page(request: Request, response: Response):
    """Elo-based taste discovery page."""
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1

    db.seed_taste_items(user_id)
    items = db.get_taste_items(user_id)
    pair = db.get_taste_matchup_pair(user_id)
    matchup_count = db.get_taste_matchup_count(user_id)
    streak_info = db.get_taste_streak(user_id)

    pair_json = json.dumps([dict(pair[0]), dict(pair[1])]) if pair else "null"
    items_json = json.dumps(items, default=str)

    resp = HTMLResponse(_layout("Taste Stack", f"""
<style>
.app-content {{ background: #0f0f1a; }}
.page-wrap {{ max-width: 700px; margin: 0 auto; padding: 32px 16px 80px; }}
.hero {{ text-align: center; padding: 48px 0 32px; }}
.hero h1 {{ font-size: 2.4rem; font-weight: 900; background: linear-gradient(135deg, #818cf8, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 8px; }}
.hero p {{ color: #94a3b8; font-size: 1rem; max-width: 400px; margin: 0 auto; }}
.matchup-card {{ background: #1e1e3a; border-radius: 20px; padding: 32px; margin: 24px 0; border: 1px solid #2d2d5e; }}
.matchup-label {{ text-align: center; font-size: 12px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: #6366f1; margin-bottom: 24px; }}
.matchup-vs {{ display: grid; grid-template-columns: 1fr auto 1fr; gap: 16px; align-items: center; }}
.matchup-option {{ background: #0f0f1a; border: 2px solid #2d2d5e; border-radius: 16px; padding: 24px 16px; text-align: center; cursor: pointer; transition: all .2s; }}
.matchup-option:hover {{ border-color: #818cf8; background: #1a1a2e; transform: translateY(-2px); box-shadow: 0 8px 32px rgba(129,140,248,.2); }}
.matchup-option.selected {{ border-color: #22c55e; background: #052e16; }}
.matchup-option .category {{ font-size: 10px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; color: #6b7280; margin-bottom: 8px; }}
.matchup-option .label {{ font-size: 1.05rem; font-weight: 700; color: #e2e8f0; line-height: 1.3; }}
.vs-badge {{ font-size: 18px; font-weight: 900; color: #4b5563; }}
.equal-btn {{ display: block; text-align: center; margin: 16px auto 0; background: transparent; border: 1px solid #374151; color: #6b7280; font-size: 13px; padding: 8px 24px; border-radius: 20px; cursor: pointer; font-family: inherit; transition: all .15s; }}
.equal-btn:hover {{ border-color: #6b7280; color: #9ca3af; }}
.stats-row {{ display: flex; gap: 16px; justify-content: center; margin-bottom: 32px; flex-wrap: wrap; }}
.stat-pill {{ background: #1e1e3a; border: 1px solid #2d2d5e; border-radius: 20px; padding: 8px 20px; font-size: 13px; color: #94a3b8; }}
.stat-pill strong {{ color: #e2e8f0; }}
.stack-section {{ margin-top: 40px; }}
.stack-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }}
.stack-header h2 {{ font-size: 1.3rem; font-weight: 800; color: #e2e8f0; }}
.add-form {{ display: flex; gap: 8px; }}
.add-form input, .add-form select {{ background: #1e1e3a; border: 1px solid #2d2d5e; color: #e2e8f0; border-radius: 8px; padding: 8px 12px; font-size: 13px; font-family: inherit; }}
.add-form input {{ flex: 1; }}
.add-form button {{ background: #4f46e5; color: white; border: none; border-radius: 8px; padding: 8px 16px; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; }}
.taste-item {{ display: flex; align-items: center; gap: 12px; background: #1e1e3a; border-radius: 12px; padding: 14px 16px; margin: 8px 0; border: 1px solid #2d2d5e; }}
.rank-num {{ font-size: 13px; font-weight: 800; color: #4b5563; width: 24px; flex-shrink: 0; text-align: right; }}
.item-info {{ flex: 1; min-width: 0; }}
.item-label {{ font-size: 14px; font-weight: 600; color: #e2e8f0; }}
.item-cat {{ font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 1px; margin-top: 2px; }}
.elo-bar-wrap {{ width: 120px; flex-shrink: 0; }}
.elo-bar {{ height: 6px; background: #2d2d5e; border-radius: 3px; overflow: hidden; }}
.elo-bar-fill {{ height: 100%; background: linear-gradient(90deg, #818cf8, #c084fc); border-radius: 3px; transition: width .4s; }}
.elo-num {{ font-size: 12px; font-weight: 700; color: #818cf8; text-align: right; margin-top: 3px; }}
.item-actions {{ display: flex; gap: 8px; }}
.del-btn {{ background: transparent; border: none; color: #4b5563; cursor: pointer; font-size: 16px; padding: 4px; transition: color .15s; }}
.del-btn:hover {{ color: #ef4444; }}
.category-group {{ margin-bottom: 28px; }}
.cat-label {{ font-size: 11px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: #4b5563; margin-bottom: 10px; padding-left: 4px; }}
.congrats {{ text-align: center; padding: 32px; color: #94a3b8; }}
</style>
<div class="page-wrap">
  <div class="hero">
    <h1>Your Taste Stack</h1>
    <p>Rank what you love. The more you compare, the better your recommendations get.</p>
  </div>

  <div class="stats-row">
    <div class="stat-pill"><strong>{matchup_count}</strong> matchups</div>
    <div class="stat-pill"><strong>{len(items)}</strong> activity types</div>
    {'<div class="stat-pill" style="border-color:#f59e0b;color:#f59e0b;"><strong style=\'color:#f59e0b;\'>' + str(streak_info["streak"]) + '</strong> day streak</div>' if streak_info["streak"] > 0 else '<div class="stat-pill" style="color:#6b7280;">Start a streak!</div>'}
    {'<div class="stat-pill" style="color:#22c55e;border-color:#166534;">✓ done today</div>' if streak_info["today_done"] else ''}
    {'<a href="/taste/share/' + (current_user["user_token"] if current_user else "") + '" target="_blank" style="text-decoration:none;"><div class="stat-pill" style="cursor:pointer;border-color:#4b5563;color:#94a3b8;">↗ Share</div></a>' if current_user else ''}
  </div>

  <div id="radar-section" style="display:flex;justify-content:center;margin:0 0 24px;"></div>

  <div class="matchup-card" id="matchup-card">
    <div class="matchup-label">Which sounds better to you?</div>
    <div class="matchup-vs" id="matchup-vs">Loading...</div>
    <button class="equal-btn" id="equal-btn" onclick="vote(null)">Equal / Can't decide</button>
  </div>

  <div class="stack-section">
    <div class="stack-header">
      <h2>Your Rankings</h2>
      <div class="add-form" id="add-form">
        <input type="text" id="new-label" placeholder="Add activity...">
        <select id="new-cat">
          <option value="general">general</option>
          <option value="music">music</option>
          <option value="social">social</option>
          <option value="arts">arts</option>
          <option value="intellectual">intellectual</option>
          <option value="active">active</option>
          <option value="food">food</option>
          <option value="maker">maker</option>
        </select>
        <button onclick="addItem()">Add</button>
      </div>
    </div>
    <div id="stack-list"></div>
  </div>
</div>

<script>
const ITEMS = {items_json};
let PAIR = {pair_json};
let currentItems = [...ITEMS];

const CAT_COLORS = {{
  music: '#f59e0b', social: '#3b82f6', arts: '#ec4899',
  intellectual: '#8b5cf6', active: '#22c55e', food: '#f97316',
  maker: '#06b6d4', general: '#6b7280'
}};

function renderMatchup() {{
  const vs = document.getElementById('matchup-vs');
  const eq = document.getElementById('equal-btn');
  if (!PAIR) {{
    vs.innerHTML = '<div class="congrats" style="grid-column:1/-1"><p style="font-size:1.1rem;color:#818cf8;font-weight:700;">You\'ve ranked everything!</p><p style="margin-top:8px;">Add more activities to keep refining your taste.</p></div>';
    eq.style.display = 'none';
    return;
  }}
  const [a, b] = PAIR;
  eq.style.display = '';
  vs.innerHTML = `
    <div class="matchup-option" id="opt-a" onclick="vote(${{a.id}})">
      <div class="category" style="color:${{CAT_COLORS[a.category] || '#6b7280'}}">${{a.category}}</div>
      <div class="label">${{a.label}}</div>
      <div style="font-size:11px;color:#4b5563;margin-top:8px">${{Math.round(a.elo_rating)}} elo</div>
    </div>
    <div class="vs-badge">vs</div>
    <div class="matchup-option" id="opt-b" onclick="vote(${{b.id}})">
      <div class="category" style="color:${{CAT_COLORS[b.category] || '#6b7280'}}">${{b.category}}</div>
      <div class="label">${{b.label}}</div>
      <div style="font-size:11px;color:#4b5563;margin-top:8px">${{Math.round(b.elo_rating)}} elo</div>
    </div>`;
}}

function vote(winnerId) {{
  if (!PAIR) return;
  const [a, b] = PAIR;
  fetch('/api/taste/vote', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{item_a_id: a.id, item_b_id: b.id, winner_id: winnerId}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      PAIR = d.next_pair;
      currentItems = d.items;
      renderMatchup();
      renderStack();
      // Update stat pills
      const pills = document.querySelectorAll('.stat-pill');
      if (pills[0]) pills[0].innerHTML = `<strong>${{d.matchup_count}}</strong> matchups`;
      if (d.streak && pills[2]) {{
        const s = d.streak;
        if (s.streak > 0) {{
          pills[2].innerHTML = `<strong style="color:#f59e0b;">${{s.streak}}</strong> day streak`;
          pills[2].style.borderColor = '#f59e0b';
          pills[2].style.color = '#f59e0b';
        }}
      }}
    }}
  }});
}}

function renderStack() {{
  const container = document.getElementById('stack-list');
  if (!currentItems.length) {{ container.innerHTML = '<p style="color:#4b5563;text-align:center">No items yet.</p>'; return; }}

  // Group by category
  const cats = {{}};
  currentItems.forEach(item => {{
    if (!cats[item.category]) cats[item.category] = [];
    cats[item.category].push(item);
  }});

  const maxElo = Math.max(...currentItems.map(i => i.elo_rating));
  const minElo = Math.min(...currentItems.map(i => i.elo_rating));
  const range = maxElo - minElo || 1;

  let rank = 1;
  let html = '';
  // Render sorted by elo
  const sorted = [...currentItems].sort((a,b) => b.elo_rating - a.elo_rating);
  const byCat = {{}};
  sorted.forEach(item => {{
    if (!byCat[item.category]) byCat[item.category] = [];
    byCat[item.category].push(item);
  }});

  sorted.forEach(item => {{
    const pct = Math.round((item.elo_rating - minElo) / range * 100);
    html += `<div class="taste-item">
      <span class="rank-num">#${{rank++}}</span>
      <div class="item-info">
        <div class="item-label">${{item.label}}</div>
        <div class="item-cat" style="color:${{CAT_COLORS[item.category] || '#6b7280'}}">${{item.category}}</div>
      </div>
      <div class="elo-bar-wrap">
        <div class="elo-bar"><div class="elo-bar-fill" style="width:${{pct}}%"></div></div>
        <div class="elo-num">${{Math.round(item.elo_rating)}}</div>
      </div>
      <div class="item-actions">
        <button class="del-btn" onclick="deleteItem(${{item.id}}, event)" title="Remove">✕</button>
      </div>
    </div>`;
  }});
  container.innerHTML = html;
}}

function addItem() {{
  const label = document.getElementById('new-label').value.trim();
  const cat = document.getElementById('new-cat').value;
  if (!label) return;
  fetch('/api/taste/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{label, category: cat}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      currentItems = d.items;
      PAIR = d.next_pair;
      renderMatchup();
      renderStack();
      document.getElementById('new-label').value = '';
    }}
  }});
}}

function deleteItem(id, e) {{
  e.stopPropagation();
  fetch('/api/taste/delete', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{item_id: id}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      currentItems = d.items;
      PAIR = d.next_pair;
      renderMatchup();
      renderStack();
    }}
  }});
}}

renderMatchup();
renderStack();

// Load radar chart
fetch('/api/taste/radar').then(r => r.json()).then(d => {{
  const sec = document.getElementById('radar-section');
  if (!sec || !d.axes || d.axes.length < 3) return;
  const n = d.axes.length;
  const size = 200;
  const cx = cy = size / 2;
  const r = size * 0.38;
  const lr = size * 0.48;
  const TWO_PI = Math.PI * 2;
  // Grid rings
  let svg = `<svg width="${{size}}" height="${{size}}" viewBox="0 0 ${{size}} ${{size}}" xmlns="http://www.w3.org/2000/svg" style="overflow:visible">`;
  [0.25,0.5,0.75,1.0].forEach(level => {{
    const pts = d.axes.map((_,i) => {{
      const a = TWO_PI * i / n - Math.PI/2;
      return `${{(cx+Math.cos(a)*r*level).toFixed(1)}},${{(cy+Math.sin(a)*r*level).toFixed(1)}}`;
    }});
    svg += `<polygon points="${{pts.join(' ')}}" fill="none" stroke="#2d2d5e" stroke-width="0.8"/>`;
  }});
  // Axes
  d.axes.forEach((_,i) => {{
    const a = TWO_PI * i / n - Math.PI/2;
    svg += `<line x1="${{cx.toFixed(1)}}" y1="${{cy.toFixed(1)}}" x2="${{(cx+Math.cos(a)*r).toFixed(1)}}" y2="${{(cy+Math.sin(a)*r).toFixed(1)}}" stroke="#2d2d5e" stroke-width="0.8"/>`;
  }});
  // Data
  const dpts = d.values.map((v,i) => {{
    const a = TWO_PI * i / n - Math.PI/2;
    return `${{(cx+Math.cos(a)*r*v).toFixed(1)}},${{(cy+Math.sin(a)*r*v).toFixed(1)}}`;
  }});
  svg += `<polygon points="${{dpts.join(' ')}}" fill="rgba(129,140,248,0.25)" stroke="#818cf8" stroke-width="2"/>`;
  // Dots + labels
  d.values.forEach((v,i) => {{
    const a = TWO_PI * i / n - Math.PI/2;
    const px = (cx+Math.cos(a)*r*v).toFixed(1);
    const py = (cy+Math.sin(a)*r*v).toFixed(1);
    const lx = (cx+Math.cos(a)*lr).toFixed(1);
    const ly = (cy+Math.sin(a)*lr).toFixed(1);
    const anchor = cx-Math.cos(a)*r > 5 ? 'end' : cx+Math.cos(a)*r > 5 ? 'start' : 'middle';
    svg += `<circle cx="${{px}}" cy="${{py}}" r="3" fill="#818cf8"/>`;
    svg += `<text x="${{lx}}" y="${{ly}}" text-anchor="${{anchor}}" dominant-baseline="middle" font-size="9" fill="#94a3b8" font-family="system-ui">${{d.axes[i]}}</text>`;
  }});
  svg += '</svg>';
  sec.innerHTML = `<div style="background:#1e1e3a;border:1px solid #2d2d5e;border-radius:16px;padding:20px 32px;text-align:center;">
    <div style="font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#6366f1;margin-bottom:12px;">Taste Profile</div>
    ${{svg}}
    <div style="font-size:11px;color:#4b5563;margin-top:8px;">Based on ${{d.axes.length}} categories · updates with each matchup</div>
  </div>`;
}});
</script>
""", current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.post("/api/taste/vote")
async def taste_vote(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    body = await request.json()
    item_a_id = int(body["item_a_id"])
    item_b_id = int(body["item_b_id"])
    winner_id = body.get("winner_id")
    if winner_id is not None:
        winner_id = int(winner_id)
    db.record_taste_matchup(user_id, item_a_id, item_b_id, winner_id)
    items = db.get_taste_items(user_id)
    pair = db.get_taste_matchup_pair(user_id)
    count = db.get_taste_matchup_count(user_id)
    streak = db.get_taste_streak(user_id)
    return {"ok": True, "items": items, "next_pair": list(pair) if pair else None, "matchup_count": count, "streak": streak}


@app.post("/api/taste/add")
async def taste_add(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    body = await request.json()
    db.add_taste_item(body["label"], body.get("category", "general"), user_id)
    items = db.get_taste_items(user_id)
    pair = db.get_taste_matchup_pair(user_id)
    return {"ok": True, "items": items, "next_pair": list(pair) if pair else None}


@app.post("/api/taste/delete")
async def taste_delete(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    body = await request.json()
    db.delete_taste_item(int(body["item_id"]), user_id)
    items = db.get_taste_items(user_id)
    pair = db.get_taste_matchup_pair(user_id)
    return {"ok": True, "items": items, "next_pair": list(pair) if pair else None}


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


@app.get("/api/taste/radar")
async def taste_radar(request: Request):
    """Return radar chart data for the current user's taste profile."""
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    items = db.get_taste_items(user_id)
    if not items:
        return JSONResponse({"axes": [], "values": []})
    # Group by category, average Elo per category
    category_order = ["music", "social", "arts", "intellectual", "active", "food", "maker", "general"]
    cat_elos: dict[str, list[float]] = {}
    for item in items:
        cat = item.get("category", "general")
        cat_elos.setdefault(cat, []).append(float(item.get("elo_rating", 1400)))
    axes = [c for c in category_order if c in cat_elos]
    if not axes:
        axes = list(cat_elos.keys())[:8]
    avg_elos = [sum(cat_elos[c]) / len(cat_elos[c]) for c in axes]
    # Normalize to 0–1 range
    min_e = min(avg_elos) if avg_elos else 1200
    max_e = max(avg_elos) if avg_elos else 1600
    rng = max_e - min_e or 1
    values = [(e - min_e) / rng for e in avg_elos]
    return JSONResponse({"axes": axes, "values": values, "raw_elos": avg_elos})


@app.get("/taste/share/{token}", response_class=HTMLResponse)
async def taste_share(token: str):
    """Public shareable taste stack for a user."""
    db = get_db()
    owner = db.get_user_by_token(token)
    if not owner:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    user_id = owner["id"]
    name = owner.get("name") or owner.get("email", "Someone")
    items = db.get_taste_items(user_id)
    count = db.get_taste_matchup_count(user_id)

    CAT_COLORS = {
        "music": "#f59e0b", "social": "#3b82f6", "arts": "#ec4899",
        "intellectual": "#8b5cf6", "active": "#22c55e", "food": "#f97316",
        "maker": "#06b6d4", "general": "#6b7280"
    }
    if not items:
        return HTMLResponse(f"<h1>{name}'s taste stack is empty.</h1>")

    min_elo = min(i["elo_rating"] for i in items)
    max_elo = max(i["elo_rating"] for i in items)
    rng = max_elo - min_elo or 1

    rows = ""
    for rank, item in enumerate(items, 1):
        pct = round((item["elo_rating"] - min_elo) / rng * 100)
        col = CAT_COLORS.get(item["category"], "#6b7280")
        rows += f"""<div style="display:flex;align-items:center;gap:12px;background:#1e1e3a;border-radius:12px;padding:14px 16px;margin:8px 0;border:1px solid #2d2d5e;">
          <span style="font-size:13px;font-weight:800;color:#4b5563;width:24px;text-align:right">#{rank}</span>
          <div style="flex:1;min-width:0;">
            <div style="font-size:14px;font-weight:600;color:#e2e8f0">{item['label']}</div>
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:{col};margin-top:2px">{item['category']}</div>
          </div>
          <div style="width:120px">
            <div style="height:6px;background:#2d2d5e;border-radius:3px;overflow:hidden">
              <div style="height:100%;width:{pct}%;background:linear-gradient(90deg,#818cf8,#c084fc);border-radius:3px"></div>
            </div>
            <div style="font-size:12px;font-weight:700;color:#818cf8;text-align:right;margin-top:3px">{round(item['elo_rating'])}</div>
          </div>
        </div>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}'s Taste Stack</title>
<meta property="og:title" content="{name}'s Taste Stack">
<meta property="og:description" content="Built with {count} matchups — what {name} loves doing.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>* {{box-sizing:border-box;margin:0;padding:0}} body {{font-family:'Inter',sans-serif;background:#0f0f1a;color:#e2e8f0;min-height:100vh}} .wrap {{max-width:600px;margin:0 auto;padding:32px 16px 80px}}</style>
</head>
<body>
<div class="wrap">
  <div style="text-align:center;padding:40px 0 32px">
    <div style="font-size:11px;font-weight:700;letter-spacing:2px;color:#6366f1;text-transform:uppercase;margin-bottom:12px">TASTE STACK</div>
    <h1 style="font-size:2rem;font-weight:900;background:linear-gradient(135deg,#818cf8,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px">{name}</h1>
    <p style="color:#94a3b8;font-size:14px">{count} matchups · {len(items)} activity types ranked</p>
  </div>
  {rows}
  <div style="text-align:center;margin-top:32px;padding:24px;background:#1e1e3a;border-radius:16px;border:1px solid #2d2d5e">
    <p style="color:#94a3b8;font-size:14px;margin-bottom:12px">Build your own Taste Stack</p>
    <a href="/taste" style="display:inline-block;background:#4f46e5;color:white;text-decoration:none;padding:10px 24px;border-radius:20px;font-weight:700;font-size:14px">Try recom →</a>
  </div>
</div>
</body>
</html>""")


@app.get("/landing", response_class=HTMLResponse)
async def landing_page():
    """Marketing / about page for recom."""
    settings = Settings()
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#1a1a2e">
<meta name="description" content="Recom — Discover Weekly for your real life. AI-curated Boston events based on what you actually listen to and watch.">
<title>Recom — Discover Weekly for Your Real Life</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', system-ui, sans-serif; background: #0a0a14; color: white; line-height: 1.5; overflow-x: hidden; }}
  a {{ text-decoration: none; }}

  /* Nav */
  nav {{ position: fixed; top: 0; left: 0; right: 0; z-index: 100; padding: 0 24px; height: 60px; display: flex; align-items: center; justify-content: space-between; background: rgba(10,10,20,.85); backdrop-filter: blur(12px); border-bottom: 1px solid rgba(255,255,255,.07); }}
  .nav-logo {{ font-size: 16px; font-weight: 800; color: white; display: flex; align-items: center; gap: 6px; }}
  .nav-logo::before {{ content: '◉'; color: #818cf8; font-size: 12px; }}
  .nav-links {{ display: flex; gap: 8px; align-items: center; }}
  .nav-links a {{ font-size: 13px; font-weight: 500; color: rgba(255,255,255,.6); padding: 6px 14px; border-radius: 8px; transition: all .15s; }}
  .nav-links a:hover {{ color: white; background: rgba(255,255,255,.1); }}
  .nav-links .cta {{ background: #4f46e5; color: white; font-weight: 700; }}
  .nav-links .cta:hover {{ background: #4338ca; }}

  /* Hero */
  .hero {{ padding: 140px 24px 80px; text-align: center; max-width: 800px; margin: 0 auto; position: relative; }}
  .hero-eyebrow {{ display: inline-block; font-size: 12px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; color: #818cf8; background: rgba(129,140,248,.12); border: 1px solid rgba(129,140,248,.25); padding: 6px 16px; border-radius: 20px; margin-bottom: 28px; }}
  .hero h1 {{ font-size: clamp(42px, 8vw, 72px); font-weight: 900; line-height: 1.05; letter-spacing: -2px; margin-bottom: 24px; }}
  .hero h1 .accent {{ background: linear-gradient(135deg, #818cf8, #a78bfa, #f472b6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }}
  .hero-sub {{ font-size: clamp(16px, 2.5vw, 20px); color: rgba(255,255,255,.6); max-width: 560px; margin: 0 auto 36px; line-height: 1.6; }}
  .hero-cta {{ display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }}
  .btn-primary {{ background: linear-gradient(135deg, #4f46e5, #7c3aed); color: white; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 50px; transition: transform .15s, box-shadow .15s; box-shadow: 0 4px 24px rgba(79,70,229,.4); }}
  .btn-primary:hover {{ transform: translateY(-2px); box-shadow: 0 8px 32px rgba(79,70,229,.5); color: white; }}
  .btn-secondary {{ background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.15); color: rgba(255,255,255,.8); font-weight: 600; font-size: 15px; padding: 14px 28px; border-radius: 50px; transition: all .15s; }}
  .btn-secondary:hover {{ background: rgba(255,255,255,.14); color: white; }}

  /* Gradient orbs */
  .orb {{ position: absolute; border-radius: 50%; filter: blur(80px); opacity: .35; pointer-events: none; }}
  .orb-1 {{ width: 500px; height: 500px; background: radial-gradient(circle, #4f46e5, transparent); top: -100px; left: -200px; }}
  .orb-2 {{ width: 400px; height: 400px; background: radial-gradient(circle, #7c3aed, transparent); top: 0; right: -150px; }}

  /* How it works */
  .section {{ padding: 80px 24px; max-width: 960px; margin: 0 auto; }}
  .section-label {{ font-size: 11px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; color: #818cf8; margin-bottom: 16px; }}
  .section h2 {{ font-size: clamp(28px, 4vw, 42px); font-weight: 800; line-height: 1.15; letter-spacing: -.5px; margin-bottom: 16px; }}
  .section-sub {{ font-size: 17px; color: rgba(255,255,255,.5); max-width: 500px; line-height: 1.6; margin-bottom: 48px; }}

  .steps {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 20px; }}
  .step {{ background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.08); border-radius: 16px; padding: 28px; transition: border-color .2s, background .2s; }}
  .step:hover {{ border-color: rgba(129,140,248,.4); background: rgba(129,140,248,.06); }}
  .step-num {{ font-size: 12px; font-weight: 700; letter-spacing: 2px; color: #818cf8; margin-bottom: 14px; }}
  .step h3 {{ font-size: 18px; font-weight: 700; margin-bottom: 8px; line-height: 1.3; }}
  .step p {{ font-size: 14px; color: rgba(255,255,255,.55); line-height: 1.6; }}
  .step-icon {{ font-size: 28px; margin-bottom: 14px; }}

  /* Feature strip */
  .features {{ background: rgba(255,255,255,.025); border-top: 1px solid rgba(255,255,255,.07); border-bottom: 1px solid rgba(255,255,255,.07); }}
  .features-inner {{ max-width: 960px; margin: 0 auto; padding: 64px 24px; }}
  .features-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 32px 40px; margin-top: 40px; }}
  .feature {{ }}
  .feature-icon {{ width: 40px; height: 40px; border-radius: 10px; background: rgba(129,140,248,.15); display: flex; align-items: center; justify-content: center; font-size: 18px; margin-bottom: 12px; }}
  .feature h4 {{ font-size: 15px; font-weight: 700; margin-bottom: 6px; }}
  .feature p {{ font-size: 13px; color: rgba(255,255,255,.5); line-height: 1.55; }}

  /* Vibe showcase */
  .vibe-cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 40px; }}
  .vibe-card {{ border-radius: 14px; padding: 22px 20px; border: 1px solid; }}
  .vibe-card.social {{ background: rgba(245,158,11,.08); border-color: rgba(245,158,11,.2); }}
  .vibe-card.intellectual {{ background: rgba(139,92,246,.08); border-color: rgba(139,92,246,.2); }}
  .vibe-card.mixed {{ background: rgba(59,130,246,.08); border-color: rgba(59,130,246,.2); }}
  .vibe-card .vibe-label {{ font-size: 11px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 10px; }}
  .vibe-card.social .vibe-label {{ color: #fbbf24; }}
  .vibe-card.intellectual .vibe-label {{ color: #a78bfa; }}
  .vibe-card.mixed .vibe-label {{ color: #60a5fa; }}
  .vibe-card h4 {{ font-size: 14px; font-weight: 700; line-height: 1.3; margin-bottom: 6px; }}
  .vibe-card p {{ font-size: 12px; color: rgba(255,255,255,.5); line-height: 1.5; }}
  .vibe-score {{ display: inline-block; font-size: 12px; font-weight: 800; padding: 2px 8px; border-radius: 8px; margin-top: 10px; background: rgba(255,255,255,.1); color: rgba(255,255,255,.7); }}

  /* CTA section */
  .cta-section {{ padding: 100px 24px; text-align: center; position: relative; }}
  .cta-section h2 {{ font-size: clamp(32px, 5vw, 52px); font-weight: 900; letter-spacing: -1px; margin-bottom: 16px; line-height: 1.1; }}
  .cta-section p {{ font-size: 18px; color: rgba(255,255,255,.5); margin-bottom: 36px; }}

  /* Footer */
  footer {{ border-top: 1px solid rgba(255,255,255,.07); padding: 32px 24px; text-align: center; }}
  footer p {{ font-size: 13px; color: rgba(255,255,255,.3); }}
  footer a {{ color: rgba(255,255,255,.5); }}
  footer a:hover {{ color: white; }}

  @media (max-width: 640px) {{
    .vibe-cards {{ grid-template-columns: 1fr; }}
    .hero {{ padding: 120px 20px 60px; }}
  }}
</style>
</head>
<body>

<nav>
  <div class="nav-logo">recom</div>
  <div class="nav-links">
    <a href="#how-it-works">How it works</a>
    <a href="/join" class="cta">Get started</a>
  </div>
</nav>

<!-- Hero -->
<div style="position:relative;overflow:hidden;">
  <div class="orb orb-1"></div>
  <div class="orb orb-2"></div>
  <div class="hero">
    <div class="hero-eyebrow">Boston &amp; Cambridge · AI-powered</div>
    <h1>Discover Weekly<br>for your <span class="accent">real life</span></h1>
    <p class="hero-sub">Every week, recom studies what you listen to, watch, and read — then surfaces the Boston events you'd actually want to go to.</p>
    <div class="hero-cta">
      <a href="/join" class="btn-primary">Get your first digest free</a>
      <a href="/" class="btn-secondary">View the calendar</a>
    </div>
  </div>
</div>

<!-- How it works -->
<div class="section" id="how-it-works">
  <div class="section-label">The system</div>
  <h2>Your taste profile.<br>Turned into a calendar.</h2>
  <p class="section-sub">Three steps, once a week, fully automated.</p>
  <div class="steps">
    <div class="step">
      <div class="step-icon">🎧</div>
      <div class="step-num">01 · INGEST</div>
      <h3>Read your taste signals</h3>
      <p>Connects to Spotify, YouTube, and your newsletters to understand what you're actually into — not just what you say you like.</p>
    </div>
    <div class="step">
      <div class="step-icon">🧠</div>
      <div class="step-num">02 · RANK</div>
      <h3>Score 1,000+ events</h3>
      <p>Claude AI scores every upcoming Boston event on 7 dimensions: interest match, social factor, logistics, discovery potential, and more.</p>
    </div>
    <div class="step">
      <div class="step-icon">📬</div>
      <div class="step-num">03 · DELIVER</div>
      <h3>Sunday digest + live calendar</h3>
      <p>A beautiful email every Sunday with your top picks, plus a live web calendar with RSVP, iCal export, and group sharing.</p>
    </div>
  </div>
</div>

<!-- Features -->
<div class="features">
  <div class="features-inner">
    <div class="section-label">What you get</div>
    <h2 style="font-size:clamp(24px,3.5vw,36px);font-weight:800;letter-spacing:-.5px;margin-bottom:8px;">Everything you need.<br>Nothing you don't.</h2>
    <div class="features-grid">
      <div class="feature">
        <div class="feature-icon">📅</div>
        <h4>Smart calendar</h4>
        <p>Week view, heatmap, and grid — filter by vibe, search by keyword, RSVP with one click.</p>
      </div>
      <div class="feature">
        <div class="feature-icon">📬</div>
        <h4>Weekly email digest</h4>
        <p>Your Spotify Wrapped, but for events. Beautiful, visual, scannable in 60 seconds.</p>
      </div>
      <div class="feature">
        <div class="feature-icon">👥</div>
        <h4>Group coordination</h4>
        <p>Create a group, invite friends, see who's going where — no group chat needed.</p>
      </div>
      <div class="feature">
        <div class="feature-icon">📡</div>
        <h4>iCal feed</h4>
        <p>Subscribe in Apple Calendar or Google Calendar. Your top picks, always in sync.</p>
      </div>
      <div class="feature">
        <div class="feature-icon">🔍</div>
        <h4>10+ event sources</h4>
        <p>Eventbrite, Meetup, Luma, Ticketmaster, MIT, Harvard, Bandsintown, RA, and more — deduplicated.</p>
      </div>
      <div class="feature">
        <div class="feature-icon">🎯</div>
        <h4>Taste profile</h4>
        <p>See exactly what Claude thinks you're into — interests, confidence scores, data sources.</p>
      </div>
    </div>
  </div>
</div>

<!-- Vibe system -->
<div class="section">
  <div class="section-label">The vibe system</div>
  <h2>Events ranked by<br>how they feel.</h2>
  <p class="section-sub">Not just "what's nearby" — but whether you're in a social mood, a learning mood, or somewhere in between.</p>
  <div class="vibe-cards">
    <div class="vibe-card social">
      <div class="vibe-label">Social</div>
      <h4>Comedy night at The Rockwell</h4>
      <p>High friend-bringability, great for groups, easy logistics from Cambridge.</p>
      <span class="vibe-score">84</span>
    </div>
    <div class="vibe-card intellectual">
      <div class="vibe-label">Brainy</div>
      <h4>MIT Media Lab open house</h4>
      <p>Strong interest match for AI + design. Discovery potential off the charts.</p>
      <span class="vibe-score">91</span>
    </div>
    <div class="vibe-card mixed">
      <div class="vibe-label">Mixed</div>
      <h4>Softcult @ Paradise Rock Club</h4>
      <p>Artist you follow on Spotify. Saturday night, walking distance, $18.</p>
      <span class="vibe-score">77</span>
    </div>
  </div>
</div>

<!-- CTA -->
<div class="cta-section">
  <div class="orb" style="width:600px;height:600px;background:radial-gradient(circle,#4f46e5,transparent);opacity:.2;top:50%;left:50%;transform:translate(-50%,-50%);position:absolute;"></div>
  <div style="position:relative;">
    <h2>Your week is full of things<br>worth showing up for.</h2>
    <p>You just have to know where to look.</p>
    <a href="/join" class="btn-primary" style="font-size:16px;padding:16px 40px;">Start discovering &rarr;</a>
    <p style="margin-top:20px;font-size:13px;color:rgba(255,255,255,.3);">Free. No credit card. Boston &amp; Cambridge area.</p>
  </div>
</div>

<footer>
  <p>
    <a href="/">Calendar</a> &nbsp;·&nbsp;
    <a href="/interests">Taste profile</a> &nbsp;·&nbsp;
    <a href="/feed.ics">iCal feed</a>
  </p>
  <p style="margin-top:8px;">Built with Claude · &copy; 2026 recom</p>
</footer>

</body>
</html>
""")


@app.get("/", response_class=HTMLResponse)
@app.get("/calendar", response_class=HTMLResponse)
@app.get("/calendar/{run_id}", response_class=HTMLResponse)
async def calendar_view(request: Request, run_id: int | None = None):
    import re as _re

    db = get_db()
    settings = Settings()

    # Resolve user from token cookie or ?u= param
    current_user = _get_current_user(request)

    # Per-user home coords (fall back to settings defaults)
    home_lat = float(current_user["home_lat"]) if current_user and current_user.get("home_lat") else settings.latitude
    home_lon = float(current_user["home_lon"]) if current_user and current_user.get("home_lon") else settings.longitude

    # Default to current user's latest run (or global latest if no user)
    if run_id is None:
        if current_user:
            latest = db.get_user_latest_run(current_user["id"])
            run_id = latest["id"] if latest else None
        if run_id is None:
            runs = db.get_runs()
            if not runs:
                return HTMLResponse(_layout("Calendar", "<h1>Calendar</h1><div class='card'><p>No runs yet. Run the pipeline first.</p></div>", current_user))
            run_id = runs[0]["id"]

    events = db.get_run_events(run_id)
    all_kept = [e for e in events if e.get("keep")]

    # Diverse pick: top 5 per day, max 2 per vibe
    from collections import defaultdict
    from datetime import datetime as dt

    day_groups: dict[str, list] = defaultdict(list)
    undated = []
    for e in all_kept:
        if e.get("start_time"):
            try:
                d = dt.fromisoformat(e["start_time"])
                day_groups[d.strftime("%Y-%m-%d")].append(e)
            except (ValueError, TypeError):
                undated.append(e)
        else:
            undated.append(e)

    # Mark primary (curated top 5/day with vibe diversity) vs overflow
    primary_ids: set[str] = set()
    kept = []
    for day_str in sorted(day_groups):
        day_evts = sorted(day_groups[day_str], key=lambda x: -(x.get("score") or 0))
        picked = []
        vibe_counts: dict[str, int] = defaultdict(int)
        for e in day_evts:
            if len(picked) >= 5:
                break
            vibe = e.get("vibe", "mixed")
            if vibe_counts[vibe] >= 2:
                continue
            picked.append(e)
            vibe_counts[vibe] += 1
        for e in picked:
            primary_ids.add(e.get("event_id", ""))
        kept.extend(day_evts)  # ALL events for the day, not just picked
    undated_all = sorted(undated, key=lambda x: -(x.get("score") or 0))
    for e in undated_all[:5]:
        primary_ids.add(e.get("event_id", ""))
    kept.extend(undated_all)

    # Fetch RSVPs for all kept events
    all_event_ids = [e.get("event_id", "") for e in kept if e.get("event_id")]
    rsvps_map = db.get_rsvps_for_events(all_event_ids)
    user_token = current_user["user_token"] if current_user else ""

    # Build JSON event array for JS consumption
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
            "primary": eid in primary_ids,
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

    # Compute hot days summary for the strip
    from datetime import date as _date, timedelta as _td
    _today = _date.today()
    hot_days_html = ""
    day_scores: dict[str, list[int]] = {}
    for e in all_kept:
        st = e.get("start_time")
        if not st:
            continue
        try:
            d = dt.fromisoformat(st).date()
            if d < _today or (d - _today).days > 14:
                continue
            day_scores.setdefault(str(d), []).append(int(e.get("score") or 0))
        except (ValueError, TypeError):
            pass
    if day_scores:
        hot_strip = []
        for day_str in sorted(day_scores.keys()):
            scores = day_scores[day_str]
            top = max(scores)
            cnt = len(scores)
            d = _date.fromisoformat(day_str)
            label = "Today" if d == _today else ("Tomorrow" if d == _today + _td(days=1) else d.strftime("%a %-d"))
            if top >= 75 or cnt >= 4:
                dot = "●"
                color = "#dc2626"
            elif top >= 60 or cnt >= 2:
                dot = "●"
                color = "#4f46e5"
            else:
                dot = ""
                color = "#6b7280"
            if dot:
                hot_strip.append(f'<span style="display:inline-flex;align-items:center;gap:3px;padding:3px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:20px;font-size:12px;font-weight:600;color:{color};cursor:pointer;">{label} <span style="color:#9ca3af;font-weight:400">({cnt})</span></span>')
        if hot_strip:
            hot_days_html = f'<div style="display:flex;gap:6px;overflow-x:auto;padding-bottom:4px;margin-bottom:12px;flex-wrap:wrap;">{"".join(hot_strip)}</div>'

    user_name = current_user["name"] if current_user else ""
    user_banner = ""
    groups_html = ""
    friend_activity_html = ""
    taste_nudge_html = ""
    if current_user:
        user_banner = f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:8px 16px;margin-bottom:12px;font-size:14px;">Viewing as <strong>{user_name}</strong> &middot; RSVP to events below</div>'

        # Daily taste matchup nudge — show if <3 matchups done ever (still building profile)
        taste_count = db.get_taste_matchup_count(current_user["id"])
        if taste_count < 3:
            taste_nudge_html = f'''<a href="/taste" style="text-decoration:none;display:block;margin-bottom:12px;">
              <div style="background:linear-gradient(135deg,#1e1b4b,#312e81);border-radius:10px;padding:12px 16px;display:flex;align-items:center;gap:12px;color:white;">
                <span style="font-size:22px;">🏆</span>
                <div style="flex:1;">
                  <div style="font-size:13px;font-weight:700;color:white;">Build your Taste Stack</div>
                  <div style="font-size:12px;color:rgba(255,255,255,.6);">3 quick picks → better recommendations. Takes 30 seconds.</div>
                </div>
                <span style="color:#818cf8;font-weight:700;">→</span>
              </div>
            </a>'''
        elif taste_count < 10:
            pair = db.get_taste_matchup_pair(current_user["id"])
            if pair:
                a_label = pair[0]["label"]
                b_label = pair[1]["label"]
                taste_nudge_html = f'''<div style="background:#fefce8;border:1px solid #fde68a;border-radius:10px;padding:12px 16px;margin-bottom:12px;display:flex;align-items:center;gap:12px;">
                  <span style="font-size:18px;">⚡</span>
                  <div style="flex:1;font-size:13px;color:#92400e;">
                    <strong>Quick pick:</strong> {a_label} vs {b_label}?
                  </div>
                  <a href="/taste" style="font-size:13px;font-weight:700;color:#d97706;text-decoration:none;">Rank it →</a>
                </div>'''

        # Group cards
        user_groups = db.get_user_groups(current_user["id"])
        if user_groups:
            group_cards = ""
            for g in user_groups:
                group_cards += f'<a href="/group/{g["slug"]}" style="flex-shrink:0;display:inline-block;background:white;border-radius:10px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.06);text-decoration:none;min-width:140px;"><strong style="color:#1a1a1a;font-size:14px;">{g["name"]}</strong><div style="color:#9ca3af;font-size:12px;">{g["member_count"]} members</div></a>'
            group_cards += f'<a href="/groups" style="flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;border:2px dashed #d1d5db;border-radius:10px;padding:12px 16px;text-decoration:none;color:#9ca3af;font-size:13px;min-width:120px;">Browse groups</a>'
            groups_html = f'<div style="display:flex;gap:10px;overflow-x:auto;padding-bottom:8px;margin-bottom:12px;">{group_cards}</div>'

        # Friend activity
        friend_rsvps = db.get_recent_friend_rsvps(current_user["id"])
        if friend_rsvps:
            items = []
            for fr in friend_rsvps[:5]:
                status_word = "is going to" if fr["status"] == "going" else "is maybe for"
                start = ""
                if fr.get("start_time"):
                    from datetime import datetime as _dt
                    try:
                        d = _dt.fromisoformat(fr["start_time"])
                        start = f" ({d.strftime('%a')})"
                    except (ValueError, TypeError):
                        pass
                items.append(f'{fr["user_name"]} {status_word} <a href="{fr.get("event_url", "#")}" style="color:#92400e;">{fr["event_title"][:40]}</a>{start}')
            friend_activity_html = f'<div style="background:#fef3c7;border:1px solid #fde68a;border-radius:8px;padding:10px 16px;margin-bottom:12px;font-size:13px;color:#92400e;">{" &middot; ".join(items)}</div>'

    top_picks = len(kept)

    page_html = LAYOUT_STYLE.replace("__TITLE__", "This Week in Cambridge") + render_nav(current_user) + '<div class="app-content">' + f"""
    <script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.17/index.global.min.js"></script>
    <style>
      /* --- Top bar --- */
      .page-header {{ margin-bottom: 16px; }}
      .page-header h1 {{ font-size: 22px; margin-bottom: 4px; }}
      .page-header .subtitle {{ font-size: 13px; color: #9ca3af; }}
      .toolbar {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
      .view-toggle {{ display: flex; background: #f3f4f6; border-radius: 8px; overflow: hidden; }}
      .view-toggle button {{ padding: 7px 16px; border: none; background: transparent; cursor: pointer; font-size: 13px; font-weight: 500; color: #6b7280; transition: all .15s; }}
      .view-toggle button.active {{ background: white; color: #1e40af; box-shadow: 0 1px 3px rgba(0,0,0,.1); border-radius: 7px; }}
      .vibe-filters {{ display: flex; gap: 6px; }}
      .vibe-filter {{ padding: 5px 12px; border-radius: 16px; border: 1.5px solid #e5e7eb; background: white; cursor: pointer; font-size: 12px; font-weight: 600; color: #6b7280; transition: all .15s; }}
      .vibe-filter.active {{ border-color: currentColor; }}
      .vibe-filter[data-vibe="social"] {{ color: #d97706; }}
      .vibe-filter[data-vibe="social"].active {{ background: #fffbeb; border-color: #f59e0b; }}
      .vibe-filter[data-vibe="intellectual"] {{ color: #7c3aed; }}
      .vibe-filter[data-vibe="intellectual"].active {{ background: #faf5ff; border-color: #8b5cf6; }}
      .vibe-filter[data-vibe="mixed"] {{ color: #2563eb; }}
      .vibe-filter[data-vibe="mixed"].active {{ background: #eff6ff; border-color: #3b82f6; }}
      .vibe-filter[data-vibe="all"].active {{ background: #f3f4f6; border-color: #9ca3af; color: #374151; }}
      .stats-row {{ display: flex; gap: 16px; font-size: 13px; color: #6b7280; margin-bottom: 12px; }}
      .stats-row strong {{ color: #374151; }}
      /* --- Search + score filter --- */
      .search-bar {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
      .search-input {{ flex: 1; min-width: 160px; padding: 7px 12px; border: 1.5px solid #e5e7eb; border-radius: 8px; font-size: 13px; color: #374151; outline: none; transition: border-color .15s; }}
      .search-input:focus {{ border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,.1); }}
      .score-filter-wrap {{ display: flex; align-items: center; gap: 6px; font-size: 12px; color: #6b7280; white-space: nowrap; }}
      .score-filter-wrap input[type=range] {{ width: 80px; accent-color: #1e40af; cursor: pointer; }}
      #score-label {{ font-weight: 700; color: #374151; min-width: 20px; }}
      /* --- FullCalendar --- */
      #fc-container .fc {{ font-size: 13px; }}
      #fc-container .fc-event {{ cursor: pointer; border: none; padding: 2px 6px; border-radius: 4px; font-size: 11px; }}
      #fc-container .fc-daygrid-event-dot {{ display: none; }}
      #fc-container .fc-toolbar {{ flex-wrap: wrap; gap: 8px; }}
      /* --- Modal --- */
      .evt-modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 1000; justify-content: center; align-items: flex-end; }}
      .evt-modal-overlay.show {{ display: flex; }}
      .evt-modal {{ background: white; border-radius: 16px 16px 0 0; padding: 24px; width: 100%; max-width: 520px; max-height: 85vh; overflow-y: auto; box-shadow: 0 -4px 32px rgba(0,0,0,.15); position: relative; animation: slideUp .2s ease-out; }}
      @keyframes slideUp {{ from {{ transform: translateY(100%); }} to {{ transform: translateY(0); }} }}
      .evt-modal .drag-handle {{ width: 36px; height: 4px; background: #d1d5db; border-radius: 2px; margin: 0 auto 16px; }}
      .evt-modal h3 {{ margin: 0 0 8px; font-size: 18px; color: #1a1a1a; }}
      .evt-modal h3 a {{ color: #1e40af; }}
      .evt-modal .close {{ position: absolute; top: 16px; right: 16px; font-size: 20px; cursor: pointer; color: #9ca3af; background: #f3f4f6; border: none; width: 32px; height: 32px; border-radius: 16px; display: flex; align-items: center; justify-content: center; }}
      .evt-modal .close:hover {{ background: #e5e7eb; color: #374151; }}
      .evt-modal .modal-meta {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 10px; }}
      .evt-modal .meta-line {{ font-size: 14px; color: #6b7280; margin: 4px 0; }}
      .evt-modal .desc {{ font-size: 14px; color: #4b5563; line-height: 1.5; margin: 10px 0; }}
      .evt-modal .reason {{ font-size: 13px; color: #7c3aed; background: #faf5ff; padding: 8px 12px; border-radius: 8px; margin: 10px 0; line-height: 1.4; }}
      .score-badge {{ display: inline-block; font-weight: 700; padding: 2px 10px; border-radius: 12px; font-size: 14px; }}
      .score-high {{ background: #dcfce7; color: #166534; }}
      .score-mid {{ background: #fef3c7; color: #92400e; }}
      .score-low {{ background: #f3f4f6; color: #6b7280; }}
      .vibe-tag {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
      .type-tag {{ display: inline-block; padding: 2px 8px; border-radius: 8px; font-size: 11px; font-weight: 600; }}
      .type-tag.club {{ background: #ede9fe; color: #6d28d9; }}
      .type-tag.cls {{ background: #fef3c7; color: #92400e; }}
      /* --- RSVP --- */
      .rsvp-badges {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }}
      .rsvp-pill {{ font-size: 11px; padding: 3px 10px; border-radius: 10px; font-weight: 600; }}
      .rsvp-going {{ background: #dcfce7; color: #166534; }}
      .rsvp-maybe {{ background: #fef3c7; color: #92400e; }}
      .rsvp-cant {{ background: #fee2e2; color: #991b1b; }}
      .rsvp-btns {{ display: flex; gap: 8px; margin-top: 10px; }}
      .rsvp-btn {{ font-size: 13px; padding: 6px 16px; border: 1.5px solid #e5e7eb; border-radius: 8px; background: white; cursor: pointer; color: #6b7280; font-weight: 500; transition: all .15s; }}
      .rsvp-btn:hover, .rsvp-btn.active {{ font-weight: 700; }}
      .rsvp-btn.going:hover, .rsvp-btn.going.active {{ background: #dcfce7; color: #166534; border-color: #86efac; }}
      .rsvp-btn.maybe:hover, .rsvp-btn.maybe.active {{ background: #fef3c7; color: #92400e; border-color: #fde68a; }}
      .rsvp-btn.cant:hover, .rsvp-btn.cant.active {{ background: #fee2e2; color: #991b1b; border-color: #fca5a5; }}
      .attend-btn {{ font-size: 13px; padding: 6px 16px; border: 1.5px solid #e5e7eb; border-radius: 8px; background: white; cursor: pointer; color: #9ca3af; margin-top: 10px; font-weight: 500; transition: all .15s; }}
      .attend-btn:hover {{ background: #dcfce7; border-color: #86efac; color: #166534; }}
      .attend-btn.done {{ background: #dcfce7; color: #166534; border-color: #86efac; cursor: default; }}
      /* --- Card list view --- */
      #list-view {{ display: none; max-width: 640px; }}
      .day-group {{ margin-bottom: 20px; }}
      .day-header {{ position: sticky; top: 0; background: linear-gradient(#f5f5f5, #f5f5f5ee); backdrop-filter: blur(8px); padding: 10px 0 6px; font-size: 14px; font-weight: 700; color: #374151; z-index: 10; border-bottom: 1px solid #e5e7eb; display: flex; justify-content: space-between; align-items: baseline; }}
      .day-header .day-count {{ font-size: 12px; font-weight: 500; color: #9ca3af; }}
      .see-more-btn {{ display: block; width: 100%; margin: 6px 0 10px; padding: 10px; background: #f9fafb; border: 1.5px dashed #d1d5db; border-radius: 10px; color: #6366f1; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; text-align: center; transition: all .15s; }}
      .see-more-btn:hover {{ background: #ede9fe; border-color: #a78bfa; color: #4f46e5; }}
      .see-more-collapse {{ color: #9ca3af; border-style: solid; background: transparent; }}
      .see-more-collapse:hover {{ background: #f3f4f6; border-color: #d1d5db; color: #6b7280; }}
      .evt-card {{ background: white; border-radius: 12px; margin: 8px 0; box-shadow: 0 1px 4px rgba(0,0,0,.07); border-left: 4px solid; transition: box-shadow .15s, transform .1s; cursor: pointer; overflow: hidden; display: flex; }}
      .evt-card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,.13); transform: translateY(-1px); }}
      .evt-card.vibe-social {{ border-left-color: #f59e0b; }}
      .evt-card.vibe-intellectual {{ border-left-color: #8b5cf6; }}
      .evt-card.vibe-mixed {{ border-left-color: #3b82f6; }}
      .evt-card.rsvp-going-card {{ box-shadow: 0 0 0 2px #bbf7d0, 0 1px 4px rgba(0,0,0,.07); }}
      .evt-card.rsvp-maybe-card {{ box-shadow: 0 0 0 2px #fde68a, 0 1px 4px rgba(0,0,0,.07); }}
      .evt-card .card-img {{ width: 80px; flex-shrink: 0; background: #f3f4f6; object-fit: cover; }}
      .evt-card .card-body {{ flex: 1; padding: 13px 15px; min-width: 0; }}
      .evt-card .card-top {{ display: flex; align-items: flex-start; gap: 8px; }}
      .evt-card .card-title {{ font-size: 14px; font-weight: 700; color: #1a1a1a; flex: 1; text-decoration: none; line-height: 1.35; }}
      .evt-card .card-title:hover {{ color: #4f46e5; }}
      .evt-card .card-score {{ font-weight: 800; padding: 2px 8px; border-radius: 10px; font-size: 12px; white-space: nowrap; flex-shrink: 0; }}
      .evt-card .card-meta {{ font-size: 12px; color: #6b7280; margin-top: 3px; }}
      .evt-card .card-reason {{ font-size: 12px; color: #6d28d9; background: #f5f3ff; padding: 4px 8px; border-radius: 6px; margin-top: 6px; line-height: 1.35; }}
      .evt-card .card-actions {{ display: flex; gap: 6px; align-items: center; margin-top: 8px; flex-wrap: wrap; }}
      .source-badge {{ font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 8px; background: #f3f4f6; color: #9ca3af; text-transform: capitalize; }}
      /* Score bar mini visualization */
      .score-bar {{ height: 2px; border-radius: 2px; background: #e5e7eb; margin-top: 8px; overflow: hidden; }}
      .score-bar-fill {{ height: 100%; border-radius: 2px; transition: width .4s ease; }}
      /* --- Timeline view (Kanban-style week columns) --- */
      #timeline-view {{ display: none; overflow-x: auto; padding-bottom: 8px; }}
      .timeline-week {{ display: flex; gap: 12px; min-width: max-content; padding: 4px 0 12px; }}
      .timeline-col {{ width: 220px; flex-shrink: 0; }}
      .timeline-col-header {{ background: white; border-radius: 10px 10px 0 0; padding: 10px 14px 8px; border-bottom: 3px solid #e5e7eb; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
      .timeline-col-header .col-day {{ font-size: 13px; font-weight: 700; color: #374151; }}
      .timeline-col-header .col-date {{ font-size: 11px; color: #9ca3af; margin-top: 2px; }}
      .timeline-col-header .col-count {{ font-size: 11px; font-weight: 600; background: #f3f4f6; color: #6b7280; padding: 1px 6px; border-radius: 8px; display: inline-block; margin-top: 4px; }}
      .timeline-col-header.today {{ border-bottom-color: #1e40af; }}
      .timeline-col-header.today .col-day {{ color: #1e40af; }}
      .tl-card {{ background: white; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.06); border-top: 3px solid; cursor: pointer; transition: box-shadow .15s, transform .1s; }}
      .tl-card:hover {{ box-shadow: 0 3px 10px rgba(0,0,0,.12); transform: translateY(-1px); }}
      .tl-card.vibe-social {{ border-top-color: #f59e0b; }}
      .tl-card.vibe-intellectual {{ border-top-color: #8b5cf6; }}
      .tl-card.vibe-mixed {{ border-top-color: #3b82f6; }}
      .tl-card .tl-title {{ font-size: 13px; font-weight: 600; color: #1a1a1a; line-height: 1.3; margin-bottom: 5px; }}
      .tl-card .tl-time {{ font-size: 11px; color: #9ca3af; margin-bottom: 3px; }}
      .tl-card .tl-loc {{ font-size: 11px; color: #6b7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
      .tl-card .tl-score {{ font-size: 11px; font-weight: 700; padding: 1px 6px; border-radius: 6px; display: inline-block; margin-top: 4px; }}
      .tl-empty {{ text-align: center; color: #d1d5db; font-size: 12px; padding: 20px 0; }}
      .tl-overflow {{ opacity: .85; border-style: dashed; }}
      .tl-more-btn {{ display: block; width: 100%; padding: 6px; background: #f9fafb; border: 1.5px dashed #d1d5db; border-radius: 8px; color: #6366f1; font-size: 11px; font-weight: 600; cursor: pointer; font-family: inherit; text-align: center; margin-top: 4px; transition: all .15s; }}
      .tl-more-btn:hover {{ background: #ede9fe; border-color: #a78bfa; }}
      .tl-collapse-btn {{ color: #9ca3af; border-color: #e5e7eb; }}
      /* --- Heatmap view (score overview) --- */
      #heat-view {{ display: none; }}
      .heat-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }}
      .heat-day {{ border-radius: 12px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.08); transition: box-shadow .15s; }}
      .heat-day:hover {{ box-shadow: 0 4px 14px rgba(0,0,0,.14); }}
      .heat-day-header {{ padding: 12px 16px 10px; display: flex; align-items: baseline; justify-content: space-between; }}
      .heat-day-header .hd-name {{ font-size: 15px; font-weight: 700; color: white; text-shadow: 0 1px 2px rgba(0,0,0,.2); }}
      .heat-day-header .hd-date {{ font-size: 12px; color: rgba(255,255,255,.8); }}
      .heat-top-event {{ background: white; padding: 12px 16px; cursor: pointer; transition: background .1s; }}
      .heat-top-event:hover {{ background: #f9fafb; }}
      .heat-top-event .he-title {{ font-size: 14px; font-weight: 600; color: #1a1a1a; line-height: 1.3; margin-bottom: 4px; }}
      .heat-top-event .he-meta {{ font-size: 12px; color: #6b7280; }}
      .heat-top-event .he-score {{ font-size: 13px; font-weight: 700; }}
      .heat-more {{ background: #f9fafb; padding: 8px 16px; font-size: 12px; color: #6b7280; border-top: 1px solid #f3f4f6; }}
      .heat-empty {{ background: white; padding: 16px; text-align: center; color: #d1d5db; font-size: 13px; }}
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

    <div class="page-header">
      <h1>Your Week</h1>
      <div class="subtitle">{top_picks} picks for Cambridge &amp; Boston</div>
    </div>
    {user_banner}
    {hot_days_html}
    {taste_nudge_html}
    {groups_html}
    {friend_activity_html}

    <div class="toolbar">
      <div class="view-toggle">
        <button id="btn-list" onclick="switchView('list')">List</button>
        <button id="btn-timeline" onclick="switchView('timeline')">Week</button>
        <button id="btn-heat" onclick="switchView('heat')">Overview</button>
        <button id="btn-cal" onclick="switchView('calendar')">Grid</button>
      </div>
      <div class="vibe-filters">
        <button class="vibe-filter active" data-vibe="all" onclick="filterVibe('all',this)">All</button>
        <button class="vibe-filter" data-vibe="social" onclick="filterVibe('social',this)">Social</button>
        <button class="vibe-filter" data-vibe="intellectual" onclick="filterVibe('intellectual',this)">Brainy</button>
        <button class="vibe-filter" data-vibe="mixed" onclick="filterVibe('mixed',this)">Mixed</button>
        <button class="vibe-filter" id="nearby-btn" onclick="toggleNearby(this)" title="Events within ~2km walking distance" style="font-size:11px;">🚶 Nearby</button>
      </div>
    </div>

    <div class="search-bar">
      <div style="display:flex;gap:8px;align-items:center;flex:1;">
        <input class="search-input" id="search-input" type="text" placeholder="Search events... or ask AI ✦" oninput="applyFilters()" onkeydown="if(event.key==='Enter'&&this.value.trim())askAI()">
        <button id="ai-search-btn" onclick="askAI()" title="AI search" style="flex-shrink:0;background:linear-gradient(135deg,#4f46e5,#7c3aed);color:white;border:none;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .15s;white-space:nowrap;">✦ Ask AI</button>
      </div>
      <div class="score-filter-wrap">
        Min score: <input type="range" min="0" max="100" step="5" value="0" id="score-slider" oninput="document.getElementById('score-label').textContent=this.value;applyFilters()">
        <span id="score-label">0</span>
        &nbsp;&nbsp;
        Max distance: <input type="range" min="1" max="50" step="1" value="50" id="dist-slider" oninput="updateDistLabel();applyFilters()">
        <span id="dist-label">Any</span>
      </div>
    </div>
    <div id="ai-result-banner" style="display:none;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:8px 14px;margin-bottom:8px;font-size:13px;color:#166534;display:flex;align-items:center;gap:8px;">
      <span id="ai-result-text"></span>
      <button onclick="clearAISearch()" style="margin-left:auto;background:none;border:none;cursor:pointer;color:#6b7280;font-size:16px;padding:0 4px;">✕</button>
    </div>

    <div id="cal-view" style="display:none"><div id="fc-container"></div></div>
    <div id="list-view"></div>
    <div id="timeline-view"><div class="timeline-week" id="tl-week"></div></div>
    <div id="heat-view"><div class="heat-grid" id="heat-grid"></div></div>

    <!-- Event detail modal -->
    <div class="evt-modal-overlay" id="evt-modal" onclick="if(event.target===this)closeModal()">
      <div class="evt-modal">
        <div class="drag-handle"></div>
        <button class="close" onclick="closeModal()">&times;</button>
        <div id="modal-body"></div>
      </div>
    </div>

    <script>
    const EVENTS = {events_json_str};
    const RUN_ID = {run_id};
    const USER_TOKEN = '{user_token}';
    const HAS_USER = {'true' if current_user else 'false'};
    const HOME_LAT = {home_lat};
    const HOME_LON = {home_lon};
    const VIBE_COLORS = {{social:'#f59e0b', intellectual:'#8b5cf6', mixed:'#3b82f6'}};
    const VIBE_BG = {{social:'#fffbeb', intellectual:'#faf5ff', mixed:'#eff6ff'}};

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
    let activeVibe = 'all';
    let nearbyOnly = false;

    function toggleNearby(btn) {{
      nearbyOnly = !nearbyOnly;
      btn.classList.toggle('active', nearbyOnly);
      if (nearbyOnly) {{
        // Set dist slider to 2km
        const sl = document.getElementById('dist-slider');
        if (sl) {{ sl.value = 2; updateDistLabel(); }}
      }} else {{
        const sl = document.getElementById('dist-slider');
        if (sl) {{ sl.value = 50; updateDistLabel(); }}
      }}
      applyFilters();
    }}

    function scoreCls(s) {{ return s >= 70 ? 'score-high' : s >= 50 ? 'score-mid' : 'score-low'; }}

    // --- View toggle ---
    function switchView(view) {{
      localStorage.setItem('recom-view', view);
      document.getElementById('cal-view').style.display = view === 'calendar' ? 'block' : 'none';
      document.getElementById('list-view').style.display = view === 'list' ? 'block' : 'none';
      document.getElementById('timeline-view').style.display = view === 'timeline' ? 'block' : 'none';
      document.getElementById('heat-view').style.display = view === 'heat' ? 'block' : 'none';
      ['cal','list','timeline','heat'].forEach(v => {{
        const btn = document.getElementById('btn-' + v);
        if (btn) btn.classList.toggle('active', v === view);
      }});
      if (view === 'calendar' && window._fc) window._fc.updateSize();
      if (view === 'timeline') buildTimelineView();
      if (view === 'heat') buildHeatmapView();
    }}

    // --- Vibe filter ---
    function filterVibe(vibe, btn) {{
      activeVibe = vibe;
      // Only reset vibe buttons, not the nearby toggle
      document.querySelectorAll('.vibe-filter[data-vibe]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      buildListView();
      buildTimelineView();
      buildHeatmapView();
      if (window._fc) {{
        const fcEvents = getFilteredFcEvents();
        window._fc.removeAllEvents();
        fcEvents.forEach(e => window._fc.addEvent(e));
      }}
    }}
    // AI search state
    let aiFilterIds = null; // null = no AI filter active, Set = filter to these IDs
    let aiSearchLabel = '';

    function updateDistLabel() {{
      const v = parseInt(document.getElementById('dist-slider')?.value || '50', 10);
      document.getElementById('dist-label').textContent = v >= 50 ? 'Any' : v + 'km';
    }}

    function getFilteredEvents() {{
      const query = (document.getElementById('search-input')?.value || '').toLowerCase().trim();
      const minScore = parseInt(document.getElementById('score-slider')?.value || '0', 10);
      const maxDist = parseInt(document.getElementById('dist-slider')?.value || '50', 10);
      return EVENTS.filter(e => {{
        if (activeVibe !== 'all' && e.vibe !== activeVibe) return false;
        if (e.score < minScore) return false;
        if (maxDist < 50 && !e.is_online) {{
          const km = distKm(e.lat, e.lon);
          if (km != null && km > maxDist) return false;
        }}
        if (aiFilterIds !== null && !aiFilterIds.has(e.id)) return false;
        if (query && !aiFilterIds) {{
          // Only apply text filter when no AI filter active
          const haystack = (e.title + ' ' + e.location + ' ' + e.description + ' ' + e.match_reason).toLowerCase();
          if (!haystack.includes(query)) return false;
        }}
        return true;
      }});
    }}

    function askAI() {{
      const query = document.getElementById('search-input').value.trim();
      if (!query) return;
      const btn = document.getElementById('ai-search-btn');
      btn.textContent = '⏳';
      btn.disabled = true;

      // Send query + event summaries to backend
      const eventSummaries = EVENTS.map(e => {{
        const km = distKm(e.lat, e.lon);
        return {{
          id: e.id,
          title: e.title,
          start: e.start,
          location: e.location,
          description: e.description,
          match_reason: e.match_reason,
          score: e.score,
          vibe: e.vibe,
          distance_km: km != null ? Math.round(km * 10) / 10 : null,
        }};
      }});

      fetch('/api/ai-search', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{query, events: eventSummaries}})
      }}).then(r => r.json()).then(d => {{
        btn.textContent = '✦ Ask AI';
        btn.disabled = false;
        if (d.ok && d.ids) {{
          aiFilterIds = new Set(d.ids);
          aiSearchLabel = d.summary || `Showing ${{d.ids.length}} results for "${{query}}"`;
          const banner = document.getElementById('ai-result-banner');
          document.getElementById('ai-result-text').textContent = aiSearchLabel;
          banner.style.display = 'flex';
          applyFilters();
        }}
      }}).catch(() => {{
        btn.textContent = '✦ Ask AI';
        btn.disabled = false;
      }});
    }}

    function clearAISearch() {{
      aiFilterIds = null;
      document.getElementById('search-input').value = '';
      document.getElementById('ai-result-banner').style.display = 'none';
      applyFilters();
    }}

    function applyFilters() {{
      buildListView();
      buildTimelineView();
      buildHeatmapView();
      if (window._fc) {{
        window._fc.removeAllEvents();
        getFilteredFcEvents().forEach(e => window._fc.addEvent(e));
      }}
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
          const btns = btn.parentElement.querySelectorAll('.rsvp-btn');
          btns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          // Update EVENTS so card re-renders with indicator
          const ev = EVENTS.find(x => x.id === eventId);
          if (ev) {{ ev.my_rsvp = status; applyFilters(); }}
        }}
      }});
    }}

    // --- Modal ---
    function openModal(evt) {{
      const rsvpLabels = {{going:'going', maybe:'maybe', cant:"can't"}};
      const rsvpCls = {{going:'rsvp-going', maybe:'rsvp-maybe', cant:'rsvp-cant'}};
      let badges = evt.rsvps.map(r =>
        `<span class="rsvp-pill ${{rsvpCls[r.status] || ''}}">${{r.user_name}} ${{rsvpLabels[r.status] || r.status}}</span>`
      ).join('');
      let typeBadge = '';
      if (evt.event_type === 'club') typeBadge = '<span class="type-tag club">CLUB</span>';
      else if (evt.event_type === 'class') typeBadge = '<span class="type-tag cls">CLASS</span>';
      let vibeBg = VIBE_BG[evt.vibe] || VIBE_BG.mixed;
      let vibeColor = VIBE_COLORS[evt.vibe] || VIBE_COLORS.mixed;
      let timeStr = '';
      if (evt.start) {{
        try {{
          const d = new Date(evt.start);
          timeStr = d.toLocaleDateString('en-US', {{weekday:'long', month:'short', day:'numeric'}}) +
                    ' at ' + d.toLocaleTimeString('en-US', {{hour:'numeric', minute:'2-digit'}});
        }} catch(e) {{}}
      }}
      let rsvpBtns = '';
      if (HAS_USER) {{
        const eid = evt.id.replace(/'/g, "\\\\'");
        rsvpBtns = `<div class="rsvp-btns">
          <button class="rsvp-btn going" onclick="setRsvp('${{eid}}', ${{RUN_ID}}, 'going', this)">Going</button>
          <button class="rsvp-btn maybe" onclick="setRsvp('${{eid}}', ${{RUN_ID}}, 'maybe', this)">Maybe</button>
          <button class="rsvp-btn cant" onclick="setRsvp('${{eid}}', ${{RUN_ID}}, 'cant', this)">Can't</button>
        </div>`;
      }}
      const titleEsc = evt.title.replace(/'/g, "\\\\'").replace(/"/g, '&quot;');
      const modalImg = evt.image_url ? `<img src="${{evt.image_url}}" alt="" style="width:100%;height:160px;object-fit:cover;border-radius:10px;margin-bottom:14px;" onerror="this.style.display='none'">` : '';
      document.getElementById('modal-body').innerHTML = `
        ${{modalImg}}
        <div class="modal-meta">
          <span class="score-badge ${{scoreCls(evt.score)}}">${{evt.score}}</span>
          <span class="vibe-tag" style="background:${{vibeBg}};color:${{vibeColor}}">${{evt.vibe}}</span>
          ${{typeBadge}}
          ${{evt.source ? '<span style="font-size:11px;color:#9ca3af;font-weight:600;text-transform:capitalize">via '+evt.source+'</span>' : ''}}
        </div>
        <h3>${{evt.url ? '<a href="'+evt.url+'" target="_blank">'+evt.title+'</a>' : evt.title}}</h3>
        <div class="meta-line">${{timeStr}}</div>
        ${{evt.location || distLabel(evt) ? '<div class="meta-line">' + [evt.location, evt.price, distLabel(evt)].filter(Boolean).join(' · ') + '</div>' : ''}}
        ${{evt.match_reason ? '<div class="reason">' + evt.match_reason + '</div>' : ''}}
        ${{evt.description ? '<div class="desc">' + evt.description + '</div>' : ''}}
        ${{(() => {{
          const s = evt.scores || {{}};
          const dims = [['Match','interest'],['Social','social'],['FOMO','urgency'],['Easy','logistics'],['Friends','friend'],['Discovery','discovery'],['Quality','quality']];
          const hasData = dims.some(([,k]) => s[k] > 0);
          if (!hasData) return '';
          const bars = dims.map(([label, key]) => {{
            const v = s[key] || 0;
            const pct = Math.round(v / 15 * 100);
            const col = v >= 11 ? '#22c55e' : v >= 7 ? '#818cf8' : '#9ca3af';
            return `<div style="flex:1;min-width:0;text-align:center;">
              <div style="height:40px;background:#f3f4f6;border-radius:6px;position:relative;margin-bottom:3px;overflow:hidden;">
                <div style="position:absolute;bottom:0;left:0;right:0;height:${{pct}}%;background:${{col}};border-radius:6px;transition:height .4s;"></div>
              </div>
              <div style="font-size:9px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.5px;">${{label}}</div>
              <div style="font-size:11px;font-weight:800;color:#374151;">${{v}}</div>
            </div>`;
          }}).join('');
          return `<div style="margin:12px 0;"><div style="font-size:11px;font-weight:700;color:#9ca3af;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px;">Score breakdown</div><div style="display:flex;gap:4px;align-items:flex-end;">${{bars}}</div></div>`;
        }})()}}
        ${{badges ? '<div class="rsvp-badges">' + badges + '</div>' : ''}}
        ${{rsvpBtns}}
        <button class="attend-btn" onclick="markAttend('${{evt.id}}', ${{RUN_ID}}, '${{titleEsc}}', this)">I went</button>
      `;
      document.getElementById('evt-modal').classList.add('show');
    }}
    function closeModal() {{
      document.getElementById('evt-modal').classList.remove('show');
    }}
    document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});

    // --- FullCalendar ---
    function getFilteredFcEvents() {{
      return getFilteredEvents().filter(e => e.start).map(e => ({{
        id: e.id,
        title: '[' + e.score + '] ' + e.title,
        start: e.start,
        end: e.end || undefined,
        backgroundColor: VIBE_COLORS[e.vibe] || VIBE_COLORS.mixed,
        borderColor: VIBE_COLORS[e.vibe] || VIBE_COLORS.mixed,
        extendedProps: e
      }}));
    }}

    document.addEventListener('DOMContentLoaded', function() {{
      const calEl = document.getElementById('fc-container');
      const isMobile = window.innerWidth < 641;
      const calendar = new FullCalendar.Calendar(calEl, {{
        initialView: isMobile ? 'listWeek' : 'dayGridMonth',
        headerToolbar: {{
          left: 'prev,next today',
          center: 'title',
          right: isMobile ? 'listWeek,dayGridMonth' : 'dayGridMonth,timeGridWeek,timeGridDay'
        }},
        events: getFilteredFcEvents(),
        eventClick: function(info) {{
          info.jsEvent.preventDefault();
          openModal(info.event.extendedProps);
        }},
        height: 'auto',
        nowIndicator: true,
        dayMaxEvents: 4,
      }});
      calendar.render();
      window._fc = calendar;

      // --- Build all views ---
      buildListView();
      buildTimelineView();
      buildHeatmapView();

      // Default: list on mobile, saved pref or list on desktop
      const saved = localStorage.getItem('recom-view');
      const defaultView = saved || 'list';
      switchView(defaultView);
    }});

    // Track which day groups are expanded
    const expandedDays = new Set();

    function buildListView() {{
      const container = document.getElementById('list-view');
      const filtered = getFilteredEvents();
      const sorted = [...filtered].filter(e => e.start).sort((a, b) => a.start.localeCompare(b.start));
      const undated = filtered.filter(e => !e.start);
      const groups = {{}};
      sorted.forEach(e => {{
        const day = e.start.slice(0, 10);
        if (!groups[day]) groups[day] = [];
        groups[day].push(e);
      }});
      const today = new Date(); today.setHours(0,0,0,0);
      const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate() + 1);
      let html = '';
      Object.keys(groups).sort().forEach(day => {{
        const d = new Date(day + 'T00:00:00');
        let label = d.toLocaleDateString('en-US', {{weekday:'long', month:'long', day:'numeric'}});
        if (d.getTime() === today.getTime()) label = 'Today, ' + d.toLocaleDateString('en-US', {{month:'long', day:'numeric'}});
        else if (d.getTime() === tomorrow.getTime()) label = 'Tomorrow, ' + d.toLocaleDateString('en-US', {{month:'long', day:'numeric'}});
        const allDay = [...groups[day]].sort((a,b) => b.score - a.score);
        const primary = allDay.filter(e => e.primary);
        const overflow = allDay.filter(e => !e.primary);
        const isExpanded = expandedDays.has(day);
        const shown = isExpanded ? allDay : primary;
        const total = allDay.length;
        const hiddenCount = overflow.length;
        html += `<div class="day-group" id="dg-${{day}}"><div class="day-header">
          <span>${{label}}</span>
          <span class="day-count">${{total}} event${{total !== 1 ? 's' : ''}}</span>
        </div>`;
        shown.forEach(e => {{ html += renderCard(e); }});
        if (hiddenCount > 0 && !isExpanded) {{
          html += `<button class="see-more-btn" onclick="expandDay('${{day}}')">
            + ${{hiddenCount}} more option${{hiddenCount !== 1 ? 's' : ''}} this day
          </button>`;
        }} else if (hiddenCount > 0 && isExpanded) {{
          html += `<button class="see-more-btn see-more-collapse" onclick="collapseDay('${{day}}')">
            ↑ Show top picks only
          </button>`;
        }}
        html += '</div>';
      }});
      if (undated.length) {{
        const uPrimary = undated.filter(e => e.primary).sort((a,b) => b.score - a.score);
        const uOverflow = undated.filter(e => !e.primary).sort((a,b) => b.score - a.score);
        const uExpanded = expandedDays.has('__undated__');
        const uShown = uExpanded ? [...uPrimary, ...uOverflow] : uPrimary;
        html += '<div class="day-group"><div class="day-header"><span>No Date</span><span class="day-count">' + undated.length + '</span></div>';
        uShown.forEach(e => {{ html += renderCard(e); }});
        if (uOverflow.length && !uExpanded)
          html += `<button class="see-more-btn" onclick="expandDay('__undated__')">+ ${{uOverflow.length}} more</button>`;
        else if (uOverflow.length && uExpanded)
          html += `<button class="see-more-btn see-more-collapse" onclick="collapseDay('__undated__')">↑ Show top picks only</button>`;
        html += '</div>';
      }}
      container.innerHTML = html || '<p style="color:#9ca3af">No events to display.</p>';
    }}

    function expandDay(day) {{
      expandedDays.add(day);
      buildListView();
    }}
    function collapseDay(day) {{
      expandedDays.delete(day);
      buildListView();
    }}

    function renderCard(e) {{
      const rsvpLabels = {{going:'going', maybe:'maybe', cant:"can't"}};
      const rsvpCls = {{going:'rsvp-going', maybe:'rsvp-maybe', cant:'rsvp-cant'}};
      let badges = e.rsvps.map(r =>
        `<span class="rsvp-pill ${{rsvpCls[r.status] || ''}}">${{r.user_name}} ${{rsvpLabels[r.status] || r.status}}</span>`
      ).join('');
      let timeStr = '';
      if (e.start) {{
        try {{
          const d = new Date(e.start);
          if (d.getHours() !== 0 || d.getMinutes() !== 0)
            timeStr = d.toLocaleTimeString('en-US', {{hour:'numeric', minute:'2-digit'}});
        }} catch(x) {{}}
      }}
      let typeBadge = '';
      if (e.event_type === 'club') typeBadge = '<span class="type-tag club">CLUB</span>';
      else if (e.event_type === 'class') typeBadge = '<span class="type-tag cls">CLASS</span>';
      const titleEsc = e.title.replace(/'/g, "\\\\'").replace(/"/g, '&quot;');
      const eid = e.id.replace(/'/g, "\\\\'");
      let rsvpBtns = '';
      if (HAS_USER) {{
        rsvpBtns = `<button class="rsvp-btn going" onclick="setRsvp('${{eid}}', ${{RUN_ID}}, 'going', this)">Going</button>
          <button class="rsvp-btn maybe" onclick="setRsvp('${{eid}}', ${{RUN_ID}}, 'maybe', this)">Maybe</button>`;
      }}
      const barColor = e.score >= 70 ? '#22c55e' : e.score >= 50 ? '#f59e0b' : '#9ca3af';
      const barW = Math.min(100, Math.round(e.score * 100 / 105));
      const imgHtml = e.image_url ? `<img class="card-img" src="${{e.image_url}}" alt="" loading="lazy" onerror="this.style.display='none'">` : '';
      const srcBadge = e.source ? `<span class="source-badge">${{e.source}}</span>` : '';
      // My RSVP indicator
      let myRsvpBadge = '';
      if (e.my_rsvp === 'going') myRsvpBadge = '<span title="You&apos;re going!" style="font-size:13px;color:#16a34a;font-weight:700;">✓ Going</span>';
      else if (e.my_rsvp === 'maybe') myRsvpBadge = '<span title="On your maybe list" style="font-size:12px;color:#d97706;font-weight:600;">Maybe</span>';
      else if (e.my_rsvp === 'cant') myRsvpBadge = '<span style="font-size:11px;color:#9ca3af;font-weight:500;text-decoration:line-through;">Can&apos;t go</span>';
      return `<div class="evt-card vibe-${{e.vibe}}${{e.my_rsvp === 'maybe' ? ' rsvp-maybe-card' : ''}}${{e.my_rsvp === 'going' ? ' rsvp-going-card' : ''}}" onclick="openModal(EVENTS.find(x=>x.id==='${{eid}}'))">
        ${{imgHtml}}
        <div class="card-body">
          <div class="card-top">
            ${{e.url ? '<a href="'+e.url+'" target="_blank" class="card-title" onclick="event.stopPropagation()">'+e.title+'</a>' : '<span class="card-title">'+e.title+'</span>'}}
            <span class="card-score ${{scoreCls(e.score)}}">${{e.score}}</span>
          </div>
          <div class="card-meta">${{[timeStr, e.location, e.price, distLabel(e)].filter(Boolean).join(' · ')}}</div>
          ${{e.match_reason ? '<div class="card-reason">' + e.match_reason + '</div>' : ''}}
          <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap;">
            ${{srcBadge}}
            ${{myRsvpBadge}}
            ${{badges ? '<span class="rsvp-badges" onclick="event.stopPropagation()">' + badges + '</span>' : ''}}
          </div>
          <div class="score-bar"><div class="score-bar-fill" style="width:${{barW}}%;background:${{barColor}}"></div></div>
          ${{rsvpBtns || typeBadge ? '<div class="card-actions" onclick="event.stopPropagation()">' + rsvpBtns + typeBadge + '</div>' : ''}}
        </div>
      </div>`;
    }}

    // --- Timeline (week columns) view ---
    function buildTimelineView() {{
      const container = document.getElementById('tl-week');
      const filtered = getFilteredEvents();
      const today = new Date(); today.setHours(0,0,0,0);
      const groups = {{}};
      filtered.filter(e => e.start).forEach(e => {{
        const day = e.start.slice(0, 10);
        if (!groups[day]) groups[day] = [];
        groups[day].push(e);
      }});
      // Build 7 days starting today
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
          html += '<div class="tl-empty">Nothing yet</div>';
        }} else {{
          const tlKey = 'tl-' + key;
          const primary = dayEvts.filter(e => e.primary);
          const overflow = dayEvts.filter(e => !e.primary);
          const tlExpanded = expandedDays.has(tlKey);
          const shown = tlExpanded ? dayEvts : primary;
          shown.forEach(e => {{
            const eid = e.id.replace(/'/g, "\\\\'");
            let timeStr = '';
            try {{
              const dt = new Date(e.start);
              if (dt.getHours() || dt.getMinutes())
                timeStr = dt.toLocaleTimeString('en-US', {{hour: 'numeric', minute: '2-digit'}});
            }} catch(x) {{}}
            html += `<div class="tl-card vibe-${{e.vibe}}${{!e.primary && tlExpanded ? ' tl-overflow' : ''}}" onclick="openModal(EVENTS.find(x=>x.id==='${{eid}}'))">
              <div class="tl-title">${{e.title}}</div>
              ${{timeStr ? '<div class="tl-time">' + timeStr + '</div>' : ''}}
              ${{e.location ? '<div class="tl-loc">' + e.location + '</div>' : ''}}
              <span class="tl-score ${{scoreCls(e.score)}}">${{e.score}}</span>
            </div>`;
          }});
          if (overflow.length && !tlExpanded)
            html += `<button class="tl-more-btn" onclick="expandDay('${{tlKey}}');buildTimelineView()">+${{overflow.length}} more</button>`;
          else if (overflow.length && tlExpanded)
            html += `<button class="tl-more-btn tl-collapse-btn" onclick="collapseDay('${{tlKey}}');buildTimelineView()">↑ less</button>`;
        }}
        html += '</div>';
      }}
      container.innerHTML = html;
    }}

    // --- Heatmap (score overview) view ---
    function buildHeatmapView() {{
      const container = document.getElementById('heat-grid');
      const filtered = getFilteredEvents();
      const today = new Date(); today.setHours(0,0,0,0);
      const groups = {{}};
      filtered.filter(e => e.start).forEach(e => {{
        const day = e.start.slice(0, 10);
        if (!groups[day]) groups[day] = [];
        groups[day].push(e);
      }});
      // Color based on top score: green=high, amber=mid, blue=normal, gray=low
      function dayBg(score) {{
        if (score >= 75) return 'linear-gradient(135deg,#166534,#16a34a)';
        if (score >= 60) return 'linear-gradient(135deg,#1e40af,#2563eb)';
        if (score >= 45) return 'linear-gradient(135deg,#92400e,#d97706)';
        return 'linear-gradient(135deg,#374151,#6b7280)';
      }}
      let html = '';
      // Show days that have events, plus next 7 days
      const dayKeys = new Set();
      for (let i = 0; i < 7; i++) {{
        const d = new Date(today); d.setDate(today.getDate() + i);
        dayKeys.add(d.toISOString().slice(0, 10));
      }}
      Object.keys(groups).forEach(k => dayKeys.add(k));
      [...dayKeys].sort().filter(k => k >= today.toISOString().slice(0, 10)).forEach(key => {{
        const evts = (groups[key] || []).sort((a, b) => b.score - a.score);
        const d = new Date(key + 'T00:00:00');
        const isToday = key === today.toISOString().slice(0, 10);
        const dayName = isToday ? 'Today' : d.toLocaleDateString('en-US', {{weekday: 'long'}});
        const dateFmt = d.toLocaleDateString('en-US', {{month: 'short', day: 'numeric'}});
        const topEvt = evts[0];
        const topScore = topEvt ? topEvt.score : 0;
        const highCount = evts.filter(e => e.score >= 60).length;
        const bg = evts.length ? dayBg(topScore) : 'linear-gradient(135deg,#d1d5db,#e5e7eb)';
        const textCol = evts.length ? 'white' : '#9ca3af';
        const hotLabel = highCount >= 4 ? 'Busy day' : highCount >= 2 ? 'Good day' : '';
        html += `<div class="heat-day">
          <div class="heat-day-header" style="background:${{bg}}">
            <div>
              <div class="hd-name" style="color:${{textCol}}">${{dayName}}${{hotLabel ? ' <span style="font-size:10px;font-weight:600;opacity:.85">'+hotLabel+'</span>' : ''}}</div>
              <div class="hd-date" style="color:${{evts.length?'rgba(255,255,255,.7)':'#9ca3af'}}">${{dateFmt}} · ${{evts.length}} event${{evts.length!==1?'s':''}}</div>
            </div>
            ${{evts.length ? `<div style="background:rgba(0,0,0,.2);border-radius:50%;width:40px;height:40px;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:800;color:white;">${{topScore}}</div>` : ''}}
          </div>`;
        if (!evts.length) {{
          html += '<div class="heat-empty">Nothing on the calendar</div>';
        }} else {{
          // Show top event as hero
          const eid = topEvt.id.replace(/'/g, "\\\\'");
          let timeStr = '';
          try {{
            const dt = new Date(topEvt.start);
            if (dt.getHours() || dt.getMinutes())
              timeStr = ' · ' + dt.toLocaleTimeString('en-US', {{hour: 'numeric', minute: '2-digit'}});
          }} catch(x) {{}}
          const vibeBar = `<div style="width:4px;background:${{VIBE_COLORS[topEvt.vibe]||VIBE_COLORS.mixed}};border-radius:2px;flex-shrink:0;align-self:stretch;"></div>`;
          html += `<div class="heat-top-event" onclick="openModal(EVENTS.find(x=>x.id==='${{eid}}'))">
            <div style="display:flex;gap:8px;align-items:flex-start;">
              ${{vibeBar}}
              <div style="flex:1;min-width:0;">
                <div class="he-title">${{topEvt.title}}</div>
                <div class="he-meta">${{topEvt.location || ''}}${{timeStr}}</div>
                ${{topEvt.match_reason ? `<div style="font-size:11px;color:#7c3aed;margin-top:4px;line-height:1.35;">${{topEvt.match_reason.slice(0,80)}}${{topEvt.match_reason.length>80?'…':''}}</div>` : ''}}
              </div>
            </div>
          </div>`;
          // List remaining events (primary first, overflow collapsed)
          const restPrimary = evts.slice(1).filter(e => e.primary);
          const heatOverflow = evts.filter(e => !e.primary);
          const heatKey = 'heat-' + key;
          const heatExpanded = expandedDays.has(heatKey);
          const restShown = heatExpanded ? evts.slice(1) : restPrimary;
          if (restShown.length > 0) {{
            let moreHtml = '<div style="padding:0 16px 10px;">';
            restShown.forEach(e => {{
              const eid2 = e.id.replace(/'/g, "\\\\'");
              let t2 = '';
              try {{ const dt2 = new Date(e.start); if (dt2.getHours()||dt2.getMinutes()) t2 = dt2.toLocaleTimeString('en-US',{{hour:'numeric',minute:'2-digit'}}); }} catch(x){{}}
              moreHtml += `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-top:1px solid #f3f4f6;cursor:pointer;${{!e.primary && heatExpanded ? 'opacity:.8;' : ''}}" onclick="openModal(EVENTS.find(x=>x.id==='${{eid2}}'))">
                <div style="width:3px;height:24px;background:${{VIBE_COLORS[e.vibe]||VIBE_COLORS.mixed}};border-radius:2px;flex-shrink:0;"></div>
                <div style="flex:1;min-width:0;">
                  <div style="font-size:12px;font-weight:600;color:#111;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${{e.title}}</div>
                  <div style="font-size:11px;color:#9ca3af;">${{t2||e.location||''}}</div>
                </div>
                <span style="font-size:11px;font-weight:800;color:#6b7280;flex-shrink:0;">${{e.score}}</span>
              </div>`;
            }});
            if (heatOverflow.length && !heatExpanded)
              moreHtml += `<button onclick="expandDay('${{heatKey}}');buildHeatmapView()" style="margin-top:6px;width:100%;padding:6px;background:#f9fafb;border:1.5px dashed #d1d5db;border-radius:8px;color:#6366f1;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit;">+${{heatOverflow.length}} more</button>`;
            else if (heatOverflow.length && heatExpanded)
              moreHtml += `<button onclick="collapseDay('${{heatKey}}');buildHeatmapView()" style="margin-top:6px;width:100%;padding:6px;background:transparent;border:1px solid #e5e7eb;border-radius:8px;color:#9ca3af;font-size:11px;cursor:pointer;font-family:inherit;">↑ less</button>`;
            moreHtml += '</div>';
            html += moreHtml;
          }} else if (heatOverflow.length && !heatExpanded) {{
            html += `<div style="padding:4px 16px 10px;"><button onclick="expandDay('${{heatKey}}');buildHeatmapView()" style="width:100%;padding:6px;background:#f9fafb;border:1.5px dashed #d1d5db;border-radius:8px;color:#6366f1;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit;">+${{heatOverflow.length}} more options</button></div>`;
          }}
        }}
        html += '</div>';
      }});
      container.innerHTML = html || '<p style="color:#9ca3af;padding:20px">No events to display.</p>';
    }}
    </script>
    """ + LAYOUT_FOOT
    resp = HTMLResponse(page_html)
    return _maybe_set_cookie(request, resp, current_user)


@app.post("/api/ai-search", response_class=JSONResponse)
async def ai_search(request: Request):
    """AI-powered event search: interpret a natural language query against current events."""
    import anthropic as _anthropic
    data = await request.json()
    query = (data.get("query") or "").strip()
    events = data.get("events") or []

    if not query or not events:
        return {"ok": False, "error": "Missing query or events"}

    settings = Settings()
    if not settings.anthropic_api_key:
        return {"ok": False, "error": "No API key"}

    # Build a compact event list for the prompt
    lines = []
    for i, e in enumerate(events[:200]):  # cap at 200
        dt = ""
        if e.get("start"):
            try:
                from datetime import datetime as _dt
                d = _dt.fromisoformat(e["start"])
                dt = d.strftime("%a %b %-d %-I%p")
            except Exception:
                dt = e["start"][:10]
        dist = f'{e["distance_km"]}km' if e.get("distance_km") is not None else "?"
        lines.append(f'{i}. [{e["id"]}] {e["title"]} | {dt} | {e.get("location","?")} | {dist} | score:{e["score"]} | {e.get("description","")[:80]}')

    event_list = "\n".join(lines)

    prompt = f"""You are helping a user find events from their personalized recommendation list.

User query: "{query}"

Events (format: index. [id] title | date | location | distance_from_home | score | description):
{event_list}

Return a JSON object with:
- "ids": array of event IDs that best match the query (empty array if none match)
- "summary": one sentence describing what you found (e.g. "3 jazz events this weekend")

Only include genuinely relevant matches. Interpret the query loosely:
- "jazz tonight" → jazz events on today's date
- "outdoor stuff" → outdoor/nature/active events
- "cheap things" → free or low-cost events
- "nearby" / "walkable" / "close" → events with distance_km < 3
- "this weekend" → events on Saturday or Sunday
- "music" → concerts, live music, DJ sets

Return ONLY valid JSON, no markdown."""

    try:
        client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.claude_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Strip fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        return {"ok": True, "ids": result.get("ids", []), "summary": result.get("summary", "")}
    except Exception as exc:
        logger.exception("AI search failed")
        return {"ok": False, "error": str(exc)}


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

    # Notify group-mates when RSVP is "going"
    if data["status"] == "going":
        try:
            settings = Settings()
            rsvper_name = user.get("name") or user.get("email", "")
            # Get event info
            event_row = db.conn.execute(
                "SELECT title, url FROM events WHERE event_id = ? LIMIT 1",
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
        except Exception:
            logger.exception("Error sending RSVP notifications")

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


@app.get("/bucket-list", response_class=HTMLResponse)
async def bucket_list_page(request: Request, response: Response):
    """Bucket list — first-class page showing activities with status tracking."""
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    items = db.get_user_bucket_list(user_id)

    # Get latest run events for fuzzy matching bucket list items to events
    run = db.get_user_latest_run(user_id)
    matched_events: dict[int, list[dict]] = {}
    if run:
        kept_events = db.conn.execute(
            """SELECT e.event_id, e.title, e.start_time, e.url, rk.score
               FROM events e JOIN rankings rk ON rk.event_id = e.event_id AND rk.run_id = e.run_id
               WHERE e.run_id = ? AND rk.keep = 1 ORDER BY rk.score DESC LIMIT 300""",
            (run["id"],),
        ).fetchall()
        kept_events = [dict(r) for r in kept_events]
        # Simple keyword match: check if any word from bucket item appears in event title
        for item in items:
            words = [w.lower() for w in item["activity"].split() if len(w) > 3]
            matches = []
            for ev in kept_events:
                ev_title_lower = (ev.get("title") or "").lower()
                if any(w in ev_title_lower for w in words):
                    matches.append(ev)
            if matches:
                matched_events[item["id"]] = matches[:3]

    pending = [i for i in items if i.get("status", "pending") != "done"]
    done = [i for i in items if i.get("status", "pending") == "done"]
    total = len(items)
    done_count = len(done)

    def _item_html(item: dict) -> str:
        item_id = item["id"]
        status = item.get("status", "pending")
        is_done = status == "done"
        matches = matched_events.get(item_id, [])
        match_html = ""
        if matches and not is_done:
            match_html = '<div style="margin-top:6px;">'
            for m in matches:
                dt = ""
                if m.get("start_time"):
                    try:
                        from datetime import datetime as _dt
                        dt = _dt.fromisoformat(m["start_time"]).strftime("%b %d")
                    except Exception:
                        pass
                match_html += (
                    f'<a href="{m.get("url","#")}" target="_blank" '
                    f'style="display:inline-block;margin-right:6px;margin-bottom:4px;padding:3px 10px;'
                    f'background:#ede9fe;color:#6d28d9;border-radius:12px;font-size:12px;'
                    f'text-decoration:none;font-weight:600;">'
                    f'🎯 {m["title"][:40]}{" · " + dt if dt else ""}</a>'
                )
            match_html += "</div>"
        done_style = "text-decoration:line-through;color:#9ca3af;" if is_done else ""
        btn_html = (
            f'<button onclick="setStatus({item_id},\'pending\')" '
            f'style="padding:3px 10px;border:1px solid #d1d5db;background:#f9fafb;color:#374151;'
            f'border-radius:6px;font-size:12px;cursor:pointer;margin-left:8px;">Undo</button>'
            if is_done else
            f'<button onclick="setStatus({item_id},\'done\')" '
            f'style="padding:3px 10px;border:1px solid #16a34a;background:#dcfce7;color:#166534;'
            f'border-radius:6px;font-size:12px;cursor:pointer;margin-left:8px;">✓ Done</button>'
        )
        del_btn = (
            f'<button onclick="delItem({item_id})" '
            f'style="padding:3px 8px;border:none;background:none;color:#9ca3af;'
            f'font-size:12px;cursor:pointer;" title="Remove">✕</button>'
        )
        completed_label = ""
        if is_done and item.get("completed_at"):
            try:
                from datetime import datetime as _dt
                completed_label = f' <span style="font-size:11px;color:#9ca3af;">({_dt.fromisoformat(item["completed_at"]).strftime("%b %d, %Y")})</span>'
            except Exception:
                pass
        return (
            f'<div id="bl-{item_id}" style="display:flex;align-items:flex-start;gap:8px;'
            f'padding:10px 12px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:8px;'
            f'{"background:#f9fafb;" if is_done else "background:#fff;"}">'
            f'<div style="flex:1;">'
            f'<span style="{done_style}font-size:15px;font-weight:600;">{item["activity"]}</span>'
            f'{completed_label}'
            f'{match_html}'
            f'</div>'
            f'<div style="display:flex;align-items:center;flex-shrink:0;">'
            f'{btn_html}{del_btn}'
            f'</div>'
            f'</div>'
        )

    pending_html = "".join(_item_html(i) for i in pending) or '<p style="color:#9ca3af;">No pending items.</p>'
    done_html = "".join(_item_html(i) for i in done) or ""

    resp = HTMLResponse(_layout("Bucket List", f"""
    <h1>Bucket List</h1>
    <p style="color:#6b7280;margin-bottom:8px;font-size:14px;">
        Activities you want to do. Items matching current recommendations are highlighted.
        <strong style="color:#374151;">{done_count}/{total} completed</strong>
    </p>

    <div style="margin-bottom:24px;">
        <div style="display:flex;gap:8px;margin-bottom:16px;">
            <input id="new-item" type="text" placeholder="Add something to your bucket list..."
                   style="flex:1;padding:9px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;"
                   onkeydown="if(event.key==='Enter')addItem()">
            <button onclick="addItem()"
                    style="padding:9px 18px;background:#2563eb;color:white;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;">Add</button>
        </div>
        <div id="pending-list">{pending_html}</div>
    </div>

    {f'''<details style="margin-top:8px;">
        <summary style="cursor:pointer;color:#6b7280;font-size:14px;font-weight:600;padding:8px 0;">
            Completed ({done_count})
        </summary>
        <div style="margin-top:12px;">{done_html}</div>
    </details>''' if done_html else ""}

    <script>
    async function addItem() {{
        const inp = document.getElementById('new-item');
        const activity = inp.value.trim();
        if (!activity) return;
        const r = await fetch('/api/bucket/add', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{activity}})}});
        const d = await r.json();
        if (d.ok) {{ inp.value = ''; location.reload(); }}
        else alert(d.error || 'Failed to add');
    }}
    async function delItem(id) {{
        if (!confirm('Remove this item?')) return;
        await fetch('/api/bucket/delete', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id}})}});
        document.getElementById('bl-'+id)?.remove();
    }}
    async function setStatus(id, status) {{
        await fetch('/api/bucket/status', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id,status}})}});
        location.reload();
    }}
    </script>
    """, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.post("/api/bucket/status")
async def api_bucket_status(request: Request):
    data = await request.json()
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    db.update_bucket_item_status(int(data.get("id", 0)), data.get("status", "pending"), user_id)
    return JSONResponse({"ok": True})


@app.get("/attended", response_class=HTMLResponse)
async def attended_page(request: Request, response: Response):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    try:
        rows = db.conn.execute(
            "SELECT a.*, e.title FROM attended a LEFT JOIN events e ON e.event_id = a.event_id WHERE a.user_id = ? ORDER BY a.attended_at DESC",
            (user_id,),
        ).fetchall()
    except Exception:
        rows = []

    rows_html = ""
    for r in rows:
        r = dict(r)
        stars = "★" * (r.get("rating") or 0) + "☆" * (5 - (r.get("rating") or 0))
        title = (r.get("title") or r.get("event_id", ""))[:50]
        rows_html += f"""<tr>
            <td>{title}</td>
            <td>{(r.get('attended_at') or '')[:10]}</td>
            <td style="color:#f59e0b;">{stars}</td>
            <td>{r.get('notes') or ''}</td>
        </tr>"""

    resp = HTMLResponse(_layout("History", f"""
    <h1>Events You Attended</h1>
    <p style="color:#6b7280;margin-bottom:16px;">Mark events as attended from the calendar view. Ratings feed back into recommendations.</p>
    <table>
        <thead><tr><th>Event</th><th>Date</th><th>Rating</th><th>Notes</th></tr></thead>
        <tbody>{rows_html if rows_html else '<tr><td colspan="4" style="color:#9ca3af;padding:12px;">No events marked yet.</td></tr>'}</tbody>
    </table>
    """, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/venues", response_class=HTMLResponse)
async def venues_page(request: Request, response: Response):
    """Venue history — places the user has attended events."""
    db = get_db()
    current_user = _get_current_user(request)
    if not current_user:
        return RedirectResponse("/login")
    venues = db.get_venue_profile(current_user["id"])
    nav = render_nav(current_user)

    rows_html = ""
    for v in venues:
        stars = ""
        if v.get("avg_rating"):
            r = round(v["avg_rating"])
            stars = "★" * r + "☆" * (5 - r) + f' <span style="color:#9ca3af;font-size:12px;">({v["avg_rating"]:.1f})</span>'
        last = ""
        if v.get("last_visited"):
            try:
                from datetime import datetime as _dt
                last = _dt.fromisoformat(v["last_visited"]).strftime("%b %Y")
            except Exception:
                pass
        rows_html += f"""<tr>
            <td><strong>{v['venue']}</strong><br><span style="font-size:12px;color:#9ca3af;">{v.get('address','')}</span></td>
            <td style="text-align:center;font-weight:700;">{v['visits']}</td>
            <td style="color:#f59e0b;">{stars or '—'}</td>
            <td style="color:#9ca3af;font-size:13px;">{last}</td>
        </tr>"""

    if not rows_html:
        rows_html = '<tr><td colspan="4" style="color:#9ca3af;text-align:center;">No venues yet — mark events as attended to build your history.</td></tr>'

    resp = HTMLResponse(_layout("Venues", f"""
    <h1>My Venues</h1>
    <p style="color:#6b7280;margin-bottom:16px;font-size:14px;">Places you've been to. Ratings feed back into your recommendations.</p>
    <table>
        <thead><tr>
            <th>Venue</th>
            <th>Visits</th>
            <th>Avg Rating</th>
            <th>Last Visit</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
    """, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, response: Response):
    """AI-powered natural language event search."""
    current_user = _get_current_user(request)
    if not current_user:
        return RedirectResponse("/login")
    resp = HTMLResponse(_layout("Search Events", """
    <h1>Search Events</h1>
    <div class="card" style="max-width:680px;">
        <p style="color:#6b7280;margin-bottom:16px;font-size:14px;">
            Ask anything — <em>"jazz this weekend"</em>, <em>"free outdoor stuff"</em>,
            <em>"something to bring a date to"</em>, <em>"nerdy talks near me"</em>
        </p>
        <div style="display:flex;gap:8px;">
            <input id="q" type="text" placeholder="What are you looking for?"
                   style="flex:1;padding:10px 14px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;"
                   onkeydown="if(event.key==='Enter')doSearch(false)">
            <button onclick="doSearch(false)"
                    style="padding:10px 20px;background:#2563eb;color:white;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;">Search</button>
            <button onclick="doSearch(true)" title="Search the web for events not in our database"
                    style="padding:10px 16px;background:#059669;color:white;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;">🌐 Web</button>
        </div>
        <div id="tier-hint" style="margin-top:6px;font-size:11px;color:#9ca3af;"></div>
        <div id="results" style="margin-top:16px;"></div>
    </div>
    <script>
    async function doSearch(webOnly) {
        const q = document.getElementById('q').value.trim();
        if (!q) return;
        const res = document.getElementById('results');
        const hint = document.getElementById('tier-hint');
        res.innerHTML = '<p style="color:#6b7280;">' + (webOnly ? 'Searching the web...' : 'Searching...') + '</p>';
        hint.textContent = '';
        try {
            const r = await fetch('/api/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query: q, web_only: !!webOnly})
            });
            const data = await r.json();
            if (data.error) { res.innerHTML = `<p style="color:#dc2626;">${data.error}</p>`; return; }
            if (!data.results || !data.results.length) {
                res.innerHTML = '<p style="color:#6b7280;">No matches found. Try the 🌐 Web button to search beyond our database.</p>';
                return;
            }
            if (data.tier === 'web') {
                hint.textContent = '🌐 Results from web search — not yet in your recommendations database';
                hint.style.color = '#059669';
            } else {
                hint.textContent = `Found ${data.results.length} matches in your event database`;
            }
            res.innerHTML = data.results.map(e => `
                <div style="padding:14px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:10px;${e.source_tier==='web'?'border-color:#a7f3d0;background:#f0fdf4;':''}">
                    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                        <a href="${e.url || '#'}" target="_blank"
                           style="font-size:15px;font-weight:700;color:#1e293b;text-decoration:none;">${e.title}</a>
                        <div style="display:flex;gap:4px;flex-shrink:0;margin-left:8px;">
                            ${e.source_tier==='web' ? '<span style="background:#dcfce7;color:#166534;font-size:10px;font-weight:700;padding:2px 7px;border-radius:8px;">WEB</span>' : ''}
                            ${e.score ? '<span style="background:#f1f5f9;color:#374151;font-size:11px;font-weight:800;padding:2px 8px;border-radius:8px;">'+e.score+'</span>' : ''}
                        </div>
                    </div>
                    <p style="margin:4px 0 6px;font-size:12px;color:#6b7280;">${e.start_time ? new Date(e.start_time).toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : 'Anytime'} · ${e.location_name || ''}</p>
                    <p style="margin:0;font-size:13px;color:#6d28d9;">${e.reason || ''}</p>
                </div>
            `).join('');
        } catch(err) {
            res.innerHTML = `<p style="color:#dc2626;">Search failed: ${err}</p>`;
        }
    }
    // Auto-focus and allow pre-filled query from URL
    const urlQ = new URLSearchParams(location.search).get('q');
    if (urlQ) { document.getElementById('q').value = urlQ; doSearch(false); }
    else document.getElementById('q').focus();
    </script>
    """, current_user))
    return _maybe_set_cookie(request, resp, current_user)


def _search_db_events(db, run_id: int, query: str, all_runs: bool = False) -> list[dict]:
    """Search kept events via Claude Haiku. all_runs=True searches all historical runs."""
    import anthropic as _anthropic
    settings = Settings()
    if not settings.anthropic_api_key:
        return []
    if all_runs:
        rows = db.conn.execute(
            """SELECT e.*, rk.score, rk.match_reason FROM events e
               JOIN rankings rk ON rk.event_id = e.event_id AND rk.run_id = e.run_id
               WHERE rk.keep = 1 ORDER BY rk.score DESC LIMIT 400""",
        ).fetchall()
    else:
        rows = db.conn.execute(
            """SELECT e.*, rk.score, rk.match_reason FROM events e
               JOIN rankings rk ON rk.event_id = e.event_id AND rk.run_id = e.run_id
               WHERE e.run_id = ? AND rk.keep = 1 ORDER BY rk.score DESC LIMIT 200""",
            (run_id,),
        ).fetchall()
    events = [dict(r) for r in rows]
    if not events:
        return []
    event_list = [
        {
            "id": e.get("event_id", ""),
            "title": e.get("title", ""),
            "description": (e.get("description") or "")[:100],
            "start_time": e.get("start_time"),
            "location_name": e.get("location_name", ""),
            "score": int(e.get("score") or 0),
            "url": e.get("url", ""),
        }
        for e in events
    ]
    prompt = (
        f'User query: "{query}"\n\n'
        f'Here are {len(event_list)} events. Return the top 5 most relevant matches as JSON:\n'
        f'{{"matches": [{{"event_id": "...", "reason": "one-line why this matches"}}]}}\n\n'
        f'Return ONLY valid JSON, no markdown.\n\nEvents:\n{json.dumps(event_list, default=str)}'
    )
    try:
        client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
    except Exception as exc:
        logger.error("Search AI call failed: %s", exc)
        return []
    events_by_id = {e.get("event_id", ""): e for e in events}
    results = []
    for m in data.get("matches", [])[:5]:
        eid = m.get("event_id", "")
        ev = events_by_id.get(eid)
        if not ev:
            continue
        results.append({
            "event_id": eid,
            "title": ev.get("title", ""),
            "start_time": ev.get("start_time"),
            "location_name": ev.get("location_name", ""),
            "url": ev.get("url", ""),
            "score": int(ev.get("score") or 0),
            "reason": m.get("reason", ""),
            "source_tier": "db",
        })
    return results


def _search_web_fallback(query: str, settings: Settings) -> list[dict]:
    """Use Claude with web_search tool to find events not in our DB."""
    import anthropic as _anthropic
    if not settings.anthropic_api_key:
        return []
    try:
        client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
        location = settings.location_query or "Boston, MA"
        system = (
            f"You help find local events in {location}. "
            "When asked about events, use the web_search tool to find real upcoming events. "
            "Return results as JSON only — no prose."
        )
        user_msg = (
            f'Find events matching: "{query}" near {location}. '
            "Search for real upcoming events and return JSON:\n"
            '{"events": [{"title": "...", "date": "...", "venue": "...", "url": "...", "description": "..."}]}\n'
            "Return 3-5 events max. Return ONLY the JSON, no other text."
        )
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
            messages=[{"role": "user", "content": user_msg}],
        )
        # Extract text from response (may have tool use blocks)
        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text = block.text.strip()
                break
        if not text:
            return []
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
        results = []
        for ev in data.get("events", [])[:5]:
            results.append({
                "event_id": f"web_{hash(ev.get('title','') + ev.get('url','')) % 10**8:08x}",
                "title": ev.get("title", ""),
                "start_time": ev.get("date"),
                "location_name": ev.get("venue", ""),
                "url": ev.get("url", ""),
                "score": 0,
                "reason": ev.get("description", "")[:120],
                "source_tier": "web",
            })
        return results
    except Exception as exc:
        logger.warning("Web search fallback failed: %s", exc)
        return []


@app.post("/api/search")
async def api_search(request: Request):
    """Tiered AI event search: DB kept events → DB all events → web fallback."""
    current_user = _get_current_user(request)
    if not current_user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    try:
        body = await request.json()
        query = (body.get("query") or "").strip()
        web_only = bool(body.get("web_only", False))
        if not query:
            return JSONResponse({"error": "Empty query"})
    except Exception:
        return JSONResponse({"error": "Invalid request"}, status_code=400)

    db = get_db()
    settings = Settings()
    run = db.get_user_latest_run(current_user["id"])

    if not settings.anthropic_api_key:
        return JSONResponse({"error": "No API key configured"}, status_code=500)

    if web_only:
        # Direct web search (user explicitly requested)
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        results = await loop.run_in_executor(None, _search_web_fallback, query, settings)
        return JSONResponse({"results": results, "tier": "web"})

    # Tier 1: search kept events from latest run
    results: list[dict] = []
    if run:
        results = await _asyncio_run_in_executor(_search_db_events, db, run["id"], query, False)

    # Tier 2: expand to all events if < 3 results
    if len(results) < 3 and run:
        logger.info("Search tier 2: expanding to all historical events for %r", query)
        all_results = await _asyncio_run_in_executor(_search_db_events, db, run["id"], query, True)
        # Merge: prefer existing, add new ones
        existing_ids = {r["event_id"] for r in results}
        for r in all_results:
            if r["event_id"] not in existing_ids:
                results.append(r)
        results = results[:5]

    # Tier 3: web fallback if still < 3 results
    if len(results) < 3:
        logger.info("Search tier 3: web fallback for %r", query)
        web_results = await _asyncio_run_in_executor(_search_web_fallback, query, settings)
        existing_ids = {r["event_id"] for r in results}
        for r in web_results:
            if r["event_id"] not in existing_ids:
                results.append(r)

    tier = "web" if results and results[-1].get("source_tier") == "web" else "db"
    return JSONResponse({"results": results[:5], "tier": tier})


async def _asyncio_run_in_executor(fn, *args):
    """Run a sync function in a thread pool executor."""
    import asyncio as _asyncio
    loop = _asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


@app.get("/group/create", response_class=HTMLResponse)
async def group_create_page(request: Request):
    user = _get_current_user(request)
    if not user:
        return HTMLResponse(_layout("Create Group", """
        <h1>Create a Group</h1>
        <div class="card" style="max-width:400px;">
            <p style="color:#6b7280;">Sign in to create a group.</p>
            <div style="margin-top:12px;">
                <a href="/login" style="padding:8px 20px;background:#2563eb;color:white;border-radius:6px;
                   font-size:14px;text-decoration:none;font-weight:600;">Sign in</a>
                <a href="/join" style="margin-left:12px;font-size:14px;">or join</a>
            </div>
        </div>
        """))

    return HTMLResponse(_layout("Create Group", f"""
    <h1>Create a Group</h1>
    <div class="card">
        <form action="/group/create" method="post" style="display:flex;flex-direction:column;gap:12px;max-width:400px;">
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
    """, user=user))


@app.post("/group/create")
async def group_create_submit(request: Request):
    user = _get_current_user(request)
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    form = await request.form()
    name = form.get("name", "").strip()
    slug = form.get("slug", "").strip().lower()
    if not name or not slug:
        return HTMLResponse("<h1>Name and slug required</h1>", status_code=400)
    db = get_db()
    group_id = db.create_group(name, slug, user["id"])
    db.add_group_member(group_id, user["id"])
    return RedirectResponse(f"/group/{slug}", status_code=303)


@app.post("/group/{slug}/invite")
async def group_invite(slug: str, request: Request):
    user = _get_current_user(request)
    db = get_db()
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
    settings = Settings()
    if not invited:
        # Auto-create user and send invite email
        user_id = db.create_user(email)
        invited = db.get_user(user_id)
        db.add_group_member(group["id"], invited["id"])
        inviter_name = user.get("name") or user.get("email", "")
        try:
            send_invite_email(
                email, invited["user_token"], group["name"], inviter_name,
                slug, settings.dashboard_url, settings,
            )
        except Exception:
            logger.exception("Failed to send invite email to %s", email)
    else:
        db.add_group_member(group["id"], invited["id"])
        inviter_name = user.get("name") or user.get("email", "")
        try:
            send_invite_email(
                email, invited["user_token"], group["name"], inviter_name,
                slug, settings.dashboard_url, settings,
            )
        except Exception:
            logger.exception("Failed to send invite email to %s", email)
    return RedirectResponse(f"/group/{slug}", status_code=303)


@app.get("/group/{slug}", response_class=HTMLResponse)
async def group_calendar(slug: str, request: Request):
    db = get_db()
    current_user = _get_current_user(request)
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
            <form action="/group/{slug}/invite" method="post" style="display:flex;gap:8px;align-items:end;">
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
    const USER_TOKEN = '""" + (current_user.get("user_token", "") if current_user else "") + """';
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

    return HTMLResponse(LAYOUT_STYLE.replace("__TITLE__", group["name"]) + render_nav(current_user) + '<div class="app-content">' + rsvp_css + f"""
    <h1>{group["name"]}</h1>
    {user_banner}
    <p style="color:#6b7280;margin-bottom:12px;">Members: {members_html}
        &middot; <a href="{ical_link}">Subscribe to iCal</a>
        &middot; <a href="/group/create">Create new group</a>
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
    settings = Settings()
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

        lines.extend([
            "BEGIN:VEVENT",
            _fold_line(f"UID:{uid}"),
            f"DTSTART:{dtstart}",
            _fold_line(f"SUMMARY:[{score}] {title}"),
            _fold_line(f"LOCATION:{location}"),
            _fold_line(f"URL:{url}"),
            _fold_line(f"DESCRIPTION:{price}\\nScore: {score}/100{dist_str}\\n{reason}"),
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

    return HTMLResponse(LAYOUT_STYLE.replace("__TITLE__", "Join Recom") + render_nav(None) + f"""
    <div class="app-content" style="max-width:560px;">

    {success_banner}

    <div style="text-align:center;padding:32px 0 24px;">
      <div style="font-size:13px;font-weight:700;letter-spacing:2px;color:#818cf8;text-transform:uppercase;margin-bottom:12px;">◉ RECOM</div>
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
<title>Set up your taste — recom</title>
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
.btn-primary {{ display: block; width: 100%; padding: 14px; background: linear-gradient(135deg, #4f46e5, #7c3aed); color: white; border: none; border-radius: 14px; font-size: 16px; font-weight: 700; cursor: pointer; font-family: inherit; text-align: center; text-decoration: none; transition: transform .15s, box-shadow .15s; box-shadow: 0 4px 20px rgba(79,70,229,.4); }}
.btn-primary:hover {{ transform: translateY(-1px); box-shadow: 0 6px 28px rgba(79,70,229,.5); }}
.btn-secondary {{ display: block; width: 100%; padding: 12px; background: transparent; color: #6b7280; border: 1px solid #374151; border-radius: 14px; font-size: 14px; font-weight: 600; cursor: pointer; font-family: inherit; text-align: center; margin-top: 10px; transition: all .15s; text-decoration: none; }}
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
.top-3 {{ background: #1e1e3a; border-radius: 14px; padding: 16px; margin-bottom: 20px; border: 1px solid #2d2d5e; }}
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
<div class="logo">◉ recom</div>

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
    <a href="/taste" class="btn-secondary">Keep ranking (fine-tune your taste)</a>
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


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_layout("Login", """
    <h1>Sign In</h1>
    <div class="card" style="max-width:400px;">
        <p style="color:#6b7280;margin-bottom:16px;">Enter your email and we'll send you a link to your events.</p>
        <form action="/api/login" method="post" style="display:flex;flex-direction:column;gap:12px;">
            <input name="email" type="email" placeholder="you@gmail.com" required
                   style="padding:10px 14px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:15px;">
            <button type="submit" style="padding:10px 20px;background:#2563eb;color:white;border:none;
                    border-radius:8px;font-size:15px;cursor:pointer;font-weight:600;">Send me my link</button>
        </form>
        <p style="margin-top:16px;font-size:13px;color:#9ca3af;">New here? <a href="/join">Join Recom</a></p>
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

    cards_html = ""
    for g in groups:
        is_member = db.is_group_member(g["id"], current_user["id"]) if current_user else False
        action = f'<a href="/group/{g["slug"]}" style="color:#2563eb;font-size:13px;">View</a>'
        if current_user and not is_member:
            action = f'''<form action="/group/{g["slug"]}/join" method="post" style="display:inline;">
                <button type="submit" style="padding:4px 14px;background:#2563eb;color:white;border:none;
                        border-radius:6px;font-size:13px;cursor:pointer;">Join</button></form>'''
        cards_html += f"""<div class="card" style="display:flex;justify-content:space-between;align-items:center;">
            <div>
                <strong>{g["name"]}</strong>
                <span style="color:#9ca3af;font-size:13px;margin-left:8px;">{g["member_count"]} member{"s" if g["member_count"] != 1 else ""}</span>
            </div>
            {action}
        </div>"""

    create_btn = ""
    if current_user:
        create_btn = f'<a href="/group/create" style="display:inline-block;padding:8px 20px;background:#2563eb;color:white;border-radius:8px;font-weight:600;font-size:14px;text-decoration:none;margin-bottom:16px;">Create Group</a>'

    return HTMLResponse(_layout("Groups", f"""
    <h1>Groups</h1>
    {create_btn}
    {cards_html if cards_html else '<div class="card"><p style="color:#9ca3af;">No groups yet.</p></div>'}
    """, user=current_user))


@app.post("/group/{slug}/join")
async def group_join(slug: str, request: Request):
    user = _get_current_user(request)
    db = get_db()
    if not user:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    group = db.get_group(slug)
    if not group:
        return HTMLResponse("<h1>Group not found</h1>", status_code=404)
    db.add_group_member(group["id"], user["id"])
    return RedirectResponse(f"/group/{slug}", status_code=303)


@app.get("/u/{token}/feed.ics")
async def user_ical_feed(token: str, min_score: int = 55):
    """Per-user shareable iCal feed. The token in the URL IS the auth."""
    db = get_db()
    user = db.get_user_by_token(token)
    if not user:
        return Response(content="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR",
                       media_type="text/calendar")

    run = db.get_user_latest_run(user["id"])
    if not run:
        return Response(content="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR",
                       media_type="text/calendar")

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
            chunk = encoded[:75]
            # Don't split a multi-byte char
            while len(chunk) > 0 and (chunk[-1] & 0xC0) == 0x80:
                chunk = chunk[:-1]
            chunks.append(chunk.decode("utf-8"))
            encoded = encoded[len(chunk):]
        chunks.append(encoded.decode("utf-8"))
        return "\r\n ".join(chunks)

    user_name = user.get("name") or user.get("email", "")
    settings = Settings()
    dashboard_url = settings.dashboard_url

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//recom//User {_ical_escape(user_name)}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Recom — {_ical_escape(user_name)}'s Picks",
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

        # Build RSVP links for the description
        enc_title = _urlparse.quote_plus(raw_title)
        rsvp_going = f"{dashboard_url}/api/rsvp-link?event_id={eid}&status=going&u={token}&title={enc_title}"
        rsvp_maybe = f"{dashboard_url}/api/rsvp-link?event_id={eid}&status=maybe&u={token}&title={enc_title}"
        desc = f"{price}\\nScore: {score}/100\\n{reason}\\n\\nRSVP Going: {rsvp_going}\\nRSVP Maybe: {rsvp_maybe}"

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART:{dtstart}",
            f"SUMMARY:[{score}] {title}",
            f"LOCATION:{location}",
            f"URL:{url}",
            f"DESCRIPTION:{desc}",
            "DURATION:PT2H",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar",
        headers={"Content-Disposition": f"inline; filename=recom-{token}.ics"},
    )


@app.get("/u/{token}/rsvps.ics")
async def user_rsvps_ical(token: str):
    """Per-user iCal feed containing only events the user RSVP'd 'going' or 'maybe'."""
    import html as _html

    db = get_db()
    user = db.get_user_by_token(token)
    if not user:
        return Response(content="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR",
                       media_type="text/calendar")

    user_id = user["id"]
    user_name = user.get("name") or user.get("email", "")

    # Get all RSVPs for this user with event details
    rows = db.conn.execute(
        """SELECT r.status, r.event_id, e.title, e.start_time, e.location_name,
                  e.url, e.price, e.description
           FROM rsvps r
           JOIN events e ON e.event_id = r.event_id
           WHERE r.user_id = ? AND r.status IN ('going', 'maybe')
           ORDER BY e.start_time ASC LIMIT 100""",
        (user_id,),
    ).fetchall()
    rsvps = [dict(r) for r in rows]

    def _ical_escape(text: str) -> str:
        text = _html.unescape(str(text))
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//recom//RSVPs {_ical_escape(user_name)}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Recom — {_ical_escape(user_name)}'s Confirmed Plans",
        "X-APPLE-CALENDAR-COLOR:#22c55e",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]

    for ev in rsvps:
        start = ev.get("start_time")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start)
        except (ValueError, TypeError):
            continue
        dtstart = dt.strftime("%Y%m%dT%H%M%S")
        status = ev.get("status", "going")
        title = _ical_escape(ev.get("title") or "")
        location = _ical_escape(ev.get("location_name") or "")
        url = ev.get("url") or ""
        price = _ical_escape(ev.get("price") or "Free")
        eid = ev.get("event_id", "")
        status_label = "✓ Going" if status == "going" else "? Maybe"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{eid}@rsvps-{token}",
            f"DTSTART:{dtstart}",
            f"SUMMARY:{status_label}: {title}",
            f"LOCATION:{location}",
            f"URL:{url}",
            f"DESCRIPTION:{price}",
            "DURATION:PT2H",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar",
        headers={"Content-Disposition": f"inline; filename=recom-rsvps-{token}.ics"},
    )


@app.get("/u/{token}/cal", response_class=HTMLResponse)
async def user_cal_page(token: str, request: Request):
    """Page showing all available calendar feed URLs for the user."""
    db = get_db()
    user = db.get_user_by_token(token)
    if not user:
        return HTMLResponse("<h1>User not found</h1>", status_code=404)
    settings = Settings()
    dashboard_url = settings.dashboard_url
    feed_url = f"{dashboard_url}/u/{token}/feed.ics"
    rsvps_url = f"{dashboard_url}/u/{token}/rsvps.ics"
    webcal_feed = feed_url.replace("https://", "webcal://").replace("http://", "webcal://")
    webcal_rsvps = rsvps_url.replace("https://", "webcal://").replace("http://", "webcal://")
    user_name = user.get("name") or user.get("email", "")

    def _feed_row(label: str, url: str, webcal: str, desc: str, color: str = "#4f46e5") -> str:
        safe_url = url.replace("'", "\\'")
        return f"""
        <div style="padding:14px 0;border-bottom:1px solid #e5e7eb;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
                <div>
                    <div style="font-weight:700;font-size:15px;color:{color};">{label}</div>
                    <div style="font-size:12px;color:#6b7280;margin-top:2px;">{desc}</div>
                </div>
                <div style="display:flex;gap:6px;flex-shrink:0;">
                    <a href="{webcal}" style="padding:5px 12px;background:#f3f4f6;color:#374151;border-radius:6px;font-size:12px;font-weight:600;text-decoration:none;">+ Calendar</a>
                    <button onclick="navigator.clipboard.writeText('{safe_url}');this.textContent='Copied!';setTimeout(()=>this.textContent='Copy URL',1500)"
                            style="padding:5px 12px;background:#e0e7ff;color:#3730a3;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;">Copy URL</button>
                </div>
            </div>
        </div>"""

    return HTMLResponse(_layout("Your Calendars", f"""
    <h1>Your Calendars</h1>
    <p style="color:#6b7280;margin-bottom:20px;font-size:14px;">
        Subscribe to any of these feeds in Google Calendar, Apple Calendar, or Outlook.
        They update automatically.
    </p>
    <div class="card" style="max-width:640px;">
        {_feed_row("My Recommendations", feed_url, webcal_feed, "All events scored ≥55 from your latest run", "#4f46e5")}
        {_feed_row("My Confirmed Plans", rsvps_url, webcal_rsvps, "Only events you RSVP'd going or maybe", "#16a34a")}
    </div>
    <div class="card" style="max-width:640px;margin-top:16px;">
        <p style="font-size:13px;color:#6b7280;margin-bottom:8px;"><strong>How to subscribe:</strong></p>
        <ul style="font-size:13px;color:#6b7280;margin:0;padding-left:20px;line-height:1.8;">
            <li><strong>Google Calendar:</strong> Other calendars → + → From URL → paste URL</li>
            <li><strong>Apple Calendar:</strong> File → New Calendar Subscription → paste URL</li>
            <li><strong>Outlook:</strong> Add calendar → From internet → paste URL</li>
        </ul>
    </div>
    """, user=user))


def run():
    import uvicorn
    settings = Settings()
    logger.info(f"Starting dashboard at http://{settings.dashboard_host}:{settings.dashboard_port}")
    uvicorn.run(app, host=settings.dashboard_host, port=settings.dashboard_port)


# ---------------------------------------------------------------------------
# UI Variant Routes — /variants index + 12 variant pages
# ---------------------------------------------------------------------------

def _get_variant_events() -> list[dict]:
    """Return top 10 upcoming kept events from the latest run."""
    db = get_db()
    runs = db.get_runs()
    if not runs:
        return []
    run_id = runs[0]["id"]
    events = db.get_run_events(run_id)
    now_str = datetime.now().isoformat()
    kept = [e for e in events if e.get("keep") and e.get("start_time")]
    kept.sort(key=lambda e: e.get("start_time") or "")
    upcoming = [e for e in kept if (e.get("start_time") or "") >= now_str]
    return upcoming[:10] or kept[:10]


def _fmt_event_dt(start_time: str | None) -> str:
    if not start_time:
        return ""
    try:
        dt = datetime.fromisoformat(start_time)
        return dt.strftime("%a %b %-d, %-I:%M %p")
    except Exception:
        return start_time[:16]


@app.get("/variants", response_class=HTMLResponse)
async def variants_index(request: Request):
    """Index page listing all 12 UI variants."""
    db = get_db()
    current_user = _get_current_user(request)
    body = """
<style>
.variants-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }
.variant-card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.08); border-left: 4px solid #818cf8; }
.variant-card h3 { font-size: 15px; font-weight: 700; color: #1a1a1a; margin-bottom: 4px; }
.variant-card .tag { display: inline-block; font-size: 11px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; padding: 2px 8px; border-radius: 8px; margin-bottom: 8px; }
.tag-dense { background: #f3f4f6; color: #374151; }
.tag-magazine { background: #fdf2f8; color: #9d174d; }
.tag-app { background: #0f0f1a; color: #818cf8; }
.variant-card p { font-size: 12px; color: #6b7280; margin-bottom: 12px; }
.variant-card a { display: inline-block; font-size: 13px; font-weight: 600; color: #4f46e5; text-decoration: none; }
.variant-card a:hover { text-decoration: underline; }
.section-label { font-size: 11px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: #818cf8; margin: 28px 0 8px; }
</style>
<h1>UI Variants</h1>
<p style="color:#6b7280;margin-bottom:8px;">13 experimental layouts for 4 main pages. Each shows real data.</p>

<div class="section-label">Calendar / Events</div>
<div class="variants-grid">
  <div class="variant-card" style="border-left-color:#374151;">
    <span class="tag tag-dense">Dense</span>
    <h3>Calendar — Dense</h3>
    <p>Compact table-style list, 12px text, max info density, muted colors.</p>
    <a href="/v/calendar/dense">View variant &rarr;</a>
  </div>
  <div class="variant-card" style="border-left-color:#ec4899;">
    <span class="tag tag-magazine">Magazine</span>
    <h3>Calendar — Magazine</h3>
    <p>Large hero card, bold typography, generous whitespace, accent colors.</p>
    <a href="/v/calendar/magazine">View variant &rarr;</a>
  </div>
  <div class="variant-card" style="border-left-color:#818cf8;">
    <span class="tag tag-app">App</span>
    <h3>Calendar — App</h3>
    <p>Dark theme, rounded cards, sticky bottom nav, iOS/Android native feel.</p>
    <a href="/v/calendar/app">View variant &rarr;</a>
  </div>
</div>

<div class="section-label">Taste</div>
<div class="variants-grid">
  <div class="variant-card" style="border-left-color:#374151;">
    <span class="tag tag-dense">Dense</span>
    <h3>Taste — Dense</h3>
    <p>Compact ranked list, small text, table-like layout, muted palette.</p>
    <a href="/v/taste/dense">View variant &rarr;</a>
  </div>
  <div class="variant-card" style="border-left-color:#ec4899;">
    <span class="tag tag-magazine">Magazine</span>
    <h3>Taste — Magazine</h3>
    <p>Big hero section, large category labels, magazine-style ranking spread.</p>
    <a href="/v/taste/magazine">View variant &rarr;</a>
  </div>
  <div class="variant-card" style="border-left-color:#818cf8;">
    <span class="tag tag-app">App</span>
    <h3>Taste — App</h3>
    <p>Dark theme, swipeable-style cards, large touch targets, native feel.</p>
    <a href="/v/taste/app">View variant &rarr;</a>
  </div>
</div>

<div class="section-label">Groups</div>
<div class="variants-grid">
  <div class="variant-card" style="border-left-color:#374151;">
    <span class="tag tag-dense">Dense</span>
    <h3>Groups — Dense</h3>
    <p>Compact table of all groups with member counts and quick-action links.</p>
    <a href="/v/groups/dense">View variant &rarr;</a>
  </div>
  <div class="variant-card" style="border-left-color:#ec4899;">
    <span class="tag tag-magazine">Magazine</span>
    <h3>Groups — Magazine</h3>
    <p>Large group cards, hero layout, bold accent colors, invite-focused.</p>
    <a href="/v/groups/magazine">View variant &rarr;</a>
  </div>
  <div class="variant-card" style="border-left-color:#818cf8;">
    <span class="tag tag-app">App</span>
    <h3>Groups — App</h3>
    <p>Dark theme, list-style group tiles, bottom nav, app-native aesthetic.</p>
    <a href="/v/groups/app">View variant &rarr;</a>
  </div>
</div>

<div class="section-label">Profile</div>
<div class="variants-grid">
  <div class="variant-card" style="border-left-color:#374151;">
    <span class="tag tag-dense">Dense</span>
    <h3>Profile — Dense</h3>
    <p>Compact info grid, small labels, tabular account details, muted tones.</p>
    <a href="/v/profile/dense">View variant &rarr;</a>
  </div>
  <div class="variant-card" style="border-left-color:#ec4899;">
    <span class="tag tag-magazine">Magazine</span>
    <h3>Profile — Magazine</h3>
    <p>Full-bleed avatar header, big name display, magazine-spread settings.</p>
    <a href="/v/profile/magazine">View variant &rarr;</a>
  </div>
  <div class="variant-card" style="border-left-color:#818cf8;">
    <span class="tag tag-app">App</span>
    <h3>Profile — App</h3>
    <p>Dark settings-screen style, section groups, iOS toggle aesthetics.</p>
    <a href="/v/profile/app">View variant &rarr;</a>
  </div>
</div>
"""
    resp = HTMLResponse(_layout("UI Variants", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


# ── Calendar variants ────────────────────────────────────────────────────────

@app.get("/v/calendar/dense", response_class=HTMLResponse)
async def v_calendar_dense(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    events = _get_variant_events()

    rows = ""
    for e in events:
        score = int(e.get("score") or 0)
        score_color = "#166534" if score >= 70 else "#92400e" if score >= 40 else "#6b7280"
        rows += f"""<tr>
          <td style="max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
            <a href="{e.get('url','#')}" target="_blank" style="color:#1e40af;font-size:12px;font-weight:600;">{e.get('title','')[:60]}</a>
          </td>
          <td style="font-size:12px;color:#374151;white-space:nowrap;">{_fmt_event_dt(e.get('start_time'))}</td>
          <td style="font-size:12px;color:#374151;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{e.get('location_name','')[:30]}</td>
          <td style="font-size:12px;color:#374151;">{e.get('price') or 'Free'}</td>
          <td style="font-size:12px;font-weight:700;color:{score_color};">{score}</td>
          <td style="font-size:11px;color:#9ca3af;text-transform:capitalize;">{e.get('vibe','')}</td>
        </tr>"""

    body = f"""
<style>
body {{ background: #f8fafc; }}
.d-wrap {{ max-width: 900px; margin: 0 auto; padding: 12px 16px 40px; }}
.d-header {{ display: flex; align-items: baseline; gap: 12px; margin-bottom: 10px; }}
.d-header h1 {{ font-size: 15px; font-weight: 700; color: #374151; }}
.d-header .d-sub {{ font-size: 12px; color: #9ca3af; }}
.d-table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 6px;
            box-shadow: 0 1px 2px rgba(0,0,0,.06); font-size: 12px; }}
.d-table th {{ background: #f8fafc; color: #9ca3af; font-size: 10px; font-weight: 700;
               text-transform: uppercase; letter-spacing: .6px; padding: 6px 10px;
               border-bottom: 1px solid #e5e7eb; text-align: left; }}
.d-table td {{ padding: 6px 10px; border-bottom: 1px solid #f3f4f6; color: #374151; }}
.d-table tr:last-child td {{ border-bottom: none; }}
.d-table tr:hover td {{ background: #f9fafb; }}
.d-back {{ font-size: 12px; color: #6b7280; display: inline-block; margin-bottom: 8px; }}
</style>
<div class="d-wrap">
  <a href="/variants" class="d-back">&larr; All variants</a>
  <div class="d-header">
    <h1>Upcoming Events</h1>
    <span class="d-sub">{len(events)} picks &middot; Dense view</span>
  </div>
  <table class="d-table">
    <thead><tr>
      <th>Event</th><th>When</th><th>Location</th><th>Price</th><th>Score</th><th>Vibe</th>
    </tr></thead>
    <tbody>{rows if rows else '<tr><td colspan="6" style="color:#9ca3af;text-align:center;padding:20px;">No events yet — run the pipeline first.</td></tr>'}</tbody>
  </table>
</div>"""
    resp = HTMLResponse(_layout("Calendar — Dense", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/v/calendar/magazine", response_class=HTMLResponse)
async def v_calendar_magazine(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    events = _get_variant_events()

    hero_html = ""
    rest_html = ""
    if events:
        h = events[0]
        score = int(h.get("score") or 0)
        hero_html = f"""
<div style="background:linear-gradient(135deg,#4f46e5,#ec4899);border-radius:20px;padding:40px;margin-bottom:32px;color:white;position:relative;overflow:hidden;">
  <div style="position:absolute;top:-60px;right:-60px;width:200px;height:200px;background:rgba(255,255,255,.06);border-radius:50%;"></div>
  <p style="font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;opacity:.7;margin-bottom:12px;">TOP PICK THIS WEEK</p>
  <h2 style="font-size:2.2rem;font-weight:900;line-height:1.1;margin-bottom:14px;max-width:560px;">{h.get('title','')}</h2>
  <p style="font-size:15px;opacity:.85;margin-bottom:20px;">{_fmt_event_dt(h.get('start_time'))} &middot; {h.get('location_name','')[:40]}</p>
  <div style="display:flex;align-items:center;gap:16px;">
    <a href="{h.get('url','#')}" target="_blank" style="display:inline-block;background:white;color:#4f46e5;font-weight:800;font-size:14px;padding:10px 24px;border-radius:50px;text-decoration:none;">Get tickets &rarr;</a>
    <span style="font-size:28px;font-weight:900;opacity:.9;">{score}</span>
    <span style="font-size:13px;opacity:.7;">/ 100</span>
  </div>
</div>"""
        for e in events[1:]:
            sc = int(e.get("score") or 0)
            rest_html += f"""
<div style="background:white;border-radius:16px;padding:24px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.06);display:flex;align-items:flex-start;gap:20px;">
  <div style="flex-shrink:0;width:56px;height:56px;border-radius:14px;background:linear-gradient(135deg,#4f46e5,#ec4899);display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:900;color:white;">{sc}</div>
  <div style="flex:1;min-width:0;">
    <p style="font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#ec4899;margin-bottom:4px;">{e.get('vibe','').upper()}</p>
    <h3 style="font-size:1.15rem;font-weight:800;color:#1e1b4b;margin-bottom:6px;line-height:1.25;">{e.get('title','')[:80]}</h3>
    <p style="font-size:13px;color:#6b7280;">{_fmt_event_dt(e.get('start_time'))} &middot; {e.get('location_name','')[:40]} &middot; {e.get('price') or 'Free'}</p>
    {f'<p style="font-size:13px;color:#7c3aed;margin-top:6px;line-height:1.4;">{e.get("match_reason","")[:120]}</p>' if e.get("match_reason") else ''}
  </div>
  <a href="{e.get('url','#')}" target="_blank" style="flex-shrink:0;font-size:13px;font-weight:700;color:#4f46e5;text-decoration:none;white-space:nowrap;">View &rarr;</a>
</div>"""

    body = f"""
<style>body {{ background: #fdf2f8; }}</style>
<div style="max-width:680px;margin:0 auto;padding:16px 16px 60px;">
  <a href="/variants" style="font-size:12px;color:#9ca3af;">&larr; All variants</a>
  <div style="text-align:center;padding:32px 0 24px;">
    <p style="font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:#4f46e5;margin-bottom:8px;">YOUR WEEK IN BOSTON</p>
    <h1 style="font-size:2.8rem;font-weight:900;color:#1e1b4b;letter-spacing:-1.5px;line-height:1.05;">What's On</h1>
  </div>
  {hero_html}
  {rest_html if rest_html else '<p style="color:#9ca3af;text-align:center;">No events yet — run the pipeline first.</p>'}
</div>"""
    resp = HTMLResponse(_layout("Calendar — Magazine", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/v/calendar/app", response_class=HTMLResponse)
async def v_calendar_app(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    events = _get_variant_events()

    cards = ""
    for e in events:
        score = int(e.get("score") or 0)
        vibe_color = {"social": "#f59e0b", "intellectual": "#818cf8", "mixed": "#34d399"}.get(e.get("vibe", "mixed"), "#34d399")
        cards += f"""
<a href="{e.get('url','#')}" target="_blank" style="text-decoration:none;display:block;background:#1e1e3a;border-radius:20px;padding:18px 20px;margin-bottom:12px;border:1px solid #2d2d5e;transition:border-color .15s;">
  <div style="display:flex;align-items:flex-start;gap:14px;">
    <div style="flex-shrink:0;width:48px;height:48px;border-radius:14px;background:rgba(129,140,248,.15);display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:900;color:#818cf8;">{score}</div>
    <div style="flex:1;min-width:0;">
      <div style="font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:{vibe_color};margin-bottom:3px;">{e.get('vibe','').upper()}</div>
      <div style="font-size:15px;font-weight:700;color:#e2e8f0;line-height:1.25;margin-bottom:5px;">{e.get('title','')[:70]}</div>
      <div style="font-size:12px;color:#6b7280;">{_fmt_event_dt(e.get('start_time'))} &middot; {e.get('location_name','')[:35]}</div>
      {f'<div style="font-size:12px;color:#818cf8;margin-top:4px;">{e.get("price") or "Free"}</div>' if e.get("price") else ''}
    </div>
    <div style="color:#4b5563;font-size:16px;flex-shrink:0;">&rsaquo;</div>
  </div>
</a>"""

    body = f"""
<style>
html, body {{ background: #0f0f1a !important; color: #e2e8f0; }}
.app-content {{ background: #0f0f1a !important; }}
</style>
<div style="max-width:480px;margin:0 auto;padding:8px 16px 100px;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
    <div>
      <a href="/variants" style="font-size:11px;color:#4b5563;">&larr; variants</a>
      <h1 style="font-size:22px;font-weight:800;color:#e2e8f0;margin-top:4px;">This Week</h1>
    </div>
    <div style="font-size:12px;color:#6b7280;">{len(events)} picks</div>
  </div>
  {cards if cards else '<p style="color:#4b5563;text-align:center;padding:40px 0;">No events yet.</p>'}
</div>
<nav style="position:fixed;bottom:0;left:0;right:0;background:#1e1e3a;border-top:1px solid #2d2d5e;display:flex;justify-content:space-around;padding:12px 0 20px;z-index:100;">
  <a href="/v/calendar/app" style="text-align:center;color:#818cf8;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">📅</div>Events</a>
  <a href="/v/taste/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">🏆</div>Taste</a>
  <a href="/v/groups/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">👥</div>Groups</a>
  <a href="/v/profile/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">👤</div>Profile</a>
</nav>"""
    resp = HTMLResponse(_layout("Calendar — App", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


# ── Taste variants ────────────────────────────────────────────────────────────

@app.get("/v/taste/dense", response_class=HTMLResponse)
async def v_taste_dense(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    db.seed_taste_items(user_id)
    items = db.get_taste_items(user_id)
    pair = db.get_taste_matchup_pair(user_id)

    min_elo = min((i["elo_rating"] for i in items), default=1000)
    max_elo = max((i["elo_rating"] for i in items), default=1000)
    rng = max_elo - min_elo or 1

    pair_html = ""
    if pair:
        a, b = pair[0], pair[1]
        pair_html = f"""
<div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:6px;padding:10px 12px;margin-bottom:10px;display:flex;align-items:center;gap:8px;font-size:12px;color:#374151;">
  <span style="color:#9ca3af;font-weight:600;">Next:</span>
  <strong>{a['label']}</strong>
  <span style="color:#9ca3af;">vs</span>
  <strong>{b['label']}</strong>
  <a href="/taste" style="margin-left:auto;font-size:11px;font-weight:700;color:#4f46e5;">Rank &rarr;</a>
</div>"""

    rows = ""
    for rank, item in enumerate(items, 1):
        pct = round((item["elo_rating"] - min_elo) / rng * 100)
        rows += f"""<tr>
          <td style="font-size:12px;font-weight:700;color:#9ca3af;width:28px;">#{rank}</td>
          <td style="font-size:12px;font-weight:600;color:#374151;">{item['label']}</td>
          <td style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.5px;">{item['category']}</td>
          <td>
            <div style="display:flex;align-items:center;gap:6px;">
              <div style="width:80px;height:4px;background:#f3f4f6;border-radius:2px;overflow:hidden;">
                <div style="height:100%;width:{pct}%;background:#818cf8;border-radius:2px;"></div>
              </div>
              <span style="font-size:11px;font-weight:700;color:#818cf8;">{round(item['elo_rating'])}</span>
            </div>
          </td>
        </tr>"""

    body = f"""
<style>body {{ background: #f8fafc; }}</style>
<div style="max-width:600px;margin:0 auto;padding:12px 16px 40px;">
  <a href="/variants" style="font-size:12px;color:#6b7280;">&larr; All variants</a>
  <div style="display:flex;align-items:baseline;gap:10px;margin:8px 0 10px;">
    <h1 style="font-size:15px;font-weight:700;color:#374151;">Taste Stack</h1>
    <span style="font-size:12px;color:#9ca3af;">{len(items)} activities &middot; Dense view</span>
  </div>
  {pair_html}
  <table style="width:100%;border-collapse:collapse;background:white;border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.06);">
    <thead><tr>
      <th style="background:#f8fafc;color:#9ca3af;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:left;">#</th>
      <th style="background:#f8fafc;color:#9ca3af;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:left;">Activity</th>
      <th style="background:#f8fafc;color:#9ca3af;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:left;">Category</th>
      <th style="background:#f8fafc;color:#9ca3af;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:left;">Elo</th>
    </tr></thead>
    <tbody>{rows if rows else '<tr><td colspan="4" style="color:#9ca3af;text-align:center;padding:20px;font-size:12px;">No items yet — visit /taste to add some.</td></tr>'}</tbody>
  </table>
</div>"""
    resp = HTMLResponse(_layout("Taste — Dense", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/v/taste/magazine", response_class=HTMLResponse)
async def v_taste_magazine(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    db.seed_taste_items(user_id)
    items = db.get_taste_items(user_id)
    pair = db.get_taste_matchup_pair(user_id)
    matchup_count = db.get_taste_matchup_count(user_id)

    min_elo = min((i["elo_rating"] for i in items), default=1000)
    max_elo = max((i["elo_rating"] for i in items), default=1000)
    rng = max_elo - min_elo or 1

    CAT_COLORS = {"music": "#f59e0b", "social": "#3b82f6", "arts": "#ec4899",
                  "intellectual": "#8b5cf6", "active": "#22c55e", "food": "#f97316",
                  "maker": "#06b6d4", "general": "#6b7280"}

    top3 = items[:3]
    top3_html = ""
    for rank, item in enumerate(top3, 1):
        pct = round((item["elo_rating"] - min_elo) / rng * 100)
        col = CAT_COLORS.get(item["category"], "#6b7280")
        size = ["2rem", "1.5rem", "1.25rem"][rank - 1]
        top3_html += f"""
<div style="margin-bottom:24px;padding-bottom:24px;{'border-bottom:1px solid rgba(255,255,255,.1);' if rank < 3 else ''}">
  <div style="font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{col};margin-bottom:6px;">#{rank} {item['category'].upper()}</div>
  <h3 style="font-size:{size};font-weight:900;color:white;margin-bottom:10px;line-height:1.1;">{item['label']}</h3>
  <div style="background:rgba(255,255,255,.15);border-radius:4px;height:6px;overflow:hidden;max-width:300px;">
    <div style="height:100%;width:{pct}%;background:{col};border-radius:4px;"></div>
  </div>
  <div style="font-size:12px;color:rgba(255,255,255,.5);margin-top:4px;">{round(item['elo_rating'])} Elo &middot; {pct}th percentile</div>
</div>"""

    rest_html = ""
    for rank, item in enumerate(items[3:], 4):
        col = CAT_COLORS.get(item["category"], "#6b7280")
        rest_html += f"""
<div style="background:white;border-radius:14px;padding:18px 20px;margin-bottom:12px;display:flex;align-items:center;gap:16px;box-shadow:0 2px 8px rgba(0,0,0,.05);">
  <span style="font-size:24px;font-weight:900;color:#e5e7eb;min-width:32px;">#{rank}</span>
  <div style="flex:1;">
    <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:{col};margin-bottom:2px;">{item['category']}</div>
    <div style="font-size:16px;font-weight:700;color:#1e1b4b;">{item['label']}</div>
  </div>
  <div style="font-size:14px;font-weight:800;color:#4f46e5;">{round(item['elo_rating'])}</div>
</div>"""

    matchup_html = ""
    if pair:
        a, b = pair[0], pair[1]
        matchup_html = f"""
<div style="background:linear-gradient(135deg,#4f46e5,#ec4899);border-radius:20px;padding:28px;margin:32px 0;color:white;text-align:center;">
  <p style="font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;opacity:.7;margin-bottom:12px;">NEXT MATCHUP</p>
  <h3 style="font-size:1.4rem;font-weight:800;margin-bottom:6px;">{a['label']}</h3>
  <p style="opacity:.6;margin-bottom:6px;">vs</p>
  <h3 style="font-size:1.4rem;font-weight:800;margin-bottom:16px;">{b['label']}</h3>
  <a href="/taste" style="display:inline-block;background:white;color:#4f46e5;font-weight:800;font-size:14px;padding:10px 28px;border-radius:50px;text-decoration:none;">Choose one &rarr;</a>
</div>"""

    body = f"""
<style>body {{ background: #f5f3ff; }}</style>
<div style="max-width:640px;margin:0 auto;padding:16px 16px 60px;">
  <a href="/variants" style="font-size:12px;color:#9ca3af;">&larr; All variants</a>
  <div style="text-align:center;padding:32px 0 24px;">
    <p style="font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:#4f46e5;margin-bottom:8px;">YOUR TASTE PROFILE</p>
    <h1 style="font-size:3rem;font-weight:900;color:#1e1b4b;letter-spacing:-1.5px;line-height:1;">What You Love</h1>
    <p style="color:#6b7280;margin-top:8px;">{matchup_count} matchups completed &middot; {len(items)} activities ranked</p>
  </div>
  <div style="background:linear-gradient(135deg,#1e1b4b,#312e81);border-radius:20px;padding:28px;margin-bottom:24px;color:white;">
    <p style="font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:#818cf8;margin-bottom:16px;">TOP 3</p>
    {top3_html if top3_html else '<p style="color:rgba(255,255,255,.4);">No items yet. Visit /taste to add some.</p>'}
  </div>
  {matchup_html}
  {rest_html}
</div>"""
    resp = HTMLResponse(_layout("Taste — Magazine", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/v/taste/app", response_class=HTMLResponse)
async def v_taste_app(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    user_id = current_user["id"] if current_user else 1
    db.seed_taste_items(user_id)
    items = db.get_taste_items(user_id)
    pair = db.get_taste_matchup_pair(user_id)
    matchup_count = db.get_taste_matchup_count(user_id)

    min_elo = min((i["elo_rating"] for i in items), default=1000)
    max_elo = max((i["elo_rating"] for i in items), default=1000)
    rng = max_elo - min_elo or 1

    CAT_COLORS = {"music": "#f59e0b", "social": "#3b82f6", "arts": "#ec4899",
                  "intellectual": "#8b5cf6", "active": "#22c55e", "food": "#f97316",
                  "maker": "#06b6d4", "general": "#6b7280"}

    pair_html = ""
    if pair:
        a, b = pair[0], pair[1]
        col_a = CAT_COLORS.get(a["category"], "#6b7280")
        col_b = CAT_COLORS.get(b["category"], "#6b7280")
        pair_html = f"""
<div style="background:#1e1e3a;border-radius:20px;padding:20px;margin-bottom:16px;border:1px solid #2d2d5e;">
  <div style="font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#6366f1;margin-bottom:12px;text-align:center;">QUICK PICK</div>
  <div style="display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:center;margin-bottom:12px;">
    <a href="/taste" style="background:#0f0f1a;border:2px solid {col_a};border-radius:16px;padding:16px 12px;text-align:center;text-decoration:none;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;color:{col_a};margin-bottom:4px;">{a['category']}</div>
      <div style="font-size:14px;font-weight:700;color:#e2e8f0;">{a['label']}</div>
    </a>
    <span style="font-size:14px;font-weight:900;color:#4b5563;">vs</span>
    <a href="/taste" style="background:#0f0f1a;border:2px solid {col_b};border-radius:16px;padding:16px 12px;text-align:center;text-decoration:none;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;color:{col_b};margin-bottom:4px;">{b['category']}</div>
      <div style="font-size:14px;font-weight:700;color:#e2e8f0;">{b['label']}</div>
    </a>
  </div>
  <a href="/taste" style="display:block;text-align:center;font-size:12px;color:#6b7280;text-decoration:none;">Tap to decide &rarr;</a>
</div>"""

    items_html = ""
    for rank, item in enumerate(items, 1):
        pct = round((item["elo_rating"] - min_elo) / rng * 100)
        col = CAT_COLORS.get(item["category"], "#6b7280")
        items_html += f"""
<div style="background:#1e1e3a;border-radius:16px;padding:14px 16px;margin-bottom:10px;border:1px solid #2d2d5e;display:flex;align-items:center;gap:12px;">
  <span style="font-size:13px;font-weight:800;color:#4b5563;min-width:24px;">#{rank}</span>
  <div style="flex:1;min-width:0;">
    <div style="font-size:14px;font-weight:600;color:#e2e8f0;">{item['label']}</div>
    <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:{col};margin-top:2px;">{item['category']}</div>
  </div>
  <div>
    <div style="width:60px;height:4px;background:#2d2d5e;border-radius:2px;overflow:hidden;margin-bottom:3px;">
      <div style="height:100%;width:{pct}%;background:linear-gradient(90deg,#818cf8,#c084fc);border-radius:2px;"></div>
    </div>
    <div style="font-size:11px;font-weight:700;color:#818cf8;text-align:right;">{round(item['elo_rating'])}</div>
  </div>
</div>"""

    body = f"""
<style>
html, body {{ background: #0f0f1a !important; color: #e2e8f0; }}
.app-content {{ background: #0f0f1a !important; }}
</style>
<div style="max-width:480px;margin:0 auto;padding:8px 16px 100px;">
  <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:16px;">
    <div>
      <a href="/variants" style="font-size:11px;color:#4b5563;">&larr; variants</a>
      <h1 style="font-size:22px;font-weight:800;color:#e2e8f0;margin-top:4px;">Taste Stack</h1>
    </div>
    <div style="font-size:12px;color:#6b7280;">{matchup_count} votes</div>
  </div>
  {pair_html}
  {items_html if items_html else '<p style="color:#4b5563;text-align:center;padding:40px 0;">No items yet.</p>'}
</div>
<nav style="position:fixed;bottom:0;left:0;right:0;background:#1e1e3a;border-top:1px solid #2d2d5e;display:flex;justify-content:space-around;padding:12px 0 20px;z-index:100;">
  <a href="/v/calendar/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">📅</div>Events</a>
  <a href="/v/taste/app" style="text-align:center;color:#818cf8;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">🏆</div>Taste</a>
  <a href="/v/groups/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">👥</div>Groups</a>
  <a href="/v/profile/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">👤</div>Profile</a>
</nav>"""
    resp = HTMLResponse(_layout("Taste — App", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


# ── Groups variants ───────────────────────────────────────────────────────────

@app.get("/v/groups/dense", response_class=HTMLResponse)
async def v_groups_dense(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    groups = db.get_all_groups()

    rows = ""
    for g in groups:
        is_member = db.is_group_member(g["id"], current_user["id"]) if current_user else False
        member_badge = f'<span style="font-size:10px;font-weight:700;color:#166534;background:#dcfce7;padding:1px 7px;border-radius:8px;">member</span>' if is_member else ""
        rows += f"""<tr>
          <td style="font-size:12px;font-weight:600;color:#374151;">{g['name']} {member_badge}</td>
          <td style="font-size:12px;color:#9ca3af;text-align:center;">{g['member_count']}</td>
          <td style="font-size:12px;">
            <a href="/group/{g['slug']}" style="color:#1e40af;font-weight:600;">View</a>
          </td>
        </tr>"""

    body = f"""
<style>body {{ background: #f8fafc; }}</style>
<div style="max-width:640px;margin:0 auto;padding:12px 16px 40px;">
  <a href="/variants" style="font-size:12px;color:#6b7280;">&larr; All variants</a>
  <div style="display:flex;align-items:baseline;justify-content:space-between;margin:8px 0 10px;">
    <h1 style="font-size:15px;font-weight:700;color:#374151;">Groups</h1>
    <div style="display:flex;gap:8px;">
      <span style="font-size:12px;color:#9ca3af;">{len(groups)} group{"s" if len(groups) != 1 else ""}</span>
      <a href="/group/create" style="font-size:12px;font-weight:700;color:#4f46e5;">+ Create</a>
    </div>
  </div>
  <table style="width:100%;border-collapse:collapse;background:white;border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.06);">
    <thead><tr>
      <th style="background:#f8fafc;color:#9ca3af;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:left;">Name</th>
      <th style="background:#f8fafc;color:#9ca3af;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:center;">Members</th>
      <th style="background:#f8fafc;color:#9ca3af;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:left;">Action</th>
    </tr></thead>
    <tbody>{rows if rows else '<tr><td colspan="3" style="color:#9ca3af;text-align:center;padding:20px;font-size:12px;">No groups yet.</td></tr>'}</tbody>
  </table>
</div>"""
    resp = HTMLResponse(_layout("Groups — Dense", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/v/groups/magazine", response_class=HTMLResponse)
async def v_groups_magazine(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    groups = db.get_all_groups()

    ACCENT_PAIRS = [
        ("#4f46e5", "#818cf8"), ("#ec4899", "#f472b6"), ("#0ea5e9", "#38bdf8"),
        ("#10b981", "#34d399"), ("#f59e0b", "#fbbf24"), ("#8b5cf6", "#a78bfa"),
    ]

    cards_html = ""
    for idx, g in enumerate(groups):
        is_member = db.is_group_member(g["id"], current_user["id"]) if current_user else False
        base, light = ACCENT_PAIRS[idx % len(ACCENT_PAIRS)]
        action_html = f'<a href="/group/{g["slug"]}" style="display:inline-block;background:{base};color:white;font-weight:700;font-size:13px;padding:10px 20px;border-radius:50px;text-decoration:none;">View group &rarr;</a>'
        if current_user and not is_member:
            action_html = f'''<form action="/group/{g['slug']}/join" method="post" style="display:inline;">
              <button type="submit" style="background:{base};color:white;border:none;font-weight:700;font-size:13px;padding:10px 20px;border-radius:50px;cursor:pointer;">Join group &rarr;</button>
            </form>'''
        member_label = f'<span style="background:rgba(255,255,255,.15);color:white;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;">You&apos;re a member</span>' if is_member else ""
        cards_html += f"""
<div style="background:linear-gradient(135deg,{base},{light});border-radius:20px;padding:28px;margin-bottom:20px;color:white;position:relative;overflow:hidden;">
  <div style="position:absolute;bottom:-30px;right:-30px;width:120px;height:120px;background:rgba(255,255,255,.06);border-radius:50%;"></div>
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
    <h2 style="font-size:1.6rem;font-weight:900;line-height:1.1;flex:1;">{g['name']}</h2>
    {member_label}
  </div>
  <p style="font-size:14px;opacity:.75;margin-bottom:16px;">{g['member_count']} member{"s" if g['member_count'] != 1 else ""}</p>
  {action_html}
</div>"""

    body = f"""
<style>body {{ background: #fdf4ff; }}</style>
<div style="max-width:640px;margin:0 auto;padding:16px 16px 60px;">
  <a href="/variants" style="font-size:12px;color:#9ca3af;">&larr; All variants</a>
  <div style="text-align:center;padding:32px 0 24px;">
    <p style="font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:#4f46e5;margin-bottom:8px;">SHARED EXPERIENCES</p>
    <h1 style="font-size:3rem;font-weight:900;color:#1e1b4b;letter-spacing:-1.5px;">Your Groups</h1>
  </div>
  {cards_html if cards_html else '<div style="text-align:center;padding:40px 0;color:#9ca3af;">No groups yet.</div>'}
  <div style="text-align:center;margin-top:8px;">
    <a href="/group/create" style="display:inline-block;background:white;border:2px solid #4f46e5;color:#4f46e5;font-weight:800;font-size:14px;padding:12px 28px;border-radius:50px;text-decoration:none;">+ Create a group</a>
  </div>
</div>"""
    resp = HTMLResponse(_layout("Groups — Magazine", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/v/groups/app", response_class=HTMLResponse)
async def v_groups_app(request: Request):
    db = get_db()
    current_user = _get_current_user(request)
    groups = db.get_all_groups()

    ICON_COLORS = ["#818cf8", "#34d399", "#f472b6", "#fbbf24", "#38bdf8", "#a78bfa"]

    items_html = ""
    for idx, g in enumerate(groups):
        is_member = db.is_group_member(g["id"], current_user["id"]) if current_user else False
        col = ICON_COLORS[idx % len(ICON_COLORS)]
        initials = g["name"][:2].upper()
        member_dot = f'<div style="width:8px;height:8px;background:#34d399;border-radius:50%;flex-shrink:0;"></div>' if is_member else ""
        items_html += f"""
<a href="/group/{g['slug']}" style="text-decoration:none;display:flex;align-items:center;gap:14px;background:#1e1e3a;border-radius:16px;padding:14px 16px;margin-bottom:10px;border:1px solid #2d2d5e;">
  <div style="width:44px;height:44px;border-radius:14px;background:{col};display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:900;color:white;flex-shrink:0;">{initials}</div>
  <div style="flex:1;min-width:0;">
    <div style="font-size:15px;font-weight:700;color:#e2e8f0;">{g['name']}</div>
    <div style="font-size:12px;color:#6b7280;">{g['member_count']} member{"s" if g['member_count'] != 1 else ""}</div>
  </div>
  {member_dot}
  <div style="color:#4b5563;font-size:16px;">&rsaquo;</div>
</a>"""

    body = f"""
<style>
html, body {{ background: #0f0f1a !important; color: #e2e8f0; }}
.app-content {{ background: #0f0f1a !important; }}
</style>
<div style="max-width:480px;margin:0 auto;padding:8px 16px 100px;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
    <div>
      <a href="/variants" style="font-size:11px;color:#4b5563;">&larr; variants</a>
      <h1 style="font-size:22px;font-weight:800;color:#e2e8f0;margin-top:4px;">Groups</h1>
    </div>
    <a href="/group/create" style="background:#4f46e5;color:white;font-size:12px;font-weight:700;padding:7px 16px;border-radius:20px;text-decoration:none;">+ Create</a>
  </div>
  {items_html if items_html else '<p style="color:#4b5563;text-align:center;padding:40px 0;">No groups yet.</p>'}
</div>
<nav style="position:fixed;bottom:0;left:0;right:0;background:#1e1e3a;border-top:1px solid #2d2d5e;display:flex;justify-content:space-around;padding:12px 0 20px;z-index:100;">
  <a href="/v/calendar/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">📅</div>Events</a>
  <a href="/v/taste/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">🏆</div>Taste</a>
  <a href="/v/groups/app" style="text-align:center;color:#818cf8;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">👥</div>Groups</a>
  <a href="/v/profile/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">👤</div>Profile</a>
</nav>"""
    resp = HTMLResponse(_layout("Groups — App", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


# ── Profile variants ──────────────────────────────────────────────────────────

@app.get("/v/profile/dense", response_class=HTMLResponse)
async def v_profile_dense(request: Request):
    db = get_db()
    current_user = _get_current_user(request)

    if not current_user:
        body = """
<div style="max-width:500px;margin:0 auto;padding:12px 16px 40px;">
  <a href="/variants" style="font-size:12px;color:#6b7280;">&larr; All variants</a>
  <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:6px;padding:20px;margin-top:10px;font-size:13px;color:#374151;">
    Not logged in. <a href="/login" style="color:#1e40af;font-weight:600;">Sign in</a> to view your profile.
  </div>
</div>"""
    else:
        settings = Settings()
        home_lat = current_user.get("home_lat") or settings.latitude
        home_lon = current_user.get("home_lon") or settings.longitude
        fields = [
            ("Name", current_user.get("name") or "—"),
            ("Email", current_user.get("email") or "—"),
            ("Location", current_user.get("location_query") or "—"),
            ("Home lat", str(home_lat)[:10]),
            ("Home lon", str(home_lon)[:10]),
            ("Member since", (current_user.get("created_at") or "")[:10]),
        ]
        rows = "".join(f"""<tr>
          <td style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.5px;padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:nowrap;">{k}</td>
          <td style="font-size:12px;color:#374151;padding:6px 10px;border-bottom:1px solid #f3f4f6;">{v}</td>
        </tr>""" for k, v in fields)
        body = f"""
<div style="max-width:500px;margin:0 auto;padding:12px 16px 40px;">
  <a href="/variants" style="font-size:12px;color:#6b7280;">&larr; All variants</a>
  <div style="display:flex;align-items:baseline;gap:10px;margin:8px 0 10px;">
    <h1 style="font-size:15px;font-weight:700;color:#374151;">Profile</h1>
    <span style="font-size:12px;color:#9ca3af;">Dense view</span>
  </div>
  <table style="width:100%;border-collapse:collapse;background:white;border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.06);">
    <tbody>{rows}</tbody>
  </table>
  <div style="margin-top:10px;display:flex;gap:8px;">
    <a href="/profile" style="font-size:12px;font-weight:600;color:#4f46e5;">Edit profile &rarr;</a>
  </div>
</div>"""

    resp = HTMLResponse(_layout("Profile — Dense", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/v/profile/magazine", response_class=HTMLResponse)
async def v_profile_magazine(request: Request):
    db = get_db()
    current_user = _get_current_user(request)

    if not current_user:
        body = """
<div style="max-width:600px;margin:0 auto;padding:16px 16px 60px;text-align:center;">
  <a href="/variants" style="font-size:12px;color:#9ca3af;">&larr; All variants</a>
  <div style="padding:60px 0;">
    <h2 style="font-size:2rem;font-weight:900;color:#1e1b4b;margin-bottom:12px;">Sign in to view your profile</h2>
    <a href="/login" style="display:inline-block;background:linear-gradient(135deg,#4f46e5,#ec4899);color:white;font-weight:800;font-size:14px;padding:12px 28px;border-radius:50px;text-decoration:none;">Sign in &rarr;</a>
  </div>
</div>"""
    else:
        settings = Settings()
        name = current_user.get("name") or current_user.get("email", "").split("@")[0]
        email = current_user.get("email") or ""
        location = current_user.get("location_query") or "Not set"
        initials = name[:2].upper() if name else "?"
        body = f"""
<div style="max-width:600px;margin:0 auto;padding:16px 16px 60px;">
  <a href="/variants" style="font-size:12px;color:#9ca3af;">&larr; All variants</a>
  <div style="background:linear-gradient(135deg,#4f46e5,#ec4899);border-radius:20px;padding:40px 32px;margin:16px 0 24px;color:white;text-align:center;position:relative;overflow:hidden;">
    <div style="position:absolute;top:-40px;right:-40px;width:160px;height:160px;background:rgba(255,255,255,.07);border-radius:50%;"></div>
    <div style="width:80px;height:80px;border-radius:50%;background:rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center;font-size:28px;font-weight:900;margin:0 auto 16px;">{initials}</div>
    <h1 style="font-size:2.2rem;font-weight:900;margin-bottom:4px;line-height:1.1;">{name}</h1>
    <p style="opacity:.7;font-size:14px;">{email}</p>
    <p style="opacity:.6;font-size:13px;margin-top:4px;">{location}</p>
  </div>
  <div style="background:white;border-radius:20px;padding:28px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.06);">
    <h2 style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#9ca3af;margin-bottom:20px;">Account Details</h2>
    <div style="margin-bottom:16px;">
      <div style="font-size:12px;color:#9ca3af;margin-bottom:3px;">Full Name</div>
      <div style="font-size:16px;font-weight:600;color:#1e1b4b;">{name}</div>
    </div>
    <div style="margin-bottom:16px;">
      <div style="font-size:12px;color:#9ca3af;margin-bottom:3px;">Email</div>
      <div style="font-size:16px;font-weight:600;color:#1e1b4b;">{email}</div>
    </div>
    <div>
      <div style="font-size:12px;color:#9ca3af;margin-bottom:3px;">Location</div>
      <div style="font-size:16px;font-weight:600;color:#1e1b4b;">{location}</div>
    </div>
  </div>
  <div style="text-align:center;">
    <a href="/profile" style="display:inline-block;background:linear-gradient(135deg,#4f46e5,#7c3aed);color:white;font-weight:800;font-size:14px;padding:12px 28px;border-radius:50px;text-decoration:none;">Edit profile &rarr;</a>
  </div>
</div>"""

    resp = HTMLResponse(_layout("Profile — Magazine", body, current_user))
    return _maybe_set_cookie(request, resp, current_user)


@app.get("/v/profile/app", response_class=HTMLResponse)
async def v_profile_app(request: Request):
    db = get_db()
    current_user = _get_current_user(request)

    if not current_user:
        not_logged_in = """
<div style="max-width:480px;margin:0 auto;padding:8px 16px 100px;text-align:center;">
  <a href="/variants" style="font-size:11px;color:#4b5563;">&larr; variants</a>
  <div style="padding:60px 0;">
    <div style="font-size:48px;margin-bottom:16px;">👤</div>
    <h2 style="font-size:20px;font-weight:800;color:#e2e8f0;margin-bottom:12px;">Not signed in</h2>
    <a href="/login" style="display:inline-block;background:#4f46e5;color:white;font-weight:700;font-size:14px;padding:12px 28px;border-radius:20px;text-decoration:none;">Sign in</a>
  </div>
</div>"""
        body = not_logged_in
    else:
        settings = Settings()
        name = current_user.get("name") or current_user.get("email", "").split("@")[0]
        email = current_user.get("email") or ""
        location = current_user.get("location_query") or "Not set"
        member_since = (current_user.get("created_at") or "")[:10]
        initials = name[:2].upper() if name else "?"

        sections = [
            ("Account", [
                ("Name", name),
                ("Email", email),
            ]),
            ("Location", [
                ("City", location),
                ("Member since", member_since),
            ]),
        ]

        sections_html = ""
        for section_title, fields in sections:
            fields_html = ""
            for i, (label, value) in enumerate(fields):
                border = "border-bottom:1px solid #2d2d5e;" if i < len(fields) - 1 else ""
                fields_html += f"""
<div style="display:flex;justify-content:space-between;align-items:center;padding:14px 0;{border}">
  <span style="font-size:14px;color:#6b7280;">{label}</span>
  <span style="font-size:14px;font-weight:600;color:#e2e8f0;">{value}</span>
</div>"""
            sections_html += f"""
<div style="background:#1e1e3a;border-radius:20px;padding:4px 20px;margin-bottom:16px;border:1px solid #2d2d5e;">
  <p style="font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#4b5563;padding:12px 0 4px;">{section_title}</p>
  {fields_html}
</div>"""

        body = f"""
<div style="max-width:480px;margin:0 auto;padding:8px 16px 100px;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;">
    <a href="/variants" style="font-size:11px;color:#4b5563;">&larr; variants</a>
    <a href="/profile" style="font-size:12px;font-weight:700;color:#818cf8;text-decoration:none;">Edit</a>
  </div>
  <div style="text-align:center;margin-bottom:28px;">
    <div style="width:72px;height:72px;border-radius:50%;background:linear-gradient(135deg,#4f46e5,#ec4899);display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:900;color:white;margin:0 auto 12px;">{initials}</div>
    <h1 style="font-size:20px;font-weight:800;color:#e2e8f0;">{name}</h1>
    <p style="font-size:13px;color:#6b7280;">{email}</p>
  </div>
  {sections_html}
</div>"""

    app_body = f"""
<style>
html, body {{ background: #0f0f1a !important; color: #e2e8f0; }}
.app-content {{ background: #0f0f1a !important; }}
</style>
{body}
<nav style="position:fixed;bottom:0;left:0;right:0;background:#1e1e3a;border-top:1px solid #2d2d5e;display:flex;justify-content:space-around;padding:12px 0 20px;z-index:100;">
  <a href="/v/calendar/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">📅</div>Events</a>
  <a href="/v/taste/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">🏆</div>Taste</a>
  <a href="/v/groups/app" style="text-align:center;color:#6b7280;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">👥</div>Groups</a>
  <a href="/v/profile/app" style="text-align:center;color:#818cf8;text-decoration:none;font-size:10px;font-weight:700;"><div style="font-size:20px;margin-bottom:2px;">👤</div>Profile</a>
</nav>"""
    resp = HTMLResponse(_layout("Profile — App", app_body, current_user))
    return _maybe_set_cookie(request, resp, current_user)
