# UI Spec — Dashboard Redesign

**Status:** Ready for implementation  
**Date:** 2026-07-20  
**Companion doc:** [`dashboard-redesign.md`](dashboard-redesign.md) — backend endpoint, data model, time windows

---

## Design philosophy

Three principles that govern every decision:

1. **Surface recedes, data leads.** The page background and card chrome are neutral — color belongs to the data, not the container.
2. **One dominant element.** The spend number owns the top of the screen. Everything else is secondary.
3. **Color as grammar.** Each metric has one color used consistently: slate blue for protein, forest green for fiber, warm sienna for sodium. Status (good/low) is encoded separately via a small status dot, never by reusing metric colors.

---

## Color tokens

| Token | Value | Usage |
|---|---|---|
| `brand-green` | `#2D6A4F` | Hero background, action icons, accent |
| `hero-bg` | `#2D6A4F` | Full-bleed hero section |
| `page-bg` | `#F4F4F4` | Below-the-fold background |
| `card-white` | `#FFFFFF` | Action strip, recent orders |
| `card-nutrition` | `#F4FBF7` | Nutrition card only — barely-green tint |
| `text-primary` | `#1C1C1E` | Body text, row labels |
| `text-secondary` | `#8E8E93` | Sub-labels, values |
| `text-dim` | `#5A5A5F` | Nutrition section label |
| `calories-orange` | `#FF6B00` | Calorie badge icon + text |
| `nutrition-protein` | `#4A7FA5` | Protein dot fill (muted slate blue) |
| `nutrition-fiber` | `#2D6A4F` | Fiber dot fill (brand forest green) |
| `nutrition-sodium` | `#8B6336` | Sodium dot fill (warm sienna) |
| `status-good` | `#40916C` | Status dot: on track / under limit |
| `status-low` | `#C87941` | Status dot: below target |
| `border-card` | `rgba(0,0,0,.06)` | Card borders (white cards) |
| `border-nutrition` | `rgba(45,106,79,.14)` | Card border (nutrition card) |
| `border-nutrition-row` | `rgba(45,106,79,.09)` | Divider between nutrition rows |

---

## Typography

All text uses the system font stack: `-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`.

| Role | Size | Weight | Notes |
|---|---|---|---|
| Hero number | 48px | 900 | `letter-spacing: -2px`, `font-variant-numeric: tabular-nums` |
| Hero sub-label | 12px | 400 | `color: rgba(255,255,255,.5)` |
| Hero stat number | 16px | 800 | `font-variant-numeric: tabular-nums` |
| Hero stat label | 9px | 400 | `color: rgba(255,255,255,.4)` |
| Section label | 10px | 700 | `text-transform: uppercase`, `letter-spacing: 0.1em` |
| Nutrition section label | 10px | 800 | `text-transform: uppercase`, `letter-spacing: 0.1em`, `color: #5A5A5F` |
| Action button label | 11px | 700 | |
| Action button sub | 9px | 400 | `color: #8E8E93` |
| Nutrition row label | 11px | 600 | `color: #1C1C1E` |
| Nutrition row value | 10px | 400 | `color: #8E8E93`, `font-variant-numeric: tabular-nums` |
| Order name | 12px | 500 | `color: #1C1C1E` |
| Order amount | 13px | 800 | `color: #1C1C1E` |

---

## Layout (top to bottom)

```
┌─────────────────────────────────────┐
│  HERO  (bg: #2D6A4F, full-bleed)   │
│   🏠 PantryPilot          ⚙        │
│                                     │
│   [ALERT BANNER — conditional]      │
│                                     │
│   THIS WEEK                         │  ← UPPERCASE, 10px, 45% opacity white
│   ₹1,840                            │  ← 48px, 900 weight
│   of ₹2,000 budget                  │  ← 12px, 50% white
│   [═══════════════════════    ]     │  ← 3px progress, white fill
│   🔥 12,400 / 14,000 kcal          │  ← calorie badge, orange
│                                     │
│   ──────────────────────────        │  ← 0.5px divider, 12% white
│   19          ₹420       4/5        │  ← 16px stat numbers
│   orders      avg order  resolved   │  ← 9px labels
├─────────────────────────────────────┤
│  PAGE  (bg: #F4F4F4)               │
│                                     │
│  ┌─────────────────────────────┐   │
│  │  ↻ Flow  │ 📅 Rout │ + QO  │   │  ← action strip, white card
│  └─────────────────────────────┘   │
│                                     │
│  NUTRITION                          │  ← 10px, uppercase, #5A5A5F
│  ┌─────────────────────────────┐   │
│  │ ● Protein  ●●●●●●○  48/50g │   │  ← tinted card #F4FBF7
│  │ ● Fiber    ●●●○○○○  12/25g │
│  │ ● Sodium   ●●●●●○○  1.8/2k │
│  └─────────────────────────────┘   │
│                                     │
│  RECENT ORDERS                      │  ← 10px, uppercase, #8E8E93
│  ┌─────────────────────────────┐   │
│  │ Mon  Lay's, Milk +3  ₹620 ›│   │  ← white card
│  │ Thu  Eggs, Bread     ₹220 ›│
│  │ Mon  Peanuts +1      ₹480 ›│
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

---

## Section specs

### Hero section

- Background: `#2D6A4F`, `padding: 16px 20px 20px`
- No `border-radius` — bleeds to screen edges
- The section following it (page body) has `border-radius: 0` top, rounded on the outer screen frame only

