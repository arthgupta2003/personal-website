# Calyx — product reference

The single source of truth for "what does this app do, who's it for, and what
exists today." Read this before diving into routes. Update it when you ship
something that changes the surface area.

Sister docs:
- `CLAUDE.md` — repo / build / deploy instructions
- `DESIGN.md` — visual + tone guidelines
- `todo.txt` — active work queue
- `ADMIN_TODO.md` — operator chores

---

## What is Calyx

Calyx is a **group calendar that finds events you'll actually go to.**

Two halves working together:
1. **Discover** — a weekly pipeline ingests your Spotify, YouTube, Gmail
   newsletters, and 13+ Boston-area event sources, then has Claude rank ~1100
   events on 7 dimensions to surface the ~5–10 you'd actually love.
2. **Groups** — a Partiful-meets-Google-Calendar shared calendar for friends.
   Add events manually or from the discover feed; RSVP; subscribe in your
   real calendar; share single-event links that one-tap-RSVP via Google sign-in.

Beta. Boston/Cambridge only. One real user (the developer) and a few friends
trickling in. No external users yet — break things freely, no migrations.

---

## Personas

### Owner (the developer)
Runs the pipeline. Gets weekly digest emails. Uses `/calendar` to pick what to
do. Hosts groups. Cares about everything.

### Friend (invited via link)
Got a group link or single-event link from a friend. Probably opens it **on
phone**. Doesn't care about Spotify ingestion or rankings — just wants to RSVP
and see what their group's doing. Their experience should feel like
**when2meet, but better**: zero setup, sign in with Google, done.

### Future: Discoverer (not yet a real persona)
Someone who finds Calyx, wants personalized event recs, signs up, connects
Spotify/YouTube, gets weekly emails. Today this is just the Owner. Would
require: signup-without-invite that connects integrations, location flexibility
(non-Boston), payment maybe.

---

## Core user journeys

### J1 — Weekly discovery (Owner)
1. Saturday 9am: cron runs pipeline → ingests, ranks ~1100 events, emails digest
2. Owner opens digest in Gmail on phone or laptop
3. Taps a "Going / Maybe" button → opens `/api/rsvp-link` (works without signin via per-user `?u=token`)
4. Optional: opens `/calendar` to browse list/week view, filter by score, RSVP from card

### J2 — Group invite (Friend)
1. Owner: `/group/{id}` → "Copy invite link" button → texts/shares URL
2. Friend opens `/group/{id}/join/{invite_code}` on phone
3. Sees group preview (events, members) + "Continue with Google" CTA
4. Taps Google → consents → bounces back through `/auth/google/callback` → cookie set
5. Invite handler auto-adds them as a member, redirects to `/group/{id}` with success banner
6. Friend can now RSVP to events, add new events, see availability

