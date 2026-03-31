# Calyx Design Language

## Identity

**Calyx** = the green sepals that protect a flower bud before it opens. The app helps things bloom — events, plans, friendships.

**Voice:** Warm but direct. Like a friend who's really good at finding things to do. Not corporate, not cutesy.

## Color Palette

### Primary: Sage Green
- **Main:** `#4a6741` — logo, nav active, buttons, links, high-score badges, toggles
- **Dark:** `#3a5334` — hover states, button pressed, text on green backgrounds
- **Light:** `#edf2eb` — active nav background, tag backgrounds, subtle fills
- **Tint:** `#f4f7f3` — hover backgrounds, light card fills

### Secondary: Terracotta
- **Main:** `#c4734f` — social vibe accent, "show more" buttons, search gap alerts, price display
- **Light:** `#fdf5f2` — hover backgrounds for terracotta elements

### Neutral
- **Text:** `#111` body, `#1a1a1a` headings, `#555` secondary, `#888` tertiary
- **Borders:** `#e0e0e0` cards/dividers, `#eee` subtle separators, `#ccc` inputs
- **Background:** `#fff` (pure white, not cream)

### Score Colors
- **High (70+):** Sage green `#4a6741` bg, white text
- **Mid (50-69):** `#e8ede7` bg, `#4a6741` text
- **Low (<50):** `#f5f5f5` bg, `#999` text

### Vibe Colors (event cards, email)
- **Social:** Terracotta `#c4734f`
- **Intellectual:** Sage `#4a6741`
- **Mixed:** Steel blue `#5b7fa5`

### Member Avatar Colors (cycle through)
`#4a6741`, `#c4734f`, `#5b7fa5`, `#8b6b47`, `#7a5c8a`, `#5a8a6e`

## Typography

**Font:** Inter (Google Fonts), fallback to system sans-serif.

- **h1:** 2rem, weight 800, letter-spacing -0.5px, color #1a1a1a
- **h2 (section headers):** 11px, weight 700, uppercase, letter-spacing 2px, color #4a6741
- **Body:** 14px, weight 400, line-height 1.55
- **Nav links:** 13px, weight 500, uppercase, letter-spacing 0.3px
- **Buttons:** 13px, weight 700, uppercase, letter-spacing 0.5px

## Shape Language

**Sharp.** No rounded corners on cards, buttons, inputs, or badges. `border-radius: 0` everywhere. The only exceptions:
- Toggle switches (inherently round)
- Map markers (circles)
- Member avatars in the map

This is deliberate — the sharp edges give the design authority and make the botanical green feel modern, not whimsical.

## Components

### Nav Bar
- White background, sage green bottom border (2px)
- Logo: leaf SVG + "calyx" in sage green, weight 800
- Active link: sage green text + green bottom border underline
- Order: Discover | Groups | You

### Buttons
- **Primary:** Sage green bg, white text, uppercase
- **Secondary:** White bg, sage green text, sage green border
- No hover animations beyond color change (no lift, no shadow)

### Cards
- White bg, `1px solid #e0e0e0`, no shadow, no radius
- 24px padding, 24px margin-bottom

### Event Cards (Discover)
- Left border: 3px, colored by score (green/terracotta/gray)
- Going events: 4px green left border + `#f8faf7` background
- Hover: `#f8faf7` background
- "via [source]" in 10px gray at bottom

### Tags (Taste Profile)
- Left border accent (3px) cycling through avatar colors
- Background: color at 10% opacity
- Font: 13px, weight 600

### Day Headers (Discover list)
- Sticky, white background
- Sage green text, uppercase, 1.5px letter spacing
- 2px sage green bottom border

### Filter Chips
- Uppercase, weight 600, 11px
- Active: sage green bg, white text
- Inactive: white bg, gray border

## Map

- **Tiles:** CARTO light (`basemaps.cartocdn.com/light_all`)
- **Top picks (70+):** Star SVG markers in sage green
- **Mid events:** Small terracotta circles
- **Low events:** Tiny gray circles
- **Clusters:** Sage green numbered circles with white border
- **Home marker:** Small sage green dot with white border + shadow
- **Time filter:** "Today | This Week | All" floating buttons at bottom center

## Email

Same palette as dashboard. Key differences for email compatibility:
- Inline styles only (no CSS classes)
- Table-based layout
- Hero header: dark green gradient (`#4a6741` to `#2d3f27`)
- White text on hero, sage green accents in body
- Sharp edges (`border-radius: 0`)
- "Add to my week" button: sage green bg, white text

## Decorative Elements

- **Leaf SVG** in nav logo (layered calyx/petal shape)
- **Botanical watermark** on You page (large faded leaf, top right, opacity 0.07)
- Tags cycle through 6 botanical colors for visual variety

## What NOT to Do

- No rounded corners (except toggles/avatars)
- No shadows on cards or buttons
- No gradients on the dashboard (only in email hero)
- No emoji in UI text (fine in data like match reasons)
- No purple — the old palette is fully retired
- No cream/warm backgrounds — pure white only
- No soft/whimsical styling — this is sharp + botanical, not cottagecore
