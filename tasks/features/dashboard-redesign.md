# PRD — Dashboard Redesign: Stats-First Home Screen

**Status:** Draft  
**Date:** 2026-07-19

---

## Problem

The current dashboard is three identical green-header cards stacked vertically — Flow, Routines, Quick Order — each taking ~120px. Together they consume most of the screen before any data appears. A three-stat row sits below as an afterthought. Nutrition data that we now compute for every order is completely absent.

The home screen should answer "how is my household doing this week?" at a glance, not just "what features exist?"

---

## Design reference

Fitbit and Apple Watch home screens share a structural pattern:
- A **hero metric** (the number that matters most right now) sits at the top, large
- **Progress rings or bars** encode how far you are toward a weekly/daily target
- **Compact stat tiles** in a 2×2 or 3-column grid — dense but scannable
- **Recent activity** as a slim list, not cards
- **Primary actions** are a compact bottom strip, not the dominant element

PantryPilot's equivalent: the hero is weekly calorie coverage; the progress ring is spend vs. budget; the recent activity is last 3 orders; the action strip replaces the three tall cards.

---

## Layout (mobile, top to bottom)

```
┌─────────────────────────────────┐
│  🥦 PantryPilot          ⚙     │  slim header (unchanged)
├─────────────────────────────────┤
│  [ALERT] Basket ready — Review  │  conditional, only when pending
├─────────────────────────────────┤
│  ┌──────────┐┌────────┐┌──────┐│
│  │  ↻ Flow  ││📋 Rout.││🛒 QO ││  compact action strip (top)
│  │ Next: Mon││2 active││ Order││
│  └──────────┘└────────┘└──────┘│
├─────────────────────────────────┤
│                                 │
│   This week                     │  section label
│                                 │
│  ┌────────────────┐ ┌─────────┐ │
│  │  12,400 kcal   │ │ ₹1,840  │ │  2-col hero tiles
│  │  of 14,000     │ │ of ₹2k  │ │
│  │  [======○  ]   │ │ [====○ ]│ │  inline progress bars
│  └────────────────┘ └─────────┘ │
│                                 │
│  ┌──────┐ ┌──────┐ ┌──────┐   │
│  │  7   │ │  ₹420│ │  4/5 │   │  3-col stat tiles
│  │orders│ │ avg  │ │resolv│   │
│  └──────┘ └──────┘ └──────┘   │
│                                 │
│  Nutrition                      │  section label
│  Protein  [●●●●○○○]  48g/50g   │
│  Fiber    [●●○○○○○]  12g/25g   │  macro progress rows
│  Sodium   [●●●●●●○] 1800/2300mg│
│                                 │
│  Recent orders                  │  section label
│  Mon · Lays, Milk +3   ₹420 ›  │
│  Thu · Eggs, Bread     ₹180 ›  │  slim order rows (tap → /orders)
│                                 │
└─────────────────────────────────┘
```

---

## Sections in detail

### 1. Alert banner (conditional)

Shown only when `basketPending = true` or `flowInProgress = true`.

- **Basket pending:** green pill banner — "Your basket is ready. Tap to review." → navigates to `/flow`
- **Flow in progress:** amber pulsing banner — "Building your basket…" (no tap target)
- Sits directly below the header, above all stats
- Disappears when neither condition is true

### 3. This Week — hero tiles (2-column)

**Left tile — Calories**
- Large number: total kcal ordered this week (from `OrderNutrition` rows for this household, last 7 days)
- Sub-label: "of {weekly_target} kcal"
- Inline progress bar
- Tap: navigates to a future weekly nutrition detail page (no-op for now, just visual)

**Right tile — Spend**
- Large number: ₹ total spent this week (sum of `Order.grand_total` last 7 days)
- Sub-label: "of ₹{weekly_budget_max}"
- Inline progress bar (green if under budget, amber if ≥80%, red if over)
- Both tiles have the same card style: white, rounded-2xl, consistent padding

