# recom

A personal weekly event recommender for the Boston/Cambridge area. Learns your interests from YouTube, Spotify, and email newsletters, discovers local events from 7+ sources, ranks everything with Claude using 7 scoring dimensions, and sends you a weekly email digest.

Live dashboard: **https://recom.arthgupta.dev**

## How it works

```
INGEST (YouTube API + Spotify + Gmail newsletters)
  → EXTRACT (Claude → interest profile, merged with manual keywords)
    → DISCOVER (APIs + scrapers, 7 sources in parallel)
      → RANK (Claude, 7 dimensions, vibe-based weight vectors)
        → STORE (SQLite) → EMAIL + DASHBOARD
```

### Pipeline steps

1. **Ingest** — Pull subscriptions + liked videos from YouTube, top artists + tracks + recently played from Spotify, newsletter emails from Gmail
2. **Extract interests** — Claude analyzes your activity and produces an interest profile (cached 7 days). Merged with manual keywords from `my_interests.txt`
3. **Discover events** — Queries 7 sources in parallel:
   - Eventbrite API (general events, 10mi radius)
   - Meetup GraphQL API (groups, community)
   - Ticketmaster API (concerts, artist-specific searches for your Spotify artists)
   - MIT Events (calendar scraper)
   - Harvard Events (Trumba JSON API)
   - The Boston Calendar + Do617 + ArtsBoston (scrapers)
4. **Rank** — Claude scores every event on 7 dimensions (0-15 each), weighted differently based on whether the event is social, intellectual, or mixed
5. **Bucket list** — Claude picks 3-5 seasonally relevant activities from your `bucket_list.txt`
6. **Email** — HTML digest with top 10 picks, clubs/classes, bucket list suggestions, and all remaining events organized by day
7. **Store** — Everything saved to SQLite for the dashboard

### Scoring dimensions

| Dimension | Weight (social) | Weight (intellectual) | Weight (mixed) |
|-----------|:-:|:-:|:-:|
| **Interest match** | 1.5 | 3.5 | 2.5 |
| **Social/fun factor** | 2.5 | 0.5 | 1.5 |
| **Urgency/FOMO** | 1.5 | 1.5 | 1.5 |
| **Logistics ease** | 1.5 | 1.5 | 1.5 |
| **Friend-bringability** | 2.5 | 0.5 | 1.5 |
| **Discovery potential** | 0.5 | 1.5 | 1.0 |
| **Venue/quality** | 1.0 | 2.0 | 1.0 |

A niche math lecture (intellectual vibe) isn't penalized for low friend-bringability. A college basketball game (social vibe) isn't penalized for low interest match.

## Setup

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- API keys: Anthropic (required), Ticketmaster (recommended), Eventbrite/Songkick (optional)
- OAuth credentials: Google Cloud (YouTube + Gmail), Spotify
- Gmail app password for SMTP sending

### Install

```bash
git clone <repo> && cd recom
cp .env.example .env  # fill in API keys and credentials
uv sync
```

### One-time auth

```bash
uv run python scripts/auth_spotify.py    # opens browser for Spotify OAuth
uv run python scripts/auth_youtube.py    # opens browser for YouTube/Gmail OAuth
```

### Configure your interests

Edit `my_interests.txt` — one keyword per line:
```
jazz
functional programming
rock climbing
board games
```

Edit `bucket_list.txt` — activities you want to do (not tied to events):
```
kite surfing
skiing at Loon Mountain, NH
try November Project (Wed 6:30am Harvard Stadium)
```

### Run

```bash
uv run recom              # full pipeline — ingest → rank → email
uv run recom-dashboard    # start dashboard on localhost:8000
```

### Cron (weekly, Saturday 9am)

```bash
bash scripts/install_cron.sh
```

## Deployment

The dashboard is deployed via Cloudflare Tunnel to `recom.arthgupta.dev`. Both the dashboard and tunnel run as persistent macOS services via `launchd`.

### How it works

- Pipeline runs locally via cron (needs OAuth tokens for Spotify/YouTube/Gmail)
- Dashboard (FastAPI on `localhost:8000`) reads from `recom.db` (SQLite)
- Cloudflare Tunnel exposes `localhost:8000` to the public internet with SSL
- Both services auto-start on login and auto-restart on crash via `launchd`

### launchd services

Two plist files in `~/Library/LaunchAgents/`:

| Service | Plist | What it does |
|---------|-------|-------------|
| Dashboard | `com.recom.dashboard.plist` | Runs `uv run recom-dashboard` on port 8000 |
| Tunnel | `com.recom.tunnel.plist` | Runs `cloudflared tunnel run recom-dashboard` |

