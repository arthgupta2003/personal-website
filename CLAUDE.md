# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo structure

This is a **monorepo** with two main areas:
- `calyx/` — the Calyx app (Python, FastAPI, SQLite). All commands below should be run from within `calyx/`.
- `site/` — static landing page for arthgupta.dev

## Project status

**This project is in beta.** There are no external users yet — only the developer and a few friends. This means:
- No backwards compatibility required. Break old routes, rename endpoints, change DB schemas freely.
- No migration scripts needed. Blow away old artifacts, delete unused code, restructure tables.
- Move fast. Don't add deprecation warnings or keep dead code around "just in case."

## Commands

```bash
# Install dependencies
uv sync

# Run full pipeline (discovers events, ranks, emails)
uv run calyx                  # single user (from .env)
uv run calyx --user 2         # specific user ID
uv run calyx --all-users      # all active users

# Individual services
uv run calyx-daily            # send daily digest email
uv run calyx-dashboard        # start FastAPI dashboard on port 8000

# Start all services (dashboard + Cloudflare tunnel + Claude Code Web) in tmux
./start.sh
./start.sh stop
./start.sh status
tmux attach -t calyx          # view logs

# One-time OAuth setup
uv run python scripts/auth_spotify.py
uv run python scripts/auth_youtube.py
uv run python scripts/auth_gmail.py

# Cron jobs (3 total — run once to install all)
bash scripts/install_cron.sh
# Installs:
#   Weekly pipeline   — Saturday 9am (discover + rank + email)
#   Daily digest      — 8am every day (today's picks from latest run)
#   Admin digest      — Sunday 10am (source health, retros, TODOs)
# Logs: state/{cron,daily,admin}.log
# Verify: crontab -l
```

No Makefile or test suite — `uv` handles all build/run tasks.

## Testing & quality checklist

**After any dashboard change**, run BOTH tests before considering the task done. Dashboard must be running first (`/workspace/.venv/bin/uvicorn calyx.dashboard.app:app --host 0.0.0.0 --port 8000`).

```bash
# 1. HTTP smoke test — checks all routes return correct status codes
bash scripts/smoke_test.sh test@example.com     # ~35 checks, must be all-pass/0-fail

# 2. Browser test — real Chromium, checks rendered output after JS executes
/workspace/.venv/bin/python scripts/browser_test.py   # ~55 checks, must be all-pass/0-fail
```

Also validate JS syntax after any template change:
```bash
curl -s http://localhost:8000/ > /tmp/p.html
python3 -c "import re; html=open('/tmp/p.html').read(); s=max(re.findall(r'<script[^>]*>(.*?)</script>',html,re.DOTALL),key=len); open('/tmp/s.js','w').write(s)"
node --check /tmp/s.js   # must show no output (clean)
```

**Checklist for dashboard changes:**
1. Import check: `python -c "from calyx.dashboard.app import app; print('OK')"`
2. All pages use `_layout(..., current_user)` — never `LAYOUT_HEAD` directly
3. `_layout(title, body, current_user)` — body must NOT end with `+ LAYOUT_FOOT`
4. Auth-required pages call `_get_current_user(request)` and redirect if None
5. Logged-in users see their own data — use `db.get_user_latest_run(user_id)` not `db.get_runs()[0]`
6. No unescaped apostrophes in JS strings — use `&apos;` or `&quot;` in HTML attributes inside JS

## Git workflow

Commit changes regularly — at minimum after completing each feature or fixing a bug:

```bash
git add src/ scripts/ CLAUDE.md README.md
git commit -m "short description of what changed"
```

Keep commits focused. Don't batch unrelated changes. Never force-push.

## Architecture

Calyx is a personal weekly event recommender for Boston/Cambridge. The core flow is a linear pipeline:

```
Ingest (YouTube + Spotify + Gmail) → Extract Interests (Claude) → Discover Events (7 sources, parallel) → Rank (Claude) → Email + Dashboard (SQLite)
```

**Pipeline orchestrator**: `src/calyx/main.py` — runs each stage in sequence, handles `--user`/`--all-users` flags, tracks cost per run.

### Key modules

| Path | Purpose |
|------|---------|
| `src/calyx/config.py` | Pydantic settings loaded from `.env` (API keys, location, email, model) |
| `src/calyx/models.py` | Shared data models: `Event`, `RankedEvent`, `InterestProfile`, `ActivityItem` |
| `src/calyx/db.py` | SQLite ORM layer — all persistence: runs, events, rankings, RSVPs, groups, costs |
| `src/calyx/ingest/` | YouTube API, Spotify API, Gmail newsletter scanning |
| `src/calyx/extract/interests.py` | Claude analyzes activity → `InterestProfile` (cached 7 days) |
| `src/calyx/events/` | One file per source; `aggregator.py` runs all in parallel and deduplicates |
| `src/calyx/ranking/ranker.py` | Claude scores events on 7 dimensions in batches of 40 |
| `src/calyx/ranking/bucket_list.py` | Claude picks seasonal activities from `bucket_list.txt` |
| `src/calyx/email/composer.py` | Jinja2 HTML email templates |
| `src/calyx/email/sender.py` | Gmail SMTP (STARTTLS) — sends digests, invites, RSVP notifications |
| `src/calyx/dashboard/app.py` | FastAPI app: calendar view, RSVP API, groups, iCal feed, admin endpoints |