**Logo bar:**
- Left: `ti-home` icon (15px) + "PantryPilot" (14px, 700, `rgba(255,255,255,.75)`)
- Right: `ti-settings` icon (16px, `rgba(255,255,255,.35)`)
- `margin-bottom: 18px`

**THIS WEEK label:** 10px, 700, uppercase, `letter-spacing: 0.1em`, `rgba(255,255,255,.45)`

**Spend number:** 48px, 900, white, `letter-spacing: -2px`, `line-height: 1`

**Sub-label:** "of ₹{budget} budget" — 12px, 400, `rgba(255,255,255,.5)`, `margin-top: 4px`

**Progress bar:**
- Track: 3px, `rgba(255,255,255,.15)`, `border-radius: 99px`, `margin-top: 12px`
- Fill: `rgba(255,255,255,.72)`, width = `(total_spend / budget_max) * 100%`, capped at 100%
- `transition: width 0.4s ease`
- If `budget_max` is null: hide the bar entirely

**Calorie badge:** `display: inline-flex`, `margin-top: 11px`
- `ti-flame` icon (13px) + number (12px, 700) — both `#FF6B00`
- "/ {target} kcal" suffix in `rgba(255,107,0,.55)`
- Hide entirely when `has_nutrition_data = false`

**Stats footer:**
- `margin-top: 16px`, `padding-top: 14px`
- `border-top: 0.5px solid rgba(255,255,255,.12)`
- 3-column grid: "orders placed" / "avg order" / "items resolved"
- Column separator: `border-left: 0.5px solid rgba(255,255,255,.12)`
- Number: 16px, 800, `rgba(255,255,255,.9)`
- Label: 9px, 400, `rgba(255,255,255,.4)`
- Show `—` for avg and resolved when `total_orders = 0`

---

### Alert banners (conditional, above THIS WEEK label)

Sit inside the hero section, between the logo bar and the THIS WEEK label. Only one shows at a time; neither shows in the normal state.

**Basket pending (`flow.basket_pending = true`):**
- Background: `rgba(255,255,255,.13)`, border: `0.5px solid rgba(255,255,255,.18)`
- White dot (7px) + "Basket ready for review" (12px, 700) + sub-line "Tap to confirm your weekly order" (10px)
- `ti-arrow-right` on the right
- Tapping navigates to `/flow`

**Planning in progress (`flow.in_progress = true`):**
- Background: `rgba(245,158,11,.2)`
- Pulsing amber dot (7px, `animation: pulse 1.5s infinite`) + "Building your basket…" (12px, 700, `rgba(255,255,255,.95)`) + sub-line (10px)
- No tap target

**Placing order (`flow.placing_order = true`):**
- Same amber treatment as planning, sub-line: "Placing your order…"

---

### Action strip

Single white card (`#FFFFFF`), `border-radius: 12px`, `border: 0.5px solid rgba(0,0,0,.06)`, `margin-bottom: 12px`.

3-column CSS grid. Column separators: `border-left: 0.5px solid rgba(0,0,0,.07)`.

| Column | Icon | Label | Sub-label |
|---|---|---|---|
| Flow | `ti-refresh` | Flow | "Next: {day}" / "Review now" / "Planning…" / "Placing…" |
| Routines | `ti-calendar` | Routines | "{N} active" / "Set up" |
| Quick | `ti-plus` | Quick | "Order now" |

Icons: 17px, `#2D6A4F`. Label: 11px, 700, `#1C1C1E`. Sub-label: 9px, `#8E8E93`.

