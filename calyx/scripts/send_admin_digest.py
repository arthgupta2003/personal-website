#!/usr/bin/env python3
"""Send weekly admin digest: source health, search retros, TODOs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from recom.config import Settings
from recom.db import Database
from recom.email.sender import send_email


def main():
    settings = Settings()
    db = Database(settings.db_path)

    # Source health from latest run
    runs = db.get_runs()
    if not runs:
        print("No runs found")
        return

    latest_run = runs[0]
    stats = db.conn.execute(
        "SELECT source_name, events_found, error_message FROM source_stats WHERE run_id=? ORDER BY events_found DESC",
        (latest_run["id"],),
    ).fetchall()

    source_rows = ""
    broken = []
    for s in stats:
        if s["source_name"].startswith("_"):
            continue
        count = s["events_found"]
        color = "#4a6741" if count >= 10 else "#c4734f" if count >= 1 else "#d00"
        source_rows += f'<tr><td style="padding:6px 12px;border-bottom:1px solid #eee;">{s["source_name"]}</td><td style="padding:6px 12px;border-bottom:1px solid #eee;font-weight:700;color:{color};">{count}</td></tr>'
        if count == 0:
            broken.append(s["source_name"])

    # Search retros from past week
    retros = db.conn.execute(
        "SELECT query, db_count, web_count, diagnosis FROM search_retros ORDER BY id DESC LIMIT 10"
    ).fetchall()

    retro_rows = ""
    for r in retros:
        retro_rows += f'<tr><td style="padding:6px 12px;border-bottom:1px solid #eee;">{r["query"]}</td><td style="padding:6px 12px;border-bottom:1px solid #eee;">{r["db_count"]} DB / {r["web_count"]} web</td></tr>'

    # TODOs
    todo_path = Path("todo.txt")
    todos = []
    if todo_path.exists():
        for line in todo_path.read_text().splitlines():
            if line.strip().startswith("# TODO:"):
                todos.append(line.strip()[8:])

    todo_html = "".join(f"<li style='margin:4px 0;'>{t}</li>" for t in todos[:15])

    html = f"""
    <div style="font-family:Inter,system-ui,sans-serif;max-width:600px;margin:0 auto;">
        <div style="border-bottom:2px solid #4a6741;padding:16px 0;margin-bottom:20px;">
            <h1 style="font-size:22px;font-weight:800;color:#4a6741;margin:0;">Calyx Admin Digest</h1>
            <p style="font-size:13px;color:#888;margin:4px 0 0;">Latest run: #{latest_run["id"]} ({latest_run["timestamp"][:10]})</p>
        </div>

        {"<div style='background:#fff3f3;border-left:3px solid #d00;padding:12px 16px;margin-bottom:16px;'><strong style='color:#d00;'>Broken sources:</strong> " + ", ".join(broken) + "</div>" if broken else ""}

        <h2 style="font-size:11px;font-weight:700;color:#4a6741;text-transform:uppercase;letter-spacing:2px;">Source Health</h2>
        <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
            <tr><th style="text-align:left;padding:6px 12px;border-bottom:2px solid #4a6741;font-size:11px;color:#888;text-transform:uppercase;">Source</th><th style="text-align:left;padding:6px 12px;border-bottom:2px solid #4a6741;font-size:11px;color:#888;text-transform:uppercase;">Events</th></tr>
            {source_rows}
        </table>

        {"<h2 style='font-size:11px;font-weight:700;color:#c4734f;text-transform:uppercase;letter-spacing:2px;'>Search Gaps (retros)</h2><table style='width:100%;border-collapse:collapse;margin-bottom:24px;'><tr><th style='text-align:left;padding:6px 12px;border-bottom:2px solid #c4734f;font-size:11px;color:#888;'>Query</th><th style='text-align:left;padding:6px 12px;border-bottom:2px solid #c4734f;font-size:11px;color:#888;'>Results</th></tr>" + retro_rows + "</table>" if retro_rows else ""}

        <h2 style="font-size:11px;font-weight:700;color:#4a6741;text-transform:uppercase;letter-spacing:2px;">Open TODOs ({len(todos)})</h2>
        <ul style="font-size:13px;color:#333;padding-left:20px;margin-bottom:24px;">
            {todo_html}
        </ul>

        <div style="border-top:1px solid #eee;padding-top:12px;font-size:12px;color:#888;">
            <a href="{settings.dashboard_url}/admin" style="color:#4a6741;">Open admin dashboard</a>
        </div>
    </div>
    """

    send_email("[Calyx] Weekly Admin Digest", html, settings)
    print("Admin digest sent!")


if __name__ == "__main__":
    main()
