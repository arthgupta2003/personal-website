# recom

A personal weekly event recommender for the Boston/Cambridge area. Learns your interests from YouTube, Spotify, and email newsletters, discovers local events from 7+ sources, ranks everything with Claude using 7 scoring dimensions, and sends you a weekly email digest.

Live dashboard: **https://recom.arthgupta.dev**

## Quick start

```bash
./start.sh          # launches everything in a tmux session
./start.sh stop     # kills the tmux session
./start.sh status   # shows what's running
```

This starts 3 services in tmux panes:
- **dashboard** — FastAPI on port 8000
- **tunnel** — Cloudflare Tunnel (recom.arthgupta.dev + code.arthgupta.dev)
- **code-web** — Claude Code Web UI on port 32352 (accessible at code.arthgupta.dev)

Attach to the tmux session with `tmux attach -t recom`.

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
3. **Discover events** — Queries 14 sources in parallel:
   - Eventbrite API (general events, 10mi radius)
   - Meetup GraphQL API (groups, community)
   - Ticketmaster API (concerts, artist-specific searches for your Spotify artists)
   - Bandsintown API (concert discovery)
   - Dice.fm (electronic/live music)
   - Resident Advisor (club/electronic events)
   - University calendars (MIT, Harvard, Northeastern, Tufts, BU, Brandeis, Wellesley, MassArt, Emerson, Babson)
   - Museums (ICA, MFA, Gardner, Harvard Art Museums, etc.)
   - Outdoor/nature (hikes, day trips, state parks)
   - The Boston Calendar + TimeOut Boston (scrapers)
   - Gmail newsletters (Claude extraction)
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

### Backtester (quant-style signal analysis)

The ranking pipeline is treated like an alpha model. A built-in backtester evaluates signal quality against realized user behavior — the same way a quant evaluates trading signals against realized PnL.

| Quant concept | Recom equivalent |
|---|---|
| Alpha signal | Scoring dimension (interest, social, urgency, etc.) |
| Signal weight | Vibe weight vector |
| Universe | Event pool from scrapers |
| Portfolio construction | Top-N event selection (keep threshold) |
| Transaction costs | Logistics score (distance/time friction) |
| Realized PnL | Attended events + star ratings |
| Sharpe ratio | Attend rate × avg rating / variance |
| Turnover | New events surfaced week-to-week |

**Signal attribution** — For each dimension, compute hit rate, miss rate, and lift vs baseline. Which signals actually predict what the user does?

**Information Coefficient (IC)** — Spearman rank correlation between each signal score and actual attendance/rating. IC > 0.1 = useful signal, IC < 0.02 = noise. Tracked over time to detect signal decay.

**Decay analysis** — Different signals predict better at different horizons. Urgency dominates for events <3 days out; interest dominates for events >7 days out. IC computed per days-until-event bucket.

**Weight optimization** — Logistic regression on attended history to find optimal vibe weights. Shows current vs suggested weights with confidence intervals. Requires ~30+ attended events per vibe to be meaningful.

**Backtest report** — Per-run precision/recall at current threshold, precision@K, false positive/negative analysis, and threshold sweep curve to find optimal keep cutoff.

## Setup

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22 (for claude-code-web; `nvm use 22`)
- API keys: Anthropic (required), Ticketmaster (recommended), Eventbrite/Songkick (optional)
- OAuth credentials: Google Cloud (YouTube + Gmail), Spotify
- Gmail app password for SMTP sending

### Install

```bash
git clone https://github.com/arthgupta2003/personal-website.git && cd personal-website
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
uv run recom --all-users  # run for all active users
uv run recom-dashboard    # start dashboard on localhost:8000
uv run python -m recom.daily              # send daily email
uv run python -m recom.daily --all-users  # daily email for all users
```

### Cron (weekly, Saturday 9am)

```bash
bash scripts/install_cron.sh
```

## Services & startup

### tmux startup (recommended)

The `start.sh` script manages all services in a single tmux session:

```bash
./start.sh          # start all services
./start.sh stop     # stop everything
./start.sh status   # check what's running
tmux attach -t recom   # attach to see logs
```

Services started:
| Pane | Service | Port | Public URL |
|------|---------|------|------------|
| dashboard | `uv run recom-dashboard` | 8000 | recom.arthgupta.dev |
| tunnel | `cloudflared tunnel run` | — | routes to 8000 + 32352 |
| code-web | `npx claude-code-web` | 32352 | code.arthgupta.dev |

### Claude Code Web (remote dev from phone)

Access Claude Code from any browser (including phone) at **https://code.arthgupta.dev**.

- Requires Node 22 (`nvm use 22`) — Node 23 has a broken `node-pty`
- Auth token is printed on startup (check the tmux `code-web` pane)
- Spawns new Claude Code sessions (not attached to existing ones)

### launchd services (alternative)

Two plist files in `~/Library/LaunchAgents/` for auto-start on login:

| Service | Plist | What it does |
|---------|-------|-------------|
| Dashboard | `com.recom.dashboard.plist` | Runs `uv run recom-dashboard` on port 8000 |
| Tunnel | `com.recom.tunnel.plist` | Runs `cloudflared tunnel run recom-dashboard` |

```bash
launchctl load ~/Library/LaunchAgents/com.recom.dashboard.plist
launchctl load ~/Library/LaunchAgents/com.recom.tunnel.plist
launchctl list | grep recom   # check status
```

If using tmux startup, you don't need launchd (and vice versa).

### Tunnel config

`~/.cloudflared/config.yml`:
```yaml
tunnel: <tunnel-id>
credentials-file: ~/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: recom.arthgupta.dev
    service: http://localhost:8000
  - hostname: code.arthgupta.dev
    service: http://localhost:32352
  - service: http_status:404
```

Add more subdomains:
```bash
cloudflared tunnel route dns recom-dashboard <subdomain>.arthgupta.dev
# Then add to ingress in config.yml
```

### Keeping the server alive

**System Settings → Displays → Advanced → Prevent automatic sleeping on power adapter** → turn ON

## Social features

### RSVP

Users can RSVP (Going / Maybe / Can't) to events on the calendar and in daily emails.

- Each user gets a unique token (8-char hex) for identity
- Visit the calendar with `?u=<token>` to see RSVP buttons
- RSVPs show as colored pills on event cards (visible to all users)
- Daily emails include Going/Maybe links that set RSVPs via one click

### Groups (shared calendar)

Create groups of friends who share a blended calendar:

1. Visit `/group/create?u=<token>` to create a group
2. Invite friends by email via the group page
3. Group calendar at `/group/<slug>` shows union of all members' events with RSVP badges
4. Subscribe to `/group/<slug>/feed.ics` for a shared iCal feed

### Calendar subscription

```
https://recom.arthgupta.dev/feed.ics
```

- Default: only strong matches (score >= 55)
- Use `?min_score=25` for all recommended events
- Events show as `[72] Event Title` in your calendar

## Multi-user support

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

## Project structure

```
src/recom/
├── main.py              # pipeline orchestrator
├── config.py            # pydantic-settings from .env
├── models.py            # data models (Event, RankedEvent, InterestProfile, etc.)
├── db.py                # SQLite (users, events, rankings, RSVPs, groups)
├── daily.py             # daily email sender (per-user with RSVP links)
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
│   ├── composer.py      # Jinja2 HTML email template (weekly + daily)
│   └── sender.py        # Gmail SMTP
└── dashboard/
    └── app.py           # FastAPI dashboard + RSVP API + group pages
```

## Cost

Each full run costs ~$3-4 in Claude API calls (mostly ranking ~1100 events in batches of 40). Uses `claude-sonnet-4-20250514` by default. Configurable via `RECOM_CLAUDE_MODEL`.

## Dashboard

Live at **https://recom.arthgupta.dev**

Cookie-based auth via magic links (`?u=<token>`). All pages use a shared nav bar with links to every section.

| Route | Auth | Description |
|-------|------|-------------|
| `/` | public | Week calendar with RSVP buttons, hot-day strip, heatmap |
| `/run/<id>` | public | Run detail: source stats, interest profile, all events with score breakdown |
| `/interests` | public | Interest profile: signals, confidence bars, bucket list |
| `/attended` | public | Events marked as attended (personal attendance log) |
| `/taste` | public | Elo-style taste ranker — swipe events to train the ranking model |
| `/groups` | public | List all groups |
| `/group/<slug>` | public | Shared group calendar with RSVP badges for all members |
| `/group/create` | public | Create a new group |
| `/landing` | public | Marketing landing page |
| `/login` | public | Magic link login |
| `/feed.ics` | public | iCal feed (`?min_score=55` default; `?u=token` for personal) |
| `/group/<slug>/feed.ics` | public | Group iCal feed |
| `/venues` | auth | Venue tracker: pin favorite venues, see upcoming events at each |
| `/search` | auth | Full-text event search with AI re-ranking |
| `/budget` | auth | Budget tracker: log spend per event, see totals |
| `/travel` | auth | Travel planner: upcoming trips with auto-pulled nearby events |
| `/profile` | auth | User settings: name, location, email, notification prefs |
| `/admin` | public | Admin: run pipeline, view all users, recent runs |
| `/admin/sources` | public | Source health: last fetch time, event counts, errors |

### Taste ranker

`/taste` presents pairs of events and asks which you'd rather attend. Ratings feed into an Elo system that influences the ranking weights for your next pipeline run. Streak shown at top.

## Testing

After any dashboard change, run the smoke test:

```bash
bash scripts/smoke_test.sh                         # unauthenticated checks only
bash scripts/smoke_test.sh test@example.com        # + authenticated flow (creates test user)
```

The authenticated flow creates a test user, grabs their token, and hits every route with a cookie. All 23 checks must pass before considering a dashboard change done.