Sub-label logic for Flow:
- `basket_pending = true` → "Review now"
- `placing_order = true` → "Placing…"
- `in_progress = true` → "Planning…"
- `next_run_at` set → "Next: {short weekday}" (e.g. "Next: Mon")
- None of the above → "Set up"

---

### NUTRITION section

**Section label:** `div.sec-n` — 10px, 800, uppercase, `letter-spacing: 0.1em`, `color: #5A5A5F`, `margin: 0 0 6px 2px`

Slightly heavier than "RECENT ORDERS" label — intentional hierarchy.

**Card:** `background: #F4FBF7`, `border-radius: 12px`, `border: 0.5px solid rgba(45,106,79,.14)`, `margin-bottom: 10px`

Three rows: Protein / Fiber / Sodium. Row divider: `0.5px solid rgba(45,106,79,.09)`.

Each row (`padding: 11px 14px`, `display: flex`, `align-items: center`, `gap: 8px`):

| Part | Spec |
|---|---|
| Status dot | 7px circle before the label (CSS `::before`). Color via `--s` custom property |
| Label | 11px, 600, `#1C1C1E`, `width: 58px` |
| Dot bar | 7 dots × 9px circles, `gap: 4px`, `flex: 1`. Empty: `rgba(45,106,79,.13)` |
| Value | 10px, `#8E8E93`, right-aligned, `width: 58px`, tabular-nums |

**Dot fill colors:**
- Protein: `#4A7FA5` (muted slate blue)
- Fiber: `#2D6A4F` (brand forest green)
- Sodium: `#8B6336` (warm sienna)

**Dot fill count:** `round(min(1, actual / target) * 7)`

**Status dot color (`--s`):**
- Protein ≥ 70%: `#40916C` (green). Protein < 70%: `#C87941` (amber)
- Fiber ≥ 70%: `#40916C`. Fiber < 70%: `#C87941`
- Sodium ≤ 100%: `#40916C` (green — under limit is good). Sodium > 100%: `#C87941`

**Visibility:** Render only when `week.has_nutrition_data = true`. When hidden, no empty state — section disappears entirely.

---

### RECENT ORDERS section

**Section label:** `div.sec` — 10px, 700, uppercase, `letter-spacing: 0.1em`, `color: #8E8E93`

**Card:** `background: #FFFFFF`, `border-radius: 12px`, `border: 0.5px solid rgba(0,0,0,.06)`

Up to 3 rows. Row divider: `0.5px solid rgba(0,0,0,.05)`. Each row `padding: 10px 12px`.

| Part | Spec |
|---|---|
| Day | 10px, 700, `#AEAEB2`, `width: 24px` — short weekday (Mon/Tue…) |
| Name | 12px, 500, `#1C1C1E`, `flex: 1`, truncated with ellipsis |
| Amount | 13px, 800, `#1C1C1E` |
| Chevron | `ti-arrow-right`, 11px, `#AEAEB2` |

Name string: `preview` from API (first 2 product names) + " +{extra_count}" when `extra_count > 0`.

Tapping any row navigates to `/orders`.

**Visibility:** Render only when `recent_orders.length > 0`.

---

## State matrix

| State | Alert banner | Spend | Calorie badge | Nutrition | Orders |
|---|---|---|---|---|---|
| No orders | — | ₹0, 0% bar | Hidden | Hidden | Hidden |
| Orders, no nutrition | — | Real value | Hidden | Hidden | Shown |
| Orders + nutrition | — | Real value | Shown | Shown | Shown |
| Basket pending | Green banner | Real value | Shown if available | Shown if available | Shown if available |
| Planning | Amber banner (pulsing) | Real value | Shown if available | Shown if available | Shown if available |
| Placing order | Amber banner | Real value | Shown if available | Shown if available | Shown if available |

---

## What replaces what

| Old element | Replaced by |
|---|---|
| Three tall green-header cards (Flow, Routines, Quick) | Action strip (single white card, 3 sections, ~44px) |
| Three small stat tiles below cards | Stats row embedded in hero footer |
| No nutrition on dashboard | NUTRITION section |
| Parallel fetches on mount | Single `GET /v1/dashboard` call |

---

## Files to change

| File | Change |
|---|---|
| `app/cockpit/src/app/dashboard/page.tsx` | Full rebuild per this spec |
| `app/cockpit/src/lib/api.ts` | Already has `dashboard.get()` — no change needed |
| `app/pilot/app/api/dashboard.py` | Already implemented — no change needed |