Both have `KeepAlive: true` and `RunAtLoad: true` — they start on login and restart if they crash.

```bash
# Manage services
launchctl load ~/Library/LaunchAgents/com.recom.dashboard.plist
launchctl load ~/Library/LaunchAgents/com.recom.tunnel.plist
launchctl unload ~/Library/LaunchAgents/com.recom.dashboard.plist   # stop
launchctl list | grep recom                                         # check status

# Logs
tail -f state/dashboard.log
tail -f state/tunnel.log
```

### Keeping the server alive

To prevent macOS from sleeping (required for the tunnel to stay up):

**System Settings → Displays → Advanced → Prevent automatic sleeping on power adapter** → turn ON

This keeps the laptop awake while plugged in so the dashboard and tunnel stay accessible.

### Tunnel config

Tunnel config lives at `~/.cloudflared/config.yml`:
```yaml
tunnel: <tunnel-id>
credentials-file: ~/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: recom.arthgupta.dev
    service: http://localhost:8000
  - service: http_status:404
```

### Add more subdomains

```bash
cloudflared tunnel route dns recom-dashboard <subdomain>.arthgupta.dev
# Then add to ingress in config.yml
```

### Calendar subscription

Subscribe to the iCal feed for top events in any calendar app:

```
https://recom.arthgupta.dev/feed.ics
```

- Default: only strong matches (score >= 55)
- Use `?min_score=25` for all recommended events
- Events show as `[72] Event Title` in your calendar
- Feed auto-updates with each pipeline run

## Project structure

```
src/recom/
├── main.py              # pipeline orchestrator
├── config.py            # pydantic-settings from .env
├── models.py            # data models (Event, RankedEvent, InterestProfile, etc.)
├── db.py                # SQLite helpers
├── ingest/
│   ├── youtube.py       # YouTube Data API (subscriptions + liked videos)
│   ├── spotify.py       # Spotify API (top artists, tracks, recently played)
│   └── gmail.py         # Gmail API (newsletter scanning)
├── extract/
│   └── interests.py     # Claude: activity → interest profile
├── events/
│   ├── eventbrite.py    # Eventbrite API
│   ├── meetup.py        # Meetup GraphQL API
│   ├── ticketmaster.py  # Ticketmaster Discovery API + artist searches
│   ├── university.py    # MIT + Harvard calendar scrapers
│   ├── boston_calendar.py # Boston Calendar, Do617, ArtsBoston scrapers
│   ├── newsletters.py   # Extract events from newsletter HTML via Claude
│   └── aggregator.py    # Parallel discovery + deduplication
├── ranking/
│   ├── ranker.py        # 7-dimension scoring with vibe-based weights
│   └── bucket_list.py   # Seasonal activity suggestions
├── email/
│   ├── composer.py      # Jinja2 HTML email template
│   └── sender.py        # Gmail SMTP
└── dashboard/
    └── app.py           # FastAPI dashboard
```

## Cost

Each full run costs ~$3-4 in Claude API calls (mostly ranking ~1100 events in batches of 40). Uses `claude-sonnet-4-20250514` by default. Configurable via `RECOM_CLAUDE_MODEL`.

## Dashboard

Live at **https://recom.arthgupta.dev**

- **Run History** — all runs with event count, top score, cost. WIP runs show a progress banner with ranking completion percentage.
- **Run Detail** — interest profile, source stats, all events with 7-dimension score breakdown, cost breakdown
- **Calendar View** — top 10 events per day with "I went" tracking buttons. Midnight events show as "All day".
- **Interests** — extracted interest profile with source signals (YouTube/Spotify/Manual), confidence bars, bucket list
- **Attended** — events you've marked as attended (feedback loop)
- **Join** — onboarding page for new users (Spotify OAuth, calendar subscription)
- **iCal Feed** — `/feed.ics` for calendar subscriptions (strong matches only by default)

### Email digest

Weekly HTML email with:
- Top 10 picks with clickable titles linking to event pages
- Clubs, classes & memberships section
- Bucket list suggestions (seasonal, from `bucket_list.txt`)
- All remaining events organized by day (top 10 per day)
- Header links to dashboard run detail page
- Footer links to dashboard and calendar subscription

### Multi-user support

Multiple users can each get personalized recommendations with their own:
- Spotify/YouTube/Gmail OAuth tokens
- Interest keywords (`my_interests.txt`)
- Bucket list (`bucket_list.txt`)
- Location (city + zip code)
- Email address for digest delivery

```bash
uv run recom --user 2         # run for specific user
uv run recom --all-users      # run for all active users (used by cron)
```