### 4. Stat tiles (3-column)

Small, data-dense. All from existing data, no new APIs.

| Tile | Value | Source |
|---|---|---|
| Orders | total orders placed all-time | `COUNT(*) FROM orders WHERE household_id = ?` — placed orders only, not loop_runs (which includes skipped/failed runs) |
| Avg spend | average order total | `AVG(grand_total) FROM orders WHERE household_id = ?` |
| Resolved | resolved / total items in last order | `resolved_items / total_items` from the most recent `order_nutrition` row |

### 5. Nutrition section

Weekly macro progress rows. Three rows: Protein, Fiber, Sodium.

Each row:
- Label (left)
- Segmented dot bar: **7 dots**, one per day of the week. Filled count = `round(actual / target * 7)`. This is semantically meaningful — a full bar means you hit the weekly target.
- Value + target (right, tabular-nums)

Data source: summed from `order_nutrition` rows for the current calendar week.

**Show this section when `has_nutrition_data = true`** (i.e. at least one `order_nutrition` row exists for the household). Hide only when no resolved nutrition data exists at all.

Targets use `household_nutrition_goals.daily_* × 7` when a goals row is set; otherwise ICMR RDA defaults are used automatically — the section is never hidden just because the user hasn't configured goals.

### 6. Recent orders (slim list)

Last 3 placed orders as single-line rows:
- Day label (Mon, Thu) + item name preview
- Right: spend amount + `›` chevron
- Tap: navigates to `/orders`

**Preview string construction:** First 2 `product_name` values from `order_items` (by insertion order), comma-joined. `extra_count = total_items - 2` (0 if the order has ≤ 2 items). Example: order has 5 items → `"Lays, Amul Milk"` with `extra_count: 3`.

Only show if at least 1 order exists. If no orders: skip the section entirely (no empty state — the screen is already rich enough without it).

### 2. Action strip (replaces three tall cards)

Three equal-width buttons in a horizontal row, placed **immediately below the alert banner** (or below the header when no alert is active). Each is a compact card:

| Button | Icon | Sub-label | Tap |
|---|---|---|---|
| Flow | ↻ | Next: {day} / "Planning…" / "Review now" / "Set up" | `/flow` |
| Routines | 📋 | {N} active or "Set up" | `/routines` |
| Quick Order | 🛒 | "Order now" | `/quick` |

Height target: ~70px each. Sits at the top so primary navigation is always reachable without scrolling. This replaces all three current tall cards and saves ~200px of vertical space.

---

## Time windows — explicit decisions

These choices are fixed; they are not configurable by the user on the home screen. Time-range filters belong on a future `/stats` drill-down page, not here.

| Section | Window | Resets |
|---|---|---|
| Spend hero tile | Current calendar week (Mon 00:00 → Sun 23:59 IST) | Every Monday at midnight IST |
| Calories hero tile | Current calendar week | Every Monday at midnight IST |
| Nutrition dot bars (Protein / Fiber / Sodium) | Current calendar week | Every Monday at midnight IST |
| Orders stat tile | All-time count | Never |
| Avg spend stat tile | All-time average across all placed orders | Never |
| Items resolved stat tile | Resolved / total from the most recent order with nutrition data | Per-order, not windowed |
| Recent orders list | Last 3 placed orders, regardless of date | Rolling — always the 3 most recent |

**Why calendar week instead of rolling 7 days?**  
Rolling 7 days produces a bar that drains and fills continuously, which makes the "of ₹2,000" budget feel arbitrary — you can never see it fully reset. A Monday-anchored calendar week lets the spend bar start at zero on Monday and fill cleanly by Sunday, matching how most people think about a grocery budget.

**Why no filter toggle on the home screen?**  
A grocery cadence is inherently weekly. Showing "last 30 days" vs "this week" on the home screen adds a picker with marginal value — the numbers become less actionable, not more. Historical trends belong on a dedicated stats page (out of scope for this task).

