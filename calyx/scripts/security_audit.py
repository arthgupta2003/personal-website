#!/usr/bin/env python3
"""Run a security audit and email the report."""

import html
import re
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recom.config import Settings
from recom.email.sender import send_email


def audit() -> list[dict]:
    """Run all audit checks and return findings."""
    findings = []
    root = Path(__file__).resolve().parent.parent
    app_path = root / "src" / "recom" / "dashboard" / "app.py"
    db_path = root / "src" / "recom" / "db.py"
    sender_path = root / "src" / "recom" / "email" / "sender.py"

    app_code = app_path.read_text() if app_path.exists() else ""
    db_code = db_path.read_text() if db_path.exists() else ""
    sender_code = sender_path.read_text() if sender_path.exists() else ""

    # 1. Check token length
    m = re.search(r"token_hex\((\d+)\)", db_code)
    if m and int(m.group(1)) < 16:
        findings.append({
            "severity": "CRITICAL",
            "category": "Auth",
            "title": f"Auth token is only {int(m.group(1))} bytes — brute-forceable",
            "detail": f"secrets.token_hex({m.group(1)}) generates only {2**(int(m.group(1))*8):,} possibilities. Use token_hex(16) or longer.",
            "file": "db.py",
        })

    # 2. Check for state-changing GET endpoints
    get_state_changes = re.findall(r'@app\.get\(["\']/(api/(?:steer|rate|rsvp-link|attend-link|ping-group))', app_code)
    if get_state_changes:
        findings.append({
            "severity": "CRITICAL",
            "category": "CSRF",
            "title": f"{len(get_state_changes)} state-changing GET endpoints",
            "detail": f"Endpoints: {', '.join(get_state_changes)}. These can be triggered via <img> tags from any website. Use POST with CSRF tokens.",
            "file": "app.py",
        })

    # 3. Check for unescaped f-string HTML patterns
    # Look for f-string HTML with {variable} that's not html.escape'd
    xss_patterns = re.findall(r'HTMLResponse\(f["\'].*?\{(?!{)(\w+)', app_code)
    if xss_patterns:
        unique_vars = set(xss_patterns[:20])
        findings.append({
            "severity": "HIGH",
            "category": "XSS",
            "title": "Unescaped user input in HTML f-strings",
            "detail": f"Found {len(xss_patterns)} f-string HTML interpolations. Variables like {', '.join(list(unique_vars)[:5])} may contain user input. Use html.escape() or Jinja2 with autoescape.",
            "file": "app.py",
        })

    # 4. Check admin endpoints for auth
    admin_routes = re.findall(r'@app\.\w+\(["\']/(admin/[^"\']+)', app_code)
    admin_with_check = len(re.findall(r'current_user.*?id.*?!= 1', app_code))
    if admin_routes and admin_with_check < len(admin_routes):
        findings.append({
            "severity": "HIGH",
            "category": "Auth",
            "title": f"Only {admin_with_check}/{len(admin_routes)} admin routes check admin status",
            "detail": "Most /admin/* routes are accessible to any authenticated user (or unauthenticated via user_id=1 fallback).",
            "file": "app.py",
        })

    # 5. Check for user_id fallback to 1
    fallbacks = re.findall(r'else\s+1\b', app_code)
    if len(fallbacks) > 3:
        findings.append({
            "severity": "HIGH",
            "category": "Auth",
            "title": f"Unauthenticated fallback to user_id=1 ({len(fallbacks)} places)",
            "detail": "Pattern 'current_user[\"id\"] if current_user else 1' lets unauthenticated requests act as the admin user.",
            "file": "app.py",
        })

    # 6. Check cookie security
    if "secure=True" not in app_code and "set_cookie" in app_code:
        findings.append({
            "severity": "MEDIUM",
            "category": "Config",
            "title": "Cookie missing Secure flag",
            "detail": "set_cookie() does not include secure=True. Cookie can be sent over HTTP.",
            "file": "app.py",
        })

    # 7. Check for SQL f-string interpolation
    sql_fstrings = re.findall(r'execute\(f["\']', db_code)
    if sql_fstrings:
        findings.append({
            "severity": "MEDIUM",
            "category": "SQLi",
            "title": f"SQL queries with f-string interpolation ({len(sql_fstrings)} instances)",
            "detail": "Dynamic SQL via f-strings risks injection if column/table names come from user input. Use parameterized queries or validate against allowlists.",
            "file": "db.py",
        })

    # 8. Check email sender for HTML escaping
    if "html.escape" not in sender_code and "f\"" in sender_code:
        findings.append({
            "severity": "MEDIUM",
            "category": "XSS",
            "title": "Unescaped user input in HTML emails",
            "detail": "Email HTML uses f-strings with user names and event titles without html.escape(). Could render malicious HTML in email clients.",
            "file": "sender.py",
        })

    # 9. Check if recom.db is in .gitignore
    gitignore = (root / ".gitignore").read_text() if (root / ".gitignore").exists() else ""
    if "recom.db" not in gitignore:
        findings.append({
            "severity": "LOW",
            "category": "Secrets",
            "title": "recom.db not in .gitignore",
            "detail": "Database contains user tokens, emails, and personal data. Add recom.db to .gitignore and git rm --cached recom.db.",
            "file": ".gitignore",
        })

    # 10. Check for rate limiting
    if "slowapi" not in app_code.lower() and "ratelimit" not in app_code.lower():
        findings.append({
            "severity": "INFO",
            "category": "Config",
            "title": "No rate limiting on any endpoint",
            "detail": "No rate limiting middleware detected. /api/search calls Claude API and costs money. /api/login could be brute-forced.",
            "file": "app.py",
        })

    # 11. Check /join exposes user list
    if "/join" in app_code and "email" in app_code[app_code.find("/join"):app_code.find("/join") + 2000]:
        findings.append({
            "severity": "INFO",
            "category": "Auth",
            "title": "User enumeration via /join page",
            "detail": "The /join page lists all users with names and emails publicly.",
            "file": "app.py",
        })

    # 12. Check for no-referrer meta tag
    if 'referrer' not in app_code.lower() or 'no-referrer' not in app_code.lower():
        findings.append({
            "severity": "MEDIUM",
            "category": "Auth",
            "title": "User tokens leak via Referer headers",
            "detail": "Auth tokens in ?u= URLs leak via HTTP Referer when users click external links. Add <meta name=\"referrer\" content=\"no-referrer\">.",
            "file": "app.py",
        })

    return findings