### J3 — Single event share (Friend, narrowest path)
1. Group member: taps "share" on a manual event → `/share/event/{gid}/{eid}/{invite}` URL is copied (or native share-sheet on iOS)
2. Friend taps URL → sees event preview (title, time, location, who's going)
3. Friend taps `Going` button → bounces through Google sign-in (next param preserves the action)
4. Callback returns → action handler auto-joins group + records RSVP in one shot
5. Lands on `/group/{id}` with "RSVP saved (going)" banner

### J4 — Calendar subscription (anyone)
1. User: `/group/{id}` → "Add to Apple Calendar" / "Google Calendar" button
2. Calendar app subscribes to `webcal://calyx.arthgupta.dev/group/{id}/feed.ics`
3. Group events (including new ones) appear in their real calendar, no app needed
4. Personal version: `/u/{token}/feed.ics` includes top discovered events too

### J5 — Manually add event to group
1. Member: taps `+ Add event` on group page → panel slides down
2. Paste a URL (lu.ma, eventbrite, etc.) → "Autofill" → Claude extracts title/date/time/location
3. Or: tap a quick-pick chip ("Tonight 7pm", "Fri 8pm") + fill title manually
4. Tap "Add event" → posted, auto-RSVPs adder as `going`, notifies other members by email

---

## Sitemap (every route, grouped)

### Public (no auth)
| Route | Purpose |
|---|---|
| `/landing` | Marketing page (probably stale, low traffic) |
| `/login` | "Continue with Google" button |
| `/join` | 303 → `/login` (legacy) |
| `/feed.ics` | Public top-event iCal feed (no per-user customization) |

### Auth flow
| Route | Purpose |
|---|---|
| `/auth/google/login?next=…` | Start OAuth, set state + verifier cookies, 302 to Google |
| `/auth/google/callback` | Receive code, exchange for ID token, find-or-create user, set session cookie |
| `/auth/logout` | Clear cookie → `/login` |
| `/auth/spotify`, `/callback` | Spotify connect (per-user, Owner only really) |
| `/auth/youtube`, `/callback/youtube` | YouTube connect |

### Discover (member, personal)
| Route | Purpose |
|---|---|
| `/` | Authed → `/calendar`, unauth → `/landing` |
| `/calendar` | Main "Discover" feed: list + week views, filter by score, RSVP, modal preview |
| `/api/search` | Claude-powered NL event search (with web_search fallback) |
| `/api/rsvp` | RSVP from `/calendar` (POST JSON) |
| `/api/rsvp/{event_id}` | Read RSVP status |
| `/api/rsvp-link` | One-click email RSVP target (handles `?u=` token + status param) |

### Personalization
| Route | Purpose |
|---|---|
| `/taste-profile` | Owner-style: shown taste, Elo matchups, ratings, settings |
| `/onboarding/{token}` | (Legacy) Forced 10-question Elo onboarding — no longer in main flow |
| `/profile` | Settings: home location, work hours, integrations (Spotify/YT), interests text |
| `/api/profile/update` | Update settings |
| `/api/profile/paste-interests` | Bulk paste interests text |
| `/api/profile/upload-youtube` | Upload Google Takeout YouTube history |
| `/api/steer` | One-click "more like this" / "block" from email |
| `/api/rate` | One-click 1–5 star rating from post-event email |

### Groups
| Route | Purpose |
|---|---|
| `/groups` | List of user's groups + upcoming events |
| `/group/create` | Form to create a new group |
| `/group/{id}` | Group page: members, upcoming events (manual + RSVP'd discoveries), add-event panel |
| `/group/{id}/join` | POST to join (from one-click button on group page when authed but not member) |
| `/group/{id}/join/{invite_code}` | Invite landing — auto-joins authed users, shows Google CTA for unauth |
| `/group/{id}/invite` | POST to email an invite |
| `/group/{id}/feed.ics` | Group iCal subscription |
| `/group/{id}/plan` | Standalone group plan/availability view (older, may be redundant) |
| `/api/group/{id}/add-event` | Create manual event |
| `/api/group/{id}/delete-event` | Remove manual event |
| `/api/group/{id}/rename` | Rename group (inline edit) |
| `/api/group/{id}/leave` | Leave group |
| `/api/group/{id}/mute` | Toggle email notifications |
| `/api/group/{id}/kick` | Remove member (creator only) |
| `/api/group/{id}/delete` | Delete the group (creator only) |
| `/api/group/{id}/rsvp` | RSVP to a group event |
| `/api/group/{id}/availability`, `/grid` | Availability poll endpoints (UI removed; backend still here) |
| `/api/extract-event-url` | Server-side: fetch URL, Claude extracts title/date/time/location |
| `/api/ping-group` | Email all group members about an event |

### Sharing
| Route | Purpose |
|---|---|
| `/e/{event_id}` | Public preview of a *discovered* event (not group-specific) |
| `/share/event/{gid}/{eid}/{invite_code}` | Public preview of a *group's manual event*, with Going/Maybe/No buttons |
| `/share/event/{gid}/{eid}/{invite_code}/rsvp/{status}` | Action: bounces through Google if unauth, then auto-joins group + records RSVP |

### Calendar subscriptions / iCal (machine endpoints)
| Route | Purpose |
|---|---|
| `/feed.ics` | Public top picks |
| `/u/{token}/feed.ics` | Personal feed (top discoveries + RSVPs) |
| `/u/{token}/rsvps.ics` | RSVPs only (Going/Maybe) |
| `/event/{id}.ics` | Single .ics download |
| `/u/{token}/event/{id}.ics` | Same, scoped to user (records "added to calendar" intent) |
| `/u/{token}/event/{id}/added` | Confirmation page after add |
| `/group/{id}/feed.ics` | Group calendar |

### Owner / Admin
| Route | Purpose |
|---|---|
| `/admin` | Health overview |
| `/admin/sources` | Per-source success rate + sparklines |
| `/api/admin/schedule` | Tweak cron times |
| `/api/gcal/status`, `/api/gcal/sync` | Google Calendar sync helpers |
| `/api/attend`, `/api/attend/rate`, `/api/attend-link` | "Did you go? Rate it" flow |

---

## Feature inventory

### Live
- **Pipeline** — weekly cron ingests Spotify/YouTube/Gmail + 13 event sources, ranks via Claude, emails digest
- **/calendar Discover** — list + week views, score badges, RSVP, modal preview, filter chips, search; group events surface alongside discoveries with a "GROUP · {name}" pill
- **Groups** — create, invite link, member list, RSVP, mute notifications, leave, kick, delete, rename
- **Manual events in groups** — URL paste autofill, quick-pick chips ("Tonight 7pm" etc.), end time, notes, recurring; live in unified `events` table with `source='manual'`
- **Edit manual events** — same form, opens populated, "Save changes" submit
- **Email digests** — weekly Saturday, daily, weekend preview, tonight, post-event ratings, admin Sunday (consolidation pending)
- **Auth** — Google sign-in (single source); cookie session 1y; `?u=token` for email click-through convenience
- **iCal feeds** — `/u/{token}/feed.ics` (personal: top picks + RSVPs + group events); `/group/{id}/feed.ics`; `/event/{id}.ics` and `/u/{token}/event/{id}.ics` for individual events
- **Sharing**
  - `/e/{event_id}` for discovered events
  - `/share/event/{gid}/{eid}/{code}` for group events with one-click RSVP (auto-joins group). Has dynamic OG image at `/og/event/{gid}/{eid}/{code}.png` for chat unfurls.
- **Mobile-first design** — 375px audited, 44px tap targets, no horizontal overflow
- **Admin dashboards** — source health, ingest stats (cron managed via `scripts/install_cron.sh`)

### In progress
- (8) Email consolidation — collapse weekend preview + tonight into the daily digest; weekly digest stays as the Saturday pipeline output

### Planned
- Future: GCal read access — see your existing schedule when Calyx ranks
- Location-adaptive sources for non-Boston users
- More niche sources (pottery, climbing, cooking)
- Docker for deployment
- Pipeline observability dashboard

### Removed
- Email/password ("magic link") sign-in → replaced by Google
- "Who's free this week?" availability widget on group page; `/api/group/{id}/availability` GET/POST and tables `availability_polls/_votes/_grids`
- `/group/{id}/plan` (when2meet-style availability page) and `/api/group/{id}/grid`
- `/api/group/{id}/rsvp` — folded into `/api/rsvp` (works for both discovered + group events via `event_id`)
- `set_guest_rsvp` and `guest_rsvps` table — Google sign-in is mandatory now
- `/onboarding/{token}` (forced 10-Q taste matchup); `/join` (was 303 → /login)
- `/feed.ics` (public no-token feed) — `/u/{token}/feed.ics` is the per-user replacement
- `/u/{token}/rsvps.ics` — same data as `/u/{token}/feed.ics`; calendar clients can filter
- `/api/admin/schedule` and the schedule UI — cron managed via `scripts/install_cron.sh`
- `group_events` **table** — manual events now live in `events` with `source='manual'`, `group_id`, `created_by`, `notes` columns. Migration preserves RSVPs by remapping `event_id`.

---

## Auth model (one-pager)

- **Interactive sign-in** = Google OAuth only. Sets `recom_token` cookie (1y) → identifies user via `_get_current_user` → DB lookup by token.
- **Email convenience links** carry `?u=<user_token>` so a digest email's "Going" button works in one tap, no sign-in detour. The `?u=` token resolution sets the cookie too, so subsequent navigation doesn't need it.
- **iCal subscriptions** use `/u/{token}/feed.ics` style URLs — calendar clients can't OAuth, so the URL itself identifies the user. Token in URL = bearer credential.
- **Admin auth** — currently no specific guard; `/admin` is open. 🚨 Future: add a "is_admin" check.

---

## Open questions

1. **What's the entry point for a new "discoverer" user?** Today, signup creates an account but doesn't connect Spotify/YouTube → user gets generic events without personalization. Should non-invited signups go to a "connect Spotify to get personal recs" page?
2. **Per-group vs. global discovery** — should the group page surface discovered events that match all members' interests? Today it only shows manually-added + RSVP'd events.
3. **Admin auth.** None right now. Fix before going public.
4. **Push notifications** — useful for "your friend RSVP'd" or "event in 1 hour"? Web push or just email?
5. **Email consolidation final shape** — daily picks + weekly Sat digest only? Or keep tonight as a separate Fri/Sat afternoon nudge?

---

## Conventions

- One file: `src/calyx/dashboard/app.py` holds nearly all routes. Big, but searchable.
- Templates inline (f-strings + `<style>` blocks), no Jinja for dashboard pages.
- DB is one SQLite file (`calyx.db`).
- No tests for handlers — `scripts/smoke_test.sh` and `scripts/browser_test.py` are the gate.
