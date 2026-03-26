# Admin TODO — Manual actions required

Things only you (Arth) can do. Not code changes — account/config/external stuff.

## Urgent

- [ ] **Spotify redirect URI** — Add `https://recom.arthgupta.dev/callback` to your Spotify app's Redirect URIs at https://developer.spotify.com/dashboard. Without this, the "Connect Spotify" button will fail on the callback step.

- [ ] **Pipeline cron not firing** — Last run was March 11 (manually triggered one on March 19). The Saturday 9am cron (`0 9 * * 6`) doesn't fire if your Mac is asleep. Options:
  - Move cron to the Docker container / cloud server
  - Use `launchd` with `StartCalendarInterval` (wakes Mac)
  - Run pipeline from the dashboard's `/admin/pipeline` page manually each week

## Soon (for public alpha)

- [ ] **Domain for Calyx** — Register calyx.app or similar, then:
  - Update `dashboard_url` in `.env` to new domain
  - Update Cloudflare tunnel to point to new domain
  - Update Spotify redirect URI to new domain
  - Code rebrand is done (UI, emails, OG tags all say "Calyx")

- [ ] **OG image** — Create a 1200x630px `og-image.png` with Calyx branding, host at `/static/og-image.png`

- [ ] **Invite a few friends to test** — Create a group, share the invite link, see if the join flow works end-to-end on their devices (especially mobile Safari)

- [ ] **Email deliverability** — Check spam folder. Gmail SMTP from a personal account may land in spam for non-contacts. Consider:
  - SPF/DKIM records if using custom domain
  - Or switch to Resend/Postmark for transactional email

- [ ] **HTTPS on callback routes** — Verify Cloudflare tunnel handles HTTPS → HTTP correctly for OAuth callbacks

## Nice to have

- [ ] **Google OAuth consent screen** — If you want web-based YouTube/Gmail OAuth (not just Takeout upload), you need to configure the consent screen in Google Cloud Console and add redirect URIs

- [ ] **App Store / PWA** — Add `manifest.json` for add-to-homescreen on mobile. Service worker for offline support.