**Nutrition targets — how derived:**  
- Weekly target = `household_nutrition_goals.daily_*` × 7 when a goals row exists  
- When no goals row is set, ICMR RDA defaults are used (`member_count × per-person daily value × 7`). The section still shows — targets are never absent.

**Empty-state variants — two distinct cases:**

| Case | Spend tile | Calorie tile | Nutrition section | Recent orders |
|---|---|---|---|---|
| No orders placed at all | `₹0` with `0%` bar | `—` (no data) | Hidden | Hidden |
| Orders exist, nutrition not yet resolved | Real spend value + bar | `—` (no data) | Hidden | Shown |
| Orders exist, nutrition resolved, no goals set | Real spend value | Real calorie total | Shown (ICMR defaults used as targets) | Shown |
| Fully loaded | Real spend value | Real calorie total | Shown | Shown |

The frontend must not treat these cases identically. `week.order_count > 0` determines whether spend data is real. `has_nutrition_data` determines whether the nutrition section renders. The calorie tile shows `—` whenever `has_nutrition_data = false`.

---

## Data requirements

All data exists in current APIs. Dashboard makes **one consolidated call** instead of 5 parallel requests:

### New endpoint: `GET /v1/dashboard`

Returns everything needed in a single response:

```json
{
  "flow": {
    "basket_pending": false,
    "in_progress": false,
    "next_run_at": "2026-07-21T09:00:00Z"
  },
  "routines": {
    "active_count": 2,
    "next_run_at": "2026-07-20T08:00:00Z"
  },
  "week": {
    "week_start": "2026-07-13T00:00:00+05:30",  // Monday 00:00 IST
    "week_end":   "2026-07-19T23:59:59+05:30",  // Sunday 23:59 IST
    "total_spend": 1840.0,
    "budget_max": 2000.0,
    "order_count": 3,
    "total_calories": 12400.0,
    "calorie_target": 14000.0,   // daily_calories * 7
    "total_protein_g": 48.0,
    "protein_target": 50.0,      // daily_protein_g * 7
    "total_fiber_g": 12.0,
    "fiber_target": 25.0,        // daily_fiber_g * 7
    "total_sodium_mg": 1800.0,
    "sodium_target": 2300.0,     // daily_sodium_mg * 7
    "has_nutrition_data": true   // true when ≥1 resolved order_nutrition row exists; goals row not required (ICMR defaults used when absent)
  },
  "stats": {
    "total_orders": 12,
    "avg_order_total": 420.0,
    "last_nutrition": {
      "resolved_items": 4,
      "total_items": 5
    }
  },
  "recent_orders": [
    { "placed_at": "2026-07-18T...", "preview": "Lays, Milk", "extra_count": 3, "total": 420.0 },
    { "placed_at": "2026-07-15T...", "preview": "Eggs, Bread", "extra_count": 0, "total": 180.0 }
  ]
}
```

**Why one endpoint?** The current dashboard fires 5 fetches in parallel on mount. A single endpoint: removes 4 round-trips, enables server-side aggregation (the weekly spend + nutrition join is a single DB query), and gives us one loading state instead of partial renders.

---

## What goes away

- The three tall green-header cards (Flow, Routines, Quick Order)
- The three small stat tiles in the current "quick stats" row
- The `api.runs.list()` call on mount (data moved into `GET /v1/dashboard`)
- The `api.basket.pending()` call on mount (merged into dashboard)
- The `api.routines.list()` call on mount (merged into dashboard)

---

## Implementation order

1. **`GET /v1/dashboard` backend endpoint** — aggregate query; no new DB tables
2. **Dashboard page rebuild** — replace current layout with new sections
3. **Action strip component** — reusable `OrderModeStrip` if needed elsewhere

---

## Out of scope

- Weekly nutrition detail drill-down page (separate task)
- Push notifications / live updates (separate task)
- Dark-mode specific palette tuning (covered by existing Tailwind dark: classes)
- Spend breakdown by category