### Ranking system

Events are scored on 7 dimensions (0–15 each): interest match, social/fun factor, urgency/FOMO, logistics ease, friend-bringability, discovery potential, venue/quality. Scores are weighted by **vibe** (social / intellectual / mixed) — e.g., social events weight "friend-bringability" higher; intellectual events weight "interest match" higher.

Cost: ~$3–4 per full run (ranking ~1100 events with claude-sonnet).

### Database schema (SQLite: `calyx.db`)

Tables: `users`, `runs`, `events`, `rankings`, `costs`, `source_stats`, `ingest_stats`, `attended`, `rsvps`, `groups`, `group_members`, `taste_items`, `taste_matchups`, `impressions`, `steering`, `travel_plans`, `source_cache`. Multi-user: each user has their own OAuth tokens, interest profile, location, and email.

### Event sources (13+)

Eventbrite (JSON-LD scrape), Meetup (Next.js __APOLLO_STATE__), Ticketmaster, Harvard/Tufts/Brandeis (Trumba JSON), The Boston Calendar / Do617 / ArtsBoston (scrapers), Luma, Bandsintown (per-artist, Spotify-seeded), Dice.fm (Next.js browse state), Resident Advisor (area 530), Museums (ICA/MFA/MIT List/Gardner/Harvard Art/MoS), BPL (BiblioCommons API), Bowery Presents (AXS JSON), BSO (Algolia), Coolidge, Boston.gov, Outdoor (curated DCR/AMC spots), University (MIT, Northeastern, MassArt, BU, Suffolk, BC via Localist; Berklee via Drupal Views scraper). Sources fail gracefully — missing API keys just skip that source. The aggregator flags silent rot: any source returning events but 0 with dates gets a warning in its SourceStat (visible on /admin/sources).

### Ranking system (two-pass)

1. **Haiku prefilter** (>150 events): fast relevance pass, ~$0.30 for 2000 events
2. **Sonnet full ranking**: 7-dimension scoring (interest, social, urgency, logistics, friend, discovery, quality), weighted by vibe (social/intellectual/mixed)

Post-scoring adjustments:
- Season context injected (spring/summer/fall/winter hints)
- Calendar density: user's upcoming RSVPs passed as context
- Friend boost: +25 (going) / +10 (maybe) from group-mates
- Steering directives: block/done/more/less/pause per keyword/category

Cost: ~$1–2 per full run (down from ~$4 with prefilter).

### Dashboard

FastAPI app with Google OAuth sign-in (cookie session); per-user `?u=<token>` and `/u/<token>/...` URLs are bearer convenience tokens for email and iCal contexts. Key routes:

| Route | Purpose |
|-------|---------|
| `/` | Week calendar (list + FullCalendar views) with RSVP |
| `/search` | AI-powered NL event search (Haiku) |
| `/taste` | Elo taste discovery — compare activity archetypes |
| `/groups`, `/group/<slug>` | Group coordination, shared RSVP |
| `/attended` | History with star ratings |
| `/venues` | Venue taste profile |
| `/travel` | Multi-city travel mode (add trip → get city events) |
| `/budget` | Monthly spending tracker |
| `/profile` | User settings, home location |
| `/feed.ics` | Public iCal feed (`?min_score=`) |
| `/u/<token>/feed.ics` | Per-user iCal with RSVP links |
| `/u/<token>/rsvps.ics` | Per-user RSVP-only iCal feed |
| `/variants` | UI variant experiments |
| `/admin/sources` | Scraper health: success rate, sparklines, cache age |
| `/admin/email-preview` | Preview email templates before sending |
| `/admin/pipeline` | Trigger and monitor pipeline runs |
| `/admin/backtest` | Quant-style signal attribution and backtest reports |
| `/admin/cal-preview` | Preview calendar feed output |
| `/admin/ml` | ML model training dashboard |
| `/admin/retros` | Post-run retrospectives and analysis |
| `/admin/ranking-analysis` | Ranking dimension analysis and weight tuning |
| `/api/steer` | One-click steering from email links |
| `/api/taste/vote` | Elo vote endpoint |

## Configuration

Copy `.env.example` to `.env`. Only `CALYX_ANTHROPIC_API_KEY` is strictly required to run. Event API keys (Eventbrite, Ticketmaster, etc.) are optional — missing keys skip that source. The default Claude model is `claude-sonnet-4-20250514`; switch to `claude-haiku` in `.env` to reduce cost.

User-editable plaintext files:
- `my_interests.txt` — manual interest keywords merged with Claude-extracted profile
- `bucket_list.txt` — seasonal activities Claude selects from
- `newsletter_senders.txt` — email whitelist for newsletter scanning

OAuth tokens stored under `state/tokens/`.