def format_report(findings: list[dict]) -> str:
    """Format findings as plain HTML email."""
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    findings.sort(key=lambda f: severity_order.get(f["severity"], 5))

    severity_colors = {
        "CRITICAL": "#dc2626",
        "HIGH": "#ea580c",
        "MEDIUM": "#d97706",
        "LOW": "#2563eb",
        "INFO": "#6b7280",
    }

    counts = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    summary = " / ".join(f"{v} {k}" for k, v in counts.items())

    rows = ""
    for i, f in enumerate(findings, 1):
        color = severity_colors.get(f["severity"], "#6b7280")
        rows += f"""
        <tr style="border-bottom:1px solid #e5e7eb;">
            <td style="padding:12px 8px;font-size:13px;vertical-align:top;">
                <span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;">{html.escape(f['severity'])}</span>
            </td>
            <td style="padding:12px 8px;font-size:13px;vertical-align:top;color:#6b7280;">{html.escape(f['category'])}</td>
            <td style="padding:12px 8px;font-size:13px;vertical-align:top;">
                <strong>{html.escape(f['title'])}</strong><br>
                <span style="color:#6b7280;font-size:12px;">{html.escape(f['detail'])}</span><br>
                <code style="font-size:11px;color:#9ca3af;">{html.escape(f['file'])}</code>
            </td>
        </tr>"""

    return f"""
    <div style="font-family:-apple-system,sans-serif;max-width:700px;margin:0 auto;padding:24px;">
        <h1 style="font-size:20px;color:#1e293b;">Recom Security Audit Report</h1>
        <p style="font-size:14px;color:#64748b;">{summary}</p>
        <table style="width:100%;border-collapse:collapse;margin-top:16px;">
            <thead>
                <tr style="border-bottom:2px solid #e5e7eb;text-align:left;">
                    <th style="padding:8px;font-size:12px;color:#6b7280;width:80px;">Severity</th>
                    <th style="padding:8px;font-size:12px;color:#6b7280;width:70px;">Category</th>
                    <th style="padding:8px;font-size:12px;color:#6b7280;">Finding</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        <p style="font-size:12px;color:#9ca3af;margin-top:24px;">Generated by scripts/security_audit.py</p>
    </div>"""


def main():
    findings = audit()
    report_html = format_report(findings)

    # Print summary to stdout
    for f in findings:
        print(f"[{f['severity']}] {f['category']}: {f['title']}")
    print(f"\nTotal: {len(findings)} findings")

    # Email the report
    settings = Settings()
    if settings.email_to and settings.smtp_user:
        send_email("Recom Security Audit Report", report_html, settings)
        print(f"Report emailed to {settings.email_to}")
    else:
        print("Email not configured — report printed to stdout only.")
        # Write HTML report to file as fallback
        out = Path("state/security_audit.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report_html)
        print(f"HTML report saved to {out}")


if __name__ == "__main__":
    main()
