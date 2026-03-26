# Boston/Cambridge Event Sources Research

Compiled 2026-03-11. Sources verified via web search.

---

## 1. Museums & Galleries NOT Already Covered

The app already covers: ICA Boston, MFA Boston, MIT List Visual Arts Center.

### Isabella Stewart Gardner Museum
- **Events URL**: https://www.gardnermuseum.org/calendar
- **Newsletter**: Sign up via footer at https://www.gardnermuseum.org/
- **Format**: Custom HTML (server-rendered calendar page, scrape-friendly)
- **Notes**: Free First Thursdays, concerts, talks. High-value source.

### Harvard Art Museums
- **Events URL**: https://harvardartmuseums.org/calendar
- **Alt URL**: https://calendar.college.harvard.edu/group/harvard_art_museums/calendar
- **Newsletter**: Footer signup at https://harvardartmuseums.org/
- **Format**: Custom HTML. Also has an API (https://github.com/harvardartmuseums/api-docs) for collections but events may need scraping.
- **Notes**: "At Night" events are particularly relevant for the target audience.

### Museum of Science
- **Events URL**: https://www.mos.org/events
- **Newsletter**: https://www.mos.org/email-signup
- **Format**: Custom HTML
- **Notes**: Special events, themed weekends, after-hours programs.

### Boston Children's Museum
- **Events URL**: https://www.bostonchildrensmuseum.org/calendar/
- **Newsletter**: Check footer at https://bostonchildrensmuseum.org/
- **Format**: Custom HTML
- **Notes**: Lower priority (family-oriented), but has adult events occasionally.

### Peabody Essex Museum (Salem)
- **Events URL**: https://my.pem.org/events
- **Newsletter**: Check footer at https://www.pem.org/
- **Format**: Custom ticketing system (Tessitura TNEW)
- **Notes**: World-class museum, worth the trip. Ticketing API may be scrapeable.

### deCordova Sculpture Park & Museum (Lincoln)
- **Events URL**: https://thetrustees.org/place/decordova/ (managed by The Trustees)
- **Newsletter**: Via The Trustees newsletter at https://thetrustees.org/
- **Format**: Custom HTML (Trustees platform)
- **Notes**: Outdoor sculpture park, seasonal events, yoga, snowshoe tours.

### MIT Museum
- **Events URL**: https://mitmuseum.mit.edu/ (events section)
- **Alt URL**: https://calendar.mit.edu/department/museum
- **Newsletter**: Via MIT Museum mailing list on site
- **Format**: Localist (calendar.mit.edu is Localist-powered)
- **Notes**: Cambridge Science Festival is a major annual event.

### Boston Athenaeum
- **Events URL**: https://events.bostonathenaeum.org/
- **Alt URL**: https://bostonathenaeum.org/whats-on/
- **Newsletter**: Via community portal at https://community.bostonathenaeum.org/
- **Format**: Custom (Salesforce-based community portal)
- **Notes**: Author talks, concerts, workshops. Membership required for some events.

### Somerville Museum
- **Events URL**: https://www.somervillemuseum.org/calendar-events
- **Newsletter**: Check site footer
- **Format**: Custom HTML (likely Squarespace)
- **Notes**: Small but community-focused. Open studios, local history.

---

## 2. Cinemas / Film

### Coolidge Corner Theatre (Brookline)
- **Events URL**: https://coolidge.org/films-events/upcoming-programs
- **Showtimes**: https://coolidge.org/showtimes
- **Newsletter**: Footer signup at https://coolidge.org/
- **Format**: Custom HTML
- **Notes**: Independent cinema, special programs, midnight movies.

### Brattle Theatre (Cambridge)
- **Events URL**: https://brattlefilm.org/
- **Newsletter**: Footer signup at https://brattlefilm.org/
- **Format**: Custom HTML
- **Notes**: Foreign, independent, classic films. Schlock Around the Clock, Bugs Bunny Film Festival.

### Somerville Theatre
- **Events URL**: https://www.somervilletheatre.com/events/
- **Newsletter**: Check site
- **Format**: Custom HTML
- **Notes**: Also hosts concerts and live events, not just films.

---

## 3. Universities NOT Already Covered

Already covered: MIT (Localist), Harvard (Trumba), Northeastern (Localist), MassArt (Localist).

### Boston University (BU) — LOCALIST
- **Events URL**: https://butodayevents.bu.edu/
- **API Pattern**: https://butodayevents.bu.edu/api/2/events (Localist API)
- **Platform**: Localist
- **Notes**: Same Localist API as Northeastern/MassArt. Easy to add.

### Suffolk University — LOCALIST
- **Events URL**: https://events.suffolk.edu/
- **API Pattern**: https://events.suffolk.edu/api/2/events (Localist API)
- **Platform**: Localist
- **Notes**: Downtown Boston campus. Same API pattern.

### Simmons University — LOCALIST
- **Events URL**: https://www.simmons.edu/events
- **Alt URL**: https://events.mcphs.edu/simmons-university-566/calendar
- **Platform**: Localist (shared with MCPHS)
- **Notes**: Fenway area. Localist API available.

### Tufts University — TRUMBA
- **Events URL**: https://events.tufts.edu/
- **RSS Feed**: https://www.trumba.com/calendars/tufts?media=rss (Trumba RSS)
- **Platform**: Trumba
- **Notes**: Trumba provides RSS feeds. Pattern: trumba.com/calendars/{cal_name}?media=rss

### Brandeis University — TRUMBA
- **Events URL**: https://www.brandeis.edu/events/
- **RSS Feed**: Available via Trumba (multiple sub-calendars)
- **Platform**: Trumba
- **Notes**: Multiple Trumba sub-calendars (student activities, arts, athletics, etc.)

### Wellesley College — 25LIVE/COLLEGENET
- **Events URL**: https://www.wellesley.edu/events
- **Alt URL**: https://www.wellesley.edu/public-calendar
- **Platform**: 25Live by CollegeNet
- **Notes**: Custom platform, would need HTML scraping.

### Emerson College
- **Events URL**: https://today.emerson.edu/events/
- **ArtsEmerson**: https://artsemerson.org/calendar/
- **Platform**: Custom (WordPress-based for today.emerson.edu)
- **Notes**: ArtsEmerson is high-value (national/international performances downtown).

### Babson College
- **Events URL**: https://www.babson.edu/about/events/
- **Platform**: Custom HTML
- **Notes**: Entrepreneurship/business focused events.

### Berklee College of Music
- **Events URL**: https://college.berklee.edu/events
- **BPC Calendar**: https://www.berklee.edu/BPC/full-calendar-of-events-at-the-bpc
- **Platform**: Custom
- **Notes**: 1,500+ concerts/year! Very high value for music lovers.

### Wentworth Institute of Technology
- **Events URL**: https://wit.edu/calendar
- **Platform**: Custom
- **Notes**: Lower priority, mostly academic.

---

## 4. Music Venues

Already partially covered via Bandsintown/Ticketmaster/Dice.fm/Resident Advisor/Songkick. These are the venue-specific calendars for direct scraping:

### Crossroads Presents (promoter — multiple venues)
- **All Events**: https://crossroadspresents.com/pages/events
- **Newsletter**: Footer signup at https://crossroadspresents.com/
- **Venues**: Paradise Rock Club, Brighton Music Hall, MGM Music Hall at Fenway
- **Notes**: Single scrape covers 3 venues. High value.

### The Bowery Presents (promoter — multiple venues)
- **Boston Calendar**: https://www.bowerypresents.com/boston/calendar/
- **Newsletter**: https://www.bowerypresents.com/boston/newsletter/
- **Venues**: Roadrunner, The Sinclair, Royale
- **Notes**: Single scrape covers 3 venues. Very high value.

### Individual Venue Calendars
| Venue | URL | Capacity |
|-------|-----|----------|
| House of Blues Boston | https://www.houseofblues.com/boston/concert-events | 2,500 |
| Roadrunner | https://roadrunnerboston.com/calendar/ | 3,500 |
| The Sinclair | https://www.sinclaircambridge.com/events | 525 |
| Paradise Rock Club | https://crossroadspresents.com/pages/paradise-rock-club | 933 |
| Brighton Music Hall | https://crossroadspresents.com/pages/brighton-music-hall | 400 |
| Crystal Ballroom | https://www.crystalballroomboston.com/events/ | 500 |
| Royale | https://royaleboston.com/events/ | 1,200 |
| Big Night Live | https://bignightlive.com/calendar | 1,600 |
| MGM Music Hall at Fenway | https://crossroadspresents.com/pages/mgm-fenway-music-hall | 5,000 |
| Leader Bank Pavilion | https://www.leaderbankpavilion.com/shows | 5,000 |
| Somerville Theatre | https://www.somervilletheatre.com/events/ | 900 |

**Recommendation**: Scraping Crossroads Presents + Bowery Presents covers 6 of these venues. The remaining (House of Blues, Big Night Live, Leader Bank Pavilion) are likely already captured by Bandsintown/Ticketmaster.

---

## 5. Comedy

### Improv Asylum
- **Events URL**: https://improvasylum.com/events/ or https://calendar.improvasylum.com/
- **Newsletter**: Footer signup at https://improvasylum.com/
- **Format**: Custom (Tixr ticketing)
- **Notes**: Daily improv shows. Also manages Laugh Boston.

### Laugh Boston
- **Events URL**: https://calendar.laughboston.com/
- **Newsletter**: Via Improv Asylum
- **Notes**: Stand-up comedy, 300 seats. Changing ticketing platforms in 2026.

### Nick's Comedy Stop
- **Events URL**: https://www.nickscomedystop.com/event-list
- **Newsletter**: Check site
- **Format**: Custom HTML
- **Notes**: Boston's longest-running comedy club. Theater District.

### ImprovBoston — CLOSED (2023)
- Permanently closed after 40 years. Remove from consideration.

---

## 6. Performing Arts / Theater

### American Repertory Theater (A.R.T.)
- **Events URL**: https://americanrepertorytheater.org/shows-events/
- **Tickets**: https://ticket.americanrepertorytheater.org/events
- **Newsletter**: Footer signup at https://americanrepertorytheater.org/
- **Format**: Custom (Tessitura TNEW ticketing)
- **Notes**: Harvard-affiliated, Tony Award winner. Very high value.

### ArtsEmerson
- **Events URL**: https://artsemerson.org/calendar/
- **Newsletter**: Check site footer
- **Format**: Custom
- **Notes**: International performances, downtown Boston.

### Huntington Theatre
- **Events URL**: https://www.huntingtontheatre.org/plays-and-events/
- **Season Calendar**: https://www.huntingtontheatre.org/season/calendar/
- **Newsletter**: Footer signup at https://www.huntingtontheatre.org/
- **Format**: Custom HTML
- **Notes**: Boston's leading professional theatre. 150+ awards.

### Boston Lyric Opera
- **Events URL**: https://blo.org/season/
- **Newsletter**: Check site footer
- **Format**: Custom
- **Notes**: New England's largest opera company. 50th anniversary 2026-27.

### Boston Ballet
- **Events URL**: https://www.bostonballet.org/home/tickets-performances/
- **Newsletter**: Check site footer
- **Format**: Custom
- **Notes**: Major productions at Citizens Bank Opera House.

### Boston Symphony Orchestra (BSO)
- **Events URL**: https://www.bso.org/events
- **Newsletter**: https://www.bso.org/newsletter
- **Format**: Custom
- **Notes**: Symphony Hall + Tanglewood. Very high value.

### Global Arts Live
- **Events URL**: https://www.globalartslive.org/
- **Newsletter**: Check site footer
- **Format**: Custom
- **Notes**: 60+ concerts/year across 15+ venues. World music, jazz, dance.

### Boston Center for the Arts (BCA)
- **Events URL**: https://bostonarts.org/
- **Newsletter**: Check site footer
- **Format**: Custom
- **Notes**: Multiple resident companies, SoWa area.

---

## 7. Community / City Calendars

### City of Cambridge
- **Arts Calendar**: https://www.cambridgema.gov/arts/Calendar
- **DHSP Calendar**: https://www.cambridgema.gov/DHSP/newsandevents/Calendar.aspx
- **Format**: ASP.NET (CivicEngage platform)

### City of Somerville
- **Events URL**: https://www.somervillema.gov/calendar
- **Format**: CivicEngage platform
- **Notes**: Open studios, festivals (YUM, ArtBeat).

### Town of Brookline
- **Events URL**: https://www.brooklinema.gov/Calendar.aspx
- **Rec Dept**: https://www.brooklinerec.com/calendar.aspx
- **Format**: CivicEngage platform

### City of Boston
- **Events URL**: https://www.boston.gov/events
- **Newsletter**: https://newsletters.boston.gov/subscribe
- **Format**: Custom (Drupal-based)
- **Notes**: City Hall Plaza events, neighborhood events.

### Cambridge Day (blog/aggregator)
- **Events URL**: https://www.cambridgeday.com/events-ahead/
- **Format**: WordPress
- **Notes**: Weekly roundup of Cambridge/Somerville events.

---

## 8. Tech / Startup

### Startup Boston
- **Events URL**: https://www.startupbos.org/directory/events
- **Newsletter**: Check site
- **Notes**: Monthly roundups of startup events.

### dev.events (aggregator)
- **URL**: https://dev.events/meetups/NA/US/MA/Boston/tech
- **Notes**: Tech meetup aggregator, structured data.

### Luma Boston (already in app)
- **URL**: https://luma.com/boston
- **Notes**: Already a source but reportedly returning 0 events. May need fixing.

---

## 9. Food & Drink

### Boston Local Food
- **Events URL**: https://www.bostonlocalfood.org/events
- **Festival**: https://www.bostonlocalfood.org/bostonlocalfoodfestival
- **Notes**: Annual Boston Local Food Festival (September).

### Street Food App (food trucks)
- **Map/Schedule**: https://streetfoodapp.com/boston/map
- **City Schedule**: https://www.boston.gov/departments/small-business-development/city-boston-food-trucks-schedule
- **Notes**: Real-time food truck locations. Possible API.

### Boston Wine & Food Festival
- **Events URL**: https://www.boswineandfoodfestival.com/event-calendar/
- **Notes**: Seasonal event series.

### Dine Out Boston (Restaurant Week)
- **URL**: https://www.bostonusa.com/dine-out-boston/
- **Notes**: Twice yearly (March, August). One-off calendar entries.

---

## 10. Aggregators Worth Monitoring

| Source | URL | Notes |
|--------|-----|-------|
| ArtsBoston Calendar | https://calendar.artsboston.org/ | 700+ arts orgs. THE comprehensive arts calendar. |
| Boston Theatre Scene | https://www.bostontheatrescene.com/shows-and-events/ | Theater-specific aggregator (Huntington-run) |
| Meet Boston (official tourism) | https://www.meetboston.com/events/ | City's official tourism calendar |
| Boston Central | https://www.bostoncentral.com/ | Family + general events |
| Do617 | https://do617.com/ | Already partially used via Boston Calendar |
| BostonShows.org | https://bostonshows.org/ | Concert-specific aggregator |

---

## Priority Recommendations for Implementation

### Tier 1 — High Value, Easy to Add (Localist API)
These use the same Localist API pattern as Northeastern/MassArt:
1. **Boston University** — https://butodayevents.bu.edu/api/2/events
2. **Suffolk University** — https://events.suffolk.edu/api/2/events
3. **MIT Museum** — https://calendar.mit.edu/api/2/events (department filter)

### Tier 2 — High Value, Moderate Effort
4. **Bowery Presents Boston** — covers Roadrunner, Sinclair, Royale
5. **Crossroads Presents** — covers Paradise, Brighton Music Hall, MGM
6. **ArtsEmerson** — international performances
7. **A.R.T.** — Harvard's Tony-winning theater
8. **Gardner Museum** — top museum events
9. **Berklee events** — 1,500 concerts/year
10. **BSO** — Symphony Hall events

### Tier 3 — Good Value, RSS Available
11. **Tufts University** — Trumba RSS feed available
12. **Brandeis University** — Trumba RSS feed available

### Tier 4 — Scraping Required
13. Museum of Science, Peabody Essex, deCordova
14. Huntington Theatre, Boston Ballet, Boston Lyric Opera
15. Comedy venues (Improv Asylum, Nick's, Laugh Boston)
16. Coolidge Corner Theatre, Brattle Theatre
